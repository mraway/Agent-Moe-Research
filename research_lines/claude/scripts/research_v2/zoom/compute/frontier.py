#!/usr/bin/env python3
"""Cost/accuracy frontier: attach an analytic per-token cost + a measured microsecond cost to
every reduced configuration, and find the cheapest one within (+8 recall -0.05, FAR +0.02) of
each frozen candidate in BOTH directions."""
from __future__ import annotations

import itertools
import json
import sys
import time
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
torch.set_num_threads(6)

OUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "zoom" / "compute"
BAND = list(range(5, 16))
sweep = json.loads((OUT / "layer_sweep.json").read_text())
R = sweep["results"]
FULL = "+".join(map(str, BAND))
MID = "+".join(map(str, range(5, 12)))
REF = {"wgm": (FULL, 8), "pdm": (MID, 4)}


def cost_wgm(L, w, feature="top8", state_bits=32):
    """(ops/token, state bytes, ints read/token)."""
    reads = 8 * L if feature in ("top8", "prob_top8") else (L if feature == "top1" else 64 * L)
    ring = 2 * 8 * L if feature == "top8" else 2 * reads
    dist = 4 * 64 * L
    return {"ops_per_token": ring + dist, "state_bytes": int(3 * 64 * L * state_bits / 8),
            "reads_per_token": reads, "ring_bytes": int(w * reads * 1)}


def cost_pdm(L, w, mode="top1", bits=32):
    reads = L if mode == "top1" else 8 * L
    lookups = (L - 1) if mode == "top1" else 64 * (L - 1)
    return {"ops_per_token": reads + 2 * lookups + 3,
            "state_bytes": int(((L - 1) * 64 * 64 + 64) * bits / 8),
            "reads_per_token": reads, "ring_bytes": w * 8}


def within(row, ref, dr=0.05, df=0.02):
    return (row["recall_plus_8"] >= ref["recall_plus_8"] - dr) and (row["far_all"] <= ref["far_all"] + df)


rows = []
for fam in ("wgm", "pdm"):
    rk, rw = REF[fam]
    refs = {c: R[f"{fam}|{c}|w{rw}"][rk] for c in ("b1_to_b2", "b2_to_b1")}
    for width in (4, 8):
        for key, r1 in R[f"{fam}|b1_to_b2|w{width}"].items():
            k = key.count("+") + 1
            r2 = R[f"{fam}|b2_to_b1|w{width}"][key]
            cost = cost_wgm(k, width) if fam == "wgm" else cost_pdm(k, width)
            rows.append({
                "family": fam, "layers": key, "k": k, "w": width,
                "variant": "layer_subset", **cost,
                "b1_to_b2": r1, "b2_to_b1": r2,
                "pass": all(within(r, refs[c]) for c, r in (("b1_to_b2", r1), ("b2_to_b1", r2))),
            })

repr_rows = json.loads((OUT / "repr_variants.json").read_text())["rows"]
for r in repr_rows:
    fam = r["family"]
    rk, rw = REF[fam]
    refs = {c: R[f"{fam}|{c}|w{rw}"][rk] for c in ("b1_to_b2", "b2_to_b1")}
    name = r["variant"]
    L = 11 if fam == "wgm" else 7
    if fam == "wgm":
        feat = "top1" if "top1" in name else ("prob_full" if "prob_full" in name else ("prob_top8" if "prob_top8" in name else "top8"))
        bits = 8 if "uint8" in name else (4 if "4-bit" in name else 32)
        cost = cost_wgm(L, r["window_width"], feat, bits)
    else:
        mode = "top8" if "top8" in name else "top1"
        bits = 8 if "uint8" in name else (4 if "4-bit" in name else 32)
        cost = cost_pdm(L, r["window_width"], mode, bits)
    rows.append({
        "family": fam, "layers": FULL if fam == "wgm" else MID, "k": L, "w": r["window_width"],
        "variant": name, **cost,
        "b1_to_b2": r["b1_to_b2"], "b2_to_b1": r["b2_to_b1"],
        "pass": all(within(r[c], refs[c]) for c in ("b1_to_b2", "b2_to_b1")),
    })

(OUT / "frontier.json").write_text(json.dumps(rows), encoding="utf-8")

lines = []


def emit(s=""):
    lines.append(s)
    print(s)


for fam in ("wgm", "pdm"):
    rk, rw = REF[fam]
    emit(f"\n########## {fam.upper()} cheapest within tolerance (both directions) ##########")
    ok = [r for r in rows if r["family"] == fam and r["pass"]]
    ok.sort(key=lambda r: (r["ops_per_token"], r["state_bytes"]))
    emit(f"{len(ok)} configurations pass out of {len([r for r in rows if r['family']==fam])}")
    for r in ok[:12]:
        emit(f"  ops={r['ops_per_token']:6d} state={r['state_bytes']:7d}B k={r['k']} w={r['w']} "
             f"L={r['layers'].replace('+',',')} {r['variant']:28s} "
             f"R8 {r['b1_to_b2']['recall_plus_8']:.3f}/{r['b2_to_b1']['recall_plus_8']:.3f} "
             f"FAR {r['b1_to_b2']['far_all']:.3f}/{r['b2_to_b1']['far_all']:.3f}")
    # frontier: for each ops budget, best min-direction R8
    emit("  -- frontier (best min-direction +8 recall at or below each ops budget) --")
    fam_rows = sorted([r for r in rows if r["family"] == fam], key=lambda r: r["ops_per_token"])
    best = -1
    for r in fam_rows:
        m = min(r["b1_to_b2"]["recall_plus_8"], r["b2_to_b1"]["recall_plus_8"])
        maxfar = max(r["b1_to_b2"]["far_all"], r["b2_to_b1"]["far_all"])
        if maxfar > 0.15:
            continue
        if m > best:
            best = m
            emit(f"    ops={r['ops_per_token']:6d} state={r['state_bytes']:7d}B minR8={m:.3f} maxFAR={maxfar:.3f} "
                 f"k={r['k']} w={r['w']} L={r['layers'].replace('+',',')} {r['variant']}")

(OUT / "frontier_tables.txt").write_text("\n".join(lines), encoding="utf-8")
