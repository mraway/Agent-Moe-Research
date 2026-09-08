#!/usr/bin/env python3
"""Run the preregistered finite-memory state/transition trajectory ablation."""

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
    evenly_spaced_positions,
    finite_upper_threshold,
    read_manifold_traces,
    select_records,
    sha256,
)
from routing import validate_trace  # noqa: E402
from run_normal_manifold_age_free_ablation import (  # noqa: E402
    AgeFreeBank,
    _build_age_free_bank,
    _global_robust_standardize,
    _pair_group_maxima,
    _score_features,
    _summary,
)
from run_normal_manifold_p1_label_free import (  # noqa: E402
    ALPHA,
    BANDS,
    CALIBRATION_FOLDS,
    FIT_FOLDS,
    NEIGHBOR_K,
    WIDTH,
    _pairwise_distance,
    _signatures,
)


PLAN = "docs/normal_manifold_trajectory_ablation_plan.md"
AGE_FREE_RESULT_SHA256 = (
    "cb1feab85b939a66b830ee4de2c08fcf083ac04ada4555f3ee4e3e89715f3988"
)
CONDITIONAL_CANDIDATE_COUNT = 16
TRANSITION_ANCHORS_PER_TRACE = 8
HISTORY_WIDTH = 4
HISTORY_QUANTILE = 0.25
METHOD_LOOKBACK = {
    "state_endpoint_z": WIDTH,
    "transition_endpoint_z": WIDTH + 1,
    "state_floor_4": WIDTH + HISTORY_WIDTH - 1,
    "transition_floor_4": WIDTH + HISTORY_WIDTH,
    "joint_floor_4": WIDTH + HISTORY_WIDTH,
}
METHOD_ORDER = tuple(METHOD_LOOKBACK)
RISK_STEPS = (4, 8, 16, 32, 64)
DEFAULT_OBSERVATION = (
    ROOT / "artifacts" / "agent_v2" / "routing_observation_atlas" / "observation.json"
)
DEFAULT_AGE_FREE_RESULT = (
    ROOT
    / "artifacts"
    / "agent_v2"
    / "normal_manifold_age_free_ablation"
    / "result.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "artifacts"
    / "agent_v2"
    / "normal_manifold_trajectory_ablation"
    / "result.json"
)


