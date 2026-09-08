#!/usr/bin/env python3
"""Audit exact B2 replay prefixes and prepare text-only engagement review rows."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from routing import validate_trace  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parent",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "agent_v2_5_b2",
    )
    parser.add_argument(
        "--replay",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "agent_v2_5_b2_horizon384",
    )
    parser.add_argument(
        "--audit-output",
        type=Path,
        default=None,
    )
    parser.add_argument(
        "--review-output",
        type=Path,
        default=None,
    )
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _final_generation(trace: dict[str, Any]) -> dict[str, Any]:
    rows = [
        row for row in trace["events"] if row.get("kind") == "model_generation"
    ]
    if not rows:
        raise ValueError(f"trace lacks model generation: {trace.get('trace_id')}")
    return rows[-1]


def compare_replay_trace(
    parent: dict[str, Any], replay: dict[str, Any]
) -> dict[str, Any]:
    """Compare the declared inputs and generated output prefix of one replay."""

    if parent["base_task_id"] != replay["base_task_id"]:
        raise ValueError("base task changed")
    if parent["condition_id"] != replay["condition_id"]:
        raise ValueError("condition changed")
    if parent["seed"] != replay["seed"]:
        raise ValueError("sampling seed changed")
    for field in (
        "model_id",
        "model_revision",
        "agent_id",
        "agent_definition_version",
        "system_prompt_hash",
        "tool_schema_hash",
        "task_mandate",
        "perturbation",
    ):
        if parent[field] != replay[field]:
            raise ValueError(f"replay input field changed: {field}")

    parent_generation = _final_generation(parent)
    replay_generation = _final_generation(replay)
    if parent_generation["rendered_prompt"] != replay_generation["rendered_prompt"]:
        raise ValueError("final-generation rendered prompt changed")
    old_ids = [int(value) for value in parent_generation["output_token_ids"]]
    new_ids = [int(value) for value in replay_generation["output_token_ids"]]
    old_stop = str(parent_generation["stop_reason"])
    if old_stop == "eos":
        prefix_identical = old_ids == new_ids
        expected_length = len(old_ids)
        extension_count = 0
        replay_stop_compatible = replay_generation["stop_reason"] == "eos"
    elif old_stop == "length":
        if len(old_ids) != 192:
            raise ValueError("parent length stop does not contain 192 tokens")
        prefix_identical = old_ids == new_ids[: len(old_ids)]
        expected_length = len(old_ids)
        extension_count = max(0, len(new_ids) - len(old_ids))
        replay_stop_compatible = len(new_ids) >= len(old_ids)
    else:
        raise ValueError(f"unsupported parent stop reason: {old_stop}")

    return {
        "trace_id": replay["trace_id"],
        "pair_group_id": replay["pair_group_id"],
        "arm": replay["perturbation"]["arm"],
        "parent_stop_reason": old_stop,
        "replay_stop_reason": replay_generation["stop_reason"],
        "parent_output_token_count": len(old_ids),
        "replay_output_token_count": len(new_ids),
        "compared_prefix_token_count": expected_length,
        "extension_token_count": extension_count,
        "prefix_identical": prefix_identical,
        "stop_compatible": replay_stop_compatible,
        "passed": prefix_identical and replay_stop_compatible,
    }


def build_review_row(
    parent: dict[str, Any], replay: dict[str, Any], comparison: dict[str, Any]
) -> dict[str, Any]:
    """Build a routing-blind review row for one attack replay."""

    generation = _final_generation(replay)
    parent_generation = _final_generation(parent)
    text = str(generation["content"])
    old_text = str(parent_generation["content"])
    extension = text[len(old_text) :] if text.startswith(old_text) else None
    goal = replay["perturbation"].get("attack_goal") or {}
    return {
        "trace_id": replay["trace_id"],
        "pair_group_id": replay["pair_group_id"],
        "event_index": generation["event_index"],
        "seed": replay["seed"],
        "workflow": replay["task_mandate"]["authorized_goal"],
        "attack_channel": replay["perturbation"]["channel"],
        "attack_family_id": replay["perturbation"].get("attack_family_id"),
        "target_domain": goal.get("target_domain"),
        "required_substrings": goal.get("required_substrings"),
        "parent_goal_deviation": parent["outcome"][
            "goal_plan_deviation_started"
        ],
        "parent_goal_deviation_start": parent["outcome"].get(
            "goal_plan_deviation_start_output_token"
        ),
        "parent_stop_reason": comparison["parent_stop_reason"],
        "replay_stop_reason": comparison["replay_stop_reason"],
        "parent_output_token_count": comparison["parent_output_token_count"],
        "replay_output_token_count": comparison["replay_output_token_count"],
        "prefix_identical": comparison["prefix_identical"],
        "model_visible_user_messages": [
            event["content"]
            for event in replay["events"]
            if event.get("kind") == "user_message" and event.get("model_visible")
        ],
        "attack_input": replay["perturbation"],
        "parent_output": old_text,
        "extension_after_parent": extension,
        "replay_output": text,
        "annotation": {
            "engagement_class": None,
            "engagement_evidence": None,
            "engagement_reason": None,
            "support_resumed_after_engagement": None,
            "support_resume_evidence": None,
            "goal_deviation_label": None,
            "goal_deviation_evidence": None,
            "goal_deviation_reason": None,
            "attacker_goal_achieved": None,
            "post192_continuation_class": None,
            "reviewer": None,
        },
    }


def audit(parent_dir: Path, replay_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    parent_paths = {
        path.relative_to(parent_dir): path
        for path in parent_dir.glob("*/*/trace.json")
    }
    replay_paths = {
        path.relative_to(replay_dir): path
        for path in replay_dir.glob("*/*/trace.json")
    }
    if len(parent_paths) != 240 or set(parent_paths) != set(replay_paths):
        raise ValueError(
            f"trace matrix mismatch: parent={len(parent_paths)}, replay={len(replay_paths)}"
        )

    comparisons: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    routing_valid = 0
    tool_flow = 0
    for relative in sorted(parent_paths, key=str):
        parent = _read_json(parent_paths[relative])
        replay = _read_json(replay_paths[relative])
        comparison = compare_replay_trace(parent, replay)
        validation = validate_trace(replay_paths[relative].parent)
        comparison["routing_validation_passed"] = validation["passed"]
        comparison["trace_json_sha256"] = _sha256(replay_paths[relative])
        comparisons.append(comparison)
        routing_valid += int(validation["passed"])
        tool_flow += int(replay["outcome"]["routine_tool_flow_completed"])
        if replay["perturbation"]["arm"] == "attack":
            review_rows.append(build_review_row(parent, replay, comparison))

    stop_pairs = Counter(
        (row["parent_stop_reason"], row["replay_stop_reason"])
        for row in comparisons
    )
    report = {
        "schema_version": 1,
        "audit_scope": "input_prefix_routing_integrity_without_routing_values",
        "parent_run": str(parent_dir.resolve()),
        "replay_run": str(replay_dir.resolve()),
        "trace_count": len(comparisons),
        "attack_review_row_count": len(review_rows),
        "routing_valid_count": routing_valid,
        "routine_tool_flow_count": tool_flow,
        "prefix_identical_count": sum(row["prefix_identical"] for row in comparisons),
        "passed_count": sum(row["passed"] for row in comparisons),
        "parent_length_stop_count": sum(
            row["parent_stop_reason"] == "length" for row in comparisons
        ),
        "length_stops_with_extension": sum(
            row["parent_stop_reason"] == "length"
            and row["extension_token_count"] > 0
            for row in comparisons
        ),
        "stop_reason_transitions": {
            f"{old}->{new}": count for (old, new), count in sorted(stop_pairs.items())
        },
        "exact_paired_replay_passed": (
            len(comparisons) == 240
            and routing_valid == 240
            and tool_flow == 240
            and all(row["passed"] for row in comparisons)
        ),
        "comparisons": comparisons,
    }
    return report, review_rows


def main() -> int:
    args = _args()
    parent = args.parent.resolve()
    replay = args.replay.resolve()
    audit_output = (
        args.audit_output.resolve()
        if args.audit_output is not None
        else replay / "prefix_replay_audit.json"
    )
    review_output = (
        args.review_output.resolve()
        if args.review_output is not None
        else replay / "engagement_review_packet.jsonl"
    )
    report, rows = audit(parent, replay)
    audit_output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    review_output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    summary = {key: value for key, value in report.items() if key != "comparisons"}
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if report["exact_paired_replay_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
