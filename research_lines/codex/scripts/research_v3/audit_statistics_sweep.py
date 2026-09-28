"""B-M rank sweep from the stored per-channel p streams (LENS = statistics).

For every (target, calibration column) the M-channel conformal p stream inside the frozen
TRM-3 outputs is thresholded at every attainable rank j/(n_half+1); for each rank the
routine FAR and the primary-event +8 recall are recomputed and McNemar is run against the
frozen TRM-3 decisions.  Read-only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_statistics_lib as L  # noqa: E402
import audit_statistics_recompute as R  # noqa: E402

CELLS = {"b2": "final_b2_both", "b1": "final_b1_both", "h384": "final_h384_both"}
OUT = {}

for tag, cell in CELLS.items():
    d = L.load(cell)
    OUT[tag] = {}
    for col in d["calibration_columns"]:
        C = d["columns"][col]
        trm = C["variants"]["trm3"]["sets"]["target"]
        summaries = trm["summaries"]
        smap = {s["key"]: s for s in summaries}
        anch = (R.h384_anchor_maps()[0] if tag == "h384" else R.core_anchors(summaries, "product"))
        pos = [s["key"] for s in R.positives_of(summaries, anch)]
        sd = set((trm.get("spontaneous_drift") or {}).get("keys", []) or [])
        nref = {h: b["n_reference"] for h, b in C["variants"]["trm3"]["alpha_budget"]["by_half"].items()}
        streams = R.load_streams(cell, col)
        # frozen TRM-3 hits / alarms
        trm_hits = {k: R.anchor_hit(smap[k]["alarm_ends"], smap[k]["last_end"], anch[k])["hit_plus_8"]
                    for k in pos}
        trm_far = R.far_block(summaries, excluded=sd)
        ranks = {}
        maxrank = 16
        for j in range(1, maxrank + 1):
            # per-half threshold j/(n_half+1)
            ends = {}
            for k, s in smap.items():
                if k not in streams:
                    continue
                n = nref[str(s["calibration_half"])]
                thr = j / (n + 1.0)
                ends[k] = R.alarms_from_stream(streams[k], 1, thr)
            fake = []
            for s in summaries:
                t = dict(s)
                e = ends.get(s["key"], [])
                t["alarm"] = bool(e)
                t["alarm_ends"] = e
                fake.append(t)
            fb = R.far_block(fake, excluded=sd)
            fmap = {t["key"]: t for t in fake}
            hits = {k: R.anchor_hit(fmap[k]["alarm_ends"], fmap[k]["last_end"], anch[k])["hit_plus_8"]
                    for k in pos}
            mc = L.mcnemar_from_hits(trm_hits, hits)
            ranks[j] = {
                "alpha_by_half": {h: j / (n + 1.0) for h, n in nref.items()},
                "far": {kk: fb[kk] for kk in ("clean", "benign", "pooled", "matched_group")},
                "r8": (sum(1 for v in hits.values() if v), len(hits)),
                "mcnemar_trm3_vs_bm": mc,
            }
        OUT[tag][col] = {
            "n_reference_by_half": nref,
            "trm3_r8": (sum(1 for v in trm_hits.values() if v), len(trm_hits)),
            "trm3_far": {kk: trm_far[kk] for kk in ("clean", "benign", "pooled", "matched_group")},
            "ranks": ranks,
        }
print(json.dumps(OUT))
