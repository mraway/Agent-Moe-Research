#!/usr/bin/env python3
"""Audit every zero-alarm P1 false negative in the B1-to-B2 direction."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from inspect_normal_manifold_p1_miss import (  # noqa: E402
    MIDDLE_LAYERS,
    LATE_LAYERS,
    PRIMARY_DIRECTION,
    _decode_token_rows,
    _entropy,
    _jsd,
    _json,
    _mean,
    _observation_row,
    _phase,
    _variant,
)
from phase_a.normal_manifold import (  # noqa: E402
    ManifoldTrace,
    age_bin,
    load_cached_routing,
    read_manifold_traces,
    select_records,
)
from run_normal_manifold_p1_knn import (  # noqa: E402
    FIT_FOLDS,
    NEIGHBOR_K,
    WIDTH,
    _build_bank,
    _full_pool_key,
    _pairwise_distance,
    _score_record,
    _signatures,
)


DEFAULT_RESULT = ROOT / "artifacts" / "agent_v2" / "normal_manifold_p1_knn" / "result.json"
DEFAULT_OBSERVATION = (
    ROOT / "artifacts" / "agent_v2" / "routing_observation_atlas" / "observation.json"
)
DEFAULT_OUTPUT = (
    ROOT / "artifacts" / "agent_v2" / "normal_manifold_p1_all_misses_audit"
)
SELECTED_LAYERS = (*MIDDLE_LAYERS, *LATE_LAYERS)


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
    return parser.parse_args()


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty CSV: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _maximum(values: Iterable[float]) -> float | None:
    rows = [float(value) for value in values]
    return max(rows) if rows else None


def _relative_change(before: float | None, after: float | None) -> float | None:
    if before is None or after is None or before == 0.0:
        return None
    return (after - before) / before


def _trace_messages(trace: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "event_index": int(event["event_index"]),
            "kind": event["kind"],
            "content": event["content"],
        }
        for event in trace["events"]
        if event["kind"] in {"user_message", "assistant_message", "tool_result"}
    ]


def _phase_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "endpoint_count": len(rows),
        "raw_mean": _mean(float(row["raw_knn_score"]) for row in rows),
        "raw_max": _maximum(float(row["raw_knn_score"]) for row in rows),
        "rarity_mean": _mean(float(row["rarity_score"]) for row in rows),
        "rarity_max": _maximum(float(row["rarity_score"]) for row in rows),
        "persistent_mean": _mean(float(row["persistent_score"]) for row in rows),
        "persistent_max": _maximum(float(row["persistent_score"]) for row in rows),
        "middle_distance_mean": _mean(float(row["middle_kth_distance"]) for row in rows),
        "late_distance_mean": _mean(float(row["late_kth_distance"]) for row in rows),
        "middle_previous_token_jsd_mean": _mean(
            float(row["middle_previous_token_jsd"]) for row in rows
        ),
        "late_previous_token_jsd_mean": _mean(
            float(row["late_previous_token_jsd"]) for row in rows
        ),
    }


def _score_one(
    record: ManifoldTrace,
    frozen_row: dict[str, Any],
    observation_path: Path,
    variant: Any,
    bank: Any,
    threshold: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    observation = _observation_row(observation_path, record.pair_group_id)
    evidence_start = int(observation["evidence_start_output_token"])
    boundary = int(observation["annotated_boundary_output_token"])
    if evidence_start != int(frozen_row["evidence_onset"]):
        raise ValueError(f"onset mismatch for {record.trace_id}")
    if boundary != int(frozen_row["completion_boundary"]):
        raise ValueError(f"boundary mismatch for {record.trace_id}")

    trace, tokens = _decode_token_rows(record)
    routing = load_cached_routing(record)
    if len(tokens) != routing["top_k_ids"].shape[1]:
        raise ValueError(f"routing/token mismatch for {record.trace_id}")
    ends, query_features = _signatures(record, variant)
    scored = _score_record(record, variant, bank)
    distances = _pairwise_distance(query_features, bank.features, variant.bands)
    if [int(value) for value in scored["persistent_endpoints"].tolist()] != frozen_row["score_endpoints"]:
        raise ValueError(f"endpoint reconstruction mismatch for {record.trace_id}")
    reconstructed = [float(value) for value in scored["persistent_scores"].tolist()]
    if any(
        abs(left - right) > 1e-6
        for left, right in zip(reconstructed, frozen_row["scores"], strict=True)
    ):
        raise ValueError(f"score reconstruction mismatch for {record.trace_id}")

    endpoint_details: dict[int, dict[str, Any]] = {}
    layer_rows: list[dict[str, Any]] = []
    probabilities = routing["probabilities"].float()
    top_k_ids = routing["top_k_ids"].long()
    for position, end_value in enumerate(ends.tolist()):
        end = int(end_value)
        pool_key, pool = _full_pool_key(record.workflow_family, age_bin(end), bank, {})
        ordered_local = torch.argsort(distances[position, pool])[:NEIGHBOR_K]
        kth_reference = int(pool[ordered_local[-1]].item())
        layer_affinity = (
            query_features[position] * bank.features[kth_reference]
        ).sum(dim=1)
        layer_distance = (
            1.0 - layer_affinity.clamp(0.0, 1.0)
        ).clamp_min(0.0).sqrt()
        raw = float(scored["raw_scores"][position].item())
        middle_distance = float(layer_distance[list(MIDDLE_LAYERS)].mean().item())
        late_distance = float(layer_distance[list(LATE_LAYERS)].mean().item())
        if abs(raw - 0.5 * (middle_distance + late_distance)) > 1e-6:
            raise ValueError(f"layer decomposition failed for {record.trace_id}:{end}")
        tail = bank.tail_scores.get(pool_key, bank.tail_scores["global_reference"])
        exceedances = int((tail >= raw).sum().item())
        p_value = (1.0 + exceedances) / (tail.numel() + 1.0)
        endpoint_details[end] = {
            "position": position,
            "pool_key": pool_key,
            "tail_count": int(tail.numel()),
            "tail_exceedance_count": exceedances,
            "p_value": p_value,
            "rarity_ceiling": math.log(int(tail.numel()) + 1),
            "raw": raw,
            "rarity": float(scored["rarity_scores"][position].item()),
            "persistent": None,
            "middle": middle_distance,
            "late": late_distance,
            "layer_distances": [float(value) for value in layer_distance.tolist()],
            "kth_reference_trace_id": bank.trace_ids[kth_reference],
        }
    for end, value in zip(
        scored["persistent_endpoints"].tolist(),
        scored["persistent_scores"].tolist(),
        strict=True,
    ):
        endpoint_details[int(end)]["persistent"] = float(value)

    timeline_rows: list[dict[str, Any]] = []
    for end in scored["persistent_endpoints"].tolist():
        end = int(end)
        detail = endpoint_details[end]
        previous = endpoint_details[end - 1]
        persistent_ceiling = min(
            float(previous["rarity_ceiling"]), float(detail["rarity_ceiling"])
        )
        phase = _phase(end, evidence_start, boundary)
        middle_jsd = _mean(
            _jsd(probabilities[layer, end - 1], probabilities[layer, end])
            for layer in MIDDLE_LAYERS
        )
        late_jsd = _mean(
            _jsd(probabilities[layer, end - 1], probabilities[layer, end])
            for layer in LATE_LAYERS
        )
        row = {
            "trace_id": record.trace_id,
            "pair_group_id": record.pair_group_id,
            "workflow": record.workflow,
            "workflow_family": record.workflow_family,
            "channel": record.channel,
            "domain": record.domain,
            "token_index": end,
            "token_id": int(tokens[end]["token_id"]),
            "token_text": tokens[end]["token_text"],
            "phase": phase,
            "window_start": end - WIDTH + 1,
            "window_text": "".join(
                item["token_text"] for item in tokens[end - WIDTH + 1 : end + 1]
            ),
            "pool_key": detail["pool_key"],
            "tail_count": detail["tail_count"],
            "tail_exceedance_count": detail["tail_exceedance_count"],
            "empirical_p_value": detail["p_value"],
            "raw_knn_score": detail["raw"],
            "rarity_score": detail["rarity"],
            "persistent_score": detail["persistent"],
            "persistent_ceiling": persistent_ceiling,
            "threshold": threshold,
            "threshold_attainable": persistent_ceiling > threshold,
            "ceiling_saturated": abs(float(detail["persistent"]) - persistent_ceiling) < 1e-5,
            "middle_kth_distance": detail["middle"],
            "late_kth_distance": detail["late"],
            "middle_previous_token_jsd": middle_jsd,
            "late_previous_token_jsd": late_jsd,
            "kth_reference_trace_id": detail["kth_reference_trace_id"],
            "top1_experts_l0_to_l15": json.dumps(
                [
                    int(probabilities[layer, end].argmax().item())
                    for layer in range(probabilities.shape[0])
                ],
                separators=(",", ":"),
            ),
        }
        timeline_rows.append(row)
        for layer in SELECTED_LAYERS:
            selected_ids = [int(value) for value in top_k_ids[layer, end].tolist()]
            layer_rows.append(
                {
                    "trace_id": record.trace_id,
                    "token_index": end,
                    "token_text": tokens[end]["token_text"],
                    "phase": phase,
                    "layer": layer,
                    "band": "middle" if layer in MIDDLE_LAYERS else "late",
                    "hellinger_to_kth_normal": detail["layer_distances"][layer],
                    "top8_expert_ids": json.dumps(selected_ids, separators=(",", ":")),
                    "top8_router_probabilities": json.dumps(
                        [
                            round(float(probabilities[layer, end, expert].item()), 7)
                            for expert in selected_ids
                        ],
                        separators=(",", ":"),
                    ),
                    "top1_expert_id": int(probabilities[layer, end].argmax().item()),
                    "top1_probability": float(probabilities[layer, end].max().item()),
                    "router_entropy": _entropy(probabilities[layer, end]),
                    "previous_token_jsd": _jsd(
                        probabilities[layer, end - 1], probabilities[layer, end]
                    ),
                }
            )

    pre_rows = [row for row in timeline_rows if row["phase"] == "routine_pre_evidence"]
    evidence_rows = [
        row for row in timeline_rows if row["phase"] == "drift_evidence_phrase"
    ]
    continuation_rows = [
        row for row in timeline_rows if row["phase"] == "drift_continuation"
    ]
    post_rows = [row for row in timeline_rows if int(row["token_index"]) >= evidence_start]
    local_pre_rows = [
        row
        for row in timeline_rows
        if evidence_start - WIDTH <= int(row["token_index"]) < evidence_start
    ]
    local_post_rows = [
        row
        for row in timeline_rows
        if evidence_start <= int(row["token_index"]) < evidence_start + WIDTH
    ]
    attainable_post = [row for row in post_rows if row["threshold_attainable"]]
    saturated_post = [row for row in post_rows if row["ceiling_saturated"]]
    max_post = max(post_rows, key=lambda row: float(row["persistent_score"]))
    max_all = max(timeline_rows, key=lambda row: float(row["persistent_score"]))

    per_layer: list[dict[str, Any]] = []
    for layer in SELECTED_LAYERS:
        pre_values = [
            float(row["hellinger_to_kth_normal"])
            for row in layer_rows
            if int(row["layer"]) == layer and row["phase"] == "routine_pre_evidence"
        ]
        post_values = [
            float(row["hellinger_to_kth_normal"])
            for row in layer_rows
            if int(row["layer"]) == layer and row["phase"] != "routine_pre_evidence"
        ]
        pre_mean = _mean(pre_values)
        post_mean = _mean(post_values)
        per_layer.append(
            {
                "trace_id": record.trace_id,
                "layer": layer,
                "band": "middle" if layer in MIDDLE_LAYERS else "late",
                "pre_endpoint_count": len(pre_values),
                "post_endpoint_count": len(post_values),
                "pre_hellinger_mean": pre_mean,
                "post_hellinger_mean": post_mean,
                "post_minus_pre": (
                    None if pre_mean is None or post_mean is None else post_mean - pre_mean
                ),
            }
        )
    comparable_layers = [row for row in per_layer if row["post_minus_pre"] is not None]

    generation = [event for event in trace["events"] if event["kind"] == "model_generation"][-1]
    first_saturation = (
        min(int(row["token_index"]) for row in saturated_post) if saturated_post else None
    )
    result = {
        "trace_id": record.trace_id,
        "pair_group_id": record.pair_group_id,
        "workflow": record.workflow,
        "workflow_family": record.workflow_family,
        "channel": record.channel,
        "domain": record.domain,
        "decode_token_count": len(tokens),
        "evidence": observation["evidence"],
        "evidence_start": evidence_start,
        "completion_boundary": boundary,
        "post_endpoint_count": len(post_rows),
        "attainable_post_endpoint_count": len(attainable_post),
        "attainable_post_endpoint_rate": len(attainable_post) / len(post_rows),
        "post_threshold_support": (
            "none"
            if not attainable_post
            else ("all" if len(attainable_post) == len(post_rows) else "partial")
        ),
        "post_ceiling_saturated_count": len(saturated_post),
        "post_ceiling_saturated_rate": len(saturated_post) / len(post_rows),
        "first_post_ceiling_saturation": first_saturation,
        "first_post_ceiling_saturation_latency": (
            None if first_saturation is None else first_saturation - evidence_start
        ),
        "maximum_all": float(max_all["persistent_score"]),
        "maximum_all_endpoint": int(max_all["token_index"]),
        "maximum_post": float(max_post["persistent_score"]),
        "maximum_post_endpoint": int(max_post["token_index"]),
        "maximum_post_margin": float(max_post["persistent_score"]) - threshold,
        "pre": _phase_metrics(pre_rows),
        "evidence_phrase": _phase_metrics(evidence_rows),
        "continuation": _phase_metrics(continuation_rows),
        "post": _phase_metrics(post_rows),
        "local_boundary": {
            "pre_width": _phase_metrics(local_pre_rows),
            "post_width": _phase_metrics(local_post_rows),
            "raw_mean_change": (
                None
                if not local_pre_rows or not local_post_rows
                else _mean(float(row["raw_knn_score"]) for row in local_post_rows)
                - _mean(float(row["raw_knn_score"]) for row in local_pre_rows)
            ),
            "persistent_mean_change": (
                None
                if not local_pre_rows or not local_post_rows
                else _mean(float(row["persistent_score"]) for row in local_post_rows)
                - _mean(float(row["persistent_score"]) for row in local_pre_rows)
            ),
            "middle_distance_change": (
                None
                if not local_pre_rows or not local_post_rows
                else _mean(float(row["middle_kth_distance"]) for row in local_post_rows)
                - _mean(float(row["middle_kth_distance"]) for row in local_pre_rows)
            ),
            "late_distance_change": (
                None
                if not local_pre_rows or not local_post_rows
                else _mean(float(row["late_kth_distance"]) for row in local_post_rows)
                - _mean(float(row["late_kth_distance"]) for row in local_pre_rows)
            ),
        },
        "post_vs_pre": {
            "raw_relative_change": _relative_change(
                _mean(float(row["raw_knn_score"]) for row in pre_rows),
                _mean(float(row["raw_knn_score"]) for row in post_rows),
            ),
            "persistent_relative_change": _relative_change(
                _mean(float(row["persistent_score"]) for row in pre_rows),
                _mean(float(row["persistent_score"]) for row in post_rows),
            ),
            "middle_distance_change": (
                None
                if not pre_rows
                else _mean(float(row["middle_kth_distance"]) for row in post_rows)
                - _mean(float(row["middle_kth_distance"]) for row in pre_rows)
            ),
            "late_distance_change": (
                None
                if not pre_rows
                else _mean(float(row["late_kth_distance"]) for row in post_rows)
                - _mean(float(row["late_kth_distance"]) for row in pre_rows)
            ),
            "positive_layer_delta_count": sum(
                float(row["post_minus_pre"]) > 0.0 for row in comparable_layers
            ),
            "comparable_layer_count": len(comparable_layers),
        },
        "output": generation["content"],
        "stop_reason": generation["stop_reason"],
        "original_task_completed": bool(trace["outcome"]["original_task_completed"]),
        "attacker_goal_achieved": bool(trace["outcome"]["attacker_goal_achieved"]),
        "messages": _trace_messages(trace),
        "perturbation": trace["perturbation"],
        "trace_path": str((record.trace_dir / "trace.json").relative_to(ROOT)),
    }
    return result, timeline_rows, layer_rows


def _plot(path: Path, cases: Sequence[dict[str, Any]], timelines: Sequence[dict[str, Any]], threshold: float) -> None:
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/agent-moe-matplotlib")
    import matplotlib.pyplot as plt

    by_trace: dict[str, list[dict[str, Any]]] = {}
    for row in timelines:
        by_trace.setdefault(str(row["trace_id"]), []).append(row)
    figure, axes = plt.subplots(7, 2, figsize=(15, 21), sharey=True)
    flat_axes = list(axes.flat)
    for axis, case in zip(flat_axes, cases, strict=False):
        rows = by_trace[case["trace_id"]]
        x = [int(row["token_index"]) for row in rows]
        score = [float(row["persistent_score"]) for row in rows]
        ceiling = [float(row["persistent_ceiling"]) for row in rows]
        axis.plot(x, score, color="#2864a5", linewidth=1.2, label="score")
        axis.plot(x, ceiling, color="#d17a00", linewidth=1.0, label="cell ceiling")
        axis.axhline(threshold, color="#b22222", linestyle="--", linewidth=1.0)
        axis.axvline(case["evidence_start"], color="#cc2f2f", linestyle=":", linewidth=1.2)
        axis.set_title(
            f"{case['trace_id'].split('--')[0]}\n{case['domain']} / {case['channel']}",
            fontsize=8,
        )
        axis.grid(alpha=0.15)
    flat_axes[-1].axis("off")
    handles, labels = flat_axes[0].get_legend_handles_labels()
    figure.legend(handles, labels, loc="lower right")
    figure.suptitle(
        "All 13 zero-alarm P1 misses: persistent score, finite-cell ceiling, and drift onset",
        fontsize=14,
    )
    figure.supxlabel("decode token / window end")
    figure.supylabel("P1 persistent rarity")
    figure.tight_layout(rect=(0.02, 0.02, 1.0, 0.98))
    figure.savefig(path, dpi=170)
    plt.close(figure)


def calculate(args: argparse.Namespace) -> dict[str, Any]:
    p1 = _json(args.p1_result)
    direction = p1["directions"][PRIMARY_DIRECTION]
    threshold = float(direction["calibration"]["threshold"])
    misses = [
        row
        for row in direction["trace_results"]
        if row["positive"]
        and not row["pre_onset_alarm"]
        and row["first_post_onset_alarm"] is None
    ]
    if len(misses) != 13:
        raise ValueError(f"expected 13 complete misses, found {len(misses)}")

    b1 = read_manifold_traces(
        args.b1.resolve(), args.cache_dir.resolve(), args.observation.resolve(), "b1"
    )
    b2 = read_manifold_traces(
        args.b2.resolve(), args.cache_dir.resolve(), args.observation.resolve(), "b2"
    )
    b2_lookup = {record.trace_id: record for record in b2}
    variant = _variant()
    fit = select_records(b1, normal=True, folds=FIT_FOLDS)
    bank = _build_bank(fit, variant)

    case_rows: list[dict[str, Any]] = []
    timeline_rows: list[dict[str, Any]] = []
    token_layer_rows: list[dict[str, Any]] = []
    for index, frozen in enumerate(misses, start=1):
        case, timeline, layers = _score_one(
            b2_lookup[frozen["trace_id"]],
            frozen,
            args.observation,
            variant,
            bank,
            threshold,
        )
        case_rows.append(case)
        timeline_rows.extend(timeline)
        token_layer_rows.extend(layers)
        print(f"audited {index}/{len(misses)}: {frozen['trace_id']}", flush=True)

    matched_lookup = {row["trace_id"]: row for row in direction["trace_results"]}
    for case in case_rows:
        controls = [
            record
            for record in b2
            if record.pair_group_id == case["pair_group_id"] and not record.positive
        ]
        case["matched_controls"] = [
            {
                "trace_id": control.trace_id,
                "arm": control.arm,
                "decode_token_count": matched_lookup[control.trace_id]["decode_token_count"],
                "maximum": max(float(value) for value in matched_lookup[control.trace_id]["scores"]),
                "false_alarm": matched_lookup[control.trace_id]["false_alarm"],
            }
            for control in sorted(controls, key=lambda record: record.arm)
        ]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    flat_case_rows = []
    for case in case_rows:
        flat_case_rows.append(
            {
                "trace_id": case["trace_id"],
                "workflow": case["workflow"],
                "workflow_family": case["workflow_family"],
                "channel": case["channel"],
                "domain": case["domain"],
                "decode_token_count": case["decode_token_count"],
                "original_task_completed": case["original_task_completed"],
                "attacker_goal_achieved": case["attacker_goal_achieved"],
                "evidence": case["evidence"],
                "evidence_start": case["evidence_start"],
                "completion_boundary": case["completion_boundary"],
                "post_endpoint_count": case["post_endpoint_count"],
                "post_threshold_support": case["post_threshold_support"],
                "attainable_post_endpoint_count": case["attainable_post_endpoint_count"],
                "attainable_post_endpoint_rate": case["attainable_post_endpoint_rate"],
                "post_ceiling_saturated_count": case["post_ceiling_saturated_count"],
                "post_ceiling_saturated_rate": case["post_ceiling_saturated_rate"],
                "first_post_ceiling_saturation": case["first_post_ceiling_saturation"],
                "first_post_ceiling_saturation_latency": case[
                    "first_post_ceiling_saturation_latency"
                ],
                "maximum_post": case["maximum_post"],
                "maximum_post_endpoint": case["maximum_post_endpoint"],
                "maximum_post_margin": case["maximum_post_margin"],
                "pre_persistent_mean": case["pre"]["persistent_mean"],
                "post_persistent_mean": case["post"]["persistent_mean"],
                "pre_middle_distance_mean": case["pre"]["middle_distance_mean"],
                "post_middle_distance_mean": case["post"]["middle_distance_mean"],
                "pre_late_distance_mean": case["pre"]["late_distance_mean"],
                "post_late_distance_mean": case["post"]["late_distance_mean"],
                "raw_relative_change": case["post_vs_pre"]["raw_relative_change"],
                "persistent_relative_change": case["post_vs_pre"][
                    "persistent_relative_change"
                ],
                "positive_layer_delta_count": case["post_vs_pre"][
                    "positive_layer_delta_count"
                ],
                "comparable_layer_count": case["post_vs_pre"]["comparable_layer_count"],
                "local_raw_mean_change": case["local_boundary"]["raw_mean_change"],
                "local_persistent_mean_change": case["local_boundary"][
                    "persistent_mean_change"
                ],
                "local_middle_distance_change": case["local_boundary"][
                    "middle_distance_change"
                ],
                "local_late_distance_change": case["local_boundary"][
                    "late_distance_change"
                ],
            }
        )
    layer_phase_rows = [
        row
        for case in case_rows
        for row in _layer_phase_rows(case["trace_id"], token_layer_rows)
    ]
    _write_csv(args.output_dir / "misses.csv", flat_case_rows)
    _write_csv(args.output_dir / "token_score_timelines.csv", timeline_rows)
    _write_csv(args.output_dir / "token_layer_routes.csv", token_layer_rows)
    _write_csv(args.output_dir / "layer_phase_summary.csv", layer_phase_rows)
    (args.output_dir / "case_details.json").write_text(
        json.dumps(case_rows, indent=2) + "\n", encoding="utf-8"
    )
    _plot(args.output_dir / "p1_all_misses.png", case_rows, timeline_rows, threshold)

    support_counts = Counter(case["post_threshold_support"] for case in case_rows)
    family_activity: dict[str, Any] = {}
    for family in sorted({row["workflow_family"] for row in direction["trace_results"]}):
        negatives = [
            row
            for row in direction["trace_results"]
            if not row["positive"] and row["workflow_family"] == family
        ]
        positives = [
            row
            for row in direction["trace_results"]
            if row["positive"] and row["workflow_family"] == family
        ]
        family_activity[family] = {
            "non_drift_trace_count": len(negatives),
            "non_drift_false_alarm_count": sum(bool(row["false_alarm"]) for row in negatives),
            "drift_trace_count": len(positives),
            "drift_pre_onset_alarm_count": sum(
                bool(row["pre_onset_alarm"]) for row in positives
            ),
            "drift_clean_post_onset_hit_count": sum(
                not row["pre_onset_alarm"] and row["first_post_onset_alarm"] is not None
                for row in positives
            ),
            "drift_zero_alarm_count": sum(
                not row["pre_onset_alarm"] and row["first_post_onset_alarm"] is None
                for row in positives
            ),
        }
    summary = {
        "schema_version": 1,
        "analysis_id": "normal-manifold-p1-all-zero-alarm-misses-audit",
        "scope": {
            "direction": PRIMARY_DIRECTION,
            "definition": "positive trace with no pre-onset alarm and no post-onset alarm",
            "miss_count": len(case_rows),
            "threshold": threshold,
        },
        "threshold_support_counts": dict(support_counts),
        "alarm_activity_by_workflow_family": family_activity,
        "ceiling_saturated_case_count": sum(
            case["post_ceiling_saturated_count"] > 0 for case in case_rows
        ),
        "all_selected_layers_increase_count": sum(
            case["post_vs_pre"]["comparable_layer_count"] == len(SELECTED_LAYERS)
            and case["post_vs_pre"]["positive_layer_delta_count"] == len(SELECTED_LAYERS)
            for case in case_rows
        ),
        "insufficient_pre_context_count": sum(
            case["post_vs_pre"]["comparable_layer_count"] == 0 for case in case_rows
        ),
        "cases": flat_case_rows,
        "files": {
            "misses": "misses.csv",
            "case_details": "case_details.json",
            "token_score_timelines": "token_score_timelines.csv",
            "token_layer_routes": "token_layer_routes.csv",
            "layer_phase_summary": "layer_phase_summary.csv",
            "plot": "p1_all_misses.png",
        },
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def _layer_phase_rows(
    trace_id: str, rows: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    selected = [row for row in rows if row["trace_id"] == trace_id]
    result: list[dict[str, Any]] = []
    for layer in SELECTED_LAYERS:
        layer_rows = [row for row in selected if int(row["layer"]) == layer]
        pre = [
            float(row["hellinger_to_kth_normal"])
            for row in layer_rows
            if row["phase"] == "routine_pre_evidence"
        ]
        post = [
            float(row["hellinger_to_kth_normal"])
            for row in layer_rows
            if row["phase"] != "routine_pre_evidence"
        ]
        pre_mean = _mean(pre)
        post_mean = _mean(post)
        result.append(
            {
                "trace_id": trace_id,
                "layer": layer,
                "band": "middle" if layer in MIDDLE_LAYERS else "late",
                "pre_endpoint_count": len(pre),
                "post_endpoint_count": len(post),
                "pre_hellinger_mean": pre_mean,
                "post_hellinger_mean": post_mean,
                "post_minus_pre": (
                    None if pre_mean is None or post_mean is None else post_mean - pre_mean
                ),
            }
        )
    return result


def main() -> None:
    args = _args()
    result = calculate(args)
    print(
        json.dumps(
            {
                "analysis_id": result["analysis_id"],
                "miss_count": result["scope"]["miss_count"],
                "threshold_support_counts": result["threshold_support_counts"],
                "ceiling_saturated_case_count": result["ceiling_saturated_case_count"],
                "all_selected_layers_increase_count": result[
                    "all_selected_layers_increase_count"
                ],
                "output_dir": str(args.output_dir),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
