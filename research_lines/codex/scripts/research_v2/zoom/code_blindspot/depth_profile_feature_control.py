"""Probability mass vs top-8 selection set, on identical 48-token windows.

Same windows, same fit/held routine split, same diagonal whitening (CAND-A's g1 form,
sd + variance floor 1e-3), two features:
  P = window-mean full router probabilities [64 per layer]
  S = window top-8 selection rates [64 per layer]  (CAND-A's actual feature)
Also the trace-level null: for each held-out routine trace and each resist trace, the MAX
over its own windows, which is what an alarm rule would face.  Diagnostic only.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
CACHE = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot/depth_profile"
L = 16
DOMAINS = ["cooking", "fiction", "general_knowledge", "legal_analysis",
           "mathematics", "poetry", "travel_planning"]
FLOOR = 1e-3


def auc(pos, neg):
    a = np.concatenate([pos, neg]); order = np.argsort(a, kind="mergesort"); sa = a[order]
    ranks = np.empty(len(a)); i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sa[j + 1] == sa[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0; i = j + 1
    return (ranks[:len(pos)].sum() - len(pos) * (len(pos) - 1) / 2.0) / (len(pos) * len(neg))


def scores(z, layers):
    """Whitened distance to the routine centre over the given layer set."""
    F = np.concatenate([z["routine_fit"][:, l, :] for l in layers], axis=1).astype(np.float64)
    mu, sd = F.mean(0), F.std(0) + FLOOR
    out = {}
    for g in z.files:
        X = np.concatenate([z[g][:, l, :] for l in layers], axis=1).astype(np.float64)
        out[g] = np.linalg.norm((X - mu) / sd, axis=1)
    return out


def main():
    zp = np.load(CACHE / "window_probs.npz")
    zs = np.load(CACHE / "window_sel.npz")
    meta = json.loads((CACHE / "depth_profile.json").read_text())["window_meta"]
    other = [f"other:{x}" for x in DOMAINS]

    print("## F1 whitened distance, probabilities (P) vs top-8 selection rates (S), per layer")
    print("ratio = median(group)/median(held-out routine); AUC vs the 851 held-out routine windows")
    print()
    print("| layer set | P code ratio | P code AUC | P other ratio | P other AUC | "
          "S code ratio | S code AUC | S other ratio | S other AUC |")
    print("|" + "---|" * 9)
    sets = {**{f"L{l}": [l] for l in range(L)},
            "0-4": list(range(5)), "5-11": list(range(5, 12)), "12-15": [12, 13, 14, 15],
            "5-15 (CAND-A band)": list(range(5, 16)), "0-15": list(range(16))}
    keep = {}
    for name, ls in sets.items():
        row = [name]
        for z in (zp, zs):
            s = scores(z, ls)
            rn = s["routine"]
            o = np.concatenate([s[g] for g in other])
            row += [f"{np.median(s['code'])/np.median(rn):.2f}", f"{auc(s['code'], rn):.3f}",
                    f"{np.median(o)/np.median(rn):.2f}", f"{auc(o, rn):.3f}"]
            keep[(name, "P" if z is zp else "S")] = s
        print("| " + " | ".join(row) + " |")
    print()

    print("## F2 trace-level null for the layer sets that look best for code")
    print("Each held-out routine trace / resist trace contributes the MAX over its own windows; "
          "the columns give, per drift trace, its window score and the fraction of the 120 "
          "held-out routine traces (and 61 resist traces) whose max window is above it.")
    print()
    for feat in ("P", "S"):
        for name in ("L13", "12-15", "5-15 (CAND-A band)"):
            s = keep[(name, feat)]
            tmax = {}
            for grp in ("routine", "resist"):
                by = defaultdict(list)
                for i, m in enumerate(meta[grp]):
                    by[m["trace_id"]].append(s[grp][i])
                tmax[grp] = np.array([max(v) for v in by.values()])
            print(f"### feature {feat}, layers {name}")
            print("| trace | domain | score | routine traces above | resist traces above |")
            print("|---|---|---|---|---|")
            for i, m in enumerate(meta["code"]):
                v = s["code"][i]
                print(f"| {'-'.join(m['trace_id'].split('-')[:3])} | programming | {v:.2f} | "
                      f"{(tmax['routine'] > v).mean():.2f} | {(tmax['resist'] > v).mean():.2f} |")
            for dom in DOMAINS:
                vs = s[f"other:{dom}"]
                fr = [float((tmax['routine'] > v).mean()) for v in vs]
                fs = [float((tmax['resist'] > v).mean()) for v in vs]
                print(f"| ({len(vs)} traces, median) | {dom} | {np.median(vs):.2f} | "
                      f"{np.median(fr):.2f} | {np.median(fs):.2f} |")
            print()

    print("## F3 the eight code traces under P at every layer (score / percentile in held-out "
          "routine windows / fraction of routine traces whose max window is above)")
    print()
    hdr = "| trace | " + " | ".join(f"L{l}" for l in range(L)) + " |"
    print(hdr); print("|" + "---|" * (L + 1))
    tmax_cache = {}
    for l in range(L):
        s = keep[(f"L{l}", "P")]
        by = defaultdict(list)
        for i, m in enumerate(meta["routine"]):
            by[m["trace_id"]].append(s["routine"][i])
        tmax_cache[l] = np.array([max(v) for v in by.values()])
    for i, m in enumerate(meta["code"]):
        cells = []
        for l in range(L):
            s = keep[(f"L{l}", "P")]
            v = s["code"][i]
            cells.append(f"{(s['routine'] < v).mean()*100:.0f}/{(tmax_cache[l] > v).mean()*100:.0f}")
        print(f"| {'-'.join(m['trace_id'].split('-')[:3])} | " + " | ".join(cells) + " |")
    print()


if __name__ == "__main__":
    main()
