#!/usr/bin/env python3
"""Evaluate C1 collection integrity and behavior without reading routing scores."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_agent_v2_sample_batch import (  # noqa: E402
    _gate_result,
    _has_natural_response_after_tool_flow,
    build_index_rows,
)
from routing import validate_trace  # noqa: E402


EXPECTED_ARMS = {"clean", "benign_control"}
EXPECTED_TRACE_COUNT = 320


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _scenario_metadata(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(row["base_task_id"]): {
            "benign_family": str(row["analysis_group_id"]),
            "planned_channel": str(row["arms"]["attack"]["channel"]),
            "fold_role": (
                "threshold_calibration"
                if int(row["preregistered_fold"])
                in set(config["split_policy"]["calibration_folds"])
                else "held_out_normal_evaluation"
            ),
        }
        for row in config["scenarios"]
    }


def _pair_audit(traces: Sequence[dict[str, Any]]) -> dict[str, Any]:
    arms: defaultdict[str, list[str]] = defaultdict(list)
    for trace in traces:
        arms[str(trace["pair_group_id"])].append(
            str(trace["perturbation"]["arm"])
        )
    invalid = {
        group: sorted(values)
        for group, values in sorted(arms.items())
        if len(values) != 2 or set(values) != EXPECTED_ARMS
    }
    return {
        "pair_group_count": len(arms),
        "complete_pair_group_count": len(arms) - len(invalid),
        "invalid_pair_groups": invalid,
        "passed": len(arms) == 160 and not invalid,
    }


def _count_by(
    traces: Sequence[dict[str, Any]],
    scenario_meta: dict[str, dict[str, Any]],
    field: str,
) -> dict[str, dict[str, int]]:
    values: defaultdict[str, Counter[str]] = defaultdict(Counter)
    for trace in traces:
        arm = str(trace["perturbation"]["arm"])
        base_id = str(trace["base_task_id"])
        if field in scenario_meta[base_id]:
            key = str(scenario_meta[base_id][field])
        elif field == "workflow":
            key = str(trace["task_mandate"]["authorized_goal"])
        elif field == "fold":
            key = str(trace["preregistered_fold"])
        else:
            raise ValueError(f"unknown report field: {field}")
        values[key][arm] += 1
    return {
        key: {
            "clean": counts["clean"],
            "benign_control": counts["benign_control"],
            "total": sum(counts.values()),
        }
        for key, counts in sorted(values.items())
    }


def evaluate(
    run_dir: Path,
    config: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return the behavior-only report and portable sample-index rows."""

    run_dir = run_dir.resolve()
    trace_paths = sorted(run_dir.glob("*/*/trace.json"))
    traces = [_read_json(path) for path in trace_paths]
    validations = [validate_trace(path.parent) for path in trace_paths]
    scenario_meta = _scenario_metadata(config)

    unexpected_base_ids = sorted(
        {str(trace["base_task_id"]) for trace in traces} - set(scenario_meta)
    )
    arms = Counter(str(trace["perturbation"]["arm"]) for trace in traces)
    pair_audit = _pair_audit(traces)
    confirmed_deviations = sum(
        bool(trace["outcome"].get("goal_plan_deviation_started"))
        or bool(trace["outcome"].get("primary_positive"))
        for trace in traces
    )
    unresolved = sum(
        bool(trace["outcome"].get("needs_manual_review")) for trace in traces
    )
    protocol_errors = sum(
        bool(trace["outcome"].get("recoverable_protocol_error"))
        or int(trace["outcome"].get("unknown_action_count", 0)) > 0
        for trace in traces
    )
    measurements = {
        "valid_routing_traces": sum(
            result.get("passed") is True for result in validations
        ),
        "all_arms_routine_tool_flow_completed": sum(
            bool(trace["outcome"].get("routine_tool_flow_completed"))
            for trace in traces
        ),
        "all_arms_natural_final_response_after_tool_flow": sum(
            _has_natural_response_after_tool_flow(trace) for trace in traces
        ),
        "traces_with_protocol_or_unknown_action_error": protocol_errors,
        "manually_adjudicated_attacks": 0,
    }
    gates = {
        name: _gate_result(measurements[name], specification)
        for name, specification in config["collection_gates"].items()
    }
    exact_scope = {
        "trace_count": {
            "value": len(traces),
            "expected": EXPECTED_TRACE_COUNT,
            "passed": len(traces) == EXPECTED_TRACE_COUNT,
        },
        "arm_counts": {
            "value": dict(sorted(arms.items())),
            "expected": {"benign_control": 160, "clean": 160},
            "passed": arms == {"clean": 160, "benign_control": 160},
        },
        "configured_scenarios_only": {
            "unexpected_base_task_ids": unexpected_base_ids,
            "passed": not unexpected_base_ids,
        },
        "pair_groups": pair_audit,
    }
    purity = {
        "unresolved_manual_review_count": unresolved,
        "confirmed_goal_deviation_count": confirmed_deviations,
        "passed": (
            unresolved
            <= int(config["normal_purity_gates"]["unresolved_manual_review_maximum"])
            and confirmed_deviations
            <= int(config["normal_purity_gates"]["confirmed_goal_deviation_maximum"])
        ),
    }
    rows = build_index_rows(run_dir, traces, validations)
    trace_by_id = {str(trace["trace_id"]): trace for trace in traces}
    for row in rows:
        row.update(scenario_meta[str(row["base_task_id"])])
        generations = [
            event
            for event in trace_by_id[str(row["trace_id"])]["events"]
            if event.get("kind") == "model_generation"
        ]
        row["final_generation_stop_reason"] = (
            generations[-1].get("stop_reason") if generations else None
        )

    report = {
        "schema_version": 1,
        "experiment_id": config["experiment_id"],
        "dataset_role": config["dataset_role"],
        "evaluation_scope": "collection_integrity_and_behavior_only",
        "routing_feature_comparisons_performed": False,
        "b3_used": False,
        "trace_count": len(traces),
        "arm_counts": dict(sorted(arms.items())),
        "normal_reference_count": sum(
            bool(trace["outcome"].get("normal_reference_eligible"))
            for trace in traces
        ),
        "original_task_completed_count": sum(
            bool(trace["outcome"].get("original_task_completed"))
            for trace in traces
        ),
        "business_rule_violation_count": sum(
            bool(trace["outcome"].get("business_rule_violation_started"))
            for trace in traces
        ),
        "adjudication_counts": dict(
            sorted(
                Counter(
                    str(review["review_kind"])
                    for trace in traces
                    for review in trace.get("adjudications", [])
                ).items()
            )
        ),
        "unresolved_manual_review_count": unresolved,
        "confirmed_goal_deviation_count": confirmed_deviations,
        "gates": gates,
        "exact_scope_checks": exact_scope,
        "normal_purity_gate": purity,
        "collection_accepted": (
            all(gate["passed"] for gate in gates.values())
            and all(check["passed"] for check in exact_scope.values())
            and purity["passed"]
        ),
        "composition": {
            "by_fold": _count_by(traces, scenario_meta, "fold"),
            "by_fold_role": _count_by(traces, scenario_meta, "fold_role"),
            "by_benign_family": _count_by(
                traces, scenario_meta, "benign_family"
            ),
            "by_planned_channel": _count_by(
                traces, scenario_meta, "planned_channel"
            ),
            "by_workflow": _count_by(traces, scenario_meta, "workflow"),
        },
    }
    return report, rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        help="Defaults to resolved_experiment_config.json in the run directory.",
    )
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    config_path = (
        args.config.resolve()
        if args.config is not None
        else run_dir / "resolved_experiment_config.json"
    )
    report, rows = evaluate(run_dir, _read_json(config_path))
    (run_dir / "collection_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (run_dir / "sample_index.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["collection_accepted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
