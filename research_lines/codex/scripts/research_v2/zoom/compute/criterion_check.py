#!/usr/bin/env python3
"""Does the routine-only layer criterion carry any signal about detection quality?
Rank-correlate the subset-level criterion (mean per-layer RV / RTS over the subset, from
fit-side routine only) against the label-based outcome (min-over-directions +8 recall and
max-over-directions FAR) across all 550 subsets."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "zoom" / "compute"
BAND = list(range(5, 16))
sweep = json.loads((OUT / "layer_sweep.json").read_text())
crit = json.loads((OUT / "layer_criteria.json").read_text())
R = sweep["results"]


def spearman(a, b):
    def rank(x):
        order = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0] * len(x)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for t in range(i, j + 1):
                r[order[t]] = avg
            i = j + 1
        return r
    ra, rb = rank(a), rank(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    da = sum((x - ma) ** 2 for x in ra) ** 0.5
    db = sum((y - mb) ** 2 for y in rb) ** 0.5
    return num / (da * db) if da and db else 0.0


lines = []
for fam in ("wgm", "pdm"):
    for width in (4, 8):
        keys = [k for k in R[f"{fam}|b1_to_b2|w{width}"] if 2 <= k.count("+") + 1 <= 4]
        for criterion in ("RV", "RTS"):
            cvals, r8, far = [], [], []
            for key in keys:
                layers = [int(v) for v in key.split("+")]
                # subset criterion = mean of per-layer criterion, averaged over both fit sides
                s = 0.0
                for case in ("b1_to_b2", "b2_to_b1"):
                    c = crit["cases"][case][f"w{width}"][criterion]
                    s += sum(c[str(l)] for l in layers) / len(layers)
                cvals.append(s / 2)
                a = R[f"{fam}|b1_to_b2|w{width}"][key]
                b = R[f"{fam}|b2_to_b1|w{width}"][key]
                r8.append(min(a["recall_plus_8"], b["recall_plus_8"]))
                far.append(max(a["far_all"], b["far_all"]))
            line = (f"{fam} w={width} {criterion}: n={len(keys)} "
                    f"spearman(criterion, min-dir +8 recall)={spearman(cvals, r8):+.3f}  "
                    f"spearman(criterion, max-dir FAR)={spearman(cvals, far):+.3f}")
            lines.append(line)
            print(line)

# rank of the criterion pick among subsets of its size
print()
lines.append("")
for fam in ("wgm", "pdm"):
    for width in (4, 8):
        for criterion in ("RV", "RTS"):
            ranks = {l: 0.0 for l in BAND}
            for case in ("b1_to_b2", "b2_to_b1"):
                c = crit["cases"][case][f"w{width}"][criterion]
                order = sorted(BAND, key=lambda l: c[str(l)])
                for i, l in enumerate(order):
                    ranks[l] += i
            order = sorted(BAND, key=lambda l: ranks[l])
            for k in (2, 3, 4):
                pick = "+".join(map(str, sorted(order[:k])))
                sized = [key for key in R[f"{fam}|b1_to_b2|w{width}"] if key.count("+") + 1 == k]
                def minr8(key):
                    a = R[f"{fam}|b1_to_b2|w{width}"][key]
                    b = R[f"{fam}|b2_to_b1|w{width}"][key]
                    return min(a["recall_plus_8"], b["recall_plus_8"])
                vals = sorted(sized, key=minr8, reverse=True)
                pos = vals.index(pick) + 1
                line = (f"{fam} w={width} {criterion} k={k} pick={pick.replace('+',',')}: "
                        f"rank {pos}/{len(vals)} by min-dir +8 recall "
                        f"(pick {minr8(pick):.3f}, best {minr8(vals[0]):.3f}, median {minr8(vals[len(vals)//2]):.3f})")
                lines.append(line)
                print(line)

(OUT / "criterion_check.txt").write_text("\n".join(lines), encoding="utf-8")
