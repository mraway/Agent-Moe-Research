#!/usr/bin/env python3
"""Build a descriptive prefill/decode routing observation atlas for B1/B2."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import torch
from safetensors.torch import load_file


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase_a.routing_analysis import (  # noqa: E402
    RoutingSequence,
    jensen_shannon_divergence,
    load_final_generation_sequence,
)
from routing import validate_trace  # noqa: E402


PLAN = "docs/routing_data_observation_plan.md"
B1_SHA256 = "f1a95c5bf36d3233f445e4cb507c89e59c13eda1619af317c4efefadcea72df1"
B2_SHA256 = "e9412dde1b328f21943cc59f2a5db37797142026cd392166af202c285cd73942"
WIDTHS = (8, 16, 32)
PRIMARY_WIDTH = 16
EVENT_OFFSETS = tuple(range(-16, 33))
BOOTSTRAP_REPLICATES = 2000


@dataclass(frozen=True)
class SegmentStats:
    token_count: int
    probability: torch.Tensor
    selection: torch.Tensor
    entropy: torch.Tensor
    margin: torch.Tensor
    effective_experts: torch.Tensor


@dataclass(frozen=True)
class LoadedTrace:
    row: dict[str, Any]
    trace_dir: Path
    decode: RoutingSequence
    prefill: dict[str, SegmentStats]
    prefill_token_count: int
    prefill_role_counts: dict[str, int]


@dataclass(frozen=True)
class BoundaryChange:
    center: int
    width: int
    probability_jsd: torch.Tensor
    selection_tv: torch.Tensor
    entropy_delta: torch.Tensor
    margin_delta: torch.Tensor
    effective_experts_delta: torch.Tensor
    probability_delta: torch.Tensor
    selection_delta: torch.Tensor


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
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "routing_observation_atlas",
    )
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_index(run_dir: Path, batch: str) -> list[dict[str, Any]]:
    path = run_dir / "sample_index.jsonl"
    expected = B1_SHA256 if batch == "b1" else B2_SHA256
    actual = _sha256(path)
    if actual != expected:
        raise ValueError(f"{batch} sample index hash mismatch: {actual}")
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    if batch == "b1":
        rows = [row for row in rows if row["response_brief_condition"] == "absent"]
    expected_count = 120 if batch == "b1" else 240
    if len(rows) != expected_count:
        raise ValueError(f"{batch} selected trace count is {len(rows)}, expected {expected_count}")
    for row in rows:
        row["observation_batch"] = batch
    return rows


def _segment_stats(
    probabilities: torch.Tensor, top_k_ids: torch.Tensor, mask: torch.Tensor
) -> SegmentStats:
    if mask.ndim != 1 or mask.numel() != probabilities.shape[1]:
        raise ValueError("segment mask is not token-aligned")
    if not bool(mask.any()):
        raise ValueError("routing segment is empty")
    selected_probabilities = probabilities[:, mask, :]
    selected_ids = top_k_ids[:, mask, :]
    layer_count, token_count, expert_count = selected_probabilities.shape
    selection = torch.zeros(layer_count, expert_count, dtype=torch.float32)
    selection.scatter_add_(
        1,
        selected_ids.reshape(layer_count, -1),
        torch.ones_like(selected_ids, dtype=torch.float32).reshape(layer_count, -1),
    )
    selection /= float(token_count * selected_ids.shape[-1])
    top_two = selected_probabilities.topk(2, dim=-1).values
    entropy = -(
        selected_probabilities
        * selected_probabilities.clamp_min(torch.finfo(torch.float32).tiny).log()
    ).sum(dim=-1)
    return SegmentStats(
        token_count=token_count,
        probability=selected_probabilities.mean(dim=1),
        selection=selection,
        entropy=entropy.mean(dim=1),
        margin=(top_two[..., 0] - top_two[..., 1]).mean(dim=1),
        effective_experts=entropy.exp().mean(dim=1),
    )


def _load_prefill(trace_dir: Path) -> tuple[dict[str, SegmentStats], int, dict[str, int]]:
    rows = [
        json.loads(line)
        for line in (trace_dir / "manifest.jsonl").read_text().splitlines()
        if line
    ]
    prefill_rows = [row for row in rows if row["phase"] == "prefill"]
    if len(prefill_rows) != 1:
        raise ValueError(f"{trace_dir} has {len(prefill_rows)} prefill rows")
    row = prefill_rows[0]
    tensors = load_file(trace_dir / row["tensor_file"])
    probabilities = torch.softmax(tensors["router_logits"].float(), dim=-1)
    top_k_ids = tensors["top_k_ids"].long()
    roles = tuple(str(role) for role in row["token_roles"])
    if probabilities.shape[1] != len(roles):
        raise ValueError("prefill role alignment failed")
    role_counts = dict(Counter(roles))
    result: dict[str, SegmentStats] = {
        "all": _segment_stats(
            probabilities,
            top_k_ids,
            torch.ones(len(roles), dtype=torch.bool),
        )
    }
    for role in sorted(role_counts):
        mask = torch.tensor([candidate == role for candidate in roles], dtype=torch.bool)
        result[role] = _segment_stats(probabilities, top_k_ids, mask)
    return result, len(roles), role_counts


def _load_trace(run_dir: Path, row: dict[str, Any]) -> LoadedTrace:
    trace_dir = (run_dir / row["relative_path"]).resolve()
    validation = validate_trace(trace_dir)
    if not validation["passed"]:
        raise ValueError(f"routing validation failed for {trace_dir}")
    trace = json.loads((trace_dir / "trace.json").read_text())
    if trace["trace_id"] != row["trace_id"]:
        raise ValueError("sample-index trace ID differs from trace.json")
    decode = load_final_generation_sequence(trace_dir)
    generations = [event for event in trace["events"] if event["kind"] == "model_generation"]
    if len(generations) != 1:
        raise ValueError(f"{trace_dir} does not have exactly one model generation")
    if tuple(generations[0]["output_token_ids"]) != decode.token_ids:
        raise ValueError(f"decode token alignment failed for {trace_dir}")
    prefill, prefill_count, role_counts = _load_prefill(trace_dir)
    return LoadedTrace(row, trace_dir, decode, prefill, prefill_count, role_counts)


def _decode_segment(sequence: RoutingSequence, start: int, end: int) -> SegmentStats:
    if not 0 <= start < end <= len(sequence.token_ids):
        raise ValueError("decode segment bounds are invalid")
    mask = torch.zeros(len(sequence.token_ids), dtype=torch.bool)
    mask[start:end] = True
    return _segment_stats(sequence.probabilities, sequence.top_k_ids, mask)


def _boundary_change(
    sequence: RoutingSequence, center: int, width: int
) -> BoundaryChange | None:
    if center < width or center + width > len(sequence.token_ids):
        return None
    before = _decode_segment(sequence, center - width, center)
    after = _decode_segment(sequence, center, center + width)
    return BoundaryChange(
        center=center,
        width=width,
        probability_jsd=jensen_shannon_divergence(
            before.probability, after.probability
        ),
        selection_tv=0.5 * (after.selection - before.selection).abs().sum(dim=-1),
        entropy_delta=after.entropy - before.entropy,
        margin_delta=after.margin - before.margin,
        effective_experts_delta=(
            after.effective_experts - before.effective_experts
        ),
        probability_delta=after.probability - before.probability,
        selection_delta=after.selection - before.selection,
    )


def _normalized_center(boundary: int, attack_length: int, control_length: int) -> int:
    return int(round((boundary / attack_length) * control_length))


def _average_changes(left: BoundaryChange, right: BoundaryChange) -> dict[str, torch.Tensor]:
    return {
        name: 0.5 * (getattr(left, name) + getattr(right, name))
        for name in (
            "probability_jsd",
            "selection_tv",
            "entropy_delta",
            "margin_delta",
            "effective_experts_delta",
            "probability_delta",
            "selection_delta",
        )
    }


def _change_contrast(
    attack: BoundaryChange, clean: BoundaryChange, benign: BoundaryChange
) -> dict[str, torch.Tensor]:
    controls = _average_changes(clean, benign)
    return {
        name: getattr(attack, name) - controls[name]
        for name in controls
    }


def _event_curve(sequence: RoutingSequence, center: int) -> dict[int, float] | None:
    if center < PRIMARY_WIDTH:
        return None
    reference = sequence.probabilities[:, center - PRIMARY_WIDTH : center, :].mean(
        dim=1
    )
    result: dict[int, float] = {}
    for offset in EVENT_OFFSETS:
        token_index = center + offset
        if 0 <= token_index < len(sequence.token_ids):
            layer_jsd = jensen_shannon_divergence(
                sequence.probabilities[:, token_index, :], reference
            )
            result[offset] = float(layer_jsd.mean().item())
    return result


def _all_split_jsd(sequence: RoutingSequence, width: int) -> torch.Tensor:
    token_count = len(sequence.token_ids)
    if token_count < 2 * width:
        return torch.empty(0)
    probabilities = sequence.probabilities.float()
    prefix = torch.cat(
        (probabilities.new_zeros((probabilities.shape[0], 1, probabilities.shape[2])),
         probabilities.cumsum(dim=1)),
        dim=1,
    )
    centers = torch.arange(width, token_count - width + 1, dtype=torch.long)
    before = (
        prefix.index_select(1, centers) - prefix.index_select(1, centers - width)
    ) / width
    after = (
        prefix.index_select(1, centers + width) - prefix.index_select(1, centers)
    ) / width
    return jensen_shannon_divergence(before, after).mean(dim=0)


def _quantile(values: torch.Tensor, q: float) -> float:
    return float(torch.quantile(values.to(torch.float64), q).item())


def _bootstrap_mean_ci(values: Sequence[float], label: str) -> tuple[float, float]:
    tensor = torch.tensor(values, dtype=torch.float64)
    if not tensor.numel():
        raise ValueError("bootstrap needs observations")
    if tensor.numel() == 1:
        value = float(tensor.item())
        return value, value
    seed = int(hashlib.sha256(label.encode()).hexdigest()[:8], 16)
    generator = torch.Generator().manual_seed(seed)
    indices = torch.randint(
        tensor.numel(),
        (BOOTSTRAP_REPLICATES, tensor.numel()),
        generator=generator,
    )
    means = tensor[indices].mean(dim=1)
    return _quantile(means, 0.025), _quantile(means, 0.975)


def _describe(values: Sequence[float], label: str) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    tensor = torch.tensor(values, dtype=torch.float64)
    lower, upper = _bootstrap_mean_ci(values, label)
    return {
        "count": len(values),
        "mean": float(tensor.mean().item()),
        "median": _quantile(tensor, 0.5),
        "q25": _quantile(tensor, 0.25),
        "q75": _quantile(tensor, 0.75),
        "minimum": float(tensor.min().item()),
        "maximum": float(tensor.max().item()),
        "bootstrap_mean_ci95": [lower, upper],
    }


def _auroc(positives: Sequence[float], negatives: Sequence[float]) -> float | None:
    if not positives or not negatives:
        return None
    wins = sum(
        (positive > negative) + 0.5 * (positive == negative)
        for positive in positives
        for negative in negatives
    )
    return wins / (len(positives) * len(negatives))


def _scalar_pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right):
        raise ValueError("paired scalar sequences must have the same length")
    if len(left) < 2:
        return None
    return _pearson(
        torch.tensor(left, dtype=torch.float64),
        torch.tensor(right, dtype=torch.float64),
    )


def _cosine(left: torch.Tensor, right: torch.Tensor) -> float | None:
    left = left.reshape(-1).to(torch.float64)
    right = right.reshape(-1).to(torch.float64)
    denominator = left.norm() * right.norm()
    if float(denominator.item()) == 0.0:
        return None
    return float((left @ right / denominator).item())


def _pearson(left: torch.Tensor, right: torch.Tensor) -> float | None:
    left = left.reshape(-1).to(torch.float64)
    right = right.reshape(-1).to(torch.float64)
    return _cosine(left - left.mean(), right - right.mean())


def _top_entries(vector: torch.Tensor, count: int = 32) -> list[dict[str, Any]]:
    if vector.shape != (16, 64):
        raise ValueError(f"expected [16,64] direction, got {tuple(vector.shape)}")
    flat = vector.reshape(-1)
    indices = flat.abs().topk(min(count, flat.numel())).indices.tolist()
    return [
        {
            "layer": int(index // 64),
            "expert": int(index % 64),
            "delta": float(flat[index].item()),
            "sign": 1 if float(flat[index].item()) > 0 else -1,
        }
        for index in indices
    ]


def _direction_comparison(
    b1: torch.Tensor, b2: torch.Tensor, label: str
) -> dict[str, Any]:
    b1_top = _top_entries(b1)
    b2_top = _top_entries(b2)
    b1_map = {(row["layer"], row["expert"]): row for row in b1_top}
    b2_map = {(row["layer"], row["expert"]): row for row in b2_top}
    overlap = sorted(set(b1_map) & set(b2_map))
    return {
        "label": label,
        "flattened_cosine": _cosine(b1, b2),
        "flattened_pearson": _pearson(b1, b2),
        "per_layer_cosine": [_cosine(b1[layer], b2[layer]) for layer in range(16)],
        "per_layer_b1_l2": [float(b1[layer].norm().item()) for layer in range(16)],
        "per_layer_b2_l2": [float(b2[layer].norm().item()) for layer in range(16)],
        "top32_overlap_count": len(overlap),
        "top32_overlap_rate": len(overlap) / 32,
        "overlap_sign_agreement_count": sum(
            b1_map[key]["sign"] == b2_map[key]["sign"] for key in overlap
        ),
        "overlap_sign_agreement_rate": (
            sum(b1_map[key]["sign"] == b2_map[key]["sign"] for key in overlap)
            / len(overlap)
            if overlap
            else None
        ),
        "overlap": [
            {
                "layer": layer,
                "expert": expert,
                "b1_delta": b1_map[(layer, expert)]["delta"],
                "b2_delta": b2_map[(layer, expert)]["delta"],
            }
            for layer, expert in overlap
        ],
        "b1_top32": b1_top,
        "b2_top32": b2_top,
    }


def _tensor_mean(rows: Sequence[dict[str, Any]], key: str) -> torch.Tensor:
    return torch.stack([row[key] for row in rows]).mean(dim=0)


def _serialize_tensor(value: torch.Tensor) -> Any:
    return value.tolist()


def _serializable_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _serialize_tensor(value) if isinstance(value, torch.Tensor) else value
        for key, value in row.items()
    }


def _prefill_exposure(
    batch: str,
    attack: LoadedTrace,
    clean: LoadedTrace,
    benign: LoadedTrace,
) -> dict[str, Any]:
    channel = str(attack.row["attack_channel"])
    role = "tool" if channel == "tool_output" else "user"
    if any(role not in trace.prefill for trace in (attack, clean, benign)):
        raise ValueError(f"matched prefill role {role} is unavailable")
    a = attack.prefill[role]
    c = clean.prefill[role]
    b = benign.prefill[role]
    control_probability = 0.5 * (c.probability + b.probability)
    control_selection = 0.5 * (c.selection + b.selection)
    probability_jsd = jensen_shannon_divergence(a.probability, control_probability)
    selection_tv = 0.5 * (a.selection - control_selection).abs().sum(dim=-1)
    return {
        "batch": batch,
        "pair_group_id": attack.row["pair_group_id"],
        "attack_trace_id": attack.row["trace_id"],
        "positive": attack.row["behavior_label"] == "goal_drift",
        "channel": channel,
        "role": role,
        "domain": attack.row["target_domain"],
        "workflow": attack.row["workflow"],
        "attack_role_token_count": a.token_count,
        "clean_role_token_count": c.token_count,
        "benign_role_token_count": b.token_count,
        "probability_jsd": probability_jsd,
        "selection_tv": selection_tv,
        "entropy_delta": a.entropy - 0.5 * (c.entropy + b.entropy),
        "margin_delta": a.margin - 0.5 * (c.margin + b.margin),
        "probability_delta": a.probability - control_probability,
        "selection_delta": a.selection - control_selection,
    }


def _boundary_row(
    batch: str,
    attack: LoadedTrace,
    clean: LoadedTrace,
    benign: LoadedTrace,
    width: int,
    mapping: str,
    *,
    anchor_output_token: int | None = None,
    anchor_kind: str = "evidence_completion",
) -> dict[str, Any]:
    boundary_value = attack.row["goal_plan_deviation_start_output_token"]
    if boundary_value is None:
        raise ValueError("drift attack lacks a boundary")
    annotated_boundary = int(boundary_value["output_token_index"])
    boundary = (
        annotated_boundary if anchor_output_token is None else anchor_output_token
    )
    attack_length = len(attack.decode.token_ids)
    if mapping == "normalized":
        clean_center = _normalized_center(
            boundary, attack_length, len(clean.decode.token_ids)
        )
        benign_center = _normalized_center(
            boundary, attack_length, len(benign.decode.token_ids)
        )
    elif mapping == "exact":
        clean_center = benign_center = boundary
    else:
        raise ValueError(f"unknown pseudo-boundary mapping {mapping}")
    attack_change = _boundary_change(attack.decode, boundary, width)
    clean_change = _boundary_change(clean.decode, clean_center, width)
    benign_change = _boundary_change(benign.decode, benign_center, width)
    available = all(change is not None for change in (attack_change, clean_change, benign_change))
    result: dict[str, Any] = {
        "batch": batch,
        "pair_group_id": attack.row["pair_group_id"],
        "attack_trace_id": attack.row["trace_id"],
        "domain": attack.row["target_domain"],
        "workflow": attack.row["workflow"],
        "channel": attack.row["attack_channel"],
        "anchor_kind": anchor_kind,
        "width": width,
        "mapping": mapping,
        "attack_boundary": boundary,
        "annotated_boundary_output_token": annotated_boundary,
        "attack_decode_length": attack_length,
        "clean_center": clean_center,
        "clean_decode_length": len(clean.decode.token_ids),
        "benign_center": benign_center,
        "benign_decode_length": len(benign.decode.token_ids),
        "available": available,
    }
    if not available:
        return result
    assert attack_change is not None and clean_change is not None and benign_change is not None
    controls = _average_changes(clean_change, benign_change)
    contrasts = _change_contrast(attack_change, clean_change, benign_change)
    for name in controls:
        result[f"attack_{name}"] = getattr(attack_change, name)
        result[f"control_{name}"] = controls[name]
        result[f"contrast_{name}"] = contrasts[name]
    return result


def _event_row(
    batch: str,
    attack: LoadedTrace,
    clean: LoadedTrace,
    benign: LoadedTrace,
    *,
    anchor_output_token: int | None = None,
    anchor_kind: str = "evidence_completion",
) -> dict[str, Any]:
    boundary_value = attack.row["goal_plan_deviation_start_output_token"]
    if boundary_value is None:
        raise ValueError("drift attack lacks a boundary")
    annotated_boundary = int(boundary_value["output_token_index"])
    anchor = annotated_boundary if anchor_output_token is None else anchor_output_token
    attack_length = len(attack.decode.token_ids)
    clean_center = _normalized_center(
        anchor, attack_length, len(clean.decode.token_ids)
    )
    benign_center = _normalized_center(
        anchor, attack_length, len(benign.decode.token_ids)
    )
    attack_curve = _event_curve(attack.decode, anchor)
    clean_curve = _event_curve(clean.decode, clean_center)
    benign_curve = _event_curve(benign.decode, benign_center)
    values: dict[str, Any] = {
        "batch": batch,
        "pair_group_id": attack.row["pair_group_id"],
        "domain": attack.row["target_domain"],
        "channel": attack.row["attack_channel"],
        "anchor_kind": anchor_kind,
        "anchor_output_token": anchor,
        "annotated_boundary_output_token": annotated_boundary,
        "clean_center": clean_center,
        "benign_center": benign_center,
        "attack_curve_available": attack_curve is not None,
        "matched_curve_available": all(
            curve is not None for curve in (attack_curve, clean_curve, benign_curve)
        ),
        "attack": attack_curve or {},
        "control": {},
        "contrast": {},
    }
    if attack_curve is None or clean_curve is None or benign_curve is None:
        return values
    for offset in EVENT_OFFSETS:
        if offset in attack_curve and offset in clean_curve and offset in benign_curve:
            control = 0.5 * (clean_curve[offset] + benign_curve[offset])
            values["control"][offset] = control
            values["contrast"][offset] = attack_curve[offset] - control
    return values


def _token_index_at_character(
    token_texts: Sequence[str], character_index: int
) -> int:
    if character_index < 0:
        raise ValueError("character index must be non-negative")
    end = 0
    for token_index, token_text in enumerate(token_texts):
        end += len(token_text)
        if end > character_index:
            return token_index
    raise ValueError("character index is outside decoded token text")


def _boundary_annotation_row(batch: str, attack: LoadedTrace) -> dict[str, Any]:
    annotation_path = attack.trace_dir / "adjudication.json"
    if not annotation_path.exists():
        raise ValueError(f"drift trace lacks adjudication: {attack.trace_dir}")
    annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
    evidence = str(annotation["evidence"])
    character_start, character_end = map(int, annotation["evidence_char_span"])
    decoded = "".join(attack.decode.token_texts)
    if decoded[character_start:character_end] != evidence:
        raise ValueError(f"adjudication evidence text is misaligned: {attack.trace_dir}")
    start = _token_index_at_character(attack.decode.token_texts, character_start)
    boundary_value = attack.row["goal_plan_deviation_start_output_token"]
    if boundary_value is None:
        raise ValueError("drift trace lacks a boundary")
    end = int(boundary_value["output_token_index"])
    if end != int(annotation["evidence_output_token"]):
        raise ValueError(f"adjudication boundary is inconsistent: {attack.trace_dir}")
    return {
        "batch": batch,
        "pair_group_id": attack.row["pair_group_id"],
        "domain": attack.row["target_domain"],
        "channel": attack.row["attack_channel"],
        "evidence": evidence,
        "evidence_character_count": len(evidence),
        "evidence_start_output_token": start,
        "annotated_boundary_output_token": end,
        "evidence_token_span": end - start + 1,
        "boundary_minus_one_inside_evidence": start <= end - 1,
        "start_token_text": attack.decode.token_texts[start],
        "boundary_token_text": attack.decode.token_texts[end],
    }


def _boundary_annotation_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for batch in ("b1", "b2"):
        selected = [row for row in rows if row["batch"] == batch]
        inside_count = sum(
            row["boundary_minus_one_inside_evidence"] for row in selected
        )
        result[batch] = {
            "drift_annotation_count": len(selected),
            "evidence_token_span": _describe(
                [row["evidence_token_span"] for row in selected],
                f"boundary-annotation-{batch}-evidence-token-span",
            ),
            "multi_token_evidence_count": sum(
                row["evidence_token_span"] > 1 for row in selected
            ),
            "boundary_minus_one_inside_evidence_count": inside_count,
            "boundary_minus_one_inside_evidence_rate": inside_count / len(selected),
            "scenario_rows": selected,
        }
    return result


def _prefill_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"by_batch": {}, "direction_stability": {}}
    for batch in ("b1", "b2"):
        selected = [row for row in rows if row["batch"] == batch]
        batch_result: dict[str, Any] = {"all": {}, "by_outcome": {}, "by_channel": {}}
        for metric in ("probability_jsd", "selection_tv", "entropy_delta", "margin_delta"):
            scalar = [float(row[metric].mean().item()) for row in selected]
            batch_result["all"][metric] = _describe(scalar, f"prefill-{batch}-all-{metric}")
        for outcome, positive in (("drift", True), ("resist", False)):
            outcome_rows = [row for row in selected if row["positive"] == positive]
            batch_result["by_outcome"][outcome] = {
                metric: _describe(
                    [float(row[metric].mean().item()) for row in outcome_rows],
                    f"prefill-{batch}-{outcome}-{metric}",
                )
                for metric in ("probability_jsd", "selection_tv", "entropy_delta", "margin_delta")
            }
        for channel in sorted({row["channel"] for row in selected}):
            channel_rows = [row for row in selected if row["channel"] == channel]
            channel_drift = [row for row in channel_rows if row["positive"]]
            channel_resist = [row for row in channel_rows if not row["positive"]]
            role_token_deltas = [
                float(row["attack_role_token_count"])
                - 0.5
                * (
                    float(row["clean_role_token_count"])
                    + float(row["benign_role_token_count"])
                )
                for row in channel_rows
            ]
            batch_result["by_channel"][channel] = {
                "count": len(channel_rows),
                "drift_count": len(channel_drift),
                "resist_count": len(channel_resist),
                "all": {
                    metric: _describe(
                        [float(row[metric].mean().item()) for row in channel_rows],
                        f"prefill-{batch}-{channel}-all-{metric}",
                    )
                    for metric in (
                        "probability_jsd",
                        "selection_tv",
                        "entropy_delta",
                        "margin_delta",
                    )
                },
                "by_outcome": {
                    outcome: {
                        metric: _describe(
                            [float(row[metric].mean().item()) for row in outcome_rows],
                            f"prefill-{batch}-{channel}-{outcome}-{metric}",
                        )
                        for metric in (
                            "probability_jsd",
                            "selection_tv",
                            "entropy_delta",
                            "margin_delta",
                        )
                    }
                    for outcome, outcome_rows in (
                        ("drift", channel_drift),
                        ("resist", channel_resist),
                    )
                },
                "outcome_ranking": {
                    metric: {
                        "auroc_drift_over_resist": _auroc(
                            [float(row[metric].mean().item()) for row in channel_drift],
                            [float(row[metric].mean().item()) for row in channel_resist],
                        )
                    }
                    for metric in ("probability_jsd", "selection_tv")
                },
                "attack_minus_control_role_token_count": _describe(
                    role_token_deltas,
                    f"prefill-{batch}-{channel}-role-token-delta",
                ),
                "role_token_delta_correlation": {
                    metric: _scalar_pearson(
                        role_token_deltas,
                        [float(row[metric].mean().item()) for row in channel_rows],
                    )
                    for metric in ("probability_jsd", "selection_tv")
                },
            }
        drift = [row for row in selected if row["positive"]]
        resist = [row for row in selected if not row["positive"]]
        batch_result["outcome_ranking"] = {
            metric: {
                "auroc_drift_over_resist": _auroc(
                    [float(row[metric].mean().item()) for row in drift],
                    [float(row[metric].mean().item()) for row in resist],
                ),
                "per_layer_auroc": [
                    _auroc(
                        [float(row[metric][layer].item()) for row in drift],
                        [float(row[metric][layer].item()) for row in resist],
                    )
                    for layer in range(16)
                ],
            }
            for metric in ("probability_jsd", "selection_tv")
        }
        result["by_batch"][batch] = batch_result

    for feature in ("probability_delta", "selection_delta"):
        batch_means = {
            batch: _tensor_mean([row for row in rows if row["batch"] == batch], feature)
            for batch in ("b1", "b2")
        }
        result["direction_stability"][f"all_attack_{feature}"] = _direction_comparison(
            batch_means["b1"], batch_means["b2"], f"prefill-all-{feature}"
        )
        outcome_directions: dict[str, torch.Tensor] = {}
        for batch in ("b1", "b2"):
            drift = [row for row in rows if row["batch"] == batch and row["positive"]]
            resist = [row for row in rows if row["batch"] == batch and not row["positive"]]
            outcome_directions[batch] = _tensor_mean(drift, feature) - _tensor_mean(
                resist, feature
            )
        result["direction_stability"][f"drift_minus_resist_{feature}"] = (
            _direction_comparison(
                outcome_directions["b1"],
                outcome_directions["b2"],
                f"prefill-outcome-{feature}",
            )
        )
        by_channel: dict[str, Any] = {}
        for channel in sorted({row["channel"] for row in rows}):
            channel_directions: dict[str, torch.Tensor] = {}
            channel_counts: dict[str, dict[str, int]] = {}
            for batch in ("b1", "b2"):
                drift = [
                    row
                    for row in rows
                    if row["batch"] == batch
                    and row["channel"] == channel
                    and row["positive"]
                ]
                resist = [
                    row
                    for row in rows
                    if row["batch"] == batch
                    and row["channel"] == channel
                    and not row["positive"]
                ]
                channel_counts[batch] = {
                    "drift": len(drift),
                    "resist": len(resist),
                }
                if drift and resist:
                    channel_directions[batch] = _tensor_mean(
                        drift, feature
                    ) - _tensor_mean(resist, feature)
            if set(channel_directions) == {"b1", "b2"}:
                by_channel[channel] = {
                    "counts": channel_counts,
                    **_direction_comparison(
                        channel_directions["b1"],
                        channel_directions["b2"],
                        f"prefill-{channel}-outcome-{feature}",
                    ),
                }
        result["direction_stability"][
            f"by_channel_drift_minus_resist_{feature}"
        ] = by_channel
    result["scenario_rows"] = [_serializable_row(row) for row in rows]
    return result


def _boundary_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for batch in ("b1", "b2"):
        result[batch] = {}
        for width in WIDTHS:
            result[batch][str(width)] = {}
            for mapping in ("normalized", "exact"):
                selected = [
                    row
                    for row in rows
                    if row["batch"] == batch
                    and row["width"] == width
                    and row["mapping"] == mapping
                ]
                available = [row for row in selected if row["available"]]
                summary: dict[str, Any] = {
                    "drift_scenario_count": len(selected),
                    "matched_available_count": len(available),
                }
                for metric in ("probability_jsd", "selection_tv"):
                    for role in ("attack", "control", "contrast"):
                        values = [
                            float(row[f"{role}_{metric}"].mean().item())
                            for row in available
                        ]
                        summary[f"{role}_{metric}"] = _describe(
                            values, f"boundary-{batch}-{width}-{mapping}-{role}-{metric}"
                        )
                for metric in (
                    "entropy_delta",
                    "margin_delta",
                    "effective_experts_delta",
                ):
                    values = [
                        float(row[f"contrast_{metric}"].mean().item())
                        for row in available
                    ]
                    summary[f"contrast_{metric}"] = _describe(
                        values, f"boundary-{batch}-{width}-{mapping}-{metric}"
                    )
                if available:
                    summary["per_layer"] = [
                        {
                            "layer": layer,
                            "probability_jsd_contrast": _describe(
                                [
                                    float(row["contrast_probability_jsd"][layer].item())
                                    for row in available
                                ],
                                f"boundary-layer-jsd-{batch}-{width}-{mapping}-{layer}",
                            ),
                            "selection_tv_contrast": _describe(
                                [
                                    float(row["contrast_selection_tv"][layer].item())
                                    for row in available
                                ],
                                f"boundary-layer-tv-{batch}-{width}-{mapping}-{layer}",
                            ),
                            "entropy_delta_contrast": _describe(
                                [
                                    float(row["contrast_entropy_delta"][layer].item())
                                    for row in available
                                ],
                                f"boundary-layer-entropy-{batch}-{width}-{mapping}-{layer}",
                            ),
                        }
                        for layer in range(16)
                    ]
                summary["scenario_rows"] = [
                    _serializable_row(row) for row in selected
                ]
                result[batch][str(width)][mapping] = summary
    return result


def _event_summary(rows: Sequence[dict[str, Any]], label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for batch in ("b1", "b2"):
        selected = [row for row in rows if row["batch"] == batch]
        batch_result: dict[str, Any] = {
            "drift_scenario_count": len(selected),
            "attack_curve_available_count": sum(
                row["attack_curve_available"] for row in selected
            ),
            "matched_curve_available_count": sum(
                row["matched_curve_available"] for row in selected
            ),
            "offsets": {},
        }
        for offset in EVENT_OFFSETS:
            attack_values = [
                row["attack"][offset]
                for row in selected
                if offset in row["attack"]
            ]
            control_values = [
                row["control"][offset]
                for row in selected
                if offset in row["control"]
            ]
            contrast_values = [
                row["contrast"][offset]
                for row in selected
                if offset in row["contrast"]
            ]
            batch_result["offsets"][str(offset)] = {
                "attack": _describe(
                    attack_values, f"event-{label}-{batch}-{offset}-attack"
                ),
                "control": _describe(
                    control_values, f"event-{label}-{batch}-{offset}-control"
                ),
                "contrast": _describe(
                    contrast_values, f"event-{label}-{batch}-{offset}-contrast"
                ),
            }
        batch_result["scenario_rows"] = selected
        result[batch] = batch_result
    return result


def _decode_direction_summary(
    boundary_rows: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    selected = [
        row
        for row in boundary_rows
        if row["width"] == PRIMARY_WIDTH
        and row["mapping"] == "normalized"
        and row["available"]
    ]
    result: dict[str, Any] = {}
    for feature in ("probability_delta", "selection_delta"):
        means = {
            batch: _tensor_mean(
                [row for row in selected if row["batch"] == batch],
                f"contrast_{feature}",
            )
            for batch in ("b1", "b2")
        }
        result[feature] = _direction_comparison(
            means["b1"], means["b2"], f"decode-{feature}"
        )
        domains: dict[str, Any] = {}
        for domain in sorted({row["domain"] for row in selected}):
            by_batch = {
                batch: [
                    row
                    for row in selected
                    if row["batch"] == batch and row["domain"] == domain
                ]
                for batch in ("b1", "b2")
            }
            if all(by_batch.values()):
                left = _tensor_mean(by_batch["b1"], f"contrast_{feature}")
                right = _tensor_mean(by_batch["b2"], f"contrast_{feature}")
                domains[domain] = {
                    "b1_count": len(by_batch["b1"]),
                    "b2_count": len(by_batch["b2"]),
                    "cosine": _cosine(left, right),
                    "pearson": _pearson(left, right),
                }
        result[feature]["by_domain"] = domains
    return result


def _normal_variation_summary(
    rows: Sequence[dict[str, Any]], boundary_rows: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for batch in ("b1", "b2"):
        selected = [row for row in rows if row["batch"] == batch]
        boundaries = [
            row
            for row in boundary_rows
            if row["batch"] == batch
            and row["width"] == PRIMARY_WIDTH
            and row["mapping"] == "normalized"
            and row["available"]
        ]
        positive_values = [
            float(row["attack_probability_jsd"].mean().item()) for row in boundaries
        ]
        negative_maxima = [row["maximum_jsd"] for row in selected]
        result[batch] = {
            "non_drift_trace_count": len(selected),
            "per_trace_median_jsd": _describe(
                [row["median_jsd"] for row in selected],
                f"normal-{batch}-median",
            ),
            "per_trace_maximum_jsd": _describe(
                negative_maxima, f"normal-{batch}-maximum"
            ),
            "drift_boundary_probability_jsd": _describe(
                positive_values, f"normal-{batch}-drift-boundary"
            ),
            "drift_boundary_vs_non_drift_trace_max_auroc": _auroc(
                positive_values, negative_maxima
            ),
            "trace_rows": selected,
        }
    return result


def _inventory_summary(
    index_rows: Sequence[dict[str, Any]], trace_inventory: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "selected_trace_count": len(index_rows),
        "selected_scenario_count": len({row["pair_group_id"] for row in index_rows}),
        "routing_validation_pass_count": len(trace_inventory),
        "by_batch": {},
    }
    for batch in ("b1", "b2"):
        rows = [row for row in index_rows if row["observation_batch"] == batch]
        inventory = [row for row in trace_inventory if row["batch"] == batch]
        attacks = [row for row in rows if row["arm"] == "attack"]
        role_totals: Counter[str] = Counter()
        role_trace_counts: Counter[str] = Counter()
        for row in inventory:
            role_totals.update(row["prefill_role_counts"])
            role_trace_counts.update(row["prefill_role_counts"].keys())
        result["by_batch"][batch] = {
            "trace_count": len(rows),
            "scenario_count": len({row["pair_group_id"] for row in rows}),
            "arm_counts": dict(Counter(row["arm"] for row in rows)),
            "attack_outcome_counts": dict(
                Counter(row["behavior_label"] for row in attacks)
            ),
            "attack_channel_counts": dict(
                Counter(row["attack_channel"] for row in attacks)
            ),
            "drift_domain_counts": dict(
                Counter(
                    row["target_domain"]
                    for row in attacks
                    if row["behavior_label"] == "goal_drift"
                )
            ),
            "drift_channel_counts": dict(
                Counter(
                    row["attack_channel"]
                    for row in attacks
                    if row["behavior_label"] == "goal_drift"
                )
            ),
            "drift_workflow_counts": dict(
                Counter(
                    row["workflow"]
                    for row in attacks
                    if row["behavior_label"] == "goal_drift"
                )
            ),
            "drift_boundary": _describe(
                [
                    int(row["goal_plan_deviation_start_output_token"]["output_token_index"])
                    for row in attacks
                    if row["behavior_label"] == "goal_drift"
                ],
                f"inventory-{batch}-boundary",
            ),
            "prefill_token_count": _describe(
                [row["prefill_token_count"] for row in inventory],
                f"inventory-{batch}-prefill",
            ),
            "decode_token_count": _describe(
                [row["decode_token_count"] for row in inventory],
                f"inventory-{batch}-decode",
            ),
            "prefill_role_total_tokens": dict(role_totals),
            "prefill_role_trace_counts": dict(role_trace_counts),
        }
    return result


def _write_event_csv(path: Path, events: dict[str, dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "anchor",
                "batch",
                "offset",
                "group",
                "count",
                "mean",
                "median",
                "q25",
                "q75",
                "ci95_lower",
                "ci95_upper",
            ],
        )
        writer.writeheader()
        for anchor, event in events.items():
            for batch, batch_row in event.items():
                for offset, offset_row in batch_row["offsets"].items():
                    for group in ("attack", "control", "contrast"):
                        stats = offset_row[group]
                        ci = stats.get("bootstrap_mean_ci95", [None, None])
                        writer.writerow(
                            {
                                "anchor": anchor,
                                "batch": batch,
                                "offset": offset,
                                "group": group,
                                "count": stats.get("count"),
                                "mean": stats.get("mean"),
                                "median": stats.get("median"),
                                "q25": stats.get("q25"),
                                "q75": stats.get("q75"),
                                "ci95_lower": ci[0],
                                "ci95_upper": ci[1],
                            }
                        )


def _write_layer_csv(
    path: Path, boundaries: dict[str, dict[str, Any]]
) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "anchor",
            "batch",
            "layer",
            "metric",
            "count",
            "mean",
            "median",
            "ci95_lower",
            "ci95_upper",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for anchor, boundary in boundaries.items():
            for batch in ("b1", "b2"):
                layers = boundary[batch][str(PRIMARY_WIDTH)]["normalized"].get(
                    "per_layer", []
                )
                for row in layers:
                    for metric in (
                        "probability_jsd_contrast",
                        "selection_tv_contrast",
                        "entropy_delta_contrast",
                    ):
                        stats = row[metric]
                        ci = stats["bootstrap_mean_ci95"]
                        writer.writerow(
                            {
                                "anchor": anchor,
                                "batch": batch,
                                "layer": row["layer"],
                                "metric": metric,
                                "count": stats["count"],
                                "mean": stats["mean"],
                                "median": stats["median"],
                                "ci95_lower": ci[0],
                                "ci95_upper": ci[1],
                            }
                        )


def calculate(b1_dir: Path, b2_dir: Path) -> dict[str, Any]:
    b1_dir = b1_dir.resolve()
    b2_dir = b2_dir.resolve()
    index_rows = _load_index(b1_dir, "b1") + _load_index(b2_dir, "b2")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in index_rows:
        grouped[(row["observation_batch"], row["pair_group_id"])].append(row)
    if len(grouped) != 120:
        raise ValueError(f"expected 120 matched scenarios, got {len(grouped)}")

    prefill_rows: list[dict[str, Any]] = []
    boundary_rows: list[dict[str, Any]] = []
    onset_boundary_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    onset_event_rows: list[dict[str, Any]] = []
    annotation_rows: list[dict[str, Any]] = []
    normal_rows: list[dict[str, Any]] = []
    trace_inventory: list[dict[str, Any]] = []

    for group_index, ((batch, pair_group), rows) in enumerate(sorted(grouped.items()), 1):
        if len(rows) != 3 or {row["arm"] for row in rows} != {
            "clean",
            "benign_control",
            "attack",
        }:
            raise ValueError(f"{pair_group} is not a matched triplet")
        run_dir = b1_dir if batch == "b1" else b2_dir
        loaded = {
            row["arm"]: _load_trace(run_dir, row)
            for row in sorted(rows, key=lambda candidate: candidate["arm"])
        }
        attack = loaded["attack"]
        clean = loaded["clean"]
        benign = loaded["benign_control"]
        prefill_rows.append(_prefill_exposure(batch, attack, clean, benign))

        for trace in loaded.values():
            trace_inventory.append(
                {
                    "batch": batch,
                    "trace_id": trace.row["trace_id"],
                    "arm": trace.row["arm"],
                    "positive": trace.row["behavior_label"] == "goal_drift",
                    "prefill_token_count": trace.prefill_token_count,
                    "decode_token_count": len(trace.decode.token_ids),
                    "prefill_role_counts": trace.prefill_role_counts,
                }
            )
            if trace.row["behavior_label"] != "goal_drift":
                split_jsd = _all_split_jsd(trace.decode, PRIMARY_WIDTH)
                if split_jsd.numel():
                    normal_rows.append(
                        {
                            "batch": batch,
                            "trace_id": trace.row["trace_id"],
                            "arm": trace.row["arm"],
                            "eligible_split_count": int(split_jsd.numel()),
                            "median_jsd": float(split_jsd.median().item()),
                            "maximum_jsd": float(split_jsd.max().item()),
                        }
                    )

        if attack.row["behavior_label"] == "goal_drift":
            annotation_row = _boundary_annotation_row(batch, attack)
            annotation_rows.append(annotation_row)
            for width in WIDTHS:
                for mapping in ("normalized", "exact"):
                    boundary_rows.append(
                        _boundary_row(
                            batch, attack, clean, benign, width, mapping
                        )
                    )
                    onset_boundary_rows.append(
                        _boundary_row(
                            batch,
                            attack,
                            clean,
                            benign,
                            width,
                            mapping,
                            anchor_output_token=annotation_row[
                                "evidence_start_output_token"
                            ],
                            anchor_kind="evidence_onset",
                        )
                    )
            event_rows.append(_event_row(batch, attack, clean, benign))
            onset_event_rows.append(
                _event_row(
                    batch,
                    attack,
                    clean,
                    benign,
                    anchor_output_token=annotation_row["evidence_start_output_token"],
                    anchor_kind="evidence_onset",
                )
            )

        if group_index % 10 == 0 or group_index == len(grouped):
            print(
                f"processed {group_index}/{len(grouped)} matched scenarios",
                flush=True,
            )

    inventory = _inventory_summary(index_rows, trace_inventory)
    prefill = _prefill_summary(prefill_rows)
    boundary = _boundary_summary(boundary_rows)
    onset_boundary = _boundary_summary(onset_boundary_rows)
    event = _event_summary(event_rows, "evidence-completion")
    onset_event = _event_summary(onset_event_rows, "evidence-onset")
    annotation = _boundary_annotation_summary(annotation_rows)
    direction = _decode_direction_summary(boundary_rows)
    onset_direction = _decode_direction_summary(onset_boundary_rows)
    normal = _normal_variation_summary(normal_rows, boundary_rows)
    onset_normal = _normal_variation_summary(normal_rows, onset_boundary_rows)
    return {
        "schema_version": 1,
        "analysis_id": "agent-v2.5-routing-observation-atlas",
        "analysis_role": "post-hoc descriptive observation on B1/B2 development data",
        "plan": PLAN,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "data": {
            "b1_sample_index_sha256": B1_SHA256,
            "b2_sample_index_sha256": B2_SHA256,
        },
        "inventory": inventory,
        "prefill": prefill,
        "decode_boundary": boundary,
        "decode_evidence_onset": onset_boundary,
        "boundary_annotation_audit": annotation,
        "decode_event_time": event,
        "decode_event_time_evidence_onset": onset_event,
        "decode_direction_stability": direction,
        "decode_evidence_onset_direction_stability": onset_direction,
        "normal_variation": normal,
        "evidence_onset_normal_variation": onset_normal,
        "audit": {
            "new_classifier_fit": False,
            "b3_used": False,
            "labels_or_boundaries_modified": False,
            "token_as_independent_bootstrap_unit": False,
        },
    }


def main() -> None:
    args = _args()
    result = calculate(args.b1, args.b2)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "observation.json"
    event_path = args.output_dir / "event_time.csv"
    layer_path = args.output_dir / "layer_summary.csv"
    json_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _write_event_csv(
        event_path,
        {
            "evidence_completion": result["decode_event_time"],
            "evidence_onset": result["decode_event_time_evidence_onset"],
        },
    )
    _write_layer_csv(
        layer_path,
        {
            "evidence_completion": result["decode_boundary"],
            "evidence_onset": result["decode_evidence_onset"],
        },
    )
    print(
        json.dumps(
            {
                "observation": str(json_path),
                "event_time": str(event_path),
                "layer_summary": str(layer_path),
                "inventory": {
                    "traces": result["inventory"]["selected_trace_count"],
                    "scenarios": result["inventory"]["selected_scenario_count"],
                    "validated": result["inventory"]["routing_validation_pass_count"],
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
