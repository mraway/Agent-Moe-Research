"""Causal fixed-window features and alarm utilities for routing streams."""

from __future__ import annotations

import torch

from .routing_analysis import RoutingSequence, jensen_shannon_divergence


def window_end_indices(token_count: int, width: int) -> torch.Tensor:
    """Return inclusive token indices for every complete causal window."""

    if token_count <= 0:
        raise ValueError("token count must be positive")
    if width <= 0:
        raise ValueError("window width must be positive")
    if token_count < width:
        return torch.empty(0, dtype=torch.long)
    return torch.arange(width - 1, token_count, dtype=torch.long)


def _window_means(values: torch.Tensor, width: int) -> torch.Tensor:
    """Take window means along dimension one of [channel, token, value]."""

    if values.ndim != 3:
        raise ValueError("window values must have shape [channel, token, value]")
    if width <= 0:
        raise ValueError("window width must be positive")
    if values.shape[1] < width:
        return values.new_empty((0, values.shape[0] * values.shape[2]))
    prefix = torch.cat(
        (values.new_zeros((values.shape[0], 1, values.shape[2])), values.cumsum(1)),
        dim=1,
    )
    means = (prefix[:, width:, :] - prefix[:, :-width, :]) / width
    return means.permute(1, 0, 2).reshape(means.shape[1], -1)


