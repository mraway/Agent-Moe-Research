"""REFUTER step C: independent recomputation of depth_profile claim (b) at w=8,
plus the cross-batch transfer test (attack 4) and the sign-inverted rmass score.

Features
  P    window-mean router probability   [layers x 64]
  S    window top-8 selection rate      [layers x 64]   (CAND-A's feature)
  R    sign-inverted routine-top-8 probability mass (score = mean rmass, higher = more
       anomalous), the statistic the critic's E2 / section 4 proposes.

Protocols
  within  : depth_profile's own -- fit half of the pooled routine (both batches),
            null = held-out half, trace-max, within-batch ranking.
  cross   : fit mu/sd (and the routine top-8 sets, and the position-bucket mu/sigma)
            on ONE batch's routine (clean+benign), score the OTHER batch.

Everything is a post-hoc diagnostic on B1/B2 development data; nothing here is a detector
result and no improvement is claimed.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch

from research_v2 import io as rio

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot/refute_depth_profile"
LABELS = REPO / "docs/research_v2/labels/product_onset_v1_adjudicated.jsonl"
W, FLOOR = 8, 1e-3
PROG = {"b1-f2-012", "b1-f2-014", "b1-f3-016", "b2-f2-011",
        "b2-f2-012", "b2-f2-014", "b2-f2-015", "b2-f3-020"}
LITERAL = {"b1-f2-012", "b1-f2-014", "b1-f3-016", "b2-f2-011", "b2-f3-020"}
SETS = {"5-15": list(range(5, 16)), "12-15": [12, 13, 14, 15],
        "L5": [5], "L13": [13], "11-14": [11, 12, 13, 14]}


def short(tid):
    return "-".join(tid.split("-")[:3])


def auc(pos, neg):
    a = np.concatenate([pos, neg]); order = np.argsort(a, kind="mergesort"); sa = a[order]
    r = np.empty(len(a)); i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sa[j + 1] == sa[i]:
            j += 1
        r[order[i:j + 1]] = (i + j) / 2.0; i = j + 1
    return (r[:len(pos)].sum() - len(pos) * (len(pos) - 1) / 2.0) / (len(pos) * len(neg))


def windows(x):
    c = torch.cat((torch.zeros(16, 1, x.shape[2]), x.cumsum(1)), dim=1)
    m = (c[:, W:, :] - c[:, :-W, :]) / float(W)
    return m.permute(1, 0, 2).numpy().astype(np.float32)   # [nwin,16,64]


def main():
    labels = {}
    for line in LABELS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            labels[r["trace_id"]] = r
    batches = rio.load_core()
    traces = [t for b in ("b1", "b2") for t in batches[b]]

    # depth_profile's fit/held split (reproduced)
    routine = sorted([t for t in traces if rio.arm_class(t) in ("clean", "benign")],
                     key=lambda t: t.trace_id)
    grp = defaultdict(list)
    for t in routine:
        grp[(t.batch, t.arm)].append(t)
    fit_ids, held_ids = set(), set()
    for k in sorted(grp):
        for i, t in enumerate(grp[k]):
            (fit_ids if i % 2 == 0 else held_ids).add(t.trace_id)

    P, S, ENDS, META = {}, {}, {}, {}
    top8_sel = {"b1": torch.zeros(16, 64, dtype=torch.float64),
                "b2": torch.zeros(16, 64, dtype=torch.float64),
                "fit": torch.zeros(16, 64, dtype=torch.float64)}
    for t in traces:
        if t.token_count < W:
            continue
        pr = t.probabilities().float()
        oh = torch.zeros(16, t.token_count, 64)
        oh.scatter_(2, t.top_k_ids, 1.0)
        tid = t.trace_id
        P[tid] = windows(pr); S[tid] = windows(oh)
        ENDS[tid] = np.arange(W - 1, t.token_count)
        ac = rio.arm_class(t)
        lab = labels.get(tid)
        META[tid] = {"short": short(tid), "batch": t.batch, "arm_class": ac,
                     "T": t.token_count, "domain": (lab or {}).get("domain"),
                     "onset": None if lab is None else int(lab["product_onset"]),
                     "is_code": short(tid) in PROG,
                     "in_fit": tid in fit_ids, "in_held": tid in held_ids}
        if ac in ("clean", "benign"):
            top8_sel[t.batch] += oh.sum(1).double()
            if tid in fit_ids:
                top8_sel["fit"] += oh.sum(1).double()
        t._probabilities = None
    masks = {}
    for k, v in top8_sel.items():
        m = torch.zeros(16, 64, dtype=torch.long)
        m.scatter_(1, v.argsort(dim=1, descending=True)[:, :8], 1)
        masks[k] = m.numpy().astype(np.float32)
    ids = list(META)

    def drift_win_mask(tid):
        m = META[tid]; e = ENDS[tid]
        sel = (e >= m["onset"]) & (e < min(m["onset"] + 48, m["T"]))
        if not sel.any():
            sel = np.zeros_like(sel); sel[0] = True
        return sel

    def build(fit_tids, ls, feat):
        A = np.concatenate([(P if feat == "P" else S)[t][::2][:, ls, :].reshape(-1, len(ls) * 64)
                            for t in fit_tids], axis=0).astype(np.float64)
        return A.mean(0), A.std(0) + FLOOR

    def score_stream(tid, ls, feat, mu, sd, sub=1):
        X = (P if feat == "P" else S)[tid][::sub][:, ls, :].reshape(-1, len(ls) * 64).astype(np.float64)
        return np.linalg.norm((X - mu) / sd, axis=1), ENDS[tid][::sub]

    def rmass_stream(tid, ls, mask, sub=1):
        X = P[tid][::sub][:, ls, :]
        return (X * mask[ls][None]).sum(2).mean(1).astype(np.float64), ENDS[tid][::sub]

    results = {}

    # ================== A. reproduce the within-batch w=8 table ==================
    fit_t = [t for t in ids if META[t]["in_fit"]]
    held_t = [t for t in ids if META[t]["in_held"]]
    res_t = [t for t in ids if META[t]["arm_class"] == "resist"]
    drift_t = [t for t in ids if META[t]["arm_class"] == "drift"]
    print("## A. reproduction of depth_profile section 6 (w=8, trace-max within-batch null)")
    print("| layer set | feat | code median ratio | code AUC | code clean | other ratio | other AUC | other clean |")
    print("|" + "---|" * 8)
    repro = {}
    for name in ("5-15", "12-15", "L5", "L13"):
        ls = SETS[name]
        for feat in ("P", "S"):
            mu, sd = build(fit_t, ls, feat)
            hmax = np.array([score_stream(t, ls, feat, mu, sd, sub=2)[0].max() for t in held_t])
            dv = {}
            for t in drift_t:
                s, _ = score_stream(t, ls, feat, mu, sd, sub=1)
                dv[t] = s[drift_win_mask(t)].max()
            code = np.array([dv[t] for t in drift_t if META[t]["is_code"]])
            oth = np.array([dv[t] for t in drift_t if not META[t]["is_code"]])
            med = np.median(hmax)
            cc = sum(1 for v in code if (hmax > v).mean() <= 0.05)
            oc = sum(1 for v in oth if (hmax > v).mean() <= 0.05)
            print(f"| {name} | {feat} | {np.median(code)/med:.2f} | {auc(code,hmax):.3f} | {cc}/8 | "
                  f"{np.median(oth)/med:.2f} | {auc(oth,hmax):.3f} | {oc}/51 |")
            repro[f"{name}|{feat}"] = {
                "ratio": float(np.median(code) / med), "auc": float(auc(code, hmax)),
                "clean": cc, "other_ratio": float(np.median(oth) / med),
                "other_auc": float(auc(oth, hmax)), "other_clean": oc,
                "per_code": {META[t]["short"]: [float(dv[t]), float((hmax > dv[t]).mean())]
                             for t in drift_t if META[t]["is_code"]}}
    results["within_repro"] = repro
    print("\n### which 5 of 8 are 'clean' at 5-15/P and 12-15/P (score / fraction of routine traces above)")
    for key in ("5-15|P", "5-15|S", "12-15|P"):
        print(key, {k: (round(v[0], 2), round(v[1], 3)) for k, v in repro[key]["per_code"].items()})

    # ================== B. cross-batch transfer ==================
    print("\n## B. cross-batch: fit on one batch's routine (clean+benign), score the other")
    cross = {}
    for src, tgt in (("b1", "b2"), ("b2", "b1")):
        srt = [t for t in ids if META[t]["batch"] == src and META[t]["arm_class"] in ("clean", "benign")]
        tgt_routine = [t for t in ids if META[t]["batch"] == tgt and META[t]["arm_class"] in ("clean", "benign")]
        tgt_clean = [t for t in tgt_routine if META[t]["arm_class"] == "clean"]
        tgt_benign = [t for t in tgt_routine if META[t]["arm_class"] == "benign"]
        tgt_resist = [t for t in ids if META[t]["batch"] == tgt and META[t]["arm_class"] == "resist"]
        tgt_drift = [t for t in ids if META[t]["batch"] == tgt and META[t]["arm_class"] == "drift"]
        code_t = [t for t in tgt_drift if META[t]["is_code"]]
        for name in ("5-15", "12-15", "11-14"):
            ls = SETS[name]
            for feat in ("P", "S", "R"):
                if feat == "R":
                    mk = masks[src]
                    strm = lambda t, sub=1: rmass_stream(t, ls, mk, sub)
                else:
                    mu, sd = build(srt, ls, feat)
                    strm = lambda t, sub=1, mu=mu, sd=sd, f=feat: score_stream(t, ls, f, mu, sd, sub)
                # raw trace-max
                def tmax(tl, drift=False):
                    o = []
                    for t in tl:
                        s, e = strm(t, 1 if drift else 2)
                        if drift:
                            s = s[drift_win_mask(t)]
                        o.append(float(s.max()))
                    return np.array(o)
                rt = tmax(tgt_routine)
                cl, be, rs = tmax(tgt_clean), tmax(tgt_benign), tmax(tgt_resist)
                cd = tmax(code_t, drift=True)
                od = tmax([t for t in tgt_drift if not META[t]["is_code"]], drift=True)
                n_clean = sum(1 for v in cd if (rt > v).mean() <= 0.05)
                # threshold that admits 5/8 code (or, if fewer than 5 code traces in this
                # batch, the threshold that admits ceil(5/8 * n) of them)
                k = max(1, int(round(len(cd) * 5.0 / 8.0)))
                thr = np.sort(cd)[::-1][k - 1] if len(cd) else np.nan
                far = {"all": float((rt >= thr).mean()), "clean": float((cl >= thr).mean()),
                       "benign": float((be >= thr).mean()), "resist": float((rs >= thr).mean()),
                       "other_drift_recall": float((od >= thr).mean())}
                cross[f"{src}->{tgt}|{name}|{feat}"] = {
                    "n_code": len(cd), "k_admit": k, "thr": float(thr),
                    "code_scores": {META[t]["short"]: float(v) for t, v in zip(code_t, cd)},
                    "routine_median": float(np.median(rt)),
                    "ratio": float(np.median(cd) / np.median(rt)),
                    "auc_vs_routine": float(auc(cd, rt)),
                    "auc_vs_resist": float(auc(cd, rs)) if len(rs) else None,
                    "clean_count": n_clean, "far": far,
                    "n_routine": len(rt), "n_resist": len(rs)}
        print(f"\n### {src} -> {tgt}   (target: {len(tgt_routine)} routine, {len(tgt_resist)} resist, "
              f"{len(code_t)} programming, {len(tgt_drift)-len(code_t)} other drift)")
        print("| layers | feat | code ratio | AUC vs routine | AUC vs resist | code clean (<=5%) | "
              "thr admitting 5/8-equiv | FAR all | FAR clean | FAR benign | FAR resist | other-drift above thr |")
        print("|" + "---|" * 12)
        for name in ("5-15", "12-15", "11-14"):
            for feat in ("P", "S", "R"):
                c = cross[f"{src}->{tgt}|{name}|{feat}"]
                f = c["far"]
                print(f"| {name} | {feat} | {c['ratio']:.2f} | {c['auc_vs_routine']:.3f} | "
                      f"{c['auc_vs_resist']:.3f} | {c['clean_count']}/{c['n_code']} | "
                      f"{c['thr']:.3f} | {f['all']:.3f} | {f['clean']:.3f} | {f['benign']:.3f} | "
                      f"{f['resist']:.3f} | {f['other_drift_recall']:.3f} |")
    results["cross"] = cross
    results["ref_top8_agreement"] = {
        "b1_vs_b2_layers_identical": int((masks["b1"] == masks["b2"]).all(1).sum()),
        "b1_vs_b2_mean_overlap": float((masks["b1"] * masks["b2"]).sum(1).mean()),
        "fit_vs_b1_layers_identical": int((masks["fit"] == masks["b1"]).all(1).sum()),
        "fit_vs_b2_layers_identical": int((masks["fit"] == masks["b2"]).all(1).sum()),
    }
    print("\nrouting reference stability: routine top-8 sets identical on "
          f"{results['ref_top8_agreement']['b1_vs_b2_layers_identical']}/16 layers between B1 and B2; "
          f"mean per-layer overlap {results['ref_top8_agreement']['b1_vs_b2_mean_overlap']:.2f}/8")
    (OUT / "w8_cross.json").write_text(json.dumps(results), encoding="utf-8")


if __name__ == "__main__":
    main()
