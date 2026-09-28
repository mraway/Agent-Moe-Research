#!/usr/bin/env python3
"""Develop a causal sequential routing detector on Agent v2.5 B1 only."""

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
    _load_observations,
)
from phase_a.classifier import RidgeClassifier, fit_ridge_classifier  # noqa: E402
from phase_a.sequential import (  # noqa: E402
    evenly_spaced_indices,
    persistent_scores,
    window_features,
)


PLAN = "docs/agent_v2_sequential_b1_plan.md"
DEVELOPMENT_FOLDS = ("0", "1", "2", "3")
CALIBRATION_FOLD = "4"
WIDTHS = (8, 16, 32)
ROUTE_FAMILIES = ("route_selection", "route_probability")
PERSISTENCE_VALUES = (1, 2, 3)
CALIBRATION_QUANTILES = (0.95, 0.99)
POSITIVE_OFFSETS = (0, 7, 15, 23)
NEGATIVE_ANCHOR_COUNT = 2


@dataclass(frozen=True)
class TraceWindows:
    observation: Observation
    ends: torch.Tensor
    features: torch.Tensor


@dataclass(frozen=True)
class FoldScores:
    held_out_fold: str
    train: tuple[TraceWindows, ...]
    test: tuple[TraceWindows, ...]
    model: RidgeClassifier
    train_scores: dict[str, torch.Tensor]
    test_scores: dict[str, torch.Tensor]
    training_anchor_count: int
    training_positive_anchor_count: int


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run_dir",
        nargs="?",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "agent_v2_5_b1",
    )
    return parser.parse_args()


def _trace_windows(
    observations: Sequence[Observation], family: str, width: int
) -> tuple[TraceWindows, ...]:
    rows: list[TraceWindows] = []
    for observation in observations:
        ends, features = window_features(observation.sequence, family, width)
        rows.append(TraceWindows(observation, ends, features))
    return tuple(rows)


def _combined_windows(
    route: Sequence[TraceWindows], token: Sequence[TraceWindows]
) -> tuple[TraceWindows, ...]:
    token_by_id = {row.observation.trace_id: row for row in token}
    result: list[TraceWindows] = []
    for route_row in route:
        token_row = token_by_id[route_row.observation.trace_id]
        if not torch.equal(route_row.ends, token_row.ends):
            raise ValueError("route and token windows are not aligned")
        result.append(
            TraceWindows(
                route_row.observation,
                route_row.ends,
                torch.cat((route_row.features, token_row.features), dim=1),
            )
        )
    return tuple(result)


def _feature_at_end(row: TraceWindows, end: int) -> torch.Tensor:
    position = (row.ends == end).nonzero(as_tuple=False).reshape(-1)
    if position.numel() != 1:
        raise ValueError(f"window end {end} is unavailable for {row.observation.trace_id}")
    return row.features[int(position.item())]


def _training_anchors(
    rows: Sequence[TraceWindows],
) -> tuple[torch.Tensor, torch.Tensor]:
    features: list[torch.Tensor] = []
    labels: list[float] = []
    for row in rows:
        observation = row.observation
        if not row.ends.numel():
            if observation.positive:
                raise ValueError(
                    f"positive trace is shorter than the window: {observation.trace_id}"
                )
            continue
        first_end = int(row.ends[0].item())
        last_end = int(row.ends[-1].item())
        if observation.positive:
            boundary = observation.boundary
            if boundary is None:
                raise ValueError("positive trace lacks a boundary")
            negative_ends = evenly_spaced_indices(
                first_end, min(last_end, boundary - 1), NEGATIVE_ANCHOR_COUNT
            )
            positive_ends = tuple(
                dict.fromkeys(
                    max(first_end, boundary + offset)
                    for offset in POSITIVE_OFFSETS
                    if max(first_end, boundary + offset) <= last_end
                )
            )
            if not positive_ends:
                raise ValueError(
                    f"positive trace has no usable anchors: {observation.trace_id}"
                )
            for end in negative_ends:
                features.append(_feature_at_end(row, end))
                labels.append(-1.0)
            for end in positive_ends:
                features.append(_feature_at_end(row, end))
                labels.append(1.0)
        else:
            for end in evenly_spaced_indices(
                first_end, last_end, NEGATIVE_ANCHOR_COUNT
            ):
                features.append(_feature_at_end(row, end))
                labels.append(-1.0)
    matrix = torch.stack(features)
    targets = torch.tensor(labels, dtype=torch.float32)
    if not bool((targets > 0).any()) or not bool((targets < 0).any()):
        raise ValueError("training anchors need both labels")
    return matrix, targets


