"""Why the depth bump at 12-15 does not survive whitening.

Per layer, take the window-mean router probability vectors (48-token windows) of the
held-out routine half as the reference cloud, and decompose the group mean shift in its
PCA basis: raw L2 norm, whitened (Mahalanobis) norm, and how much of the shift energy
sits in the routine's own high-variance directions.  Also scores every window with the
whitened distance to the routine centre, one layer at a time.  Diagnostic only.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
CACHE = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot/depth_profile"
L = 16
DOMAINS = ["cooking", "fiction", "general_knowledge", "legal_analysis",
           "mathematics", "poetry", "travel_planning"]
FLOOR = 1e-3   # eigenvalue floor as a fraction of the layer's mean eigenvalue


def auc(pos, neg):
    a = np.concatenate([pos, neg]); order = np.argsort(a, kind="mergesort"); sa = a[order]
    ranks = np.empty(len(a)); i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sa[j + 1] == sa[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0; i = j + 1
    return (ranks[:len(pos)].sum() - len(pos) * (len(pos) - 1) / 2.0) / (len(pos) * len(neg))


def main():
    z = np.load(CACHE / "window_probs.npz")
    meta = json.loads((CACHE / "depth_profile.json").read_text())["window_meta"]
    groups = ["code"] + [f"other:{x}" for x in DOMAINS]
    R = z["routine"]                       # held-out routine windows [851,16,64]
    F = z["routine_fit"]                   # fit-half routine windows (reference cloud)
    print(f"fit routine windows {F.shape[0]}, held-out routine windows {R.shape[0]}, "
          f"code windows {z['code'].shape[0]}")
    print()

    print("## W1 mean-shift geometry per layer (window-mean router probabilities)")
    print("raw = ||mean_group - mean_routine||2 ; wht = Mahalanobis norm of the same shift in "
          "the routine window covariance ; top5 = share of the shift's squared length lying in "
          "the routine cloud's 5 leading PCA directions.")
    print()
    print("| layer | raw code | wht code | top5 code | raw other | wht other | top5 other | "
          "raw/wht code | raw/wht other | routine leading eigval share (top5) |")
    print("|" + "---|" * 10)
    per_layer = {}
    for l in range(L):
        X = F[:, l, :].astype(np.float64)
        mu = X.mean(0)
        C = np.cov(X - mu, rowvar=False)
        w, V = np.linalg.eigh(C)
        w = np.clip(w, FLOOR * w.mean(), None)
        other = np.concatenate([z[g][:, l, :] for g in groups[1:]], axis=0).astype(np.float64)
        rows = {}
        for name, arr in (("code", z["code"][:, l, :].astype(np.float64)), ("other", other)):
            d = arr.mean(0) - mu
            proj = V.T @ d
            raw = float(np.linalg.norm(d))
            wht = float(np.sqrt((proj ** 2 / w).sum()))
            idx = np.argsort(-w)[:5]
            top5 = float((proj[idx] ** 2).sum() / (proj ** 2).sum())
            rows[name] = (raw, wht, top5)
        eig_share = float(np.sort(w)[::-1][:5].sum() / w.sum())
        per_layer[l] = (rows, mu, w, V)
        print(f"| {l} | {rows['code'][0]:.4f} | {rows['code'][1]:.2f} | {rows['code'][2]:.2f} | "
              f"{rows['other'][0]:.4f} | {rows['other'][1]:.2f} | {rows['other'][2]:.2f} | "
              f"{rows['code'][0]/rows['code'][1]:.4f} | {rows['other'][0]/rows['other'][1]:.4f} | "
              f"{eig_share:.2f} |")
    print()

    print("## W2 per-layer whitened distance to the routine centre, CAND-A style "
          "(diagonal whitening, sd + variance floor 1e-3), reference = the fit routine half, "
          "scored on the held-out routine half")
    print("| layer | routine median | code median / ratio / AUC | other median / ratio / AUC |"
          " code windows above routine q90 |")
    print("|---|---|---|---|---|")
    dist = {}
    for l in range(L):
        rows, mu, w, V = per_layer[l]
        Xf = F[:, l, :].astype(np.float64)
        muf = Xf.mean(0)
        sdf = Xf.std(0) + 1e-3
        def dfun(arr):
            return np.linalg.norm((arr.astype(np.float64) - muf) / sdf, axis=1)
        dr = dfun(R[:, l, :])
        dc = dfun(z["code"][:, l, :])
        do = dfun(np.concatenate([z[g][:, l, :] for g in groups[1:]], axis=0))
        dist[l] = (dr, dc, do)
        q90 = np.quantile(dr, 0.90)
        print(f"| {l} | {np.median(dr):.2f} | {np.median(dc):.2f} / "
              f"{np.median(dc)/np.median(dr):.2f} / {auc(dc, dr):.3f} | "
              f"{np.median(do):.2f} / {np.median(do)/np.median(dr):.2f} / {auc(do, dr):.3f} | "
              f"{int((dc > q90).sum())}/8 |")
    print()

    print("## W3 per-code-trace whitened distance profile (value and percentile inside the 851 held-out routine windows)")
    ids = [m["trace_id"] for m in meta["code"]]
    print("| trace | " + " | ".join(f"L{l}" for l in range(L)) + " |")
    print("|" + "---|" * (L + 1))
    for i, tid in enumerate(ids):
        cells = []
        for l in range(L):
            dr, dc, _ = dist[l]
            cells.append(f"{dc[i]:.1f}({(dr < dc[i]).mean()*100:.0f})")
        print(f"| {'-'.join(tid.split('-')[:3])} | " + " | ".join(cells) + " |")
    print()

    print("## W4 JS vs whitened distance, side by side (median ratio to routine)")
    dprof = json.loads((CACHE / "depth_profile.json").read_text())
    wm = {g: np.array(v["js"]) for g, v in dprof["window_medians"].items() if "js" in v}
    print("| layer | code JS ratio | code whitened ratio | other JS ratio | other whitened ratio |")
    print("|---|---|---|---|---|")
    other_js = np.concatenate([wm[f"other:{x}"] for x in DOMAINS], axis=0)
    for l in range(L):
        dr, dc, do = dist[l]
        rn = np.median(wm["routine"][:, l])
        print(f"| {l} | {np.median(wm['code'][:, l])/rn:.2f} | {np.median(dc)/np.median(dr):.2f} "
              f"| {np.median(other_js[:, l])/rn:.2f} | {np.median(do)/np.median(dr):.2f} |")
    print()


if __name__ == "__main__":
    main()
