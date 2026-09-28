"""Proposal 2 -- CM (Conditional Manifold), spec section 4.

The routine manifold is treated as a *conditional* distribution: given the current
token (C1), the locally visible text (C2) and the prefill context (C3), routine routing
is much tighter than its unconditional distribution.  The departure score is the
whitened energy of the conditional residual

    r_t = ind_t - E[ind | conditioning]           (per-token, [L*64] top-8 indicator)
    rbar_e = mean of r over the causal window [e-w+1, e]
    z_e    = (rbar_e - mu_R) / sd_R               (routine residual-window whitening)
    e_e    = ||z_e||^2                            (optionally / exp(g(prefill)) for C3)

Everything is fitted on routine traces only.  Nothing here reads ``positive``,
``evidence_onset``, ``completion_boundary``, ``scenario_domain`` or ``channel``.

Preregistration: docs/research_v2/cm_prereg.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import torch
from safetensors import safe_open
from safetensors.torch import load_file, save_file

from research_v2 import io as rio
from research_v2.features import ALL_LAYERS, MIDDLE_LATE_LAYERS, MIDDLE_LAYERS, window_means
from research_v2.scorers import register

EARLY_LAYERS = tuple(range(0, 5))
LATE_LAYERS = tuple(range(11, 16))
LAYER_BANDS = {
    "middle": MIDDLE_LAYERS,
    "middle_late": MIDDLE_LATE_LAYERS,
    "all": ALL_LAYERS,
    "early": EARLY_LAYERS,
    "late": LATE_LAYERS,
}

EXPERTS = 64
VOCAB_SIZE = 50304
PREFILL_CACHE = rio.RESEARCH_V2_ROOT / "cm" / "_prefill_cache"


# ---------------------------------------------------------------------------
# per-token top-8 indicator
# ---------------------------------------------------------------------------


def indicator(top_k_ids: torch.Tensor, layers: Sequence[int]) -> torch.Tensor:
    """[T, len(layers)*64] float top-8 selection indicator of every decode token."""

    selected = top_k_ids[list(layers)].long()  # [L, T, K]
    layer_count, tokens, _ = selected.shape
    out = torch.zeros(tokens, layer_count, EXPERTS)
    out.scatter_(2, selected.permute(1, 0, 2), 1.0)
    return out.reshape(tokens, layer_count * EXPERTS)


# ---------------------------------------------------------------------------
# C3: prefill routing summary (cached; the frozen cache holds decode routing only)
# ---------------------------------------------------------------------------


def _final_prefill_row(trace_dir: Path) -> dict[str, Any]:
    trace = json.loads((trace_dir / "trace.json").read_text(encoding="utf-8"))
    generations = [event for event in trace["events"] if event["kind"] == "model_generation"]
    if not generations:
        raise ValueError(f"{trace_dir} has no model generation")
    final_step = int(generations[-1]["agent_step"])
    rows = [
        json.loads(line)
        for line in (trace_dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    decode_steps = [
        row["step_index"]
        for row in rows
        if row["phase"] == "decode" and any(int(s) == final_step for s in row["agent_steps"])
    ]
    if not decode_steps:
        raise ValueError(f"{trace_dir} has no final-generation decode row")
    first_decode = min(decode_steps)
    prefill = [row for row in rows if row["phase"] == "prefill" and row["step_index"] < first_decode]
    if not prefill:
        raise ValueError(f"{trace_dir} has no prefill row before the final generation")
    return max(prefill, key=lambda row: row["step_index"])


def prefill_summary(trace: Any, cache_dir: Path = PREFILL_CACHE) -> torch.Tensor:
    """Mean top-8 selection rate of the final generation's prefill, flattened [16*64]."""

    trace_dir = Path(trace.record.trace_dir)
    cache_file = cache_dir / trace.batch / f"{trace.trace_id}.safetensors"
    if cache_file.exists():
        return load_file(cache_file)["prefill_selection_mean"].reshape(-1)
    row = _final_prefill_row(trace_dir)
    shard = (trace_dir / row["tensor_file"]).resolve()
    if trace_dir.resolve() not in shard.parents:
        raise ValueError("routing tensor escapes trace directory")
    with safe_open(shard, framework="pt", device="cpu") as handle:
        top_k_ids = handle.get_tensor("top_k_ids").long()
    layer_count, tokens, _ = top_k_ids.shape
    mean = torch.zeros(layer_count, EXPERTS)
    ones = torch.ones(top_k_ids.shape[1] * top_k_ids.shape[2])
    for layer in range(layer_count):
        mean[layer].index_add_(0, top_k_ids[layer].reshape(-1), ones)
    mean /= float(tokens)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    save_file({"prefill_selection_mean": mean.contiguous()}, cache_file)
    return mean.reshape(-1)


