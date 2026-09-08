#!/usr/bin/env python3
"""Ablate absolute decode age from the label-free normal manifold."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from phase_a.classifier import average_precision, binary_auroc  # noqa: E402
from phase_a.normal_manifold import (  # noqa: E402
    B1_INDEX_SHA256,
    B2_INDEX_SHA256,
    ManifoldTrace,
    aggregate_alarm_summaries,
    alarm_summary,
    ensure_routing_cache,
    finite_upper_threshold,
    read_manifold_traces,
    select_records,
)
from routing import validate_trace  # noqa: E402
from run_normal_manifold_p1_label_free import (  # noqa: E402
    ALPHA,
    BANDS,
    CALIBRATION_FOLDS,
    FIT_FOLDS,
    NEIGHBOR_K,
    WIDTH,
    LabelFreeBank,
    _build_bank,
    _pairwise_distance,
    _robust_scale,
    _signatures,
)


PLAN = "docs/normal_manifold_age_free_ablation_plan.md"
SCORE_ORDER = ("raw_knn", "global_robust_z", "local_density_z")
EPSILON = torch.finfo(torch.float32).eps
DEFAULT_OBSERVATION = (
    ROOT / "artifacts" / "agent_v2" / "routing_observation_atlas" / "observation.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "artifacts"
    / "agent_v2"
    / "normal_manifold_age_free_ablation"
    / "result.json"
)


@dataclass(frozen=True)
class AgeFreeBank:
    """Global normal bank and age-free source-fit normalization statistics."""

    base: LabelFreeBank
    reference_neighbor_indices: torch.Tensor
    reference_local_log_ratios: torch.Tensor
    raw_center: float
    raw_scale: float
    local_center: float
    local_scale: float


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
    parser.add_argument("--observation", type=Path, default=DEFAULT_OBSERVATION)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _reference_neighbor_indices(
    distances: torch.Tensor,
    trace_ids: Sequence[str],
    k: int = NEIGHBOR_K,
) -> torch.Tensor:
    """Find each anchor's nearest anchors after excluding its entire trace."""

    if distances.ndim != 2 or distances.shape[0] != distances.shape[1]:
        raise ValueError("reference distance matrix must be square")
    if distances.shape[0] != len(trace_ids):
        raise ValueError("reference distance matrix and trace IDs must align")
    rows: list[torch.Tensor] = []
    for index, trace_id in enumerate(trace_ids):
        candidates = torch.tensor(
            [candidate != trace_id for candidate in trace_ids], dtype=torch.bool
        ).nonzero(as_tuple=False).reshape(-1)
        if candidates.numel() < k:
            raise ValueError("leave-one-trace-out reference pool is smaller than k")
        local = torch.topk(
            distances[index, candidates], k=k, largest=False, sorted=True
        ).indices
        rows.append(candidates[local])
    return torch.stack(rows)


def _global_robust_standardize(
    values: torch.Tensor, reference_values: torch.Tensor
) -> tuple[torch.Tensor, float, float]:
    """Standardize against one source-fit distribution without position labels."""

    values = values.float().reshape(-1)
    reference_values = reference_values.float().reshape(-1)
    if reference_values.numel() < 2:
        raise ValueError("global robust standardization needs two references")
    center = torch.quantile(reference_values, 0.5)
    scale = _robust_scale(reference_values)
    return (values - center) / scale, float(center.item()), float(scale.item())


def _local_density_log_ratio(
    raw_distances: torch.Tensor,
    neighbor_indices: torch.Tensor,
    reference_raw_distances: torch.Tensor,
    *,
    epsilon: float = float(EPSILON),
) -> torch.Tensor:
    """Compare query support radius with its neighbors' normal support radii."""

    raw = raw_distances.float().reshape(-1)
    neighbors = neighbor_indices.long()
    reference = reference_raw_distances.float().reshape(-1)
    if neighbors.ndim != 2 or neighbors.shape[0] != raw.numel():
        raise ValueError("local-density neighbors must align with query distances")
    if not neighbors.numel():
        raise ValueError("local-density normalization needs neighbors")
    if int(neighbors.min().item()) < 0 or int(neighbors.max().item()) >= reference.numel():
        raise ValueError("local-density neighbor index is out of range")
    if epsilon <= 0.0:
        raise ValueError("epsilon must be positive")
    local_radius = torch.quantile(reference[neighbors], 0.5, dim=1)
    return torch.log((raw + epsilon) / (local_radius + epsilon))


