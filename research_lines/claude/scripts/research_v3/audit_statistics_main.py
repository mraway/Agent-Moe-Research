"""LENS = statistics: full adversarial recompute of the frozen TRM-3 cells.

Read-only.  Emits a JSON blob on stdout (and a human digest on stderr).
"""
from __future__ import annotations

import json
import math
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_statistics_lib as L  # noqa: E402
import audit_statistics_recompute as R  # noqa: E402

CELLS = {
    "b2": "final_b2_both",
    "b1": "final_b1_both",
    "h384": "final_h384_both",
    "c1_heldout": "final_c1_heldout_C1",
}
OUT: dict = {}


def anchors_for(tag, summaries, which="primary"):
    if tag == "h384":
        p, s = R.h384_anchor_maps()
        return p if which == "primary" else s
    if tag == "c1_heldout":
        return {}
    if which == "primary":
        return R.core_anchors(summaries, "product")
    return R.core_anchors(summaries, "evidence")


def hits_at(summaries_by_key, keys, anchors, alarm_ends_by_key, band=0, horizon=8):
    out = {}
    for k in keys:
        s = summaries_by_key[k]
        b = R.anchor_hit(alarm_ends_by_key[k], s["last_end"], anchors[k], band=band)
        out[k] = b[f"hit_plus_{horizon}"] if horizon else b["hit_final"]
    return out


def cell_block(tag, cell):
    d = L.load(cell)
    block = {"cell": cell, "target": d["target"], "columns": {}}
    for col in d["calibration_columns"]:
        C = d["columns"][col]
        smaps = {v: {x["key"]: x for x in C["variants"][v]["sets"]["target"]["summaries"]}
                 for v in C["variants"]}
        trm = C["variants"]["trm3"]["sets"]["target"]
        anch = anchors_for(tag, trm["summaries"], "primary")
        pos = [s["key"] for s in R.positives_of(trm["summaries"], anch)] if anch else []
        sd = (trm.get("spontaneous_drift") or {}).get("keys", []) or []
        cb = {
            "n_reference_by_half": {h: b["n_reference"]
                                    for h, b in C["variants"]["trm3"]["alpha_budget"]["by_half"].items()},
            "alpha_eff_by_half": {h: b["effective"]["alpha_eff"]
                                  for h, b in C["variants"]["trm3"]["alpha_budget"]["by_half"].items()},
            "channel_attainable": {h: {c: v["attainable"] for c, v in b["effective"]["channels"].items()}
                                   for h, b in C["variants"]["trm3"]["alpha_budget"]["by_half"].items()},
            "positive_count": len(pos),
            "variants": {},
        }
        for v in C["variants"]:
            T = C["variants"][v]["sets"]["target"]
            fb = R.far_block(T["summaries"], excluded=sd)
            entry = {"far": {k: fb[k] for k in ("clean", "benign", "pooled", "matched_group")},
                     "far_halves": {str(h): fb["halves"][h] for h in fb["halves"]}}
            if pos:
                sm = smaps[v]
                blocks = {k: R.anchor_hit(sm[k]["alarm_ends"], sm[k]["last_end"], anch[k]) for k in pos}
                rb = R.recall_block(blocks)
                entry["recall_strict"] = {kk: rb[kk] for kk in ("r8", "r16", "r32", "r64", "final", "pre")}
                tb = {k: R.anchor_hit(sm[k]["alarm_ends"], sm[k]["last_end"], anch[k], band=5) for k in pos}
                rt = R.recall_block(tb)
                entry["recall_tolerant"] = {kk: rt[kk] for kk in ("r8", "r16", "r32", "r64", "final", "pre")}
                entry["hits8"] = {k: blocks[k]["hit_plus_8"] for k in pos}
            cb["variants"][v] = entry
        block["columns"][col] = cb
    return block


for tag, cell in CELLS.items():
    OUT[tag] = cell_block(tag, cell)

print(json.dumps(OUT))
