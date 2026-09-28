#!/usr/bin/env python3
"""Reduced-representation counterfactuals for CAND-A / CAND-B, through the real harness
metric layer (S1 both directions, routine=cb, mode D, alpha=0.10, persist2).

Axes:
  * window width w in {4, 8}
  * top-1 expert only vs top-8 selection rate (CAND-A), top-1 chain vs top-8 set chain (CAND-B)
  * probability features vs selection features (both candidates)
  * uint8 / 4-bit quantization of the fitted state (CAND-A mu/sd/centre; CAND-B log tables)

Every variant is fitted on routine traces only and scored causally; nothing here is a new
result, it is a cost/accuracy diagnostic on frozen candidates.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
torch.set_num_threads(6)

from research_v2 import io as rio  # noqa: E402
from research_v2.harness import (  # noqa: E402
    HarnessConfig,
    build_split_cases,
    candidate_summary,
    routine_traces,
    run_case,
)
from research_v2.readings import build_readings  # noqa: E402
from research_v2.scorers.pdm import PdmScorer  # noqa: E402

OUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "zoom" / "compute"
OUT.mkdir(parents=True, exist_ok=True)
EXPERTS = 64
TOPK = 8
FLOOR = 1e-3
LATE = tuple(range(5, 16))
MID = tuple(range(5, 12))

CFG = HarnessConfig(
    scorer="zoom", windows=(8,), routines=("cb",), splits=("S1",), modes=("D",),
    alphas=(0.10,), readings=("persist2",), bootstrap_draws=0,
    emit_q1=False, emit_audit=False, emit_ranking=False, store_score_streams=False,
)
KEYS = ("far_all", "far_clean", "far_benign", "far_resist", "recall_plus_4",
        "recall_plus_8", "recall_plus_16", "recall_final", "median_latency", "pre_alarm_rate")


def win_mean(x: torch.Tensor, width: int):
    """x [T,D] -> causal window means."""
    t = x.shape[0]
    if t < width:
        return torch.empty(0, dtype=torch.long), x.new_empty((0, x.shape[1]))
    pre = torch.cat((x.new_zeros((1, x.shape[1])), x.cumsum(0)))
    return torch.arange(width - 1, t, dtype=torch.long), (pre[width:] - pre[:-width]) / float(width)


def quantize(t: torch.Tensor, bits: int) -> torch.Tensor:
    """Per-tensor affine quantize/dequantize round trip."""
    lo, hi = float(t.min()), float(t.max())
    levels = (1 << bits) - 1
    if hi <= lo:
        return t.clone()
    scale = (hi - lo) / levels
    q = torch.clamp(torch.round((t - lo) / scale), 0, levels)
    return q * scale + lo


# ------------------------------------------------------------------ CAND-A variants
class GeomScorer:
    """Whitened squared distance over a configurable per-token [16,64] feature."""

    requires_positives = False

    def __init__(self, feature: str, layers=LATE, width: int = 8, state_bits: int | None = None):
        self.feature = feature
        self.layers = list(layers)
        self.window_width = width
        self.state_bits = state_bits

    def _tok(self, trace) -> torch.Tensor:
        """[T, len(layers)*64] per-token feature."""
        tk = trace.top_k_ids[self.layers].long()  # [L,T,8]
        L, T, _ = tk.shape
        if self.feature == "top8":
            f = torch.zeros((L, T, EXPERTS))
            f.scatter_(2, tk, 1.0)
        elif self.feature == "top1":
            f = torch.zeros((L, T, EXPERTS))
            f.scatter_(2, tk[:, :, :1], 1.0)
        elif self.feature == "prob_full":
            f = trace.probabilities()[self.layers].float()
        elif self.feature == "prob_top8":
            p = trace.probabilities()[self.layers].float()
            m = torch.zeros_like(p)
            m.scatter_(2, tk, 1.0)
            f = p * m
        else:
            raise ValueError(self.feature)
        return f.permute(1, 0, 2).reshape(T, L * EXPERTS).contiguous()

    def fit(self, pool):
        blocks = []
        for t in pool:
            _, w = win_mean(self._tok(t), self.window_width)
            if w.shape[0]:
                blocks.append(w)
        m = torch.cat(blocks)
        mu, sd = m.mean(0), m.std(0) + FLOOR
        centre = ((m - mu) / sd).mean(0)
        if self.state_bits:
            mu, sd, centre = (quantize(v, self.state_bits) for v in (mu, sd, centre))
            sd = sd.clamp_min(FLOOR)
        return {"mu": mu, "sd": sd, "centre": centre}

    def score(self, state, trace):
        ends, w = win_mean(self._tok(trace), self.window_width)
        if not ends.numel():
            return torch.empty(0, dtype=torch.float64), ends
        z = (w - state["mu"]) / state["sd"] - state["centre"]
        return (z * z).sum(1).to(torch.float64), ends

    def state_bytes(self, state):
        b = 8 if self.state_bits is None else self.state_bits / 8.0
        return int(sum(v.numel() for v in state.values()) * (4 if self.state_bits is None else b))


# ------------------------------------------------------------------ CAND-B variants
class PdmVariant(PdmScorer):
    """CAND-B with an optional top-8 / probability-weighted depth chain and quantized tables."""

    def __init__(self, mode: str = "top1", table_bits: int | None = None, **kw):
        super().__init__(**kw)
        self.mode = mode
        self.table_bits = table_bits
        self.zero_fraction = None

    def fit(self, pool):
        state = super().fit(pool)
        if self.table_bits:
            state.log_depth = quantize(state.log_depth, self.table_bits)
            state.log_depth_initial = quantize(state.log_depth_initial, self.table_bits)
            # refit the standardizer under the quantized tables (routine-only)
            vals = [self._components(state, t.top_k_ids)["u"] for t in pool if t.top_k_ids.shape[1]]
            pooled = torch.cat(vals)
            state.mean["u"] = float(pooled.mean())
            state.sd["u"] = float(pooled.std()) + self.variance_floor
        return state

    def _components(self, state, top_k_ids):
        if self.mode == "top1":
            return super()._components(state, top_k_ids)
        tk = top_k_ids[list(self.layers)].long()  # [L,T,8]
        p_depth = torch.exp(state.log_depth)  # [L-1,64,64]
        p_init = torch.exp(state.log_depth_initial)
        total = torch.log(p_init[tk[0]].mean(1))
        for i in range(tk.shape[0] - 1):
            g = p_depth[i][tk[i].unsqueeze(2), tk[i + 1].unsqueeze(1)]  # [T,8,8]
            total = total + torch.log(g.mean(dim=(1, 2)))
        return {"u": -total}


def evaluate(case, scorer, width):
    res = run_case(case, scorer, CFG, "cb", width, list(build_readings(("persist2",))))
    return {k: candidate_summary(res["candidates"][0]).get(k) for k in KEYS}


def main() -> None:
    batches = rio.load_core()
    cases = {c.name: c for c in build_split_cases(batches, "S1")}
    variants = []

    def add(name, family, cost_note, factory, width):
        variants.append((name, family, cost_note, factory, width))

    # ---- CAND-A -----------------------------------------------------------
    add("A0 baseline top8 L5-15 w8", "wgm", "88 ids/tok, 704-d", lambda: GeomScorer("top8", LATE, 8), 8)
    add("A0 top8 L5-15 w4", "wgm", "88 ids/tok, 704-d", lambda: GeomScorer("top8", LATE, 4), 4)
    add("A1 top1 L5-15 w8", "wgm", "11 ids/tok, 704-d", lambda: GeomScorer("top1", LATE, 8), 8)
    add("A1 top1 L5-15 w4", "wgm", "11 ids/tok, 704-d", lambda: GeomScorer("top1", LATE, 4), 4)
    add("A2 prob_full L5-15 w8", "wgm", "704 floats/tok", lambda: GeomScorer("prob_full", LATE, 8), 8)
    add("A3 prob_top8 L5-15 w8", "wgm", "88 (id,prob)/tok", lambda: GeomScorer("prob_top8", LATE, 8), 8)
    add("A4 top8 state uint8 w8", "wgm", "state 8-bit", lambda: GeomScorer("top8", LATE, 8, 8), 8)
    add("A5 top8 state 4-bit w8", "wgm", "state 4-bit", lambda: GeomScorer("top8", LATE, 8, 4), 8)

    # ---- CAND-B -----------------------------------------------------------
    add("B0 baseline d1 top1 L5-11 w4", "pdm", "7 ids/tok, 6x64x64 tables",
        lambda: PdmVariant(model="d1", layers="middle", window_width=4), 4)
    add("B0 d1 top1 L5-11 w8", "pdm", "7 ids/tok", lambda: PdmVariant(model="d1", layers="middle", window_width=8), 8)
    add("B1 d1 top8 set-chain L5-11 w4", "pdm", "56 ids/tok, 6x64x64",
        lambda: PdmVariant(mode="top8", model="d1", layers="middle", window_width=4), 4)
    add("B2 d1 tables uint8 w4", "pdm", "tables 8-bit",
        lambda: PdmVariant(model="d1", layers="middle", window_width=4, table_bits=8), 4)
    add("B3 d1 tables 4-bit w4", "pdm", "tables 4-bit",
        lambda: PdmVariant(model="d1", layers="middle", window_width=4, table_bits=4), 4)

    rows = []
    for name, family, note, factory, width in variants:
        row = {"variant": name, "family": family, "cost_note": note, "window_width": width}
        for cname in ("b1_to_b2", "b2_to_b1"):
            t0 = time.time()
            sc = factory()
            row[cname] = evaluate(cases[cname], sc, width)
            row[cname]["seconds"] = round(time.time() - t0, 2)
        rows.append(row)
        print(f"{name:34s} B1->B2 R8={row['b1_to_b2']['recall_plus_8']:.3f} FAR={row['b1_to_b2']['far_all']:.3f} "
              f"| B2->B1 R8={row['b2_to_b1']['recall_plus_8']:.3f} FAR={row['b2_to_b1']['far_all']:.3f}", flush=True)
        if "prob" in name:
            for t in (*cases["b1_to_b2"].target_traces, *cases["b2_to_b1"].target_traces):
                t._probabilities = None

    # ---- CAND-B table sparsity -------------------------------------------
    sparsity = {}
    for cname, case in cases.items():
        pool = routine_traces(case.fit_traces, "cb")
        sc = PdmScorer(model="d1", layers="middle", window_width=4)
        st = sc.fit(pool)
        counts = torch.zeros((len(MID) - 1, EXPERTS, EXPERTS), dtype=torch.float64)
        init = torch.zeros(EXPERTS, dtype=torch.float64)
        for t in pool:
            top1 = t.top_k_ids[list(MID), :, 0].long()
            for i in range(len(MID) - 1):
                flat = top1[i] * EXPERTS + top1[i + 1]
                counts[i] += torch.bincount(flat, minlength=EXPERTS * EXPERTS).reshape(EXPERTS, EXPERTS).double()
            init += torch.bincount(top1[0], minlength=EXPERTS).double()
        nz = int((counts > 0).sum())
        total = counts.numel()
        rows_nonempty = int((counts.sum(2) > 0).sum())
        sparsity[cname] = {
            "fit_traces": len(pool),
            "fit_tokens": st.token_count,
            "cells": total,
            "nonzero_cells": nz,
            "empty_fraction": round(1 - nz / total, 4),
            "nonempty_rows": rows_nonempty,
            "total_rows": int(counts.shape[0] * EXPERTS),
            "dense_float64_bytes": total * 8,
            "dense_float32_bytes": total * 4,
            "csr_float32_bytes": nz * 4 + nz * 1 + (counts.shape[0] * (EXPERTS + 1)) * 4,
            "csr_uint8_bytes": nz * 1 + nz * 1 + (counts.shape[0] * (EXPERTS + 1)) * 4,
            "init_nonzero": int((init > 0).sum()),
            "unseen_depth_fraction_reported": st.unseen_depth_fraction,
        }
    payload = {"rows": rows, "cand_b_table_sparsity": sparsity}
    (OUT / "repr_variants.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(sparsity, indent=2))


if __name__ == "__main__":
    main()