def window_features(
    sequence: RoutingSequence,
    family: str,
    width: int,
    *,
    token_hash_dimension: int = 2048,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Extract one feature row for every causal window ending in a sequence."""

    sequence.validate()
    ends = window_end_indices(len(sequence.token_ids), width)
    if family == "route_probability":
        return ends, _window_means(sequence.probabilities.float(), width).float()
    if family == "route_selection":
        layer_count, token_count, expert_count = sequence.probabilities.shape
        selected = torch.zeros(
            layer_count, token_count, expert_count, dtype=torch.float32
        )
        selected.scatter_add_(
            2,
            sequence.top_k_ids,
            torch.ones_like(sequence.top_k_ids, dtype=torch.float32),
        )
        return ends, _window_means(selected, width).float()
    if family == "token_hash":
        if token_hash_dimension <= 0:
            raise ValueError("token hash dimension must be positive")
        per_token = torch.zeros(
            len(sequence.token_ids), token_hash_dimension, dtype=torch.float32
        )
        positions = torch.arange(len(sequence.token_ids), dtype=torch.long)
        mixed = torch.tensor(
            [
                (int(token_id) * 0x9E3779B1) & 0xFFFFFFFF
                for token_id in sequence.token_ids
            ],
            dtype=torch.int64,
        )
        buckets = mixed.remainder(token_hash_dimension)
        signs = torch.where(
            mixed.bitwise_and(0x80000000) != 0,
            torch.tensor(1.0),
            torch.tensor(-1.0),
        )
        per_token[positions, buckets] = signs
        if not ends.numel():
            return ends, per_token.new_empty((0, token_hash_dimension))
        prefix = torch.cat(
            (per_token.new_zeros((1, token_hash_dimension)), per_token.cumsum(0)),
            dim=0,
        )
        features = prefix[width:] - prefix[:-width]
        norms = features.norm(dim=1, keepdim=True).clamp_min(
            torch.finfo(features.dtype).eps
        )
        return ends, features / norms
    raise ValueError(f"unknown sequential feature family: {family}")


def adjacent_block_selection_jsd(
    sequence: RoutingSequence, width: int
) -> tuple[torch.Tensor, torch.Tensor]:
    """Compare adjacent top-k expert-selection blocks at causal endpoints.

    For endpoint ``t``, the previous block ends at ``t - width`` and the
    current block ends at ``t``.  The score is the mean per-layer JSD between
    the two categorical expert-selection distributions.
    """

    sequence.validate()
    if width <= 0:
        raise ValueError("block width must be positive")
    layer_count, token_count, expert_count = sequence.probabilities.shape
    if token_count < 2 * width:
        return (
            torch.empty(0, dtype=torch.long),
            torch.empty(0, dtype=torch.float32),
        )

    selected = torch.zeros(
        layer_count, token_count, expert_count, dtype=torch.float32
    )
    selected.scatter_add_(
        2,
        sequence.top_k_ids,
        torch.ones_like(sequence.top_k_ids, dtype=torch.float32),
    )
    prefix = torch.cat(
        (selected.new_zeros((layer_count, 1, expert_count)), selected.cumsum(1)),
        dim=1,
    )
    count = token_count - 2 * width + 1
    previous = prefix[:, width : width + count] - prefix[:, :count]
    current = (
        prefix[:, 2 * width : 2 * width + count]
        - prefix[:, width : width + count]
    )
    normalizer = float(width * sequence.top_k_ids.shape[2])
    layer_scores = jensen_shannon_divergence(
        previous / normalizer, current / normalizer
    )
    ends = torch.arange(2 * width - 1, token_count, dtype=torch.long)
    return ends, layer_scores.mean(dim=0).float()


def finite_sample_upper_threshold(values: torch.Tensor, alpha: float) -> float:
    """Return an upper-tail finite-sample order statistic.

    Callers alarm strictly above the returned value. ``alpha`` is the desired
    upper-tail probability.
    """

    values = values.reshape(-1).to(torch.float64)
    if not values.numel():
        raise ValueError("threshold calibration needs at least one value")
    if not 0.0 < alpha < 1.0:
        raise ValueError("alpha must lie strictly between zero and one")
    rank = min(
        int(torch.ceil(torch.tensor((values.numel() + 1) * (1.0 - alpha))).item()),
        values.numel(),
    )
    return float(values.sort().values[rank - 1].item())


def causal_history_median_difference(
    scores: torch.Tensor, ends: torch.Tensor, gap: int
) -> tuple[torch.Tensor, torch.Tensor]:
    """Subtract the median of every causally eligible historical score.

    For the score ending at ``t``, history contains endpoints at most
    ``t - gap``.  A positive gap can therefore keep fixed-width current and
    historical windows disjoint.
    """

    scores = scores.reshape(-1).to(torch.float64)
    ends = ends.reshape(-1).long()
    if scores.numel() != ends.numel():
        raise ValueError("scores and window ends are not aligned")
    if gap <= 0:
        raise ValueError("history gap must be positive")
    if ends.numel() > 1 and not bool((ends[1:] > ends[:-1]).all()):
        raise ValueError("window ends must be strictly increasing")

    relative: list[torch.Tensor] = []
    relative_ends: list[int] = []
    for index, end in enumerate(ends.tolist()):
        history = scores[ends <= end - gap]
        if not history.numel():
            continue
        relative.append(scores[index] - torch.quantile(history, 0.5))
        relative_ends.append(end)
    if not relative:
        return scores.new_empty(0), ends.new_empty(0)
    return torch.stack(relative), torch.tensor(relative_ends, dtype=torch.long)


def persistent_scores(
    scores: torch.Tensor, ends: torch.Tensor, persistence: int
) -> tuple[torch.Tensor, torch.Tensor]:
    """Return the minimum score across each consecutive persistence run."""

    scores = scores.reshape(-1)
    ends = ends.reshape(-1).long()
    if scores.numel() != ends.numel():
        raise ValueError("scores and window ends are not aligned")
    if persistence <= 0:
        raise ValueError("persistence must be positive")
    if scores.numel() < persistence:
        return scores.new_empty(0), ends.new_empty(0)
    values = scores.unfold(0, persistence, 1).amin(dim=1)
    return values, ends[persistence - 1 :]


def evenly_spaced_indices(start: int, end: int, count: int) -> tuple[int, ...]:
    """Choose up to count deterministic inclusive indices from an interval."""

    if count <= 0:
        raise ValueError("count must be positive")
    if start > end:
        return ()
    if start == end:
        return (start,)
    values = torch.linspace(float(start), float(end), steps=count).round().long()
    return tuple(dict.fromkeys(int(value.item()) for value in values))
