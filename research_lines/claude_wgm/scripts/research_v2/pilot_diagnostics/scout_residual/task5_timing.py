"""Task 5: boundary-aligned timing of the 16-token causal window score (middle layers, w and E fitted on B1)
on B2 drift traces vs the same output positions in the matched clean arm of the same scenario.
Post-hoc pilot diagnostic on development data; not a result."""
import numpy as np, torch
from common import *

data = {b: stack_batch(load_batch(b)) for b in ("b1", "b2")}
rows = {b: load_batch(b) for b in ("b1", "b2")}
tab = routine_tables(data["b1"], "p")
mu = tab["mu"]
OFFSETS = list(range(-32, 49))


def feats(d):
    E = shrunk_expectation(tab, d["ids"], mu=mu)
    return {"raw_p": d["p"], "residual": d["p"] - E, "E_token_only": E}


fx, fy = feats(data["b1"]), feats(data["b2"])
clean_by_group = {r["pair_group_id"]: i for i, r in enumerate(rows["b2"]) if r["arm"] == "clean"}
tr = data["b2"]["trace"].numpy(); pos = data["b2"]["pos"].numpy()
out = {"note": "post-hoc pilot diagnostic on development data; not a result",
       "setup": "w = diagonal LDA fitted on B1 (DRIFT_POST vs R), E[p|token] fitted on R(B1) with m=5; middle layers 5-11; "
                "16-token causal window mean (partial window for t<15); B2 drift traces vs matched clean arm at same output index",
       "offsets": OFFSETS, "curves": {}}
for fname in ("raw_p", "residual", "E_token_only"):
    w = diag_lda(fx[fname][data["b1"]["grp"] == 1], fx[fname][data["b1"]["grp"] == 0])
    s = band_scores(fy[fname], w, "middle")
    wm = causal_window_means(s, data["b2"]["trace"], full_only=False)
    per_trace = {i: wm[tr == i] for i in range(len(rows["b2"]))}
    # standardize by routine-window distribution in B1 (fit batch) so units are comparable: use B1 routine window sd
    s1 = band_scores(fx[fname], w, "middle"); wm1 = causal_window_means(s1, data["b1"]["trace"], full_only=True)
    wl1 = data["b1"]["grp"].numpy()
    ref_mean = float(np.nanmean(wm1[wl1 == 0])); ref_sd = float(np.nanstd(wm1[wl1 == 0]))
    drift_curve, clean_curve, diff_curve = [], [], []
    for o in OFFSETS:
        dv, cv = [], []
        for i, r in enumerate(rows["b2"]):
            if not r["positive"]:
                continue
            t = r["boundary"] + o
            ci = clean_by_group[r["pair_group_id"]]
            if t < 0 or t >= len(per_trace[i]) or t >= len(per_trace[ci]):
                continue
            dv.append(per_trace[i][t]); cv.append(per_trace[ci][t])
        dv, cv = np.array(dv), np.array(cv)
        drift_curve.append({"offset": o, "n": int(len(dv)), "drift_mean": float(dv.mean()) if len(dv) else None,
                            "clean_mean": float(cv.mean()) if len(cv) else None,
                            "drift_sd": float(dv.std()) if len(dv) else None, "clean_sd": float(cv.std()) if len(cv) else None,
                            "pooled_sd": float(np.sqrt(0.5 * (dv.var() + cv.var()))) if len(dv) else None,
                            "paired_diff_mean": float((dv - cv).mean()) if len(dv) else None,
                            "paired_auroc": auroc(dv, cv) if len(dv) else None})
    first = None
    for c in drift_curve:
        if c["n"] >= 5 and c["pooled_sd"] and (c["drift_mean"] - c["clean_mean"]) > 2 * c["pooled_sd"]:
            first = c["offset"]; break
    # also: first offset where drift mean exceeds clean mean by > 2 SD and stays so for the rest of the offsets with n>=5
    persistent = None
    exceed = [(c["offset"], bool(c["pooled_sd"]) and (c["drift_mean"] - c["clean_mean"]) > 2 * c["pooled_sd"])
              for c in drift_curve if c["n"] >= 5]
    for k, (o, e) in enumerate(exceed):
        if e and all(ee for _, ee in exceed[k:]):
            persistent = o; break
    out["curves"][fname] = {"first_offset_gt_2sd": first, "first_offset_gt_2sd_persistent": persistent,
                            "b1_routine_window_ref": {"mean": ref_mean, "sd": ref_sd}, "curve": drift_curve}
    print(fname, "first offset >2 pooled SD:", first, "persistent:", persistent)
    for c in drift_curve:
        if c["offset"] % 4 == 0 or -8 <= c["offset"] <= 16:
            print(f"  off {c['offset']:+3d} n={c['n']:2d} drift={c['drift_mean']:8.3f} clean={c['clean_mean']:8.3f} "
                  f"pooledSD={c['pooled_sd']:.3f} z={(c['drift_mean']-c['clean_mean'])/c['pooled_sd']:.2f} pairedAUC={c['paired_auroc']:.3f}")
dump("task5_timing.json", out)
