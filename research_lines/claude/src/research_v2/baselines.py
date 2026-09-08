"""Text baselines and supervised references of protocol v2 (spec section 1.9).

* ``oov_fraction`` (T2) -- fraction of window tokens whose id occurs < 5 times in the
  fitting routine pool.  One-class, text-only, no routing.  This is the baseline the
  routing scorers have to beat (spec 1.9, E28).
* ``t1_embedding_knn`` (T1) -- window mean of the OLMoE *static input embedding*
  (local safetensors, offline), kNN-10 novelty in a routine-fitted PCA-32 space.
  One-class, text-only, no routing, no forward pass.
* ``s0_diffmeans`` (S0) -- SUPERVISED REFERENCE ONLY.  Difference-of-means direction
  between drift post-onset windows and the matched benign-control windows of the same
  scenarios, on middle-layer selection-rate features.  It uses drift labels and is
  therefore not a detector; it says what supervision buys.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import torch

from research_v2.features import MIDDLE_LATE_LAYERS, MIDDLE_LAYERS, selection_rate_windows, window_means
from research_v2.io import load_input_embeddings
from research_v2.scorers import register

VOCAB_SIZE = 50304
LAYER_BANDS = {"middle": MIDDLE_LAYERS, "middle_late": MIDDLE_LATE_LAYERS, "all": tuple(range(16))}


# ---------------------------------------------------------------------------
# T2: out-of-vocabulary fraction
# ---------------------------------------------------------------------------


@dataclass
class OovState:
    counts: torch.Tensor
    trace_count: int
    token_count: int


class OovFractionScorer:
    name = "oov_fraction"
    requires_positives = False

    def __init__(self, window_width: int = 8, min_count: int = 5, vocab_size: int = VOCAB_SIZE) -> None:
        self.window_width = int(window_width)
        self.min_count = int(min_count)
        self.vocab_size = int(vocab_size)

    def config(self) -> dict[str, Any]:
        return {
            "window_width": self.window_width,
            "min_count": self.min_count,
            "vocab_size": self.vocab_size,
        }

    def fit(self, routine_traces: Sequence[Any]) -> OovState:
        counts = torch.zeros(self.vocab_size)
        total = 0
        for trace in routine_traces:
            ids = trace.token_ids
            counts.index_add_(0, ids, torch.ones(ids.numel()))
            total += int(ids.numel())
        return OovState(counts=counts, trace_count=len(routine_traces), token_count=total)

    def score(self, state: OovState, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        novel = (state.counts[trace.token_ids] < self.min_count).float().unsqueeze(1)
        ends, means = window_means(novel, self.window_width)
        if not ends.numel():
            return torch.empty(0), ends
        return means[:, 0].contiguous(), ends


@register("oov_fraction")
def _build_oov(config: dict[str, Any]) -> OovFractionScorer:
    return OovFractionScorer(**config)


# ---------------------------------------------------------------------------
# T1: static input-embedding kNN novelty
# ---------------------------------------------------------------------------


@dataclass
class EmbeddingKnnState:
    mean: torch.Tensor
    components: torch.Tensor | None
    reference: torch.Tensor
    window_count: int
    trace_count: int


class EmbeddingKnnScorer:
    name = "t1_embedding_knn"
    requires_positives = False

    def __init__(
        self,
        window_width: int = 8,
        components: int | None = 32,
        neighbours: int = 10,
        reference_cap: int = 6000,
        seed: int = 0,
    ) -> None:
        """``components=None`` (or 0) skips the PCA projection and runs the kNN in the
        full 2048-dimensional embedding-mean space.  The spec prescribes PCA-32; on this
        data that projection destroys the signal (see docs/research_v2/harness_report.md),
        so both variants are provided and reported."""

        self.window_width = int(window_width)
        self.components = None if components in (None, 0) else int(components)
        self.neighbours = int(neighbours)
        self.reference_cap = int(reference_cap)
        self.seed = int(seed)

    def config(self) -> dict[str, Any]:
        return {
            "window_width": self.window_width,
            "components": self.components,
            "neighbours": self.neighbours,
            "reference_cap": self.reference_cap,
            "seed": self.seed,
        }

    def _windows(self, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        embeddings = load_input_embeddings()[trace.token_ids]
        return window_means(embeddings, self.window_width)

    def fit(self, routine_traces: Sequence[Any]) -> EmbeddingKnnState:
        blocks = []
        for trace in routine_traces:
            ends, means = self._windows(trace)
            if ends.numel():
                blocks.append(means)
        if not blocks:
            raise ValueError("no routine windows available for fitting")
        matrix = torch.cat(blocks)
        mean = matrix.mean(0)
        centred = matrix - mean
        generator = torch.Generator().manual_seed(self.seed)
        order = torch.randperm(centred.shape[0], generator=generator)
        subset = centred[order[: self.reference_cap]]
        if self.components is None:
            components = None
            reference = subset
        else:
            _, _, vt = torch.linalg.svd(subset, full_matrices=False)
            components = vt[: self.components]
            reference = subset @ components.T
        return EmbeddingKnnState(
            mean=mean,
            components=components,
            reference=reference.contiguous(),
            window_count=int(matrix.shape[0]),
            trace_count=len(blocks),
        )

    def score(self, state: EmbeddingKnnState, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        ends, means = self._windows(trace)
        if not ends.numel():
            return torch.empty(0), ends
        centred = means - state.mean
        projected = centred if state.components is None else centred @ state.components.T
        distances = torch.cdist(projected, state.reference)
        k = min(self.neighbours, state.reference.shape[0])
        return distances.topk(k, dim=1, largest=False).values.mean(1), ends


@register("t1_embedding_knn")
def _build_t1(config: dict[str, Any]) -> EmbeddingKnnScorer:
    return EmbeddingKnnScorer(**config)


# ---------------------------------------------------------------------------
# S0: supervised difference-of-means reference (NOT a one-class detector)
# ---------------------------------------------------------------------------


@dataclass
class DiffMeansState:
    mu: torch.Tensor
    sd: torch.Tensor
    direction: torch.Tensor
    drift_trace_count: int
    reference_trace_count: int


class DiffMeansReference:
    name = "s0_diffmeans"
    requires_positives = True

    def __init__(
        self,
        window_width: int = 8,
        layers: str | Sequence[int] = "middle",
        variance_floor: float = 1e-3,
    ) -> None:
        self.window_width = int(window_width)
        self.layers = tuple(LAYER_BANDS[layers]) if isinstance(layers, str) else tuple(int(v) for v in layers)
        self.variance_floor = float(variance_floor)

    def config(self) -> dict[str, Any]:
        return {
            "window_width": self.window_width,
            "layers": list(self.layers),
            "variance_floor": self.variance_floor,
            "supervised_reference": True,
        }

    def fit(self, fit_traces: Sequence[Any]) -> DiffMeansState:
        routine = [trace for trace in fit_traces if not trace.positive and trace.arm in ("clean", "benign_control")]
        blocks = []
        for trace in routine:
            _, windows = selection_rate_windows(trace.top_k_ids, self.window_width, self.layers)
            if windows.shape[0]:
                blocks.append(windows)
        matrix = torch.cat(blocks)
        mu = matrix.mean(0)
        sd = matrix.std(0) + self.variance_floor

        drift = [trace for trace in fit_traces if trace.positive and trace.evidence_onset is not None]
        drift_groups = {trace.pair_group_id for trace in drift}
        matched = [
            trace
            for trace in fit_traces
            if trace.arm == "benign_control" and trace.pair_group_id in drift_groups
        ]
        drift_windows = []
        for trace in drift:
            ends, windows = selection_rate_windows(trace.top_k_ids, self.window_width, self.layers)
            mask = ends >= int(trace.evidence_onset)
            if bool(mask.any()):
                drift_windows.append(((windows[mask] - mu) / sd).mean(0))
        reference_windows = []
        for trace in matched:
            _, windows = selection_rate_windows(trace.top_k_ids, self.window_width, self.layers)
            if windows.shape[0]:
                reference_windows.append(((windows - mu) / sd).mean(0))
        if not drift_windows or not reference_windows:
            raise ValueError("s0_diffmeans needs drift and matched benign traces in the fitting set")
        direction = torch.stack(drift_windows).mean(0) - torch.stack(reference_windows).mean(0)
        direction = direction / (direction.norm() + 1e-12)
        return DiffMeansState(
            mu=mu,
            sd=sd,
            direction=direction,
            drift_trace_count=len(drift_windows),
            reference_trace_count=len(reference_windows),
        )

    def score(self, state: DiffMeansState, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        ends, windows = selection_rate_windows(trace.top_k_ids, self.window_width, self.layers)
        if not ends.numel():
            return torch.empty(0), ends
        z = (windows - state.mu) / state.sd
        return z @ state.direction, ends


@register("s0_diffmeans")
def _build_s0(config: dict[str, Any]) -> DiffMeansReference:
    return DiffMeansReference(**config)
