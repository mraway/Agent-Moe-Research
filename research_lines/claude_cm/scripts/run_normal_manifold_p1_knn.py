#!/usr/bin/env python3
"""Run the preregistered normal-only layered-kNN manifold experiment."""

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


PLAN = "docs/normal_manifold_p1_knn_plan.md"
WIDTH = 8
REFERENCE_ANCHORS_PER_TRACE = 8
MINIMUM_CELL_SIZE = 20
NEIGHBOR_K = 5
ALPHA = 0.10
FIT_FOLDS = {0, 1, 2}
CALIBRATION_FOLDS = {3, 4}


@dataclass(frozen=True)
class Variant:
    name: str
    feature_family: str
    bands: tuple[tuple[int, ...], ...]
    shuffled: bool = False
    clean_benign_fit: bool = False


VARIANTS = (
    Variant("selection_middle_late", "selection", (tuple(range(5, 11)), tuple(range(11, 16)))),
    Variant("selection_early", "selection", (tuple(range(0, 5)),)),
    Variant("selection_middle", "selection", (tuple(range(5, 11)),)),
    Variant("selection_late", "selection", (tuple(range(11, 16)),)),
    Variant("probability_middle_late", "probability", (tuple(range(5, 11)), tuple(range(11, 16)))),
    Variant(
        "selection_middle_late_shuffled",
        "selection",
        (tuple(range(5, 11)), tuple(range(11, 16))),
        shuffled=True,
    ),
    Variant(
        "selection_middle_late_clean_benign_fit",
        "selection",
        (tuple(range(5, 11)), tuple(range(11, 16))),
        clean_benign_fit=True,
    ),
)


@dataclass(frozen=True)
class ReferenceBank:
    features: torch.Tensor
    trace_ids: tuple[str, ...]
    workflow_families: tuple[str, ...]
    ages: tuple[str, ...]
    bands: tuple[tuple[int, ...], ...]
    reference_raw_scores: torch.Tensor
    reference_pool_keys: tuple[str, ...]
    tail_scores: dict[str, torch.Tensor]


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
        "--cache-dir",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "normal_manifold_cache",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "normal_manifold_p1_knn" / "result.json",
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
    if not ends.numel():
        raise ValueError(f"trace too short for P1: {record.trace_id}")
    return ends, signatures.clamp_min(0.0).sqrt()


def _pairwise_distance(
    left: torch.Tensor,
    right: torch.Tensor,
    bands: Sequence[Sequence[int]],
) -> torch.Tensor:
    """Mean per-layer Hellinger distance, equal-weighted across layer bands."""

    if left.ndim != 3 or right.ndim != 3 or left.shape[1:] != right.shape[1:]:
        raise ValueError("routing features are not compatible")
    result = torch.zeros((left.shape[0], right.shape[0]), dtype=torch.float32)
    for band in bands:
        band_distance = torch.zeros_like(result)
        for layer in band:
            affinity = left[:, layer, :] @ right[:, layer, :].T
            band_distance += (1.0 - affinity.clamp(0.0, 1.0)).clamp_min(0.0).sqrt()
        result += band_distance / float(len(band))
    return result / float(len(bands))


def _full_pool_key(family: str, age: str, bank: ReferenceBank | None, metadata: dict[str, tuple[str, ...]]) -> tuple[str, torch.Tensor]:
    families = bank.workflow_families if bank is not None else metadata["families"]
    ages = bank.ages if bank is not None else metadata["ages"]
    exact = torch.tensor(
        [candidate_family == family and candidate_age == age for candidate_family, candidate_age in zip(families, ages, strict=True)],
        dtype=torch.bool,
    )
    if int(exact.sum().item()) >= MINIMUM_CELL_SIZE:
        return f"cell::{family}::{age}", exact.nonzero(as_tuple=False).reshape(-1)
    family_mask = torch.tensor([candidate == family for candidate in families], dtype=torch.bool)
    if int(family_mask.sum().item()) >= MINIMUM_CELL_SIZE:
        return f"family::{family}", family_mask.nonzero(as_tuple=False).reshape(-1)
    return "global", torch.arange(len(families), dtype=torch.long)


