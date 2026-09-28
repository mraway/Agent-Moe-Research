"""Proposal 3: Path-Dynamics Manifold (PDM) -- routing surprisal under routine-fitted
low-order Markov models over expert paths and expert transitions (spec section 5).

Three generative count models, all fitted on routine decode tokens only with additive
smoothing alpha = 0.5 (spec 5.3):

* ``D1`` depth chain     P(e_l0) * prod_l P(e_{l+1} | e_l) over the top-1 expert of each layer
* ``D2`` time chain      per layer P(e_l(0)) * prod_t P(e_l(t) | e_l(t-1))
* ``D3`` kernel-smoothed weighted top-8 set transition (the preregistered choice among the
  two variants spec 5.3 offers; the top-2 joint state would need a 4096x4096 table per
  layer against ~10-15k routine tokens)

Each surprisal is standardized by routine-token mean/sd; ``d1d2`` is the sum of the two
standardized streams (spec 5.3 "D1+D2 sum").  The per-token stream is then averaged over a
causal window of width w (w = 1 is the per-token reading).  The harness applies the
position-bucket standardization and the conformal threshold on top.

Causality: ``score`` reads only ``trace.top_k_ids[:, <= t, :]``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import torch

from research_v2.features import ALL_LAYERS, MIDDLE_LATE_LAYERS, MIDDLE_LAYERS, window_means
from research_v2.scorers import register

EXPERTS = 64
TOP_K = 8
LAYER_BANDS = {
    "all": ALL_LAYERS,
    "middle": MIDDLE_LAYERS,
    "middle_late": MIDDLE_LATE_LAYERS,
}
MODELS = ("d1", "d2", "d1d2", "d3")


def _log_conditional(counts: torch.Tensor, smoothing: float) -> torch.Tensor:
    """Row-normalized log P(b|a) of a [64, 64] count table with additive smoothing."""

    smoothed = counts + smoothing
    return torch.log(smoothed / smoothed.sum(dim=1, keepdim=True))


def _log_marginal(counts: torch.Tensor, smoothing: float) -> torch.Tensor:
    smoothed = counts + smoothing
    return torch.log(smoothed / smoothed.sum())


def _conditional(counts: torch.Tensor, smoothing: float) -> torch.Tensor:
    smoothed = counts + smoothing
    return smoothed / smoothed.sum(dim=1, keepdim=True)


def _marginal(counts: torch.Tensor, smoothing: float) -> torch.Tensor:
    smoothed = counts + smoothing
    return smoothed / smoothed.sum()


def _pair_counts(previous: torch.Tensor, current: torch.Tensor) -> torch.Tensor:
    """[64, 64] counts of (previous -> current) index pairs."""

    flat = previous.reshape(-1) * EXPERTS + current.reshape(-1)
    return torch.bincount(flat, minlength=EXPERTS * EXPERTS).reshape(EXPERTS, EXPERTS).double()


def _set_pair_counts(previous: torch.Tensor, current: torch.Tensor) -> torch.Tensor:
    """Weighted [64, 64] counts of all top-k set pairs, weight 1/(k*k) per token."""

    if previous.shape[0] == 0:
        return torch.zeros((EXPERTS, EXPERTS), dtype=torch.float64)
    k = previous.shape[1]
    pairs = previous.unsqueeze(2) * EXPERTS + current.unsqueeze(1)  # [T-1, k, k]
    weights = torch.full((pairs.numel(),), 1.0 / float(k * k), dtype=torch.float64)
    return (
        torch.bincount(pairs.reshape(-1), weights=weights, minlength=EXPERTS * EXPERTS)
        .reshape(EXPERTS, EXPERTS)
        .double()
    )


@dataclass
class PdmState:
    log_depth: torch.Tensor | None  # [len(layers)-1, 64, 64]
    log_depth_initial: torch.Tensor | None  # [64]
    log_time: torch.Tensor | None  # [len(layers), 64, 64]
    log_time_initial: torch.Tensor | None  # [len(layers), 64]
    set_transition: torch.Tensor | None  # [len(layers), 64, 64] probabilities
    set_marginal: torch.Tensor | None  # [len(layers), 64] probabilities
    mean: dict[str, float]
    sd: dict[str, float]
    token_count: int
    trace_count: int
    unseen_depth_fraction: float | None = None
    unseen_time_fraction: float | None = None


class PdmScorer:
    """Routine-fitted routing-dynamics surprisal (spec 5.3)."""

    name = "pdm"
    requires_positives = False

    def __init__(
        self,
        window_width: int = 4,
        model: str = "d1d2",
        layers: str | Sequence[int] = "all",
        smoothing: float = 0.5,
        variance_floor: float = 1e-6,
    ) -> None:
        if model not in MODELS:
            raise ValueError(f"unknown pdm model {model!r}; available: {MODELS}")
        self.window_width = int(window_width)
        self.model = model
        self.layers = (
            tuple(LAYER_BANDS[layers]) if isinstance(layers, str) else tuple(int(v) for v in layers)
        )
        if len(self.layers) < 2:
            raise ValueError("pdm needs at least two layers")
        self.smoothing = float(smoothing)
        self.variance_floor = float(variance_floor)
        self.layers_name = layers if isinstance(layers, str) else "custom"

    def config(self) -> dict[str, Any]:
        return {
            "window_width": self.window_width,
            "model": self.model,
            "layers": list(self.layers),
            "layers_name": self.layers_name,
            "smoothing": self.smoothing,
        }

    # -- fitting -------------------------------------------------------------
    @property
    def _needs_depth(self) -> bool:
        return self.model in ("d1", "d1d2")

    @property
    def _needs_time(self) -> bool:
        return self.model in ("d2", "d1d2")

    def _top1(self, top_k_ids: torch.Tensor) -> torch.Tensor:
        """[len(layers), T] top-1 expert per selected layer."""

        return top_k_ids[list(self.layers), :, 0].long()

    def _topk(self, top_k_ids: torch.Tensor) -> torch.Tensor:
        """[len(layers), T, 8] top-k expert set per selected layer."""

        return top_k_ids[list(self.layers), :, :].long()

    def fit(self, routine_traces: Sequence[Any]) -> PdmState:
        depth = self._needs_depth
        time = self._needs_time
        sets = self.model == "d3"
        layer_count = len(self.layers)
        depth_counts = torch.zeros((layer_count - 1, EXPERTS, EXPERTS), dtype=torch.float64)
        depth_initial = torch.zeros(EXPERTS, dtype=torch.float64)
        time_counts = torch.zeros((layer_count, EXPERTS, EXPERTS), dtype=torch.float64)
        time_initial = torch.zeros((layer_count, EXPERTS), dtype=torch.float64)
        set_counts = torch.zeros((layer_count, EXPERTS, EXPERTS), dtype=torch.float64)
        set_initial = torch.zeros((layer_count, EXPERTS), dtype=torch.float64)
        token_total = 0
        used = 0
        for trace in routine_traces:
            top_k = trace.top_k_ids
            if top_k.shape[1] == 0:
                continue
            used += 1
            token_total += int(top_k.shape[1])
            top1 = self._top1(top_k)
            if depth:
                for index in range(layer_count - 1):
                    depth_counts[index] += _pair_counts(top1[index], top1[index + 1])
                depth_initial += torch.bincount(top1[0], minlength=EXPERTS).double()
            if time:
                for index in range(layer_count):
                    if top1.shape[1] > 1:
                        time_counts[index] += _pair_counts(top1[index, :-1], top1[index, 1:])
                    time_initial[index, int(top1[index, 0])] += 1.0
            if sets:
                topk = self._topk(top_k)
                for index in range(layer_count):
                    if topk.shape[1] > 1:
                        set_counts[index] += _set_pair_counts(topk[index, :-1], topk[index, 1:])
                    set_initial[index] += (
                        torch.bincount(topk[index, 0], minlength=EXPERTS).double() / float(TOP_K)
                    )
        if used == 0:
            raise ValueError("no routine traces available for fitting")

        state = PdmState(
            log_depth=_log_conditional_stack(depth_counts, self.smoothing) if depth else None,
            log_depth_initial=_log_marginal(depth_initial, self.smoothing) if depth else None,
            log_time=_log_conditional_stack(time_counts, self.smoothing) if time else None,
            log_time_initial=(
                torch.stack([_log_marginal(row, self.smoothing) for row in time_initial])
                if time
                else None
            ),
            set_transition=(
                torch.stack([_conditional(table, self.smoothing) for table in set_counts])
                if sets
                else None
            ),
            set_marginal=(
                torch.stack([_marginal(row, self.smoothing) for row in set_initial])
                if sets
                else None
            ),
            mean={},
            sd={},
            token_count=token_total,
            trace_count=used,
        )
        if depth:
            state.unseen_depth_fraction = float((depth_counts == 0).double().mean())
        if time:
            state.unseen_time_fraction = float((time_counts == 0).double().mean())

        # second pass: routine-token mean/sd of every surprisal component
        components: dict[str, list[torch.Tensor]] = {}
        for trace in routine_traces:
            if trace.top_k_ids.shape[1] == 0:
                continue
            for key, values in self._components(state, trace.top_k_ids).items():
                components.setdefault(key, []).append(values)
        for key, blocks in components.items():
            pooled = torch.cat(blocks)
            state.mean[key] = float(pooled.mean())
            state.sd[key] = float(pooled.std()) + self.variance_floor if pooled.numel() > 1 else 1.0
        return state

    # -- scoring -------------------------------------------------------------
    def _components(self, state: PdmState, top_k_ids: torch.Tensor) -> dict[str, torch.Tensor]:
        """Raw per-token surprisals (before standardization), keyed by component name."""

        top1 = self._top1(top_k_ids)
        tokens = top1.shape[1]
        out: dict[str, torch.Tensor] = {}
        if state.log_depth is not None:
            total = state.log_depth_initial[top1[0]].clone()
            for index in range(top1.shape[0] - 1):
                total = total + state.log_depth[index][top1[index], top1[index + 1]]
            out["u"] = -total
        if state.log_time is not None:
            total = torch.zeros(tokens, dtype=torch.float64)
            for index in range(top1.shape[0]):
                row = torch.empty(tokens, dtype=torch.float64)
                row[0] = state.log_time_initial[index][int(top1[index, 0])]
                if tokens > 1:
                    row[1:] = state.log_time[index][top1[index, :-1], top1[index, 1:]]
                total = total + row
            out["v"] = -total
        if state.set_transition is not None:
            topk = self._topk(top_k_ids)
            total = torch.zeros(tokens, dtype=torch.float64)
            for index in range(topk.shape[0]):
                row = torch.empty(tokens, dtype=torch.float64)
                first = topk[index, 0]
                row[0] = torch.log(state.set_marginal[index][first].sum() / float(TOP_K))
                if tokens > 1:
                    previous = topk[index, :-1]  # [T-1, k]
                    current = topk[index, 1:]
                    table = state.set_transition[index]
                    gathered = table[previous.unsqueeze(2), current.unsqueeze(1)]  # [T-1,k,k]
                    row[1:] = torch.log(gathered.mean(dim=(1, 2)))
                total = total + row
            out["d"] = -total
        return out

    def _stream(self, state: PdmState, top_k_ids: torch.Tensor) -> torch.Tensor:
        components = self._components(state, top_k_ids)
        standardized = {
            key: (values - state.mean[key]) / state.sd[key] for key, values in components.items()
        }
        if self.model == "d1":
            return standardized["u"]
        if self.model == "d2":
            return standardized["v"]
        if self.model == "d3":
            return standardized["d"]
        return standardized["u"] + standardized["v"]

    def score(self, state: PdmState, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        top_k_ids = trace.top_k_ids
        if top_k_ids.shape[1] < self.window_width:
            return torch.empty(0, dtype=torch.float64), torch.empty(0, dtype=torch.long)
        stream = self._stream(state, top_k_ids).reshape(-1, 1)
        ends, means = window_means(stream, self.window_width)
        return means.reshape(-1), ends


def _log_conditional_stack(counts: torch.Tensor, smoothing: float) -> torch.Tensor:
    return torch.stack([_log_conditional(table, smoothing) for table in counts])


@register("pdm")
def _build(config: dict[str, Any]) -> PdmScorer:
    return PdmScorer(**config)
