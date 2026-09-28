"""EXPLORATORY channel ``prob_rare_mass`` -- the soft (weight-carrying) version of S.

**Status: EXPLORATORY / POST-HOC, not part of the frozen TRM-3 preregistration.**

Channel S counts *selections* of routine-rare coordinates::

    S_t = sum_l sum_{e in top8(l, t)} 1[(l, e) in Omega_rare] * (-log q[l, e])

so a token that puts 0.9 of its layer-``l`` mass on one rare expert and a token that puts
0.02 on it score identically as long as the expert is in the top 8.  This channel replaces
the selection indicator by the router probability itself::

    prob_rare_mass_t = sum_{(l, e) in Omega_rare} p_t[l, e]

with the SAME routine model as S -- ``q[l, e] = (count + 0.5) / (N_tok + 32)`` over the
routine fitting pool and ``Omega_rare = {(l, e) : q[l, e] < 0.02}`` -- and takes the causal
mean over ``w = 8`` tokens, all 16 layers.  It is the pure "how much weight went to experts
the routine hardly ever uses" statistic: no surprisal weighting, no selection threshold.

Fitted on routine traces only.  Inference reads ``top_k_ids`` (never, only through the
fitted ``q``) and the trace's router probabilities.
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
class ProbRareMassState:
    q: torch.Tensor  # [16, 64] smoothed routine selection rates (identical to channel S)
    rare_mask: torch.Tensor  # [16, 64] bool
    table: torch.Tensor  # [16, 64] float64 indicator of the rare band, layer-masked
    counts: torch.Tensor
    n_tokens: int
    n_traces: int
    rare_threshold: float
    smoothing: float
    layers: tuple[int, ...]

    def describe(self) -> dict[str, Any]:
        return {
            "channel": "prob_rare_mass",
            "status": "EXPLORATORY_POST_HOC",
            "n_routine_tokens": int(self.n_tokens),
            "n_routine_traces": int(self.n_traces),
            "rare_threshold": float(self.rare_threshold),
            "smoothing": float(self.smoothing),
            "layers": list(self.layers),
            "rare_coordinates": int(self.rare_mask.sum()),
            "rare_coordinates_per_layer": [int(v) for v in self.rare_mask.sum(1).tolist()],
            "rare_coordinates_in_band": int(self.table.sum()),
            "coordinates": LAYERS * EXPERTS,
            "unseen_coordinates": int((self.counts == 0).sum()),
        }

    def to_json(self) -> dict[str, Any]:
        return self.describe()


class ProbRareMassScorer:
    """Router-probability mass on the routine-rare coordinate set (soft channel S)."""

    name = "prob_rare_mass"
    requires_positives = False

    def __init__(
        self,
        window_width: int = WINDOW_WIDTH,
        rare_threshold: float = RARE_THRESHOLD,
        smoothing: float = SMOOTHING,
        layers: str | Sequence[int] = "all",
    ) -> None:
        if int(window_width) <= 0:
            raise ValueError("window_width must be positive")
        if float(smoothing) <= 0.0:
            raise ValueError("smoothing must be positive")
        self.window_width = int(window_width)
        self.rare_threshold = float(rare_threshold)
        self.smoothing = float(smoothing)
        self.layer_band = layers if isinstance(layers, str) else "custom"
        self.layers = resolve_layers(layers)

    def config(self) -> dict[str, Any]:
        return {
            "window_width": self.window_width,
            "rare_threshold": self.rare_threshold,
            "smoothing": self.smoothing,
            "layers": list(self.layers),
            "layer_band": self.layer_band,
        }

    def fit(self, routine_traces: Sequence[Any]) -> ProbRareMassState:
        counts, n_tokens, n_traces = selection_counts(routine_traces)
        q = smoothed_rates(counts, n_tokens, self.smoothing)
        rare_mask = q < self.rare_threshold
        band = torch.zeros((LAYERS, EXPERTS), dtype=torch.float64)
        band[list(self.layers), :] = 1.0
        table = rare_mask.to(torch.float64) * band
        return ProbRareMassState(
            q=q,
            rare_mask=rare_mask,
            table=table,
            counts=counts,
            n_tokens=n_tokens,
            n_traces=n_traces,
            rare_threshold=self.rare_threshold,
            smoothing=self.smoothing,
            layers=self.layers,
        )

    def token_scores(self, state: ProbRareMassState, trace: Any) -> torch.Tensor:
        probs = trace_probabilities(trace)
        return weighted_token_scores(probs, state.table)

    def top_coordinates(
        self, state: ProbRareMassState, trace: Any, end: int, n: int = 3
    ) -> list[dict[str, Any]]:
        probs = trace_probabilities(trace)
        bounds = causal_window_bounds(int(probs.shape[1]), end, self.window_width)
        if bounds is None:
            return []
        start, stop = bounds
        return top_weighted_coordinates(probs, state.table, start, stop, n)

    def score(self, state: ProbRareMassState, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        per_token = self.token_scores(state, trace)
        if per_token.numel() < self.window_width:
            return torch.empty(0, dtype=torch.float64), torch.empty(0, dtype=torch.long)
        ends, means = window_means(per_token[:, None], self.window_width)
        return means[:, 0].contiguous(), ends


@register("prob_rare_mass")
def _build(config: dict[str, Any]) -> ProbRareMassScorer:
    return ProbRareMassScorer(**config)