# ---------------------------------------------------------------------------
# ridge helpers (routine-only lambda selection by held-out R^2)
# ---------------------------------------------------------------------------


def _solve_ridge(gram: torch.Tensor, cross: torch.Tensor, lam: float) -> torch.Tensor:
    dim = gram.shape[0]
    return torch.linalg.solve(gram + lam * torch.eye(dim, dtype=gram.dtype), cross)


def _r2(prediction: torch.Tensor, target: torch.Tensor) -> float:
    residual = ((target - prediction) ** 2).sum()
    total = ((target - target.mean(0, keepdim=True)) ** 2).sum()
    if float(total) <= 0:
        return float("nan")
    return float(1.0 - residual / total)


def _halves(traces: Sequence[Any]) -> tuple[list[int], list[int]]:
    """Split routine traces into two halves by pair_group order (routine-only)."""

    groups = sorted({trace.pair_group_id for trace in traces})
    rank = {group: index for index, group in enumerate(groups)}
    first = [i for i, trace in enumerate(traces) if rank[trace.pair_group_id] % 2 == 0]
    second = [i for i, trace in enumerate(traces) if rank[trace.pair_group_id] % 2 == 1]
    if not first or not second:
        first = list(range(0, len(traces), 2))
        second = list(range(1, len(traces), 2))
    return first, second


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------


@dataclass
class CmState:
    mu: torch.Tensor
    table: torch.Tensor  # [rows, D]; row 0 = mu fallback
    token_row: torch.Tensor  # [vocab] long, 0 = fallback
    covered_row: torch.Tensor  # [rows] bool
    window_mean: torch.Tensor
    window_sd: torch.Tensor
    pca_mean: torch.Tensor | None = None
    pca_basis: torch.Tensor | None = None
    ridge_x_mean: torch.Tensor | None = None
    ridge_y_mean: torch.Tensor | None = None
    ridge_beta: torch.Tensor | None = None
    c3_mean: torch.Tensor | None = None
    c3_basis: torch.Tensor | None = None
    c3_workflows: tuple[str, ...] = ()
    c3_beta: torch.Tensor | None = None
    c3_constant: float = 0.0
    diagnostics: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# scorer
# ---------------------------------------------------------------------------


