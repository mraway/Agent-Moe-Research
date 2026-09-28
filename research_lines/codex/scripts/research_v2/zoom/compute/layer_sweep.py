#!/usr/bin/env python3
"""Exhaustive layer-subset sweep for CAND-A (wgm g1) and CAND-B (pdm d1), via an exact
additive fast path, evaluated through the real harness metric layer (mode D, alpha 0.10,
persist2, both S1 directions).

Exactness
---------
CAND-A: mu/sd/centre are per-feature statistics, so the whitened squared distance of a layer
subset S is exactly sum_{l in S} of the per-layer contribution computed with the full-band
statistics.  Verified against the real WGMScorer in `--verify`.

CAND-B: D1 is  -[log P(e_{l1}) + sum_i log P(e_{l_{i+1}} | e_{l_i})]  over the *ordered subset*.
We pre-fit the 11 marginals and all 55 ordered pair tables (l<l') on the same routine pool the
scorer would see, so any subset's per-token surprisal is a sum of pre-gathered terms; the
routine mean/sd standardization is then recomputed per subset from the routine-token stream.
Verified against the real PdmScorer in `--verify`.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
torch.set_num_threads(6)

from research_v2 import io as rio  # noqa: E402
from research_v2.features import selection_rate_windows  # noqa: E402
from research_v2.harness import (  # noqa: E402
    HarnessConfig,
    build_split_cases,
    candidate_summary,
    routine_traces,
    run_case,
)
from research_v2.readings import build_readings  # noqa: E402
from research_v2.scorers import build as build_scorer  # noqa: E402

OUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "zoom" / "compute"
OUT.mkdir(parents=True, exist_ok=True)
BAND = tuple(range(5, 16))
EXPERTS = 64
FLOOR = 1e-3
SMOOTHING = 0.5


def window_mean_1d(values: torch.Tensor, width: int):
    t = values.shape[0]
    if t < width:
        return torch.empty(0, dtype=torch.long), torch.empty(0, dtype=torch.float64)
    prefix = torch.cat((torch.zeros(1, dtype=values.dtype), values.cumsum(0)))
    means = (prefix[width:] - prefix[:-width]) / float(width)
    return torch.arange(width - 1, t, dtype=torch.long), means


# --------------------------------------------------------------------- CAND-A
class WGMPrecomputed:
    """Per-layer squared whitened distance streams; subset score = sum over layers."""

    def __init__(self, fit_pool, width):
        self.width = width
        blocks = []
        for t in fit_pool:
            _, w = selection_rate_windows(t.top_k_ids, width, BAND)
            if w.shape[0]:
                blocks.append(w)
        m = torch.cat(blocks)
        self.mu = m.mean(0)
        self.sd = m.std(0) + FLOOR
        self.centre = ((m - self.mu) / self.sd).mean(0)
        self.cache: dict[str, tuple[torch.Tensor, torch.Tensor]] = {}

    def layer_streams(self, trace):
        key = trace.trace_id
        if key in self.cache:
            return self.cache[key]
        ends, w = selection_rate_windows(trace.top_k_ids, self.width, BAND)
        if not ends.numel():
            out = (torch.empty(0, dtype=torch.long), torch.empty((0, len(BAND))))
            self.cache[key] = out
            return out
        z = (w - self.mu) / self.sd - self.centre
        per_layer = (z * z).reshape(z.shape[0], len(BAND), EXPERTS).sum(2)
        out = (ends, per_layer.to(torch.float64))
        self.cache[key] = out
        return out


class WGMSubsetScorer:
    requires_positives = False

    def __init__(self, pre: WGMPrecomputed, subset):
        self.pre = pre
        self.idx = [BAND.index(l) for l in subset]
        self.window_width = pre.width
        self.subset = tuple(subset)

    def fit(self, routine_traces_):
        return None

    def score(self, state, trace):
        ends, per_layer = self.pre.layer_streams(trace)
        if not ends.numel():
            return torch.empty(0, dtype=torch.float64), ends
        return per_layer[:, self.idx].sum(1), ends


# --------------------------------------------------------------------- CAND-B
class PDMPrecomputed:
    """Marginal + all-ordered-pair D1 log tables and per-trace gathered terms."""

    def __init__(self, fit_pool, width):
        self.width = width
        self.fit_pool = list(fit_pool)
        n = len(BAND)
        marg = torch.zeros((n, EXPERTS), dtype=torch.float64)
        pair = torch.zeros((n, n, EXPERTS, EXPERTS), dtype=torch.float64)
        for t in fit_pool:
            top1 = t.top_k_ids[list(BAND), :, 0].long()
            if top1.shape[1] == 0:
                continue
            for i in range(n):
                marg[i] += torch.bincount(top1[i], minlength=EXPERTS).double()
                for j in range(i + 1, n):
                    flat = top1[i] * EXPERTS + top1[j]
                    pair[i, j] += (
                        torch.bincount(flat, minlength=EXPERTS * EXPERTS)
                        .reshape(EXPERTS, EXPERTS)
                        .double()
                    )
        self.counts_marg = marg
        self.counts_pair = pair
        sm = marg + SMOOTHING
        self.log_marg = torch.log(sm / sm.sum(dim=1, keepdim=True))
        sp = pair + SMOOTHING
        self.log_pair = torch.log(sp / sp.sum(dim=3, keepdim=True))
        self.cache: dict[str, tuple[torch.Tensor, torch.Tensor]] = {}
        self._pool_cache = None

    def terms(self, trace):
        """(marg_terms [L,T], pair_terms [L,L,T]) for one trace."""
        key = trace.trace_id
        if key in self.cache:
            return self.cache[key]
        top1 = trace.top_k_ids[list(BAND), :, 0].long()
        n, T = top1.shape
        mt = torch.stack([self.log_marg[i][top1[i]] for i in range(n)])
        pt = torch.zeros((n, n, T), dtype=torch.float64)
        for i in range(n):
            for j in range(i + 1, n):
                pt[i, j] = self.log_pair[i, j][top1[i], top1[j]]
        out = (mt, pt)
        self.cache[key] = out
        return out

    def raw_stream(self, trace, idx):
        mt, pt = self.terms(trace)
        acc = mt[idx[0]].clone()
        for a, b in zip(idx[:-1], idx[1:]):
            acc += pt[a, b]
        return -acc

    def _pooled(self):
        if getattr(self, "_pool_cache", None) is None:
            mts, pts = [], []
            for t in self.fit_pool:
                if t.top_k_ids.shape[1] == 0:
                    continue
                mt, pt = self.terms(t)
                mts.append(mt)
                pts.append(pt)
            self._pool_cache = (torch.cat(mts, dim=1), torch.cat(pts, dim=2))
        return self._pool_cache

    def standardizer(self, idx):
        mt, pt = self._pooled()
        acc = mt[idx[0]].clone()
        for a, b in zip(idx[:-1], idx[1:]):
            acc += pt[a, b]
        vals = -acc
        return float(vals.mean()), float(vals.std()) + 1e-6


class PDMSubsetScorer:
    requires_positives = False

    def __init__(self, pre: PDMPrecomputed, subset):
        self.pre = pre
        self.idx = [BAND.index(l) for l in subset]
        self.window_width = pre.width
        self.subset = tuple(subset)
        self.mean, self.sd = pre.standardizer(self.idx)

    def fit(self, routine_traces_):
        return None

    def score(self, state, trace):
        if trace.top_k_ids.shape[1] < self.window_width:
            return torch.empty(0, dtype=torch.float64), torch.empty(0, dtype=torch.long)
        raw = (self.pre.raw_stream(trace, self.idx) - self.mean) / self.sd
        ends, means = window_mean_1d(raw, self.window_width)
        return means, ends


# --------------------------------------------------------------------- driver
CFG = HarnessConfig(
    scorer="zoom",
    windows=(8,),
    routines=("cb",),
    splits=("S1",),
    modes=("D",),
    alphas=(0.10,),
    readings=("persist2",),
    bootstrap_draws=0,
    emit_q1=False,
    emit_audit=False,
    emit_ranking=False,
    store_score_streams=False,
)
KEYS = (
    "far_all",
    "far_clean",
    "far_benign",
    "far_resist",
    "recall_plus_4",
    "recall_plus_8",
    "recall_plus_16",
    "recall_final",
    "median_latency",
    "pre_alarm_rate",
)


def evaluate(case, scorer, width):
    res = run_case(case, scorer, CFG, "cb", width, list(build_readings(("persist2",))))
    cand = res["candidates"][0]
    s = candidate_summary(cand)
    return {k: s.get(k) for k in KEYS}


def verify(batches):
    case = [c for c in build_split_cases(batches, "S1") if c.name == "b1_to_b2"][0]
    pool = routine_traces(case.fit_traces, "cb")
    trace = case.target_traces[7]
    pre = WGMPrecomputed(pool, 8)
    fast = WGMSubsetScorer(pre, (6, 9, 13)).score(None, trace)[0]
    real_s = build_scorer("wgm", {"metric": "g1", "layers": [6, 9, 13], "window_width": 8})
    real = real_s.score(real_s.fit(pool), trace)[0]
    dw = float((fast - real).abs().max())
    pre2 = PDMPrecomputed(pool, 4)
    fastp = PDMSubsetScorer(pre2, (6, 9, 13)).score(None, trace)[0]
    real_p = build_scorer("pdm", {"model": "d1", "layers": [6, 9, 13], "window_width": 4})
    realp = real_p.score(real_p.fit(pool), trace)[0]
    dp = float((fastp - realp).abs().max())
    print(f"verify: wgm max|fast-real|={dw:.3e}  pdm max|fast-real|={dp:.3e}")
    return dw, dp


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--widths", default="4,8")
    ap.add_argument("--sizes", default="2,3,4")
    args = ap.parse_args()
    batches = rio.load_core()
    if args.verify:
        verify(batches)
        return
    widths = [int(v) for v in args.widths.split(",")]
    sizes = [int(v) for v in args.sizes.split(",")]
    cases = build_split_cases(batches, "S1")
    subsets = [s for k in sizes for s in itertools.combinations(BAND, k)]
    out = {"band": list(BAND), "sizes": sizes, "widths": widths, "results": {}}
    for case in cases:
        pool = routine_traces(case.fit_traces, "cb")
        for width in widths:
            t0 = time.time()
            preA = WGMPrecomputed(pool, width)
            preB = PDMPrecomputed(pool, width)
            for t in case.target_traces:
                preA.layer_streams(t)
                preB.terms(t)
            for t in pool:
                preB.terms(t)
            print(f"[{case.name} w{width}] precompute {time.time()-t0:.1f}s", flush=True)
            for fam, pre, cls in (("wgm", preA, WGMSubsetScorer), ("pdm", preB, PDMSubsetScorer)):
                t1 = time.time()
                rows = {}
                # full-band references
                for ref in (BAND, tuple(range(5, 12))):
                    rows["+".join(map(str, ref))] = evaluate(case, cls(pre, ref), width)
                for sub in subsets:
                    rows["+".join(map(str, sub))] = evaluate(case, cls(pre, sub), width)
                out["results"][f"{fam}|{case.name}|w{width}"] = rows
                print(f"  {fam}: {len(rows)} subsets in {time.time()-t1:.1f}s", flush=True)
            preB.cache.clear()
            preA.cache.clear()
    (OUT / "layer_sweep.json").write_text(json.dumps(out), encoding="utf-8")
    print("wrote", OUT / "layer_sweep.json")


if __name__ == "__main__":
    main()
