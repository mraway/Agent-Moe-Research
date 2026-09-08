"""Baseline B-S ``surprisal_marginal`` -- mean marginal routing surprisal (prereg section 6).

The ablation that keeps the surprisal weighting of TRM-3 channel S but drops the rare-set
restriction: every one of the 16 x 8 selected coordinates of a token contributes, with the
same 0.5-smoothed routine selection rate ``q`` as ``trm3_s``::

    q[l, e] = (#routine tokens with e in top8(l) + 0.5) / (N_tok + 32)
    b_t     = mean_{l, e in top8(l, t)} (-log q[l, e])          (mean over 128 selections)

and the window score is the causal mean of ``b_t`` over ``w = 8`` tokens.

This is the analogue of Codex's ``surprisal8``
(``scripts/analyze_agent_v2_routine_expert_support.py``) built on our loader and scorer
registry: same window width, same 0.5 pseudocount, same per-token mean over the 128
selections.  The one difference is the normaliser -- Codex divides by the per-layer top-8
mass (``8 * N_tok + 32``, a per-layer distribution), the prereg divides by ``N_tok + 32``
(a per-expert selection rate).  That is a constant ``log 8`` shift per coordinate, so the
two streams differ by an additive constant and the bucket standardization removes it; the
prereg form is used here so that S and B-S share one table.
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
    TOP_K,
    causal_window_bounds,
    check_top_k_ids,
    empty_stream,
    gather_selected,
    selection_counts,
    smoothed_rates,
    top_coordinate_contributions,
)

WINDOW_WIDTH = 8


@dataclass
class SurprisalMarginalState:
    q: torch.Tensor  # [16, 64] smoothed routine selection rates
    surprisal: torch.Tensor  # [16, 64] -log q, all coordinates
    counts: torch.Tensor  # [16, 64] raw routine token counts
    n_tokens: int
    n_traces: int
    smoothing: float

    def describe(self) -> dict[str, Any]:
        return {
            "channel": "surprisal_marginal",
            "n_routine_tokens": int(self.n_tokens),
            "n_routine_traces": int(self.n_traces),
            "smoothing": float(self.smoothing),
            "coordinates": LAYERS * EXPERTS,
            "selections_per_token": LAYERS * TOP_K,
            "unseen_coordinates": int((self.counts == 0).sum()),
            "q_min": float(self.q.min()),
            "q_max": float(self.q.max()),
            "surprisal_max": float(self.surprisal.max()),
        }

    def to_json(self) -> dict[str, Any]:
        return self.describe()


class SurprisalMarginalScorer:
    """Mean marginal routing surprisal over all selected coordinates (routine-fitted)."""

    name = "surprisal_marginal"
    requires_positives = False

    def __init__(self, window_width: int = WINDOW_WIDTH, smoothing: float = SMOOTHING) -> None:
        if int(window_width) <= 0:
            raise ValueError("window_width must be positive")
        if float(smoothing) <= 0.0:
            raise ValueError("smoothing must be positive")
        self.window_width = int(window_width)
        self.smoothing = float(smoothing)

    def config(self) -> dict[str, Any]:
        return {
            "window_width": self.window_width,
            "smoothing": self.smoothing,
            "layers": list(range(LAYERS)),
        }

    def fit(self, routine_traces: Sequence[Any]) -> SurprisalMarginalState:
        counts, n_tokens, n_traces = selection_counts(routine_traces)
        q = smoothed_rates(counts, n_tokens, self.smoothing)
        return SurprisalMarginalState(
            q=q,
            surprisal=-q.log(),
            counts=counts,
            n_tokens=n_tokens,
            n_traces=n_traces,
            smoothing=self.smoothing,
        )

    def token_scores(self, state: SurprisalMarginalState, trace: Any) -> torch.Tensor:
        """Per-token mean marginal surprisal ``b_t`` [T]."""

        ids = check_top_k_ids(trace.top_k_ids)
        if not ids.shape[1]:
            return torch.empty(0, dtype=torch.float64)
        return gather_selected(state.surprisal, ids).mean(dim=(0, 2))

    def top_coordinates(
        self, state: SurprisalMarginalState, trace: Any, end: int, n: int = 3
    ) -> list[dict[str, Any]]:
        """Top-``n`` contributing coordinates of the window ending at ``end``.

        Attribution output only; the contributions sum to the window score ``b_t``.
        """

        ids = check_top_k_ids(trace.top_k_ids)
        bounds = causal_window_bounds(int(ids.shape[1]), end, self.window_width)
        if bounds is None:
            return []
        start, stop = bounds
        scale = 1.0 / (float(self.window_width) * float(LAYERS * TOP_K))
        return top_coordinate_contributions(ids, state.surprisal, start, stop, scale, n)


    def score(
        self, state: SurprisalMarginalState, trace: Any
    ) -> tuple[torch.Tensor, torch.Tensor]:
        per_token = self.token_scores(state, trace)
        if per_token.numel() < self.window_width:
            return empty_stream()
        ends, means = window_means(per_token[:, None], self.window_width)
        return means[:, 0].contiguous(), ends


@register("surprisal_marginal")
def _build(config: dict[str, Any]) -> SurprisalMarginalScorer:
    return SurprisalMarginalScorer(**config)