class ConditionalManifoldScorer:
    name = "cm"
    requires_positives = False

    def __init__(
        self,
        window_width: int = 8,
        layers: str | Sequence[int] = "middle",
        conditioning: str = "c1",
        use_c3: bool = False,
        uncovered: str = "mu",
        min_count: int = 5,
        shrinkage_m: float = 5.0,
        variance_floor: float = 1e-3,
        pca_components: int = 256,
        pca_token_cap: int = 40000,
        ema_spans: Sequence[int] = (8, 32),
        ridge_lambdas: Sequence[float] = (1.0, 10.0, 100.0, 1000.0, 10000.0),
        c3_components: int = 32,
        c3_lambdas: Sequence[float] = (0.1, 1.0, 10.0, 100.0, 1000.0),
        seed: int = 0,
    ) -> None:
        if conditioning not in ("c1", "c1c2"):
            raise ValueError(f"unknown conditioning: {conditioning}")
        if uncovered not in ("mu", "zero"):
            raise ValueError(f"unknown uncovered policy: {uncovered}")
        self.window_width = int(window_width)
        self.layers = tuple(LAYER_BANDS[layers]) if isinstance(layers, str) else tuple(int(v) for v in layers)
        self.layers_name = layers if isinstance(layers, str) else "custom"
        self.conditioning = conditioning
        self.use_c3 = bool(use_c3)
        self.uncovered = uncovered
        self.min_count = int(min_count)
        self.shrinkage_m = float(shrinkage_m)
        self.variance_floor = float(variance_floor)
        self.pca_components = int(pca_components)
        self.pca_token_cap = int(pca_token_cap)
        self.ema_spans = tuple(int(v) for v in ema_spans)
        self.ridge_lambdas = tuple(float(v) for v in ridge_lambdas)
        self.c3_components = int(c3_components)
        self.c3_lambdas = tuple(float(v) for v in c3_lambdas)
        self.seed = int(seed)

    def config(self) -> dict[str, Any]:
        return {
            "window_width": self.window_width,
            "layers": self.layers_name,
            "layer_ids": list(self.layers),
            "conditioning": self.conditioning,
            "use_c3": self.use_c3,
            "uncovered": self.uncovered,
            "min_count": self.min_count,
            "shrinkage_m": self.shrinkage_m,
            "variance_floor": self.variance_floor,
            "pca_components": self.pca_components,
            "ema_spans": list(self.ema_spans),
            "ridge_lambdas": list(self.ridge_lambdas),
            "c3_components": self.c3_components,
            "c3_lambdas": list(self.c3_lambdas),
        }

    # -- C2 input features ---------------------------------------------------

    def _raw_text_blocks(self, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        """(embeddings [T, H], ema blocks [T, H*len(spans)]) -- causal, offline."""

        embeddings = rio.load_input_embeddings()[trace.token_ids]
        blocks = []
        for span in self.ema_spans:
            alpha = 1.0 / float(span)
            ema = torch.empty_like(embeddings)
            state = embeddings[0].clone()
            for index in range(embeddings.shape[0]):
                state = (1.0 - alpha) * state + alpha * embeddings[index]
                ema[index] = state
            blocks.append(ema)
        return embeddings, torch.cat(blocks, dim=1) if blocks else embeddings.new_zeros((embeddings.shape[0], 0))

    def _text_features(self, state: CmState, trace: Any) -> torch.Tensor:
        embeddings, emas = self._raw_text_blocks(trace)
        centred = embeddings - state.pca_mean
        parts = [centred @ state.pca_basis.T]
        hidden = embeddings.shape[1]
        for index in range(len(self.ema_spans)):
            block = emas[:, index * hidden : (index + 1) * hidden] - state.pca_mean
            parts.append(block @ state.pca_basis.T)
        position = torch.log(torch.arange(1, embeddings.shape[0] + 1, dtype=torch.float32)).unsqueeze(1)
        parts.append(position)
        return torch.cat(parts, dim=1)

    # -- fit -----------------------------------------------------------------

    def fit(self, routine_traces: Sequence[Any]) -> CmState:
        traces = [trace for trace in routine_traces if int(trace.token_ids.numel()) > 0]
        if not traces:
            raise ValueError("no routine traces available for fitting")
        dim = len(self.layers) * EXPERTS
        diagnostics: dict[str, Any] = {
            "routine_trace_count": len(traces),
            "conditioning": self.conditioning,
            "use_c3": self.use_c3,
            "uncovered": self.uncovered,
        }

        # --- C1 table -------------------------------------------------------
        all_ids = torch.cat([trace.token_ids.long() for trace in traces])
        unique_ids = torch.unique(all_ids)
        token_row = torch.zeros(VOCAB_SIZE, dtype=torch.long)
        token_row[unique_ids] = torch.arange(1, unique_ids.numel() + 1, dtype=torch.long)
        sums = torch.zeros(unique_ids.numel() + 1, dim)
        counts = torch.zeros(unique_ids.numel() + 1)
        total = torch.zeros(dim)
        token_total = 0
        for trace in traces:
            ind = indicator(trace.top_k_ids, self.layers)
            rows = token_row[trace.token_ids.long()]
            sums.index_add_(0, rows, ind)
            counts.index_add_(0, rows, torch.ones(rows.numel()))
            total += ind.sum(0)
            token_total += int(ind.shape[0])
        mu = total / float(token_total)
        shrunk = (sums + self.shrinkage_m * mu) / (counts + self.shrinkage_m).unsqueeze(1)
        covered_row = counts >= float(self.min_count)
        covered_row[0] = False
        table = torch.where(covered_row.unsqueeze(1), shrunk, mu.unsqueeze(0).expand_as(shrunk))
        table[0] = mu
        diagnostics["routine_token_count"] = token_total
        diagnostics["unique_token_count"] = int(unique_ids.numel())
        diagnostics["covered_token_type_count"] = int(covered_row.sum())
        diagnostics["routine_covered_token_fraction"] = float(
            (counts[covered_row].sum() / float(token_total)).item()
        )
        state = CmState(
            mu=mu,
            table=table,
            token_row=token_row,
            covered_row=covered_row,
            window_mean=torch.zeros(dim),
            window_sd=torch.ones(dim),
            diagnostics=diagnostics,
        )

        # --- C2 ridge -------------------------------------------------------
        if self.conditioning == "c1c2":
            self._fit_text_map(state, traces)

        # --- whitening of the conditional residual windows -------------------
        blocks = []
        for trace in traces:
            residual = self._residual(state, trace)
            ends, means = window_means(residual, self.window_width)
            if ends.numel():
                blocks.append(means)
        if not blocks:
            raise ValueError("no routine residual windows available for fitting")
        matrix = torch.cat(blocks)
        state.window_mean = matrix.mean(0)
        state.window_sd = matrix.std(0) + self.variance_floor
        diagnostics["routine_window_count"] = int(matrix.shape[0])

        # --- C3 prefill conditioning ----------------------------------------
        if self.use_c3:
            self._fit_prefill_map(state, traces)
        return state

    def _fit_text_map(self, state: CmState, traces: Sequence[Any]) -> None:
        generator = torch.Generator().manual_seed(self.seed)
        embedding_blocks = []
        budget = self.pca_token_cap
        for trace in traces:
            rows = rio.load_input_embeddings()[trace.token_ids]
            if budget <= 0:
                break
            embedding_blocks.append(rows[:budget])
            budget -= rows.shape[0]
        sample = torch.cat(embedding_blocks)
        if sample.shape[0] > self.pca_token_cap:
            order = torch.randperm(sample.shape[0], generator=generator)[: self.pca_token_cap]
            sample = sample[order]
        pca_mean = sample.mean(0)
        centred = sample - pca_mean
        covariance = (centred.T @ centred) / float(max(centred.shape[0] - 1, 1))
        eigenvalues, eigenvectors = torch.linalg.eigh(covariance.double())
        order = torch.argsort(eigenvalues, descending=True)[: self.pca_components]
        state.pca_mean = pca_mean
        state.pca_basis = eigenvectors[:, order].T.float().contiguous()
        state.diagnostics["pca_explained_variance_ratio"] = float(
            (eigenvalues[order].sum() / eigenvalues.clamp(min=0).sum()).item()
        )

        features: list[torch.Tensor] = []
        targets_ind: list[torch.Tensor] = []
        targets_res: list[torch.Tensor] = []
        for trace in traces:
            features.append(self._text_features(state, trace))
            ind = indicator(trace.top_k_ids, self.layers)
            targets_ind.append(ind)
            targets_res.append(ind - state.table[state.token_row[trace.token_ids.long()]])
        first, second = _halves(traces)

        def gram_of(indices: Sequence[int], target: Sequence[torch.Tensor]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
            x = torch.cat([features[i] for i in indices]).double()
            y = torch.cat([target[i] for i in indices]).double()
            x_mean = x.mean(0)
            y_mean = y.mean(0)
            xc = x - x_mean
            yc = y - y_mean
            return xc.T @ xc, xc.T @ yc, x_mean, y_mean

        report: dict[str, Any] = {}
        chosen: dict[str, float] = {}
        for label, target in (("ind", targets_ind), ("c1_residual", targets_res)):
            gram, cross, x_mean, y_mean = gram_of(first, target)
            x_eval = torch.cat([features[i] for i in second]).double()
            y_eval = torch.cat([target[i] for i in second]).double()
            curve = []
            for lam in self.ridge_lambdas:
                beta = _solve_ridge(gram, cross, lam)
                prediction = (x_eval - x_mean) @ beta + y_mean
                curve.append({"lambda": lam, "holdout_r2": _r2(prediction, y_eval)})
            best = max(curve, key=lambda row: (row["holdout_r2"] if row["holdout_r2"] == row["holdout_r2"] else -1e9))
            report[label] = {"curve": curve, "selected_lambda": best["lambda"], "holdout_r2": best["holdout_r2"]}
            chosen[label] = best["lambda"]
        state.diagnostics["c2_ridge"] = report

        gram, cross, x_mean, y_mean = gram_of(list(range(len(traces))), targets_res)
        beta = _solve_ridge(gram, cross, chosen["c1_residual"])
        state.ridge_x_mean = x_mean.float()
        state.ridge_y_mean = y_mean.float()
        state.ridge_beta = beta.float()

    def _fit_prefill_map(self, state: CmState, traces: Sequence[Any]) -> None:
        summaries = torch.stack([prefill_summary(trace) for trace in traces])
        targets = []
        for trace in traces:
            energy, _ = self._energy(state, trace)
            targets.append(float(torch.log(energy + 1e-6).mean()) if energy.numel() else float("nan"))
        target = torch.tensor(targets, dtype=torch.float64)
        keep = ~torch.isnan(target)
        summaries = summaries[keep]
        target = target[keep]
        kept = [trace for trace, flag in zip(traces, keep.tolist()) if flag]
        state.c3_constant = float(target.mean())
        if summaries.shape[0] < 8:
            state.diagnostics["c3"] = {"status": "constant", "reason": "too few routine traces"}
            return
        mean = summaries.mean(0)
        centred = summaries - mean
        _, _, vt = torch.linalg.svd(centred.double(), full_matrices=False)
        basis = vt[: min(self.c3_components, vt.shape[0])].float()
        workflows = tuple(sorted({trace.workflow for trace in kept}))
        state.c3_mean = mean
        state.c3_basis = basis
        state.c3_workflows = workflows

        design = torch.stack([self._c3_features(state, trace) for trace in kept]).double()
        first, second = _halves(kept)
        x_fit, y_fit = design[first], target[first]
        x_eval, y_eval = design[second], target[second]
        x_mean = x_fit.mean(0)
        y_mean = y_fit.mean()
        gram = (x_fit - x_mean).T @ (x_fit - x_mean)
        cross = (x_fit - x_mean).T @ (y_fit - y_mean).unsqueeze(1)
        curve = []
        for lam in self.c3_lambdas:
            beta = _solve_ridge(gram, cross, lam)
            prediction = (x_eval - x_mean) @ beta + y_mean
            curve.append({"lambda": lam, "holdout_r2": _r2(prediction, y_eval.unsqueeze(1))})
        best = max(curve, key=lambda row: (row["holdout_r2"] if row["holdout_r2"] == row["holdout_r2"] else -1e9))
        if not (best["holdout_r2"] > 0.0):
            state.diagnostics["c3"] = {
                "status": "constant",
                "reason": "holdout R2 <= 0",
                "curve": curve,
                "constant_log_energy": state.c3_constant,
            }
            state.c3_beta = None
            return
        full_mean = design.mean(0)
        full_target_mean = target.mean()
        gram_full = (design - full_mean).T @ (design - full_mean)
        cross_full = (design - full_mean).T @ (target - full_target_mean).unsqueeze(1)
        beta = _solve_ridge(gram_full, cross_full, best["lambda"])
        state.c3_beta = torch.cat(
            (beta.squeeze(1).float(), (full_target_mean - full_mean @ beta.squeeze(1)).reshape(1).float())
        )
        state.diagnostics["c3"] = {
            "status": "fitted",
            "curve": curve,
            "selected_lambda": best["lambda"],
            "holdout_r2": best["holdout_r2"],
            "workflow_count": len(workflows),
            "constant_log_energy": state.c3_constant,
        }

    def _c3_features(self, state: CmState, trace: Any) -> torch.Tensor:
        projected = (prefill_summary(trace) - state.c3_mean) @ state.c3_basis.T
        onehot = torch.zeros(len(state.c3_workflows))
        if trace.workflow in state.c3_workflows:
            onehot[state.c3_workflows.index(trace.workflow)] = 1.0
        return torch.cat((projected, onehot))

    # -- scoring -------------------------------------------------------------

    def _residual(self, state: CmState, trace: Any) -> torch.Tensor:
        ind = indicator(trace.top_k_ids, self.layers)
        rows = state.token_row[trace.token_ids.long()]
        residual = ind - state.table[rows]
        if self.uncovered == "zero":
            residual = residual * state.covered_row[rows].unsqueeze(1).float()
        if state.ridge_beta is not None:
            features = self._text_features(state, trace)
            residual = residual - ((features - state.ridge_x_mean) @ state.ridge_beta + state.ridge_y_mean)
        return residual

    def _energy(self, state: CmState, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        residual = self._residual(state, trace)
        ends, means = window_means(residual, self.window_width)
        if not ends.numel():
            return torch.empty(0), ends
        z = (means - state.window_mean) / state.window_sd
        return (z**2).sum(1), ends

    def score(self, state: CmState, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        energy, ends = self._energy(state, trace)
        if not ends.numel():
            return energy, ends
        if self.use_c3:
            if state.c3_beta is not None:
                features = self._c3_features(state, trace)
                prediction = float(features @ state.c3_beta[:-1] + state.c3_beta[-1])
            else:
                prediction = state.c3_constant
            energy = energy / float(torch.exp(torch.tensor(prediction)))
        return energy, ends


@register("cm")
def _build_cm(config: dict[str, Any]) -> ConditionalManifoldScorer:
    return ConditionalManifoldScorer(**config)


# ---------------------------------------------------------------------------
# same-feature text one-class control (spec 4.4): kNN novelty on the C2 inputs x_t
# ---------------------------------------------------------------------------


@dataclass
class XKnnState:
    pca_mean: torch.Tensor
    pca_basis: torch.Tensor
    reference: torch.Tensor
    window_count: int


class XFeatureKnnScorer(ConditionalManifoldScorer):
    """kNN-10 novelty of the window mean of the C2 input features x_t.

    Same inputs as C2, no routing at all -- the fairest text control for CM.
    """

    name = "cm_x_knn"

    def __init__(
        self,
        window_width: int = 8,
        pca_components: int = 256,
        neighbours: int = 10,
        reference_cap: int = 6000,
        **kwargs: Any,
    ) -> None:
        super().__init__(window_width=window_width, pca_components=pca_components, **kwargs)
        self.neighbours = int(neighbours)
        self.reference_cap = int(reference_cap)

    def config(self) -> dict[str, Any]:
        base = super().config()
        base.update({"neighbours": self.neighbours, "reference_cap": self.reference_cap, "scorer": "cm_x_knn"})
        return base

    def fit(self, routine_traces: Sequence[Any]) -> XKnnState:
        traces = [trace for trace in routine_traces if int(trace.token_ids.numel()) > 0]
        shell = CmState(
            mu=torch.zeros(1),
            table=torch.zeros(1, 1),
            token_row=torch.zeros(VOCAB_SIZE, dtype=torch.long),
            covered_row=torch.zeros(1, dtype=torch.bool),
            window_mean=torch.zeros(1),
            window_sd=torch.ones(1),
        )
        generator = torch.Generator().manual_seed(self.seed)
        sample = torch.cat([rio.load_input_embeddings()[trace.token_ids] for trace in traces])
        if sample.shape[0] > self.pca_token_cap:
            order = torch.randperm(sample.shape[0], generator=generator)[: self.pca_token_cap]
            sample = sample[order]
        pca_mean = sample.mean(0)
        centred = sample - pca_mean
        covariance = (centred.T @ centred) / float(max(centred.shape[0] - 1, 1))
        eigenvalues, eigenvectors = torch.linalg.eigh(covariance.double())
        order = torch.argsort(eigenvalues, descending=True)[: self.pca_components]
        shell.pca_mean = pca_mean
        shell.pca_basis = eigenvectors[:, order].T.float().contiguous()
        blocks = []
        for trace in traces:
            ends, means = window_means(self._text_features(shell, trace), self.window_width)
            if ends.numel():
                blocks.append(means)
        matrix = torch.cat(blocks)
        order = torch.randperm(matrix.shape[0], generator=generator)[: self.reference_cap]
        return XKnnState(
            pca_mean=shell.pca_mean,
            pca_basis=shell.pca_basis,
            reference=matrix[order].contiguous(),
            window_count=int(matrix.shape[0]),
        )

    def score(self, state: XKnnState, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        shell = CmState(
            mu=torch.zeros(1),
            table=torch.zeros(1, 1),
            token_row=torch.zeros(1, dtype=torch.long),
            covered_row=torch.zeros(1, dtype=torch.bool),
            window_mean=torch.zeros(1),
            window_sd=torch.ones(1),
            pca_mean=state.pca_mean,
            pca_basis=state.pca_basis,
        )
        ends, means = window_means(self._text_features(shell, trace), self.window_width)
        if not ends.numel():
            return torch.empty(0), ends
        distances = torch.cdist(means, state.reference)
        k = min(self.neighbours, state.reference.shape[0])
        return distances.topk(k, dim=1, largest=False).values.mean(1), ends


@register("cm_x_knn")
def _build_x_knn(config: dict[str, Any]) -> XFeatureKnnScorer:
    return XFeatureKnnScorer(**config)
