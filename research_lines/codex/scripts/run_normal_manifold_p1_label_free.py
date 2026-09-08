#!/usr/bin/env python3
"""Run the label-free, single-agent replacement for P1 normal kNN."""

from __future__ import annotations

import argparse
import json
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
    aggregate_alarm_summaries,
    alarm_summary,
    ensure_routing_cache,
    evenly_spaced_positions,
    finite_upper_threshold,
    load_cached_routing,
    read_manifold_traces,
    select_records,
    selection_window_signatures,
)
from routing import validate_trace  # noqa: E402
from run_normal_manifold_p1_knn import _pairwise_distance  # noqa: E402


PLAN = "docs/normal_manifold_p1_label_free_plan.md"
WIDTH = 8
REFERENCE_ANCHORS_PER_TRACE = 8
NEIGHBOR_K = 5
LOCAL_AGE_REFERENCE_COUNT = 128
LOCAL_SCALE_FLOOR_FRACTION = 0.25
ALPHA = 0.10
FIT_FOLDS = {0, 1, 2}
CALIBRATION_FOLDS = {3, 4}
BANDS = (tuple(range(5, 11)), tuple(range(11, 16)))
CUSUM_ALLOWANCE = 0.5
LEAKY_RHO = 0.95
METHOD_ORDER = (
    "endpoint_z",
    "rolling_mean_4",
    "rolling_mean_8",
    "cusum_0_5",
    "leaky_cusum_0_95_0_5",
)
PRIMARY_METHOD = "leaky_cusum_0_95_0_5"


@dataclass(frozen=True)
class LabelFreeBank:
    """Detector state plus explicitly audit-only workflow metadata."""

    features: torch.Tensor
    trace_ids: tuple[str, ...]
    ends: torch.Tensor
    reference_raw_scores: torch.Tensor
    global_robust_scale: float
    audit_workflow_families: tuple[str, ...]


@dataclass(frozen=True)
class BaseScores:
    ends: torch.Tensor
    raw: torch.Tensor
    local_median: torch.Tensor
    local_scale: torch.Tensor
    standardized: torch.Tensor
    neighbor_indices: torch.Tensor


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
        default=(
            ROOT
            / "artifacts"
            / "agent_v2"
            / "normal_manifold_p1_label_free"
            / "result.json"
        ),
    )
    return parser.parse_args()


def _signatures(record: ManifoldTrace) -> tuple[torch.Tensor, torch.Tensor]:
    routing = load_cached_routing(record)
    ends, signatures = selection_window_signatures(routing["top_k_ids"], WIDTH)
    if not ends.numel():
        raise ValueError(f"trace too short for label-free P1: {record.trace_id}")
    return ends, signatures.clamp_min(0.0).sqrt()


def _robust_scale(values: torch.Tensor) -> torch.Tensor:
    values = values.float().reshape(-1)
    if values.numel() < 2:
        raise ValueError("robust scale needs at least two values")
    q25, q75 = torch.quantile(values, torch.tensor([0.25, 0.75]))
    return ((q75 - q25) / 1.349).clamp_min(torch.finfo(torch.float32).eps)


def _build_bank(records: Sequence[ManifoldTrace]) -> LabelFreeBank:
    features: list[torch.Tensor] = []
    trace_ids: list[str] = []
    ends: list[int] = []
    audit_families: list[str] = []
    for record in records:
        candidate_ends, candidates = _signatures(record)
        for position in evenly_spaced_positions(
            candidate_ends.numel(), REFERENCE_ANCHORS_PER_TRACE
        ):
            features.append(candidates[position])
            trace_ids.append(record.trace_id)
            ends.append(int(candidate_ends[position].item()))
            audit_families.append(record.workflow_family)
    matrix = torch.stack(features)
    end_tensor = torch.tensor(ends, dtype=torch.long)
    distances = _pairwise_distance(matrix, matrix, BANDS)
    raw_scores: list[torch.Tensor] = []
    for index, trace_id in enumerate(trace_ids):
        candidates = torch.tensor(
            [candidate_trace != trace_id for candidate_trace in trace_ids],
            dtype=torch.bool,
        ).nonzero(as_tuple=False).reshape(-1)
        if candidates.numel() < NEIGHBOR_K:
            raise ValueError("leave-trace-out global bank is smaller than k")
        raw_scores.append(
            torch.kthvalue(distances[index, candidates], NEIGHBOR_K).values
        )
    raw = torch.stack(raw_scores).float()
    return LabelFreeBank(
        features=matrix,
        trace_ids=tuple(trace_ids),
        ends=end_tensor,
        reference_raw_scores=raw,
        global_robust_scale=float(_robust_scale(raw).item()),
        audit_workflow_families=tuple(audit_families),
    )