def _build_bank(records: Sequence[ManifoldTrace], variant: Variant) -> ReferenceBank:
    features: list[torch.Tensor] = []
    trace_ids: list[str] = []
    families: list[str] = []
    ages: list[str] = []
    for record in records:
        ends, candidates = _signatures(record, variant)
        for position in evenly_spaced_positions(ends.numel(), REFERENCE_ANCHORS_PER_TRACE):
            features.append(candidates[position])
            trace_ids.append(record.trace_id)
            families.append(record.workflow_family)
            ages.append(age_bin(int(ends[position].item())))
    matrix = torch.stack(features)
    metadata = {"families": tuple(families), "ages": tuple(ages)}
    placeholder = ReferenceBank(
        matrix,
        tuple(trace_ids),
        tuple(families),
        tuple(ages),
        variant.bands,
        torch.empty(0),
        (),
        {},
    )
    distances = _pairwise_distance(matrix, matrix, variant.bands)
    raw_scores: list[torch.Tensor] = []
    pool_keys: list[str] = []
    for index, (trace_id, family, age) in enumerate(
        zip(trace_ids, families, ages, strict=True)
    ):
        key, candidates = _full_pool_key(family, age, placeholder, metadata)
        candidates = candidates[
            torch.tensor([trace_ids[int(candidate)] != trace_id for candidate in candidates], dtype=torch.bool)
        ]
        if candidates.numel() < NEIGHBOR_K:
            raise ValueError(f"leave-trace-out pool is smaller than k for {key}")
        raw_scores.append(torch.kthvalue(distances[index, candidates], NEIGHBOR_K).values)
        pool_keys.append(key)
    raw = torch.stack(raw_scores).float()
    grouped: dict[str, list[torch.Tensor]] = defaultdict(list)
    for key, value in zip(pool_keys, raw, strict=True):
        grouped[key].append(value)
    grouped["global_reference"] = [value for value in raw]
    tails = {key: torch.stack(values).sort().values for key, values in grouped.items()}
    return ReferenceBank(
        matrix,
        tuple(trace_ids),
        tuple(families),
        tuple(ages),
        variant.bands,
        raw,
        tuple(pool_keys),
        tails,
    )


