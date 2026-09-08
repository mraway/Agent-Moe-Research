"""Shared causal window features for research-v2 scorers."""

from __future__ import annotations

from typing import Any, Sequence

import torch

from phase_a.normal_manifold import selection_window_signatures

TOP_K = 8
EXPERTS = 64
MIDDLE_LAYERS = tuple(range(5, 12))
MIDDLE_LATE_LAYERS = tuple(range(5, 16))
ALL_LAYERS = tuple(range(16))

# Router geometry of the frozen OLMoE pools (research v2 / v3).  Any trace that does not
# carry its own ``router`` metadata is assumed to be one of them, so every existing caller
# keeps the frozen behaviour.
OLMOE_ROUTER = {"num_moe_layers": 16, "num_experts": EXPERTS, "top_k": TOP_K}


def router_geometry(trace: Any) -> dict[str, int]:
    """``{num_moe_layers, num_experts, top_k}`` of one trace (research v4, additive).

    gpt-oss traces carry ``trace.json["router"]`` (24 MoE layers, 32 experts, top-4); the
    loader copies it onto the in-memory trace as ``trace.router``.  A trace without that
    attribute is an OLMoE trace of the frozen pools and gets the frozen 16 / 64 / 8
    geometry, so nothing on the existing paths changes.  The layer and top-k counts are
    cross-checked against ``top_k_ids`` when the tensor is present.
    """

    meta = getattr(trace, "router", None) or {}
    geometry = {
        "num_moe_layers": int(meta.get("num_moe_layers", OLMOE_ROUTER["num_moe_layers"])),
        "num_experts": int(meta.get("num_experts", OLMOE_ROUTER["num_experts"])),
        "top_k": int(meta.get("top_k", OLMOE_ROUTER["top_k"])),
    }
    ids = getattr(trace, "top_k_ids", None)
    if isinstance(ids, torch.Tensor) and ids.ndim == 3:
        if int(ids.shape[0]) != geometry["num_moe_layers"]:
            raise ValueError(
                f"router metadata says {geometry['num_moe_layers']} MoE layers but "
                f"top_k_ids has {int(ids.shape[0])}"
            )
        if int(ids.shape[2]) != geometry["top_k"]:
            raise ValueError(
                f"router metadata says top_k={geometry['top_k']} but top_k_ids has "
                f"{int(ids.shape[2])}"
            )
    return geometry


def selection_counts_per_token(
    top_k_ids: torch.Tensor, layers: Sequence[int], num_experts: int = EXPERTS
) -> torch.Tensor:
    """Per-token selection indicator ``[T, len(layers) * num_experts]`` (float32).

    Entry ``(t, l * num_experts + e)`` is 1 when expert ``e`` is in the top-k of layer
    ``l`` at token ``t``.  The causal window mean of this matrix is exactly the
    selection-rate window of :func:`selection_rate_windows`, which is why the two share
    this helper: the G views need window means that stop at a channel boundary, and those
    are computed from the per-token matrix rather than from a global cumulative sum.
    """

    if top_k_ids.ndim != 3:
        raise ValueError("top-k ids must have shape [layer, token, k]")
    index = list(layers)
    selected = top_k_ids[index, :, :].long()
    tokens = int(selected.shape[1])
    out = torch.zeros((len(index), tokens, int(num_experts)), dtype=torch.float32)
    out.scatter_add_(2, selected, torch.ones_like(selected, dtype=torch.float32))
    return out.permute(1, 0, 2).reshape(tokens, len(index) * int(num_experts)).contiguous()


def selection_rate_windows(
    top_k_ids: torch.Tensor,
    width: int,
    layers: Sequence[int],
    *,
    num_experts: int | None = None,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Causal top-k *selection rate* windows, flattened over (layer, expert).

    ``selection_window_signatures`` normalises by ``width * top_k`` (a per-layer
    distribution); multiplying by the router's ``top_k`` restores the selection rate in
    [0, 1], the scale used by the lead's one-class pilot.  Whitening makes the two scales
    equivalent up to the variance floor.

    ``num_experts`` defaults to the frozen OLMoE 64, in which case the frozen
    ``phase_a`` implementation is used unchanged; a router with a different expert count
    (gpt-oss: 32) takes the equivalent generic path.  ``top_k`` is read off the tensor,
    which is 8 on every OLMoE trace, so the frozen callers are unaffected.
    """

    experts = EXPERTS if num_experts is None else int(num_experts)
    top_k = int(top_k_ids.shape[2]) if top_k_ids.ndim == 3 else TOP_K
    if experts == EXPERTS:
        ends, signatures = selection_window_signatures(top_k_ids, width)
        if not ends.numel():
            return ends, torch.empty((0, len(list(layers)) * EXPERTS))
        selected = signatures[:, list(layers), :] * float(top_k)
        return ends, selected.reshape(signatures.shape[0], -1).contiguous()
    if width <= 0:
        raise ValueError("window width must be positive")
    tokens = int(top_k_ids.shape[1])
    if tokens < width:
        return torch.empty(0, dtype=torch.long), torch.empty((0, len(list(layers)) * experts))
    per_token = selection_counts_per_token(top_k_ids, layers, experts)
    return window_means(per_token, int(width))


def window_means(values: torch.Tensor, width: int) -> tuple[torch.Tensor, torch.Tensor]:
    """Causal window means of a [T, D] per-token feature."""

    if values.ndim != 2:
        raise ValueError("expected a [T, D] tensor")
    tokens = values.shape[0]
    if tokens < width:
        return torch.empty(0, dtype=torch.long), values.new_empty((0, values.shape[1]))
    prefix = torch.cat((values.new_zeros((1, values.shape[1])), values.cumsum(0)), dim=0)
    means = (prefix[width:] - prefix[:-width]) / float(width)
    ends = torch.arange(width - 1, tokens, dtype=torch.long)
    return ends, means
