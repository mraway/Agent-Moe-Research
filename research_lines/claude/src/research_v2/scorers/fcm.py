"""Proposal 4: FCM -- form-conditioned routine manifold (docs/research_v2/fcm_plan.md).

The frozen candidates miss code / SQL / structured deliverables because the routine
manifold itself contains a large amount of structured (tool-call JSON) routing.  This
module implements the preregistered candidates of ``docs/research_v2/fcm_prereg.md``:

* ``form_whitened`` -- F1 / F1R / F1soft / F2 / F7: per-form whitened distance.  Every
  routine window is labelled ``prose`` or ``structured`` (form source ``T`` = decoded
  token classes, ``R`` = k-means on routine routing), each form gets its own mu / sigma,
  and a window is scored against the statistics of its own form (``soft``: the minimum
  over forms).  ``feature="probability"`` swaps the top-8 selection rates for the window
  mean of the router probabilities.
* ``run_length`` -- F3: the number of consecutive ``structured`` windows.
* ``unseen_expert`` -- F5: per token, the number of top-8 experts whose routine selection
  rate is below the per-layer q10, averaged over the causal window.
* ``unseen_transition`` -- F6: per token, the number of depth-chain links whose routine
  count table cell is empty (the PDM depth table's zero cells), window mean.
* ``filtered_wgm`` / ``filtered_pdm`` -- F8: CAND-A / CAND-B refitted after dropping the
  routine windows (resp. tokens) that the ``T`` rule calls ``structured``.

Everything is fitted on routine traces only (``fit`` never sees a drift trace).  Scoring
uses decode routing of tokens <= t, the decode token ids (form source ``T`` only, a
*text-derived* feature that the report marks as such) and offline routine statistics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from types import SimpleNamespace
from typing import Any, Sequence

import torch

from research_v2 import io as rio
from research_v2.features import (
    ALL_LAYERS,
    MIDDLE_LATE_LAYERS,
    MIDDLE_LAYERS,
    TOP_K,
    selection_rate_windows,
    window_means,
)
from research_v2.scorers import register
from research_v2.scorers.pdm import PdmScorer

EXPERTS = 64
LAYER_BANDS: dict[str, tuple[int, ...]] = {
    "middle": MIDDLE_LAYERS,
    "middle_late": MIDDLE_LATE_LAYERS,
    "all": ALL_LAYERS,
}

# ---------------------------------------------------------------------------
# form labels, source T (prereg section 2.1) -- TEXT DERIVED
# ---------------------------------------------------------------------------

HARD_CHARS = set("{}[]()<>\"'`=;|\\")
SOFT_CHARS = set(":,.-_/#*+0123456789")
_CAMEL = re.compile(r"[a-z][A-Z]")
STRUCTURED_QUANTILE = 0.80
FORM_NAMES = ("prose", "structured")


def structured_surface(norm: str, *, strip_whitespace: bool = False) -> bool:
    """The frozen token-class rule of prereg 2.1 applied to a normalised surface string.

    ``strip_whitespace`` is the post-hoc *sensitivity* variant (report section on rule
    defects): the preregistered rule strips spaces only, so a token whose surface is pure
    newline / newline+indent never reaches the ``s0`` branch and is labelled prose.  The
    grid runs use the preregistered rule (``strip_whitespace=False``).
    """

    core = norm.strip() if strip_whitespace else norm.strip(" ")
    if core == "":
        return "\n" in norm
    if any(char in HARD_CHARS for char in core):
        return True
    if all(char in SOFT_CHARS for char in core):
        return True
    if "_" in core and any(char.isalnum() for char in core):
        return True
    return bool(_CAMEL.search(core))


@lru_cache(maxsize=2)
def structured_vocab_mask(strip_whitespace: bool = False) -> torch.Tensor:
    """[vocab] bool mask: does this token id count as a structured token?"""

    tokenizer = rio.load_tokenizer()
    size = tokenizer.get_vocab_size(with_added_tokens=True)
    mask = torch.zeros(size, dtype=torch.bool)
    for token_id in range(size):
        piece = tokenizer.id_to_token(token_id)
        if piece is None:
            continue
        norm = piece.replace("Ġ", " ").replace("Ċ", "\n")
        mask[token_id] = structured_surface(norm, strip_whitespace=strip_whitespace)
    return mask


def structured_token_mask(token_ids: torch.Tensor, strip_whitespace: bool = False) -> torch.Tensor:
    vocab = structured_vocab_mask(strip_whitespace)
    ids = token_ids.long().clamp_(min=0, max=vocab.shape[0] - 1)
    return vocab[ids]


def structured_fraction_windows(
    token_ids: torch.Tensor, width: int, strip_whitespace: bool = False
) -> tuple[torch.Tensor, torch.Tensor]:
    """Causal windows of the structured-token fraction; aligned with feature windows."""

    mask = structured_token_mask(token_ids, strip_whitespace).to(torch.float64).reshape(-1, 1)
    ends, means = window_means(mask, width)
    return ends, means.reshape(-1)


def token_form_labels(
    token_ids: torch.Tensor, width: int, tau: float, strip_whitespace: bool = False
) -> torch.Tensor:
    """Per-token form label (prereg 4, F8b): the window ending at t; short prefix uses w0."""

    ends, fractions = structured_fraction_windows(token_ids, width, strip_whitespace)
    tokens = int(token_ids.shape[0])
    labels = torch.zeros(tokens, dtype=torch.long)
    if not ends.numel():
        return labels
    flags = (fractions >= tau).long()
    labels[ends] = flags
    labels[: int(ends[0])] = int(flags[0])
    return labels


# ---------------------------------------------------------------------------
# form labels, source R (prereg section 2.2) -- pure routing
# ---------------------------------------------------------------------------


def kmeans(
    matrix: torch.Tensor, k: int, *, seed: int = 0, iterations: int = 25
) -> torch.Tensor:
    """Lloyd k-means with k-means++ seeding; returns [k, D] centroids."""

    generator = torch.Generator().manual_seed(seed)
    count = matrix.shape[0]
    first = int(torch.randint(count, (1,), generator=generator))
    centroids = [matrix[first]]
    for _ in range(1, k):
        stacked = torch.stack(centroids)
        distances = torch.cdist(matrix, stacked).amin(dim=1) ** 2
        total = float(distances.sum())
        if total <= 0:
            centroids.append(matrix[int(torch.randint(count, (1,), generator=generator))])
            continue
        probabilities = (distances / total).to(torch.float64)
        index = int(torch.multinomial(probabilities, 1, generator=generator))
        centroids.append(matrix[index])
    centres = torch.stack(centroids)
    for _ in range(iterations):
        assignment = torch.cdist(matrix, centres).argmin(dim=1)
        updated = centres.clone()
        for cluster in range(k):
            selected = matrix[assignment == cluster]
            if selected.shape[0]:
                updated[cluster] = selected.mean(0)
        if torch.allclose(updated, centres):
            centres = updated
            break
        centres = updated
    return centres


# ---------------------------------------------------------------------------
# state
# ---------------------------------------------------------------------------


@dataclass
class FcmState:
    mu: torch.Tensor | None = None  # [F, D] per-form means (form_whitened)
    sd: torch.Tensor | None = None  # [F, D]
    tau: float | None = None
    centroids: torch.Tensor | None = None  # [k, D] R-form centroids
    whiten_mu: torch.Tensor | None = None  # global routine mean (R clustering space)
    whiten_sd: torch.Tensor | None = None
    rare_mask: torch.Tensor | None = None  # [L, 64] bool (unseen_expert)
    eps: torch.Tensor | None = None  # [L]
    zero_depth: torch.Tensor | None = None  # [L-1, 64, 64] bool (unseen_transition)
    zero_initial: torch.Tensor | None = None  # [64] bool
    form_counts: dict[str, int] = field(default_factory=dict)
    window_count: int = 0
    trace_count: int = 0
    inner: Any = None

    def to_json(self) -> dict[str, Any]:
        block: dict[str, Any] = {
            "window_count": self.window_count,
            "trace_count": self.trace_count,
            "tau": self.tau,
            "form_counts": self.form_counts,
        }
        if self.eps is not None:
            block["eps_per_layer"] = [round(float(v), 6) for v in self.eps.tolist()]
        if self.zero_depth is not None:
            block["zero_cell_fraction"] = float(self.zero_depth.double().mean())
        return block


# ---------------------------------------------------------------------------
# scorers
# ---------------------------------------------------------------------------


class FcmScorer:
    """All preregistered FCM variants behind one causal scorer interface."""

    name = "fcm"
    requires_positives = False

    def __init__(
        self,
        variant: str = "form_whitened",
        window_width: int = 8,
        layers: str | Sequence[int] = "middle_late",
        form_source: str = "T",
        form_k: int = 2,
        score_mode: str = "assigned",
        feature: str = "selection",
        form_window: int = 8,
        quantile: float = STRUCTURED_QUANTILE,
        rare_quantile: float = 0.10,
        variance_floor: float = 1e-3,
        pdm_model: str = "d1",
        seed: int = 0,
        strip_whitespace: bool = False,
    ) -> None:
        if variant not in (
            "form_whitened",
            "run_length",
            "unseen_expert",
            "unseen_transition",
            "filtered_wgm",
            "filtered_pdm",
        ):
            raise ValueError(f"unknown fcm variant {variant!r}")
        if form_source not in ("T", "R"):
            raise ValueError(f"unknown form source {form_source!r}")
        if score_mode not in ("assigned", "soft"):
            raise ValueError(f"unknown score mode {score_mode!r}")
        if feature not in ("selection", "probability"):
            raise ValueError(f"unknown feature {feature!r}")
        self.variant = variant
        self.window_width = int(window_width)
        self.layers = (
            tuple(LAYER_BANDS[layers]) if isinstance(layers, str) else tuple(int(v) for v in layers)
        )
        self.layer_band = layers if isinstance(layers, str) else "custom"
        self.form_source = form_source
        self.form_k = int(form_k)
        self.score_mode = score_mode
        self.feature = feature
        self.form_window = int(form_window)
        self.quantile = float(quantile)
        self.rare_quantile = float(rare_quantile)
        self.variance_floor = float(variance_floor)
        self.pdm_model = pdm_model
        self.seed = int(seed)
        self.strip_whitespace = bool(strip_whitespace)

    def config(self) -> dict[str, Any]:
        return {
            "variant": self.variant,
            "window_width": self.window_width,
            "layer_band": self.layer_band,
            "layers": list(self.layers),
            "form_source": self.form_source,
            "form_k": self.form_k,
            "score_mode": self.score_mode,
            "feature": self.feature,
            "form_window": self.form_window,
            "quantile": self.quantile,
            "rare_quantile": self.rare_quantile,
            "variance_floor": self.variance_floor,
            "pdm_model": self.pdm_model,
            "seed": self.seed,
            "strip_whitespace": self.strip_whitespace,
            "text_derived": self.uses_text,
        }

    @property
    def uses_text(self) -> bool:
        if self.variant in ("run_length", "filtered_wgm", "filtered_pdm"):
            return True
        if self.variant == "form_whitened":
            return self.form_source == "T" and self.score_mode == "assigned"
        return False

    # -- features -------------------------------------------------------------
    def _feature_windows(self, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        if self.feature == "selection":
            return selection_rate_windows(trace.top_k_ids, self.window_width, self.layers)
        probabilities = trace.probabilities()[list(self.layers)]  # [L, T, 64]
        flat = probabilities.permute(1, 0, 2).reshape(probabilities.shape[1], -1).to(torch.float32)
        return window_means(flat, self.window_width)

    def _form_labels(self, state: FcmState, trace: Any, ends: torch.Tensor, windows: torch.Tensor):
        if self.form_source == "T":
            assert state.tau is not None
            _, fractions = structured_fraction_windows(trace.token_ids, self.window_width, self.strip_whitespace)
            fractions = fractions[: ends.numel()]
            return (fractions >= state.tau).long()
        assert state.centroids is not None
        z = (windows - state.whiten_mu) / state.whiten_sd
        return torch.cdist(z, state.centroids).argmin(dim=1)

    # -- fit ------------------------------------------------------------------
    def fit(self, routine_traces: Sequence[Any]) -> FcmState:
        if self.variant == "form_whitened":
            return self._fit_form_whitened(routine_traces)
        if self.variant == "run_length":
            return self._fit_tau(routine_traces)
        if self.variant == "unseen_expert":
            return self._fit_unseen_expert(routine_traces)
        if self.variant == "unseen_transition":
            return self._fit_unseen_transition(routine_traces)
        if self.variant == "filtered_wgm":
            return self._fit_filtered_wgm(routine_traces)
        return self._fit_filtered_pdm(routine_traces)

    def _routine_tau(self, routine_traces: Sequence[Any], width: int) -> float:
        fractions = []
        for trace in routine_traces:
            _, values = structured_fraction_windows(trace.token_ids, width, self.strip_whitespace)
            if values.numel():
                fractions.append(values)
        if not fractions:
            raise ValueError("no routine windows for the form threshold")
        pooled = torch.cat(fractions)
        return float(torch.quantile(pooled, self.quantile))

    def _fit_tau(self, routine_traces: Sequence[Any]) -> FcmState:
        tau = self._routine_tau(routine_traces, self.window_width)
        counts = {"prose": 0, "structured": 0}
        windows = 0
        for trace in routine_traces:
            _, values = structured_fraction_windows(trace.token_ids, self.window_width, self.strip_whitespace)
            if not values.numel():
                continue
            flags = (values >= tau).long()
            counts["structured"] += int(flags.sum())
            counts["prose"] += int((1 - flags).sum())
            windows += int(values.numel())
        return FcmState(
            tau=tau,
            form_counts=counts,
            window_count=windows,
            trace_count=len(routine_traces),
        )

    def _fit_form_whitened(self, routine_traces: Sequence[Any]) -> FcmState:
        blocks: list[torch.Tensor] = []
        label_blocks: list[torch.Tensor] = []
        tau: float | None = None
        if self.form_source == "T":
            tau = self._routine_tau(routine_traces, self.window_width)
        for trace in routine_traces:
            ends, windows = self._feature_windows(trace)
            if not ends.numel():
                continue
            blocks.append(windows)
            if self.form_source == "T":
                _, fractions = structured_fraction_windows(trace.token_ids, self.window_width, self.strip_whitespace)
                label_blocks.append((fractions[: ends.numel()] >= tau).long())
        if not blocks:
            raise ValueError("no routine windows available for fitting")
        matrix = torch.cat(blocks)
        whiten_mu = matrix.mean(0)
        whiten_sd = matrix.std(0) + self.variance_floor
        centroids: torch.Tensor | None = None
        if self.form_source == "R":
            centroids = kmeans((matrix - whiten_mu) / whiten_sd, self.form_k, seed=self.seed)
            labels = torch.cdist((matrix - whiten_mu) / whiten_sd, centroids).argmin(dim=1)
        else:
            labels = torch.cat(label_blocks)
        forms = self.form_k if self.form_source == "R" else 2
        mus = []
        sds = []
        counts: dict[str, int] = {}
        for form in range(forms):
            selected = matrix[labels == form]
            key = FORM_NAMES[form] if forms == 2 and self.form_source == "T" else f"form{form}"
            counts[key] = int(selected.shape[0])
            if selected.shape[0] < 2:
                mus.append(whiten_mu)
                sds.append(whiten_sd)
                continue
            mus.append(selected.mean(0))
            sds.append(selected.std(0) + self.variance_floor)
        return FcmState(
            mu=torch.stack(mus),
            sd=torch.stack(sds),
            tau=tau,
            centroids=centroids,
            whiten_mu=whiten_mu,
            whiten_sd=whiten_sd,
            form_counts=counts,
            window_count=int(matrix.shape[0]),
            trace_count=len(blocks),
        )

    def _fit_unseen_expert(self, routine_traces: Sequence[Any]) -> FcmState:
        layers = list(self.layers)
        counts = torch.zeros((len(layers), EXPERTS), dtype=torch.float64)
        tokens = 0
        for trace in routine_traces:
            top_k = trace.top_k_ids[layers]  # [L, T, 8]
            if top_k.shape[1] == 0:
                continue
            tokens += int(top_k.shape[1])
            for index in range(len(layers)):
                counts[index] += torch.bincount(
                    top_k[index].reshape(-1), minlength=EXPERTS
                ).double()
        if tokens == 0:
            raise ValueError("no routine tokens available for fitting")
        rates = counts / float(tokens)
        eps = torch.quantile(rates, self.rare_quantile, dim=1)
        rare = rates < eps.unsqueeze(1)
        return FcmState(
            rare_mask=rare,
            eps=eps,
            window_count=tokens,
            trace_count=len(routine_traces),
            form_counts={"rare_experts": int(rare.sum())},
        )

    def _fit_unseen_transition(self, routine_traces: Sequence[Any]) -> FcmState:
        layers = list(self.layers)
        counts = torch.zeros((len(layers) - 1, EXPERTS, EXPERTS), dtype=torch.float64)
        initial = torch.zeros(EXPERTS, dtype=torch.float64)
        tokens = 0
        for trace in routine_traces:
            top1 = trace.top_k_ids[layers, :, 0].long()
            if top1.shape[1] == 0:
                continue
            tokens += int(top1.shape[1])
            for index in range(len(layers) - 1):
                flat = top1[index] * EXPERTS + top1[index + 1]
                counts[index] += (
                    torch.bincount(flat, minlength=EXPERTS * EXPERTS)
                    .reshape(EXPERTS, EXPERTS)
                    .double()
                )
            initial += torch.bincount(top1[0], minlength=EXPERTS).double()
        if tokens == 0:
            raise ValueError("no routine tokens available for fitting")
        return FcmState(
            zero_depth=counts == 0,
            zero_initial=initial == 0,
            window_count=tokens,
            trace_count=len(routine_traces),
        )

    def _prose_windows(self, trace: Any, tau: float) -> torch.Tensor:
        _, fractions = structured_fraction_windows(trace.token_ids, self.form_window, self.strip_whitespace)
        return fractions < tau

    def _fit_filtered_wgm(self, routine_traces: Sequence[Any]) -> FcmState:
        tau = self._routine_tau(routine_traces, self.form_window)
        blocks = []
        dropped = 0
        kept = 0
        for trace in routine_traces:
            ends, windows = self._feature_windows(trace)
            if not ends.numel():
                continue
            _, fractions = structured_fraction_windows(trace.token_ids, self.form_window, self.strip_whitespace)
            keep = torch.ones(ends.numel(), dtype=torch.bool)
            limit = min(ends.numel(), fractions.numel())
            keep[:limit] = fractions[:limit] < tau
            dropped += int((~keep).sum())
            kept += int(keep.sum())
            if keep.any():
                blocks.append(windows[keep])
        if not blocks:
            raise ValueError("no prose routine windows available for fitting")
        matrix = torch.cat(blocks)
        mu = matrix.mean(0)
        sd = matrix.std(0) + self.variance_floor
        return FcmState(
            mu=mu.unsqueeze(0),
            sd=sd.unsqueeze(0),
            tau=tau,
            form_counts={"kept_windows": kept, "dropped_windows": dropped},
            window_count=int(matrix.shape[0]),
            trace_count=len(blocks),
        )

    def _fit_filtered_pdm(self, routine_traces: Sequence[Any]) -> FcmState:
        tau = self._routine_tau(routine_traces, self.form_window)
        inner = PdmScorer(
            window_width=self.window_width, model=self.pdm_model, layers=self.layers
        )
        filtered = []
        kept = 0
        dropped = 0
        for trace in routine_traces:
            labels = token_form_labels(trace.token_ids, self.form_window, tau, self.strip_whitespace)
            keep = labels == 0
            dropped += int((~keep).sum())
            kept += int(keep.sum())
            if int(keep.sum()) < 2:
                continue
            filtered.append(SimpleNamespace(top_k_ids=trace.top_k_ids[:, keep, :]))
        if not filtered:
            raise ValueError("no prose routine tokens available for fitting")
        state = inner.fit(filtered)
        return FcmState(
            tau=tau,
            inner=state,
            form_counts={"kept_tokens": kept, "dropped_tokens": dropped},
            window_count=kept,
            trace_count=len(filtered),
            mu=None,
        )

    # -- score ----------------------------------------------------------------
    def score(self, state: FcmState, trace: Any) -> tuple[torch.Tensor, torch.Tensor]:
        if self.variant == "form_whitened":
            ends, windows = self._feature_windows(trace)
            if not ends.numel():
                return torch.empty(0, dtype=torch.float64), ends
            assert state.mu is not None and state.sd is not None
            per_form = []
            for form in range(state.mu.shape[0]):
                z = (windows - state.mu[form]) / state.sd[form]
                per_form.append((z**2).sum(1))
            stacked = torch.stack(per_form)  # [F, nwin]
            if self.score_mode == "soft":
                return stacked.amin(dim=0).to(torch.float64), ends
            labels = self._form_labels(state, trace, ends, windows)
            picked = stacked.gather(0, labels.reshape(1, -1)).reshape(-1)
            return picked.to(torch.float64), ends

        if self.variant == "run_length":
            ends, fractions = structured_fraction_windows(trace.token_ids, self.window_width, self.strip_whitespace)
            if not ends.numel():
                return torch.empty(0, dtype=torch.float64), ends
            flags = (fractions >= state.tau).to(torch.float64)
            run = torch.zeros_like(flags)
            current = 0.0
            for index in range(flags.numel()):
                current = current + 1.0 if flags[index] > 0 else 0.0
                run[index] = current
            return run, ends

        if self.variant == "unseen_expert":
            layers = list(self.layers)
            top_k = trace.top_k_ids[layers]  # [L, T, 8]
            if top_k.shape[1] < self.window_width:
                return torch.empty(0, dtype=torch.float64), torch.empty(0, dtype=torch.long)
            assert state.rare_mask is not None
            per_token = torch.zeros(top_k.shape[1], dtype=torch.float64)
            for index in range(len(layers)):
                per_token += state.rare_mask[index][top_k[index]].sum(dim=1).double()
            ends, means = window_means(per_token.reshape(-1, 1), self.window_width)
            return means.reshape(-1), ends

        if self.variant == "unseen_transition":
            layers = list(self.layers)
            top1 = trace.top_k_ids[layers, :, 0].long()
            if top1.shape[1] < self.window_width:
                return torch.empty(0, dtype=torch.float64), torch.empty(0, dtype=torch.long)
            assert state.zero_depth is not None and state.zero_initial is not None
            per_token = state.zero_initial[top1[0]].double()
            for index in range(len(layers) - 1):
                per_token = per_token + state.zero_depth[index][top1[index], top1[index + 1]].double()
            ends, means = window_means(per_token.reshape(-1, 1), self.window_width)
            return means.reshape(-1), ends

        if self.variant == "filtered_wgm":
            ends, windows = self._feature_windows(trace)
            if not ends.numel():
                return torch.empty(0, dtype=torch.float64), ends
            assert state.mu is not None and state.sd is not None
            z = (windows - state.mu[0]) / state.sd[0]
            return (z**2).sum(1).to(torch.float64), ends

        inner = PdmScorer(
            window_width=self.window_width, model=self.pdm_model, layers=self.layers
        )
        return inner.score(state.inner, trace)


@register("fcm")
def _build(config: dict[str, Any]) -> FcmScorer:
    return FcmScorer(**config)
