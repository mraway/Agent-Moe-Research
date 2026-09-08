"""TRM-3 channel S -- rare-coordinate support-expansion surprisal (prereg section 2).

Fitted on routine traces only; inference reads nothing but ``trace.top_k_ids``.

Routine model (all 16 layers, all 64 experts)::

    q[l, e] = (#routine tokens with e in top8(l) + 0.5) / (N_tok + 32)
    Omega_rare = {(l, e) : q[l, e] < rare_threshold}      rare_threshold = 0.02 (frozen)

Per decode token the channel sums the surprisal of the *rare* selected coordinates only
(the 128 = 16 x 8 selections of that token), so a token that stays inside the routine
support scores exactly 0::

    s_t = sum_l sum_{e in top8(l, t)} 1[(l, e) in Omega_rare] * (-log q[l, e])

and the window score is the causal mean of ``s_t`` over ``w = 8`` tokens
(``features.window_means``; endpoint = decode index of the window's last token).

Note on the normalisation: ``q`` is a per-expert *selection rate* (denominator
``N_tok + 64 * 0.5``), not the per-layer top-8 distribution used by
``scripts/analyze_agent_v2_routine_expert_support.py`` (denominator ``8 * N_tok + 32``).
The prereg fixes this form; the two differ by an approximately constant ``log 8`` offset
per coordinate, which matters for the rare threshold and is therefore not interchangeable.

This module also holds the count/gather helpers shared by ``unseen_only`` and
``surprisal_marginal``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import torch

from research_v2.features import window_means
from research_v2.scorers import register

LAYERS = 16
EXPERTS = 64
TOP_K = 8

# Frozen constants (docs/research_v3/trm3_prereg.md sections 2 and 5).
SMOOTHING = 0.5
RARE_THRESHOLD = 0.02
WINDOW_WIDTH = 8


def check_top_k_ids(top_k_ids: torch.Tensor) -> torch.Tensor:
    """Validate a ``[16, T, 8]`` top-8 selection tensor and return it as ``long``."""

    if top_k_ids.ndim != 3 or top_k_ids.shape[0] != LAYERS or top_k_ids.shape[2] != TOP_K:
        raise ValueError(f"expected top_k_ids with shape [16, T, 8], got {tuple(top_k_ids.shape)}")
    ids = top_k_ids.long()
    if ids.numel() and (int(ids.min()) < 0 or int(ids.max()) >= EXPERTS):
        raise ValueError("top_k_ids contains an expert id outside [0, 63]")
    return ids


def selection_counts(traces: Sequence[Any]) -> tuple[torch.Tensor, int, int]:
    """``(counts[16, 64], n_tokens, n_traces)`` over the routine fitting pool.

    ``counts[l, e]`` = number of decode tokens whose layer-``l`` top-8 contains ``e``
    (the eight ids of one token/layer are distinct, so a plain occurrence count is a
    token count).  Counts are unsmoothed; smoothing is applied by the callers.
    """

    counts = torch.zeros((LAYERS, EXPERTS), dtype=torch.float64)
    n_tokens = 0
    n_traces = 0
    for trace in traces:
        ids = check_top_k_ids(trace.top_k_ids)
        n_traces += 1
        tokens = int(ids.shape[1])
        if not tokens:
            continue
        n_tokens += tokens
        flat = ids.reshape(LAYERS, -1)
        counts.scatter_add_(1, flat, torch.ones_like(flat, dtype=torch.float64))
    if n_tokens == 0:
        raise ValueError("no routine decode tokens available for fitting")
    return counts, n_tokens, n_traces


def smoothed_rates(counts: torch.Tensor, n_tokens: int, smoothing: float = SMOOTHING) -> torch.Tensor:
    """``q[l, e] = (count + smoothing) / (N_tok + 64 * smoothing)`` (prereg section 2)."""

    return (counts + smoothing) / (float(n_tokens) + EXPERTS * smoothing)


def gather_selected(table: torch.Tensor, ids: torch.Tensor) -> torch.Tensor:
    """``table[16, 64]`` evaluated at every selected coordinate -> ``[16, T, 8]``."""

    tokens = int(ids.shape[1])
    return torch.gather(table[:, None, :].expand(LAYERS, tokens, EXPERTS), 2, ids)


def empty_stream(dtype: torch.dtype = torch.float64) -> tuple[torch.Tensor, torch.Tensor]:
    return torch.empty(0, dtype=dtype), torch.empty(0, dtype=torch.long)



def causal_window_bounds(tokens: int, end: int, width: int) -> tuple[int, int] | None:
    """Inclusive ``[start, end]`` of the causal window ending at decode index ``end``.

    Returns ``None`` when ``end`` is outside the decode range or the window is not full
    (the scorers only emit endpoints for full causal windows).
    """

    end = int(end)
    if end < 0 or end >= int(tokens) or end < int(width) - 1:
        return None
    return end - int(width) + 1, end


def top_coordinate_contributions(
    ids: torch.Tensor,
    table: torch.Tensor,
    start: int,
    end: int,
    scale: float,
    n: int,
) -> list[dict[str, Any]]:
    """Top-``n`` ``(layer, expert)`` contributions inside the causal window ``[start, end]``.

    ``table[l, e]`` is the per-selection weight of a coordinate (rare-masked surprisal for
    channel S, plain surprisal for B-S, the unseen indicator for B-U) and ``scale`` turns a
    per-selection weight into the units of the window score, so for the additive channels
    the returned contributions sum to the window score.  Attribution only; nothing here
    feeds the score or the state.
    """

    window = ids[:, int(start) : int(end) + 1, :]
    layers = torch.arange(LAYERS, dtype=torch.long)[:, None, None].expand_as(window)
    flat = (layers * EXPERTS + window).reshape(-1)
    counts = torch.bincount(flat, minlength=LAYERS * EXPERTS).to(torch.float64)
    values = counts * table.reshape(-1).to(torch.float64) * float(scale)
    order = torch.argsort(values, descending=True, stable=True)
    out: list[dict[str, Any]] = []
    for index in order[: max(0, int(n))].tolist():
        value = float(values[index])
        if value <= 0.0:
            break
        out.append(
            {
                "layer": int(index) // EXPERTS,
                "expert": int(index) % EXPERTS,
                "contribution": value,
                "window_selections": int(counts[index]),
            }
        )
    return out


@dataclass
class TRM3SState:
    q: torch.Tensor  # [16, 64] smoothed routine selection rates
    rare_mask: torch.Tensor  # [16, 64] bool, q < rare_threshold
    surprisal: torch.Tensor  # [16, 64] rare-masked -log q
    counts: torch.Tensor  # [16, 64] raw routine token counts
    n_tokens: int
    n_traces: int
    rare_threshold: float
    smoothing: float
    rare_per_layer: tuple[int, ...]

    def describe(self) -> dict[str, Any]:
        return {
            "channel": "trm3_s",
            "n_routine_tokens": int(self.n_tokens),
            "n_routine_traces": int(self.n_traces),
            "rare_threshold": float(self.rare_threshold),
            "smoothing": float(self.smoothing),
            "rare_coordinates": int(self.rare_mask.sum()),
            "rare_coordinates_per_layer": list(self.rare_per_layer),
            "coordinates": LAYERS * EXPERTS,
            "unseen_coordinates": int((self.counts == 0).sum()),
            "q_min": float(self.q.min()),
            "q_max": float(self.q.max()),
        }

    # provenance alias, matching the convention of the frozen v2 scorers
    def to_json(self) -> dict[str, Any]:
        return self.describe()


class TRM3SScorer:
    """Support-expansion channel S of TRM-3; fitted on routine traces only."""

    name = "trm3_s"
    requires_positives = False

    def __init__(
        self,
        window_width: int = WINDOW_WIDTH,
        rare_threshold: float = RARE_THRESHOLD,
        smoothing: float = SMOOTHING,
    ) -> None:
        if int(window_width) <= 0:
            raise ValueError("window_width must be positive")
        if float(smoothing) <= 0.0:
            raise ValueError("smoothing must be positive")
        self.window_width = int(window_width)
        self.rare_threshold = float(rare_threshold)
        self.smoothing = float(smoothing)

    def config(self) -> dict[str, Any]:
        return {
            "window_width": self.window_width,
            "rare_threshold": self.rare_threshold,
            "smoothing": self.smoothing,
            "layers": list(range(LAYERS)),
        }

    def fit(self, routine_traces: Sequence[Any]) -> TRM3SState:
        counts, n_tokens, n_traces = selection_counts(routine_traces)
        q = smoothed_rates(counts, n_tokens, self.smoothing)
        rare_mask = q < self.rare_threshold
        surprisal = torch.where(rare_mask, -q.log(), torch.zeros_like(q))
        return TRM3SState(
            q=q,
            rare_mask=rare_mask,
            surprisal=surprisal,
            counts=counts,
            n_tokens=n_tokens,
            n_traces=n_traces,
            rare_threshold=self.rare_threshold,
            smoothing=self.smoothing,
            rare_per_layer=tuple(int(v) for v in rare_mask.sum(1).tolist()),
        )

    def token_scores(self, state: TRM3SState, trace: Any) -> torch.Tensor:
        """Per-token ``s_t`` [T] (exposed for attribution / the top-3 coordinate report)."""

        ids = check_top_k_ids(trace.top_k_ids)
        if not ids.shape[1]:
            return torch.empty(0, dtype=torch.float64)
        return gather_selected(state.surprisal, ids).sum(dim=(0, 2))

    def top_coordinates(
        self, state: TRM3SState, trace: Any, end: int, n: int = 3
    ) -> list[dict[str, Any]]:
        """Top-``n`` rare coordinates of the window ending at ``end`` (prereg section 2).

        Attribution output only; the contributions sum to the window score ``S_t``.
        """

        ids = check_top_k_ids(trace.top_k_ids)
        bounds = causal_window_bounds(int(ids.shape[1]), end, self.window_width)
        if bounds is None:
            return []
        start, stop = bounds
        return top_coordinate_contributions(
            ids, state.surprisal, start, stop, 1.0 / float(self.window_width), n
        )


    def score(self, state: TRM3SState, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        per_token = self.token_scores(state, trace)
        if per_token.numel() < self.window_width:
            return empty_stream()
        ends, means = window_means(per_token[:, None], self.window_width)
        return means[:, 0].contiguous(), ends


@register("trm3_s")
def _build(config: dict[str, Any]) -> TRM3SScorer:
    return TRM3SScorer(**config)
