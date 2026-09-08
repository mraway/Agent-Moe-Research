"""Frozen, route-only exploratory metrics for the Phase A trace set."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import torch
from safetensors.torch import load_file


LAYER_BANDS: dict[str, tuple[int, ...]] = {
    "early": tuple(range(0, 5)),
    "middle": tuple(range(5, 12)),
    "late": tuple(range(12, 16)),
}


@dataclass(frozen=True)
class RoutingSequence:
    """One token sequence and its aligned router observations."""

    token_ids: tuple[int, ...]
    token_texts: tuple[str, ...]
    probabilities: torch.Tensor
    top_k_ids: torch.Tensor

    def validate(self) -> None:
        if not self.token_ids:
            raise ValueError("routing sequence is empty")
        if len(self.token_texts) != len(self.token_ids):
            raise ValueError("token text and ID counts differ")
        if self.probabilities.ndim != 3:
            raise ValueError("probabilities must have shape [layer, token, expert]")
        if self.top_k_ids.ndim != 3:
            raise ValueError("top_k_ids must have shape [layer, token, k]")
        if self.probabilities.shape[:2] != self.top_k_ids.shape[:2]:
            raise ValueError("probability and top-k layer/token dimensions differ")
        if self.probabilities.shape[1] != len(self.token_ids):
            raise ValueError("router tensor is not token-aligned")


@dataclass(frozen=True)
class PhaseATrace:
    """Metadata and the two frozen sequence slices used by the analysis."""

    trace_dir: Path
    trace: dict[str, Any]
    decode: RoutingSequence
    tool_prefill: RoutingSequence
    validation: dict[str, Any]

    @property
    def trace_id(self) -> str:
        return str(self.trace["trace_id"])

    @property
    def pair_group_id(self) -> str:
        return str(self.trace["pair_group_id"])

    @property
    def arm(self) -> str:
        return str(self.trace["perturbation"]["arm"])

    @property
    def primary_positive(self) -> bool:
        return bool(self.trace["outcome"]["primary_positive"])


def jensen_shannon_divergence(p: torch.Tensor, q: torch.Tensor) -> torch.Tensor:
    """Return JSD along the final dimension, using natural logarithms."""

    if p.shape[-1] != q.shape[-1]:
        raise ValueError("JSD inputs have different event dimensions")
    p = p.float()
    q = q.float()
    midpoint = 0.5 * (p + q)
    tiny = torch.finfo(torch.float32).tiny
    p_term = p * (p.clamp_min(tiny).log() - midpoint.clamp_min(tiny).log())
    q_term = q * (q.clamp_min(tiny).log() - midpoint.clamp_min(tiny).log())
    return 0.5 * (p_term.sum(dim=-1) + q_term.sum(dim=-1))


def rolling_max(scores: torch.Tensor, width: int) -> dict[str, float | int]:
    """Return the maximum fixed-width rolling mean and its half-open span."""

    scores = scores.float().reshape(-1)
    if scores.numel() == 0:
        raise ValueError("cannot summarize an empty score sequence")
    if width <= 0:
        raise ValueError("window width must be positive")
    effective_width = min(width, scores.numel())
    means = scores.unfold(0, effective_width, 1).mean(dim=-1)
    start = int(means.argmax().item())
    return {
        "value": float(means[start].item()),
        "start": start,
        "end": start + effective_width,
        "width": effective_width,
    }


def contiguous_rolling_max(
    scores: torch.Tensor,
    eligible: torch.Tensor,
    width: int,
) -> dict[str, float | int] | None:
    """Return a rolling maximum over windows whose every token is eligible."""

    scores = scores.float().reshape(-1)
    eligible = eligible.bool().reshape(-1)
    if scores.shape != eligible.shape:
        raise ValueError("scores and eligibility mask have different shapes")
    if width <= 0:
        raise ValueError("window width must be positive")
    if scores.numel() < width:
        return None
    window_scores = scores.unfold(0, width, 1).mean(dim=-1)
    window_eligible = eligible.unfold(0, width, 1).all(dim=-1)
    if not bool(window_eligible.any()):
        return None
    eligible_indices = window_eligible.nonzero(as_tuple=False).reshape(-1)
    local_index = int(window_scores[eligible_indices].argmax().item())
    start = int(eligible_indices[local_index].item())
    return {
        "value": float(window_scores[start].item()),
        "start": start,
        "end": start + width,
        "width": width,
    }


def mean_probability_profile(sequences: Sequence[RoutingSequence]) -> torch.Tensor:
    """Pool token probabilities from one or more normal sequences."""

    if not sequences:
        raise ValueError("normal profile needs at least one sequence")
    probabilities = torch.cat([sequence.probabilities for sequence in sequences], dim=1)
    return probabilities.mean(dim=1)


def score_sequence(
    sequence: RoutingSequence,
    profile: torch.Tensor,
    *,
    window_width: int = 8,
    layer_bands: dict[str, tuple[int, ...]] = LAYER_BANDS,
) -> dict[str, Any]:
    """Score one sequence against a per-layer normal routing profile."""

    sequence.validate()
    if profile.shape != (
        sequence.probabilities.shape[0],
        sequence.probabilities.shape[2],
    ):
        raise ValueError("normal profile shape does not match sequence routers")
    layer_token_jsd = jensen_shannon_divergence(
        sequence.probabilities,
        profile[:, None, :],
    )
    token_jsd = layer_token_jsd.mean(dim=0)
    window = rolling_max(token_jsd, window_width)
    start, end = int(window["start"]), int(window["end"])

    profile_top_k = profile.topk(sequence.top_k_ids.shape[-1], dim=-1).indices
    overlap = (
        sequence.top_k_ids[..., None] == profile_top_k[:, None, None, :]
    ).any(dim=-1).float().sum(dim=-1)
    layer_token_novelty = 1.0 - overlap / sequence.top_k_ids.shape[-1]
    token_novelty = layer_token_novelty.mean(dim=0)

    band_scores: dict[str, Any] = {}
    for name, indices in layer_bands.items():
        index = torch.tensor(indices, dtype=torch.long)
        band_token_scores = layer_token_jsd.index_select(0, index).mean(dim=0)
        band_scores[name] = {
            "mean_jsd": float(band_token_scores.mean().item()),
            "max_window_jsd_w8": rolling_max(band_token_scores, window_width),
        }

    return {
        "token_jsd": token_jsd,
        "layer_token_jsd": layer_token_jsd,
        "token_top8_novelty": token_novelty,
        "mean_jsd": float(token_jsd.mean().item()),
        "max_window_jsd_w8": window,
        "peak_window_text": "".join(sequence.token_texts[start:end]),
        "peak_window_layer_jsd": [
            float(value)
            for value in layer_token_jsd[:, start:end].mean(dim=1).tolist()
        ],
        "mean_top8_novelty": float(token_novelty.mean().item()),
        "max_window_top8_novelty_w8": rolling_max(token_novelty, window_width),
        "layer_bands": band_scores,
    }


def matched_prefix_diagnostics(
    left: RoutingSequence,
    right: RoutingSequence,
) -> dict[str, Any]:
    """Compare routes while token, position, and generated prefix still match."""

    left.validate()
    right.validate()
    prefix_length = 0
    for left_id, right_id in zip(left.token_ids, right.token_ids, strict=False):
        if left_id != right_id:
            break
        prefix_length += 1
    if prefix_length == 0:
        raise ValueError("sequences have no shared token prefix")
    layer_token_jsd = jensen_shannon_divergence(
        left.probabilities[:, :prefix_length, :],
        right.probabilities[:, :prefix_length, :],
    )
    token_jsd = layer_token_jsd.mean(dim=0)
    maximum_index = int(token_jsd.argmax().item())
    return {
        "shared_prefix_tokens": prefix_length,
        "shared_prefix_text": "".join(left.token_texts[:prefix_length]),
        "mean_jsd": float(layer_token_jsd.mean().item()),
        "max_token_jsd": float(token_jsd[maximum_index].item()),
        "max_token_index": maximum_index,
        "max_token_text": left.token_texts[maximum_index],
        "per_layer_mean_jsd": [
            float(value) for value in layer_token_jsd.mean(dim=1).tolist()
        ],
        "per_token_mean_jsd": [float(value) for value in token_jsd.tolist()],
    }


def find_unique_text_token_indices(
    sequence: RoutingSequence,
    text: str,
) -> tuple[int, ...]:
    """Locate the tokens that overlap one unique substring occurrence."""

    if not text:
        raise ValueError("text span cannot be empty")
    rendered = "".join(sequence.token_texts)
    start = rendered.find(text)
    if start < 0:
        raise ValueError(f"text is absent from routing sequence: {text!r}")
    if rendered.find(text, start + 1) >= 0:
        raise ValueError(f"text occurs more than once in routing sequence: {text!r}")
    end = start + len(text)
    indices: list[int] = []
    token_start = 0
    for token_index, token_text in enumerate(sequence.token_texts):
        token_end = token_start + len(token_text)
        if token_start < end and token_end > start:
            indices.append(token_index)
        token_start = token_end
    if not indices:
        raise ValueError("text span did not overlap any token")
    return tuple(indices)


def subset_routing_sequence(
    sequence: RoutingSequence,
    indices: Sequence[int],
) -> RoutingSequence:
    """Select an ordered token subset from a routing sequence."""

    if not indices:
        raise ValueError("routing subset cannot be empty")
    if any(index < 0 or index >= len(sequence.token_ids) for index in indices):
        raise ValueError("routing subset contains an out-of-range token index")
    index = torch.tensor(indices, dtype=torch.long)
    subset = RoutingSequence(
        token_ids=tuple(sequence.token_ids[position] for position in indices),
        token_texts=tuple(sequence.token_texts[position] for position in indices),
        probabilities=sequence.probabilities.index_select(1, index),
        top_k_ids=sequence.top_k_ids.index_select(1, index),
    )
    subset.validate()
    return subset


def concatenate_routing_sequences(
    sequences: Sequence[RoutingSequence],
) -> RoutingSequence:
    """Concatenate compatible routing sequences along the token dimension."""

    if not sequences:
        raise ValueError("routing concatenation needs at least one sequence")
    for sequence in sequences:
        sequence.validate()
    reference_shape = (
        sequences[0].probabilities.shape[0],
        sequences[0].probabilities.shape[2],
        sequences[0].top_k_ids.shape[2],
    )
    for sequence in sequences[1:]:
        shape = (
            sequence.probabilities.shape[0],
            sequence.probabilities.shape[2],
            sequence.top_k_ids.shape[2],
        )
        if shape != reference_shape:
            raise ValueError("routing sequences have incompatible router dimensions")
    combined = RoutingSequence(
        token_ids=tuple(token for sequence in sequences for token in sequence.token_ids),
        token_texts=tuple(text for sequence in sequences for text in sequence.token_texts),
        probabilities=torch.cat(
            [sequence.probabilities for sequence in sequences], dim=1
        ),
        top_k_ids=torch.cat([sequence.top_k_ids for sequence in sequences], dim=1),
    )
    combined.validate()
    return combined


def routing_profile(sequence: RoutingSequence) -> dict[str, torch.Tensor]:
    """Build per-layer probability and top-k selection profiles."""

    sequence.validate()
    layer_count, token_count, expert_count = sequence.probabilities.shape
    mean_probability = sequence.probabilities.mean(dim=1)
    selection_rate = torch.zeros(layer_count, expert_count, dtype=torch.float32)
    selection_rate.scatter_add_(
        1,
        sequence.top_k_ids.reshape(layer_count, -1),
        torch.ones_like(sequence.top_k_ids, dtype=torch.float32).reshape(layer_count, -1),
    )
    selection_rate /= token_count
    return {
        "mean_probability": mean_probability,
        "selection_rate": selection_rate,
    }


def compare_routing_profiles(
    left: RoutingSequence,
    right: RoutingSequence,
) -> dict[str, Any]:
    """Compare aggregate expert use between two token segments."""

    left_profile = routing_profile(left)
    right_profile = routing_profile(right)
    left_probability = left_profile["mean_probability"]
    right_probability = right_profile["mean_probability"]
    if left_probability.shape != right_probability.shape:
        raise ValueError("routing profiles have different shapes")
    layer_count, expert_count = left_probability.shape
    top_k = left.top_k_ids.shape[-1]
    layer_jsd = jensen_shannon_divergence(left_probability, right_probability)

    left_probability_top = left_probability.topk(top_k, dim=-1).indices
    right_probability_top = right_probability.topk(top_k, dim=-1).indices
    probability_overlap = (
        left_probability_top[:, :, None] == right_probability_top[:, None, :]
    ).any(dim=-1).sum(dim=-1)

    left_selection = left_profile["selection_rate"]
    right_selection = right_profile["selection_rate"]
    left_selection_top = left_selection.topk(top_k, dim=-1).indices
    right_selection_top = right_selection.topk(top_k, dim=-1).indices
    selection_overlap = (
        left_selection_top[:, :, None] == right_selection_top[:, None, :]
    ).any(dim=-1).sum(dim=-1)

    probability_changes: list[dict[str, Any]] = []
    selection_changes: list[dict[str, Any]] = []
    for layer in range(layer_count):
        for expert in range(expert_count):
            probability_delta = float(
                (left_probability[layer, expert] - right_probability[layer, expert]).item()
            )
            probability_changes.append(
                {
                    "layer": layer,
                    "expert": expert,
                    "left": float(left_probability[layer, expert].item()),
                    "right": float(right_probability[layer, expert].item()),
                    "delta": probability_delta,
                    "absolute_delta": abs(probability_delta),
                }
            )
            selection_delta = float(
                (left_selection[layer, expert] - right_selection[layer, expert]).item()
            )
            selection_changes.append(
                {
                    "layer": layer,
                    "expert": expert,
                    "left": float(left_selection[layer, expert].item()),
                    "right": float(right_selection[layer, expert].item()),
                    "delta": selection_delta,
                    "absolute_delta": abs(selection_delta),
                }
            )
    probability_changes.sort(key=lambda row: row["absolute_delta"], reverse=True)
    selection_changes.sort(key=lambda row: row["absolute_delta"], reverse=True)

    return {
        "left_token_count": len(left.token_ids),
        "right_token_count": len(right.token_ids),
        "mean_layer_centroid_jsd": float(layer_jsd.mean().item()),
        "max_layer_centroid_jsd": float(layer_jsd.max().item()),
        "max_jsd_layer": int(layer_jsd.argmax().item()),
        "per_layer_centroid_jsd": [float(value) for value in layer_jsd.tolist()],
        "mean_probability_top8_overlap": float(probability_overlap.float().mean().item()),
        "per_layer_probability_top8_overlap": [
            int(value) for value in probability_overlap.tolist()
        ],
        "mean_frequent_top8_overlap": float(selection_overlap.float().mean().item()),
        "per_layer_frequent_top8_overlap": [
            int(value) for value in selection_overlap.tolist()
        ],
        "left_frequent_top8": left_selection_top.tolist(),
        "right_frequent_top8": right_selection_top.tolist(),
        "largest_probability_changes": probability_changes[:32],
        "largest_selection_rate_changes": selection_changes[:32],
    }


def compare_aligned_token_routes(
    left: RoutingSequence,
    right: RoutingSequence,
) -> dict[str, Any]:
    """Compare an identical token sequence until its first token-ID mismatch."""

    left.validate()
    right.validate()
    aligned = 0
    for left_id, right_id in zip(left.token_ids, right.token_ids, strict=False):
        if left_id != right_id:
            break
        aligned += 1
    if aligned == 0:
        raise ValueError("routing sequences do not begin with the same token")
    layer_token_jsd = jensen_shannon_divergence(
        left.probabilities[:, :aligned, :],
        right.probabilities[:, :aligned, :],
    )
    actual_top_k_overlap = (
        left.top_k_ids[:, :aligned, :, None]
        == right.top_k_ids[:, :aligned, None, :]
    ).any(dim=-1).sum(dim=-1).float()
    return {
        "aligned_tokens": aligned,
        "left_total_tokens": len(left.token_ids),
        "right_total_tokens": len(right.token_ids),
        "mean_token_layer_jsd": float(layer_token_jsd.mean().item()),
        "per_layer_mean_jsd": [
            float(value) for value in layer_token_jsd.mean(dim=1).tolist()
        ],
        "mean_actual_top8_overlap": float(actual_top_k_overlap.mean().item()),
        "per_layer_actual_top8_overlap": [
            float(value) for value in actual_top_k_overlap.mean(dim=1).tolist()
        ],
    }


def build_token_conditioned_references(
    traces: Iterable[PhaseATrace],
) -> dict[str, dict[int, list[torch.Tensor]]]:
    """Index every clean decode observation by source trace and token ID."""

    references: dict[str, dict[int, list[torch.Tensor]]] = {}
    for trace in traces:
        if trace.arm != "clean":
            continue
        trace_references: dict[int, list[torch.Tensor]] = {}
        for token_index, token_id in enumerate(trace.decode.token_ids):
            trace_references.setdefault(token_id, []).append(
                trace.decode.probabilities[:, token_index, :]
            )
        references[trace.trace_id] = trace_references
    if not references:
        raise ValueError("same-token reference index has no clean traces")
    return references


def score_same_token(
    trace: PhaseATrace,
    references: dict[str, dict[int, list[torch.Tensor]]],
    *,
    window_width: int = 8,
) -> dict[str, Any]:
    """Compare each token only with clean occurrences of the same token ID."""

    token_count = len(trace.decode.token_ids)
    scores = torch.full((token_count,), float("nan"), dtype=torch.float32)
    reference_counts = [0] * token_count
    for token_index, token_id in enumerate(trace.decode.token_ids):
        observations: list[torch.Tensor] = []
        for source_trace_id, per_token in references.items():
            if trace.arm == "clean" and source_trace_id == trace.trace_id:
                continue
            observations.extend(per_token.get(token_id, ()))
        if not observations:
            continue
        reference = torch.stack(observations, dim=0).mean(dim=0)
        candidate = trace.decode.probabilities[:, token_index, :]
        scores[token_index] = jensen_shannon_divergence(candidate, reference).mean()
        reference_counts[token_index] = len(observations)

    eligible = scores.isfinite()
    comparable = int(eligible.sum().item())
    finite_scores = scores[eligible]
    return {
        "token_jsd": scores,
        "reference_counts": reference_counts,
        "comparable_tokens": comparable,
        "total_tokens": token_count,
        "coverage": comparable / token_count,
        "mean_jsd": float(finite_scores.mean().item()) if comparable else None,
        "max_contiguous_window_jsd_w8": contiguous_rolling_max(
            scores.nan_to_num(), eligible, window_width
        ),
    }


def load_sequence(
    trace_dir: Path,
    rows: Sequence[dict[str, Any]],
    token_masks: Sequence[Sequence[bool]],
) -> RoutingSequence:
    """Load selected tokens from one or more manifest rows."""

    if len(rows) != len(token_masks):
        raise ValueError("manifest rows and masks have different lengths")
    token_ids: list[int] = []
    token_texts: list[str] = []
    probability_parts: list[torch.Tensor] = []
    top_k_parts: list[torch.Tensor] = []
    for row, raw_mask in zip(rows, token_masks, strict=True):
        mask = torch.tensor(raw_mask, dtype=torch.bool)
        if mask.numel() != len(row["token_ids"]):
            raise ValueError("selection mask is not token-aligned")
        if not bool(mask.any()):
            continue
        shard_path = (trace_dir / row["tensor_file"]).resolve()
        if trace_dir.resolve() not in shard_path.parents:
            raise ValueError("tensor path escapes trace directory")
        tensors = load_file(shard_path)
        logits = tensors["router_logits"].float()[:, mask, :]
        probability_parts.append(torch.softmax(logits, dim=-1))
        top_k_parts.append(tensors["top_k_ids"].long()[:, mask, :])
        selected = mask.tolist()
        token_ids.extend(
            token_id for token_id, keep in zip(row["token_ids"], selected, strict=True) if keep
        )
        token_texts.extend(
            text for text, keep in zip(row["token_texts"], selected, strict=True) if keep
        )
    if not probability_parts:
        raise ValueError("manifest selection produced no tokens")
    sequence = RoutingSequence(
        token_ids=tuple(token_ids),
        token_texts=tuple(token_texts),
        probabilities=torch.cat(probability_parts, dim=1),
        top_k_ids=torch.cat(top_k_parts, dim=1),
    )
    sequence.validate()
    return sequence


def load_decode_sequence(trace_dir: Path, agent_step: int) -> RoutingSequence:
    """Load every decode token belonging to one model-generation step."""

    trace_dir = trace_dir.resolve()
    rows = [
        json.loads(line)
        for line in (trace_dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    decode_rows: list[dict[str, Any]] = []
    decode_masks: list[list[bool]] = []
    for row in rows:
        if row["phase"] != "decode":
            continue
        mask = [step == agent_step for step in row["agent_steps"]]
        if any(mask):
            decode_rows.append(row)
            decode_masks.append(mask)
    if not decode_rows:
        raise ValueError(f"{trace_dir} has no decode tokens for agent step {agent_step}")
    return load_sequence(trace_dir, decode_rows, decode_masks)


def load_final_generation_sequence(trace_dir: Path) -> RoutingSequence:
    """Load the decode route for the final model-generation event in a trace."""

    trace_dir = trace_dir.resolve()
    trace = json.loads((trace_dir / "trace.json").read_text(encoding="utf-8"))
    generations = [
        event for event in trace["events"] if event["kind"] == "model_generation"
    ]
    if not generations:
        raise ValueError(f"{trace_dir} has no model-generation events")
    return load_decode_sequence(trace_dir, int(generations[-1]["agent_step"]))


def load_phase_a_trace(trace_dir: Path, validation: dict[str, Any]) -> PhaseATrace:
    """Load exactly the two slices frozen in the exploration plan."""

    trace_dir = trace_dir.resolve()
    trace = json.loads((trace_dir / "trace.json").read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (trace_dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    decode_rows: list[dict[str, Any]] = []
    decode_masks: list[list[bool]] = []
    prefill_rows: list[dict[str, Any]] = []
    prefill_masks: list[list[bool]] = []
    for row in rows:
        if row["phase"] == "decode":
            mask = [step == 1 for step in row["agent_steps"]]
            if any(mask):
                decode_rows.append(row)
                decode_masks.append(mask)
        elif row["phase"] == "prefill":
            mask = [role == "tool" for role in row["token_roles"]]
            if any(mask):
                prefill_rows.append(row)
                prefill_masks.append(mask)
    if len(prefill_rows) != 1:
        raise ValueError(
            f"{trace_dir} has {len(prefill_rows)} prefill rows containing tool tokens, expected 1"
        )
    return PhaseATrace(
        trace_dir=trace_dir,
        trace=trace,
        decode=load_sequence(trace_dir, decode_rows, decode_masks),
        tool_prefill=load_sequence(trace_dir, prefill_rows, prefill_masks),
        validation=validation,
    )


def boundary_diagnostics(
    trace: PhaseATrace,
    token_scores: torch.Tensor,
    peak_window: dict[str, Any],
    *,
    radius: int = 8,
) -> dict[str, Any] | None:
    """Summarize scores around a saved goal-deviation boundary."""

    boundary = trace.trace["outcome"].get("goal_plan_deviation_start_output_token")
    if boundary is None or int(boundary.get("agent_step", -1)) != 1:
        return None
    index = int(boundary["output_token_index"])
    if not 0 <= index < token_scores.numel():
        raise ValueError(f"{trace.trace_id} deviation boundary is outside decode sequence")
    center_start = max(0, index - radius)
    center_end = min(token_scores.numel(), index + radius + 1)
    peak_start = int(peak_window["start"])
    peak_end = int(peak_window["end"])
    neighborhood_start = max(0, index - radius)
    neighborhood_end = min(token_scores.numel(), index + radius + 1)
    return {
        "output_token_index": index,
        "boundary_token_text": trace.decode.token_texts[index],
        "pre_boundary_mean_jsd": (
            float(token_scores[:index].mean().item()) if index > 0 else None
        ),
        "post_boundary_mean_jsd": float(token_scores[index:].mean().item()),
        "center_plus_minus_8_mean_jsd": float(
            token_scores[center_start:center_end].mean().item()
        ),
        "peak_window_intersects_boundary_plus_minus_8": (
            peak_start < neighborhood_end and peak_end > neighborhood_start
        ),
        "peak_window_start_minus_boundary": peak_start - index,
        "peak_window_end_minus_boundary": peak_end - index,
    }