def _pair_group_maxima(
    trace_ids: Sequence[str],
    pair_group_ids: Sequence[str],
    maxima: Sequence[float],
) -> list[dict[str, Any]]:
    """Collapse correlated normal arms to one maximum per pair group."""

    if not (len(trace_ids) == len(pair_group_ids) == len(maxima)):
        raise ValueError("pair-group maxima inputs must align")
    grouped: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for trace_id, pair_group_id, maximum in zip(
        trace_ids, pair_group_ids, maxima, strict=True
    ):
        grouped[str(pair_group_id)].append((str(trace_id), float(maximum)))
    rows = []
    for pair_group_id, members in sorted(grouped.items()):
        maximum = max(value for _, value in members)
        rows.append(
            {
                "pair_group_id": pair_group_id,
                "trace_count": len(members),
                "trace_ids": [trace_id for trace_id, _ in members],
                "maximum": maximum,
            }
        )
    return rows


def _build_age_free_bank(records: Sequence[ManifoldTrace]) -> AgeFreeBank:
    """Build P1-LF geometry and position-free density statistics."""

    base = _build_bank(records)
    distances = _pairwise_distance(base.features, base.features, BANDS)
    neighbor_indices = _reference_neighbor_indices(
        distances, base.trace_ids, NEIGHBOR_K
    )
    recomputed_raw = distances.gather(1, neighbor_indices)[:, -1].float()
    if not torch.allclose(recomputed_raw, base.reference_raw_scores, atol=1e-6, rtol=0):
        raise ValueError("age-free bank did not reproduce P1-LF raw reference scores")
    local_log_ratios = _local_density_log_ratio(
        recomputed_raw, neighbor_indices, recomputed_raw
    )
    _, raw_center, raw_scale = _global_robust_standardize(
        recomputed_raw, recomputed_raw
    )
    _, local_center, local_scale = _global_robust_standardize(
        local_log_ratios, local_log_ratios
    )
    return AgeFreeBank(
        base=base,
        reference_neighbor_indices=neighbor_indices,
        reference_local_log_ratios=local_log_ratios,
        raw_center=raw_center,
        raw_scale=raw_scale,
        local_center=local_center,
        local_scale=local_scale,
    )


def _score_features(features: torch.Tensor, bank: AgeFreeBank) -> dict[str, torch.Tensor]:
    distances = _pairwise_distance(features, bank.base.features, BANDS)
    nearest = torch.topk(
        distances, k=NEIGHBOR_K, dim=1, largest=False, sorted=True
    )
    raw = nearest.values[:, -1].float()
    global_z = (raw - bank.raw_center) / bank.raw_scale
    local_log_ratio = _local_density_log_ratio(
        raw, nearest.indices, bank.base.reference_raw_scores
    )
    local_z = (local_log_ratio - bank.local_center) / bank.local_scale
    return {
        "raw_knn": raw,
        "global_robust_z": global_z,
        "local_density_z": local_z,
    }


def _final_generation_stop_reason(record: ManifoldTrace) -> str:
    trace = json.loads((record.trace_dir / "trace.json").read_text(encoding="utf-8"))
    generations = [event for event in trace["events"] if event["kind"] == "model_generation"]
    if not generations:
        raise ValueError(f"trace has no generation: {record.trace_id}")
    return str(generations[-1]["stop_reason"])


def _score_record(record: ManifoldTrace, bank: AgeFreeBank) -> dict[str, Any]:
    ends, features = _signatures(record)
    scores = _score_features(features, bank)
    return {
        "trace_id": record.trace_id,
        "pair_group_id": record.pair_group_id,
        "batch": record.batch,
        "fold": record.fold,
        "arm": record.arm,
        "workflow": record.workflow,
        "workflow_family": record.workflow_family,
        "channel": record.channel,
        "domain": record.domain,
        "positive": record.positive,
        "evidence_onset": record.evidence_onset,
        "completion_boundary": record.completion_boundary,
        "decode_token_count": int(ends[-1].item()) + 1,
        "stop_reason": _final_generation_stop_reason(record),
        "endpoints": [int(value) for value in ends.tolist()],
        "scores": {
            name: [float(value) for value in scores[name].tolist()]
            for name in SCORE_ORDER
        },
    }


