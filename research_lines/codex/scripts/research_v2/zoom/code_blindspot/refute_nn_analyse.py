"""Refutation analysis: per-trace distances, block-median baselines, neighbour
identity, sub-reference counterfactuals.  Diagnostic, post-hoc."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import torch

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
CODE = {"b1-f2-012", "b1-f2-014", "b1-f3-016", "b2-f2-011", "b2-f2-012", "b2-f2-014", "b2-f2-015", "b2-f3-020"}

RE_CODE = re.compile(r"(```|SELECT |FROM |WHERE |GROUP BY|ORDER BY|JOIN |COUNT\(|\bdef \b|\breturn \b|\bfn \b|\blet \b|\bconst \b|\bfunction\b|=>|::|\bimport \b|\bpub fn|\bstruct \b)")


def code_body(text: str) -> bool:
    if RE_CODE.search(text):
        return True
    return sum(ch in "(){}[];=_<>" for ch in text) >= 3


def q(t: torch.Tensor, ps):
    return [float(torch.quantile(t, p)) for p in ps]


def pct(dist: torch.Tensor, v: float) -> float:
    return float((dist <= v).float().mean())


def main(band="middle_late", anchor="product"):
    report = {}
    for batch in ("b1", "b2"):
        c = torch.load(OUT / f"refute_nn_{band}_{batch}.pt", weights_only=False)
        meta = c["meta"]
        rd1 = c["routine_d"][:, 0]
        rd10 = c["routine_d"].mean(1)
        rd1_j = c["routine_d_json"][:, 0]
        rd1_p = c["routine_d_prose"][:, 0]
        rg1 = c["routine_g1"]
        rproj = c["routine_proj"]
        rt = c["routine_trace_idx"]
        isj = c["routine_is_json"]
        rtext = torch.load(OUT / f"refute_rtext_{batch}.pt", weights_only=False)["texts"]
        rforms = torch.load(OUT / f"refute_rtext_{batch}.pt", weights_only=False)["forms"]

        # ---- routine per-window reference ----
        ref = {
            "n_routine_windows": int(rd1.numel()),
            "json_base_rate": float(isj.float().mean()),
            "d1_q": q(rd1, [0.05, 0.25, 0.5, 0.75, 0.95, 0.99]),
            "d10_q": q(rd10, [0.5, 0.95]),
            "d1_jsonref_q": q(rd1_j, [0.5, 0.95]),
            "d1_proseref_q": q(rd1_p, [0.5, 0.95]),
            "g1_q": q(rg1, [0.05, 0.5, 0.95, 0.99]),
            "proj_q": q(rproj, [0.05, 0.5, 0.95]),
            "proj_json_q": q(rproj[isj], [0.05, 0.5, 0.95]),
            "proj_prose_q": q(rproj[~isj], [0.05, 0.5, 0.95]),
            "json_internal_d1_q": q(rd1_j[isj], [0.5, 0.95]),
        }
        # ---- routine BLOCK medians (matched to a 41-window target block) ----
        blk = defaultdict(list)
        for gi in range(rt.numel()):
            blk[int(rt[gi])].append(gi)
        bl_d1, bl_g1, bl_d10, bl_d1p = [], [], [], []
        for ti, idxs in blk.items():
            idxs = sorted(idxs)
            for s in range(0, max(1, len(idxs) - 41 + 1), 8):  # stride 8 blocks of 41 windows
                sel = torch.tensor(idxs[s : s + 41])
                if sel.numel() < 20:
                    continue
                bl_d1.append(float(rd1[sel].median()))
                bl_d10.append(float(rd10[sel].median()))
                bl_g1.append(float(rg1[sel].median()))
                bl_d1p.append(float(rd1_p[sel].median()))
        bl_d1 = torch.tensor(bl_d1); bl_g1 = torch.tensor(bl_g1)
        bl_d10 = torch.tensor(bl_d10); bl_d1p = torch.tensor(bl_d1p)
        ref["n_blocks"] = int(bl_d1.numel())
        ref["block_d1_q"] = q(bl_d1, [0.5, 0.9, 0.95, 0.99, 1.0])
        ref["block_d10_q"] = q(bl_d10, [0.5, 0.95, 1.0])
        ref["block_g1_q"] = q(bl_g1, [0.5, 0.9, 0.95, 0.99, 1.0])
        ref["block_d1prose_q"] = q(bl_d1p, [0.5, 0.95, 1.0])

        # ---- per-target-trace ----
        rows = {}
        by_trace = defaultdict(list)
        for i, m in enumerate(meta):
            if m["anchor"] != anchor:
                continue
            by_trace[m["trace_id"]].append(i)
        for tid, idxs in by_trace.items():
            ii = torch.tensor(idxs)
            d1 = c["target_d"][ii, 0]
            d10 = c["target_d"][ii].mean(1)
            d1p = c["target_d_prose"][ii, 0]
            d1j = c["target_d_json"][ii, 0]
            g1 = c["target_g1"][ii]
            pr = c["target_proj"][ii]
            nbf = [rforms[int(j)] for i2 in idxs for j in c["target_idx"][i2]]
            cb = [i2 for i2 in idxs if code_body(meta[i2]["text"])]
            nbf_cb = [rforms[int(j)] for i2 in cb for j in c["target_idx"][i2]]
            short = tid.split("-")[0] + "-" + tid.split("-")[1] + "-" + tid.split("-")[2]
            rows[tid] = {
                "short": short,
                "domain": meta[idxs[0]]["domain"],
                "pclass": meta[idxs[0]]["product_class"],
                "onset": meta[idxs[0]]["onset"],
                "n_win": len(idxs),
                "code_body_frac": round(len(cb) / len(idxs), 3),
                "d1_med": round(float(d1.median()), 2),
                "d1_pct_window": round(pct(rd1, float(d1.median())), 4),
                "d1_pct_block": round(pct(bl_d1, float(d1.median())), 4),
                "frac_below_rq95": round(float((d1 <= ref["d1_q"][4]).float().mean()), 3),
                "d10_med": round(float(d10.median()), 2),
                "d10_pct_block": round(pct(bl_d10, float(d10.median())), 4),
                "d1_prose_med": round(float(d1p.median()), 2),
                "d1_prose_pct_window": round(pct(rd1_p, float(d1p.median())), 4),
                "d1_prose_pct_block": round(pct(bl_d1p, float(d1p.median())), 4),
                "d1_json_med": round(float(d1j.median()), 2),
                "g1_med": round(float(g1.median()), 1),
                "g1_pct_window": round(pct(rg1, float(g1.median())), 4),
                "g1_pct_block": round(pct(bl_g1, float(g1.median())), 4),
                "proj_med": round(float(pr.median()), 2),
                "nb_json": round(Counter(nbf)["json"] / max(1, len(nbf)), 4),
                "nb_json_codebody": round(Counter(nbf_cb)["json"] / max(1, len(nbf_cb)), 4) if nbf_cb else None,
                "n_cb_windows": len(cb),
            }
        report[batch] = {"reference": ref, "traces": rows}
    (OUT / f"refute_analyse_{band}_{anchor}.json").write_text(json.dumps(report, indent=2))

    # ---- print ----
    for batch in ("b1", "b2"):
        r = report[batch]
        print(f"\n===== {batch} band={band} anchor={anchor} =====")
        print("routine ref:", json.dumps(r["reference"], indent=None))
        rows = r["traces"]
        codes = {k: v for k, v in rows.items() if v["domain"] == "programming"}
        others = {k: v for k, v in rows.items() if v["domain"] != "programming" and not v["pclass"].startswith("P3")}
        p3 = {k: v for k, v in rows.items() if v["pclass"].startswith("P3")}
        hdr = f"{'trace':46s} {'dom':16s} {'cls':4s} {'ons':>4s} {'nw':>3s} {'cb%':>5s} {'d1':>7s} {'pctW':>6s} {'pctB':>6s} {'d10':>7s} {'d1pr':>7s} {'prPctW':>7s} {'prPctB':>7s} {'g1':>8s} {'g1pW':>6s} {'g1pB':>6s} {'proj':>7s} {'nbJ':>6s} {'nbJcb':>6s}"
        print(hdr)
        def line(k, v):
            return (f"{v['short']:46s} {v['domain']:16s} {v['pclass']:4s} {v['onset']:4d} {v['n_win']:3d} {v['code_body_frac']*100:5.0f} "
                    f"{v['d1_med']:7.2f} {v['d1_pct_window']:6.3f} {v['d1_pct_block']:6.3f} {v['d10_med']:7.2f} "
                    f"{v['d1_prose_med']:7.2f} {v['d1_prose_pct_window']:7.3f} {v['d1_prose_pct_block']:7.3f} "
                    f"{v['g1_med']:8.1f} {v['g1_pct_window']:6.3f} {v['g1_pct_block']:6.3f} {v['proj_med']:7.2f} {v['nb_json']:6.3f} "
                    + (f"{v['nb_json_codebody']:6.3f}" if v['nb_json_codebody'] is not None else "   n/a"))
        for k, v in sorted(codes.items()):
            print("CODE ", line(k, v))
        for k, v in sorted(p3.items()):
            print("P3   ", line(k, v))
        for k, v in sorted(others.items(), key=lambda kv: kv[1]["d1_med"]):
            print("other", line(k, v))


if __name__ == "__main__":
    import sys
    main(*(sys.argv[1:] or []))
