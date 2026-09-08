"""EXPLORATORY channels ``prob_concentration`` (rmass) and ``prob_entropy_drop``.

**Status: EXPLORATORY / POST-HOC, not part of the frozen TRM-3 preregistration.**

Both channels come from the code-blindspot zoom
(`docs/research_v2/zoom/code_blindspot/lead_synthesis.md` section 1.3 and
`refute_depth_profile.md`), which found that code deliverables do *not* recruit rare experts
but instead push probability mass **further onto the routine's own favourite experts** --
a movement that every selection-based statistic is blind to, and that has the *opposite*
sign from the other drift domains.

``prob_concentration`` (rmass, SIGN-INVERTED)
---------------------------------------------
::

    top8_routine[l] = the 8 experts with the highest routine selection count in layer l
    rmass_t         = mean_{l in 11..14} sum_{e in top8_routine[l]} p_t[l, e]
    score           = causal window mean of rmass_t   (w = 8)

"Sign-inverted" is meant relative to the usual divergence reading: for channel S / M a
*larger* score means "further from routine", whereas here a larger score means "more
concentrated on routine's own experts".  The scorer emits ``+rmass`` so that the detector's
"alarm when the running max is large" rule fires on high concentration, which is the
direction the zoom measured for code (routine ~0.185-0.20, code 0.236-0.312, other-domain
drift ~0.124).  The refutation report showed this quantity does not transfer across batches
(B2 -> B1 collapse, resist-arm FAR 0.438-1.000); testing that under the frozen protocol is
precisely what this channel is for.

``prob_entropy_drop``
---------------------
::

    score_t = mean_{l in band} ( H_routine[l] - H(p_t[l, :]) )     (nats)
    score   = causal window mean of score_t                        (w = 8)

with ``H_routine[l]`` the routine mean per-token router entropy of layer ``l`` (fit pool).
Positive = the router is *sharper* than routine.  Unlike rmass, sharpening was found to be a
property of all drift domains, not a code signature, so this channel is the natural
"generic sharpening" control for the concentration channel.

Both are fitted on routine traces only and are causal.
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
    WINDOW_WIDTH,
    causal_window_bounds,
    resolve_layers,
    routine_probability_stats,
    row_entropy,
    top_weighted_coordinates,
    trace_probabilities,
    weighted_token_scores,
)
from research_v2.scorers.trm3_s import selection_counts

TOP_K = 8


def routine_top_experts(counts: torch.Tensor, top_k: int = TOP_K) -> torch.Tensor:
    """``[16, 64]`` bool mask of the ``top_k`` most frequently selected experts per layer.

    Ties are broken by the lower expert id (``torch.argsort`` with ``stable=True`` on the
    negated counts), so the mask is a deterministic function of the routine fitting pool.
    """

    order = torch.argsort(-counts.to(torch.float64), dim=1, stable=True)
    mask = torch.zeros_like(counts, dtype=torch.bool)
    mask.scatter_(1, order[:, :top_k], True)
    return mask


@dataclass
class ProbConcentrationState:
    counts: torch.Tensor  # [16, 64] routine selection counts
    top_mask: torch.Tensor  # [16, 64] bool -- routine per-layer top-8
    table: torch.Tensor  # [16, 64] float64 -- top_mask / len(layers) inside the band
    n_tokens: int
    n_traces: int
    top_k: int
    layers: tuple[int, ...]

    def describe(self) -> dict[str, Any]:
        return {
            "channel": "prob_concentration",
            "status": "EXPLORATORY_POST_HOC",
            "sign": "inverted: higher concentration on the routine top-8 = more anomalous",
            "n_routine_tokens": int(self.n_tokens),
            "n_routine_traces": int(self.n_traces),
            "top_k": int(self.top_k),
            "layers": list(self.layers),
            "top_experts_per_layer": {
                str(layer): sorted(int(e) for e in self.top_mask[layer].nonzero().flatten())
                for layer in self.layers
            },
        }

    def to_json(self) -> dict[str, Any]:
        return self.describe()


class ProbConcentrationScorer:
    """Probability mass on the routine's per-layer top-8 experts (sign-inverted rmass)."""

    name = "prob_concentration"
    requires_positives = False

    def __init__(
        self,
        window_width: int = WINDOW_WIDTH,
        layers: str | Sequence[int] = "concentration",
        top_k: int = TOP_K,
    ) -> None:
        if int(window_width) <= 0:
            raise ValueError("window_width must be positive")
        if not 1 <= int(top_k) <= EXPERTS:
            raise ValueError("top_k must lie in [1, 64]")
        self.window_width = int(window_width)
        self.layer_band = layers if isinstance(layers, str) else "custom"
        self.layers = resolve_layers(layers)
        self.top_k = int(top_k)

    def config(self) -> dict[str, Any]:
        return {
            "window_width": self.window_width,
            "layers": list(self.layers),
            "layer_band": self.layer_band,
            "top_k": self.top_k,
        }

    def fit(self, routine_traces: Sequence[Any]) -> ProbConcentrationState:
        counts, n_tokens, n_traces = selection_counts(routine_traces)
        top_mask = routine_top_experts(counts, self.top_k)
        band = torch.zeros((LAYERS, EXPERTS), dtype=torch.float64)
        band[list(self.layers), :] = 1.0 / float(len(self.layers))
        return ProbConcentrationState(
            counts=counts,
            top_mask=top_mask,
            table=top_mask.to(torch.float64) * band,
            n_tokens=n_tokens,
            n_traces=n_traces,
            top_k=self.top_k,
            layers=self.layers,
        )

    def token_scores(self, state: ProbConcentrationState, trace: Any) -> torch.Tensor:
        probs = trace_probabilities(trace)
        return weighted_token_scores(probs, state.table)

    def top_coordinates(
        self, state: ProbConcentrationState, trace: Any, end: int, n: int = 3
    ) -> list[dict[str, Any]]:
        probs = trace_probabilities(trace)
        bounds = causal_window_bounds(int(probs.shape[1]), end, self.window_width)
        if bounds is None:
            return []
        start, stop = bounds
        return top_weighted_coordinates(probs, state.table, start, stop, n)

    def score(
        self, state: ProbConcentrationState, trace: Any
    ) -> tuple[torch.Tensor, torch.Tensor]:
        per_token = self.token_scores(state, trace)
        if per_token.numel() < self.window_width:
            return torch.empty(0, dtype=torch.float64), torch.empty(0, dtype=torch.long)
        ends, means = window_means(per_token[:, None], self.window_width)
        return means[:, 0].contiguous(), ends


