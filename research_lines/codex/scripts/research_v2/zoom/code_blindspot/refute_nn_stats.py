"""Refutation stats: (a) position-matched references, (b) window-level exceedance,
(c) orthogonal decomposition at matched granularity, (d) routine edge-attractor
control, (e) neighbour-identity vs distance dissociation.  Diagnostic, post-hoc."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import torch

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
RE_CODE = re.compile(r"(```|SELECT |FROM |WHERE |GROUP BY|ORDER BY|JOIN |COUNT\(|\bdef \b|\breturn \b|\bfn \b|\blet \b|\bconst \b|\bfunction\b|=>|::|\bimport \b|\bpub fn|\bstruct \b)")


def code_body(t: str) -> bool:
    return bool(RE_CODE.search(t)) or sum(ch in "(){}[];=_<>" for ch in t) >= 3


def qs(t, ps):
    return [round(float(torch.quantile(t, p)), 2) for p in ps]


def main(band="middle_late", anchor="product"):
    res = {}
    for batch in ("b1", "b2"):
        c = torch.load(OUT / f"refute_nn_{band}_{batch}.pt", weights_only=False)
        meta = c["meta"]
        rd1 = c["routine_d"][:, 0]; rg1 = c["routine_g1"]; rproj = c["routine_proj"]
        rend = c["routine_end"]; rt = c["routine_trace_idx"]; isj = c["routine_is_json"]
        rforms = torch.load(OUT / f"refute_rtext_{batch}.pt", weights_only=False)["forms"]
        ridx = c["routine_idx"]
        # orthogonal distance to axis (||z-centre||^2 - proj^2)
        rorth = (rg1 - rproj**2).clamp_min(0).sqrt()
        qg1 = c["target_g1"]; qproj = c["target_proj"]
        qorth = (qg1 - qproj**2).clamp_min(0).sqrt()
        qd1 = c["target_d"][:, 0]

        out = {}
        # ---- (a) position-stratified routine reference (32-token buckets) ----
        buckets = (rend // 32).clamp(max=5)
        pos_ref = {}
        for b in range(6):
            m = buckets == b
            if int(m.sum()) < 50:
                continue
            pos_ref[b] = {"n": int(m.sum()), "d1_q": qs(rd1[m], [0.5, 0.9, 0.95, 0.99]),
                          "g1_q": qs(rg1[m], [0.5, 0.9, 0.95, 0.99]),
                          "orth_q": qs(rorth[m], [0.5, 0.95])}
        out["position_reference"] = pos_ref
        out["routine_orth_q"] = qs(rorth, [0.5, 0.95])

        # ---- (c) block-median orthogonal / proj reference ----
        blk = defaultdict(list)
        for gi in range(rt.numel()):
            blk[int(rt[gi])].append(gi)
        bo = []
        for ti, idxs in blk.items():
            idxs = sorted(idxs)
            for s in range(0, max(1, len(idxs) - 41 + 1), 8):
                sel = torch.tensor(idxs[s : s + 41])
                if sel.numel() < 20:
                    continue
                bo.append(float(rorth[sel].median()))
        bo = torch.tensor(bo)
        out["block_orth_q"] = qs(bo, [0.5, 0.95, 0.99, 1.0])

        # ---- (d) edge-attractor control: routine non-JSON windows by d1 decile ----
        nonj = torch.nonzero(~isj).flatten()
        d_nonj = rd1[nonj]
        order = torch.argsort(d_nonj)
        dec = {}
        n = order.numel()
        for k in range(10):
            sel = nonj[order[int(k * n / 10) : int((k + 1) * n / 10)]]
            nb = [rforms[int(j)] for i in sel.tolist() for j in ridx[i]]
            dec[k] = {"d1_range": [round(float(rd1[sel].min()), 2), round(float(rd1[sel].max()), 2)],
                      "n": int(sel.numel()),
                      "nb_json": round(Counter(nb)["json"] / max(1, len(nb)), 4)}
        # extreme tail
        tail = nonj[order[int(0.99 * n) :]]
        nb = [rforms[int(j)] for i in tail.tolist() for j in ridx[i]]
        out["routine_nonjson_by_d1_decile"] = dec
        out["routine_nonjson_top1pct"] = {"n": int(tail.numel()),
                                          "d1_min": round(float(rd1[tail].min()), 2),
                                          "nb_json": round(Counter(nb)["json"] / max(1, len(nb)), 4)}

        # ---- per-trace, per-window level ----
        by_trace = defaultdict(list)
        for i, m in enumerate(meta):
            if m["anchor"] == anchor:
                by_trace[m["trace_id"]].append(i)
        rows = {}
        for tid, idxs in by_trace.items():
            ii = torch.tensor(idxs)
            ends = torch.tensor([meta[i]["end"] for i in idxs])
            b = (ends // 32).clamp(max=5)
            # position-matched percentile of each window's g1 / d1
            pm_g1, pm_d1 = [], []
            for j, i in enumerate(idxs):
                bb = int(b[j])
                m = buckets == bb
                pm_g1.append(float((rg1[m] <= qg1[i]).float().mean()))
                pm_d1.append(float((rd1[m] <= qd1[i]).float().mean()))
            pm_g1 = torch.tensor(pm_g1); pm_d1 = torch.tensor(pm_d1)
            nbj = []
            for i in idxs:
                f = [rforms[int(j)] for j in c["target_idx"][i]]
                nbj.append(Counter(f)["json"] / len(f))
            nbj = torch.tensor(nbj)
            cbm = torch.tensor([code_body(meta[i]["text"]) for i in idxs])
            rows[tid] = {
                "domain": meta[idxs[0]]["domain"], "pclass": meta[idxs[0]]["product_class"],
                "onset": meta[idxs[0]]["onset"], "n": len(idxs),
                "g1_frac_above_rq90": round(float((qg1[ii] > torch.quantile(rg1, 0.9)).float().mean()), 3),
                "g1_frac_above_rq95": round(float((qg1[ii] > torch.quantile(rg1, 0.95)).float().mean()), 3),
                "g1_frac_above_rq99": round(float((qg1[ii] > torch.quantile(rg1, 0.99)).float().mean()), 3),
                "d1_frac_above_rq90": round(float((qd1[ii] > torch.quantile(rd1, 0.9)).float().mean()), 3),
                "pm_g1_pct_med": round(float(pm_g1.median()), 3),
                "pm_d1_pct_med": round(float(pm_d1.median()), 3),
                "orth_med": round(float(qorth[ii].median()), 2),
                "orth_pct_block": round(float((bo <= float(qorth[ii].median())).float().mean()), 3),
                "nbj_med": round(float(nbj.median()), 3),
                "d1_med_cb": round(float(qd1[ii][cbm].median()), 2) if bool(cbm.any()) else None,
                "nbj_med_cb": round(float(nbj[cbm].median()), 3) if bool(cbm.any()) else None,
                "n_cb": int(cbm.sum()),
            }
        out["traces"] = rows
        res[batch] = out

        print(f"\n===== {batch} {band} {anchor} =====")
        print("routine orth q50/q95:", out["routine_orth_q"], " block-median orth q50/q95/q99/max:", out["block_orth_q"])
        print("position buckets (end//32):")
        for b, v in pos_ref.items():
            print(f"  bucket {b} n={v['n']:6d} d1 q50/q90/q95/q99={v['d1_q']} g1={v['g1_q']} orth={v['orth_q']}")
        print("routine NON-JSON windows, neighbour json share by own-d1 decile:")
        for k, v in dec.items():
            print(f"  dec{k} d1{v['d1_range']} n={v['n']:5d} nb_json={v['nb_json']}")
        print("  top1pct:", out["routine_nonjson_top1pct"])
        print(f"{'trace':44s} {'dom':16s} {'g1>q90':>7s} {'g1>q95':>7s} {'g1>q99':>7s} {'d1>q90':>7s} {'pmG1':>6s} {'pmD1':>6s} {'orth':>7s} {'orthB':>6s} {'nbj':>6s} {'d1cb':>7s} {'nbjcb':>6s} {'ncb':>4s}")
        for tid, v in sorted(rows.items(), key=lambda kv: (kv[1]["domain"] != "programming", kv[1]["domain"])):
            tag = "CODE " if v["domain"] == "programming" else ("P3   " if v["pclass"].startswith("P3") else "other")
            print(f"{tag}{tid[:39]:39s} {v['domain']:16s} {v['g1_frac_above_rq90']:7.3f} {v['g1_frac_above_rq95']:7.3f} {v['g1_frac_above_rq99']:7.3f} {v['d1_frac_above_rq90']:7.3f} {v['pm_g1_pct_med']:6.3f} {v['pm_d1_pct_med']:6.3f} {v['orth_med']:7.2f} {v['orth_pct_block']:6.3f} {v['nbj_med']:6.3f} " + (f"{v['d1_med_cb']:7.2f} {v['nbj_med_cb']:6.3f} {v['n_cb']:4d}" if v['d1_med_cb'] is not None else "    n/a    n/a    0"))
    (OUT / f"refute_stats_{band}_{anchor}.json").write_text(json.dumps(res, indent=2))


if __name__ == "__main__":
    import sys
    main(*(sys.argv[1:] or []))
