#!/usr/bin/env python3
"""Run the fixed learned-direction, trace-relative routing pilot."""

from __future__ import annotations

import argparse
import json
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
from explore_agent_v2_relative_change import (  # noqa: E402
    ScoredTrace,
    _aggregate,
    _alarm_summary,
    _calibrate,
    _domain_metrics,
)
from phase_a.classifier import RidgeClassifier, fit_ridge_classifier  # noqa: E402
from phase_a.sequential import (  # noqa: E402
    causal_history_median_difference,
    evenly_spaced_indices,
    window_features,
)
from score_agent_v2_b2_frozen import (  # noqa: E402
    _load_observations as load_b2_observations,
)


PLAN = "docs/sequential_directional_relative_pilot_plan.md"
WIDTH = 8
POSITIVE_OFFSETS = (7, 15, 23)
NEGATIVE_ANCHOR_COUNT = 2
FEATURE_DIMENSION = 16 * 64
B1_SAMPLE_INDEX_SHA256 = (
    "f1a95c5bf36d3233f445e4cb507c89e59c13eda1619af317c4efefadcea72df1"
)
B2_SAMPLE_INDEX_SHA256 = (
    "e9412dde1b328f21943cc59f2a5db37797142026cd392166af202c285cd73942"
)


@dataclass(frozen=True)
class FeatureTrace:
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
    features: torch.Tensor


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
            / "sequential_directional_relative_pilot.json"
        ),
    )
    return parser.parse_args()


def _feature_rows(observations: Sequence[Observation]) -> tuple[FeatureTrace, ...]:
    result: list[FeatureTrace] = []
    for observation in observations:
        ends, features = window_features(
            observation.sequence, "route_selection", WIDTH
        )
        if features.shape != (ends.numel(), FEATURE_DIMENSION):
            raise ValueError(f"feature shape mismatch for {observation.trace_id}")
        result.append(
            FeatureTrace(
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
                features=features,
            )
        )
    return tuple(result)


def _feature_at_end(row: FeatureTrace, end: int) -> torch.Tensor:
    positions = (row.ends == end).nonzero(as_tuple=False).reshape(-1)
    if positions.numel() != 1:
        raise ValueError(f"window end {end} unavailable for {row.trace_id}")
    return row.features[int(positions.item())]


def _training_anchors(
    rows: Sequence[FeatureTrace],
) -> tuple[torch.Tensor, torch.Tensor, dict[str, int]]:
    features: list[torch.Tensor] = []
    labels: list[float] = []
    negative_trace_count = 0
    positive_trace_count = 0
    for row in rows:
        if not row.ends.numel():
            continue
        first_end = int(row.ends[0].item())
        last_end = int(row.ends[-1].item())
        if row.positive:
            boundary = row.boundary
            if boundary is None:
                raise ValueError("positive trace lacks a boundary")
            negative_ends = evenly_spaced_indices(
                first_end, min(last_end, boundary - 1), NEGATIVE_ANCHOR_COUNT
            )
            positive_ends = tuple(
                boundary + offset
                for offset in POSITIVE_OFFSETS
                if first_end <= boundary + offset <= last_end
            )
            if not positive_ends:
                raise ValueError(f"positive trace has no full post anchor: {row.trace_id}")
            positive_trace_count += 1
            for end in negative_ends:
                features.append(_feature_at_end(row, end))
                labels.append(-1.0)
            for end in positive_ends:
                features.append(_feature_at_end(row, end))
                labels.append(1.0)
        else:
            negative_trace_count += 1
            for end in evenly_spaced_indices(
                first_end, last_end, NEGATIVE_ANCHOR_COUNT
            ):
                features.append(_feature_at_end(row, end))
                labels.append(-1.0)
    matrix = torch.stack(features)
    targets = torch.tensor(labels, dtype=torch.float32)
    return matrix, targets, {
        "negative_trace_count": negative_trace_count,
        "positive_trace_count": positive_trace_count,
        "negative_anchor_count": int((targets < 0).sum().item()),
        "positive_anchor_count": int((targets > 0).sum().item()),
        "total_anchor_count": int(targets.numel()),
    }


def _fit(rows: Sequence[FeatureTrace]) -> tuple[RidgeClassifier, dict[str, int]]:
    features, labels, counts = _training_anchors(rows)
    model = fit_ridge_classifier(features, labels, penalty=FEATURE_DIMENSION)
    return model, counts


