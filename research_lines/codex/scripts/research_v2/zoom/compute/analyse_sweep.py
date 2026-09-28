#!/usr/bin/env python3
"""Turn the layer sweep + criteria into the report tables (frontier, criterion picks, oracle)."""
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
FULL = "+".join(map(str, BAND))
MID = "+".join(map(str, range(5, 12)))

# per-candidate frozen reference key
REF = {"wgm": (FULL, 8), "pdm": (MID, 4)}


def get(fam, case, w, key):
    return R[f"{fam}|{case}|w{w}"][key]


def fmt(row):
    return (f"{row['far_all']:.3f}/{row['far_clean']:.3f}/{row['far_benign']:.3f}/{row['far_resist']:.3f}"
            f" | {row['recall_plus_8']:.3f}/{row['recall_plus_16']:.3f}/{row['recall_final']:.3f}"
            f" | {row['median_latency']}")


def pooled_criterion_order(width, criterion):
    """Direction-agnostic pick: average of the two cases' ranks."""
    ranks = {l: 0.0 for l in BAND}
    for case in ("b1_to_b2", "b2_to_b1"):
        c = crit["cases"][case][f"w{width}"][criterion]
        order = sorted(BAND, key=lambda l: c[str(l)])
        for i, l in enumerate(order):
            ranks[l] += i
    return sorted(BAND, key=lambda l: ranks[l])


def within(row, ref, dr=0.05, df=0.02):
    return (row["recall_plus_8"] >= ref["recall_plus_8"] - dr) and (row["far_all"] <= ref["far_all"] + df)


out_lines = []


def emit(s=""):
    out_lines.append(s)
    print(s)


for fam in ("wgm", "pdm"):
    refkey, refw = REF[fam]
    refs = {c: get(fam, c, refw, refkey) for c in ("b1_to_b2", "b2_to_b1")}
    emit(f"\n===== {fam.upper()} (frozen ref: layers {refkey.replace('+',',')} w={refw}) =====")
    for c in ("b1_to_b2", "b2_to_b1"):
        emit(f"  ref {c}: {fmt(refs[c])}")

    for width in (4, 8):
        emit(f"\n-- w={width} --")
        # criterion picks (direction-agnostic pooled ranking)
        for criterion in ("RTS", "RV"):
            order = pooled_criterion_order(width, criterion)
            for k in (2, 3, 4):
                sub = sorted(order[:k])
                key = "+".join(map(str, sub))
                rows = {c: get(fam, c, width, key) for c in ("b1_to_b2", "b2_to_b1")}
                ok = all(within(rows[c], refs[c]) for c in rows)
                emit(f"  {criterion} k={k} L={sub}  "
                     f"B1->B2 {fmt(rows['b1_to_b2'])}  ||  B2->B1 {fmt(rows['b2_to_b1'])}  {'PASS' if ok else ''}")
        # oracle: best subset per size by min over directions of R8, tie-break FAR
        for k in (2, 3, 4):
            best = None
            for key in R[f"{fam}|b1_to_b2|w{width}"]:
                if key.count("+") != k - 1:
                    continue
                r1 = get(fam, "b1_to_b2", width, key)
                r2 = get(fam, "b2_to_b1", width, key)
                score = min(r1["recall_plus_8"], r2["recall_plus_8"])
                pen = max(r1["far_all"], r2["far_all"])
                cand = (score, -pen, key, r1, r2)
                if best is None or cand[:2] > best[:2]:
                    best = cand
            emit(f"  ORACLE k={k} L={best[2].replace('+',',')}  "
                 f"B1->B2 {fmt(best[3])}  ||  B2->B1 {fmt(best[4])}")
        # how many subsets of each size meet the tolerance in both directions
        for k in (2, 3, 4):
            n = tot = 0
            for key in R[f"{fam}|b1_to_b2|w{width}"]:
                if key.count("+") != k - 1:
                    continue
                tot += 1
                if all(within(get(fam, c, width, key), refs[c]) for c in ("b1_to_b2", "b2_to_b1")):
                    n += 1
            emit(f"  tolerance-band count k={k}: {n}/{tot} subsets within (+8 recall -0.05, FAR +0.02) in BOTH directions")

(OUT / "sweep_tables.txt").write_text("\n".join(out_lines), encoding="utf-8")