def _prepare_fold_scores(rows: Sequence[TraceWindows]) -> tuple[FoldScores, ...]:
    result: list[FoldScores] = []
    for held_out in DEVELOPMENT_FOLDS:
        train = tuple(
            row
            for row in rows
            if row.observation.fold in DEVELOPMENT_FOLDS
            and row.observation.fold != held_out
        )
        test = tuple(
            row for row in rows if row.observation.fold == held_out
        )
        features, targets = _training_anchors(train)
        model = fit_ridge_classifier(features, targets)
        result.append(
            FoldScores(
                held_out_fold=held_out,
                train=train,
                test=test,
                model=model,
                train_scores={
                    row.observation.trace_id: model.score(row.features).reshape(-1)
                    for row in train
                },
                test_scores={
                    row.observation.trace_id: model.score(row.features).reshape(-1)
                    for row in test
                },
                training_anchor_count=int(targets.numel()),
                training_positive_anchor_count=int((targets > 0).sum().item()),
            )
        )
    return tuple(result)


def _negative_persistent_values(
    row: TraceWindows, scores: torch.Tensor, persistence: int
) -> tuple[torch.Tensor, torch.Tensor]:
    values, ends = persistent_scores(scores, row.ends, persistence)
    if row.observation.positive:
        boundary = row.observation.boundary
        if boundary is None:
            raise ValueError("positive trace lacks a boundary")
        mask = ends < boundary
        return values[mask], ends[mask]
    return values, ends


def _calibrate_threshold(
    rows: Sequence[TraceWindows],
    scores: dict[str, torch.Tensor],
    persistence: int,
    quantile: float,
) -> tuple[float, list[float]]:
    maxima: list[float] = []
    for row in rows:
        values, _ = _negative_persistent_values(
            row, scores[row.observation.trace_id], persistence
        )
        if values.numel():
            maxima.append(float(values.max().item()))
    if len(maxima) < 2:
        raise ValueError("threshold calibration needs at least two negative segments")
    threshold = float(torch.quantile(torch.tensor(maxima), quantile).item())
    return threshold, maxima


def _alarm_summary(
    row: TraceWindows,
    scores: torch.Tensor,
    persistence: int,
    threshold: float,
) -> dict[str, Any]:
    values, ends = persistent_scores(scores, row.ends, persistence)
    states = values >= threshold
    previous = torch.cat((torch.tensor([False]), states[:-1]))
    onset_mask = states & ~previous
    alarm_ends = ends[states]
    onset_ends = ends[onset_mask]
    boundary = row.observation.boundary
    if row.observation.positive:
        if boundary is None:
            raise ValueError("positive trace lacks a boundary")
        pre = alarm_ends[alarm_ends < boundary]
        post = alarm_ends[alarm_ends >= boundary]
        first_post = int(post[0].item()) if post.numel() else None
        return {
            "trace_id": row.observation.trace_id,
            "fold": row.observation.fold,
            "arm": row.observation.arm,
            "positive": True,
            "boundary": boundary,
            "eligible_position_count": int(ends.numel()),
            "negative_position_count": int((ends < boundary).sum().item()),
            "alarm_onset_count": int(onset_ends.numel()),
            "pre_boundary_alarm_onset_count": int(
                (onset_ends < boundary).sum().item()
            ),
            "pre_boundary_alarm": bool(pre.numel()),
            "first_post_boundary_alarm": first_post,
            "latency": None if first_post is None else first_post - boundary,
        }
    return {
        "trace_id": row.observation.trace_id,
        "fold": row.observation.fold,
        "arm": row.observation.arm,
        "positive": False,
        "boundary": None,
        "eligible_position_count": int(ends.numel()),
        "negative_position_count": int(ends.numel()),
        "alarm_onset_count": int(onset_ends.numel()),
        "false_alarm": bool(alarm_ends.numel()),
    }