def _relative_rows(
    rows: Sequence[FeatureTrace], model: RidgeClassifier
) -> tuple[ScoredTrace, ...]:
    result: list[ScoredTrace] = []
    for row in rows:
        state_scores = model.score(row.features).reshape(-1)
        relative_scores, relative_ends = causal_history_median_difference(
            state_scores, row.ends, gap=WIDTH
        )
        result.append(
            ScoredTrace(
                trace_id=row.trace_id,
                pair_group_id=row.pair_group_id,
                fold=row.fold,
                arm=row.arm,
                workflow=row.workflow,
                channel=row.channel,
                domain=row.domain,
                positive=row.positive,
                boundary=row.boundary,
                decode_token_count=row.decode_token_count,
                ends=relative_ends,
                scores=relative_scores,
            )
        )
    return tuple(result)


def _model_metadata(model: RidgeClassifier, anchors: dict[str, int]) -> dict[str, Any]:
    return {
        "feature_dimension": FEATURE_DIMENSION,
        "penalty": FEATURE_DIMENSION,
        "anchors": anchors,
        "feature_mean": [float(value) for value in model.feature_mean.tolist()],
        "feature_scale": [float(value) for value in model.feature_scale.tolist()],
        "weights": [float(value) for value in model.weights.tolist()],
        "target_mean": float(model.target_mean.item()),
    }


def _evaluate_direction(
    source_name: str,
    source: Sequence[FeatureTrace],
    target_name: str,
    target: Sequence[FeatureTrace],
) -> dict[str, Any]:
    model, anchors = _fit(source)
    source_relative = _relative_rows(source, model)
    calibration = _calibrate(source_relative)
    target_relative = _relative_rows(target, model)
    summaries = [
        _alarm_summary(row, float(calibration["threshold"]))
        for row in target_relative
    ]
    return {
        "source_batch": source_name,
        "target_batch": target_name,
        "model": _model_metadata(model, anchors),
        "calibration": calibration,
        "metrics": _aggregate(summaries),
        "drift_by_domain": _domain_metrics(summaries),
        "trace_results": summaries,
    }


def calculate(b1_dir: Path, b2_dir: Path) -> dict[str, Any]:
    print("loading and validating B1...", flush=True)
    b1_all, b1_valid = load_b1_observations(b1_dir.resolve())
    b1_selected = [row for row in b1_all if row.brief == "absent"]
    if len(b1_selected) != 120:
        raise ValueError("unexpected B1 pilot cohort size")
    b1 = _feature_rows(b1_selected)
    del b1_selected, b1_all

    print("B1 featurized; loading and validating B2...", flush=True)
    b2_all, b2_valid = load_b2_observations(b2_dir.resolve())
    if len(b2_all) != 240:
        raise ValueError("unexpected B2 pilot cohort size")
    b2 = _feature_rows(b2_all)
    del b2_all

    print("B2 featurized; fitting and evaluating both directions...", flush=True)
    return {
        "schema_version": 1,
        "analysis_id": "agent-v2.5-sequential-directional-relative-pilot",
        "analysis_role": "post-hoc exploratory development on B1 and B2",
        "plan": PLAN,
        "detector": {
            "state_feature": "8-token route_selection",
            "state_model": "standardized ridge least-squares",
            "positive_offsets": list(POSITIVE_OFFSETS),
            "negative_anchor_count_per_segment": NEGATIVE_ANCHOR_COUNT,
            "relative_score": "state score minus causal non-overlapping history median",
            "history_gap": WIDTH,
            "persistence": 1,
            "strictly_greater_than_threshold": True,
        },
        "datasets": {
            "b1": {
                "sample_index_sha256": B1_SAMPLE_INDEX_SHA256,
                "routing_validation_pass_count": b1_valid,
                "selected_trace_count": len(b1),
            },
            "b2": {
                "sample_index_sha256": B2_SAMPLE_INDEX_SHA256,
                "routing_validation_pass_count": b2_valid,
                "selected_trace_count": len(b2),
            },
        },
        "directions": {
            "b1_to_b2": _evaluate_direction("b1", b1, "b2", b2),
            "b2_to_b1": _evaluate_direction("b2", b2, "b1", b1),
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
                "anchors": row["model"]["anchors"],
                **row["metrics"],
            }
            for name, row in result["directions"].items()
        },
    }, indent=2))


if __name__ == "__main__":
    main()
