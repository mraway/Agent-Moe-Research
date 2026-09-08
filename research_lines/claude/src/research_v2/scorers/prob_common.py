"""Shared routine-only fitting helpers for the EXPLORATORY router-probability channels.

**Status: EXPLORATORY / POST-HOC.**  Nothing in this module is part of the frozen TRM-3
preregistration (`docs/research_v3/trm3_prereg.md`).  The prereg deliberately keeps the
probability tensor out of the main path ("概率张量不进主路径", section 2) and admits it
only through the routine-only stability gate S6.  The scorers built on top of these
helpers exist to answer one question on already-frozen development data: do the router
*weights* carry timing / coverage information that the selection-only statistics S and M
throw away?  They are not a patch of the frozen proposal and none of their parameters was
tuned on a target.

What the helpers provide
------------------------
``trace_probabilities``      -- the ``[16, T, 64]`` router softmax of one trace, validated.
``routine_probability_stats``-- the routine (fit-pool) mean router distribution, the routine
                                mean per-layer entropy and the token/trace counts.
``weighted_token_scores``    -- ``s_t = sum_{l,e} p_t[l,e] * table[l,e]`` for a fixed
                                coefficient table (the shape shared by the rare-mass, the
                                probability-weighted-surprisal and the concentration
                                channels).
``top_weighted_coordinates`` -- the matching ``(layer, expert)`` attribution whose entries
                                sum to the window score.

Everything is fitted on routine traces only and every score is causal: the window ending at
decode index ``t`` reads decode tokens ``t - w + 1 .. t`` and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import torch

LAYERS = 16
EXPERTS = 64

# The routine selection-rate model of channel S is reused verbatim (prereg section 2), so
# the "rare" coordinate set of the probability channels is *the same* Omega_rare as S's.
SMOOTHING = 0.5
RARE_THRESHOLD = 0.02
WINDOW_WIDTH = 8

# Layer bands.  ``ALL`` = the 16 layers channel S uses; ``OLMOE_BAND`` = the 5-15 band of
# channel M (frozen CAND-A); ``CONCENTRATION`` = the 11-14 band on which the code-blindspot
# zoom measured rmass (docs/research_v2/zoom/code_blindspot/lead_synthesis.md section 1.3).
ALL_LAYERS: tuple[int, ...] = tuple(range(LAYERS))
OLMOE_BAND: tuple[int, ...] = tuple(range(5, 16))
CONCENTRATION_BAND: tuple[int, ...] = tuple(range(11, 15))

LAYER_BANDS: dict[str, tuple[int, ...]] = {
    "all": ALL_LAYERS,
    "middle_late": OLMOE_BAND,
    "concentration": CONCENTRATION_BAND,
}


def resolve_layers(layers: str | Sequence[int]) -> tuple[int, ...]:
    """``"all"`` / ``"middle_late"`` / ``"concentration"`` or an explicit layer list."""

    if isinstance(layers, str):
        if layers not in LAYER_BANDS:
            raise ValueError(f"unknown layer band {layers!r}; known: {sorted(LAYER_BANDS)}")
        return LAYER_BANDS[layers]
    resolved = tuple(int(v) for v in layers)
    if not resolved:
        raise ValueError("layer band must not be empty")
    if any(l < 0 or l >= LAYERS for l in resolved):
        raise ValueError(f"layer index outside [0, {LAYERS - 1}]: {resolved}")
    return resolved


def check_probabilities(probs: torch.Tensor, tokens: int | None = None) -> torch.Tensor:
    """Validate a ``[16, T, 64]`` router-probability tensor; return it as ``float64``."""

    if not isinstance(probs, torch.Tensor):
        probs = torch.as_tensor(probs)
    if probs.ndim != 3 or probs.shape[0] != LAYERS or probs.shape[2] != EXPERTS:
        raise ValueError(
            f"expected router probabilities with shape [16, T, 64], got {tuple(probs.shape)}"
        )
    if tokens is not None and int(probs.shape[1]) != int(tokens):
        raise ValueError(
            f"router probabilities have {int(probs.shape[1])} tokens but the top-k tensor "
            f"has {int(tokens)}"
        )
    return probs.to(torch.float64)


def trace_probabilities(trace: Any) -> torch.Tensor:
    """``[16, T, 64]`` float64 router softmax of ``trace``.

    ``research_v2.io.LoadedTrace.probabilities`` is a *method* that lazily reads the frozen
    routing cache; a light test stand-in may expose a plain tensor attribute instead.  Both
    are accepted, and the token count is cross-checked against ``top_k_ids`` so a mismatched
    cache can never be scored silently.
    """

    probs = getattr(trace, "probabilities", None)
    if callable(probs):
        probs = probs()
    if probs is None:
        raise ValueError(
            "this scorer needs the full router probabilities; load the pool with "
            "with_probabilities=True (or expose a `probabilities` attribute/method)"
        )
    ids = getattr(trace, "top_k_ids", None)
    tokens = None if ids is None else int(ids.shape[1])
    return check_probabilities(probs, tokens)


def row_entropy(probs: torch.Tensor) -> torch.Tensor:
    """Natural-log entropy of the last axis, with ``0 log 0 = 0``.  ``[..., 64] -> [...]``."""

    safe = probs.clamp_min(0.0)
    logs = torch.where(safe > 0.0, safe.log(), torch.zeros_like(safe))
    return -(safe * logs).sum(-1)


@dataclass
class RoutineProbabilityStats:
    """Routine-only summary of the router *weights* over the fitting pool N_fit."""

    mean_prob: torch.Tensor  # [16, 64] mean router distribution per layer (sums to 1)
    mean_entropy: torch.Tensor  # [16] mean per-token router entropy per layer (nats)
    n_tokens: int
    n_traces: int
    simplex_max_deviation: float

    def describe(self) -> dict[str, Any]:
        return {
            "n_routine_tokens": int(self.n_tokens),
            "n_routine_traces": int(self.n_traces),
            "routine_mean_entropy_per_layer": [float(v) for v in self.mean_entropy],
            "routine_mean_entropy": float(self.mean_entropy.mean()),
            "mean_prob_row_sum_min": float(self.mean_prob.sum(1).min()),
            "mean_prob_row_sum_max": float(self.mean_prob.sum(1).max()),
            "simplex_max_deviation": float(self.simplex_max_deviation),
        }


def routine_probability_stats(traces: Sequence[Any]) -> RoutineProbabilityStats:
    """Mean router distribution and mean per-layer entropy over the routine fitting pool.

    Accumulated in float64 over *tokens* (not over traces), matching the token-weighted
    convention of channel S's ``q``.  ``simplex_max_deviation`` is the largest observed
    ``|sum_e p_t[l, e] - 1|``; it is provenance only (a corrupt cache would show up here).
    """

    prob_sum = torch.zeros((LAYERS, EXPERTS), dtype=torch.float64)
    entropy_sum = torch.zeros(LAYERS, dtype=torch.float64)
    n_tokens = 0
    n_traces = 0
    deviation = 0.0
    for trace in traces:
        probs = trace_probabilities(trace)
        n_traces += 1
        tokens = int(probs.shape[1])
        if not tokens:
            continue
        n_tokens += tokens
        prob_sum += probs.sum(dim=1)
        entropy_sum += row_entropy(probs).sum(dim=1)
        deviation = max(deviation, float((probs.sum(-1) - 1.0).abs().max()))
    if n_tokens == 0:
        raise ValueError("no routine decode tokens available for fitting")
    return RoutineProbabilityStats(
        mean_prob=prob_sum / float(n_tokens),
        mean_entropy=entropy_sum / float(n_tokens),
        n_tokens=n_tokens,
        n_traces=n_traces,
        simplex_max_deviation=deviation,
    )


def weighted_token_scores(probs: torch.Tensor, table: torch.Tensor) -> torch.Tensor:
    """``s_t = sum_{l, e} p_t[l, e] * table[l, e]`` -> ``[T]`` float64."""

    if table.shape != (LAYERS, EXPERTS):
        raise ValueError(f"expected a [16, 64] coefficient table, got {tuple(table.shape)}")
    if not probs.shape[1]:
        return torch.empty(0, dtype=torch.float64)
    return (probs * table.to(torch.float64)[:, None, :]).sum(dim=(0, 2))


def top_weighted_coordinates(
    probs: torch.Tensor,
    table: torch.Tensor,
    start: int,
    end: int,
    n: int,
) -> list[dict[str, Any]]:
    """Top-``n`` ``(layer, expert)`` contributions to the window score on ``[start, end]``.

    The window score of every ``weighted_token_scores`` channel is the causal mean over the
    window of a sum over coordinates, so the per-coordinate mean contributions returned here
    sum exactly to that window score.  Attribution output only.
    """

    window = probs[:, int(start) : int(end) + 1, :]
    width = int(window.shape[1])
    if width <= 0:
        return []
    values = window.mean(dim=1) * table.to(torch.float64)
    flat = values.reshape(-1)
    order = torch.argsort(flat, descending=True, stable=True)
    out: list[dict[str, Any]] = []
    for index in order[: max(0, int(n))].tolist():
        value = float(flat[index])
        if value <= 0.0:
            break
        layer, expert = divmod(int(index), EXPERTS)
        out.append(
            {
                "layer": layer,
                "expert": expert,
                "contribution": value,
                "window_probability_mass": float(window[layer, :, expert].mean()),
            }
        )
    return out


def causal_window_bounds(tokens: int, end: int, width: int) -> tuple[int, int] | None:
    """Inclusive ``[start, end]`` of the causal window, or ``None`` when it is not full."""

    end = int(end)
    if end < 0 or end >= int(tokens) or end < int(width) - 1:
        return None
    return end - int(width) + 1, end
