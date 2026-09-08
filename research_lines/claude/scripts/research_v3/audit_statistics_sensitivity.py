"""Anchor / tolerance / hit-definition sensitivity of the P1 comparison. Read-only."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_statistics_lib as L  # noqa: E402
import audit_statistics_recompute as R  # noqa: E402

CELLS = {"b2": "final_b2_both", "b1": "final_b1_both", "h384": "final_h384_both"}


def bm_ends(cell, col, C, smt):
    nref = {h: b["n_reference"] for h, b in C["variants"]["trm3"]["alpha_budget"]["by_half"].items()}
    rank = list(C["p1_matched_alpha"]["by_half"].values())[0]["rank"]
    streams = R.load_streams(cell, col)
    return {k: R.alarms_from_stream(streams[k], 1, rank / (nref[str(smt[k]["calibration_half"])] + 1.0))
            for k in smt if k in streams}


if __name__ == "__main__":
    print("cell/col  anchor                band  n  TRM3  B-M(matched)  onlyA onlyB  net     p")
    for tag, cell in CELLS.items():
        d = L.load(cell)
        for col in d["calibration_columns"]:
            C = d["columns"][col]
            summ = C["variants"]["trm3"]["sets"]["target"]["summaries"]
            smt = {s["key"]: s for s in summ}
            bm = bm_ends(cell, col, C, smt)
            if tag == "h384":
                pa, sa = R.h384_anchor_maps()
                variants = [("engagement_onset(primary)", pa), ("execution_onset(secondary)", sa)]
            else:
                variants = [("product_onset(primary)", R.core_anchors(summ, "product")),
                            ("evidence_onset", R.core_anchors(summ, "evidence"))]
            for aname, anch in variants:
                pos = [s["key"] for s in R.positives_of(summ, anch)]
                for band in (0, 5):
                    ht = {k: R.anchor_hit(smt[k]["alarm_ends"], smt[k]["last_end"], anch[k], band=band)["hit_plus_8"] for k in pos}
                    hb = {k: R.anchor_hit(bm[k], smt[k]["last_end"], anch[k], band=band)["hit_plus_8"] for k in pos}
                    mc = L.mcnemar_from_hits(ht, hb)
                    print(f"{tag}/{col:2s}  {aname:26s} {band}  {len(pos):3d} {sum(ht.values()):4d}  {sum(hb.values()):6d}       {mc['only_a']:4d} {mc['only_b']:5d} {mc['net']:+5d}  {mc['p']:.4f}")
            # hit definition without the pre-onset penalty
            anch = variants[0][1]
            pos = [s["key"] for s in R.positives_of(summ, anch)]
            at = {k: any(e <= anch[k] + 8 for e in smt[k]["alarm_ends"]) for k in pos}
            ab = {k: any(e <= anch[k] + 8 for e in bm[k]) for k in pos}
            mc = L.mcnemar_from_hits(at, ab)
            print(f"{tag}/{col:2s}  {'any-alarm-by-+8 (no pre-onset penalty)':26s} -  {len(pos):3d} {sum(at.values()):4d}  {sum(ab.values()):6d}       {mc['only_a']:4d} {mc['only_b']:5d} {mc['net']:+5d}  {mc['p']:.4f}")
            print()
