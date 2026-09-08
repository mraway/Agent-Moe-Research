"""Proposal 1: WGM -- Window-Geometry Manifold (spec section 3).

One scorer with three departure metrics over the same causal window feature
(the w-token top-8 selection-rate vector, spec 3.3):

* ``g1`` -- whitened squared distance to the routine centre (rank 0);
* ``g2`` -- low-rank PCA residual energy, rank ``r`` chosen by a routine-only
  held-out reconstruction-error elbow rule (``rank="auto"``, r <= 16) or fixed;
* ``g3`` -- mean distance to the 10 nearest routine windows in a PCA-32 space.

Design axes (spec 3.3): layer band, window width, routine definition (handled by
the harness), centre conditioning (global vs shrunk per-workflow mean), and the
``sqrt`` (Hellinger-geometry) transform of the selection rates.

Everything is fitted on routine traces only.  Inference uses the decode top-8
routing of tokens <= t plus (for ``centre="workflow"``) the deployment-visible
``workflow`` field; never a label, boundary, domain or channel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

import torch

from research_v2.features import (
    ALL_LAYERS,
    MIDDLE_LATE_LAYERS,
    MIDDLE_LAYERS,
    selection_rate_windows,
)
from research_v2.scorers import register

LAYER_BANDS: dict[str, tuple[int, ...]] = {
    "middle": MIDDLE_LAYERS,
    "middle_late": MIDDLE_LATE_LAYERS,
    "all": ALL_LAYERS,
}

# Preregistered constants (docs/research_v2/wgm_prereg.md sections 2-3).
RANK_GRID: tuple[int, ...] = (4, 8, 16)
ELBOW_TOLERANCE = 0.10
RANK_HOLDOUT_STRIDE = 3
DIAGNOSTIC_RANKS: tuple[int, ...] = (1, 2, 4, 8, 16, 32, 64, 128)


@dataclass
class WGMState:
    mu: torch.Tensor  # [D]
    sd: torch.Tensor  # [D]
    centre: torch.Tensor  # [D]
    workflow_centres: dict[str, torch.Tensor] = field(default_factory=dict)
    components: torch.Tensor | None = None  # [D, r] PCA directions (g2 / g3)
    reference: torch.Tensor | None = None  # [N, 32] PCA-32 routine reference (g3)
    rank: int | None = None
    rank_selection: dict[str, Any] = field(default_factory=dict)
    window_count: int = 0
    trace_count: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "window_count": self.window_count,
            "trace_count": self.trace_count,
            "rank": self.rank,
            "rank_selection": self.rank_selection,
            "workflow_centre_count": len(self.workflow_centres),
            "reference_size": None if self.reference is None else int(self.reference.shape[0]),
        }


def _pca_components(centred: torch.Tensor, rank: int) -> torch.Tensor:
    """Top-``rank`` eigenvectors of the covariance of ``centred`` [N, D] -> [D, rank]."""

    dims = centred.shape[1]
    rank = max(1, min(int(rank), dims))
    covariance = (centred.T @ centred) / max(1, centred.shape[0] - 1)
    covariance = 0.5 * (covariance + covariance.T)
    values, vectors = torch.linalg.eigh(covariance.to(torch.float64))
    order = torch.argsort(values, descending=True)[:rank]
    return vectors[:, order].to(centred.dtype).contiguous()


def _residual_energy(centred: torch.Tensor, components: torch.Tensor) -> torch.Tensor:
    total = (centred**2).sum(1)
    projected = centred @ components
    return (total - (projected**2).sum(1)).clamp_min(0.0)


class WGMScorer:
    """Window-geometry departure score; fitted on routine windows only."""

    name = "wgm"
    requires_positives = False

    def __init__(
        self,
        window_width: int = 8,
        layers: str | Sequence[int] = "middle",
        metric: str = "g1",
        rank: int | str = "auto",
        transform: str = "identity",
        centre: str = "global",
        workflow_shrinkage: float = 20.0,
        knn_components: int = 32,
        knn_neighbours: int = 10,
        knn_reference_cap: int = 6000,
        variance_floor: float = 1e-3,
        trace_equal_weight: bool = False,
    ) -> None:
        if metric not in ("g1", "g2", "g3"):
            raise ValueError(f"unknown metric {metric!r}")
        if transform not in ("identity", "sqrt"):
            raise ValueError(f"unknown transform {transform!r}")
        if centre not in ("global", "workflow"):
            raise ValueError(f"unknown centre {centre!r}")
        self.window_width = int(window_width)
        self.layers = (
            tuple(LAYER_BANDS[layers]) if isinstance(layers, str) else tuple(int(v) for v in layers)
        )
        self.layer_band = layers if isinstance(layers, str) else "custom"
        self.metric = metric
        self.rank = rank
        self.transform = transform
        self.centre_mode = centre
        self.workflow_shrinkage = float(workflow_shrinkage)
        self.knn_components = int(knn_components)
        self.knn_neighbours = int(knn_neighbours)
        self.knn_reference_cap = int(knn_reference_cap)
        self.variance_floor = float(variance_floor)
        self.trace_equal_weight = bool(trace_equal_weight)

    # -- configuration --------------------------------------------------------
    def config(self) -> dict[str, Any]:
        return {
            "window_width": self.window_width,
            "layer_band": self.layer_band,
            "layers": list(self.layers),
            "metric": self.metric,
            "rank": self.rank,
            "transform": self.transform,
            "centre": self.centre_mode,
            "workflow_shrinkage": self.workflow_shrinkage,
            "knn_components": self.knn_components,
            "knn_neighbours": self.knn_neighbours,
            "knn_reference_cap": self.knn_reference_cap,
            "variance_floor": self.variance_floor,
            "trace_equal_weight": self.trace_equal_weight,
            "rank_grid": list(RANK_GRID),
            "elbow_tolerance": ELBOW_TOLERANCE,
            "rank_holdout_stride": RANK_HOLDOUT_STRIDE,
        }

    # -- feature extraction ---------------------------------------------------
    def _windows(self, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        ends, windows = selection_rate_windows(trace.top_k_ids, self.window_width, self.layers)
        if windows.shape[0] and self.transform == "sqrt":
            windows = windows.clamp_min(0.0).sqrt()
        return ends, windows

    def _blocks(self, traces: Sequence[Any]) -> tuple[list[torch.Tensor], list[Any]]:
        blocks: list[torch.Tensor] = []
        kept: list[Any] = []
        for trace in traces:
            _, windows = self._windows(trace)
            if windows.shape[0]:
                blocks.append(windows)
                kept.append(trace)
        return blocks, kept

    def _whitening(self, blocks: Sequence[torch.Tensor]) -> tuple[torch.Tensor, torch.Tensor]:
        if self.trace_equal_weight:
            mu = torch.stack([block.mean(0) for block in blocks]).mean(0)
            variances = torch.stack([((block - mu) ** 2).mean(0) for block in blocks]).mean(0)
            sd = variances.sqrt() + self.variance_floor
        else:
            matrix = torch.cat(list(blocks))
            mu = matrix.mean(0)
            sd = matrix.std(0) + self.variance_floor
        return mu, sd

    # -- rank selection (routine-only, prereg section 3) ----------------------
    def _select_rank(self, blocks: Sequence[torch.Tensor], traces: Sequence[Any]) -> dict[str, Any]:
        order = sorted(range(len(traces)), key=lambda i: (traces[i].pair_group_id, traces[i].trace_id))
        holdout = [order[i] for i in range(len(order)) if i % RANK_HOLDOUT_STRIDE == 0]
        fit_idx = [order[i] for i in range(len(order)) if i % RANK_HOLDOUT_STRIDE != 0]
        if not holdout or not fit_idx:
            return {"rank": max(RANK_GRID), "reason": "insufficient routine traces for holdout"}

        fit_blocks = [blocks[i] for i in fit_idx]
        mu, sd = self._whitening(fit_blocks)
        fit_matrix = (torch.cat(fit_blocks) - mu) / sd
        centre = fit_matrix.mean(0)
        fit_centred = fit_matrix - centre
        hold_centred = (torch.cat([blocks[i] for i in holdout]) - mu) / sd - centre

        max_rank = max(max(RANK_GRID), max(r for r in DIAGNOSTIC_RANKS if r <= fit_centred.shape[1]))
        components = _pca_components(fit_centred, max_rank)
        curve: dict[int, float] = {}
        for rank in sorted(set(RANK_GRID) | {r for r in DIAGNOSTIC_RANKS if r <= components.shape[1]}):
            curve[rank] = float(_residual_energy(hold_centred, components[:, :rank]).mean())

        chosen = max(RANK_GRID)
        reductions: dict[int, float] = {}
        for index, rank in enumerate(RANK_GRID[:-1]):
            nxt = RANK_GRID[index + 1]
            reduction = (curve[rank] - curve[nxt]) / curve[rank] if curve[rank] > 0 else 0.0
            reductions[rank] = float(reduction)
            if reduction < ELBOW_TOLERANCE and chosen == max(RANK_GRID):
                chosen = rank
        return {
            "rank": int(chosen),
            "grid": list(RANK_GRID),
            "elbow_tolerance": ELBOW_TOLERANCE,
            "holdout_traces": len(holdout),
            "fit_traces": len(fit_idx),
            "holdout_windows": int(hold_centred.shape[0]),
            "reconstruction_error": {str(k): v for k, v in sorted(curve.items())},
            "relative_reduction": {str(k): v for k, v in sorted(reductions.items())},
            "elbow_found": chosen != max(RANK_GRID),
        }

    # -- fit / score ----------------------------------------------------------
    def fit(self, routine_traces: Sequence[Any]) -> WGMState:
        blocks, kept = self._blocks(routine_traces)
        if not blocks:
            raise ValueError("no routine windows available for fitting")

        rank_selection: dict[str, Any] = {}
        rank: int | None = None
        if self.metric == "g2":
            if self.rank == "auto":
                rank_selection = self._select_rank(blocks, kept)
                rank = int(rank_selection["rank"])
            else:
                rank = int(self.rank)
                rank_selection = {"rank": rank, "reason": "fixed by configuration"}

        mu, sd = self._whitening(blocks)
        matrix = (torch.cat(blocks) - mu) / sd
        centre = matrix.mean(0)

        workflow_centres: dict[str, torch.Tensor] = {}
        if self.centre_mode == "workflow":
            sums: dict[str, torch.Tensor] = {}
            counts: dict[str, int] = {}
            offset = 0
            for block, trace in zip(blocks, kept):
                block_z = matrix[offset : offset + block.shape[0]]
                offset += block.shape[0]
                key = trace.workflow
                sums[key] = sums.get(key, torch.zeros_like(centre)) + block_z.mean(0)
                counts[key] = counts.get(key, 0) + 1
            for key, total in sums.items():
                n = counts[key]
                local = total / n
                weight = n / (n + self.workflow_shrinkage)
                workflow_centres[key] = weight * local + (1.0 - weight) * centre

        components: torch.Tensor | None = None
        reference: torch.Tensor | None = None
        centred = matrix - centre
        if self.metric == "g2":
            components = _pca_components(centred, int(rank))
        elif self.metric == "g3":
            components = _pca_components(centred, self.knn_components)
            projected = centred @ components
            if projected.shape[0] > self.knn_reference_cap:
                index = torch.linspace(
                    0, projected.shape[0] - 1, self.knn_reference_cap
                ).round().long()
                index = torch.unique(index)
                projected = projected[index]
            reference = projected.contiguous()

        return WGMState(
            mu=mu,
            sd=sd,
            centre=centre,
            workflow_centres=workflow_centres,
            components=components,
            reference=reference,
            rank=rank,
            rank_selection=rank_selection,
            window_count=int(matrix.shape[0]),
            trace_count=len(blocks),
        )

    def score(self, state: WGMState, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        ends, windows = self._windows(trace)
        if not ends.numel():
            return torch.empty(0), ends
        z = (windows - state.mu) / state.sd
        centre = state.centre
        if self.centre_mode == "workflow":
            centre = state.workflow_centres.get(trace.workflow, state.centre)
        centred = z - centre

        if self.metric == "g1":
            return (centred**2).sum(1), ends
        if self.metric == "g2":
            assert state.components is not None
            return _residual_energy(centred, state.components), ends

        assert state.components is not None and state.reference is not None
        projected = centred @ state.components
        distances = torch.cdist(projected, state.reference)
        k = min(self.knn_neighbours, distances.shape[1])
        nearest = distances.topk(k, dim=1, largest=False).values
        return nearest.mean(1), ends


@register("wgm")
def _build(config: dict[str, Any]) -> WGMScorer:
    return WGMScorer(**config)