def _continuous_age_standardize(
    raw: torch.Tensor,
    ends: torch.Tensor,
    reference_raw: torch.Tensor,
    reference_ends: torch.Tensor,
    *,
    local_count: int = LOCAL_AGE_REFERENCE_COUNT,
    scale_floor_fraction: float = LOCAL_SCALE_FLOOR_FRACTION,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    """Robustly standardize raw distance using nearby continuous decode ages."""

    if raw.ndim != 1 or ends.ndim != 1 or raw.numel() != ends.numel():
        raise ValueError("query raw scores and endpoints must be aligned vectors")
    if reference_raw.ndim != 1 or reference_ends.ndim != 1:
        raise ValueError("reference raw scores and endpoints must be vectors")
    if reference_raw.numel() != reference_ends.numel():
        raise ValueError("reference raw scores and endpoints must align")
    if not 1 <= local_count <= reference_raw.numel():
        raise ValueError("local_count must fit inside the reference bank")
    if not 0.0 < scale_floor_fraction <= 1.0:
        raise ValueError("scale floor fraction must lie in (0, 1]")

    query_age = torch.log2(ends.float() + 1.0)
    reference_age = torch.log2(reference_ends.float() + 1.0)
    deltas = (query_age[:, None] - reference_age[None, :]).abs()
    local_indices = torch.topk(
        deltas, k=local_count, dim=1, largest=False, sorted=False
    ).indices
    local_values = reference_raw[local_indices]
    local_median = torch.quantile(local_values, 0.5, dim=1)
    q25 = torch.quantile(local_values, 0.25, dim=1)
    q75 = torch.quantile(local_values, 0.75, dim=1)
    local_scale = (q75 - q25) / 1.349
    global_scale = _robust_scale(reference_raw)
    local_scale = local_scale.clamp_min(global_scale * scale_floor_fraction)
    standardized = (raw - local_median) / local_scale
    return local_median, local_scale, standardized


def _score_base(
    ends: torch.Tensor, features: torch.Tensor, bank: LabelFreeBank
) -> BaseScores:
    """Score without reading any task/workflow metadata."""

    distances = _pairwise_distance(features, bank.features, BANDS)
    nearest = torch.topk(
        distances, k=NEIGHBOR_K, dim=1, largest=False, sorted=True
    )
    raw = nearest.values[:, -1]
    local_count = min(LOCAL_AGE_REFERENCE_COUNT, bank.reference_raw_scores.numel())
    local_median, local_scale, standardized = _continuous_age_standardize(
        raw,
        ends,
        bank.reference_raw_scores,
        bank.ends,
        local_count=local_count,
    )
    return BaseScores(
        ends=ends,
        raw=raw,
        local_median=local_median,
        local_scale=local_scale,
        standardized=standardized,
        neighbor_indices=nearest.indices,
    )


def _rolling_mean(
    values: torch.Tensor, ends: torch.Tensor, width: int
) -> tuple[torch.Tensor, torch.Tensor]:
    if width <= 0:
        raise ValueError("rolling width must be positive")
    if values.numel() < width:
        return values.new_empty(0), ends.new_empty(0)
    prefix = torch.cat((values.new_zeros(1), values.cumsum(0)))
    means = (prefix[width:] - prefix[:-width]) / float(width)
    return means, ends[width - 1 :]


def _cusum(
    values: torch.Tensor,
    ends: torch.Tensor,
    *,
    allowance: float,
    rho: float,
) -> tuple[torch.Tensor, torch.Tensor]:
    if not 0.0 < rho <= 1.0:
        raise ValueError("CUSUM rho must lie in (0, 1]")
    state = values.new_tensor(0.0)
    states: list[torch.Tensor] = []
    for value in values:
        state = torch.clamp(rho * state + value - allowance, min=0.0)
        states.append(state)
    return torch.stack(states), ends


def _history_streams(base: BaseScores) -> dict[str, tuple[torch.Tensor, torch.Tensor]]:
    roll4, roll4_ends = _rolling_mean(base.standardized, base.ends, 4)
    roll8, roll8_ends = _rolling_mean(base.standardized, base.ends, 8)
    cusum, cusum_ends = _cusum(
        base.standardized,
        base.ends,
        allowance=CUSUM_ALLOWANCE,
        rho=1.0,
    )
    leaky, leaky_ends = _cusum(
        base.standardized,
        base.ends,
        allowance=CUSUM_ALLOWANCE,
        rho=LEAKY_RHO,
    )
    return {
        "endpoint_z": (base.standardized, base.ends),
        "rolling_mean_4": (roll4, roll4_ends),
        "rolling_mean_8": (roll8, roll8_ends),
        "cusum_0_5": (cusum, cusum_ends),
        "leaky_cusum_0_95_0_5": (leaky, leaky_ends),
    }


def _score_record(record: ManifoldTrace, bank: LabelFreeBank) -> BaseScores:
    ends, features = _signatures(record)
    return _score_base(ends, features, bank)


def _calibrate(
    records: Sequence[ManifoldTrace], bank: LabelFreeBank
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    maxima: dict[str, list[float]] = {method: [] for method in METHOD_ORDER}
    trace_rows: dict[str, list[dict[str, Any]]] = {
        method: [] for method in METHOD_ORDER
    }
    standardized_values: list[float] = []
    standardized_by_family: dict[str, list[float]] = defaultdict(list)
    for index, record in enumerate(records, start=1):
        base = _score_record(record, bank)
        current_standardized = [float(value) for value in base.standardized.tolist()]
        standardized_values.extend(current_standardized)
        standardized_by_family[record.workflow_family].extend(current_standardized)
        streams = _history_streams(base)
        for method in METHOD_ORDER:
            scores, _ = streams[method]
            maximum = float(scores.max().item())
            maxima[method].append(maximum)
            trace_rows[method].append(
                {"trace_id": record.trace_id, "arm": record.arm, "maximum": maximum}
            )
        if index % 20 == 0 or index == len(records):
            print(f"    calibrated {index}/{len(records)} normal traces", flush=True)
    results: dict[str, dict[str, Any]] = {}
    for method in METHOD_ORDER:
        result = finite_upper_threshold(maxima[method], ALPHA)
        result["traces"] = trace_rows[method]
        results[method] = result
    audit = {
        "role": "post-hoc distribution audit; workflow labels never enter calibration scores",
        "standardized_score": _quantile_summary(standardized_values),
        "standardized_score_by_workflow_family": {
            family: _quantile_summary(values)
            for family, values in sorted(standardized_by_family.items())
        },
    }
    return results, audit


def _quantile_summary(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "mean": None, "q50": None, "q90": None, "q95": None}
    tensor = torch.tensor(values, dtype=torch.float32)
    return {
        "count": int(tensor.numel()),
        "mean": float(tensor.mean().item()),
        "q50": float(torch.quantile(tensor, 0.50).item()),
        "q90": float(torch.quantile(tensor, 0.90).item()),
        "q95": float(torch.quantile(tensor, 0.95).item()),
    }


def _normal_slices(rows: Sequence[dict[str, Any]], field: str) -> dict[str, Any]:
    negatives = [row for row in rows if not row["positive"]]
    result: dict[str, Any] = {}
    for value in sorted({str(row[field]) for row in negatives}):
        selected = [row for row in negatives if str(row[field]) == value]
        count = sum(bool(row["false_alarm"]) for row in selected)
        result[value] = {
            "trace_count": len(selected),
            "false_alarm_count": count,
            "false_alarm_rate": count / len(selected),
        }
    return result


def _evaluate(
    records: Sequence[ManifoldTrace],
    bank: LabelFreeBank,
    calibration: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], list[dict[str, Any]], dict[str, Any]]:
    method_rows: dict[str, list[dict[str, Any]]] = {
        method: [] for method in METHOD_ORDER
    }
    base_rows: list[dict[str, Any]] = []
    neighbor_counts: dict[str, Counter[str]] = defaultdict(Counter)
    neighbor_total = 0
    neighbor_same = 0
    top1_same = 0
    query_endpoint_counts: Counter[str] = Counter()
    phase_z: dict[str, list[float]] = defaultdict(list)
    trace_phase_means: dict[str, list[float]] = defaultdict(list)

    for index, record in enumerate(records, start=1):
        base = _score_record(record, bank)
        streams = _history_streams(base)
        neighbor_families = [
            [bank.audit_workflow_families[int(anchor)] for anchor in row]
            for row in base.neighbor_indices.tolist()
        ]
        for families in neighbor_families:
            query_endpoint_counts[record.workflow_family] += 1
            neighbor_total += len(families)
            neighbor_same += sum(family == record.workflow_family for family in families)
            top1_same += int(families[0] == record.workflow_family)
            neighbor_counts[record.workflow_family].update(families)

        z_values = [float(value) for value in base.standardized.tolist()]
        trace_phase_values: dict[str, list[float]] = defaultdict(list)
        for end, value in zip(base.ends.tolist(), z_values, strict=True):
            if not record.positive:
                phase_z["normal"].append(value)
                trace_phase_values["normal"].append(value)
            elif int(end) < int(record.evidence_onset):
                phase_z["drift_pre_onset"].append(value)
                trace_phase_values["drift_pre_onset"].append(value)
            else:
                phase_z["drift_post_onset"].append(value)
                trace_phase_values["drift_post_onset"].append(value)
        for phase, values in trace_phase_values.items():
            trace_phase_means[phase].append(sum(values) / len(values))

        base_rows.append(
            {
                "trace_id": record.trace_id,
                "workflow_family": record.workflow_family,
                "positive": record.positive,
                "evidence_onset": record.evidence_onset,
                "endpoints": [int(value) for value in base.ends.tolist()],
                "raw_knn_scores": [float(value) for value in base.raw.tolist()],
                "local_age_medians": [float(value) for value in base.local_median.tolist()],
                "local_age_scales": [float(value) for value in base.local_scale.tolist()],
                "standardized_scores": z_values,
                "top1_neighbor_same_workflow_family_rate": (
                    sum(families[0] == record.workflow_family for families in neighbor_families)
                    / len(neighbor_families)
                ),
                "top5_neighbor_same_workflow_family_rate": (
                    sum(
                        family == record.workflow_family
                        for families in neighbor_families
                        for family in families
                    )
                    / (len(neighbor_families) * NEIGHBOR_K)
                ),
            }
        )

        for method in METHOD_ORDER:
            scores, ends = streams[method]
            summary = alarm_summary(
                record,
                ends,
                scores,
                float(calibration[method]["threshold"]),
            )
            method_rows[method].append(summary)
        if index % 40 == 0 or index == len(records):
            print(f"    scored {index}/{len(records)} target traces", flush=True)

    metrics: dict[str, Any] = {}
    for method in METHOD_ORDER:
        aggregate = aggregate_alarm_summaries(method_rows[method])
        aggregate["normal_by_workflow_family"] = _normal_slices(
            method_rows[method], "workflow_family"
        )
        metrics[method] = aggregate

    reference_family_counts = Counter(bank.audit_workflow_families)
    random_same_family_rate = sum(
        (query_count / sum(query_endpoint_counts.values()))
        * (reference_family_counts[family] / len(bank.audit_workflow_families))
        for family, query_count in query_endpoint_counts.items()
    )
    observed_top5_same_rate = neighbor_same / neighbor_total
    normal_trace_mean_q95 = float(
        torch.quantile(torch.tensor(trace_phase_means["normal"]), 0.95).item()
    )
    drift_post_means = trace_phase_means["drift_post_onset"]
    neighbor_audit = {
        "role": "post-hoc interpretability only; workflow labels never enter detector scores",
        "endpoint_count": neighbor_total // NEIGHBOR_K,
        "top1_same_workflow_family_rate": top1_same / (neighbor_total / NEIGHBOR_K),
        "top5_same_workflow_family_rate": observed_top5_same_rate,
        "random_bank_draw_same_workflow_family_rate": random_same_family_rate,
        "top5_same_family_lift_over_random": (
            observed_top5_same_rate / random_same_family_rate
        ),
        "query_endpoint_count_by_workflow_family": dict(query_endpoint_counts),
        "reference_anchor_count_by_workflow_family": dict(reference_family_counts),
        "neighbor_family_counts_by_query_family": {
            family: dict(counts) for family, counts in sorted(neighbor_counts.items())
        },
        "standardized_score_by_phase": {
            phase: _quantile_summary(values) for phase, values in sorted(phase_z.items())
        },
        "trace_balanced_mean_standardized_score_by_phase": {
            phase: _quantile_summary(values)
            for phase, values in sorted(trace_phase_means.items())
        },
        "normal_trace_mean_q95": normal_trace_mean_q95,
        "drift_post_trace_mean_above_normal_q95_count": sum(
            value > normal_trace_mean_q95 for value in drift_post_means
        ),
        "drift_post_trace_mean_count": len(drift_post_means),
    }
    return metrics, method_rows, base_rows, neighbor_audit


def _direction(
    source_name: str,
    source: Sequence[ManifoldTrace],
    target_name: str,
    target: Sequence[ManifoldTrace],
) -> dict[str, Any]:
    fit = select_records(source, normal=True, folds=FIT_FOLDS)
    calibration_records = select_records(source, normal=True, folds=CALIBRATION_FOLDS)
    print(
        f"  {source_name}->{target_name}: fit={len(fit)} calibration={len(calibration_records)}",
        flush=True,
    )
    bank = _build_bank(fit)
    calibration, calibration_base_audit = _calibrate(calibration_records, bank)
    metrics, trace_results, base_rows, neighbor_audit = _evaluate(
        target, bank, calibration
    )
    return {
        "source_batch": source_name,
        "target_batch": target_name,
        "fit_normal_trace_count": len(fit),
        "calibration_normal_trace_count": len(calibration_records),
        "reference_anchor_count": int(bank.features.shape[0]),
        "reference_anchor_count_by_audit_workflow_family": dict(
            Counter(bank.audit_workflow_families)
        ),
        "reference_raw_score_summary": _quantile_summary(
            [float(value) for value in bank.reference_raw_scores.tolist()]
        ),
        "global_robust_scale": bank.global_robust_scale,
        "calibration": calibration,
        "calibration_base_audit": calibration_base_audit,
        "metrics": metrics,
        "trace_results": trace_results,
        "base_trace_results": base_rows,
        "neighbor_audit": neighbor_audit,
    }


def calculate(b1_dir: Path, b2_dir: Path, cache_dir: Path) -> dict[str, Any]:
    observation = (
        ROOT
        / "artifacts"
        / "agent_v2"
        / "routing_observation_atlas"
        / "observation.json"
    )
    b1 = read_manifold_traces(b1_dir.resolve(), cache_dir.resolve(), observation, "b1")
    b2 = read_manifold_traces(b2_dir.resolve(), cache_dir.resolve(), observation, "b2")
    cache = ensure_routing_cache(
        (*b1, *b2), cache_dir.resolve(), validate_trace_fn=validate_trace
    )
    directions = {
        "b1_to_b2": _direction("b1", b1, "b2", b2),
        "b2_to_b1": _direction("b2", b2, "b1", b1),
    }
    return {
        "schema_version": 1,
        "analysis_id": "normal-manifold-p1-label-free-single-agent",
        "analysis_role": "post-hoc exploratory development on previously observed B1/B2",
        "plan": PLAN,
        "datasets": {
            "b1": {"sample_index_sha256": B1_INDEX_SHA256, "trace_count": len(b1)},
            "b2": {"sample_index_sha256": B2_INDEX_SHA256, "trace_count": len(b2)},
        },
        "cache": cache,
        "detector": {
            "scope": "one fixed agent",
            "normal_model": "multimodal union of all observed normal windows",
            "forbidden_score_inputs": [
                "workflow",
                "workflow_family",
                "attack_channel",
                "target_domain",
                "token_id",
                "token_text",
                "drift_label",
                "evidence_onset",
            ],
            "allowed_score_inputs": [
                "decode routing through current token",
                "causal decode endpoint",
            ],
            "width": WIDTH,
            "bands": [list(band) for band in BANDS],
            "reference_anchors_per_trace": REFERENCE_ANCHORS_PER_TRACE,
            "neighbor_k": NEIGHBOR_K,
            "local_age_reference_count": LOCAL_AGE_REFERENCE_COUNT,
            "local_scale_floor_fraction": LOCAL_SCALE_FLOOR_FRACTION,
            "fit_folds": sorted(FIT_FOLDS),
            "calibration_folds": sorted(CALIBRATION_FOLDS),
            "alpha": ALPHA,
            "methods": list(METHOD_ORDER),
            "primary_method": PRIMARY_METHOD,
            "cusum_allowance": CUSUM_ALLOWANCE,
            "leaky_rho": LEAKY_RHO,
        },
        "directions": directions,
    }


def main() -> None:
    args = _args()
    result = calculate(args.b1, args.b2, args.cache_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    summary = {}
    for direction_name, direction in result["directions"].items():
        summary[direction_name] = {}
        for method in METHOD_ORDER:
            summary[direction_name][method] = {
                "threshold": direction["calibration"][method]["threshold"],
                **direction["metrics"][method],
            }
    print(json.dumps({"output": str(args.output), "results": summary}, indent=2), flush=True)


if __name__ == "__main__":
    main()
