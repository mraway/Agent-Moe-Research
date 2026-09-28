"""Baseline B-U ``unseen_only`` -- hard routine-support indicator (prereg section 6).

The ablation that keeps only the *support* part of TRM-3 channel S and throws the
surprisal weighting away: a coordinate either was selected at least once in the routine
fitting pool or it was not (unsmoothed counts).  Per decode token::

    u_t = #{(l, e) : e in top8(l, t) and routine_count[l, e] == 0}     (16 x 8 selections)

and the window score is the causal **maximum** of ``u_t`` over ``w = 8`` tokens, so any
never-before-selected coordinate anywhere in the window makes the score >= 1 and every
window that stays inside the routine support scores exactly 0.  Endpoints match the other
channels (decode index of the window's last token), so the harness's bucket
standardization and conformal threshold apply unchanged.

This differs from ``scripts/analyze_agent_v2_routine_expert_support.py``'s ``unseen8``,
which is the window *mean* of the per-token unseen *fraction*; the prereg fixes the
"any unseen in the window fires" reading, which is a monotone function of the same signal
but is not rescaled by the window length.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import torch

from research_v2.scorers import register
from research_v2.scorers.trm3_s import (
    EXPERTS,
    LAYERS,
    causal_window_bounds,
    check_top_k_ids,
    empty_stream,
    gather_selected,
    selection_counts,
    top_coordinate_contributions,
)

WINDOW_WIDTH = 8


def window_maxima(values: torch.Tensor, width: int) -> tuple[torch.Tensor, torch.Tensor]:
    """Causal window maxima of a [T] per-token stream -> ``(ends, maxima)``."""

    if values.ndim != 1:
        raise ValueError("expected a [T] tensor")
    tokens = int(values.shape[0])
    if tokens < width:
        return torch.empty(0, dtype=torch.long), values.new_empty(0)
    maxima = values.unfold(0, width, 1).max(dim=1).values
    ends = torch.arange(width - 1, tokens, dtype=torch.long)
    return ends, maxima.contiguous()


@dataclass
class UnseenOnlyState:
    counts: torch.Tensor  # [16, 64] raw routine token counts
    unseen_mask: torch.Tensor  # [16, 64] bool, count == 0
    n_tokens: int
    n_traces: int

    def describe(self) -> dict[str, Any]:
        return {
            "channel": "unseen_only",
            "n_routine_tokens": int(self.n_tokens),
            "n_routine_traces": int(self.n_traces),
            "coordinates": LAYERS * EXPERTS,
            "unseen_coordinates": int(self.unseen_mask.sum()),
            "unseen_coordinates_per_layer": [int(v) for v in self.unseen_mask.sum(1).tolist()],
        }

    def to_json(self) -> dict[str, Any]:
        return self.describe()


class UnseenOnlyScorer:
    """Hard support indicator; fitted on routine traces only."""

    name = "unseen_only"
    requires_positives = False

    def __init__(self, window_width: int = WINDOW_WIDTH) -> None:
        if int(window_width) <= 0:
            raise ValueError("window_width must be positive")
        self.window_width = int(window_width)

    def config(self) -> dict[str, Any]:
        return {"window_width": self.window_width, "layers": list(range(LAYERS))}

    def fit(self, routine_traces: Sequence[Any]) -> UnseenOnlyState:
        counts, n_tokens, n_traces = selection_counts(routine_traces)
        return UnseenOnlyState(
            counts=counts,
            unseen_mask=counts == 0,
            n_tokens=n_tokens,
            n_traces=n_traces,
        )

    def token_scores(self, state: UnseenOnlyState, trace: Any) -> torch.Tensor:
        """Per-token unseen-coordinate count ``u_t`` [T]."""

        ids = check_top_k_ids(trace.top_k_ids)
        if not ids.shape[1]:
            return torch.empty(0, dtype=torch.float64)
        return gather_selected(state.unseen_mask.to(torch.float64), ids).sum(dim=(0, 2))

    def top_coordinates(
        self, state: UnseenOnlyState, trace: Any, end: int, n: int = 3
    ) -> list[dict[str, Any]]:
        """The ``n`` most frequent never-in-routine coordinates of the window at ``end``.

        Attribution output only.  Unlike the additive channels the window score is a
        maximum, so these contributions (selection counts of unseen coordinates inside the
        window) do not sum to the score.
        """

        ids = check_top_k_ids(trace.top_k_ids)
        bounds = causal_window_bounds(int(ids.shape[1]), end, self.window_width)
        if bounds is None:
            return []
        start, stop = bounds
        return top_coordinate_contributions(
            ids, state.unseen_mask.to(torch.float64), start, stop, 1.0, n
        )


    def score(self, state: UnseenOnlyState, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        per_token = self.token_scores(state, trace)
        if per_token.numel() < self.window_width:
            return empty_stream()
        ends, maxima = window_maxima(per_token, self.window_width)
        return maxima, ends


@register("unseen_only")
def _build(config: dict[str, Any]) -> UnseenOnlyScorer:
    return UnseenOnlyScorer(**config)
