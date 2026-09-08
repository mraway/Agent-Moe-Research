"""REFUTER step D: cross-batch diagnostics for the rmass (R) statistic with the
protocol's position-bucket standardization, plus the batch-level shift that explains
the direction asymmetry.  Diagnostic only.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot/refute_depth_profile"
W = 8
BUCKET = 32
SETS = {"5-15": list(range(5, 16)), "12-15": [12, 13, 14, 15], "11-14": [11, 12, 13, 14]}
PROG = {"b1-f2-012", "b1-f2-014", "b1-f3-016", "b2-f2-011",
        "b2-f2-012", "b2-f2-014", "b2-f2-015", "b2-f3-020"}


def auc(pos, neg):
    a = np.concatenate([pos, neg]); o = np.argsort(a, kind="mergesort"); sa = a[o]
    r = np.empty(len(a)); i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sa[j + 1] == sa[i]:
            j += 1
        r[o[i:j + 1]] = (i + j) / 2.0; i = j + 1
    return (r[:len(pos)].sum() - len(pos) * (len(pos) - 1) / 2.0) / (len(pos) * len(neg))


def main():
    z = np.load(OUT / "tokens.npz")
    meta = json.loads((OUT / "meta.json").read_text())
    tr = meta["traces"]
    off = np.cumsum([0] + [m["T"] for m in tr])
    res = {}

    def rm_stream(i, key, ls):
        """w=8 rolling-mean of rmass over the layer set, window ends W-1..T-1."""
        a = z[key][off[i]:off[i + 1]][:, ls].mean(1).astype(np.float64)
        c = np.concatenate([[0.0], np.cumsum(a)])
        s = (c[W:] - c[:-W]) / W
        return s, np.arange(W - 1, len(a))

    print("## batch-level shift in the rmass statistic (routine clean+benign, w=8, L11-14)")
    for b in ("b1", "b2"):
        for key in ("rmass_b1", "rmass_b2"):
            v = np.concatenate([rm_stream(i, key, SETS["11-14"])[0]
                                for i, m in enumerate(tr)
                                if m["batch"] == b and m["arm_class"] in ("clean", "benign")])
            print(f"  routine {b} scored with {key:9s}: median {np.median(v):.4f} "
                  f"q90 {np.quantile(v,.9):.4f} q99 {np.quantile(v,.99):.4f}")
    res["level"] = {}

    print("\n## R (sign-inverted rmass) cross-batch WITH position-bucket standardization "
          "(buckets = end//32, mu/sigma from the source batch's routine, protocol rule)")
    print("| src->tgt | layers | standardization | code ratio | AUC vs routine | AUC vs resist | "
          "code clean | thr(5/8-equiv) | FAR all | FAR clean | FAR benign | FAR resist |")
    print("|" + "---|" * 12)
    table = {}
    for src, tgt in (("b1", "b2"), ("b2", "b1")):
        key = f"rmass_{src}"
        srt = [i for i, m in enumerate(tr) if m["batch"] == src and m["arm_class"] in ("clean", "benign")]
        for name in ("5-15", "12-15", "11-14"):
            ls = SETS[name]
            # source-fit bucket stats
            byb = defaultdict(list)
            for i in srt:
                s, e = rm_stream(i, key, ls)
                for v, b in zip(s, e // BUCKET):
                    byb[int(b)].append(v)
            cap = max(byb)
            while cap > 0 and len(byb[cap]) < 200:
                byb[cap - 1].extend(byb.pop(cap)); cap -= 1
            mu = {b: float(np.mean(v)) for b, v in byb.items()}
            sg = {b: float(np.std(v)) + 1e-9 for b, v in byb.items()}

            def tmax(i, standardize, drift):
                s, e = rm_stream(i, key, ls)
                if drift:
                    on = int(tr[i]["product_onset"])
                    sel = (e >= on) & (e < min(on + 48, tr[i]["T"]))
                    if not sel.any():
                        sel = np.zeros_like(sel); sel[0] = True
                    s, e = s[sel], e[sel]
                if standardize:
                    bk = np.clip(e // BUCKET, 0, cap)
                    s = (s - np.array([mu[int(b)] for b in bk])) / np.array([sg[int(b)] for b in bk])
                return float(s.max())

            tg = [i for i, m in enumerate(tr) if m["batch"] == tgt]
            rt_i = [i for i in tg if tr[i]["arm_class"] in ("clean", "benign")]
            cl_i = [i for i in tg if tr[i]["arm_class"] == "clean"]
            be_i = [i for i in tg if tr[i]["arm_class"] == "benign"]
            rs_i = [i for i in tg if tr[i]["arm_class"] == "resist"]
            cd_i = [i for i in tg if tr[i]["arm_class"] == "drift" and tr[i]["is_code"]]
            od_i = [i for i in tg if tr[i]["arm_class"] == "drift" and not tr[i]["is_code"]]
            for std in (False, True):
                rt = np.array([tmax(i, std, False) for i in rt_i])
                cl = np.array([tmax(i, std, False) for i in cl_i])
                be = np.array([tmax(i, std, False) for i in be_i])
                rs = np.array([tmax(i, std, False) for i in rs_i])
                cd = np.array([tmax(i, std, True) for i in cd_i])
                od = np.array([tmax(i, std, True) for i in od_i])
                k = max(1, int(round(len(cd) * 5.0 / 8.0)))
                thr = np.sort(cd)[::-1][k - 1]
                row = {"ratio": float(np.median(cd) / np.median(rt)),
                       "auc": float(auc(cd, rt)), "auc_resist": float(auc(cd, rs)),
                       "clean": sum(1 for v in cd if (rt > v).mean() <= 0.05),
                       "n_code": len(cd), "thr": float(thr),
                       "far_all": float((rt >= thr).mean()), "far_clean": float((cl >= thr).mean()),
                       "far_benign": float((be >= thr).mean()), "far_resist": float((rs >= thr).mean()),
                       "other_above": float((od >= thr).mean()),
                       "code_scores": {tr[i]["short"]: float(v) for i, v in zip(cd_i, cd)}}
                table[f"{src}->{tgt}|{name}|{'bucket-z' if std else 'raw'}"] = row
                print(f"| {src}->{tgt} | {name} | {'bucket-z' if std else 'raw'} | {row['ratio']:.2f} | "
                      f"{row['auc']:.3f} | {row['auc_resist']:.3f} | {row['clean']}/{row['n_code']} | "
                      f"{row['thr']:.3f} | {row['far_all']:.3f} | {row['far_clean']:.3f} | "
                      f"{row['far_benign']:.3f} | {row['far_resist']:.3f} |")
    res["R_cross"] = table

    # pooled: is there ONE threshold that works in both directions?
    print("\n## the same rmass threshold in both directions (L11-14, raw, source-fit mask)")
    for name in ("11-14",):
        for src, tgt in (("b1", "b2"), ("b2", "b1")):
            t = table[f"{src}->{tgt}|{name}|raw"]
            print(f"  {src}->{tgt}: code scores " +
                  ", ".join(f"{k}={v:.3f}" for k, v in sorted(t['code_scores'].items())) +
                  f" | thr {t['thr']:.3f} | FAR all {t['far_all']:.3f} resist {t['far_resist']:.3f}")
    (OUT / "cross2.json").write_text(json.dumps(res), encoding="utf-8")


if __name__ == "__main__":
    main()
