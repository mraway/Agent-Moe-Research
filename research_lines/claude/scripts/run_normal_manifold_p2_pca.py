#!/usr/bin/env python3
"""Run the preregistered normal-only conditional-PCA control-chart experiment."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
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
    evenly_spaced_positions,
    finite_upper_threshold,
    load_cached_routing,
    probability_window_signatures,
    read_manifold_traces,
    select_records,
    selection_window_signatures,
)
from routing import validate_trace  # noqa: E402


PLAN = "docs/normal_manifold_p2_pca_plan.md"
WIDTH = 8
ANCHORS_PER_TRACE = 8
PCA_RANK_PER_BAND = 16
CELL_SHRINKAGE = 16.0
ALPHA = 0.10
FIT_FOLDS = {0, 1, 2}
CALIBRATION_FOLDS = {3, 4}


@dataclass(frozen=True)
class Variant:
    name: str
    feature_family: str
    bands: tuple[tuple[int, ...], ...]
    score_mode: str = "t2_q"
    conditional: bool = True
    shuffled: bool = False
    clean_benign_fit: bool = False


MIDDLE = tuple(range(5, 11))
LATE = tuple(range(11, 16))
VARIANTS = (
    Variant("selection_conditional_t2_q_middle_late", "selection", (MIDDLE, LATE)),
    Variant(
        "selection_conditional_t2_only_middle_late",
        "selection",
        (MIDDLE, LATE),
        score_mode="t2",
    ),
    Variant(
        "selection_conditional_q_only_middle_late",
        "selection",
        (MIDDLE, LATE),
        score_mode="q",
    ),
    Variant(
        "selection_unconditional_t2_q_middle_late",
        "selection",
        (MIDDLE, LATE),
        conditional=False,
    ),
    Variant("selection_conditional_t2_q_middle", "selection", (MIDDLE,)),
    Variant("selection_conditional_t2_q_late", "selection", (LATE,)),
    Variant("probability_conditional_t2_q_middle_late", "probability", (MIDDLE, LATE)),
    Variant(
        "selection_conditional_t2_q_middle_late_shuffled",
        "selection",
        (MIDDLE, LATE),
        shuffled=True,
    ),
    Variant(
        "selection_conditional_t2_q_middle_late_clean_benign_fit",
        "selection",
        (MIDDLE, LATE),
        clean_benign_fit=True,
    ),
)


@dataclass(frozen=True)
class PCABand:
    layers: tuple[int, ...]
    residual_mean: torch.Tensor
    components: torch.Tensor
    eigenvalues: torch.Tensor


@dataclass(frozen=True)
class PCAModel:
    conditional: bool
    global_mean: torch.Tensor
    age_means: dict[str, torch.Tensor]
    cell_means: dict[str, torch.Tensor]
    cell_counts: dict[str, int]
    bands: tuple[PCABand, ...]
    fit_t2: torch.Tensor
    fit_q: torch.Tensor


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
        default=ROOT / "artifacts" / "agent_v2" / "normal_manifold_p2_pca" / "result.json",
    )
    return parser.parse_args()


def _signatures(record: ManifoldTrace, variant: Variant) -> tuple[torch.Tensor, torch.Tensor]:
    routing = load_cached_routing(record)
    if variant.feature_family == "selection":
        ends, signatures = selection_window_signatures(
            routing["top_k_ids"],
            WIDTH,
            shuffled_trace_id=record.trace_id if variant.shuffled else None,
        )
    elif variant.feature_family == "probability":
        ends, signatures = probability_window_signatures(routing["probabilities"], WIDTH)
    else:
        raise ValueError(f"unknown feature family: {variant.feature_family}")
    if ends.numel() < 2:
        raise ValueError(f"trace too short for persistent P2 score: {record.trace_id}")
    return ends, signatures.clamp_min(0.0).sqrt()


def _fit_anchors(
    records: Sequence[ManifoldTrace], variant: Variant
) -> tuple[torch.Tensor, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    features: list[torch.Tensor] = []
    trace_ids: list[str] = []
    families: list[str] = []
    ages: list[str] = []
    for record in records:
        ends, signatures = _signatures(record, variant)
        for position in evenly_spaced_positions(ends.numel(), ANCHORS_PER_TRACE):
            features.append(signatures[position])
            trace_ids.append(record.trace_id)
            families.append(record.workflow_family)
            ages.append(age_bin(int(ends[position].item())))
    return torch.stack(features), tuple(trace_ids), tuple(families), tuple(ages)


def _condition_key(family: str, age: str) -> str:
    return f"{family}::{age}"


def _condition_means(
    features: torch.Tensor,
    families: Sequence[str],
    ages: Sequence[str],
    conditional: bool,
) -> tuple[torch.Tensor, dict[str, torch.Tensor], dict[str, torch.Tensor], dict[str, int]]:
    global_mean = features.mean(dim=0)
    if not conditional:
        return global_mean, {}, {}, {"global": features.shape[0]}
    by_age: dict[str, list[torch.Tensor]] = defaultdict(list)
    by_cell: dict[str, list[torch.Tensor]] = defaultdict(list)
    for feature, family, age in zip(features, families, ages, strict=True):
        by_age[age].append(feature)
        by_cell[_condition_key(family, age)].append(feature)
    age_means = {key: torch.stack(values).mean(dim=0) for key, values in by_age.items()}
    cell_means: dict[str, torch.Tensor] = {}
    cell_counts: dict[str, int] = {}
    for key, values in by_cell.items():
        age = key.split("::", maxsplit=1)[1]
        count = len(values)
        total = torch.stack(values).sum(dim=0)
        cell_means[key] = (total + CELL_SHRINKAGE * age_means[age]) / (
            count + CELL_SHRINKAGE
        )
        cell_counts[key] = count
    return global_mean, age_means, cell_means, cell_counts


def _lookup_mean(
    model: PCAModel | None,
    family: str,
    age: str,
    *,
    global_mean: torch.Tensor | None = None,
    age_means: dict[str, torch.Tensor] | None = None,
    cell_means: dict[str, torch.Tensor] | None = None,
    conditional: bool | None = None,
) -> torch.Tensor:
    if model is not None:
        global_mean = model.global_mean
        age_means = model.age_means
        cell_means = model.cell_means
        conditional = model.conditional
    if global_mean is None or age_means is None or cell_means is None or conditional is None:
        raise ValueError("condition model is incomplete")
    if not conditional:
        return global_mean
    return cell_means.get(_condition_key(family, age), age_means.get(age, global_mean))


def _fit_band(residuals: torch.Tensor, layers: tuple[int, ...]) -> PCABand:
    matrix = residuals[:, layers, :].reshape(residuals.shape[0], -1).float()
    residual_mean = matrix.mean(dim=0)
    centered = matrix - residual_mean
    _, singular_values, vh = torch.linalg.svd(centered, full_matrices=False)
    if vh.shape[0] < PCA_RANK_PER_BAND:
        raise ValueError("not enough fit anchors for fixed PCA rank")
    components = vh[:PCA_RANK_PER_BAND].contiguous()
    eigenvalues = (
        singular_values[:PCA_RANK_PER_BAND].square() / max(1, matrix.shape[0] - 1)
    ).clamp_min(1e-8)
    return PCABand(layers, residual_mean, components, eigenvalues)


def _raw_scores(
    features: torch.Tensor,
    families: Sequence[str],
    ages: Sequence[str],
    model: PCAModel,
) -> tuple[torch.Tensor, torch.Tensor]:
    means = torch.stack(
        [_lookup_mean(model, family, age) for family, age in zip(families, ages, strict=True)]
    )
    residuals = features - means
    t2_parts: list[torch.Tensor] = []
    q_parts: list[torch.Tensor] = []
    for band in model.bands:
        matrix = residuals[:, band.layers, :].reshape(residuals.shape[0], -1)
        centered = matrix - band.residual_mean
        coordinates = centered @ band.components.T
        t2_parts.append(
            (coordinates.square() / band.eigenvalues).sum(dim=1)
            / float(band.components.shape[0])
        )
        reconstruction = coordinates @ band.components
        q_parts.append((centered - reconstruction).square().mean(dim=1))
    return torch.stack(t2_parts).mean(dim=0), torch.stack(q_parts).mean(dim=0)


def _fit_model(records: Sequence[ManifoldTrace], variant: Variant) -> PCAModel:
    features, _, families, ages = _fit_anchors(records, variant)
    global_mean, age_means, cell_means, cell_counts = _condition_means(
        features, families, ages, variant.conditional
    )
    means = torch.stack(
        [
            _lookup_mean(
                None,
                family,
                age,
                global_mean=global_mean,
                age_means=age_means,
                cell_means=cell_means,
                conditional=variant.conditional,
            )
            for family, age in zip(families, ages, strict=True)
        ]
    )
    residuals = features - means
    bands = tuple(_fit_band(residuals, layers) for layers in variant.bands)
    temporary = PCAModel(
        variant.conditional,
        global_mean,
        age_means,
        cell_means,
        cell_counts,
        bands,
        torch.empty(0),
        torch.empty(0),
    )
    fit_t2, fit_q = _raw_scores(features, families, ages, temporary)
    return PCAModel(
        variant.conditional,
        global_mean,
        age_means,
        cell_means,
        cell_counts,
        bands,
        fit_t2.sort().values,
        fit_q.sort().values,
    )


def _empirical_rarity(values: torch.Tensor, reference: torch.Tensor) -> torch.Tensor:
    result = []
    for value in values:
        p_value = (1.0 + float((reference >= value).sum().item())) / (
            reference.numel() + 1.0
        )
        result.append(-math.log(p_value))
    return torch.tensor(result, dtype=torch.float32)


def _score_record(record: ManifoldTrace, variant: Variant, model: PCAModel) -> dict[str, Any]:
    ends, features = _signatures(record, variant)
    families = (record.workflow_family,) * ends.numel()
    ages = tuple(age_bin(int(end)) for end in ends.tolist())
    t2, q = _raw_scores(features, families, ages, model)
    rarity_t2 = _empirical_rarity(t2, model.fit_t2)
    rarity_q = _empirical_rarity(q, model.fit_q)
    if variant.score_mode == "t2_q":
        rarity = torch.maximum(rarity_t2, rarity_q)
    elif variant.score_mode == "t2":
        rarity = rarity_t2
    elif variant.score_mode == "q":
        rarity = rarity_q
    else:
        raise ValueError(f"unknown score mode: {variant.score_mode}")
    return {
        "record": record,
        "ends": ends,
        "t2": t2,
        "q": q,
        "rarity_t2": rarity_t2,
        "rarity_q": rarity_q,
        "rarity": rarity,
        "persistent_ends": ends[1:],
        "persistent": torch.minimum(rarity[:-1], rarity[1:]),
    }


def _calibrate(
    records: Sequence[ManifoldTrace], variant: Variant, model: PCAModel
) -> dict[str, Any]:
    maxima = []
    traces = []
    for record in records:
        scored = _score_record(record, variant, model)
        maximum = float(scored["persistent"].max().item())
        maxima.append(maximum)
        traces.append({"trace_id": record.trace_id, "arm": record.arm, "maximum": maximum})
    result = finite_upper_threshold(maxima, ALPHA)
    result["traces"] = traces
    return result


def _evaluate(
    records: Sequence[ManifoldTrace],
    variant: Variant,
    model: PCAModel,
    threshold: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = []
    for index, record in enumerate(records, start=1):
        scored = _score_record(record, variant, model)
        summary = alarm_summary(
            record, scored["persistent_ends"], scored["persistent"], threshold
        )
        summary.update(
            {
                "raw_endpoints": [int(value) for value in scored["ends"].tolist()],
                "t2_scores": [float(value) for value in scored["t2"].tolist()],
                "q_scores": [float(value) for value in scored["q"].tolist()],
                "rarity_t2": [float(value) for value in scored["rarity_t2"].tolist()],
                "rarity_q": [float(value) for value in scored["rarity_q"].tolist()],
                "combined_rarity": [float(value) for value in scored["rarity"].tolist()],
            }
        )
        rows.append(summary)
        if index % 40 == 0 or index == len(records):
            print(f"    scored {index}/{len(records)} target traces", flush=True)
    return aggregate_alarm_summaries(rows), rows


def _model_metadata(model: PCAModel) -> dict[str, Any]:
    return {
        "conditional": model.conditional,
        "condition_cell_counts": model.cell_counts,
        "age_mean_count": len(model.age_means),
        "fit_tail_count": int(model.fit_t2.numel()),
        "fit_t2_range": [float(model.fit_t2.min().item()), float(model.fit_t2.max().item())],
        "fit_q_range": [float(model.fit_q.min().item()), float(model.fit_q.max().item())],
        "bands": [
            {
                "layers": list(band.layers),
                "rank": int(band.components.shape[0]),
                "eigenvalues": [float(value) for value in band.eigenvalues.tolist()],
            }
            for band in model.bands
        ],
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
        "analysis_id": "normal-manifold-p2-conditional-pca",
        "analysis_role": "post-hoc exploratory development on B1/B2",
        "plan": PLAN,
        "datasets": {
            "b1": {"sample_index_sha256": B1_INDEX_SHA256, "trace_count": len(b1)},
            "b2": {"sample_index_sha256": B2_INDEX_SHA256, "trace_count": len(b2)},
        },
        "cache": cache,
        "detector": {
            "width": WIDTH,
            "anchors_per_trace": ANCHORS_PER_TRACE,
            "rank_per_band": PCA_RANK_PER_BAND,
            "cell_shrinkage": CELL_SHRINKAGE,
            "fit_folds": sorted(FIT_FOLDS),
            "calibration_folds": sorted(CALIBRATION_FOLDS),
            "alpha": ALPHA,
            "persistence": 2,
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
            if key.endswith("::selection_conditional_t2_q_middle_late")
        },
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
