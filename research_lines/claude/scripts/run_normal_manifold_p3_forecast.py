#!/usr/bin/env python3
"""Run the preregistered normal-only routing forecast and CUSUM experiment."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase_a.normal_manifold import (  # noqa: E402
    B1_INDEX_SHA256,
    B2_INDEX_SHA256,
    ManifoldTrace,
    age_bin,
    aggregate_alarm_summaries,
    alarm_summary,
    ensure_routing_cache,
    finite_upper_threshold,
    load_cached_routing,
    probability_window_signatures,
    read_manifold_traces,
    select_records,
    selection_window_signatures,
)
from routing import validate_trace  # noqa: E402


PLAN = "docs/normal_manifold_p3_forecast_plan.md"
BLOCK_WIDTH = 4
PCA_RANK_PER_BAND = 16
ALPHA = 0.10
FIT_FOLDS = {0, 1, 2}
CALIBRATION_FOLDS = {3, 4}
FAMILY_ORDER = ("status_only", "knowledge_qa", "status_and_knowledge")
AGE_ORDER = ("8-15", "16-31", "32-63", "64-127", "128+")
MAD_FACTOR = 1.4826
SCALE_FLOOR = 1e-6


@dataclass(frozen=True)
class Variant:
    name: str
    feature_family: str
    bands: tuple[tuple[int, ...], ...]
    sequential_mode: str = "cusum"
    shuffled: bool = False
    clean_benign_fit: bool = False


MIDDLE = tuple(range(5, 11))
LATE = tuple(range(11, 16))
VARIANTS = (
    Variant("selection_forecast_cusum_middle_late", "selection", (MIDDLE, LATE)),
    Variant(
        "selection_forecast_single_middle_late",
        "selection",
        (MIDDLE, LATE),
        sequential_mode="single",
    ),
    Variant("selection_forecast_cusum_middle", "selection", (MIDDLE,)),
    Variant("selection_forecast_cusum_late", "selection", (LATE,)),
    Variant("probability_forecast_cusum_middle_late", "probability", (MIDDLE, LATE)),
    Variant(
        "selection_forecast_cusum_middle_late_shuffled",
        "selection",
        (MIDDLE, LATE),
        shuffled=True,
    ),
    Variant(
        "selection_forecast_cusum_middle_late_clean_benign_fit",
        "selection",
        (MIDDLE, LATE),
        clean_benign_fit=True,
    ),
)


@dataclass(frozen=True)
class BlockTrace:
    record: ManifoldTrace
    ends: torch.Tensor
    features: torch.Tensor


@dataclass(frozen=True)
class StateBand:
    layers: tuple[int, ...]
    mean: torch.Tensor
    components: torch.Tensor
    eigenvalues: torch.Tensor


@dataclass(frozen=True)
class StateModel:
    bands: tuple[StateBand, ...]


@dataclass(frozen=True)
class RidgeForecaster:
    feature_mean: torch.Tensor
    feature_scale: torch.Tensor
    target_mean: torch.Tensor
    weights: torch.Tensor
    penalty: float
    residual_center: torch.Tensor
    residual_scale: torch.Tensor
    fit_q: torch.Tensor
    transition_count: int

    def predict(self, features: torch.Tensor) -> torch.Tensor:
        standardized = (features - self.feature_mean) / self.feature_scale
        return standardized @ self.weights + self.target_mean


@dataclass(frozen=True)
class ForecastModel:
    state: StateModel
    forecaster: RidgeForecaster


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--b1", type=Path, default=ROOT / "artifacts" / "agent_v2" / "agent_v2_5_b1"
    )
    parser.add_argument(
        "--b2", type=Path, default=ROOT / "artifacts" / "agent_v2" / "agent_v2_5_b2"
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "normal_manifold_cache",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "normal_manifold_p3_forecast" / "result.json",
    )
    return parser.parse_args()


def _block_trace(record: ManifoldTrace, variant: Variant) -> BlockTrace:
    routing = load_cached_routing(record)
    if variant.feature_family == "selection":
        ends, signatures = selection_window_signatures(
            routing["top_k_ids"],
            BLOCK_WIDTH,
            shuffled_trace_id=record.trace_id if variant.shuffled else None,
        )
    elif variant.feature_family == "probability":
        ends, signatures = probability_window_signatures(
            routing["probabilities"], BLOCK_WIDTH
        )
    else:
        raise ValueError(f"unknown feature family: {variant.feature_family}")
    keep = (ends + 1).remainder(BLOCK_WIDTH) == 0
    block_ends = ends[keep]
    block_features = signatures[keep].clamp_min(0.0).sqrt()
    if block_ends.numel() < 3:
        raise ValueError(f"trace has fewer than three complete blocks: {record.trace_id}")
    expected = torch.arange(
        BLOCK_WIDTH - 1,
        BLOCK_WIDTH * block_ends.numel(),
        BLOCK_WIDTH,
        dtype=torch.long,
    )
    if not torch.equal(block_ends, expected):
        raise ValueError(f"block endpoints are not causal and contiguous: {record.trace_id}")
    return BlockTrace(record, block_ends, block_features)


def _weighted_mean(values: torch.Tensor, weights: torch.Tensor) -> torch.Tensor:
    weights = weights.reshape(-1).to(values.dtype)
    if values.shape[0] != weights.numel() or float(weights.sum().item()) <= 0.0:
        raise ValueError("weighted observations are not aligned")
    normalized = weights / weights.sum()
    return (values * normalized[:, None]).sum(dim=0)


def _weighted_pca(
    traces: Sequence[BlockTrace], layers: tuple[int, ...]
) -> StateBand:
    matrices = []
    weights = []
    for trace in traces:
        matrix = trace.features[:, layers, :].reshape(trace.ends.numel(), -1)
        matrices.append(matrix)
        weights.append(torch.full((trace.ends.numel(),), 1.0 / trace.ends.numel()))
    values = torch.cat(matrices).float()
    raw_weights = torch.cat(weights).float()
    mean = _weighted_mean(values, raw_weights)
    normalized = raw_weights / raw_weights.sum()
    weighted_centered = (values - mean) * normalized.sqrt()[:, None]
    _, singular_values, vh = torch.linalg.svd(weighted_centered, full_matrices=False)
    if vh.shape[0] < PCA_RANK_PER_BAND:
        raise ValueError("not enough normal blocks for fixed state rank")
    return StateBand(
        layers,
        mean,
        vh[:PCA_RANK_PER_BAND].contiguous(),
        singular_values[:PCA_RANK_PER_BAND].square().clamp_min(1e-12),
    )


def _fit_state(traces: Sequence[BlockTrace], variant: Variant) -> StateModel:
    return StateModel(tuple(_weighted_pca(traces, band) for band in variant.bands))


def _state_coordinates(trace: BlockTrace, model: StateModel) -> torch.Tensor:
    parts = []
    for band in model.bands:
        matrix = trace.features[:, band.layers, :].reshape(trace.ends.numel(), -1)
        parts.append((matrix - band.mean) @ band.components.T)
    return torch.cat(parts, dim=1)


def _one_hot(value: str, universe: Sequence[str]) -> torch.Tensor:
    if value not in universe:
        raise ValueError(f"unknown categorical value: {value}")
    result = torch.zeros(len(universe), dtype=torch.float32)
    result[universe.index(value)] = 1.0
    return result


def _predictor_rows(
    traces: Sequence[BlockTrace], state: StateModel
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, tuple[str, ...]]:
    inputs = []
    targets = []
    weights = []
    trace_ids = []
    for trace in traces:
        coordinates = _state_coordinates(trace, state)
        transition_count = coordinates.shape[0] - 2
        if transition_count <= 0:
            raise ValueError(f"no forecast transitions: {trace.record.trace_id}")
        family = _one_hot(trace.record.workflow_family, FAMILY_ORDER)
        for index in range(2, coordinates.shape[0]):
            age = _one_hot(age_bin(int(trace.ends[index].item())), AGE_ORDER)
            inputs.append(
                torch.cat(
                    (
                        coordinates[index - 1],
                        coordinates[index - 1] - coordinates[index - 2],
                        family,
                        age,
                    )
                )
            )
            targets.append(coordinates[index])
            weights.append(1.0 / transition_count)
            trace_ids.append(trace.record.trace_id)
    return (
        torch.stack(inputs),
        torch.stack(targets),
        torch.tensor(weights, dtype=torch.float32),
        tuple(trace_ids),
    )


def _weighted_quantile_columns(
    values: torch.Tensor, weights: torch.Tensor, quantile: float
) -> torch.Tensor:
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must lie in [0, 1]")
    result = []
    normalized = weights / weights.sum()
    for column in range(values.shape[1]):
        ordered, indices = values[:, column].sort()
        cumulative = normalized[indices].cumsum(0)
        position = int((cumulative >= quantile).nonzero(as_tuple=False)[0].item())
        result.append(ordered[position])
    return torch.stack(result)


def _residual_q(
    residuals: torch.Tensor, center: torch.Tensor, scale: torch.Tensor
) -> torch.Tensor:
    return ((residuals - center) / scale).square().mean(dim=1)


def _fit_forecaster(
    traces: Sequence[BlockTrace], state: StateModel
) -> RidgeForecaster:
    inputs, targets, raw_weights, _ = _predictor_rows(traces, state)
    weights = raw_weights / raw_weights.mean()
    feature_mean = _weighted_mean(inputs, weights)
    feature_variance = _weighted_mean((inputs - feature_mean).square(), weights)
    feature_scale = feature_variance.sqrt().clamp_min(1e-8)
    standardized = (inputs - feature_mean) / feature_scale
    target_mean = _weighted_mean(targets, weights)
    centered_targets = targets - target_mean
    penalty = float(inputs.shape[1])
    gram = standardized.T @ (weights[:, None] * standardized)
    rhs = standardized.T @ (weights[:, None] * centered_targets)
    ridge_weights = torch.linalg.solve(
        gram + penalty * torch.eye(gram.shape[0]), rhs
    )
    predictions = standardized @ ridge_weights + target_mean
    residuals = targets - predictions
    residual_center = _weighted_quantile_columns(residuals, raw_weights, 0.5)
    absolute = (residuals - residual_center).abs()
    mad = _weighted_quantile_columns(absolute, raw_weights, 0.5)
    residual_std = _weighted_mean(
        (residuals - _weighted_mean(residuals, raw_weights)).square(), raw_weights
    ).sqrt()
    residual_scale = torch.maximum(
        MAD_FACTOR * mad, 0.1 * residual_std
    ).clamp_min(SCALE_FLOOR)
    fit_q = _residual_q(residuals, residual_center, residual_scale).sort().values
    return RidgeForecaster(
        feature_mean,
        feature_scale,
        target_mean,
        ridge_weights,
        penalty,
        residual_center,
        residual_scale,
        fit_q,
        inputs.shape[0],
    )


def _fit_model(records: Sequence[ManifoldTrace], variant: Variant) -> ForecastModel:
    traces = tuple(_block_trace(record, variant) for record in records)
    state = _fit_state(traces, variant)
    return ForecastModel(state, _fit_forecaster(traces, state))


def _cusum(surprise: torch.Tensor) -> torch.Tensor:
    values = []
    current = 0.0
    for value in surprise.tolist():
        current = max(0.0, current + float(value) - 1.0)
        values.append(current)
    return torch.tensor(values, dtype=torch.float32)


def _score_record(
    record: ManifoldTrace, variant: Variant, model: ForecastModel
) -> dict[str, Any]:
    trace = _block_trace(record, variant)
    inputs, targets, _, _ = _predictor_rows((trace,), model.state)
    residuals = targets - model.forecaster.predict(inputs)
    q = _residual_q(
        residuals,
        model.forecaster.residual_center,
        model.forecaster.residual_scale,
    )
    surprise = []
    for value in q:
        p_value = (
            1.0 + float((model.forecaster.fit_q >= value).sum().item())
        ) / (model.forecaster.fit_q.numel() + 1.0)
        surprise.append(-math.log(p_value))
    u = torch.tensor(surprise, dtype=torch.float32)
    cumulative = _cusum(u)
    scores = cumulative if variant.sequential_mode == "cusum" else u
    return {
        "ends": trace.ends[2:],
        "q": q,
        "surprise": u,
        "cusum": cumulative,
        "scores": scores,
    }


def _calibrate(
    records: Sequence[ManifoldTrace], variant: Variant, model: ForecastModel
) -> dict[str, Any]:
    maxima = []
    traces = []
    for record in records:
        scored = _score_record(record, variant, model)
        maximum = float(scored["scores"].max().item())
        maxima.append(maximum)
        traces.append({"trace_id": record.trace_id, "arm": record.arm, "maximum": maximum})
    result = finite_upper_threshold(maxima, ALPHA)
    result["traces"] = traces
    return result


def _evaluate(
    records: Sequence[ManifoldTrace],
    variant: Variant,
    model: ForecastModel,
    threshold: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = []
    for index, record in enumerate(records, start=1):
        scored = _score_record(record, variant, model)
        summary = alarm_summary(record, scored["ends"], scored["scores"], threshold)
        summary.update(
            {
                "q_scores": [float(value) for value in scored["q"].tolist()],
                "surprise_scores": [float(value) for value in scored["surprise"].tolist()],
                "cusum_scores": [float(value) for value in scored["cusum"].tolist()],
            }
        )
        rows.append(summary)
        if index % 40 == 0 or index == len(records):
            print(f"    scored {index}/{len(records)} target traces", flush=True)
    return aggregate_alarm_summaries(rows), rows


def _model_metadata(model: ForecastModel) -> dict[str, Any]:
    forecaster = model.forecaster
    return {
        "state_dimension": int(sum(band.components.shape[0] for band in model.state.bands)),
        "bands": [
            {
                "layers": list(band.layers),
                "rank": int(band.components.shape[0]),
                "eigenvalues": [float(value) for value in band.eigenvalues.tolist()],
            }
            for band in model.state.bands
        ],
        "predictor_input_dimension": int(forecaster.weights.shape[0]),
        "predictor_output_dimension": int(forecaster.weights.shape[1]),
        "ridge_penalty": forecaster.penalty,
        "fit_transition_count": forecaster.transition_count,
        "residual_center": [float(value) for value in forecaster.residual_center.tolist()],
        "residual_scale": [float(value) for value in forecaster.residual_scale.tolist()],
        "fit_q_count": int(forecaster.fit_q.numel()),
        "fit_q_range": [float(forecaster.fit_q.min().item()), float(forecaster.fit_q.max().item())],
    }


def _direction(
    source_name: str,
    source: Sequence[ManifoldTrace],
    target_name: str,
    target: Sequence[ManifoldTrace],
    variant: Variant,
) -> dict[str, Any]:
    fit_arms = {"clean", "benign_control"} if variant.clean_benign_fit else None
    fit = select_records(source, normal=True, folds=FIT_FOLDS, arms=fit_arms)
    calibration = select_records(source, normal=True, folds=CALIBRATION_FOLDS)
    print(
        f"  {source_name}->{target_name} {variant.name}: fit={len(fit)} calibration={len(calibration)}",
        flush=True,
    )
    model = _fit_model(fit, variant)
    calibrated = _calibrate(calibration, variant, model)
    metrics, trace_results = _evaluate(
        target, variant, model, float(calibrated["threshold"])
    )
    return {
        "source_batch": source_name,
        "target_batch": target_name,
        "variant": variant.name,
        "fit_normal_trace_count": len(fit),
        "calibration_normal_trace_count": len(calibration),
        "model": _model_metadata(model),
        "calibration": calibrated,
        "metrics": metrics,
        "trace_results": trace_results,
    }


def calculate(b1_dir: Path, b2_dir: Path, cache_dir: Path) -> dict[str, Any]:
    observation = ROOT / "artifacts" / "agent_v2" / "routing_observation_atlas" / "observation.json"
    b1 = read_manifold_traces(b1_dir.resolve(), cache_dir.resolve(), observation, "b1")
    b2 = read_manifold_traces(b2_dir.resolve(), cache_dir.resolve(), observation, "b2")
    cache = ensure_routing_cache((*b1, *b2), cache_dir.resolve(), validate_trace_fn=validate_trace)
    directions: dict[str, Any] = {}
    for variant in VARIANTS:
        directions[f"b1_to_b2::{variant.name}"] = _direction("b1", b1, "b2", b2, variant)
        directions[f"b2_to_b1::{variant.name}"] = _direction("b2", b2, "b1", b1, variant)
    return {
        "schema_version": 1,
        "analysis_id": "normal-manifold-p3-route-forecast-cusum",
        "analysis_role": "post-hoc exploratory development on B1/B2",
        "plan": PLAN,
        "datasets": {
            "b1": {"sample_index_sha256": B1_INDEX_SHA256, "trace_count": len(b1)},
            "b2": {"sample_index_sha256": B2_INDEX_SHA256, "trace_count": len(b2)},
        },
        "cache": cache,
        "detector": {
            "block_width": BLOCK_WIDTH,
            "rank_per_band": PCA_RANK_PER_BAND,
            "fit_folds": sorted(FIT_FOLDS),
            "calibration_folds": sorted(CALIBRATION_FOLDS),
            "alpha": ALPHA,
            "cusum_reference_mean": 1.0,
            "family_order": list(FAMILY_ORDER),
            "age_order": list(AGE_ORDER),
        },
        "directions": directions,
    }


def main() -> None:
    args = _args()
    result = calculate(args.b1, args.b2, args.cache_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "primary": {
            key: {"threshold": value["calibration"]["threshold"], **value["metrics"]}
            for key, value in result["directions"].items()
            if key.endswith("::selection_forecast_cusum_middle_late")
        },
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