def _score_records(
    records: Sequence[ManifoldTrace], bank: AgeFreeBank, role: str
) -> list[dict[str, Any]]:
    rows = []
    for index, record in enumerate(records, start=1):
        rows.append(_score_record(record, bank))
        if index % 40 == 0 or index == len(records):
            print(f"    {role}: scored {index}/{len(records)} traces", flush=True)
    return rows


def _summary(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "mean": None,
            "minimum": None,
            "q25": None,
            "q50": None,
            "q75": None,
            "q90": None,
            "q95": None,
            "maximum": None,
        }
    tensor = torch.tensor(values, dtype=torch.float64)
    quantiles = torch.quantile(
        tensor, torch.tensor([0.25, 0.50, 0.75, 0.90, 0.95], dtype=torch.float64)
    )
    return {
        "count": int(tensor.numel()),
        "mean": float(tensor.mean().item()),
        "minimum": float(tensor.min().item()),
        "q25": float(quantiles[0].item()),
        "q50": float(quantiles[1].item()),
        "q75": float(quantiles[2].item()),
        "q90": float(quantiles[3].item()),
        "q95": float(quantiles[4].item()),
        "maximum": float(tensor.max().item()),
    }


def _phase(end: int, onset: int) -> str:
    if end < onset:
        return "drift_pre_onset"
    if end < onset + WIDTH - 1:
        return "drift_transition"
    return "drift_fully_post_onset"


def _trace_phase_values(row: dict[str, Any], score_name: str) -> dict[str, list[float]]:
    ends = row["endpoints"]
    scores = row["scores"][score_name]
    if len(ends) != len(scores):
        raise ValueError("score path and endpoints do not align")
    if not row["positive"]:
        return {"normal": [float(value) for value in scores]}
    onset = row["evidence_onset"]
    if onset is None:
        raise ValueError("positive row lacks evidence onset")
    values: dict[str, list[float]] = defaultdict(list)
    for end, score in zip(ends, scores, strict=True):
        phase = _phase(int(end), int(onset))
        values[phase].append(float(score))
        if int(end) >= int(onset):
            values["drift_post_onset_including_transition"].append(float(score))
    return dict(values)


def _rankdata(values: Sequence[float]) -> torch.Tensor:
    """Average ranks for ties, using one-based ranks."""

    tensor = torch.tensor(values, dtype=torch.float64)
    if tensor.ndim != 1:
        raise ValueError("rankdata expects one-dimensional values")
    order = torch.argsort(tensor, stable=True)
    ranks = torch.empty_like(tensor)
    start = 0
    while start < tensor.numel():
        end = start + 1
        current = tensor[order[start]]
        while end < tensor.numel() and bool(tensor[order[end]] == current):
            end += 1
        average_rank = (start + 1 + end) / 2.0
        ranks[order[start:end]] = average_rank
        start = end
    return ranks


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right):
        raise ValueError("Spearman inputs must align")
    if len(left) < 2:
        return None
    left_rank = _rankdata(left)
    right_rank = _rankdata(right)
    left_centered = left_rank - left_rank.mean()
    right_centered = right_rank - right_rank.mean()
    denominator = torch.linalg.vector_norm(left_centered) * torch.linalg.vector_norm(
        right_centered
    )
    if float(denominator.item()) == 0.0:
        return None
    return float((left_centered @ right_centered / denominator).item())


def _age_band(end: int) -> str:
    if end <= 31:
        return "7-31"
    if end <= 63:
        return "32-63"
    if end <= 127:
        return "64-127"
    return "128+"


def _length_band(length: int) -> str:
    if length <= 63:
        return "<=63"
    if length <= 127:
        return "64-127"
    if length <= 191:
        return "128-191"
    return "192+"