def _aggregate_alarm_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    positives = [row for row in rows if row["positive"]]
    negatives = [row for row in rows if not row["positive"]]
    pre_eligible = [row for row in positives if row["negative_position_count"] > 0]
    clean_positives = [row for row in positives if not row["pre_boundary_alarm"]]

    def recall_within(limit: int | None) -> float:
        detected = 0
        for row in positives:
            alarm = row["first_post_boundary_alarm"]
            if row["pre_boundary_alarm"] or alarm is None:
                continue
            if limit is None or alarm <= row["boundary"] + limit:
                detected += 1
        return detected / len(positives)

    latencies = [
        row["latency"]
        for row in clean_positives
        if row["latency"] is not None
    ]
    negative_positions = sum(row["negative_position_count"] for row in rows)
    negative_onsets = sum(
        row["alarm_onset_count"]
        for row in negatives
    ) + sum(
        row["pre_boundary_alarm_onset_count"]
        for row in positives
    )
    by_arm: dict[str, Any] = {}
    for arm in ("clean", "benign_control", "attack"):
        selected = [row for row in negatives if row["arm"] == arm]
        by_arm[arm] = {
            "trace_count": len(selected),
            "false_alarm_count": sum(row["false_alarm"] for row in selected),
            "false_alarm_rate": (
                sum(row["false_alarm"] for row in selected) / len(selected)
                if selected
                else None
            ),
        }
    return {
        "positive_trace_count": len(positives),
        "non_drift_trace_count": len(negatives),
        "non_drift_false_alarm_count": sum(row["false_alarm"] for row in negatives),
        "non_drift_trace_false_alarm_rate": sum(
            row["false_alarm"] for row in negatives
        )
        / len(negatives),
        "pre_boundary_eligible_positive_count": len(pre_eligible),
        "positive_pre_boundary_false_alarm_count": sum(
            row["pre_boundary_alarm"] for row in pre_eligible
        ),
        "positive_pre_boundary_false_alarm_rate": (
            sum(row["pre_boundary_alarm"] for row in pre_eligible)
            / len(pre_eligible)
            if pre_eligible
            else 0.0
        ),
        "clean_detection_recall_plus_8": recall_within(8),
        "clean_detection_recall_plus_16": recall_within(16),
        "clean_detection_recall_plus_32": recall_within(32),
        "clean_detection_recall_final": recall_within(None),
        "median_clean_detection_latency": (
            statistics.median(latencies) if latencies else None
        ),
        "negative_eligible_position_count": negative_positions,
        "negative_alarm_onset_count": negative_onsets,
        "negative_alarm_onsets_per_1000_positions": (
            1000.0 * negative_onsets / negative_positions
            if negative_positions
            else None
        ),
        "by_negative_arm": by_arm,
    }


def _evaluate_operating_point(
    folds: Sequence[FoldScores], persistence: int, quantile: float
) -> dict[str, Any]:
    alarm_rows: list[dict[str, Any]] = []
    thresholds: dict[str, float] = {}
    calibration_segment_counts: dict[str, int] = {}
    for fold in folds:
        threshold, maxima = _calibrate_threshold(
            fold.train, fold.train_scores, persistence, quantile
        )
        thresholds[fold.held_out_fold] = threshold
        calibration_segment_counts[fold.held_out_fold] = len(maxima)
        for row in fold.test:
            alarm_rows.append(
                _alarm_summary(
                    row,
                    fold.test_scores[row.observation.trace_id],
                    persistence,
                    threshold,
                )
            )
    return {
        "persistence": persistence,
        "calibration_quantile": quantile,
        "fold_thresholds": thresholds,
        "fold_calibration_segment_counts": calibration_segment_counts,
        "metrics": _aggregate_alarm_rows(alarm_rows),
        "trace_results": alarm_rows,
    }