@dataclass(frozen=True)
class TransitionBank:
    """Normal conditional-successor edges and fit-only normalization."""

    predecessors: torch.Tensor
    successors: torch.Tensor
    trace_ids: tuple[str, ...]
    ends: torch.Tensor
    reference_raw_scores: torch.Tensor
    center: float
    scale: float


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
    parser.add_argument(
        "--age-free-result", type=Path, default=DEFAULT_AGE_FREE_RESULT
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _conditional_successor_raw(
    query_predecessors: torch.Tensor,
    query_successors: torch.Tensor,
    reference_predecessors: torch.Tensor,
    reference_successors: torch.Tensor,
    *,
    candidate_count: int = CONDITIONAL_CANDIDATE_COUNT,
    neighbor_k: int = NEIGHBOR_K,
    bands: Sequence[Sequence[int]] = BANDS,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Score successors only among edges with the nearest predecessors."""

    if query_predecessors.shape != query_successors.shape:
        raise ValueError("query transition endpoints must align")
    if reference_predecessors.shape != reference_successors.shape:
        raise ValueError("reference transition endpoints must align")
    if not 1 <= neighbor_k <= candidate_count <= reference_predecessors.shape[0]:
        raise ValueError("conditional candidate counts are invalid")
    predecessor_distances = _pairwise_distance(
        query_predecessors, reference_predecessors, bands
    )
    candidates = torch.topk(
        predecessor_distances,
        k=candidate_count,
        dim=1,
        largest=False,
        sorted=True,
    ).indices
    successor_distances = _pairwise_distance(
        query_successors, reference_successors, bands
    )
    gated = successor_distances.gather(1, candidates)
    raw = torch.kthvalue(gated, neighbor_k, dim=1).values.float()
    return raw, candidates


def _leave_one_trace_out_transition_scores(
    predecessors: torch.Tensor,
    successors: torch.Tensor,
    trace_ids: Sequence[str],
    *,
    candidate_count: int = CONDITIONAL_CANDIDATE_COUNT,
    neighbor_k: int = NEIGHBOR_K,
    bands: Sequence[Sequence[int]] = BANDS,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Reference scores with every edge from the query trace excluded."""

    if predecessors.shape != successors.shape or predecessors.shape[0] != len(trace_ids):
        raise ValueError("reference transitions and trace IDs must align")
    predecessor_distances = _pairwise_distance(predecessors, predecessors, bands)
    successor_distances = _pairwise_distance(successors, successors, bands)
    raw_scores: list[torch.Tensor] = []
    gated_rows: list[torch.Tensor] = []
    for index, trace_id in enumerate(trace_ids):
        eligible = torch.tensor(
            [candidate != trace_id for candidate in trace_ids], dtype=torch.bool
        ).nonzero(as_tuple=False).reshape(-1)
        if eligible.numel() < candidate_count:
            raise ValueError("leave-one-trace-out transition pool is too small")
        local = torch.topk(
            predecessor_distances[index, eligible],
            k=candidate_count,
            largest=False,
            sorted=True,
        ).indices
        gated = eligible[local]
        raw_scores.append(
            torch.kthvalue(successor_distances[index, gated], neighbor_k).values
        )
        gated_rows.append(gated)
    return torch.stack(raw_scores).float(), torch.stack(gated_rows)


def _build_transition_bank(records: Sequence[ManifoldTrace]) -> TransitionBank:
    predecessors: list[torch.Tensor] = []
    successors: list[torch.Tensor] = []
    trace_ids: list[str] = []
    ends: list[int] = []
    for record in records:
        state_ends, features = _signatures(record)
        if features.shape[0] < 2:
            raise ValueError(f"trace has no routing transition: {record.trace_id}")
        for position in evenly_spaced_positions(
            features.shape[0] - 1, TRANSITION_ANCHORS_PER_TRACE
        ):
            predecessors.append(features[position])
            successors.append(features[position + 1])
            trace_ids.append(record.trace_id)
            ends.append(int(state_ends[position + 1].item()))
    predecessor_matrix = torch.stack(predecessors)
    successor_matrix = torch.stack(successors)
    reference_raw, _ = _leave_one_trace_out_transition_scores(
        predecessor_matrix, successor_matrix, trace_ids
    )
    _, center, scale = _global_robust_standardize(reference_raw, reference_raw)
    return TransitionBank(
        predecessor_matrix,
        successor_matrix,
        tuple(trace_ids),
        torch.tensor(ends, dtype=torch.long),
        reference_raw,
        center,
        scale,
    )


def _rolling_quantile(
    values: torch.Tensor,
    ends: torch.Tensor,
    *,
    width: int = HISTORY_WIDTH,
    quantile: float = HISTORY_QUANTILE,
) -> tuple[torch.Tensor, torch.Tensor]:
    if values.ndim != 1 or ends.ndim != 1 or values.numel() != ends.numel():
        raise ValueError("rolling values and endpoints must align")
    if width <= 0 or not 0.0 <= quantile <= 1.0:
        raise ValueError("rolling quantile parameters are invalid")
    if values.numel() < width:
        return values.new_empty(0), ends.new_empty(0)
    windows = values.unfold(0, width, 1)
    return torch.quantile(windows, quantile, dim=1), ends[width - 1 :]


def _align_minimum(
    left_scores: torch.Tensor,
    left_ends: torch.Tensor,
    right_scores: torch.Tensor,
    right_ends: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Take a strict AND after aligning two causal streams by endpoint."""

    left = {int(end): float(score) for end, score in zip(left_ends, left_scores, strict=True)}
    right = {
        int(end): float(score) for end, score in zip(right_ends, right_scores, strict=True)
    }
    common = sorted(set(left) & set(right))
    if not common:
        return left_scores.new_empty(0), left_ends.new_empty(0)
    scores = torch.tensor(
        [min(left[end], right[end]) for end in common], dtype=left_scores.dtype
    )
    return scores, torch.tensor(common, dtype=torch.long)


def _score_record(
    record: ManifoldTrace, state_bank: AgeFreeBank, transition_bank: TransitionBank
) -> dict[str, Any]:
    state_ends, features = _signatures(record)
    state_z = _score_features(features, state_bank)["global_robust_z"]
    transition_raw, _ = _conditional_successor_raw(
        features[:-1],
        features[1:],
        transition_bank.predecessors,
        transition_bank.successors,
    )
    transition_z = (
        transition_raw - transition_bank.center
    ) / transition_bank.scale
    transition_ends = state_ends[1:]
    state_floor, state_floor_ends = _rolling_quantile(state_z, state_ends)
    transition_floor, transition_floor_ends = _rolling_quantile(
        transition_z, transition_ends
    )
    joint, joint_ends = _align_minimum(
        state_floor, state_floor_ends, transition_floor, transition_floor_ends
    )
    streams = {
        "state_endpoint_z": (state_z, state_ends),
        "transition_endpoint_z": (transition_z, transition_ends),
        "state_floor_4": (state_floor, state_floor_ends),
        "transition_floor_4": (transition_floor, transition_floor_ends),
        "joint_floor_4": (joint, joint_ends),
    }
    if tuple(streams) != METHOD_ORDER:
        raise ValueError("trajectory method order changed")
    for method, (scores, ends) in streams.items():
        if not scores.numel() or scores.numel() != ends.numel():
            raise ValueError(f"empty or misaligned stream {method}: {record.trace_id}")
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
        "streams": {
            method: {
                "endpoints": [int(value) for value in ends.tolist()],
                "scores": [float(value) for value in scores.tolist()],
            }
            for method, (scores, ends) in streams.items()
        },
    }


def _score_records(
    records: Sequence[ManifoldTrace],
    state_bank: AgeFreeBank,
    transition_bank: TransitionBank,
    role: str,
) -> list[dict[str, Any]]:
    rows = []
    for index, record in enumerate(records, start=1):
        rows.append(_score_record(record, state_bank, transition_bank))
        if index % 40 == 0 or index == len(records):
            print(f"    {role}: scored {index}/{len(records)} traces", flush=True)
    return rows


def _phase_values(row: dict[str, Any], method: str) -> dict[str, list[float]]:
    stream = row["streams"][method]
    ends = stream["endpoints"]
    scores = stream["scores"]
    if not row["positive"]:
        return {"normal": [float(value) for value in scores]}
    onset = row["evidence_onset"]
    if onset is None:
        raise ValueError("positive trace lacks evidence onset")
    fully_post = int(onset) + METHOD_LOOKBACK[method] - 1
    result: dict[str, list[float]] = defaultdict(list)
    for end, score in zip(ends, scores, strict=True):
        if int(end) < int(onset):
            phase = "drift_pre_onset"
        elif int(end) < fully_post:
            phase = "drift_mixed_transition"
        else:
            phase = "drift_fully_post_onset"
        result[phase].append(float(score))
    return dict(result)


def _representation_metrics(
    target_rows: Sequence[dict[str, Any]],
    calibration_rows: Sequence[dict[str, Any]],
    method: str,
) -> dict[str, Any]:
    endpoint_by_phase: dict[str, list[float]] = defaultdict(list)
    trace_means_by_phase: dict[str, list[float]] = defaultdict(list)
    for row in target_rows:
        for phase, values in _phase_values(row, method).items():
            endpoint_by_phase[phase].extend(values)
            if values:
                trace_means_by_phase[phase].append(statistics.fmean(values))

    normal_means = trace_means_by_phase["normal"]
    post_means = trace_means_by_phase["drift_fully_post_onset"]
    normal_q95 = float(
        torch.quantile(torch.tensor(normal_means, dtype=torch.float64), 0.95).item()
    )
    paired_deltas = []
    for row in target_rows:
        if not row["positive"]:
            continue
        phases = _phase_values(row, method)
        pre = phases.get("drift_pre_onset", [])
        post = phases.get("drift_fully_post_onset", [])
        if pre and post:
            paired_deltas.append(statistics.fmean(post) - statistics.fmean(pre))

    labels = torch.tensor(
        [False] * len(normal_means) + [True] * len(post_means), dtype=torch.bool
    )
    ranking_scores = torch.tensor([*normal_means, *post_means], dtype=torch.float64)
    calibration_means = [
        statistics.fmean(row["streams"][method]["scores"])
        for row in calibration_rows
    ]
    calibration_maxima = [
        max(row["streams"][method]["scores"]) for row in calibration_rows
    ]
    target_normal_maxima = [
        max(row["streams"][method]["scores"])
        for row in target_rows
        if not row["positive"]
    ]
    return {
        "routing_lookback": METHOD_LOOKBACK[method],
        "endpoint_weighted_by_phase": {
            phase: _summary(values) for phase, values in sorted(endpoint_by_phase.items())
        },
        "trace_balanced_mean_by_phase": {
            phase: _summary(values)
            for phase, values in sorted(trace_means_by_phase.items())
        },
        "target_normal_trace_mean_q95": normal_q95,
        "fully_post_trace_mean_above_normal_q95_count": sum(
            value > normal_q95 for value in post_means
        ),
        "fully_post_trace_mean_count": len(post_means),
        "paired_fully_post_minus_pre": {
            "summary": _summary(paired_deltas),
            "positive_count": sum(value > 0.0 for value in paired_deltas),
            "pair_count": len(paired_deltas),
            "positive_rate": (
                sum(value > 0.0 for value in paired_deltas) / len(paired_deltas)
                if paired_deltas
                else None
            ),
        },
        "normal_vs_fully_post_trace_mean_ranking": {
            "normal_count": len(normal_means),
            "positive_count": len(post_means),
            "auroc": binary_auroc(ranking_scores, labels),
            "average_precision": average_precision(ranking_scores, labels),
        },
        "normal_cross_batch_location": {
            "source_calibration_trace_mean": _summary(calibration_means),
            "target_trace_mean": _summary(normal_means),
            "target_minus_source_mean": statistics.fmean(normal_means)
            - statistics.fmean(calibration_means),
            "source_calibration_trace_maximum": _summary(calibration_maxima),
            "target_trace_maximum": _summary(target_normal_maxima),
            "target_minus_source_maximum_mean": statistics.fmean(target_normal_maxima)
            - statistics.fmean(calibration_maxima),
        },
    }


def _stratum(row: dict[str, Any]) -> str:
    if row["positive"]:
        return "drift"
    if row["arm"] == "attack":
        return "resisted_attack"
    return str(row["arm"])


def _ranking(
    target_rows: Sequence[dict[str, Any]], method: str, negative_stratum: str
) -> dict[str, Any]:
    positives = [row for row in target_rows if _stratum(row) == "drift"]
    if negative_stratum == "all_non_drift":
        negatives = [row for row in target_rows if not row["positive"]]
    else:
        negatives = [row for row in target_rows if _stratum(row) == negative_stratum]
    positive_scores = [max(row["streams"][method]["scores"]) for row in positives]
    negative_scores = [max(row["streams"][method]["scores"]) for row in negatives]
    scores = torch.tensor([*negative_scores, *positive_scores], dtype=torch.float64)
    labels = torch.tensor(
        [False] * len(negative_scores) + [True] * len(positive_scores),
        dtype=torch.bool,
    )
    return {
        "positive_count": len(positive_scores),
        "negative_count": len(negative_scores),
        "auroc": binary_auroc(scores, labels),
        "average_precision": average_precision(scores, labels),
    }


def _behavior_specificity(
    target_rows: Sequence[dict[str, Any]], method: str
) -> dict[str, Any]:
    maxima_by_stratum: dict[str, list[float]] = defaultdict(list)
    for row in target_rows:
        maxima_by_stratum[_stratum(row)].append(
            max(row["streams"][method]["scores"])
        )
    return {
        "path_maximum_by_stratum": {
            stratum: _summary(values)
            for stratum, values in sorted(maxima_by_stratum.items())
        },
        "drift_vs_resisted_attack": _ranking(
            target_rows, method, "resisted_attack"
        ),
        "drift_vs_benign_control": _ranking(
            target_rows, method, "benign_control"
        ),
        "drift_vs_clean": _ranking(target_rows, method, "clean"),
        "drift_vs_all_non_drift": _ranking(
            target_rows, method, "all_non_drift"
        ),
    }


def _risk_step_curve(
    target_rows: Sequence[dict[str, Any]], method: str, threshold: float
) -> list[dict[str, Any]]:
    normal = [row for row in target_rows if not row["positive"]]
    rows = []
    for step in (*RISK_STEPS, None):
        alarm_count = 0
        at_risk_count = 0
        for row in normal:
            scores = row["streams"][method]["scores"]
            if step is not None and len(scores) >= step:
                at_risk_count += 1
            exposed = scores if step is None else scores[:step]
            alarm_count += int(any(score > threshold for score in exposed))
        rows.append(
            {
                "risk_step": "full" if step is None else step,
                "normal_trace_count": len(normal),
                "traces_reaching_step": len(normal) if step is None else at_risk_count,
                "alarm_by_step_count": alarm_count,
                "alarm_by_step_rate_all_traces": alarm_count / len(normal),
            }
        )
    return rows


def _compact_alarm_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in {"scores", "score_endpoints"}}


def _stopping_diagnostics(
    calibration_rows: Sequence[dict[str, Any]],
    target_rows: Sequence[dict[str, Any]],
    target_records: Sequence[ManifoldTrace],
    method: str,
) -> dict[str, Any]:
    maxima = [max(row["streams"][method]["scores"]) for row in calibration_rows]
    legacy = finite_upper_threshold(maxima, ALPHA)
    legacy["rows"] = [
        {"trace_id": row["trace_id"], "maximum": maximum}
        for row, maximum in zip(calibration_rows, maxima, strict=True)
    ]
    groups = _pair_group_maxima(
        [row["trace_id"] for row in calibration_rows],
        [row["pair_group_id"] for row in calibration_rows],
        maxima,
    )
    pair_group = finite_upper_threshold([row["maximum"] for row in groups], ALPHA)
    pair_group["rows"] = groups

    record_lookup = {record.trace_id: record for record in target_records}
    result = {}
    for name, calibration in (("pair_group_max", pair_group), ("legacy_trace_max", legacy)):
        threshold = float(calibration["threshold"])
        alarm_rows = []
        for row in target_rows:
            stream = row["streams"][method]
            alarm_rows.append(
                alarm_summary(
                    record_lookup[row["trace_id"]],
                    torch.tensor(stream["endpoints"], dtype=torch.long),
                    torch.tensor(stream["scores"], dtype=torch.float64),
                    threshold,
                )
            )
        aggregate = aggregate_alarm_summaries(alarm_rows)
        result[name] = {
            "calibration": calibration,
            "target_metrics": aggregate,
            "normal_risk_step_curve": _risk_step_curve(
                target_rows, method, threshold
            ),
            "target_alarm_rows": [_compact_alarm_row(row) for row in alarm_rows],
        }
    return result


def _validate_age_free_state_paths(
    rows: Sequence[dict[str, Any]], prior_rows: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    prior_lookup = {row["trace_id"]: row for row in prior_rows}
    if set(prior_lookup) != {row["trace_id"] for row in rows}:
        raise ValueError("age-free state validation trace IDs differ")
    maximum_difference = 0.0
    for row in rows:
        prior = prior_lookup[row["trace_id"]]
        stream = row["streams"]["state_endpoint_z"]
        if stream["endpoints"] != prior["endpoints"]:
            raise ValueError(f"age-free state endpoints changed: {row['trace_id']}")
        old_scores = prior["scores"]["global_robust_z"]
        if len(old_scores) != len(stream["scores"]):
            raise ValueError(f"age-free state score length changed: {row['trace_id']}")
        maximum_difference = max(
            maximum_difference,
            max(abs(new - old) for new, old in zip(stream["scores"], old_scores, strict=True)),
        )
    if maximum_difference > 1e-6:
        raise ValueError(f"age-free state score changed by {maximum_difference}")
    return {
        "trace_count": len(rows),
        "maximum_absolute_score_difference": maximum_difference,
        "tolerance": 1e-6,
        "passed": True,
    }


def _direction(
    source_name: str,
    source: Sequence[ManifoldTrace],
    target_name: str,
    target: Sequence[ManifoldTrace],
    prior: dict[str, Any],
) -> dict[str, Any]:
    fit = select_records(source, normal=True, folds=FIT_FOLDS)
    calibration = select_records(source, normal=True, folds=CALIBRATION_FOLDS)
    print(
        f"  {source_name}->{target_name}: fit={len(fit)} calibration={len(calibration)}",
        flush=True,
    )
    state_bank = _build_age_free_bank(fit)
    transition_bank = _build_transition_bank(fit)
    calibration_rows = _score_records(
        calibration, state_bank, transition_bank, "calibration"
    )
    target_rows = _score_records(target, state_bank, transition_bank, "target")
    validation = {
        "calibration": _validate_age_free_state_paths(
            calibration_rows, prior["calibration_trace_results"]
        ),
        "target": _validate_age_free_state_paths(
            target_rows, prior["target_trace_results"]
        ),
    }
    return {
        "source_batch": source_name,
        "target_batch": target_name,
        "fit_normal_trace_count": len(fit),
        "calibration_normal_trace_count": len(calibration),
        "state_reference_anchor_count": int(state_bank.base.features.shape[0]),
        "transition_reference_edge_count": int(transition_bank.predecessors.shape[0]),
        "transition_reference": {
            "raw_score": _summary(
                [float(value) for value in transition_bank.reference_raw_scores.tolist()]
            ),
            "center": transition_bank.center,
            "scale": transition_bank.scale,
        },
        "age_free_state_reproduction": validation,
        "representation_metrics": {
            method: _representation_metrics(target_rows, calibration_rows, method)
            for method in METHOD_ORDER
        },
        "behavior_specificity": {
            method: _behavior_specificity(target_rows, method)
            for method in METHOD_ORDER
        },
        "stopping_diagnostics": {
            method: _stopping_diagnostics(
                calibration_rows, target_rows, target, method
            )
            for method in METHOD_ORDER
        },
        "calibration_trace_results": calibration_rows,
        "target_trace_results": target_rows,
    }


def calculate(
    b1_dir: Path,
    b2_dir: Path,
    cache_dir: Path,
    observation_path: Path,
    age_free_result_path: Path,
) -> dict[str, Any]:
    actual_hash = sha256(age_free_result_path)
    if actual_hash != AGE_FREE_RESULT_SHA256:
        raise ValueError(f"age-free result hash mismatch: {actual_hash}")
    prior = json.loads(age_free_result_path.read_text(encoding="utf-8"))
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
        "analysis_id": "normal-manifold-finite-memory-trajectory-ablation",
        "analysis_role": "post-hoc B1/B2 development ablation; not confirmation",
        "plan": PLAN,
        "datasets": {
            "b1": {"sample_index_sha256": B1_INDEX_SHA256, "trace_count": len(b1)},
            "b2": {"sample_index_sha256": B2_INDEX_SHA256, "trace_count": len(b2)},
        },
        "cache": cache,
        "age_free_input": {
            "path": str(age_free_result_path),
            "sha256": actual_hash,
        },
        "score_contract": {
            "uses_absolute_decode_age": False,
            "uses_workflow_or_domain": False,
            "uses_token_id_or_text": False,
            "uses_drift_label_for_fit_or_calibration": False,
            "width": WIDTH,
            "bands": [list(band) for band in BANDS],
            "state_neighbor_k": NEIGHBOR_K,
            "conditional_candidate_count": CONDITIONAL_CANDIDATE_COUNT,
            "transition_neighbor_k": NEIGHBOR_K,
            "transition_anchors_per_trace": TRANSITION_ANCHORS_PER_TRACE,
            "history_width": HISTORY_WIDTH,
            "history_quantile": HISTORY_QUANTILE,
            "method_lookback": METHOD_LOOKBACK,
            "method_order": list(METHOD_ORDER),
            "alpha": ALPHA,
            "fit_folds": sorted(FIT_FOLDS),
            "calibration_folds": sorted(CALIBRATION_FOLDS),
        },
        "directions": {
            "b1_to_b2": _direction(
                "b1", b1, "b2", b2, prior["directions"]["b1_to_b2"]
            ),
            "b2_to_b1": _direction(
                "b2", b2, "b1", b1, prior["directions"]["b2_to_b1"]
            ),
        },
    }


def _headline(result: dict[str, Any]) -> dict[str, Any]:
    output = {}
    for direction_name, direction in result["directions"].items():
        output[direction_name] = {}
        for method in METHOD_ORDER:
            representation = direction["representation_metrics"][method]
            behavior = direction["behavior_specificity"][method]
            stopping = direction["stopping_diagnostics"][method]["pair_group_max"][
                "target_metrics"
            ]
            output[direction_name][method] = {
                "fully_post_trace_mean_auroc": representation[
                    "normal_vs_fully_post_trace_mean_ranking"
                ]["auroc"],
                "drift_vs_resisted_path_max_auroc": behavior[
                    "drift_vs_resisted_attack"
                ]["auroc"],
                "pair_group_far": stopping["non_drift_trace_false_alarm_rate"],
                "pair_group_recall_plus_8": stopping["clean_hit_recall_plus_8"],
                "pair_group_recall_full": stopping["clean_hit_recall_full"],
            }
    return output


def main() -> None:
    args = _args()
    result = calculate(
        args.b1,
        args.b2,
        args.cache_dir,
        args.observation,
        args.age_free_result,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps({"output": str(args.output), "headline": _headline(result)}, indent=2),
        flush=True,
    )


if __name__ == "__main__":
    main()
