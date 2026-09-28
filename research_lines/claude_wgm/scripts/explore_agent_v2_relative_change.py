#!/usr/bin/env python3
"""Run the fixed B1/B2 adjacent-block routing-change pilot."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_agent_v2_b1_routing import (  # noqa: E402
    Observation,
    _load_observations as load_b1_observations,
)
from phase_a.sequential import (  # noqa: E402
    adjacent_block_selection_jsd,
    finite_sample_upper_threshold,
)
from score_agent_v2_b2_frozen import (  # noqa: E402
    _load_observations as load_b2_observations,
)


PLAN = "docs/sequential_relative_change_pilot_plan.md"
WIDTH = 8
ALPHA = 0.10
B1_SAMPLE_INDEX_SHA256 = (
    "f1a95c5bf36d3233f445e4cb507c89e59c13eda1619af317c4efefadcea72df1"
)
B2_SAMPLE_INDEX_SHA256 = (
    "e9412dde1b328f21943cc59f2a5db37797142026cd392166af202c285cd73942"
)


@dataclass(frozen=True)
class ScoredTrace:
    trace_id: str
    pair_group_id: str
    fold: str
    arm: str
    workflow: str
    channel: str
    domain: str
    positive: bool
    boundary: int | None
    decode_token_count: int
    ends: torch.Tensor
    scores: torch.Tensor


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--b1",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "agent_v2_5_b1",
    )
    parser.add_argument(
        "--b2",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "agent_v2_5_b2",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            ROOT
            / "artifacts"
            / "agent_v2"
            / "sequential_relative_change_pilot.json"
        ),
    )
    return parser.parse_args()


def _score(observations: Sequence[Observation]) -> tuple[ScoredTrace, ...]:
    result: list[ScoredTrace] = []
    for observation in observations:
        ends, scores = adjacent_block_selection_jsd(observation.sequence, WIDTH)
        if ends.numel() != scores.numel():
            raise ValueError(f"score alignment failed for {observation.trace_id}")
        result.append(
            ScoredTrace(
                trace_id=observation.trace_id,
                pair_group_id=observation.pair_group_id,
                fold=observation.fold,
                arm=observation.arm,
                workflow=observation.workflow,
                channel=observation.channel,
                domain=observation.domain,
                positive=observation.positive,
                boundary=observation.boundary,
                decode_token_count=len(observation.sequence.token_ids),
                ends=ends,
                scores=scores,
            )
        )
    return tuple(result)


def _negative_scores(row: ScoredTrace) -> torch.Tensor:
    if not row.positive:
        return row.scores
    boundary = row.boundary
    if boundary is None:
        raise ValueError("positive trace lacks a boundary")
    return row.scores[row.ends < boundary]


def _calibrate(rows: Sequence[ScoredTrace]) -> dict[str, Any]:
    maxima: list[float] = []
    non_drift_segments = 0
    drift_pre_boundary_segments = 0
    for row in rows:
        negative = _negative_scores(row)
        if not negative.numel():
            continue
        maxima.append(float(negative.max().item()))
        if row.positive:
            drift_pre_boundary_segments += 1
        else:
            non_drift_segments += 1
    threshold = finite_sample_upper_threshold(torch.tensor(maxima), ALPHA)
    return {
        "alpha": ALPHA,
        "segment_count": len(maxima),
        "non_drift_segment_count": non_drift_segments,
        "drift_pre_boundary_segment_count": drift_pre_boundary_segments,
        "order_statistic_rank": min(
            int(torch.ceil(torch.tensor((len(maxima) + 1) * (1.0 - ALPHA))).item()),
            len(maxima),
        ),
        "threshold": threshold,
        "calibration_segment_exceedance_count": sum(
            value > threshold for value in maxima
        ),
        "calibration_segment_exceedance_rate": sum(
            value > threshold for value in maxima
        )
        / len(maxima),
        "segment_maxima": maxima,
    }


def _alarm_summary(row: ScoredTrace, threshold: float) -> dict[str, Any]:
    states = row.scores > threshold
    previous = torch.cat((torch.tensor([False]), states[:-1]))
    onset_ends = row.ends[states & ~previous]
    alarm_ends = row.ends[states]
    common = {
        "trace_id": row.trace_id,
        "pair_group_id": row.pair_group_id,
        "fold": row.fold,
        "arm": row.arm,
        "workflow": row.workflow,
        "channel": row.channel,
        "domain": row.domain,
        "positive": row.positive,
        "decode_token_count": row.decode_token_count,
        "eligible_position_count": int(row.ends.numel()),
        "alarm_onset_count": int(onset_ends.numel()),
        "window_ends": [int(value) for value in row.ends.tolist()],
        "scores": [float(value) for value in row.scores.tolist()],
    }
    if row.positive:
        boundary = row.boundary
        if boundary is None:
            raise ValueError("positive trace lacks a boundary")
        pre = alarm_ends[alarm_ends < boundary]
        post = alarm_ends[alarm_ends >= boundary]
        first_post = int(post[0].item()) if post.numel() else None
        return {
            **common,
            "boundary": boundary,
            "negative_position_count": int((row.ends < boundary).sum().item()),
            "pre_boundary_alarm_onset_count": int(
                (onset_ends < boundary).sum().item()
            ),
            "pre_boundary_alarm": bool(pre.numel()),
            "first_post_boundary_alarm": first_post,
            "latency": None if first_post is None else first_post - boundary,
        }
    return {
        **common,
        "boundary": None,
        "negative_position_count": int(row.ends.numel()),
        "pre_boundary_alarm_onset_count": 0,
        "false_alarm": bool(alarm_ends.numel()),
    }


def _safe_rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _recall(rows: Sequence[dict[str, Any]], limit: int | None) -> float:
    positives = [row for row in rows if row["positive"]]
    detected = 0
    for row in positives:
        alarm = row["first_post_boundary_alarm"]
        if row["pre_boundary_alarm"] or alarm is None:
            continue
        if limit is None or alarm <= row["boundary"] + limit:
            detected += 1
    return detected / len(positives)


def _reachable_recall(rows: Sequence[dict[str, Any]], limit: int) -> float:
    positives = [row for row in rows if row["positive"]]
    reachable = 0
    for row in positives:
        earliest = max(2 * WIDTH - 1, row["boundary"])
        latest = min(row["decode_token_count"] - 1, row["boundary"] + limit)
        reachable += earliest <= latest
    return reachable / len(positives)


def _aggregate(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    positives = [row for row in rows if row["positive"]]
    negatives = [row for row in rows if not row["positive"]]
    pre_eligible = [row for row in positives if row["negative_position_count"]]
    clean_hits = [
        row
        for row in positives
        if not row["pre_boundary_alarm"] and row["latency"] is not None
    ]
    latencies = [row["latency"] for row in clean_hits]
    negative_positions = sum(row["negative_position_count"] for row in rows)
    negative_onsets = sum(row["alarm_onset_count"] for row in negatives) + sum(
        row["pre_boundary_alarm_onset_count"] for row in positives
    )
    by_arm: dict[str, Any] = {}
    for arm in ("clean", "benign_control", "attack"):
        selected = [row for row in negatives if row["arm"] == arm]
        count = sum(row["false_alarm"] for row in selected)
        by_arm[arm] = {
            "trace_count": len(selected),
            "false_alarm_count": count,
            "false_alarm_rate": _safe_rate(count, len(selected)),
        }
    return {
        "positive_trace_count": len(positives),
        "non_drift_trace_count": len(negatives),
        "non_drift_false_alarm_count": sum(
            row["false_alarm"] for row in negatives
        ),
        "non_drift_trace_false_alarm_rate": _safe_rate(
            sum(row["false_alarm"] for row in negatives), len(negatives)
        ),
        "positive_pre_boundary_eligible_count": len(pre_eligible),
        "positive_pre_boundary_false_alarm_count": sum(
            row["pre_boundary_alarm"] for row in pre_eligible
        ),
        "positive_pre_boundary_false_alarm_rate": _safe_rate(
            sum(row["pre_boundary_alarm"] for row in pre_eligible),
            len(pre_eligible),
        ),
        "clean_detection_recall_plus_8": _recall(rows, 8),
        "clean_detection_recall_plus_16": _recall(rows, 16),
        "clean_detection_recall_plus_32": _recall(rows, 32),
        "clean_detection_recall_final": _recall(rows, None),
        "reachable_recall_plus_8": _reachable_recall(rows, 8),
        "reachable_recall_plus_16": _reachable_recall(rows, 16),
        "reachable_recall_plus_32": _reachable_recall(rows, 32),
        "clean_hit_count": len(clean_hits),
        "median_clean_detection_latency": (
            statistics.median(latencies) if latencies else None
        ),
        "minimum_clean_detection_latency": min(latencies) if latencies else None,
        "maximum_clean_detection_latency": max(latencies) if latencies else None,
        "negative_eligible_position_count": negative_positions,
        "negative_alarm_onset_count": negative_onsets,
        "alarm_onsets_per_1000_negative_positions": _safe_rate(
            1000 * negative_onsets, negative_positions
        ),
        "non_drift_by_arm": by_arm,
    }


def _domain_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    positives = [row for row in rows if row["positive"]]
    result: dict[str, Any] = {}
    for domain in sorted({row["domain"] for row in positives}):
        selected = [row for row in positives if row["domain"] == domain]
        result[domain] = {
            "drift_count": len(selected),
            "pre_boundary_false_alarm_count": sum(
                row["pre_boundary_alarm"] for row in selected
            ),
            "clean_detection_count_plus_16": sum(
                not row["pre_boundary_alarm"]
                and row["latency"] is not None
                and row["latency"] <= 16
                for row in selected
            ),
            "clean_detection_count_final": sum(
                not row["pre_boundary_alarm"] and row["latency"] is not None
                for row in selected
            ),
        }
    return result


def _evaluate_direction(
    calibration_name: str,
    calibration_rows: Sequence[ScoredTrace],
    test_name: str,
    test_rows: Sequence[ScoredTrace],
) -> dict[str, Any]:
    calibration = _calibrate(calibration_rows)
    summaries = [
        _alarm_summary(row, float(calibration["threshold"])) for row in test_rows
    ]
    return {
        "calibration_batch": calibration_name,
        "test_batch": test_name,
        "calibration": calibration,
        "metrics": _aggregate(summaries),
        "drift_by_domain": _domain_metrics(summaries),
        "trace_results": summaries,
    }


def calculate(b1_dir: Path, b2_dir: Path) -> dict[str, Any]:
    print("loading and validating B1...", flush=True)
    b1_all, b1_valid = load_b1_observations(b1_dir.resolve())
    b1 = [row for row in b1_all if row.brief == "absent"]
    if len(b1) != 120:
        raise ValueError("unexpected B1 pilot cohort size")
    b1_scored = _score(b1)
    del b1, b1_all
    print("B1 scored; loading and validating B2...", flush=True)
    b2_all, b2_valid = load_b2_observations(b2_dir.resolve())
    if len(b2_all) != 240:
        raise ValueError("unexpected B2 pilot cohort size")
    b2_scored = _score(b2_all)
    del b2_all
    print("B2 scored; evaluating both directions...", flush=True)
    return {
        "schema_version": 1,
        "analysis_id": "agent-v2.5-sequential-relative-change-pilot",
        "analysis_role": "post-hoc exploratory development on B1 and B2",
        "plan": PLAN,
        "detector": {
            "feature": "adjacent_block_top_k_selection_jsd",
            "block_width": WIDTH,
            "layer_aggregation": "mean",
            "persistence": 1,
            "strictly_greater_than_threshold": True,
        },
        "datasets": {
            "b1": {
                "sample_index_sha256": B1_SAMPLE_INDEX_SHA256,
                "routing_validation_pass_count": b1_valid,
                "selected_trace_count": len(b1_scored),
                "selected_condition": "response_brief_condition=absent",
            },
            "b2": {
                "sample_index_sha256": B2_SAMPLE_INDEX_SHA256,
                "routing_validation_pass_count": b2_valid,
                "selected_trace_count": len(b2_scored),
                "selected_condition": "all traces; all are brief absent",
            },
        },
        "directions": {
            "b1_to_b2": _evaluate_direction("b1", b1_scored, "b2", b2_scored),
            "b2_to_b1": _evaluate_direction("b2", b2_scored, "b1", b1_scored),
        },
    }


def main() -> None:
    args = _args()
    result = calculate(args.b1, args.b2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "output": str(args.output),
        "directions": {
            name: {
                "threshold": row["calibration"]["threshold"],
                **row["metrics"],
            }
            for name, row in result["directions"].items()
        },
    }, indent=2))


if __name__ == "__main__":
    main()