def _score_features(
    features: torch.Tensor,
    ends: torch.Tensor,
    record: ManifoldTrace,
    bank: ReferenceBank,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    distances = _pairwise_distance(features, bank.features, bank.bands)
    raw_values: list[torch.Tensor] = []
    rarity_values: list[torch.Tensor] = []
    for position, end in enumerate(ends.tolist()):
        key, candidates = _full_pool_key(record.workflow_family, age_bin(int(end)), bank, {})
        if candidates.numel() < NEIGHBOR_K:
            raise ValueError(f"query pool is smaller than k: {key}")
        raw = torch.kthvalue(distances[position, candidates], NEIGHBOR_K).values
        tail = bank.tail_scores.get(key, bank.tail_scores["global_reference"])
        p_value = (1.0 + float((tail >= raw).sum().item())) / (tail.numel() + 1.0)
        raw_values.append(raw)
        rarity_values.append(torch.tensor(-math.log(p_value), dtype=torch.float32))
    raw_scores = torch.stack(raw_values)
    rarity = torch.stack(rarity_values)
    persistent = torch.minimum(rarity[:-1], rarity[1:])
    persistent_ends = ends[1:]
    return raw_scores, rarity, persistent_ends, persistent


def _score_record(record: ManifoldTrace, variant: Variant, bank: ReferenceBank) -> dict[str, Any]:
    ends, features = _signatures(record, variant)
    raw, rarity, persistent_ends, persistent = _score_features(features, ends, record, bank)
    return {
        "record": record,
        "raw_endpoints": ends,
        "raw_scores": raw,
        "rarity_scores": rarity,
        "persistent_endpoints": persistent_ends,
        "persistent_scores": persistent,
    }


def _calibrate(
    records: Sequence[ManifoldTrace], variant: Variant, bank: ReferenceBank
) -> dict[str, Any]:
    maxima: list[float] = []
    rows: list[dict[str, Any]] = []
    for record in records:
        scored = _score_record(record, variant, bank)
        maximum = float(scored["persistent_scores"].max().item())
        maxima.append(maximum)
        rows.append({"trace_id": record.trace_id, "arm": record.arm, "maximum": maximum})
    result = finite_upper_threshold(maxima, ALPHA)
    result["traces"] = rows
    return result


def _evaluate(
    records: Sequence[ManifoldTrace],
    variant: Variant,
    bank: ReferenceBank,
    threshold: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        scored = _score_record(record, variant, bank)
        summary = alarm_summary(
            record,
            scored["persistent_endpoints"],
            scored["persistent_scores"],
            threshold,
        )
        summary.update(
            {
                "raw_endpoints": [int(value) for value in scored["raw_endpoints"].tolist()],
                "raw_knn_scores": [float(value) for value in scored["raw_scores"].tolist()],
                "rarity_scores": [float(value) for value in scored["rarity_scores"].tolist()],
            }
        )
        rows.append(summary)
        if index % 40 == 0 or index == len(records):
            print(f"    scored {index}/{len(records)} target traces", flush=True)
    return aggregate_alarm_summaries(rows), rows


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
    bank = _build_bank(fit, variant)
    calibration_result = _calibrate(calibration, variant, bank)
    threshold = float(calibration_result["threshold"])
    metrics, trace_results = _evaluate(target, variant, bank, threshold)
    return {
        "source_batch": source_name,
        "target_batch": target_name,
        "variant": variant.name,
        "fit_normal_trace_count": len(fit),
        "calibration_normal_trace_count": len(calibration),
        "reference_anchor_count": int(bank.features.shape[0]),
        "reference_pool_key_counts": dict(Counter(bank.reference_pool_keys)),
        "reference_tail_counts": {key: int(value.numel()) for key, value in bank.tail_scores.items()},
        "calibration": calibration_result,
        "metrics": metrics,
        "trace_results": trace_results,
    }


def calculate(b1_dir: Path, b2_dir: Path, cache_dir: Path) -> dict[str, Any]:
    observation = ROOT / "artifacts" / "agent_v2" / "routing_observation_atlas" / "observation.json"
    b1 = read_manifold_traces(b1_dir.resolve(), cache_dir.resolve(), observation, "b1")
    b2 = read_manifold_traces(b2_dir.resolve(), cache_dir.resolve(), observation, "b2")
    all_records = (*b1, *b2)
    cache = ensure_routing_cache(all_records, cache_dir.resolve(), validate_trace_fn=validate_trace)
    directions: dict[str, Any] = {}
    for variant in VARIANTS:
        directions[f"b1_to_b2::{variant.name}"] = _direction("b1", b1, "b2", b2, variant)
        directions[f"b2_to_b1::{variant.name}"] = _direction("b2", b2, "b1", b1, variant)
    return {
        "schema_version": 1,
        "analysis_id": "normal-manifold-p1-layered-knn",
        "analysis_role": "post-hoc exploratory development on B1/B2",
        "plan": PLAN,
        "datasets": {
            "b1": {"sample_index_sha256": B1_INDEX_SHA256, "trace_count": len(b1)},
            "b2": {"sample_index_sha256": B2_INDEX_SHA256, "trace_count": len(b2)},
        },
        "cache": cache,
        "detector": {
            "width": WIDTH,
            "reference_anchors_per_trace": REFERENCE_ANCHORS_PER_TRACE,
            "minimum_cell_size": MINIMUM_CELL_SIZE,
            "neighbor_k": NEIGHBOR_K,
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
            key: {
                "threshold": value["calibration"]["threshold"],
                **value["metrics"],
            }
            for key, value in result["directions"].items()
            if key.endswith("::selection_middle_late")
        },
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()
