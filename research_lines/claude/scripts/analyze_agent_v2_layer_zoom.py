#!/usr/bin/env python3
"""Post-hoc per-layer zoom-in of the frozen B2 horizon-384 routing replay.

This is a descriptive mechanism audit, not a detector selection or a B3
confirmation.  It separates layer-wise event magnitude at the frozen semantic
boundary from layer-wise recovery-versus-persistence after engagement.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_agent_v2_engagement_mechanism import (  # noqa: E402
    _config_hash,
    _replay_records,
)
from phase_a.normal_manifold import (  # noqa: E402
    ensure_routing_cache,
    evenly_spaced_positions,
    load_cached_routing,
    read_manifold_traces,
    sha256,
)
from routing import validate_trace  # noqa: E402
from run_agent_v2_proposal3_ldc import (  # noqa: E402
    _canonical_fit_ids,
    token_selection_signatures,
)


ANALYSIS_ID = "agent-v2-b2-horizon384-layer-zoom-posthoc-v1"
LAYER_COUNT = 16
EXPERT_COUNT = 64
NEIGHBOR_K = 5
REFERENCE_ANCHORS_PER_TRACE = 8
EARLY_WIDTH = 16
LATE_OFFSET = 32
HORIZON = 64
EVENT_REFERENCE_WIDTH = 16
EVENT_OFFSETS = (-8, -4, -1, 0, 1, 4, 8, 16, 32)
BOOTSTRAP_REPLICATES = 5000
BOOTSTRAP_SEED = 5092026

REPLAY_CONFIG_SHA256 = (
    "ab00100f56298fad9c08d4e2075aa70db7d2e1071ce609d9b4209e4d241abebf"
)
REPLAY_INDEX_SHA256 = (
    "5399b0b32769c8ef98722080da9ef1f159f586fba6d1d907656afcc8e34eac10"
)
PREFIX_AUDIT_SHA256 = (
    "3405298d0cc5d29a07899f32dc113bb84d08334de51bab6ce1dd442053a41359"
)
ENGAGEMENT_LABELS_SHA256 = (
    "8ad05186bbe7e508f3019a8c91aa71df8e369922bcdd4562b367bd7f298839d7"
)
ENGAGEMENT_SUMMARY_SHA256 = (
    "50b890ee4e30ff81855db5547cef53636669a5c4207c6930a969844736fae5e3"
)
COLLECTION_REPORT_SHA256 = (
    "b730343735831d962a3df4d484fd6a6ac21b652c18b5fc569d99dd7b4135b0a3"
)
EXPECTED_ENGAGEMENT_COUNTS = {
    "bounded_engagement_resisted": 5,
    "cross_domain_execution": 40,
    "no_observable_engagement": 35,
}

DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "agent_v2" / "layer_zoom_b2_horizon384"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-posthoc-b2-analysis", action="store_true")
    parser.add_argument(
        "--b1", type=Path, default=ROOT / "artifacts/agent_v2/agent_v2_5_b1"
    )
    parser.add_argument(
        "--b2", type=Path, default=ROOT / "artifacts/agent_v2/agent_v2_5_b2"
    )
    parser.add_argument(
        "--replay",
        type=Path,
        default=ROOT / "artifacts/agent_v2/agent_v2_5_b2_horizon384",
    )
    parser.add_argument(
        "--historical-cache",
        type=Path,
        default=ROOT / "artifacts/agent_v2/normal_manifold_cache",
    )
    parser.add_argument(
        "--replay-cache",
        type=Path,
        default=ROOT / "artifacts/agent_v2/agent_v2_5_b2_horizon384_cache",
    )
    parser.add_argument(
        "--observation",
        type=Path,
        default=ROOT / "artifacts/agent_v2/routing_observation_atlas/observation.json",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_json_hash(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def validate_frozen_replay(replay_dir: Path) -> dict[str, str]:
    """Validate only previously frozen B2 inputs; B3 has no code path here."""

    expected = {
        "sample_index": (replay_dir / "sample_index.jsonl", REPLAY_INDEX_SHA256),
        "prefix_audit": (
            replay_dir / "prefix_replay_audit.json",
            PREFIX_AUDIT_SHA256,
        ),
        "engagement_summary": (
            replay_dir / "engagement_adjudication_summary.json",
            ENGAGEMENT_SUMMARY_SHA256,
        ),
        "collection_report": (
            replay_dir / "collection_report.json",
            COLLECTION_REPORT_SHA256,
        ),
        "engagement_labels": (
            ROOT
            / "data/agent_v2/agent_v2_5_b2_horizon384_engagement_adjudications.jsonl",
            ENGAGEMENT_LABELS_SHA256,
        ),
    }
    observed: dict[str, str] = {}
    for name, (path, expected_hash) in expected.items():
        actual = sha256(path)
        if actual != expected_hash:
            raise ValueError(f"layer zoom frozen {name} hash mismatch: {actual}")
        observed[name] = actual
    if _read_json(expected["prefix_audit"][0]).get("exact_paired_replay_passed") is not True:
        raise ValueError("layer zoom exact-prefix replay gate failed")
    summary = _read_json(expected["engagement_summary"][0])
    if summary.get("engagement_class_counts") != EXPECTED_ENGAGEMENT_COUNTS:
        raise ValueError("layer zoom engagement strata changed")
    collection = _read_json(expected["collection_report"][0])
    if collection.get("routing_feature_comparisons_performed") is not False:
        raise ValueError("layer zoom behavior collection was not routing blind")
    config = _read_json(replay_dir / "resolved_experiment_config.json")
    if _canonical_json_hash(config) != REPLAY_CONFIG_SHA256:
        raise ValueError("layer zoom replay config hash mismatch")
    if _config_hash(config) != REPLAY_CONFIG_SHA256:
        raise ValueError("layer zoom replay config helper disagrees")
    return observed


def layer_hellinger_distances(
    queries: torch.Tensor, references: torch.Tensor
) -> torch.Tensor:
    """Return Hellinger distance [query, reference, layer]."""

    if queries.ndim != 3 or references.ndim != 3:
        raise ValueError("layer distance inputs must be [row,layer,expert]")
    if queries.shape[1:] != (LAYER_COUNT, EXPERT_COUNT):
        raise ValueError("layer distance query shape changed")
    if references.shape[1:] != (LAYER_COUNT, EXPERT_COUNT):
        raise ValueError("layer distance reference shape changed")
    similarities = torch.einsum("qle,rle->qrl", queries.float(), references.float())
    return (1.0 - similarities).clamp_min(0.0).sqrt()


def robust_layer_location_scale(values: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    if values.ndim != 2 or values.shape[1] != LAYER_COUNT:
        raise ValueError("layer reference values must be [row,16]")
    q25, center, q75 = torch.quantile(
        values.float(),
        torch.tensor([0.25, 0.50, 0.75], dtype=torch.float32),
        dim=0,
    )
    scale = ((q75 - q25) / 1.349).clamp_min(torch.finfo(torch.float32).eps)
    return center, scale


def build_layer_reference(
    rows: Sequence[tuple[str, torch.Tensor]],
) -> dict[str, Any]:
    """Build the same 208 normal anchors as LDC, now retaining all 16 layers."""

    anchors: list[torch.Tensor] = []
    trace_ids: list[str] = []
    for trace_id, top_k_ids in rows:
        signatures = token_selection_signatures(top_k_ids)
        for position in evenly_spaced_positions(
            signatures.shape[0], REFERENCE_ANCHORS_PER_TRACE
        ):
            anchors.append(signatures[position])
            trace_ids.append(trace_id)
    matrix = torch.stack(anchors)
    if matrix.shape != (208, LAYER_COUNT, EXPERT_COUNT):
        raise ValueError(f"layer zoom normal bank shape changed: {tuple(matrix.shape)}")

    distances = layer_hellinger_distances(matrix, matrix)
    for index, trace_id in enumerate(trace_ids):
        excluded = torch.tensor(
            [candidate == trace_id for candidate in trace_ids], dtype=torch.bool
        )
        distances[index, excluded, :] = math.inf
    raw = torch.kthvalue(distances, NEIGHBOR_K, dim=1).values
    center, scale = robust_layer_location_scale(raw)
    return {
        "features": matrix,
        "trace_ids": tuple(trace_ids),
        "raw": raw,
        "center": center,
        "scale": scale,
    }


def score_layer_novelty(top_k_ids: torch.Tensor, bank: Mapping[str, Any]) -> torch.Tensor:
    signatures = token_selection_signatures(top_k_ids)
    distances = layer_hellinger_distances(signatures, bank["features"])
    raw = torch.kthvalue(distances, NEIGHBOR_K, dim=1).values
    return (raw - bank["center"]) / bank["scale"]


def recovery_delta(scores: torch.Tensor, start: int) -> torch.Tensor | None:
    """Layer delta = late novelty - early novelty at one frozen event start."""

    if scores.ndim != 2 or scores.shape[1] != LAYER_COUNT:
        raise ValueError("layer score stream must be [token,16]")
    if start < 0:
        raise ValueError("recovery start must be non-negative")
    if start + HORIZON > scores.shape[0]:
        return None
    early = scores[start : start + EARLY_WIDTH].mean(dim=0)
    late = scores[start + LATE_OFFSET : start + HORIZON].mean(dim=0)
    return late - early


def probability_jsd(left: torch.Tensor, right: torch.Tensor) -> torch.Tensor:
    if left.shape != right.shape or left.shape[-1] != EXPERT_COUNT:
        raise ValueError("probability JSD inputs do not align")
    left = left.float().clamp_min(torch.finfo(torch.float32).tiny)
    right = right.float().clamp_min(torch.finfo(torch.float32).tiny)
    midpoint = 0.5 * (left + right)
    return 0.5 * (
        (left * (left / midpoint).log()).sum(dim=-1)
        + (right * (right / midpoint).log()).sum(dim=-1)
    )


def event_curve(
    probabilities: torch.Tensor, center: int
) -> dict[int, torch.Tensor] | None:
    """Per-layer token-to-pre16-reference JSD at fixed event offsets."""

    if probabilities.ndim != 3 or probabilities.shape[0] != LAYER_COUNT:
        raise ValueError("probabilities must be [16,token,64]")
    if center < EVENT_REFERENCE_WIDTH:
        return None
    reference = probabilities[
        :, center - EVENT_REFERENCE_WIDTH : center, :
    ].float().mean(dim=1)
    result: dict[int, torch.Tensor] = {}
    for offset in EVENT_OFFSETS:
        position = center + offset
        if 0 <= position < probabilities.shape[1]:
            result[offset] = probability_jsd(
                probabilities[:, position, :], reference
            )
    return result


def normalized_center(center: int, attack_length: int, control_length: int) -> int:
    return int(round((center / attack_length) * control_length))


def matched_event_row(
    event_kind: str,
    behavior_class: str,
    trace_id: str,
    center: int,
    attack_probabilities: torch.Tensor,
    clean_probabilities: torch.Tensor,
    benign_probabilities: torch.Tensor,
) -> dict[str, Any]:
    attack_length = int(attack_probabilities.shape[1])
    clean_center = normalized_center(
        center, attack_length, int(clean_probabilities.shape[1])
    )
    benign_center = normalized_center(
        center, attack_length, int(benign_probabilities.shape[1])
    )
    curves = {
        "attack": event_curve(attack_probabilities, center),
        "clean": event_curve(clean_probabilities, clean_center),
        "benign": event_curve(benign_probabilities, benign_center),
    }
    attack_only = {
        str(offset): curves["attack"][offset].tolist()
        for offset in EVENT_OFFSETS
        if curves["attack"] is not None and offset in curves["attack"]
    }
    values: dict[str, Any] = {}
    if all(curve is not None for curve in curves.values()):
        assert curves["attack"] is not None
        assert curves["clean"] is not None
        assert curves["benign"] is not None
        for offset in EVENT_OFFSETS:
            if all(offset in curve for curve in curves.values() if curve is not None):
                control = 0.5 * (
                    curves["clean"][offset] + curves["benign"][offset]
                )
                values[str(offset)] = {
                    "attack": curves["attack"][offset].tolist(),
                    "control": control.tolist(),
                    "contrast": (curves["attack"][offset] - control).tolist(),
                }
    return {
        "event_kind": event_kind,
        "behavior_class": behavior_class,
        "trace_id": trace_id,
        "attack_center": center,
        "attack_length": attack_length,
        "clean_center": clean_center,
        "clean_length": int(clean_probabilities.shape[1]),
        "benign_center": benign_center,
        "benign_length": int(benign_probabilities.shape[1]),
        "attack_only_offsets": attack_only,
        "available_offsets": values,
    }


def _describe(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "minimum": min(values),
        "maximum": max(values),
    }


def _bootstrap_mean_ci(values: Sequence[float], label: str) -> list[float] | None:
    if not values:
        return None
    tensor = torch.tensor(values, dtype=torch.float64)
    seed = int(hashlib.sha256(label.encode()).hexdigest()[:8], 16)
    generator = torch.Generator().manual_seed(seed)
    indices = torch.randint(
        tensor.numel(),
        (BOOTSTRAP_REPLICATES, tensor.numel()),
        generator=generator,
    )
    means = tensor[indices].mean(dim=1)
    return [
        float(torch.quantile(means, 0.025)),
        float(torch.quantile(means, 0.975)),
    ]


def _describe_with_ci(values: Sequence[float], label: str) -> dict[str, Any]:
    result = _describe(values)
    result["bootstrap_mean_ci95"] = _bootstrap_mean_ci(values, label)
    return result


def _auroc(positives: Sequence[float], negatives: Sequence[float]) -> float | None:
    if not positives or not negatives:
        return None
    wins = sum(
        (positive > negative) + 0.5 * (positive == negative)
        for positive in positives
        for negative in negatives
    )
    return wins / (len(positives) * len(negatives))


def bootstrap_column_contrast(
    positives: torch.Tensor, negatives: torch.Tensor
) -> tuple[torch.Tensor, torch.Tensor]:
    if positives.ndim != 2 or negatives.ndim != 2:
        raise ValueError("bootstrap matrices must be two-dimensional")
    generator = torch.Generator().manual_seed(BOOTSTRAP_SEED)
    positive_indices = torch.randint(
        positives.shape[0],
        (BOOTSTRAP_REPLICATES, positives.shape[0]),
        generator=generator,
    )
    negative_indices = torch.randint(
        negatives.shape[0],
        (BOOTSTRAP_REPLICATES, negatives.shape[0]),
        generator=generator,
    )
    contrasts = (
        positives[positive_indices].mean(dim=1)
        - negatives[negative_indices].mean(dim=1)
    )
    return (
        torch.quantile(contrasts, 0.025, dim=0),
        torch.quantile(contrasts, 0.975, dim=0),
    )


def summarize_recovery(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    bounded_rows = [
        row for row in rows if row["behavior_class"] == "bounded_engagement_resisted"
    ]
    execution_rows = [
        row for row in rows if row["behavior_class"] == "cross_domain_execution"
    ]
    bounded = torch.tensor([row["delta"] for row in bounded_rows], dtype=torch.float64)
    execution = torch.tensor(
        [row["delta"] for row in execution_rows], dtype=torch.float64
    )
    if bounded.shape[1:] != (LAYER_COUNT,) or execution.shape[1:] != (LAYER_COUNT,):
        raise ValueError("layer recovery cohorts are empty or malformed")
    lower, upper = bootstrap_column_contrast(execution, bounded)
    layer_rows = []
    for layer in range(LAYER_COUNT):
        b = bounded[:, layer].tolist()
        e = execution[:, layer].tolist()
        loo_aurocs = [
            _auroc(e, [value for index, value in enumerate(b) if index != removed])
            for removed in range(len(b))
        ]
        valid_loo = [value for value in loo_aurocs if value is not None]
        layer_rows.append(
            {
                "layer": layer,
                "bounded": _describe(b),
                "execution": _describe(e),
                "execution_minus_bounded_mean": statistics.fmean(e)
                - statistics.fmean(b),
                "bootstrap_ci95": [float(lower[layer]), float(upper[layer])],
                "auroc": _auroc(e, b),
                "bounded_recovery_fraction": sum(value < 0.0 for value in b) / len(b),
                "execution_sustained_fraction": sum(value >= 0.0 for value in e)
                / len(e),
                "leave_one_bounded_out_auroc_range": [
                    min(valid_loo),
                    max(valid_loo),
                ],
            }
        )
    ranking = sorted(
        ((row["layer"], row["auroc"]) for row in layer_rows),
        key=lambda item: float(item[1]),
        reverse=True,
    )
    return {
        "eligible_counts": {
            "bounded_engagement_resisted": len(bounded_rows),
            "cross_domain_execution": len(execution_rows),
        },
        "per_layer": layer_rows,
        "auroc_ranking": [
            {"layer": layer, "auroc": auroc} for layer, auroc in ranking
        ],
    }


def summarize_event_rows(
    rows: Sequence[dict[str, Any]], event_kind: str
) -> dict[str, Any]:
    selected = [row for row in rows if row["event_kind"] == event_kind]
    offsets: dict[str, Any] = {}
    for offset in EVENT_OFFSETS:
        attack_only_rows = [
            row["attack_only_offsets"][str(offset)]
            for row in selected
            if str(offset) in row["attack_only_offsets"]
        ]
        offset_rows = [
            row["available_offsets"][str(offset)]
            for row in selected
            if str(offset) in row["available_offsets"]
        ]
        layers = []
        for layer in range(LAYER_COUNT):
            contrast_values = [
                float(row["contrast"][layer]) for row in offset_rows
            ]
            layers.append(
                {
                    "layer": layer,
                    "attack": _describe(
                        [float(row["attack"][layer]) for row in offset_rows]
                    ),
                    "control": _describe(
                        [float(row["control"][layer]) for row in offset_rows]
                    ),
                    "contrast": _describe_with_ci(
                        contrast_values,
                        f"{event_kind}-{offset}-layer-{layer}",
                    ),
                }
            )
        band_contrast = {}
        attack_only_bands = {}
        for band, indices in {
            "early_L0_4": range(0, 5),
            "middle_L5_10": range(5, 11),
            "late_L11_15": range(11, 16),
        }.items():
            values = [
                statistics.fmean(float(row["contrast"][layer]) for layer in indices)
                for row in offset_rows
            ]
            band_contrast[band] = _describe_with_ci(
                values, f"{event_kind}-{offset}-{band}"
            )
            attack_values = [
                statistics.fmean(float(row[layer]) for layer in indices)
                for row in attack_only_rows
            ]
            attack_only_bands[band] = _describe_with_ci(
                attack_values, f"{event_kind}-{offset}-{band}-attack-only"
            )
        offsets[str(offset)] = {
            "matched_count": len(offset_rows),
            "attack_only_count": len(attack_only_rows),
            "per_layer": layers,
            "band_contrast": band_contrast,
            "attack_only_band_jsd": attack_only_bands,
        }
    return {
        "event_kind": event_kind,
        "total_rows": len(selected),
        "fully_unavailable_rows": sum(not row["available_offsets"] for row in selected),
        "offsets": offsets,
    }


def rank_correlation(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("rank correlation needs aligned nontrivial inputs")

    def ranks(values: Sequence[float]) -> torch.Tensor:
        order = sorted(range(len(values)), key=lambda index: values[index])
        result = torch.empty(len(values), dtype=torch.float64)
        cursor = 0
        while cursor < len(order):
            end = cursor + 1
            while end < len(order) and values[order[end]] == values[order[cursor]]:
                end += 1
            rank = 0.5 * (cursor + end - 1)
            for position in range(cursor, end):
                result[order[position]] = rank
            cursor = end
        return result

    left_rank = ranks(left)
    right_rank = ranks(right)
    return float(torch.corrcoef(torch.stack([left_rank, right_rank]))[0, 1])


def band_means(values: Sequence[float]) -> dict[str, float]:
    return {
        "early_L0_4": statistics.fmean(values[0:5]),
        "middle_L5_10": statistics.fmean(values[5:11]),
        "late_L11_15": statistics.fmean(values[11:16]),
    }


def _write_csvs(
    output_dir: Path,
    recovery_rows: Sequence[dict[str, Any]],
    recovery_summary: Mapping[str, Any],
    task_event: Mapping[str, Any],
) -> None:
    with (output_dir / "per_layer_summary.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        fieldnames = [
            "layer",
            "bounded_mean_delta",
            "execution_mean_delta",
            "execution_minus_bounded_mean",
            "ci95_lower",
            "ci95_upper",
            "auroc",
            "bounded_recovery_fraction",
            "execution_sustained_fraction",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in recovery_summary["per_layer"]:
            writer.writerow(
                {
                    "layer": row["layer"],
                    "bounded_mean_delta": row["bounded"]["mean"],
                    "execution_mean_delta": row["execution"]["mean"],
                    "execution_minus_bounded_mean": row[
                        "execution_minus_bounded_mean"
                    ],
                    "ci95_lower": row["bootstrap_ci95"][0],
                    "ci95_upper": row["bootstrap_ci95"][1],
                    "auroc": row["auroc"],
                    "bounded_recovery_fraction": row["bounded_recovery_fraction"],
                    "execution_sustained_fraction": row[
                        "execution_sustained_fraction"
                    ],
                }
            )

    with (output_dir / "per_sample_delta.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        fieldnames = [
            "trace_id",
            "behavior_class",
            "engagement_onset",
            "task_deviation_onset",
            "engagement_to_deviation_gap",
            "sustained_layer_count",
            *[f"layer_{layer}_delta" for layer in range(LAYER_COUNT)],
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in recovery_rows:
            writer.writerow(
                {
                    "trace_id": row["trace_id"],
                    "behavior_class": row["behavior_class"],
                    "engagement_onset": row["engagement_onset"],
                    "task_deviation_onset": row["task_deviation_onset"],
                    "engagement_to_deviation_gap": row[
                        "engagement_to_deviation_gap"
                    ],
                    "sustained_layer_count": row["sustained_layer_count"],
                    **{
                        f"layer_{layer}_delta": row["delta"][layer]
                        for layer in range(LAYER_COUNT)
                    },
                }
            )

    with (output_dir / "task_deviation_event_time.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        fieldnames = ["offset", "matched_count", "layer", "mean_contrast_jsd"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for offset, result in task_event["offsets"].items():
            for row in result["per_layer"]:
                writer.writerow(
                    {
                        "offset": offset,
                        "matched_count": result["matched_count"],
                        "layer": row["layer"],
                        "mean_contrast_jsd": row["contrast"].get("mean"),
                    }
                )


def _plot(
    output_dir: Path,
    recovery_rows: Sequence[dict[str, Any]],
    recovery_summary: Mapping[str, Any],
    task_event: Mapping[str, Any],
) -> Path:
    os.environ.setdefault("MPLCONFIGDIR", str(output_dir / "mplconfig"))
    import matplotlib  # noqa: PLC0415

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt  # noqa: PLC0415

    offsets = list(EVENT_OFFSETS)
    event_matrix = torch.tensor(
        [
            [
                task_event["offsets"][str(offset)]["per_layer"][layer][
                    "contrast"
                ].get("mean", float("nan"))
                for offset in offsets
            ]
            for layer in range(LAYER_COUNT)
        ]
    )
    bounded_means = [
        row["bounded"]["mean"] for row in recovery_summary["per_layer"]
    ]
    execution_means = [
        row["execution"]["mean"] for row in recovery_summary["per_layer"]
    ]
    ordered_samples = sorted(
        recovery_rows,
        key=lambda row: (
            row["behavior_class"] != "bounded_engagement_resisted",
            row["trace_id"],
        ),
    )
    sample_matrix = torch.tensor([row["delta"] for row in ordered_samples])

    figure, axes = plt.subplots(3, 1, figsize=(11, 13), constrained_layout=True)
    event_limit = max(float(event_matrix.abs().nan_to_num().max()), 1e-6)
    image = axes[0].imshow(
        event_matrix.numpy(),
        aspect="auto",
        cmap="coolwarm",
        vmin=-event_limit,
        vmax=event_limit,
    )
    axes[0].set_title("Matched routing-probability JSD contrast at task-deviation onset")
    axes[0].set_ylabel("MoE layer")
    axes[0].set_xticks(range(len(offsets)), labels=offsets)
    axes[0].set_xlabel("Token offset from frozen deviation onset")
    figure.colorbar(image, ax=axes[0], label="attack minus paired controls")

    layers = list(range(LAYER_COUNT))
    axes[1].plot(layers, bounded_means, marker="o", label="bounded/resisted")
    axes[1].plot(layers, execution_means, marker="o", label="execution")
    axes[1].axhline(0.0, color="black", linewidth=0.8)
    axes[1].set_title("Per-layer novelty trajectory after engagement onset")
    axes[1].set_xlabel("MoE layer")
    axes[1].set_ylabel("late minus early novelty")
    axes[1].legend()

    sample_limit = max(float(torch.quantile(sample_matrix.abs(), 0.98)), 1e-6)
    image = axes[2].imshow(
        sample_matrix.numpy(),
        aspect="auto",
        cmap="coolwarm",
        vmin=-sample_limit,
        vmax=sample_limit,
    )
    axes[2].axhline(4.5, color="black", linewidth=1.2)
    axes[2].set_title("Sample-by-layer recovery/persistence heterogeneity")
    axes[2].set_xlabel("MoE layer")
    axes[2].set_ylabel("5 bounded samples, then 35 execution samples")
    axes[2].set_xticks(range(LAYER_COUNT))
    figure.colorbar(image, ax=axes[2], label="late minus early novelty")

    path = output_dir / "layer_zoom.png"
    figure.savefig(path, dpi=180)
    plt.close(figure)
    return path


def calculate(args: argparse.Namespace) -> dict[str, Any]:
    replay_dir = args.replay.resolve()
    replay_cache = args.replay_cache.resolve()
    input_hashes = validate_frozen_replay(replay_dir)

    historical_cache = args.historical_cache.resolve()
    observation = args.observation.resolve()
    b1 = read_manifold_traces(args.b1.resolve(), historical_cache, observation, "b1")
    b2 = read_manifold_traces(args.b2.resolve(), historical_cache, observation, "b2")
    ensure_routing_cache((*b1, *b2), historical_cache)
    fit_ids = _canonical_fit_ids(args.b1.resolve(), "b1") | _canonical_fit_ids(
        args.b2.resolve(), "b2"
    )
    fit = [record for record in (*b1, *b2) if record.trace_id in fit_ids]
    if len(fit) != 26:
        raise ValueError("layer zoom canonical normal fit set changed")
    bank = build_layer_reference(
        [
            (record.trace_id, load_cached_routing(record)["top_k_ids"])
            for record in fit
        ]
    )

    records, metadata = _replay_records(replay_dir, replay_cache)
    replay_audit = ensure_routing_cache(
        records, replay_cache, validate_trace_fn=validate_trace
    )
    grouped: dict[str, dict[str, Any]] = {}
    for record in records:
        grouped.setdefault(record.pair_group_id, {})[record.arm] = record
    if len(grouped) != 80 or any(set(group) != {"clean", "benign_control", "attack"} for group in grouped.values()):
        raise ValueError("layer zoom replay triplets changed")

    recovery_rows: list[dict[str, Any]] = []
    task_anchor_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    censored = Counter()
    for group_index, (group_id, arms) in enumerate(sorted(grouped.items()), start=1):
        attack = arms["attack"]
        source = metadata[attack.trace_id]
        behavior_class = source["engagement_class"]
        if behavior_class not in {
            "bounded_engagement_resisted",
            "cross_domain_execution",
        }:
            continue
        attack_tensors = load_cached_routing(attack)
        clean_tensors = load_cached_routing(arms["clean"])
        benign_tensors = load_cached_routing(arms["benign_control"])
        scores = score_layer_novelty(attack_tensors["top_k_ids"], bank)
        engagement_onset = int(source["engagement_onset"])
        task_onset = attack.completion_boundary
        delta = recovery_delta(scores, engagement_onset)
        if delta is None:
            censored[f"engagement_delta::{behavior_class}"] += 1
        else:
            recovery_rows.append(
                {
                    "trace_id": attack.trace_id,
                    "pair_group_id": group_id,
                    "behavior_class": behavior_class,
                    "engagement_onset": engagement_onset,
                    "task_deviation_onset": task_onset,
                    "engagement_to_deviation_gap": (
                        int(task_onset) - engagement_onset
                        if task_onset is not None
                        else None
                    ),
                    "support_resume_output_token": source[
                        "support_resume_output_token"
                    ],
                    "delta": delta.tolist(),
                    "sustained_layer_count": int((delta >= 0.0).sum().item()),
                }
            )
        if task_onset is not None:
            task_delta = recovery_delta(scores, int(task_onset))
            if task_delta is None:
                censored["task_delta::cross_domain_execution"] += 1
            else:
                task_anchor_rows.append(
                    {
                        "trace_id": attack.trace_id,
                        "task_deviation_onset": int(task_onset),
                        "delta": task_delta.tolist(),
                        "sustained_layer_count": int(
                            (task_delta >= 0.0).sum().item()
                        ),
                    }
                )

        for event_kind, center in (
            ("engagement_onset", engagement_onset),
            ("task_deviation_onset", task_onset),
            ("support_resume", source["support_resume_output_token"]),
        ):
            if center is None:
                continue
            event_rows.append(
                matched_event_row(
                    event_kind,
                    behavior_class,
                    attack.trace_id,
                    int(center),
                    attack_tensors["probabilities"],
                    clean_tensors["probabilities"],
                    benign_tensors["probabilities"],
                )
            )
        if group_index % 20 == 0:
            print(f"layer zoom: visited {group_index}/80 replay groups", flush=True)

    recovery_summary = summarize_recovery(recovery_rows)
    event_summaries = {
        event_kind: summarize_event_rows(event_rows, event_kind)
        for event_kind in (
            "engagement_onset",
            "task_deviation_onset",
            "support_resume",
        )
    }
    task_event = event_summaries["task_deviation_onset"]
    onset_layers = task_event["offsets"]["0"]["per_layer"]
    onset_contrast = [float(row["contrast"].get("mean", math.nan)) for row in onset_layers]
    recovery_aurocs = [
        float(row["auroc"]) for row in recovery_summary["per_layer"]
    ]
    if any(not math.isfinite(value) for value in onset_contrast):
        raise ValueError("task-deviation onset layer contrast is unavailable")

    task_delta_tensor = torch.tensor(
        [row["delta"] for row in task_anchor_rows], dtype=torch.float64
    )
    result = {
        "schema_version": 1,
        "analysis_id": ANALYSIS_ID,
        "analysis_role": "post-hoc descriptive mechanism zoom; not model selection or confirmation",
        "b3_used": False,
        "input_hashes": input_hashes,
        "replay_cache_audit": replay_audit,
        "method": {
            "layer_count": LAYER_COUNT,
            "normal_fit_traces": len(fit),
            "normal_anchors": int(bank["features"].shape[0]),
            "neighbor_k": NEIGHBOR_K,
            "event_reference_width": EVENT_REFERENCE_WIDTH,
            "event_offsets": list(EVENT_OFFSETS),
            "recovery_early_window": [0, 15],
            "recovery_late_window": [32, 63],
            "multiple_layer_policy": "report all 16; ranks are exploratory and do not select a detector",
        },
        "normal_bank": {
            "center_by_layer": bank["center"].tolist(),
            "scale_by_layer": bank["scale"].tolist(),
        },
        "engagement_anchor_recovery": recovery_summary,
        "task_deviation_anchor_execution": {
            "eligible_count": len(task_anchor_rows),
            "censored_count": censored["task_delta::cross_domain_execution"],
            "mean_delta_by_layer": task_delta_tensor.mean(dim=0).tolist(),
            "sustained_fraction_by_layer": (
                (task_delta_tensor >= 0.0).float().mean(dim=0).tolist()
            ),
            "sample_rows": task_anchor_rows,
        },
        "matched_event_time": event_summaries,
        "layer_role_comparison": {
            "task_deviation_onset_contrast_by_layer": onset_contrast,
            "task_deviation_onset_band_means": band_means(onset_contrast),
            "recovery_auroc_by_layer": recovery_aurocs,
            "recovery_auroc_band_means": band_means(recovery_aurocs),
            "spearman_onset_magnitude_vs_recovery_auroc": rank_correlation(
                onset_contrast, recovery_aurocs
            ),
        },
        "censored_counts": dict(sorted(censored.items())),
        "recovery_sample_rows": recovery_rows,
        "matched_event_rows": event_rows,
    }
    return result


def main() -> int:
    args = _args()
    if not args.execute_posthoc_b2_analysis:
        raise SystemExit(
            "Refusing to read B2 routing without --execute-posthoc-b2-analysis; "
            "this run is post-hoc and must not be presented as confirmation."
        )
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    result = calculate(args)
    _write_csvs(
        output_dir,
        result["recovery_sample_rows"],
        result["engagement_anchor_recovery"],
        result["matched_event_time"]["task_deviation_onset"],
    )
    figure = _plot(
        output_dir,
        result["recovery_sample_rows"],
        result["engagement_anchor_recovery"],
        result["matched_event_time"]["task_deviation_onset"],
    )
    output = output_dir / "result.json"
    output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "analysis_id": ANALYSIS_ID,
                "result": str(output),
                "result_sha256": sha256(output),
                "figure": str(figure),
                "figure_sha256": sha256(figure),
                "b3_used": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
