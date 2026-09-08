#!/usr/bin/env python3
"""Reconstruct one representative P1 false negative at token/layer resolution."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from phase_a.normal_manifold import (  # noqa: E402
    ManifoldTrace,
    age_bin,
    evenly_spaced_positions,
    load_cached_routing,
    read_manifold_traces,
    select_records,
)
from run_normal_manifold_p1_knn import (  # noqa: E402
    FIT_FOLDS,
    NEIGHBOR_K,
    REFERENCE_ANCHORS_PER_TRACE,
    VARIANTS,
    WIDTH,
    _build_bank,
    _full_pool_key,
    _pairwise_distance,
    _score_record,
    _signatures,
)


PRIMARY_DIRECTION = "b1_to_b2::selection_middle_late"
DEFAULT_RESULT = ROOT / "artifacts" / "agent_v2" / "normal_manifold_p1_knn" / "result.json"
DEFAULT_OBSERVATION = (
    ROOT / "artifacts" / "agent_v2" / "routing_observation_atlas" / "observation.json"
)
DEFAULT_OUTPUT = (
    ROOT / "artifacts" / "agent_v2" / "normal_manifold_p1_miss_zoom"
)
MIDDLE_LAYERS = tuple(range(5, 11))
LATE_LAYERS = tuple(range(11, 16))


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
    parser.add_argument("--p1-result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--observation", type=Path, default=DEFAULT_OBSERVATION)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--trace-id",
        help="Inspect this missed B2 drift instead of selecting the median representative.",
    )
    parser.add_argument("--minimum-pre-onset-tokens", type=int, default=16)
    parser.add_argument("--minimum-post-onset-tokens", type=int, default=48)
    return parser.parse_args()


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _variant() -> Any:
    return next(variant for variant in VARIANTS if variant.name == "selection_middle_late")


def _representative_miss(
    rows: Sequence[dict[str, Any]],
    minimum_pre: int,
    minimum_post: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], float]:
    candidates = [
        row
        for row in rows
        if row["positive"]
        and not row["pre_onset_alarm"]
        and row["first_post_onset_alarm"] is None
        and int(row["evidence_onset"]) >= minimum_pre
        and int(row["decode_token_count"]) - int(row["evidence_onset"]) >= minimum_post
    ]
    if not candidates:
        raise ValueError("no complete P1 miss satisfies the context requirements")
    maxima = [max(float(value) for value in row["scores"]) for row in candidates]
    median = float(statistics.median(maxima))
    selected = min(
        candidates,
        key=lambda row: (
            abs(max(float(value) for value in row["scores"]) - median),
            row["trace_id"],
        ),
    )
    return selected, candidates, median


def _decode_token_rows(record: ManifoldTrace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    trace = _json(record.trace_dir / "trace.json")
    generation = [event for event in trace["events"] if event["kind"] == "model_generation"][-1]
    final_step = int(generation["agent_step"])
    token_rows: list[dict[str, Any]] = []
    manifest_rows = [
        json.loads(line)
        for line in (record.trace_dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    for manifest in manifest_rows:
        if manifest["phase"] != "decode":
            continue
        values = zip(
            manifest["token_ids"],
            manifest["token_texts"],
            manifest["positions"],
            manifest["agent_steps"],
            strict=True,
        )
        for token_id, token_text, position, agent_step in values:
            if int(agent_step) != final_step:
                continue
            token_rows.append(
                {
                    "token_index": len(token_rows),
                    "token_id": int(token_id),
                    "token_text": str(token_text),
                    "model_position": int(position),
                }
            )
    expected = [int(value) for value in generation["output_token_ids"]]
    actual = [row["token_id"] for row in token_rows]
    if actual != expected:
        raise ValueError(f"manifest token alignment failed for {record.trace_id}")
    return trace, token_rows


def _observation_row(path: Path, pair_group_id: str) -> dict[str, Any]:
    rows = _json(path)["boundary_annotation_audit"]["b2"]["scenario_rows"]
    return next(row for row in rows if row["pair_group_id"] == pair_group_id)


def _reference_metadata(
    fit_records: Sequence[ManifoldTrace], variant: Any
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in fit_records:
        ends, _ = _signatures(record, variant)
        for position in evenly_spaced_positions(ends.numel(), REFERENCE_ANCHORS_PER_TRACE):
            end = int(ends[position].item())
            rows.append(
                {
                    "trace_id": record.trace_id,
                    "record": record,
                    "window_end": end,
                    "window_start": end - WIDTH + 1,
                    "workflow_family": record.workflow_family,
                    "age": age_bin(end),
                }
            )
    return rows


def _snippet(token_rows: Sequence[dict[str, Any]], start: int, end: int) -> str:
    start = max(0, start)
    end = min(len(token_rows) - 1, end)
    if end < start:
        return ""
    return "".join(row["token_text"] for row in token_rows[start : end + 1])


def _phase(index: int, evidence_start: int, boundary: int) -> str:
    if index < evidence_start:
        return "routine_pre_evidence"
    if index <= boundary:
        return "drift_evidence_phrase"
    return "drift_continuation"


def _entropy(probability: torch.Tensor) -> float:
    values = probability.float().clamp_min(1e-12)
    return float((-(values * values.log()).sum()).item())


def _jsd(left: torch.Tensor, right: torch.Tensor) -> float:
    left = left.float().clamp_min(1e-12)
    right = right.float().clamp_min(1e-12)
    middle = 0.5 * (left + right)
    value = 0.5 * (left * (left.log() - middle.log())).sum()
    value += 0.5 * (right * (right.log() - middle.log())).sum()
    return float(value.item())


def _mean(values: Iterable[float | None]) -> float | None:
    finite = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return float(statistics.fmean(finite)) if finite else None


def _maximum(values: Iterable[float | None]) -> float | None:
    finite = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return max(finite) if finite else None


def _distribution_json(distribution: torch.Tensor) -> str:
    values = {
        str(index): round(float(value), 6)
        for index, value in enumerate(distribution.tolist())
        if float(value) > 0.0
    }
    return json.dumps(values, sort_keys=True, separators=(",", ":"))


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty CSV: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_plot(
    path: Path,
    timeline_rows: Sequence[dict[str, Any]],
    threshold: float,
    evidence_start: int,
    boundary: int,
) -> None:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/agent-moe-matplotlib")
    import matplotlib.pyplot as plt

    eligible = [row for row in timeline_rows if row["persistent_score"] != ""]
    endpoints = [int(row["token_index"]) for row in eligible]
    persistent = [float(row["persistent_score"]) for row in eligible]
    ceiling = [float(row["maximum_attainable_persistent"]) for row in eligible]
    middle = [float(row["middle_kth_distance"]) for row in eligible]
    late = [float(row["late_kth_distance"]) for row in eligible]

    figure, axes = plt.subplots(
        3,
        1,
        figsize=(12, 9),
        sharex=True,
        gridspec_kw={"height_ratios": [1.0, 1.0, 1.45]},
    )
    axes[0].plot(endpoints, persistent, label="P1 persistent rarity", color="#2864a5")
    axes[0].plot(endpoints, ceiling, label="cell-specific score ceiling", color="#d17a00")
    axes[0].axhline(threshold, label="global threshold", color="#b22222", linestyle="--")
    axes[0].set_ylabel("score")
    axes[0].legend(loc="lower right", fontsize=8)
    axes[0].set_title("P1 complete miss: signal rises, but the calibrated threshold is unattainable")

    axes[1].plot(endpoints, middle, label="middle layers 5-10", color="#4c956c")
    axes[1].plot(endpoints, late, label="late layers 11-15", color="#8f5aa2")
    axes[1].set_ylabel("Hellinger distance\nto 5th normal neighbor")
    axes[1].legend(loc="upper left", fontsize=8)

    layer_matrix = []
    for layer in (*MIDDLE_LAYERS, *LATE_LAYERS):
        layer_matrix.append(
            [
                float(
                    json.loads(row["_plot_layer_distances"])[layer]
                )
                for row in eligible
            ]
        )
    image = axes[2].imshow(
        layer_matrix,
        aspect="auto",
        interpolation="nearest",
        origin="lower",
        extent=[endpoints[0] - 0.5, endpoints[-1] + 0.5, 4.5, 15.5],
        cmap="viridis",
    )
    axes[2].set_yticks(list((*MIDDLE_LAYERS, *LATE_LAYERS)))
    axes[2].set_ylabel("MoE layer")
    axes[2].set_xlabel("decode token / causal window end")
    figure.colorbar(image, ax=axes[2], label="per-layer Hellinger distance")
    for axis in axes:
        axis.axvline(evidence_start, color="#cc2f2f", linestyle=":", linewidth=1.5)
        axis.axvline(boundary, color="#6f1d1b", linestyle="-.", linewidth=1.2)
        axis.grid(axis="x", alpha=0.12)
    figure.tight_layout()
    figure.savefig(path, dpi=180)
    plt.close(figure)


def _matched_trace_summaries(
    records: Sequence[ManifoldTrace],
    p1_rows: Sequence[dict[str, Any]],
    pair_group_id: str,
) -> list[dict[str, Any]]:
    result_lookup = {row["trace_id"]: row for row in p1_rows}
    summaries: list[dict[str, Any]] = []
    for record in records:
        if record.pair_group_id != pair_group_id:
            continue
        trace, tokens = _decode_token_rows(record)
        generation = [event for event in trace["events"] if event["kind"] == "model_generation"][-1]
        result = result_lookup[record.trace_id]
        summaries.append(
            {
                "trace_id": record.trace_id,
                "arm": record.arm,
                "positive": record.positive,
                "decode_token_count": len(tokens),
                "p1_maximum": max(float(value) for value in result["scores"]),
                "alarm_onset_ends": result["alarm_onset_ends"],
                "output": generation["content"],
            }
        )
    return sorted(summaries, key=lambda row: row["arm"])


def calculate(args: argparse.Namespace) -> dict[str, Any]:
    p1 = _json(args.p1_result)
    direction = p1["directions"][PRIMARY_DIRECTION]
    threshold = float(direction["calibration"]["threshold"])
    selected_by_rule, candidates, candidate_median = _representative_miss(
        direction["trace_results"],
        args.minimum_pre_onset_tokens,
        args.minimum_post_onset_tokens,
    )
    selected_row = selected_by_rule
    if args.trace_id:
        selected_row = next(
            row for row in direction["trace_results"] if row["trace_id"] == args.trace_id
        )
        if not selected_row["positive"] or selected_row["first_post_onset_alarm"] is not None:
            raise ValueError("--trace-id must name a P1 drift false negative")

    b1 = read_manifold_traces(
        args.b1.resolve(), args.cache_dir.resolve(), args.observation.resolve(), "b1"
    )
    b2 = read_manifold_traces(
        args.b2.resolve(), args.cache_dir.resolve(), args.observation.resolve(), "b2"
    )
    record = next(item for item in b2 if item.trace_id == selected_row["trace_id"])
    observation = _observation_row(args.observation, record.pair_group_id)
    evidence_start = int(observation["evidence_start_output_token"])
    boundary = int(observation["annotated_boundary_output_token"])
    if evidence_start != record.evidence_onset or boundary != record.completion_boundary:
        raise ValueError("P1 record and boundary-audit labels disagree")

    variant = _variant()
    fit = select_records(b1, normal=True, folds=FIT_FOLDS)
    bank = _build_bank(fit, variant)
    anchors = _reference_metadata(fit, variant)
    if len(anchors) != bank.features.shape[0]:
        raise ValueError("reference-anchor metadata is misaligned")
    for index, anchor in enumerate(anchors):
        if (
            anchor["trace_id"] != bank.trace_ids[index]
            or anchor["workflow_family"] != bank.workflow_families[index]
            or anchor["age"] != bank.ages[index]
        ):
            raise ValueError("reference bank ordering changed")

    trace, token_rows = _decode_token_rows(record)
    routing = load_cached_routing(record)
    if len(token_rows) != routing["top_k_ids"].shape[1]:
        raise ValueError("trace tokens and routing cache are misaligned")
    ends, query_features = _signatures(record, variant)
    scored = _score_record(record, variant, bank)
    distances = _pairwise_distance(query_features, bank.features, variant.bands)

    expected_endpoints = [int(value) for value in scored["persistent_endpoints"].tolist()]
    expected_scores = [float(value) for value in scored["persistent_scores"].tolist()]
    if expected_endpoints != selected_row["score_endpoints"]:
        raise ValueError("reconstructed P1 endpoints differ from the frozen result")
    if any(abs(left - right) > 1e-6 for left, right in zip(expected_scores, selected_row["scores"], strict=True)):
        raise ValueError("reconstructed P1 scores differ from the frozen result")

    reference_token_cache: dict[str, list[dict[str, Any]]] = {}
    nearest_rows: list[dict[str, Any]] = []
    endpoint_details: dict[int, dict[str, Any]] = {}
    for position, end_value in enumerate(ends.tolist()):
        end = int(end_value)
        pool_key, pool = _full_pool_key(record.workflow_family, age_bin(end), bank, {})
        ordered_local = torch.argsort(distances[position, pool])[:NEIGHBOR_K]
        nearest_indices = pool[ordered_local].tolist()
        nearest: list[dict[str, Any]] = []
        for rank, reference_index_value in enumerate(nearest_indices, start=1):
            reference_index = int(reference_index_value)
            anchor = anchors[reference_index]
            reference_record = anchor["record"]
            if reference_record.trace_id not in reference_token_cache:
                _, reference_token_cache[reference_record.trace_id] = _decode_token_rows(reference_record)
            reference_tokens = reference_token_cache[reference_record.trace_id]
            row = {
                "query_end": end,
                "query_window_start": end - WIDTH + 1,
                "query_window_text": _snippet(token_rows, end - WIDTH + 1, end),
                "pool_key": pool_key,
                "rank": rank,
                "distance": float(distances[position, reference_index].item()),
                "reference_trace_id": reference_record.trace_id,
                "reference_arm": reference_record.arm,
                "reference_workflow": reference_record.workflow,
                "reference_window_start": int(anchor["window_start"]),
                "reference_window_end": int(anchor["window_end"]),
                "reference_window_text": _snippet(
                    reference_tokens,
                    int(anchor["window_start"]),
                    int(anchor["window_end"]),
                ),
            }
            nearest.append(row)
            nearest_rows.append(row)

        kth_reference_index = int(nearest_indices[NEIGHBOR_K - 1])
        layer_affinity = (query_features[position] * bank.features[kth_reference_index]).sum(dim=1)
        layer_distance = (1.0 - layer_affinity.clamp(0.0, 1.0)).clamp_min(0.0).sqrt()
        raw = float(scored["raw_scores"][position].item())
        tail_values = bank.tail_scores.get(pool_key, bank.tail_scores["global_reference"])
        tail_exceedance_count = int((tail_values >= raw).sum().item())
        p_value = (1.0 + tail_exceedance_count) / (tail_values.numel() + 1.0)
        middle_distance = float(layer_distance[list(MIDDLE_LAYERS)].mean().item())
        late_distance = float(layer_distance[list(LATE_LAYERS)].mean().item())
        reconstructed = 0.5 * (middle_distance + late_distance)
        if abs(raw - reconstructed) > 1e-6 or abs(raw - nearest[-1]["distance"]) > 1e-6:
            raise ValueError(f"kNN score decomposition failed at endpoint {end}")
        endpoint_details[end] = {
            "position": position,
            "pool_key": pool_key,
            "tail_count": int(tail_values.numel()),
            "tail_exceedance_count": tail_exceedance_count,
            "p_value": p_value,
            "maximum_attainable_rarity": math.log(int(tail_values.numel()) + 1),
            "raw_knn": raw,
            "rarity": float(scored["rarity_scores"][position].item()),
            "persistent": None,
            "kth_reference_index": kth_reference_index,
            "kth_reference_trace_id": anchors[kth_reference_index]["trace_id"],
            "kth_reference_window_end": anchors[kth_reference_index]["window_end"],
            "middle_distance": middle_distance,
            "late_distance": late_distance,
            "layer_distances": [float(value) for value in layer_distance.tolist()],
            "nearest": nearest,
        }
    for end, score in zip(
        scored["persistent_endpoints"].tolist(),
        scored["persistent_scores"].tolist(),
        strict=True,
    ):
        endpoint_details[int(end)]["persistent"] = float(score)
    for end, detail in endpoint_details.items():
        if detail["persistent"] is None:
            detail["maximum_attainable_persistent"] = None
            continue
        previous = endpoint_details[end - 1]
        detail["maximum_attainable_persistent"] = min(
            float(previous["maximum_attainable_rarity"]),
            float(detail["maximum_attainable_rarity"]),
        )

    probabilities = routing["probabilities"].float()
    top_k_ids = routing["top_k_ids"].long()
    layer_rows: list[dict[str, Any]] = []
    timeline_rows: list[dict[str, Any]] = []
    per_token_layer_stats: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for token in token_rows:
        token_index = int(token["token_index"])
        detail = endpoint_details.get(token_index)
        for layer in range(probabilities.shape[0]):
            selected_ids = [int(value) for value in top_k_ids[layer, token_index].tolist()]
            selected_probabilities = [
                float(probabilities[layer, token_index, expert].item())
                for expert in selected_ids
            ]
            previous_jsd = (
                None
                if token_index == 0
                else _jsd(probabilities[layer, token_index - 1], probabilities[layer, token_index])
            )
            previous_overlap = None
            if token_index > 0:
                previous_ids = set(int(value) for value in top_k_ids[layer, token_index - 1].tolist())
                previous_overlap = len(previous_ids.intersection(selected_ids)) / float(len(selected_ids))
            band = "early"
            if layer in MIDDLE_LAYERS:
                band = "middle"
            elif layer in LATE_LAYERS:
                band = "late"
            layer_row: dict[str, Any] = {
                "token_index": token_index,
                "token_id": token["token_id"],
                "token_text": token["token_text"],
                "phase": _phase(token_index, evidence_start, boundary),
                "layer": layer,
                "band": band,
                "top8_expert_ids": json.dumps(selected_ids, separators=(",", ":")),
                "top8_router_probabilities": json.dumps(
                    [round(value, 7) for value in selected_probabilities], separators=(",", ":")
                ),
                "top1_expert_id": int(probabilities[layer, token_index].argmax().item()),
                "top1_probability": float(probabilities[layer, token_index].max().item()),
                "router_entropy": _entropy(probabilities[layer, token_index]),
                "previous_token_jsd": previous_jsd,
                "previous_top8_overlap_fraction": previous_overlap,
                "p1_window_eligible": detail is not None,
                "p1_query_selection_distribution": "",
                "p1_kth_normal_selection_distribution": "",
                "p1_hellinger_to_kth_normal": "",
            }
            if detail is not None:
                position = int(detail["position"])
                kth = int(detail["kth_reference_index"])
                layer_row["p1_query_selection_distribution"] = _distribution_json(
                    query_features[position, layer].square()
                )
                layer_row["p1_kth_normal_selection_distribution"] = _distribution_json(
                    bank.features[kth, layer].square()
                )
                layer_row["p1_hellinger_to_kth_normal"] = detail["layer_distances"][layer]
            layer_rows.append(layer_row)
            per_token_layer_stats[token_index].append(layer_row)

        selected_layers = [
            row for row in per_token_layer_stats[token_index] if row["band"] in {"middle", "late"}
        ]
        middle = [row for row in selected_layers if row["band"] == "middle"]
        late = [row for row in selected_layers if row["band"] == "late"]
        timeline_rows.append(
            {
                **token,
                "phase": _phase(token_index, evidence_start, boundary),
                "window_start": token_index - WIDTH + 1 if detail is not None else "",
                "age_bin": age_bin(token_index) if detail is not None else "",
                "raw_knn_score": detail["raw_knn"] if detail is not None else "",
                "rarity_score": detail["rarity"] if detail is not None else "",
                "empirical_p_value": detail["p_value"] if detail is not None else "",
                "tail_exceedance_count": (
                    detail["tail_exceedance_count"] if detail is not None else ""
                ),
                "persistent_score": (
                    detail["persistent"] if detail is not None and detail["persistent"] is not None else ""
                ),
                "threshold": threshold if detail is not None and detail["persistent"] is not None else "",
                "threshold_margin": (
                    detail["persistent"] - threshold
                    if detail is not None and detail["persistent"] is not None
                    else ""
                ),
                "p1_pool_key": detail["pool_key"] if detail is not None else "",
                "p1_tail_count": detail["tail_count"] if detail is not None else "",
                "maximum_attainable_rarity": (
                    detail["maximum_attainable_rarity"] if detail is not None else ""
                ),
                "maximum_attainable_persistent": (
                    detail["maximum_attainable_persistent"]
                    if detail is not None and detail["maximum_attainable_persistent"] is not None
                    else ""
                ),
                "threshold_attainable": bool(
                    detail is not None
                    and detail["maximum_attainable_persistent"] is not None
                    and detail["maximum_attainable_persistent"] > threshold
                ),
                "alarm": bool(detail is not None and detail["persistent"] is not None and detail["persistent"] > threshold),
                "middle_kth_distance": detail["middle_distance"] if detail is not None else "",
                "late_kth_distance": detail["late_distance"] if detail is not None else "",
                "middle_previous_token_jsd": _mean(row["previous_token_jsd"] for row in middle),
                "late_previous_token_jsd": _mean(row["previous_token_jsd"] for row in late),
                "middle_previous_top8_overlap": _mean(
                    row["previous_top8_overlap_fraction"] for row in middle
                ),
                "late_previous_top8_overlap": _mean(
                    row["previous_top8_overlap_fraction"] for row in late
                ),
                "middle_router_entropy": _mean(row["router_entropy"] for row in middle),
                "late_router_entropy": _mean(row["router_entropy"] for row in late),
                "top1_experts_l0_to_l15": json.dumps(
                    [row["top1_expert_id"] for row in per_token_layer_stats[token_index]],
                    separators=(",", ":"),
                ),
                "kth_normal_trace_id": detail["kth_reference_trace_id"] if detail is not None else "",
                "kth_normal_window_end": detail["kth_reference_window_end"] if detail is not None else "",
                "_plot_layer_distances": (
                    json.dumps(detail["layer_distances"], separators=(",", ":"))
                    if detail is not None
                    else ""
                ),
            }
        )

    def phase_summary(name: str) -> dict[str, Any]:
        rows = [row for row in timeline_rows if row["phase"] == name]
        return {
            "token_count": len(rows),
            "raw_knn_mean": _mean(row["raw_knn_score"] if row["raw_knn_score"] != "" else None for row in rows),
            "raw_knn_max": _maximum(row["raw_knn_score"] if row["raw_knn_score"] != "" else None for row in rows),
            "rarity_mean": _mean(row["rarity_score"] if row["rarity_score"] != "" else None for row in rows),
            "rarity_max": _maximum(row["rarity_score"] if row["rarity_score"] != "" else None for row in rows),
            "persistent_mean": _mean(
                row["persistent_score"] if row["persistent_score"] != "" else None for row in rows
            ),
            "persistent_max": _maximum(
                row["persistent_score"] if row["persistent_score"] != "" else None for row in rows
            ),
            "middle_kth_distance_mean": _mean(
                row["middle_kth_distance"] if row["middle_kth_distance"] != "" else None for row in rows
            ),
            "late_kth_distance_mean": _mean(
                row["late_kth_distance"] if row["late_kth_distance"] != "" else None for row in rows
            ),
            "middle_previous_token_jsd_mean": _mean(row["middle_previous_token_jsd"] for row in rows),
            "late_previous_token_jsd_mean": _mean(row["late_previous_token_jsd"] for row in rows),
            "middle_previous_top8_overlap_mean": _mean(row["middle_previous_top8_overlap"] for row in rows),
            "late_previous_top8_overlap_mean": _mean(row["late_previous_top8_overlap"] for row in rows),
        }

    layer_phase_summary: list[dict[str, Any]] = []
    for layer in range(probabilities.shape[0]):
        row: dict[str, Any] = {
            "layer": layer,
            "band": "early" if layer < 5 else ("middle" if layer < 11 else "late"),
        }
        for phase_name in ("routine_pre_evidence", "drift_evidence_phrase", "drift_continuation"):
            selected = [
                item
                for item in layer_rows
                if item["layer"] == layer
                and item["phase"] == phase_name
                and item["p1_hellinger_to_kth_normal"] != ""
            ]
            row[f"{phase_name}_hellinger_mean"] = _mean(
                float(item["p1_hellinger_to_kth_normal"]) for item in selected
            )
        pre = row["routine_pre_evidence_hellinger_mean"]
        continuation = row["drift_continuation_hellinger_mean"]
        row["continuation_minus_pre"] = (
            None if pre is None or continuation is None else continuation - pre
        )
        layer_phase_summary.append(row)

    persistent_values = [
        (int(row["token_index"]), float(row["persistent_score"]))
        for row in timeline_rows
        if row["persistent_score"] != ""
    ]
    max_end, maximum = max(persistent_values, key=lambda item: item[1])
    eligible_rows = [row for row in timeline_rows if row["persistent_score"] != ""]
    pre_eligible_rows = [
        row for row in eligible_rows if int(row["token_index"]) < evidence_start
    ]
    post_eligible_rows = [
        row for row in eligible_rows if int(row["token_index"]) >= evidence_start
    ]

    def saturated(row: dict[str, Any]) -> bool:
        return abs(
            float(row["persistent_score"]) - float(row["maximum_attainable_persistent"])
        ) < 1e-5

    key_ends = sorted(
        {
            max(WIDTH, evidence_start - 1),
            evidence_start,
            boundary,
            min(len(token_rows) - 1, evidence_start + WIDTH - 1),
            min(len(token_rows) - 1, boundary + WIDTH - 1),
            max_end,
            len(token_rows) - 1,
        }
    )
    key_endpoints: list[dict[str, Any]] = []
    for end in key_ends:
        detail = endpoint_details[end]
        key_endpoints.append(
            {
                "end": end,
                "token_text": token_rows[end]["token_text"],
                "phase": _phase(end, evidence_start, boundary),
                "window_text": _snippet(token_rows, end - WIDTH + 1, end),
                "raw_knn": detail["raw_knn"],
                "rarity": detail["rarity"],
                "empirical_p_value": detail["p_value"],
                "tail_exceedance_count": detail["tail_exceedance_count"],
                "persistent": detail["persistent"],
                "threshold_margin": (
                    None if detail["persistent"] is None else detail["persistent"] - threshold
                ),
                "pool_key": detail["pool_key"],
                "tail_count": detail["tail_count"],
                "maximum_attainable_rarity": detail["maximum_attainable_rarity"],
                "maximum_attainable_persistent": detail["maximum_attainable_persistent"],
                "threshold_attainable": bool(
                    detail["maximum_attainable_persistent"] is not None
                    and detail["maximum_attainable_persistent"] > threshold
                ),
                "middle_distance": detail["middle_distance"],
                "late_distance": detail["late_distance"],
                "nearest_normal_windows": detail["nearest"],
            }
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(args.output_dir / "token_timeline.csv", timeline_rows)
    _write_csv(args.output_dir / "token_layer_routes.csv", layer_rows)
    _write_csv(args.output_dir / "nearest_normal_windows.csv", nearest_rows)
    _write_plot(
        args.output_dir / "p1_miss_zoom.png",
        timeline_rows,
        threshold,
        evidence_start,
        boundary,
    )

    candidate_rows = [
        {
            "trace_id": item["trace_id"],
            "evidence_onset": int(item["evidence_onset"]),
            "post_onset_token_count": int(item["decode_token_count"]) - int(item["evidence_onset"]),
            "maximum": max(float(value) for value in item["scores"]),
        }
        for item in candidates
    ]
    summary = {
        "schema_version": 1,
        "analysis_id": "normal-manifold-p1-representative-miss-zoom",
        "source_result": str(args.p1_result.relative_to(ROOT)),
        "direction": PRIMARY_DIRECTION,
        "selection": {
            "rule": "Among complete misses, require the specified pre/post context, choose the trace whose maximum persistent score is closest to the candidate median, then break ties by trace_id.",
            "minimum_pre_onset_tokens": args.minimum_pre_onset_tokens,
            "minimum_post_onset_tokens": args.minimum_post_onset_tokens,
            "candidate_count": len(candidates),
            "candidate_median_maximum": candidate_median,
            "rule_selected_trace_id": selected_by_rule["trace_id"],
            "manual_trace_override": args.trace_id,
            "candidates": sorted(candidate_rows, key=lambda item: item["trace_id"]),
        },
        "trace": {
            "trace_id": record.trace_id,
            "pair_group_id": record.pair_group_id,
            "workflow": record.workflow,
            "workflow_family": record.workflow_family,
            "domain": record.domain,
            "channel": record.channel,
            "decode_token_count": len(token_rows),
            "trace_path": str((record.trace_dir / "trace.json").relative_to(ROOT)),
            "output": [event for event in trace["events"] if event["kind"] == "model_generation"][-1]["content"],
        },
        "boundary": {
            **observation,
            "evaluation_onset_semantics": "evidence_start_output_token is the first token of the audited evidence phrase and is the P1 latency origin.",
            "completion_boundary_semantics": "annotated_boundary_output_token is the existing adjudicated point at which the evidence phrase establishes the deviation.",
        },
        "p1": {
            "threshold": threshold,
            "maximum": maximum,
            "maximum_endpoint": max_end,
            "maximum_margin": maximum - threshold,
            "alarm_rule": "persistent_score > threshold (strict)",
            "alarm_onset_ends": selected_row["alarm_onset_ends"],
            "threshold_unattainable_endpoint_count": sum(
                row["persistent_score"] != "" and not row["threshold_attainable"]
                for row in timeline_rows
            ),
            "post_onset_threshold_unattainable_endpoint_count": sum(
                int(row["token_index"]) >= evidence_start
                and row["persistent_score"] != ""
                and not row["threshold_attainable"]
                for row in timeline_rows
            ),
            "post_onset_eligible_endpoint_count": sum(
                int(row["token_index"]) >= evidence_start and row["persistent_score"] != ""
                for row in timeline_rows
            ),
            "pre_onset_ceiling_saturated_endpoint_count": sum(
                saturated(row) for row in pre_eligible_rows
            ),
            "post_onset_ceiling_saturated_endpoint_count": sum(
                saturated(row) for row in post_eligible_rows
            ),
            "post_onset_ceiling_saturated_endpoint_rate": (
                sum(saturated(row) for row in post_eligible_rows) / len(post_eligible_rows)
            ),
            "reference_fit_trace_count": len(fit),
            "reference_anchor_count": len(anchors),
            "width": WIDTH,
            "neighbor_k": NEIGHBOR_K,
            "bands": {"middle": list(MIDDLE_LAYERS), "late": list(LATE_LAYERS)},
        },
        "phase_summaries": {
            phase_name: phase_summary(phase_name)
            for phase_name in (
                "routine_pre_evidence",
                "drift_evidence_phrase",
                "drift_continuation",
            )
        },
        "layer_phase_summary": layer_phase_summary,
        "key_endpoints": key_endpoints,
        "matched_traces": _matched_trace_summaries(b2, direction["trace_results"], record.pair_group_id),
        "files": {
            "token_timeline": "token_timeline.csv",
            "token_layer_routes": "token_layer_routes.csv",
            "nearest_normal_windows": "nearest_normal_windows.csv",
            "plot": "p1_miss_zoom.png",
        },
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    args = _args()
    summary = calculate(args)
    print(
        json.dumps(
            {
                "trace_id": summary["trace"]["trace_id"],
                "evidence_start": summary["boundary"]["evidence_start_output_token"],
                "completion_boundary": summary["boundary"]["annotated_boundary_output_token"],
                "threshold": summary["p1"]["threshold"],
                "maximum": summary["p1"]["maximum"],
                "maximum_endpoint": summary["p1"]["maximum_endpoint"],
                "output_dir": str(args.output_dir),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
