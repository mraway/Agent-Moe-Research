"""EXPLORATORY channels ``prob_weighted_surprisal`` / ``prob_weighted_surprisal_rare``.

**Status: EXPLORATORY / POST-HOC, not part of the frozen TRM-3 preregistration.**

Channel S and baseline B-S both evaluate ``-log q`` at the eight *selected* coordinates of
each layer; the router's own weight on those coordinates is discarded.  These two channels
keep the same routine model ``q[l, e] = (count + 0.5) / (N_tok + 32)`` (prereg section 2)
but integrate the surprisal against the full router distribution::

    prob_weighted_surprisal_t      = sum_{l, e}                p_t[l, e] * (-log q[l, e])
    prob_weighted_surprisal_rare_t = sum_{(l,e) in Omega_rare}  p_t[l, e] * (-log q[l, e])

The first is a cross-entropy-like quantity over all 16 x 64 coordinates: it moves when the
router *reweights inside* the routine support, which is exactly the regime the code-blindspot
zoom found the selection-only family blind to
(`docs/research_v2/zoom/code_blindspot/lead_synthesis.md` section 2).  The second restricts
the same integral to the rare band ``Omega_rare = {q < 0.02}`` and is the direct
weight-carrying analogue of channel S.

Causal mean over ``w = 8`` tokens, all 16 layers, fitted on routine traces only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import torch

from research_v2.features import window_means
from research_v2.scorers import register
from research_v2.scorers.prob_common import (
    EXPERTS,
    LAYERS,
    RARE_THRESHOLD,
    SMOOTHING,
    WINDOW_WIDTH,
    causal_window_bounds,
    resolve_layers,
    top_weighted_coordinates,
    trace_probabilities,
    weighted_token_scores,
)
from research_v2.scorers.trm3_s import selection_counts, smoothed_rates


@dataclass
class ProbWeightedSurprisalState:
    q: torch.Tensor  # [16, 64]
    rare_mask: torch.Tensor  # [16, 64] bool
    table: torch.Tensor  # [16, 64] float64 -log q, masked to the band (and to Omega_rare)
    counts: torch.Tensor
    n_tokens: int
    n_traces: int
    rare_only: bool
    rare_threshold: float
    smoothing: float
    layers: tuple[int, ...]

    def describe(self) -> dict[str, Any]:
        active = int((self.table > 0).sum())
        return {
            "channel": (
                "prob_weighted_surprisal_rare" if self.rare_only else "prob_weighted_surprisal"
            ),
            "status": "EXPLORATORY_POST_HOC",
            "rare_only": bool(self.rare_only),
            "n_routine_tokens": int(self.n_tokens),
            "n_routine_traces": int(self.n_traces),
            "rare_threshold": float(self.rare_threshold),
            "smoothing": float(self.smoothing),
            "layers": list(self.layers),
            "rare_coordinates": int(self.rare_mask.sum()),
            "active_coordinates": active,
            "coordinates": LAYERS * EXPERTS,
            "surprisal_min": float(self.table[self.table > 0].min()) if active else None,
            "surprisal_max": float(self.table.max()),
        }

    def to_json(self) -> dict[str, Any]:
        return self.describe()


class ProbWeightedSurprisalScorer:
    """Router-probability-weighted routine surprisal (all coordinates, or the rare band)."""

    name = "prob_weighted_surprisal"
    requires_positives = False

    def __init__(
        self,
        window_width: int = WINDOW_WIDTH,
        rare_only: bool = False,
        rare_threshold: float = RARE_THRESHOLD,
        smoothing: float = SMOOTHING,
        layers: str | Sequence[int] = "all",
    ) -> None:
        if int(window_width) <= 0:
            raise ValueError("window_width must be positive")
        if float(smoothing) <= 0.0:
            raise ValueError("smoothing must be positive")
        self.window_width = int(window_width)
        self.rare_only = bool(rare_only)
        self.rare_threshold = float(rare_threshold)
        self.smoothing = float(smoothing)
        self.layer_band = layers if isinstance(layers, str) else "custom"
        self.layers = resolve_layers(layers)

    def config(self) -> dict[str, Any]:
        return {
            "window_width": self.window_width,
            "rare_only": self.rare_only,
            "rare_threshold": self.rare_threshold,
            "smoothing": self.smoothing,
            "layers": list(self.layers),
            "layer_band": self.layer_band,
        }

    def fit(self, routine_traces: Sequence[Any]) -> ProbWeightedSurprisalState:
        counts, n_tokens, n_traces = selection_counts(routine_traces)
        q = smoothed_rates(counts, n_tokens, self.smoothing)
        rare_mask = q < self.rare_threshold
        surprisal = -q.log()
        band = torch.zeros((LAYERS, EXPERTS), dtype=torch.float64)
        band[list(self.layers), :] = 1.0
        table = surprisal.to(torch.float64) * band
        if self.rare_only:
            table = table * rare_mask.to(torch.float64)
        return ProbWeightedSurprisalState(
            q=q,
            rare_mask=rare_mask,
            table=table,
            counts=counts,
            n_tokens=n_tokens,
            n_traces=n_traces,
            rare_only=self.rare_only,
            rare_threshold=self.rare_threshold,
            smoothing=self.smoothing,
            layers=self.layers,
        )

    def token_scores(self, state: ProbWeightedSurprisalState, trace: Any) -> torch.Tensor:
        probs = trace_probabilities(trace)
        return weighted_token_scores(probs, state.table)

    def top_coordinates(
        self, state: ProbWeightedSurprisalState, trace: Any, end: int, n: int = 3
    ) -> list[dict[str, Any]]:
        probs = trace_probabilities(trace)
        bounds = causal_window_bounds(int(probs.shape[1]), end, self.window_width)
        if bounds is None:
            return []
        start, stop = bounds
        return top_weighted_coordinates(probs, state.table, start, stop, n)

    def score(
        self, state: ProbWeightedSurprisalState, trace: Any
    ) -> tuple[torch.Tensor, torch.Tensor]:
        per_token = self.token_scores(state, trace)
        if per_token.numel() < self.window_width:
            return torch.empty(0, dtype=torch.float64), torch.empty(0, dtype=torch.long)
        ends, means = window_means(per_token[:, None], self.window_width)
        return means[:, 0].contiguous(), ends


@register("prob_weighted_surprisal")
def _build(config: dict[str, Any]) -> ProbWeightedSurprisalScorer:
    config = dict(config)
    config.setdefault("rare_only", False)
    return ProbWeightedSurprisalScorer(**config)


@register("prob_weighted_surprisal_rare")
def _build_rare(config: dict[str, Any]) -> ProbWeightedSurprisalScorer:
    config = dict(config)
    config["rare_only"] = True
    return ProbWeightedSurprisalScorer(**config)