def _distribution_by_field(
    rows: Sequence[dict[str, Any]], score_name: str, field: str
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in sorted({str(row[field]) for row in rows if not row["positive"]}):
        selected = [
            row for row in rows if not row["positive"] and str(row[field]) == value
        ]
        means = [statistics.fmean(row["scores"][score_name]) for row in selected]
        maxima = [max(row["scores"][score_name]) for row in selected]
        result[value] = {
            "trace_count": len(selected),
            "trace_mean": _summary(means),
            "trace_maximum": _summary(maxima),
        }
    return result


def _representation_metrics(
    target_rows: Sequence[dict[str, Any]],
    calibration_rows: Sequence[dict[str, Any]],
    score_name: str,
) -> dict[str, Any]:
    endpoint_by_phase: dict[str, list[float]] = defaultdict(list)
    trace_mean_by_phase: dict[str, list[float]] = defaultdict(list)
    normal_endpoint_ends: list[float] = []
    normal_endpoint_scores: list[float] = []
    normal_lengths: list[float] = []
    normal_means: list[float] = []
    normal_maxima: list[float] = []
    by_age: dict[str, list[float]] = defaultdict(list)

    for row in target_rows:
        phase_values = _trace_phase_values(row, score_name)
        for phase, values in phase_values.items():
            endpoint_by_phase[phase].extend(values)
            if values:
                trace_mean_by_phase[phase].append(statistics.fmean(values))
        if not row["positive"]:
            scores = [float(value) for value in row["scores"][score_name]]
            ends = [int(value) for value in row["endpoints"]]
            normal_endpoint_ends.extend(ends)
            normal_endpoint_scores.extend(scores)
            normal_lengths.append(float(row["decode_token_count"]))
            normal_means.append(statistics.fmean(scores))
            normal_maxima.append(max(scores))
            for end, score in zip(ends, scores, strict=True):
                by_age[_age_band(end)].append(score)

    paired_deltas = []
    paired_positive = 0
    paired_count = 0
    for row in target_rows:
        if not row["positive"]:
            continue
        phases = _trace_phase_values(row, score_name)
        pre = phases.get("drift_pre_onset", [])
        post = phases.get("drift_fully_post_onset", [])
        if pre and post:
            delta = statistics.fmean(post) - statistics.fmean(pre)
            paired_deltas.append(delta)
            paired_positive += int(delta > 0.0)
            paired_count += 1

    normal_q95 = float(
        torch.quantile(torch.tensor(normal_means, dtype=torch.float64), 0.95).item()
    )
    post_means = trace_mean_by_phase.get("drift_fully_post_onset", [])
    ranking_scores = torch.tensor([*normal_means, *post_means], dtype=torch.float64)
    ranking_labels = torch.tensor(
        [False] * len(normal_means) + [True] * len(post_means), dtype=torch.bool
    )

    calibration_means = [
        statistics.fmean(row["scores"][score_name]) for row in calibration_rows
    ]
    calibration_maxima = [max(row["scores"][score_name]) for row in calibration_rows]

    return {
        "endpoint_weighted_by_phase": {
            phase: _summary(values) for phase, values in sorted(endpoint_by_phase.items())
        },
        "trace_balanced_mean_by_phase": {
            phase: _summary(values)
            for phase, values in sorted(trace_mean_by_phase.items())
        },
        "target_normal_trace_mean_q95": normal_q95,
        "fully_post_trace_mean_above_normal_q95_count": sum(
            value > normal_q95 for value in post_means
        ),
        "fully_post_trace_mean_count": len(post_means),
        "paired_fully_post_minus_pre": {
            "summary": _summary(paired_deltas),
            "positive_count": paired_positive,
            "pair_count": paired_count,
            "positive_rate": paired_positive / paired_count if paired_count else None,
        },
        "normal_vs_fully_post_trace_mean_ranking": {
            "normal_count": len(normal_means),
            "positive_count": len(post_means),
            "auroc": binary_auroc(ranking_scores, ranking_labels),
            "average_precision": average_precision(ranking_scores, ranking_labels),
        },
        "target_normal_by_arm": _distribution_by_field(
            target_rows, score_name, "arm"
        ),
        "normal_cross_batch_location": {
            "source_calibration_trace_mean": _summary(calibration_means),
            "target_trace_mean": _summary(normal_means),
            "target_minus_source_mean": statistics.fmean(normal_means)
            - statistics.fmean(calibration_means),
            "source_calibration_trace_maximum": _summary(calibration_maxima),
            "target_trace_maximum": _summary(normal_maxima),
            "target_minus_source_maximum_mean": statistics.fmean(normal_maxima)
            - statistics.fmean(calibration_maxima),
        },
        "age_diagnostics_not_used_by_score": {
            "normal_endpoint_score_vs_index_spearman": _spearman(
                normal_endpoint_scores, normal_endpoint_ends
            ),
            "normal_trace_mean_vs_length_spearman": _spearman(
                normal_means, normal_lengths
            ),
            "normal_trace_maximum_vs_length_spearman": _spearman(
                normal_maxima, normal_lengths
            ),
            "normal_endpoint_score_by_age_band": {
                band: _summary(values) for band, values in sorted(by_age.items())
            },
        },
    }


def _normal_alarm_slices(
    alarm_rows: Sequence[dict[str, Any]],
    target_lookup: dict[str, dict[str, Any]],
    field: str,
) -> dict[str, Any]:
    negatives = [row for row in alarm_rows if not row["positive"]]
    values = sorted(
        {
            _length_band(int(target_lookup[row["trace_id"]]["decode_token_count"]))
            if field == "length_band"
            else str(target_lookup[row["trace_id"]][field])
            for row in negatives
        }
    )
    result = {}
    for value in values:
        selected = []
        for row in negatives:
            target = target_lookup[row["trace_id"]]
            actual = (
                _length_band(int(target["decode_token_count"]))
                if field == "length_band"
                else str(target[field])
            )
            if actual == value:
                selected.append(row)
        false_alarms = sum(bool(row["false_alarm"]) for row in selected)
        result[value] = {
            "trace_count": len(selected),
            "false_alarm_count": false_alarms,
            "false_alarm_rate": false_alarms / len(selected),
        }
    return result


def _compact_alarm_row(row: dict[str, Any]) -> dict[str, Any]:
    """Keep alarm outcomes without duplicating score paths saved elsewhere."""

    return {
        key: value
        for key, value in row.items()
        if key not in {"score_endpoints", "scores"}
    }


def _stopping_diagnostics(
    calibration_rows: Sequence[dict[str, Any]],
    target_rows: Sequence[dict[str, Any]],
    target_records: Sequence[ManifoldTrace],
    score_name: str,
) -> dict[str, Any]:
    maxima = [max(row["scores"][score_name]) for row in calibration_rows]
    trace_calibration = finite_upper_threshold(maxima, ALPHA)
    trace_calibration["rows"] = [
        {"trace_id": row["trace_id"], "maximum": maximum}
        for row, maximum in zip(calibration_rows, maxima, strict=True)
    ]
    group_rows = _pair_group_maxima(
        [row["trace_id"] for row in calibration_rows],
        [row["pair_group_id"] for row in calibration_rows],
        maxima,
    )
    group_calibration = finite_upper_threshold(
        [row["maximum"] for row in group_rows], ALPHA
    )
    group_calibration["rows"] = group_rows

    record_lookup = {record.trace_id: record for record in target_records}
    target_lookup = {row["trace_id"]: row for row in target_rows}
    result = {}
    for calibration_name, calibration in (
        ("legacy_trace_max", trace_calibration),
        ("pair_group_max", group_calibration),
    ):
        threshold = float(calibration["threshold"])
        alarm_rows = []
        for row in target_rows:
            record = record_lookup[row["trace_id"]]
            alarm_rows.append(
                alarm_summary(
                    record,
                    torch.tensor(row["endpoints"], dtype=torch.long),
                    torch.tensor(row["scores"][score_name], dtype=torch.float64),
                    threshold,
                )
            )
        aggregate = aggregate_alarm_summaries(alarm_rows)
        aggregate["normal_by_stop_reason"] = _normal_alarm_slices(
            alarm_rows, target_lookup, "stop_reason"
        )
        aggregate["normal_by_length_band"] = _normal_alarm_slices(
            alarm_rows, target_lookup, "length_band"
        )
        result[calibration_name] = {
            "calibration": calibration,
            "target_metrics": aggregate,
            "target_alarm_rows": [_compact_alarm_row(row) for row in alarm_rows],
        }
    return result


def _direction(
    source_name: str,
    source: Sequence[ManifoldTrace],
    target_name: str,
    target: Sequence[ManifoldTrace],
) -> dict[str, Any]:
    fit = select_records(source, normal=True, folds=FIT_FOLDS)
    calibration = select_records(source, normal=True, folds=CALIBRATION_FOLDS)
    print(
        f"  {source_name}->{target_name}: fit={len(fit)} calibration={len(calibration)}",
        flush=True,
    )
    bank = _build_age_free_bank(fit)
    calibration_rows = _score_records(calibration, bank, "calibration")
    target_rows = _score_records(target, bank, "target")
    representation = {
        score_name: _representation_metrics(target_rows, calibration_rows, score_name)
        for score_name in SCORE_ORDER
    }
    stopping = {
        score_name: _stopping_diagnostics(
            calibration_rows, target_rows, target, score_name
        )
        for score_name in SCORE_ORDER
    }
    return {
        "source_batch": source_name,
        "target_batch": target_name,
        "fit_normal_trace_count": len(fit),
        "calibration_normal_trace_count": len(calibration),
        "reference_anchor_count": int(bank.base.features.shape[0]),
        "reference": {
            "raw_knn": _summary(
                [float(value) for value in bank.base.reference_raw_scores.tolist()]
            ),
            "local_density_log_ratio": _summary(
                [float(value) for value in bank.reference_local_log_ratios.tolist()]
            ),
            "raw_center": bank.raw_center,
            "raw_scale": bank.raw_scale,
            "local_center": bank.local_center,
            "local_scale": bank.local_scale,
        },
        "representation_metrics": representation,
        "stopping_diagnostics": stopping,
        "calibration_trace_results": calibration_rows,
        "target_trace_results": target_rows,
    }


def calculate(
    b1_dir: Path, b2_dir: Path, cache_dir: Path, observation_path: Path
) -> dict[str, Any]:
    b1 = read_manifold_traces(
        b1_dir.resolve(), cache_dir.resolve(), observation_path.resolve(), "b1"
    )
    b2 = read_manifold_traces(
        b2_dir.resolve(), cache_dir.resolve(), observation_path.resolve(), "b2"
    )
    cache = ensure_routing_cache(
        (*b1, *b2), cache_dir.resolve(), validate_trace_fn=validate_trace
    )
    return {
        "schema_version": 1,
        "analysis_id": "normal-manifold-absolute-age-free-ablation",
        "analysis_role": "post-hoc B1/B2 development ablation; not confirmation",
        "plan": PLAN,
        "datasets": {
            "b1": {"sample_index_sha256": B1_INDEX_SHA256, "trace_count": len(b1)},
            "b2": {"sample_index_sha256": B2_INDEX_SHA256, "trace_count": len(b2)},
        },
        "cache": cache,
        "score_contract": {
            "uses_absolute_decode_age": False,
            "uses_workflow_or_domain": False,
            "uses_token_id_or_text": False,
            "uses_drift_label_for_fit_or_calibration": False,
            "width": WIDTH,
            "bands": [list(band) for band in BANDS],
            "neighbor_k": NEIGHBOR_K,
            "score_order": list(SCORE_ORDER),
            "alpha_for_secondary_stopping_diagnostic": ALPHA,
            "fit_folds": sorted(FIT_FOLDS),
            "calibration_folds": sorted(CALIBRATION_FOLDS),
        },
        "directions": {
            "b1_to_b2": _direction("b1", b1, "b2", b2),
            "b2_to_b1": _direction("b2", b2, "b1", b1),
        },
    }


def _headline(result: dict[str, Any]) -> dict[str, Any]:
    output = {}
    for direction_name, direction in result["directions"].items():
        output[direction_name] = {}
        for score_name in SCORE_ORDER:
            representation = direction["representation_metrics"][score_name]
            legacy = direction["stopping_diagnostics"][score_name][
                "legacy_trace_max"
            ]["target_metrics"]
            grouped = direction["stopping_diagnostics"][score_name][
                "pair_group_max"
            ]["target_metrics"]
            output[direction_name][score_name] = {
                "fully_post_above_normal_q95": [
                    representation["fully_post_trace_mean_above_normal_q95_count"],
                    representation["fully_post_trace_mean_count"],
                ],
                "paired_post_minus_pre_positive_rate": representation[
                    "paired_fully_post_minus_pre"
                ]["positive_rate"],
                "trace_mean_auroc": representation[
                    "normal_vs_fully_post_trace_mean_ranking"
                ]["auroc"],
                "legacy_endpoint_far": legacy["non_drift_trace_false_alarm_rate"],
                "legacy_recall_plus_8": legacy["clean_hit_recall_plus_8"],
                "legacy_recall_full": legacy["clean_hit_recall_full"],
                "pair_group_endpoint_far": grouped[
                    "non_drift_trace_false_alarm_rate"
                ],
                "pair_group_recall_plus_8": grouped["clean_hit_recall_plus_8"],
                "pair_group_recall_full": grouped["clean_hit_recall_full"],
            }
    return output


def main() -> None:
    args = _args()
    result = calculate(args.b1, args.b2, args.cache_dir, args.observation)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"output": str(args.output), "headline": _headline(result)}, indent=2
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
