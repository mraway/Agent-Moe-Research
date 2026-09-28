#!/usr/bin/env python3
"""Routine-only layer criteria for the zoom/compute audit.

Computed BEFORE any drift-labelled evaluation, on the *fit-side routine traces only*
(the same pool the scorer is allowed to fit on).  No label, onset, arm-attack or
target-side information is used.

Two pre-declared criteria per layer l in 5..15:

  RV(l)  routine window-variance ratio.  mean_e Var_windows(rate_le) divided by the
         binomial-independence null mean_e p_le(1-p_le)/w.  < 1 means the layer's routine
         window routing is more stereotyped than an independent-token null.  LOWER = tighter
         routine manifold = preferred.

  RTS(l) routine tail separation.  Cross-fitted inside the routine pool (pair-group halves,
         whitening from the other half), per-layer whitened squared distance on held-out
         routine windows; RTS = q99 / median.  LOWER = lighter routine tail, so the conformal
         threshold sits closer to the bulk = preferred.

Primary criterion for choosing size-k subsets: the k layers with the smallest RTS.
Secondary (reported): the k layers with the smallest RV.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
torch.set_num_threads(6)

from research_v2 import io as rio  # noqa: E402
from research_v2.features import selection_rate_windows  # noqa: E402
from research_v2.harness import build_split_cases, routine_traces  # noqa: E402

OUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "zoom" / "compute"
OUT.mkdir(parents=True, exist_ok=True)
BAND = tuple(range(5, 16))
FLOOR = 1e-3


def per_layer_blocks(traces, width):
    """dict layer -> [N,64] routine windows, plus per-trace block index."""
    ends_per_trace = []
    blocks = []
    for t in traces:
        ends, win = selection_rate_windows(t.top_k_ids, width, BAND)
        if win.shape[0]:
            blocks.append(win.reshape(win.shape[0], len(BAND), 64))
            ends_per_trace.append(t.pair_group_id)
    return blocks, ends_per_trace


def criteria(traces, width):
    blocks, groups = per_layer_blocks(traces, width)
    allwin = torch.cat(blocks)  # [N, L, 64]
    n = allwin.shape[0]
    rv = {}
    for i, layer in enumerate(BAND):
        x = allwin[:, i, :]
        var = x.var(0, unbiased=False).mean()
        p = x.mean(0)
        null = (p * (1 - p)).mean() / float(width)
        rv[layer] = float(var / null)

    # cross-fitted per-layer routine score
    uniq = sorted(set(groups))
    half = {g: i % 2 for i, g in enumerate(uniq)}
    idx = torch.tensor([half[g] for g in groups])
    scores = {layer: [] for layer in BAND}
    for h in (0, 1):
        fit = torch.cat([b for b, g in zip(blocks, groups) if half[g] != h])
        hold = [b for b, g in zip(blocks, groups) if half[g] == h]
        if not len(hold) or not fit.shape[0]:
            continue
        hold = torch.cat(hold)
        mu = fit.reshape(fit.shape[0], -1).mean(0).reshape(len(BAND), 64)
        sd = fit.reshape(fit.shape[0], -1).std(0).reshape(len(BAND), 64) + FLOOR
        z_fit = (fit - mu) / sd
        centre = z_fit.reshape(z_fit.shape[0], -1).mean(0).reshape(len(BAND), 64)
        z = (hold - mu) / sd - centre
        per_layer = (z * z).sum(2)  # [N, L]
        for i, layer in enumerate(BAND):
            scores[layer].append(per_layer[:, i])
    rts = {}
    med = {}
    for layer in BAND:
        s = torch.cat(scores[layer])
        q99 = float(torch.quantile(s, 0.99))
        m = float(torch.quantile(s, 0.50))
        rts[layer] = q99 / m if m > 0 else float("inf")
        med[layer] = m
    return {"n_windows": int(n), "RV": rv, "RTS": rts, "median_routine_score": med}


def main() -> None:
    batches = rio.load_core()
    out = {"band": list(BAND), "cases": {}}
    for case in build_split_cases(batches, "S1"):
        pool = routine_traces(case.fit_traces, "cb")
        block = {"fit_traces": len(pool)}
        for width in (4, 8):
            block[f"w{width}"] = criteria(pool, width)
        out["cases"][case.name] = block

    # pre-declared subset picks (per case, per width, per criterion, per size)
    picks = {}
    for cname, block in out["cases"].items():
        picks[cname] = {}
        for width in (4, 8):
            c = block[f"w{width}"]
            picks[cname][f"w{width}"] = {}
            for crit in ("RV", "RTS"):
                order = sorted(BAND, key=lambda l: c[crit][l])
                picks[cname][f"w{width}"][crit] = {
                    "order_ascending": order,
                    **{f"k{k}": sorted(order[:k]) for k in (2, 3, 4)},
                }
    out["picks"] = picks
    (OUT / "layer_criteria.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    for cname, block in out["cases"].items():
        for width in (4, 8):
            c = block[f"w{width}"]
            print(f"\n[{cname} w={width}] n_windows={c['n_windows']}")
            print("layer  RV     RTS    med")
            for l in BAND:
                print(f"  {l:2d}  {c['RV'][l]:6.3f} {c['RTS'][l]:6.2f} {c['median_routine_score'][l]:7.1f}")
            print("  picks RTS:", {k: v for k, v in picks[cname][f'w{width}']['RTS'].items() if k != 'order_ascending'})
            print("  picks RV :", {k: v for k, v in picks[cname][f'w{width}']['RV'].items() if k != 'order_ascending'})


if __name__ == "__main__":
    main()
