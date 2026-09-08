#!/usr/bin/env python3
"""Summarize historical and reviewer-A onset structure without routing data."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase_a.normal_manifold import sha256  # noqa: E402


PLAN_SHA256 = "22397eced11b375e921bdc3947be38a566578d876b97c22eb76a0159333574bf"
REVIEW_A_SHA256 = "bb27541fca1d140374bf62ef3db01cd6d53bc1f41a6eb981577babe3c9b43592"
PACKET_SHA256 = "fc8cb237619b461167981c353779f9c44c2286e1210e015b5b6c627b10bfb1ab"
MAPPING_SHA256 = "04dfdd677a9d08e41019eeda1f08b72192f43c42c5aeecb3fa47977dc152d0d0"
DEFAULT_REPLAY = ROOT / "artifacts" / "agent_v2" / "agent_v2_5_b2_horizon384"
DEFAULT_AUDIT = ROOT / "artifacts" / "agent_v2" / "onset_reliability_audit_v1"
DEFAULT_REVIEW = (
    ROOT / "data" / "agent_v2" / "agent_v2_5_b2_horizon384_onset_review_a.jsonl"
)
DEFAULT_OUTPUT = DEFAULT_AUDIT / "reviewer_a_structural_audit.json"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--review-a", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def value_summary(values: Sequence[int]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count": 0,
            "mean": None,
            "median": None,
            "minimum": None,
            "maximum": None,
        }
    return {
        "count": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "minimum": min(values),
        "maximum": max(values),
    }


def sign_counts(values: Sequence[int]) -> dict[str, int]:
    return {
        "negative": sum(value < 0 for value in values),
        "zero": sum(value == 0 for value in values),
        "positive": sum(value > 0 for value in values),
    }


def calculate(replay_dir: Path, audit_dir: Path, review_path: Path) -> dict[str, Any]:
    plan_path = ROOT / "docs/agent_v2_onset_reliability_audit_plan.md"
    packet_path = audit_dir / "blind_review_packet.jsonl"
    mapping_path = audit_dir / "private_case_mapping.jsonl"
    actual = {
        "plan_sha256": sha256(plan_path),
        "blind_packet_sha256": sha256(packet_path),
        "private_mapping_sha256": sha256(mapping_path),
        "reviewer_a_sha256": sha256(review_path),
    }
    expected = {
        "plan_sha256": PLAN_SHA256,
        "blind_packet_sha256": PACKET_SHA256,
        "private_mapping_sha256": MAPPING_SHA256,
        "reviewer_a_sha256": REVIEW_A_SHA256,
    }
    if actual != expected:
        raise ValueError(f"onset structural-audit hash mismatch: {actual}")

    mapping = {str(row["case_id"]): row for row in _read_jsonl(mapping_path)}
    reviews = _read_jsonl(review_path)
    if len(mapping) != 80 or len(reviews) != 80:
        raise ValueError("onset structural-audit cohort changed")
    rows = []
    for review in reviews:
        case_id = str(review["case_id"])
        source = mapping[case_id]
        trace = _read_json(replay_dir / source["relative_path"] / "trace.json")
        outcome = trace["outcome"]
        goal = outcome.get("goal_plan_deviation_start_output_token")
        row: dict[str, Any] = {
            "case_id": case_id,
            "trace_id": source["trace_id"],
            "historical_class": outcome["attack_engagement_class"],
            "reviewer_a_class": review["trajectory_class"],
            "historical_engagement_start": outcome.get(
                "attack_engagement_start_output_token"
            ),
            "historical_engagement_evidence_end": outcome.get(
                "attack_engagement_evidence_end_output_token"
            ),
            "historical_goal_boundary": (
                None if goal is None else int(goal["output_token_index"])
            ),
            "reviewer_a_transition_sentence": bool(
                review["task_specific_transition_sentence"]
            ),
            "reviewer_a_confidence": review["overall_confidence"],
        }
        for event in ("engagement", "commitment", "execution"):
            value = review[event]
            row[f"reviewer_a_{event}_start"] = (
                None if value is None else int(value["span"]["token_start"])
            )
            row[f"reviewer_a_{event}_end"] = (
                None if value is None else int(value["span"]["token_end"])
            )
        rows.append(row)

    execution = [row for row in rows if row["reviewer_a_class"] == "execution"]
    if len(execution) != 40:
        raise ValueError("reviewer-A execution count changed")
    historical_span_width = [
        int(row["historical_engagement_evidence_end"])
        - int(row["historical_engagement_start"])
        for row in execution
    ]
    historical_goal_minus_start = [
        int(row["historical_goal_boundary"])
        - int(row["historical_engagement_start"])
        for row in execution
    ]
    new_e_minus_old_e = [
        int(row["reviewer_a_engagement_start"])
        - int(row["historical_engagement_start"])
        for row in execution
    ]
    c_minus_e = [
        int(row["reviewer_a_commitment_start"])
        - int(row["reviewer_a_engagement_start"])
        for row in execution
    ]
    x_minus_c = [
        int(row["reviewer_a_execution_start"])
        - int(row["reviewer_a_commitment_start"])
        for row in execution
    ]
    x_minus_e = [
        int(row["reviewer_a_execution_start"])
        - int(row["reviewer_a_engagement_start"])
        for row in execution
    ]
    old_goal_minus_x = [
        int(row["historical_goal_boundary"])
        - int(row["reviewer_a_execution_start"])
        for row in execution
    ]

    return {
        "schema_version": 1,
        "analysis_id": "agent-v2-onset-reviewer-a-structural-audit",
        "status": "awaiting_independent_reviewer_b",
        "analysis_role": "text-only interim data audit; no inter-reviewer reliability claim",
        "b3_used": False,
        "routing_read": False,
        "input_hashes": actual,
        "trace_count": len(rows),
        "historical_class_counts": dict(
            sorted(Counter(str(row["historical_class"]) for row in rows).items())
        ),
        "reviewer_a_class_counts": dict(
            sorted(Counter(str(row["reviewer_a_class"]) for row in rows).items())
        ),
        "reviewer_a_confidence_counts": dict(
            sorted(Counter(str(row["reviewer_a_confidence"]) for row in rows).items())
        ),
        "reviewer_a_transition_sentence_count": sum(
            row["reviewer_a_transition_sentence"] for row in rows
        ),
        "execution_structure": {
            "trace_count": len(execution),
            "historical_evidence_end_minus_start": value_summary(historical_span_width),
            "historical_goal_boundary_minus_engagement_start": value_summary(
                historical_goal_minus_start
            ),
            "historical_goal_equals_historical_evidence_end_count": sum(
                row["historical_goal_boundary"]
                == row["historical_engagement_evidence_end"]
                for row in execution
            ),
            "reviewer_a_engagement_start_minus_historical_start": {
                "summary": value_summary(new_e_minus_old_e),
                "sign_counts": sign_counts(new_e_minus_old_e),
            },
            "reviewer_a_commitment_start_minus_engagement_start": value_summary(c_minus_e),
            "reviewer_a_execution_start_minus_commitment_start": value_summary(x_minus_c),
            "reviewer_a_execution_start_minus_engagement_start": value_summary(x_minus_e),
            "historical_goal_boundary_minus_reviewer_a_execution_start": {
                "summary": value_summary(old_goal_minus_x),
                "sign_counts": sign_counts(old_goal_minus_x),
            },
            "transition_sentence_count": sum(
                row["reviewer_a_transition_sentence"] for row in execution
            ),
        },
        "trace_rows": sorted(rows, key=lambda row: str(row["case_id"])),
    }


def main() -> None:
    args = _args()
    result = calculate(
        args.replay.resolve(), args.audit_dir.resolve(), args.review_a.resolve()
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: result[key] for key in ("status", "trace_count", "execution_structure")}, indent=2))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
