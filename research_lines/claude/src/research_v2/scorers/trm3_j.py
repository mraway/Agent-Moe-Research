"""TRM-3 channel J -- cross-layer coupling of adjacent-layer top-1 experts (prereg section 2).

Fitted on routine traces only; inference reads nothing but ``trace.top_k_ids``.

Let ``a_l(t) = top_k_ids[l, t, 0]`` be the per-layer top-1 expert.  For every adjacent
pair ``(l, l+1)`` with ``l in 5..10`` (frozen: pairs 5-6 ... 10-11) the routine model is a
0.5-smoothed count table::

    P_pair[l](a, b) = (#tokens with a_l = a and a_{l+1} = b + 0.5) / (N_tok + 0.5 * 4096)
    P_marg[l](a)    = (#tokens with a_l = a + 0.5)                 / (N_tok + 0.5 * 64)

and the per-token statistic is the marginal-corrected (pointwise-mutual-information style)
negative log coupling::

    j_t = sum_{l=5}^{10} [ -log P_pair[l](a_l, a_{l+1})
                           + log P_marg[l](a_l) + log P_marg[l+1](a_{l+1}) ]

so a pair that is rare *only because its two ends are individually rare* contributes ~0,
while a recombination of individually common experts into a routine-rare chain scores high.
The window score is the causal mean of ``j_t`` over ``w = 4`` tokens
(``features.window_means``; endpoint = decode index of the window's last token).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import torch

from research_v2.features import window_means
from research_v2.scorers import register
from research_v2.scorers.trm3_s import (
    EXPERTS,
    LAYERS,
    SMOOTHING,
    causal_window_bounds,
    check_top_k_ids,
    empty_stream,
)

# Frozen constants (docs/research_v3/trm3_prereg.md sections 2 and 5).
PAIR_LAYERS: tuple[int, ...] = (5, 6, 7, 8, 9, 10)  # first layer of each adjacent pair
WINDOW_WIDTH = 4


@dataclass
class TRM3JState:
    layers: tuple[int, ...]
    log_pair: torch.Tensor  # [len(layers), 64, 64]
    log_marg: torch.Tensor  # [16, 64]
    pair_counts: torch.Tensor  # [len(layers), 64, 64] raw counts
    marg_counts: torch.Tensor  # [16, 64] raw top-1 counts
    n_tokens: int
    n_traces: int
    smoothing: float

    def describe(self) -> dict[str, Any]:
        unseen_pairs = (self.pair_counts == 0).sum(dim=(1, 2))
        return {
            "channel": "trm3_j",
            "layers": list(self.layers),
            "pairs": [[int(l), int(l) + 1] for l in self.layers],
            "n_routine_tokens": int(self.n_tokens),
            "n_routine_traces": int(self.n_traces),
            "smoothing": float(self.smoothing),
            "pair_cells_per_layer": EXPERTS * EXPERTS,
            "unseen_pairs_per_layer": [int(v) for v in unseen_pairs.tolist()],
            "unseen_top1_experts_per_layer": [int(v) for v in (self.marg_counts == 0).sum(1).tolist()],
        }

    def to_json(self) -> dict[str, Any]:
        return self.describe()


class TRM3JScorer:
    """Cross-layer coupling channel J of TRM-3; fitted on routine traces only."""

    name = "trm3_j"
    requires_positives = False

    def __init__(
        self,
        window_width: int = WINDOW_WIDTH,
        layers: Sequence[int] = PAIR_LAYERS,
        smoothing: float = SMOOTHING,
    ) -> None:
        if int(window_width) <= 0:
            raise ValueError("window_width must be positive")
        if float(smoothing) <= 0.0:
            raise ValueError("smoothing must be positive")
        chosen = tuple(int(v) for v in layers)
        if not chosen:
            raise ValueError("at least one adjacent-layer pair is required")
        for layer in chosen:
            if layer < 0 or layer + 1 >= LAYERS:
                raise ValueError(f"layer pair ({layer}, {layer + 1}) is outside 0..{LAYERS - 1}")
        self.window_width = int(window_width)
        self.layers = chosen
        self.smoothing = float(smoothing)

    def config(self) -> dict[str, Any]:
        return {
            "window_width": self.window_width,
            "layers": list(self.layers),
            "smoothing": self.smoothing,
        }

    def fit(self, routine_traces: Sequence[Any]) -> TRM3JState:
        pair_counts = torch.zeros((len(self.layers), EXPERTS, EXPERTS), dtype=torch.float64)
        marg_counts = torch.zeros((LAYERS, EXPERTS), dtype=torch.float64)
        n_tokens = 0
        n_traces = 0
        for trace in routine_traces:
            ids = check_top_k_ids(trace.top_k_ids)
            n_traces += 1
            tokens = int(ids.shape[1])
            if not tokens:
                continue
            n_tokens += tokens
            top1 = ids[:, :, 0]  # [16, T]
            marg_counts.scatter_add_(1, top1, torch.ones_like(top1, dtype=torch.float64))
            flat = pair_counts.view(len(self.layers), EXPERTS * EXPERTS)
            index = torch.stack(
                [top1[layer] * EXPERTS + top1[layer + 1] for layer in self.layers]
            )  # [L, T]
            flat.scatter_add_(1, index, torch.ones_like(index, dtype=torch.float64))
        if n_tokens == 0:
            raise ValueError("no routine decode tokens available for fitting")

        pair_total = float(n_tokens) + self.smoothing * EXPERTS * EXPERTS
        marg_total = float(n_tokens) + self.smoothing * EXPERTS
        log_pair = ((pair_counts + self.smoothing) / pair_total).log()
        log_marg = ((marg_counts + self.smoothing) / marg_total).log()
        return TRM3JState(
            layers=self.layers,
            log_pair=log_pair,
            log_marg=log_marg,
            pair_counts=pair_counts,
            marg_counts=marg_counts,
            n_tokens=n_tokens,
            n_traces=n_traces,
            smoothing=self.smoothing,
        )

    def token_scores(self, state: TRM3JState, trace: Any) -> torch.Tensor:
        """Per-token ``j_t`` [T] (exposed for attribution)."""

        ids = check_top_k_ids(trace.top_k_ids)
        tokens = int(ids.shape[1])
        if not tokens:
            return torch.empty(0, dtype=torch.float64)
        top1 = ids[:, :, 0]
        flat_pair = state.log_pair.view(len(state.layers), EXPERTS * EXPERTS)
        total = torch.zeros(tokens, dtype=torch.float64)
        for position, layer in enumerate(state.layers):
            lower = top1[layer]
            upper = top1[layer + 1]
            total += (
                -flat_pair[position].index_select(0, lower * EXPERTS + upper)
                + state.log_marg[layer].index_select(0, lower)
                + state.log_marg[layer + 1].index_select(0, upper)
            )
        return total

    def top_coordinates(
        self, state: TRM3JState, trace: Any, end: int, n: int = 3
    ) -> list[dict[str, Any]]:
        """Top-``n`` adjacent-layer expert chains of the window ending at ``end``.

        A "coordinate" of channel J is a pair ``(layer, layer+1)`` together with the two
        top-1 experts that were chained there.  Attribution output only; the contributions
        sum to the window score ``J_t``.
        """

        ids = check_top_k_ids(trace.top_k_ids)
        bounds = causal_window_bounds(int(ids.shape[1]), end, self.window_width)
        if bounds is None:
            return []
        start, stop = bounds
        top1 = ids[:, start : stop + 1, 0]  # [16, w]
        scale = 1.0 / float(self.window_width)
        totals: dict[tuple[int, int, int], list[float]] = {}
        for position, layer in enumerate(state.layers):
            lower = top1[layer]
            upper = top1[layer + 1]
            values = (
                -state.log_pair[position][lower, upper]
                + state.log_marg[layer][lower]
                + state.log_marg[layer + 1][upper]
            )
            for a, b, value in zip(lower.tolist(), upper.tolist(), values.tolist()):
                slot = totals.setdefault((int(layer), int(a), int(b)), [0.0, 0.0])
                slot[0] += float(value) * scale
                slot[1] += 1.0
        ranked = sorted(totals.items(), key=lambda item: (-item[1][0], item[0]))
        out: list[dict[str, Any]] = []
        # Unlike the additive-surprisal channels a J contribution can be negative (a chain
        # that is *more* common than its two ends predict), so the ranking is not truncated
        # at zero and the full list reconstructs the window score exactly.
        for (layer, a, b), (value, count) in ranked[: max(0, int(n))]:
            out.append(
                {
                    "pair": [layer, layer + 1],
                    "experts": [a, b],
                    "contribution": value,
                    "window_selections": int(count),
                }
            )
        return out


    def score(self, state: TRM3JState, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        per_token = self.token_scores(state, trace)
        if per_token.numel() < self.window_width:
            return empty_stream()
        ends, means = window_means(per_token[:, None], self.window_width)
        return means[:, 0].contiguous(), ends


@register("trm3_j")
def _build(config: dict[str, Any]) -> TRM3JScorer:
    return TRM3JScorer(**config)