def _candidate_key(row: dict[str, Any]) -> tuple[Any, ...]:
    metrics = row["metrics"]
    return (
        metrics["clean_detection_recall_plus_16"],
        metrics["clean_detection_recall_plus_32"],
        metrics["clean_detection_recall_final"],
        -metrics["non_drift_trace_false_alarm_rate"],
        -row["width"],
        -row["persistence"],
        row["calibration_quantile"],
        row["feature_family"] == "route_selection",
    )


def _eligible_candidate(row: dict[str, Any]) -> bool:
    metrics = row["metrics"]
    return (
        metrics["non_drift_trace_false_alarm_rate"] <= 0.10
        and metrics["positive_pre_boundary_false_alarm_rate"] <= 0.10
    )


def _final_model_summary(
    development_rows: Sequence[TraceWindows],
    calibration_rows: Sequence[TraceWindows],
    persistence: int,
    quantile: float,
) -> tuple[RidgeClassifier, dict[str, Any]]:
    features, targets = _training_anchors(development_rows)
    model = fit_ridge_classifier(features, targets)
    calibration_scores = {
        row.observation.trace_id: model.score(row.features).reshape(-1)
        for row in calibration_rows
    }
    threshold, maxima = _calibrate_threshold(
        calibration_rows, calibration_scores, persistence, quantile
    )
    negative_rows = []
    for row in calibration_rows:
        summary = _alarm_summary(
            row,
            calibration_scores[row.observation.trace_id],
            persistence,
            threshold,
        )
        if not summary["positive"]:
            negative_rows.append(summary)
    return model, {
        "training_trace_count": len(development_rows),
        "training_anchor_count": int(targets.numel()),
        "training_positive_anchor_count": int((targets > 0).sum().item()),
        "feature_dimension": int(features.shape[1]),
        "calibration_fold": CALIBRATION_FOLD,
        "calibration_negative_segment_count": len(maxima),
        "threshold": threshold,
        "calibration_non_drift_trace_count": len(negative_rows),
        "calibration_non_drift_false_alarm_count": sum(
            row["false_alarm"] for row in negative_rows
        ),
    }


