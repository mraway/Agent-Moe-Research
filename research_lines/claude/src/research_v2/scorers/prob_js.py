"""EXPLORATORY channels ``prob_js`` / ``prob_js_all`` -- router-distribution JS divergence.

**Status: EXPLORATORY / POST-HOC, not part of the frozen TRM-3 preregistration.**

Channel M (frozen CAND-A) measures a whitened distance between the window's top-8
*selection-rate* vector and the routine centre.  This channel keeps the same shape -- a
window-level distance to a routine reference over the OLMoE band -- but on the router's
*probability simplex*::

    pbar_l = (1 / w) * sum_{i in window} p_i[l, :]     (a distribution over 64 experts)
    qbar_l = routine mean router distribution of layer l (fit pool, token-weighted)
    JS_l   = 0.5 * KL(pbar_l || m_l) + 0.5 * KL(qbar_l || m_l),   m_l = (pbar_l + qbar_l)/2
    score  = sum_{l in band} JS_l

``prob_js`` sums over the frozen M band (layers 5-15); ``prob_js_all`` sums over all 16
layers.  Natural logarithms, ``0 log 0 = 0``, so ``0 <= JS_l <= log 2`` per layer.  Causal
window ``w = 8``; the score at endpoint ``t`` reads only decode tokens ``t-7 .. t``.

Unlike the additive channels this statistic is a nonlinear function of the window mean (the
window mean is taken on the probabilities, the divergence afterwards), which is exactly the
point: it is sensitive to a *reshaped* distribution even when no rare coordinate is touched.
Fitted on routine traces only.
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
    resolve_layers,
    routine_probability_stats,
    trace_probabilities,
)

LOG2 = float(torch.log(torch.tensor(2.0, dtype=torch.float64)))


def jensen_shannon(p: torch.Tensor, q: torch.Tensor) -> torch.Tensor:
    """Per-row Jensen-Shannon divergence (nats) of ``[..., E]`` against ``[..., E]``.

    ``0 log 0 = 0``; the result lies in ``[0, log 2]`` for probability rows.
    """

    p = p.to(torch.float64).clamp_min(0.0)
    q = q.to(torch.float64).clamp_min(0.0)
    m = 0.5 * (p + q)
    log_m = torch.where(m > 0.0, m.log(), torch.zeros_like(m))
    log_p = torch.where(p > 0.0, p.log(), torch.zeros_like(p))
    log_q = torch.where(q > 0.0, q.log(), torch.zeros_like(q))
    kl_p = (p * (log_p - log_m)).sum(-1)
    kl_q = (q * (log_q - log_m)).sum(-1)
    return (0.5 * kl_p + 0.5 * kl_q).clamp_min(0.0)


@dataclass
class ProbJSState:
    routine_mean: torch.Tensor  # [16, 64] routine mean router distribution
    routine_entropy: torch.Tensor  # [16]
    n_tokens: int
    n_traces: int
    layers: tuple[int, ...]
    simplex_max_deviation: float

    def describe(self) -> dict[str, Any]:
        return {
            "channel": "prob_js",
            "status": "EXPLORATORY_POST_HOC",
            "n_routine_tokens": int(self.n_tokens),
            "n_routine_traces": int(self.n_traces),
            "layers": list(self.layers),
            "layer_count": len(self.layers),
            "max_score": len(self.layers) * LOG2,
            "routine_mean_row_sum_min": float(self.routine_mean.sum(1).min()),
            "routine_mean_row_sum_max": float(self.routine_mean.sum(1).max()),
            "routine_mean_entropy_per_layer": [float(v) for v in self.routine_entropy],
            "simplex_max_deviation": float(self.simplex_max_deviation),
        }

    def to_json(self) -> dict[str, Any]:
        return self.describe()


class ProbJSScorer:
    """Summed per-layer Jensen-Shannon divergence of the window router distribution."""

    name = "prob_js"
    requires_positives = False

    def __init__(
        self,
        window_width: int = WINDOW_WIDTH,
        layers: str | Sequence[int] = "middle_late",
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

    def fit(self, routine_traces: Sequence[Any]) -> ProbJSState:
        stats = routine_probability_stats(routine_traces)
        return ProbJSState(
            routine_mean=stats.mean_prob,
            routine_entropy=stats.mean_entropy,
            n_tokens=stats.n_tokens,
            n_traces=stats.n_traces,
            layers=self.layers,
            simplex_max_deviation=stats.simplex_max_deviation,
        )

    def window_distributions(self, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        """``(ends, pbar[nwin, 16, 64])`` causal window-mean router distributions."""

        probs = trace_probabilities(trace)
        tokens = int(probs.shape[1])
        if tokens < self.window_width:
            return torch.empty(0, dtype=torch.long), torch.empty(
                (0, LAYERS, EXPERTS), dtype=torch.float64
            )
        flat = probs.permute(1, 0, 2).reshape(tokens, LAYERS * EXPERTS)
        ends, means = window_means(flat, self.window_width)
        return ends, means.reshape(-1, LAYERS, EXPERTS)

    def score(self, state: ProbJSState, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        ends, pbar = self.window_distributions(trace)
        if not ends.numel():
            return torch.empty(0, dtype=torch.float64), torch.empty(0, dtype=torch.long)
        band = list(state.layers)
        divergence = jensen_shannon(pbar[:, band, :], state.routine_mean[band, :][None, :, :])
        return divergence.sum(dim=1).contiguous(), ends


@register("prob_js")
def _build(config: dict[str, Any]) -> ProbJSScorer:
    config = dict(config)
    config.setdefault("layers", "middle_late")
    return ProbJSScorer(**config)


@register("prob_js_all")
def _build_all(config: dict[str, Any]) -> ProbJSScorer:
    config = dict(config)
    config.setdefault("layers", "all")
    return ProbJSScorer(**config)
