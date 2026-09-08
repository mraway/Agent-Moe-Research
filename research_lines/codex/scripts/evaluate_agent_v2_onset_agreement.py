#!/usr/bin/env python3
"""Evaluate two routing-blind onset reviews under the frozen audit protocol."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from phase_a.normal_manifold import sha256  # noqa: E402
from validate_agent_v2_onset_review import validate_review  # noqa: E402


PLAN_SHA256 = "22397eced11b375e921bdc3947be38a566578d876b97c22eb76a0159333574bf"
PACKET_SHA256 = "fc8cb237619b461167981c353779f9c44c2286e1210e015b5b6c627b10bfb1ab"
REVIEW_A_SHA256 = "bb27541fca1d140374bf62ef3db01cd6d53bc1f41a6eb981577babe3c9b43592"
DEFAULT_AUDIT = ROOT / "artifacts" / "agent_v2" / "onset_reliability_audit_v1"
DEFAULT_REVIEW_A = (
    ROOT / "data" / "agent_v2" / "agent_v2_5_b2_horizon384_onset_review_a.jsonl"
)
DEFAULT_REVIEW_B = (
    ROOT / "data" / "agent_v2" / "agent_v2_5_b2_horizon384_onset_review_b.jsonl"
)
DEFAULT_OUTPUT = DEFAULT_AUDIT / "reviewer_agreement.json"
EVENTS = ("engagement", "commitment", "execution")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-dir", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--review-a", type=Path, default=DEFAULT_REVIEW_A)
    parser.add_argument("--review-b", type=Path, default=DEFAULT_REVIEW_B)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def cohen_kappa(left: Sequence[str | bool], right: Sequence[str | bool]) -> float | None:
    if len(left) != len(right):
        raise ValueError("kappa labels do not align")
    if not left:
        return None
    observed = sum(a == b for a, b in zip(left, right, strict=True)) / len(left)
    left_counts = Counter(left)
    right_counts = Counter(right)
    categories = set(left_counts) | set(right_counts)
    expected = sum(
        left_counts[value] * right_counts[value] for value in categories
    ) / (len(left) ** 2)
    if math.isclose(expected, 1.0):
        return 1.0 if math.isclose(observed, 1.0) else None
    return (observed - expected) / (1.0 - expected)


def _summary(values: Sequence[int]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "minimum": None, "maximum": None}
    return {
        "count": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "minimum": min(values),
        "maximum": max(values),
    }


def _event_agreement(
    left_rows: Sequence[dict[str, Any]], right_rows: Sequence[dict[str, Any]], event: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    presence_left = [row[event] is not None for row in left_rows]
    presence_right = [row[event] is not None for row in right_rows]
    both = [
        (left, right)
        for left, right in zip(left_rows, right_rows, strict=True)
        if left[event] is not None and right[event] is not None
    ]
    start_differences = [
        abs(
            int(left[event]["span"]["token_start"])
            - int(right[event]["span"]["token_start"])
        )
        for left, right in both
    ]
    end_differences = [
        abs(
            int(left[event]["span"]["token_end"])
            - int(right[event]["span"]["token_end"])
        )
        for left, right in both
    ]
    overlap = []
    for left, right in both:
        left_span = left[event]["span"]
        right_span = right[event]["span"]
        overlap.append(
            max(int(left_span["token_start"]), int(right_span["token_start"]))
            <= min(int(left_span["token_end"]), int(right_span["token_end"]))
        )
    disagreements = []
    for left, right in zip(left_rows, right_rows, strict=True):
        if left[event] != right[event]:
            disagreements.append(
                {
                    "case_id": left["case_id"],
                    "reviewer_a": left[event],
                    "reviewer_b": right[event],
                }
            )
    return {
        "presence_exact_count": sum(a == b for a, b in zip(presence_left, presence_right, strict=True)),
        "presence_exact_rate": sum(a == b for a, b in zip(presence_left, presence_right, strict=True)) / len(left_rows),
        "presence_kappa": cohen_kappa(presence_left, presence_right),
        "both_present_count": len(both),
        "absolute_start_token_difference": _summary(start_differences),
        "start_exact_count": sum(value == 0 for value in start_differences),
        "start_within4_count": sum(value <= 4 for value in start_differences),
        "start_within8_count": sum(value <= 8 for value in start_differences),
        "absolute_confirmation_end_difference": _summary(end_differences),
        "evidence_span_overlap_count": sum(overlap),
        "evidence_span_overlap_rate": sum(overlap) / len(overlap) if overlap else None,
    }, disagreements


def calculate(packet_path: Path, review_a_path: Path, review_b_path: Path) -> dict[str, Any]:
    plan_path = ROOT / "docs/agent_v2_onset_reliability_audit_plan.md"
    actual = {
        "plan_sha256": sha256(plan_path),
        "blind_packet_sha256": sha256(packet_path),
        "reviewer_a_sha256": sha256(review_a_path),
        "reviewer_b_sha256": sha256(review_b_path),
    }
    if actual["plan_sha256"] != PLAN_SHA256:
        raise ValueError("onset audit plan hash mismatch")
    if actual["blind_packet_sha256"] != PACKET_SHA256:
        raise ValueError("blind review packet hash mismatch")
    if actual["reviewer_a_sha256"] != REVIEW_A_SHA256:
        raise ValueError("reviewer-A file hash mismatch")
    packet = _read_jsonl(packet_path)
    left = sorted(
        validate_review(packet, _read_jsonl(review_a_path)), key=lambda row: str(row["case_id"])
    )
    right = sorted(
        validate_review(packet, _read_jsonl(review_b_path)), key=lambda row: str(row["case_id"])
    )
    if [row["case_id"] for row in left] != [row["case_id"] for row in right]:
        raise ValueError("validated review rows do not align")
    if {str(row["reviewer"]) for row in left} & {str(row["reviewer"]) for row in right}:
        raise ValueError("reviewers A and B must be distinct")

    classes_a = [str(row["trajectory_class"]) for row in left]
    classes_b = [str(row["trajectory_class"]) for row in right]
    disagreements: dict[str, Any] = {
        "trajectory_class": [
            {
                "case_id": a["case_id"],
                "reviewer_a": a["trajectory_class"],
                "reviewer_b": b["trajectory_class"],
            }
            for a, b in zip(left, right, strict=True)
            if a["trajectory_class"] != b["trajectory_class"]
        ]
    }
    event_metrics = {}
    for event in EVENTS:
        event_metrics[event], disagreements[event] = _event_agreement(left, right, event)
    transition_a = [bool(row["task_specific_transition_sentence"]) for row in left]
    transition_b = [bool(row["task_specific_transition_sentence"]) for row in right]
    return {
        "schema_version": 1,
        "analysis_id": "agent-v2-onset-independent-review-agreement-v1",
        "status": "pre_adjudication_agreement_complete",
        "b3_used": False,
        "routing_read": False,
        "input_hashes": actual,
        "case_count": len(left),
        "trajectory_class": {
            "exact_count": sum(a == b for a, b in zip(classes_a, classes_b, strict=True)),
            "exact_rate": sum(a == b for a, b in zip(classes_a, classes_b, strict=True)) / len(left),
            "cohen_kappa": cohen_kappa(classes_a, classes_b),
            "reviewer_a_counts": dict(sorted(Counter(classes_a).items())),
            "reviewer_b_counts": dict(sorted(Counter(classes_b).items())),
        },
        "events": event_metrics,
        "transition_sentence": {
            "exact_count": sum(a == b for a, b in zip(transition_a, transition_b, strict=True)),
            "exact_rate": sum(a == b for a, b in zip(transition_a, transition_b, strict=True)) / len(left),
            "cohen_kappa": cohen_kappa(transition_a, transition_b),
        },
        "disagreements": disagreements,
    }


def main() -> None:
    args = _args()
    packet = args.audit_dir.resolve() / "blind_review_packet.jsonl"
    result = calculate(packet, args.review_a.resolve(), args.review_b.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: result[key] for key in ("status", "case_count", "trajectory_class", "events")}, indent=2))
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
