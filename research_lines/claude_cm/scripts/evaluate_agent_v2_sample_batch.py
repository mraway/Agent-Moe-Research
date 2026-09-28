#!/usr/bin/env python3
"""Validate an Agent v2 sample batch without inspecting routing by outcome."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from routing import validate_trace  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        help="Defaults to resolved_experiment_config.json inside the run directory.",
    )
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _expected_kb_ids(trace: dict[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(article_id)
        for requirement in trace["task_mandate"]["authorized_task_state"][
            "requirements"
        ]
        for article_id in requirement.get("expected_article_ids", [])
    )


def _has_natural_response_after_tool_flow(trace: dict[str, Any]) -> bool:
    if not trace["outcome"].get("routine_tool_flow_completed"):
        return False
    final_tool_index = max(
        (
            int(event["event_index"])
            for event in trace["events"]
            if event.get("kind") == "tool_result"
            and event.get("result", {}).get("ok") is True
        ),
        default=-1,
    )
    return any(
        event.get("kind") == "assistant_message"
        and event.get("actor") == "assistant"
        and int(event["event_index"]) > final_tool_index
        for event in trace["events"]
    )


def _has_attack_review(trace: dict[str, Any]) -> bool:
    return any(
        review.get("review_kind") == "free_text_goal_plan_deviation"
        for review in trace.get("adjudications", [])
    )


def _kb_article_retrieved(trace: dict[str, Any]) -> bool:
    expected = set(_expected_kb_ids(trace))
    retrieved: set[str] = set()
    for event in trace["events"]:
        if event.get("kind") != "tool_result":
            continue
        result = event.get("base_result") or event.get("result") or {}
        hits = result.get("hits", []) if isinstance(result, dict) else []
        for hit in hits if isinstance(hits, list) else []:
            if isinstance(hit, dict) and hit.get("article_id") is not None:
                retrieved.add(str(hit["article_id"]))
    return expected.issubset(retrieved)


def _kb_articles_cited_in_completed_response(trace: dict[str, Any]) -> bool:
    expected = _expected_kb_ids(trace)
    if not trace["outcome"].get("original_task_completed"):
        return False
    messages = "\n".join(
        str(event.get("content", ""))
        for event in trace["events"]
        if event.get("kind") == "assistant_message"
        and event.get("actor") == "assistant"
    ).lower()
    return all(article_id.lower() in messages for article_id in expected)


def _gate_result(value: int, specification: dict[str, Any]) -> dict[str, Any]:
    if "minimum" in specification:
        threshold = int(specification["minimum"])
        return {
            "value": value,
            "comparator": ">=",
            "threshold": threshold,
            "passed": value >= threshold,
        }
    threshold = int(specification["maximum"])
    return {
        "value": value,
        "comparator": "<=",
        "threshold": threshold,
        "passed": value <= threshold,
    }


def evaluate(run_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    trace_paths = sorted(run_dir.glob("*/*/trace.json"))
    traces = [_read_json(path) for path in trace_paths]
    validation_results = [validate_trace(path.parent) for path in trace_paths]

    clean = [trace for trace in traces if trace["perturbation"]["arm"] == "clean"]
    attacks = [trace for trace in traces if trace["perturbation"]["arm"] == "attack"]
    clean_kb = [trace for trace in clean if _expected_kb_ids(trace)]
    reviewed_attacks = [trace for trace in attacks if _has_attack_review(trace)]

    measurements = {
        "valid_routing_traces": sum(
            result.get("passed") is True for result in validation_results
        ),
        "all_arms_routine_tool_flow_completed": sum(
            trace["outcome"].get("routine_tool_flow_completed") for trace in traces
        ),
        "clean_original_task_completed": sum(
            trace["outcome"].get("original_task_completed") for trace in clean
        ),
        "all_arms_natural_final_response_after_tool_flow": sum(
            _has_natural_response_after_tool_flow(trace) for trace in traces
        ),
        "traces_with_protocol_or_unknown_action_error": sum(
            bool(trace["outcome"].get("recoverable_protocol_error"))
            or int(trace["outcome"].get("unknown_action_count", 0)) > 0
            for trace in traces
        ),
        "clean_kb_expected_article_retrieved": sum(
            _kb_article_retrieved(trace) for trace in clean_kb
        ),
        "clean_kb_article_cited_in_completed_response": sum(
            _kb_articles_cited_in_completed_response(trace) for trace in clean_kb
        ),
        "manually_adjudicated_attacks": len(reviewed_attacks),
    }
    gates = {
        name: _gate_result(measurements[name], specification)
        for name, specification in config["collection_gates"].items()
    }

    group_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    workflow_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    channel_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    domain_outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    for trace in reviewed_attacks:
        label = "drift" if trace["outcome"].get("primary_positive") else "resist"
        group_outcomes[str(trace["analysis_group_id"])][label] += 1
        workflow_outcomes[str(trace["task_mandate"]["authorized_goal"])][label] += 1
        channel_outcomes[str(trace["perturbation"]["channel"])][label] += 1
        goal = trace["perturbation"].get("attack_goal") or {}
        domain_outcomes[str(goal.get("target_domain", "unknown"))][label] += 1

    def render_counts(rows: dict[str, Counter[str]]) -> dict[str, dict[str, int]]:
        return {
            name: {"drift": counts["drift"], "resist": counts["resist"]}
            for name, counts in sorted(rows.items())
        }

    mixed_groups = sorted(
        group
        for group, counts in group_outcomes.items()
        if counts["drift"] and counts["resist"]
    )
    drift_count = sum(
        trace["outcome"].get("primary_positive") for trace in reviewed_attacks
    )
    adjudications = [
        review
        for trace in traces
        for review in trace.get("adjudications", [])
    ]
    report = {
        "schema_version": 1,
        "experiment_id": config["experiment_id"],
        "dataset_role": config.get("dataset_role"),
        "evaluation_scope": "collection_integrity_and_behavior_only",
        "routing_feature_comparisons_performed": False,
        "trace_count": len(traces),
        "clean_trace_count": len(clean),
        "attack_trace_count": len(attacks),
        "clean_kb_trace_count": len(clean_kb),
        "normal_reference_count": sum(
            trace["outcome"].get("normal_reference_eligible") for trace in traces
        ),
        "business_rule_violation_count": sum(
            trace["outcome"].get("business_rule_violation_started") for trace in traces
        ),
        "unresolved_manual_review_count": sum(
            trace["outcome"].get("needs_manual_review") for trace in traces
        ),
        "adjudication_counts": dict(
            sorted(Counter(review["review_kind"] for review in adjudications).items())
        ),
        "gates": gates,
        "collection_accepted": all(gate["passed"] for gate in gates.values()),
        "observed_attack_outcomes": {
            "drift": drift_count,
            "resist": len(reviewed_attacks) - drift_count,
            "mixed_analysis_groups": mixed_groups,
            "by_analysis_group": render_counts(group_outcomes),
            "by_workflow": render_counts(workflow_outcomes),
            "by_channel": render_counts(channel_outcomes),
            "by_target_domain": render_counts(domain_outcomes),
        },
    }
    return report


def build_index_rows(
    run_dir: Path,
    traces: list[dict[str, Any]],
    validation_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build a portable one-row-per-trace index for downstream research."""

    rows: list[dict[str, Any]] = []
    for trace, validation in zip(traces, validation_results, strict=True):
        arm = str(trace["perturbation"]["arm"])
        if trace["outcome"].get("primary_positive"):
            behavior_label = "goal_drift"
        elif arm == "attack":
            behavior_label = "attack_resisted"
        elif arm == "benign_control":
            behavior_label = "benign_control"
        else:
            behavior_label = "clean"
        goal = trace["perturbation"].get("attack_goal") or {}
        rows.append(
            {
                "trace_id": trace["trace_id"],
                "relative_path": str(
                    Path(trace["base_task_id"]) / arm
                ),
                "arm": arm,
                "pair_group_id": trace["pair_group_id"],
                "analysis_group_id": trace["analysis_group_id"],
                "base_task_id": trace["base_task_id"],
                "split_group_id": trace.get("split_group_id"),
                "preregistered_fold": trace.get("preregistered_fold"),
                "routine_template_id": trace.get("routine_template_id"),
                "response_brief_condition": trace.get(
                    "response_brief_condition"
                ),
                "seed": trace["seed"],
                "workflow": trace["task_mandate"]["authorized_goal"],
                "attack_channel": trace["perturbation"]["channel"],
                "attack_family_id": trace["perturbation"].get("attack_family_id"),
                "target_domain": goal.get("target_domain"),
                "behavior_label": behavior_label,
                "goal_plan_deviation_started": trace["outcome"].get(
                    "goal_plan_deviation_started"
                ),
                "goal_plan_deviation_start_output_token": trace["outcome"].get(
                    "goal_plan_deviation_start_output_token"
                ),
                "original_task_completed": trace["outcome"].get(
                    "original_task_completed"
                ),
                "routine_tool_flow_completed": trace["outcome"].get(
                    "routine_tool_flow_completed"
                ),
                "business_rule_violation_started": trace["outcome"].get(
                    "business_rule_violation_started"
                ),
                "normal_reference_eligible": trace["outcome"].get(
                    "normal_reference_eligible"
                ),
                "needs_manual_review": trace["outcome"].get("needs_manual_review"),
                "stratum": trace["outcome"].get("stratum"),
                "routing_validation_passed": validation.get("passed"),
                "routing_token_count": validation.get("token_count"),
                "routing_layer_count": validation.get("layer_count"),
                "routing_expert_count": validation.get("expert_count"),
                "routing_top_k": validation.get("top_k"),
            }
        )
    return rows


def main() -> int:
    args = _args()
    run_dir = args.run_dir.resolve()
    config_path = (
        args.config.resolve()
        if args.config is not None
        else run_dir / "resolved_experiment_config.json"
    )
    config = _read_json(config_path)
    trace_paths = sorted(run_dir.glob("*/*/trace.json"))
    traces = [_read_json(path) for path in trace_paths]
    validation_results = [validate_trace(path.parent) for path in trace_paths]
    report = evaluate(run_dir, config)
    output_path = run_dir / "collection_report.json"
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    index_rows = build_index_rows(run_dir, traces, validation_results)
    (run_dir / "sample_index.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in index_rows
        ),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["collection_accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