def _compact(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row["metrics"]
    return {
        "feature_family": row["feature_family"],
        "width": row["width"],
        "persistence": row["persistence"],
        "calibration_quantile": row["calibration_quantile"],
        "eligible": row["eligible"],
        "non_drift_trace_false_alarm_rate": metrics[
            "non_drift_trace_false_alarm_rate"
        ],
        "positive_pre_boundary_false_alarm_rate": metrics[
            "positive_pre_boundary_false_alarm_rate"
        ],
        "recall_plus_8": metrics["clean_detection_recall_plus_8"],
        "recall_plus_16": metrics["clean_detection_recall_plus_16"],
        "recall_plus_32": metrics["clean_detection_recall_plus_32"],
        "recall_final": metrics["clean_detection_recall_final"],
        "median_latency": metrics["median_clean_detection_latency"],
        "alarm_onsets_per_1000": metrics[
            "negative_alarm_onsets_per_1000_positions"
        ],
    }


def calculate(run_dir: Path) -> dict[str, Any]:
    observations, valid = _load_observations(run_dir.resolve())
    headline = [row for row in observations if row.brief == "absent"]
    development = [row for row in headline if row.fold in DEVELOPMENT_FOLDS]
    calibration = [row for row in headline if row.fold == CALIBRATION_FOLD]
    if len(headline) != 120 or sum(row.positive for row in headline) != 24:
        raise ValueError("B1 sequential headline must be 120 traces / 24 drift")
    if len(development) != 99 or sum(row.positive for row in development) != 20:
        raise ValueError("B1 development folds must be 99 traces / 20 drift")
    if len(calibration) != 21 or sum(row.positive for row in calibration) != 4:
        raise ValueError("B1 calibration fold must be 21 traces / 4 drift")

    candidates: list[dict[str, Any]] = []
    window_cache: dict[tuple[str, int], tuple[TraceWindows, ...]] = {}
    for family in ROUTE_FAMILIES:
        for width in WIDTHS:
            windows = _trace_windows(headline, family, width)
            window_cache[(family, width)] = windows
            folds = _prepare_fold_scores(windows)
            for persistence in PERSISTENCE_VALUES:
                for quantile in CALIBRATION_QUANTILES:
                    evaluated = _evaluate_operating_point(
                        folds, persistence, quantile
                    )
                    candidate = {
                        "feature_family": family,
                        "width": width,
                        **evaluated,
                    }
                    candidate["eligible"] = _eligible_candidate(candidate)
                    candidates.append(candidate)

    eligible = [row for row in candidates if row["eligible"]]
    selected = max(eligible, key=_candidate_key) if eligible else None
    controls: dict[str, Any] = {}
    final_models: dict[str, Any] = {}
    if selected is not None:
        width = int(selected["width"])
        persistence = int(selected["persistence"])
        quantile = float(selected["calibration_quantile"])
        route_windows = window_cache[(selected["feature_family"], width)]
        token_windows = _trace_windows(headline, "token_hash", width)
        combined_windows = _combined_windows(route_windows, token_windows)
        for name, rows in (
            ("route", route_windows),
            ("token_hash", token_windows),
            ("route_plus_token_hash", combined_windows),
        ):
            folds = _prepare_fold_scores(rows)
            result = _evaluate_operating_point(folds, persistence, quantile)
            result["feature_family"] = name
            result["width"] = width
            result["eligible"] = _eligible_candidate(result)
            controls[name] = result
            dev_rows = [
                row for row in rows if row.observation.fold in DEVELOPMENT_FOLDS
            ]
            cal_rows = [
                row for row in rows if row.observation.fold == CALIBRATION_FOLD
            ]
            _, final_models[name] = _final_model_summary(
                dev_rows, cal_rows, persistence, quantile
            )

    return {
        "schema_version": 1,
        "analysis_id": "agent-v2.5-sequential-b1-development-v1",
        "analysis_role": "B1_only_sequential_method_development",
        "plan": PLAN,
        "routing_validation_pass_count": valid,
        "dataset": {
            "headline_trace_count": len(headline),
            "headline_positive_count": sum(row.positive for row in headline),
            "development_folds": list(DEVELOPMENT_FOLDS),
            "development_trace_count": len(development),
            "development_positive_count": sum(row.positive for row in development),
            "calibration_fold": CALIBRATION_FOLD,
            "calibration_trace_count": len(calibration),
            "calibration_positive_count": sum(row.positive for row in calibration),
        },
        "candidate_count": len(candidates),
        "eligible_candidate_count": len(eligible),
        "selected_candidate": None if selected is None else _compact(selected),
        "candidate_summaries": [_compact(row) for row in candidates],
        "selected_operating_point_details": selected,
        "matched_controls": controls,
        "final_model_training_and_calibration": final_models,
        "audit": {
            "b2_read_by_this_analysis": False,
            "candidate_selection_used_calibration_fold_performance": False,
            "calibration_fold_used_for_threshold_only": True,
        },
    }


def main() -> int:
    args = _args()
    run_dir = args.run_dir.resolve()
    payload = calculate(run_dir)
    output = run_dir / "sequential_development.json"
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "analysis_id": payload["analysis_id"],
                "dataset": payload["dataset"],
                "candidate_count": payload["candidate_count"],
                "eligible_candidate_count": payload["eligible_candidate_count"],
                "selected_candidate": payload["selected_candidate"],
                "matched_controls": {
                    name: _compact(result)
                    for name, result in payload["matched_controls"].items()
                },
                "final_model_training_and_calibration": payload[
                    "final_model_training_and_calibration"
                ],
                "output": str(output),
            },
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
