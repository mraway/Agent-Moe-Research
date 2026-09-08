"""Matched control group for critic E7 -- robustness / confound checks.

(a) threshold sensitivity of the STRUCT/PROSE split;
(b) WITHIN-DOMAIN Spearman (structure score is largely a domain proxy);
(c) domain-matched STRUCT vs PROSE contrasts (only domains holding both);
(d) code-band overlap counts: how many non-code drifts fall inside the code range;
(e) a shape-adjudicated variant of STRUCT (drop the two LaTeX-formula math windows and
    the tagged-haiku window that the mechanical rule sweeps in).

Read-only post-hoc diagnosis on B1/B2.  Not a detector; no improvement claimed.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from structured_control_analyse import spearman  # noqa: E402

REPO = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
OUT = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot/structured_control"
# mechanical-rule members whose decoded window is NOT a list-like deliverable
SHAPE_EXCLUDE = ("b2-f4-024", "b2-f4-025", "b1-f1-008")  # LaTeX formula x2, tagged haiku
METRICS = ("candA_med_q95", "candA_max16_q95", "rare", "rmass_L11_13")


def val(r, k):
    return {"candA_med_q95": r["candA"]["median_over_q95"],
            "candA_max16_q95": r["candA"]["max16_over_q95"],
            "rare": r["rare_expert_mass"],
            "rmass_L11_13": r["rmass"]["L11_13_mean"]}[k]


def main() -> None:
    a = json.loads((OUT / "analysis.json").read_text())
    rows = a["rows"]
    nonprog = [r for r in rows if r["domain"] != "programming"]
    code = [r for r in rows if r["domain"] == "programming"]
    out = {}

    # (a) threshold sensitivity ------------------------------------------------------
    thr_tab = []
    for th in (0.13, 0.15, 0.1875001, 0.20, 0.2051, 0.22, 0.25, 0.30):
        s = [r for r in nonprog if r["structure_score"] >= th]
        p = [r for r in nonprog if r["structure_score"] < th]
        row = {"threshold": th, "n_struct": len(s), "n_prose": len(p),
               "p3_all_in_struct": all(r["product_class"] != "P3" or r in s
                                       for r in nonprog)}
        for k in METRICS:
            row[f"struct_median_{k}"] = float(np.median([val(r, k) for r in s])) if s else None
            row[f"prose_median_{k}"] = float(np.median([val(r, k) for r in p])) if p else None
        row["struct_no_candA_alarm"] = sum(
            1 for r in s if r["candA_alarm"]["offset_from_product_onset"] is None)
        thr_tab.append(row)
    out["threshold_sensitivity"] = thr_tab

    # (b) within-domain Spearman ------------------------------------------------------
    by_dom = defaultdict(list)
    for r in nonprog:
        by_dom[r["domain"]].append(r)
    wd = {}
    for dom, rs in sorted(by_dom.items()):
        if len(rs) < 5:
            continue
        wd[dom] = {"n": len(rs)}
        for k in METRICS:
            rho, p = spearman(np.asarray([r["structure_score"] for r in rs]),
                              np.asarray([val(r, k) for r in rs]))
            wd[dom][k] = {"rho": rho, "p": p}
    # pooled within-domain: average of Fisher-z over domains with n>=5, and a
    # domain-demeaned Spearman on ranks-within-domain
    pooled = {}
    for k in METRICS:
        xs, ys = [], []
        for dom, rs in by_dom.items():
            if len(rs) < 3:
                continue
            x = np.asarray([r["structure_score"] for r in rs], float)
            y = np.asarray([val(r, k) for r in rs], float)
            xs.append(x - x.mean()); ys.append(y - y.mean())
        X, Y = np.concatenate(xs), np.concatenate(ys)
        rho, p = spearman(X, Y)
        pooled[k] = {"n": int(X.size), "rho": rho, "p": p,
                     "note": "domain-demeaned values, domains with n>=3, 7 non-programming domains"}
    out["within_domain"] = {"per_domain": wd, "domain_demeaned_pooled": pooled}

    # (c) domain-matched STRUCT vs PROSE ---------------------------------------------
    matched = {}
    for dom, rs in sorted(by_dom.items()):
        s = [r for r in rs if r["group"] == "STRUCT"]
        p = [r for r in rs if r["group"] == "PROSE"]
        if not s or not p:
            continue
        matched[dom] = {"n_struct": len(s), "n_prose": len(p)}
        for k in METRICS:
            matched[dom][k] = {"struct_median": float(np.median([val(r, k) for r in s])),
                               "prose_median": float(np.median([val(r, k) for r in p])),
                               "struct": [round(val(r, k), 4) for r in s],
                               "prose": [round(val(r, k), 4) for r in p]}
    out["domain_matched"] = matched

    # (d) code-band overlap -----------------------------------------------------------
    band = {}
    for k in METRICS:
        cv = [val(r, k) for r in code]
        lo, hi = min(cv), max(cv)
        inside = [r["short"] for r in nonprog if lo <= val(r, k) <= hi]
        below = [r["short"] for r in nonprog if val(r, k) < lo]
        band[k] = {"code_min": lo, "code_max": hi,
                   "n_noncode_inside": len(inside), "noncode_inside": inside,
                   "n_noncode_below": len(below), "noncode_below": below,
                   "code_rank_of_worst": None}
        allv = sorted(rows, key=lambda r: val(r, k))
        order = [r["short"] for r in allv]
        band[k]["code_ranks_ascending"] = {r["short"]: order.index(r["short"]) + 1 for r in code}
    out["code_band_overlap"] = band

    # (e) shape-adjudicated STRUCT ------------------------------------------------------
    s2 = [r for r in nonprog if r["group"] == "STRUCT" and r["short"] not in SHAPE_EXCLUDE]
    p2 = [r for r in nonprog if r["group"] == "PROSE" or r["short"] in SHAPE_EXCLUDE]
    shp = {"excluded": list(SHAPE_EXCLUDE), "n_struct": len(s2), "n_prose": len(p2)}
    for k in METRICS:
        shp[k] = {"struct_median": float(np.median([val(r, k) for r in s2])),
                  "struct_min": float(min(val(r, k) for r in s2)),
                  "struct_max": float(max(val(r, k) for r in s2)),
                  "prose_median": float(np.median([val(r, k) for r in p2])),
                  "code_median": float(np.median([val(r, k) for r in code]))}
    shp["struct_no_candA_alarm"] = sum(
        1 for r in s2 if r["candA_alarm"]["offset_from_product_onset"] is None)
    shp["struct_candA_alarm_within16"] = sum(
        1 for r in s2 if (r["candA_alarm"]["offset_from_product_onset"] is not None
                          and 0 <= r["candA_alarm"]["offset_from_product_onset"] <= 16))
    out["shape_adjudicated"] = shp

    # (f) tool-call P3 subgroup ---------------------------------------------------------
    p3 = [r for r in nonprog if r["product_class"] == "P3"]
    out["p3_toolcall"] = {"members": [r["short"] for r in p3],
                          **{k: [round(val(r, k), 4) for r in p3] for k in METRICS},
                          "candA_offsets": [r["candA_alarm"]["offset_from_product_onset"] for r in p3],
                          "candB_offsets": [r["candB_alarm"]["offset_from_product_onset"] for r in p3]}

    (OUT / "robust.json").write_text(json.dumps(out, indent=2))
    print(json.dumps({k: out[k] for k in ("within_domain", "code_band_overlap",
                                          "shape_adjudicated", "p3_toolcall")}, indent=1)[:6000])
    print("--- threshold sensitivity ---")
    for r in thr_tab:
        print(f"th={r['threshold']:.4f} nS={r['n_struct']:2d} nP={r['n_prose']:2d} "
              f"S_medq95={r['struct_median_candA_med_q95']:.3f} P={r['prose_median_candA_med_q95']:.3f} "
              f"S_rare={r['struct_median_rare']:.4f} P={r['prose_median_rare']:.4f} "
              f"S_rm={r['struct_median_rmass_L11_13']:.3f} P={r['prose_median_rmass_L11_13']:.3f}")
    print("--- domain matched ---")
    for dom, v in matched.items():
        print(f"{dom:18s} nS={v['n_struct']} nP={v['n_prose']} "
              f"medq95 S={v['candA_med_q95']['struct_median']:.3f} P={v['candA_med_q95']['prose_median']:.3f} | "
              f"rare S={v['rare']['struct_median']:.4f} P={v['rare']['prose_median']:.4f} | "
              f"rm S={v['rmass_L11_13']['struct_median']:.3f} P={v['rmass_L11_13']['prose_median']:.3f}")
    print("wrote", OUT / "robust.json")


if __name__ == "__main__":
    main()
