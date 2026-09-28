"""Built-in reference scorer G1: whitened distance of the window selection-rate vector.

Spec 3.3 (G1, rank 0): the routine manifold is the whitened region occupied by the
w-token top-8 selection-rate windows of routine traffic; the departure score is the
squared whitened Euclidean distance to the routine centre.  Fitted on routine only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import torch

from research_v2.features import (
    ALL_LAYERS,
    MIDDLE_LATE_LAYERS,
    MIDDLE_LAYERS,
    selection_rate_windows,
)
from research_v2.scorers import register

LAYER_BANDS = {
    "middle": MIDDLE_LAYERS,
    "middle_late": MIDDLE_LATE_LAYERS,
    "all": ALL_LAYERS,
}


@dataclass
class WhitenedState:
    mu: torch.Tensor
    sd: torch.Tensor
    centre: torch.Tensor
    window_count: int
    trace_count: int


class WhitenedDistanceScorer:
    """score_t = || (x_t - mu)/sd - centre ||^2 over routine-whitened coordinates."""

    name = "g1_whitened_distance"
    requires_positives = False

    def __init__(
        self,
        window_width: int = 8,
        layers: str | Sequence[int] = "middle",
        variance_floor: float = 1e-3,
        trace_equal_weight: bool = False,
    ) -> None:
        self.window_width = int(window_width)
        self.layers = tuple(LAYER_BANDS[layers]) if isinstance(layers, str) else tuple(int(v) for v in layers)
        self.variance_floor = float(variance_floor)
        self.trace_equal_weight = bool(trace_equal_weight)

    def config(self) -> dict[str, Any]:
        return {
            "window_width": self.window_width,
            "layers": list(self.layers),
            "variance_floor": self.variance_floor,
            "trace_equal_weight": self.trace_equal_weight,
        }

    def fit(self, routine_traces: Sequence[Any]) -> WhitenedState:
        blocks: list[torch.Tensor] = []
        for trace in routine_traces:
            _, windows = selection_rate_windows(trace.top_k_ids, self.window_width, self.layers)
            if windows.shape[0]:
                blocks.append(windows)
        if not blocks:
            raise ValueError("no routine windows available for fitting")
        if self.trace_equal_weight:
            means = torch.stack([block.mean(0) for block in blocks])
            mu = means.mean(0)
            variances = torch.stack(
                [((block - mu) ** 2).mean(0) for block in blocks]
            ).mean(0)
            sd = variances.sqrt() + self.variance_floor
            standardized = torch.cat([(block - mu) / sd for block in blocks])
        else:
            matrix = torch.cat(blocks)
            mu = matrix.mean(0)
            sd = matrix.std(0) + self.variance_floor
            standardized = (matrix - mu) / sd
        centre = standardized.mean(0)
        return WhitenedState(
            mu=mu,
            sd=sd,
            centre=centre,
            window_count=int(sum(block.shape[0] for block in blocks)),
            trace_count=len(blocks),
        )

    def score(self, state: WhitenedState, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        ends, windows = selection_rate_windows(trace.top_k_ids, self.window_width, self.layers)
        if not ends.numel():
            return torch.empty(0), ends
        z = (windows - state.mu) / state.sd - state.centre
        return (z**2).sum(1), ends


@register("g1_whitened_distance")
def _build(config: dict[str, Any]) -> WhitenedDistanceScorer:
    return WhitenedDistanceScorer(**config)
