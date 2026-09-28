"""REFUTER step A: independent recomputation of depth_profile claim (a) --
rmass / entropy / top-1 sharpening of code onto routine's own per-layer top-8,
plus the displacement-direction cosines.  Diagnostic only.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot/refute_depth_profile"
WIN, STRIDE = 48, 8
PROG = ["b1-f2-012", "b1-f2-014", "b1-f3-016", "b2-f2-011",
        "b2-f2-012", "b2-f2-014", "b2-f2-015", "b2-f3-020"]


def load():
    z = np.load(OUT / "tokens.npz")
    meta = json.loads((OUT / "meta.json").read_text())
    return z, meta


def slices(meta):
    off = np.cumsum([0] + [m["T"] for m in meta["traces"]])
    return {m["short"]: (off[i], off[i + 1]) for i, m in enumerate(meta["traces"])}, off


def main():
    z, meta = load()
    tr = meta["traces"]
    off = np.cumsum([0] + [m["T"] for m in tr])
    res = {}

    def arr(key):
        return z[key]

    # ---- routine held-out 48-token windows (null) -------------------------------
    keys = ("rmass_fit", "ent", "top1", "js", "jac")
    null_med = {k: [] for k in keys}
    null_owner = []
    for i, m in enumerate(tr):
        if not m["in_held"]:
            continue
        s, e = off[i], off[i + 1]
        T = m["T"]
        for a in range(0, max(1, T - WIN + 1), STRIDE):
            b = min(a + WIN, T)
            if b - a < 8:
                continue
            for k in keys:
                null_med[k].append(np.median(z[k][s + a:s + b], axis=0))
            null_owner.append(m["trace_id"])
    null = {k: np.stack(v) for k, v in null_med.items()}   # [nwin, 16]
    res["n_null_windows"] = int(null["ent"].shape[0])
    res["n_held_traces"] = len(set(null_owner))

    # ---- drift windows onset..onset+48 ------------------------------------------
    drift_rows = []
    for i, m in enumerate(tr):
        if m["arm_class"] != "drift":
            continue
        s, e = off[i], off[i + 1]
        T = m["T"]
        a = max(0, min(int(m["product_onset"]), T - 1))
        b = min(a + WIN, T)
        row = {"short": m["short"], "trace_id": m["trace_id"], "domain": m["domain"],
               "is_code": m["is_code"], "onset": int(m["product_onset"]),
               "product_class": m["product_class"], "batch": m["batch"],
               "n_win_tokens": int(b - a)}
        for k in keys:
            med = np.median(z[k][s + a:s + b], axis=0)
            row[k] = med.tolist()
            row[k + "_pct"] = [(float((null[k][:, l] < med[l]).mean() * 100.0)) for l in range(16)]
        # rmass under cross-batch references
        for refname in ("rmass_b1", "rmass_b2", "rmass_b1fit", "rmass_b2fit"):
            row[refname] = np.median(z[refname][s + a:s + b], axis=0).tolist()
        drift_rows.append(row)
    res["drift"] = drift_rows

    # ---- routine / resist per-trace medians over their whole trace ---------------
    res["null_median_by_layer"] = {k: np.median(null[k], axis=0).tolist() for k in keys}
    code_rows = [r for r in drift_rows if r["is_code"]]
    other_rows = [r for r in drift_rows if not r["is_code"]]
    res["group_median"] = {
        "code": {k: np.median(np.array([r[k] for r in code_rows]), axis=0).tolist() for k in keys},
        "other": {k: np.median(np.array([r[k] for r in other_rows]), axis=0).tolist() for k in keys},
    }
    # per-domain rmass (mean of layers 11-14, as the report's headline)
    dom = {}
    for r in drift_rows:
        d = "programming" if r["is_code"] else r["domain"]
        dom.setdefault(d, []).append(float(np.mean(r["rmass_fit"][11:15])))
    res["rmass_L11_14_by_domain"] = {k: sorted(v) for k, v in dom.items()}

    # routine trace-level rmass L11-14 (held-out), for the "8/8 above routine" claim
    rt = []
    for i, m in enumerate(tr):
        if not m["in_held"]:
            continue
        s, e = off[i], off[i + 1]
        rt.append(float(np.mean(np.median(z["rmass_fit"][s:e], axis=0)[11:15])))
    res["routine_held_trace_rmass_L11_14"] = {
        "n": len(rt), "median": float(np.median(rt)), "q90": float(np.quantile(rt, .9)),
        "q95": float(np.quantile(rt, .95)), "max": float(max(rt)),
    }
    res["routine_held_window_rmass_L11_14"] = {
        "n": int(null["rmass_fit"].shape[0]),
        "median": float(np.median(null["rmass_fit"][:, 11:15].mean(1))),
        "q95": float(np.quantile(null["rmass_fit"][:, 11:15].mean(1), .95)),
        "max": float(null["rmass_fit"][:, 11:15].mean(1).max()),
    }
    (OUT / "claim_a.json").write_text(json.dumps(res), encoding="utf-8")

    # ---- printout ---------------------------------------------------------------
    print(f"null windows={res['n_null_windows']} held traces={res['n_held_traces']}")
    print("\n## rmass median (percentile among 851 held-out routine windows), product anchor")
    print("| trace | " + " | ".join(f"L{l}" for l in (4, 5, 8, 9, 10, 11, 12, 13, 14, 15)) + " |")
    for sid in PROG:
        r = next(x for x in drift_rows if x["short"] == sid)
        cells = " | ".join(f"{r['rmass_fit'][l]:.2f}({r['rmass_fit_pct'][l]:.0f})"
                           for l in (4, 5, 8, 9, 10, 11, 12, 13, 14, 15))
        print(f"| {sid} | {cells} |")
    nm = res["null_median_by_layer"]["rmass_fit"]
    print("| routine median | " + " | ".join(f"{nm[l]:.2f}" for l in (4, 5, 8, 9, 10, 11, 12, 13, 14, 15)) + " |")
    om = res["group_median"]["other"]["rmass_fit"]
    print("| other-drift median | " + " | ".join(f"{om[l]:.2f}" for l in (4, 5, 8, 9, 10, 11, 12, 13, 14, 15)) + " |")

    print("\n## L13 entropy / top1 (percentile)")
    for sid in PROG:
        r = next(x for x in drift_rows if x["short"] == sid)
        print(f"{sid}: ent {r['ent'][13]:.3f} (pct {r['ent_pct'][13]:.0f})  "
              f"top1 {r['top1'][13]:.3f} (pct {r['top1_pct'][13]:.0f})  "
              f"rmassL11 {r['rmass_fit'][11]:.3f} (pct {r['rmass_fit_pct'][11]:.0f})")
    print(f"routine median ent L13 {res['null_median_by_layer']['ent'][13]:.3f}, "
          f"top1 {res['null_median_by_layer']['top1'][13]:.3f}")
    print(f"other-drift median ent L13 {res['group_median']['other']['ent'][13]:.3f}, "
          f"top1 {res['group_median']['other']['top1'][13]:.3f}")

    print("\n## rmass mean over L11-14, per drift trace, sorted by domain")
    for d, v in sorted(res["rmass_L11_14_by_domain"].items()):
        print(f"{d:20s} n={len(v):2d}  " + " ".join(f"{x:.3f}" for x in v))
    print("\nroutine held trace-level rmass L11-14:", res["routine_held_trace_rmass_L11_14"])
    print("routine held window-level rmass L11-14:", res["routine_held_window_rmass_L11_14"])


if __name__ == "__main__":
    main()
