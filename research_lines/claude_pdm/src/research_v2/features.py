"""Shared causal window features for research-v2 scorers."""

from __future__ import annotations

from typing import Sequence

import torch

from phase_a.normal_manifold import selection_window_signatures

TOP_K = 8
MIDDLE_LAYERS = tuple(range(5, 12))
MIDDLE_LATE_LAYERS = tuple(range(5, 16))
ALL_LAYERS = tuple(range(16))


def selection_rate_windows(
    top_k_ids: torch.Tensor, width: int, layers: Sequence[int]
) -> tuple[torch.Tensor, torch.Tensor]:
    """Causal top-8 *selection rate* windows, flattened over (layer, expert).

    ``selection_window_signatures`` normalises by ``width * top_k`` (a per-layer
    distribution); multiplying by ``TOP_K`` restores the selection rate in [0, 1],
    the scale used by the lead's one-class pilot.  Whitening makes the two scales
    equivalent up to the variance floor.
    """

    ends, signatures = selection_window_signatures(top_k_ids, width)
    if not ends.numel():
        return ends, torch.empty((0, len(layers) * 64))
    selected = signatures[:, list(layers), :] * float(TOP_K)
    return ends, selected.reshape(signatures.shape[0], -1).contiguous()


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
