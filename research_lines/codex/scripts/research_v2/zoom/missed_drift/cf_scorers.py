"""Counterfactual scorer variants for the missed-drift zoom (diagnostics only).

None of these is a new candidate; they exist to test which misses a specific
design change would recover and what it costs in false alarms.
"""

from __future__ import annotations

from typing import Any, Sequence

import torch

from research_v2.features import selection_rate_windows, window_means
from research_v2.scorers import register
from research_v2.scorers.pdm import PdmScorer
from research_v2.scorers.wgm import LAYER_BANDS as WGM_BANDS

BANDS = dict(WGM_BANDS)
BANDS["late"] = tuple(range(11, 16))
BANDS["middle_late"] = tuple(range(5, 16))


def _prob_windows(trace, width: int, layers: Sequence[int]) -> tuple[torch.Tensor, torch.Tensor]:
    probs = trace.probabilities()[list(layers), :, :]  # [L, T, 64]
    flat = probs.permute(1, 0, 2).reshape(probs.shape[1], -1).to(torch.float32)
    return window_means(flat, width)


class WgmVariant:
    """G1 whitened distance with a selectable feature and per-layer aggregation."""

    requires_positives = False

    def __init__(
        self,
        window_width: int = 8,
        layers: str = "middle_late",
        feature: str = "selection",  # selection | probability
        aggregate: str = "sum",  # sum (= G1) | maxlayer
        variance_floor: float = 1e-3,
    ) -> None:
        self.window_width = int(window_width)
        self.band = layers
        self.layers = tuple(BANDS[layers]) if isinstance(layers, str) else tuple(layers)
        self.feature = feature
        self.aggregate = aggregate
        self.variance_floor = float(variance_floor)

    def config(self) -> dict[str, Any]:
        return {
            "window_width": self.window_width,
            "layers": self.band,
            "layer_list": list(self.layers),
            "feature": self.feature,
            "aggregate": self.aggregate,
        }

    def _windows(self, trace):
        if self.feature == "selection":
            return selection_rate_windows(trace.top_k_ids, self.window_width, self.layers)
        return _prob_windows(trace, self.window_width, self.layers)

    def fit(self, routine_traces):
        blocks = []
        for trace in routine_traces:
            _, w = self._windows(trace)
            if w.shape[0]:
                blocks.append(w)
        matrix = torch.cat(blocks)
        mu = matrix.mean(0)
        sd = matrix.std(0) + self.variance_floor
        z = (matrix - mu) / sd
        centre = z.mean(0)
        state = {"mu": mu, "sd": sd, "centre": centre}
        if self.aggregate == "maxlayer":
            per = ((z - centre) ** 2).reshape(z.shape[0], len(self.layers), 64).sum(2)
            state["layer_mu"] = per.mean(0)
            state["layer_sd"] = per.std(0) + 1e-6
        return state

    def score(self, state, trace):
        ends, w = self._windows(trace)
        if not ends.numel():
            return torch.empty(0, dtype=torch.float64), ends
        centred = (w - state["mu"]) / state["sd"] - state["centre"]
        if self.aggregate == "sum":
            return (centred**2).sum(1).to(torch.float64), ends
        per = (centred**2).reshape(centred.shape[0], len(self.layers), 64).sum(2)
        z = (per - state["layer_mu"]) / state["layer_sd"]
        return z.amax(1).to(torch.float64), ends


@register("zoom_wgm_variant")
def _build_wgm(config: dict[str, Any]) -> WgmVariant:
    return WgmVariant(**config)


class PdmMaxLink(PdmScorer):
    """D1 depth chain read as the max standardized single-link surprisal."""

    def __init__(self, window_width: int = 4, layers: str = "middle", **kw) -> None:
        super().__init__(window_width=window_width, model="d1", layers=layers, **kw)
        self._link_stats: dict[str, torch.Tensor] = {}

    def _links(self, state, top_k_ids):
        top1 = self._top1(top_k_ids)
        rows = [-state.log_depth_initial[top1[0]]]
        for i in range(top1.shape[0] - 1):
            rows.append(-state.log_depth[i][top1[i], top1[i + 1]])
        return torch.stack(rows)  # [L, T]

    def fit(self, routine_traces):
        state = super().fit(routine_traces)
        blocks = [self._links(state, t.top_k_ids) for t in routine_traces if t.top_k_ids.shape[1]]
        pooled = torch.cat(blocks, dim=1)
        self._link_stats = {"mu": pooled.mean(1), "sd": pooled.std(1) + 1e-6}
        return state

    def score(self, state, trace):
        top_k_ids = trace.top_k_ids
        if top_k_ids.shape[1] < self.window_width:
            return torch.empty(0, dtype=torch.float64), torch.empty(0, dtype=torch.long)
        links = self._links(state, top_k_ids)
        z = (links - self._link_stats["mu"].unsqueeze(1)) / self._link_stats["sd"].unsqueeze(1)
        ends, means = window_means(z.T.contiguous(), self.window_width)  # [nwin, L]
        return means.amax(1).to(torch.float64), ends

    def config(self) -> dict[str, Any]:
        base = super().config()
        base["aggregate"] = "maxlink"
        return base


@register("zoom_pdm_maxlink")
def _build_pdm_max(config: dict[str, Any]) -> PdmMaxLink:
    return PdmMaxLink(**config)


class PdmBand(PdmScorer):
    """D1 with an arbitrary layer list (adds a 'late' band)."""

    def __init__(self, window_width: int = 4, layers: str = "middle", **kw) -> None:
        resolved = BANDS[layers] if isinstance(layers, str) else tuple(layers)
        super().__init__(window_width=window_width, model="d1", layers=resolved, **kw)
        self.layers_name = layers if isinstance(layers, str) else "custom"


@register("zoom_pdm_band")
def _build_pdm_band(config: dict[str, Any]) -> PdmBand:
    return PdmBand(**config)