@dataclass
class ProbEntropyDropState:
    routine_entropy: torch.Tensor  # [16] routine mean per-token router entropy
    n_tokens: int
    n_traces: int
    layers: tuple[int, ...]

    def describe(self) -> dict[str, Any]:
        return {
            "channel": "prob_entropy_drop",
            "status": "EXPLORATORY_POST_HOC",
            "sign": "positive = sharper than routine",
            "n_routine_tokens": int(self.n_tokens),
            "n_routine_traces": int(self.n_traces),
            "layers": list(self.layers),
            "routine_mean_entropy_per_layer": [float(v) for v in self.routine_entropy],
            "routine_mean_entropy_in_band": float(
                self.routine_entropy[list(self.layers)].mean()
            ),
        }

    def to_json(self) -> dict[str, Any]:
        return self.describe()


class ProbEntropyDropScorer:
    """Routine mean router entropy minus the token's router entropy, averaged over layers."""

    name = "prob_entropy_drop"
    requires_positives = False

    def __init__(
        self,
        window_width: int = WINDOW_WIDTH,
        layers: str | Sequence[int] = "all",
    ) -> None:
        if int(window_width) <= 0:
            raise ValueError("window_width must be positive")
        self.window_width = int(window_width)
        self.layer_band = layers if isinstance(layers, str) else "custom"
        self.layers = resolve_layers(layers)

    def config(self) -> dict[str, Any]:
        return {
            "window_width": self.window_width,
            "layers": list(self.layers),
            "layer_band": self.layer_band,
        }

    def fit(self, routine_traces: Sequence[Any]) -> ProbEntropyDropState:
        stats = routine_probability_stats(routine_traces)
        return ProbEntropyDropState(
            routine_entropy=stats.mean_entropy,
            n_tokens=stats.n_tokens,
            n_traces=stats.n_traces,
            layers=self.layers,
        )

    def token_scores(self, state: ProbEntropyDropState, trace: Any) -> torch.Tensor:
        probs = trace_probabilities(trace)
        if not probs.shape[1]:
            return torch.empty(0, dtype=torch.float64)
        band = list(state.layers)
        entropy = row_entropy(probs[band, :, :])  # [len(band), T]
        drop = state.routine_entropy[band][:, None] - entropy
        return drop.mean(dim=0)

    def score(
        self, state: ProbEntropyDropState, trace: Any
    ) -> tuple[torch.Tensor, torch.Tensor]:
        per_token = self.token_scores(state, trace)
        if per_token.numel() < self.window_width:
            return torch.empty(0, dtype=torch.float64), torch.empty(0, dtype=torch.long)
        ends, means = window_means(per_token[:, None], self.window_width)
        return means[:, 0].contiguous(), ends


@register("prob_concentration")
def _build_concentration(config: dict[str, Any]) -> ProbConcentrationScorer:
    config = dict(config)
    config.setdefault("layers", "concentration")
    return ProbConcentrationScorer(**config)


@register("prob_entropy_drop")
def _build_entropy(config: dict[str, Any]) -> ProbEntropyDropScorer:
    config = dict(config)
    config.setdefault("layers", "all")
    return ProbEntropyDropScorer(**config)
