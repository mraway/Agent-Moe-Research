#!/usr/bin/env python3
"""REFUTER analysis on the independently rebuilt mode-D z streams."""
from __future__ import annotations

import json
import math
import statistics as st
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
OUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
C = json.loads((OUT / "refute_pertoken_cache.json").read_text(encoding="utf-8"))
META, LAB, Z = C["meta"], C["labels"], C["z"]

PROG = sorted(
    [t for t, r in LAB.items() if r["domain"] == "programming"],
    key=lambda t: LAB[t]["product_onset"],
)
DRIFTS = list(LAB.keys())
NONPROG = [t for t in DRIFTS if LAB[t]["domain"] != "programming"]
ROUTINE = [t for t, m in META.items() if (not m["positive"]) and m["arm"] in ("clean", "benign")]
def short(t):
    return "-".join(t.split("-")[:3])


SHORT2ID = None
CONTROLS_THEIRS = {  # the 8 matched controls named in the claim
    "b1-f2-012": "b1-f0-078", "b2-f2-011": "b1-f1-058", "b2-f2-015": "b1-f4-072",
    "b2-f3-020": "b1-f2-062", "b2-f2-014": "b2-f4-023", "b1-f2-014": "b2-f4-050",
    "b1-f3-016": "b1-f1-032", "b2-f2-012": "b2-f3-043",
}
WIN = 48
SHORT2ID = {short(t): t for t in LAB}
CONTROLS_THEIRS = {SHORT2ID[k]: SHORT2ID[v] for k, v in CONTROLS_THEIRS.items()}


def rstream(cand, w, tid):
    b = Z[f"{cand}|{w}"][tid]
    z = np.asarray(b["z"]); ends = np.asarray(b["ends"])
    return z / b["thr_max"], ends


def window_R(cand, w, tid, anchor="product_onset"):
    r, ends = rstream(cand, w, tid)
    o = LAB[tid][anchor]
    hi = min(o + WIN, META[tid]["T"])
    m = (ends >= o) & (ends < hi)
    return r[m], ends[m]


def runs(idx):
    if len(idx) == 0:
        return []
    out, cur = [], 1
    for a, b in zip(idx[:-1], idx[1:]):
        if b == a + 1:
            cur += 1
        else:
            out.append(cur); cur = 1
    out.append(cur)
    return out


def routine_pool(cand, w):
    return np.concatenate([rstream(cand, w, t)[0] for t in ROUTINE])


def routine_pool_bucketed(cand, w):
    rs, bs = [], []
    for t in ROUTINE:
        r, e = rstream(cand, w, t)
        rs.append(r); bs.append(np.minimum(e // 32, 5))
    return np.concatenate(rs), np.concatenate(bs)


def pct(pool_sorted, v):
    return 100.0 * np.searchsorted(pool_sorted, v, side="left") / len(pool_sorted)


def auc(a, pool_sorted):
    """P(random code token > random routine token) + 0.5 ties, via ranks."""
    lo = np.searchsorted(pool_sorted, a, side="left")
    hi = np.searchsorted(pool_sorted, a, side="right")
    return float(np.mean((lo + hi) / 2.0)) / len(pool_sorted)


def sign_test(vals, ref=0.5):
    n = sum(1 for v in vals if v != ref)
    k = sum(1 for v in vals if v > ref)
    p = sum(math.comb(n, i) for i in range(k, n + 1)) / 2 ** n
    return k, n, min(1.0, 2 * p)


rep = {}
print("=" * 78)
print("BLOCK 1 -- CAND-A w1 exceedance in the code window (my recomputation)")
for cand, w in [("CAND-A", 1), ("CAND-A", 8), ("CAND-B", 1), ("CAND-B", 4)]:
    pool = np.sort(routine_pool(cand, w))
    tot_p = hit_p = tot_c = hit_c = 0
    rows = []
    for t in PROG:
        r, e = window_R(cand, w, t)
        c = CONTROLS_THEIRS[t]
        rc, ec = window_R(cand, w, c)
        exc = np.flatnonzero(r >= 1.0)
        excc = np.flatnonzero(rc >= 1.0)
        rt, _ = rstream(cand, w, t)
        tot_p += len(r); hit_p += len(exc); tot_c += len(rc); hit_c += len(excc)
        rows.append(
            dict(tid=t, ons=LAB[t]["product_onset"], n=len(r), nex=int(len(exc)),
                 maxR=float(r.max()), medR=float(np.median(r)),
                 pctile=float(pct(pool, np.median(r))),
                 maxrun=max(runs(exc), default=0),
                 whole=int((rt >= 1.0).sum()),
                 auc=auc(r, pool),
                 ctrl=c, cn=len(rc), cnex=int(len(excc)), cmaxR=float(rc.max()),
                 cmedR=float(np.median(rc)), cpct=float(pct(pool, np.median(rc))),
                 cmaxrun=max(runs(excc), default=0),
                 cwhole=int((rstream(cand, w, c)[0] >= 1.0).sum()), cauc=auc(rc, pool))
        )
    rt_rate = float((pool >= 1.0).mean())
    print(f"\n--- {cand} w={w}   routine per-token exceedance {rt_rate:.4f} "
          f"({int((pool>=1.0).sum())}/{len(pool)})")
    print(f"{'code':<10}{'ons':>4}{'n':>4}{'#ex':>4}{'maxR':>7}{'medR':>8}{'pct':>6}"
          f"{'run':>4}{'whole':>6}{'AUC':>7} | {'ctrl':<10}{'#ex':>4}{'maxR':>7}{'medR':>8}{'pct':>6}{'whole':>6}{'AUC':>7}")
    for r in rows:
        print(f"{short(r['tid']):<10}{r['ons']:>4}{r['n']:>4}{r['nex']:>4}{r['maxR']:>7.2f}"
              f"{r['medR']:>+8.3f}{r['pctile']:>6.1f}{r['maxrun']:>4}{r['whole']:>6}{r['auc']:>7.3f}"
              f" | {short(r['ctrl']):<10}{r['cnex']:>4}{r['cmaxR']:>7.2f}{r['cmedR']:>+8.3f}{r['cpct']:>6.1f}{r['cwhole']:>6}{r['cauc']:>7.3f}")
    print(f"pooled code {hit_p}/{tot_p} = {hit_p/tot_p:.4f} | controls {hit_c}/{tot_c} = {hit_c/tot_c:.4f}")
    k, n, p = sign_test([r["auc"] for r in rows])
    print(f"sign test on per-trace AUC vs 0.5: {k}/{n} above, two-sided p = {p:.4f}; "
          f"AUCs {[round(r['auc'],3) for r in rows]}")
    rep[f"{cand}|w{w}"] = dict(rows=rows, pooled_code=[hit_p, tot_p],
                               pooled_ctrl=[hit_c, tot_c], routine_rate=rt_rate,
                               sign=[k, n, p])

print("\n" + "=" * 78)
print("BLOCK 2 -- whole-trace CAND-A w1 crossings of the 8 code traces (claim: 7/8 have none)")
for w in (1, 2, 4, 8):
    tot = []
    for t in PROG:
        r, e = rstream("CAND-A", w, t)
        idx = np.flatnonzero(r >= 1.0)
        tot.append((t, int(len(idx)), [int(e[i] - LAB[t]["product_onset"]) for i in idx]))
    zero = sum(1 for _, n, _ in tot if n == 0)
    print(f"w={w}: traces with 0 whole-trace crossings = {zero}/8 ; "
          + " ".join(f"{short(t)}:{n}{o if n else ''}" for t, n, o in tot))
    rep[f"whole_A_w{w}"] = [(t, n, o) for t, n, o in tot]

print("\n" + "=" * 78)
print("BLOCK 3 -- is the code window shifted relative to routine at all? (AUC / tail)")
for cand, ws in [("CAND-A", [1, 2, 4, 8]), ("CAND-B", [1, 2, 4])]:
    for w in ws:
        pool = np.sort(routine_pool(cand, w))
        pool_b, buck = routine_pool_bucketed(cand, w)
        order = {b: np.sort(pool_b[buck == b]) for b in range(6)}
        aucs, aucs_b, tails = [], [], []
        allr = []
        for t in PROG:
            r, e = window_R(cand, w, t)
            allr.append(r)
            aucs.append(auc(r, pool))
            bb = np.minimum(e // 32, 5)
            aucs_b.append(float(np.mean([
                auc(r[bb == b], order[b]) for b in sorted(set(bb.tolist())) if (bb == b).sum()
            ])))
            q95 = np.quantile(pool, 0.95)
            tails.append(float((r >= q95).mean()))
        allr = np.concatenate(allr)
        k, n, p = sign_test(aucs)
        kb, nb, pb = sign_test(aucs_b)
        q95 = np.quantile(pool, 0.95); q99 = np.quantile(pool, 0.99)
        print(f"{cand} w={w}: pooled code AUC {auc(allr, pool):.3f} | per-trace "
              f"{[round(v,3) for v in aucs]} sign {k}/{n} p={p:.4f} | bucket-matched AUC "
              f"{[round(v,3) for v in aucs_b]} sign {kb}/{nb} p={pb:.4f} | "
              f"frac>routineQ95 {float((allr>=q95).mean()):.3f} (exp 0.05) "
              f"frac>routineQ99 {float((allr>=q99).mean()):.3f} (exp 0.01)")
        rep[f"auc_{cand}_w{w}"] = dict(pooled=auc(allr, pool), per_trace=aucs,
                                       sign=[k, n, p], bucket=aucs_b, bucket_sign=[kb, nb, pb],
                                       tail95=float((allr >= q95).mean()),
                                       tail99=float((allr >= q99).mean()))

print("\n" + "=" * 78)
print("BLOCK 4 -- does the same statistic separate OTHER drifts? per-domain medR / AUC (CAND-A)")
for w in (1, 8):
    pool = np.sort(routine_pool("CAND-A", w))
    bydom = defaultdict(list)
    for t in DRIFTS:
        r, _ = window_R("CAND-A", w, t)
        if not len(r):
            continue
        bydom[LAB[t]["domain"]].append((t, float(np.median(r)), float(r.max()),
                                        float((r >= 1).mean()), auc(r, pool)))
    print(f"\n-- w={w}")
    print(f"{'domain':<20}{'n':>3}{'medMedR':>9}{'medMaxR':>9}{'medFrac':>9}{'medAUC':>8}{'AUC range':>18}")
    for d, v in sorted(bydom.items()):
        print(f"{d:<20}{len(v):>3}{st.median(x[1] for x in v):>+9.3f}"
              f"{st.median(x[2] for x in v):>9.3f}{st.median(x[3] for x in v):>9.3f}"
              f"{st.median(x[4] for x in v):>8.3f}"
              f"{min(x[4] for x in v):>9.3f}-{max(x[4] for x in v):.3f}")
    npv = [x for d, v in bydom.items() if d != "programming" for x in v]
    pv = bydom["programming"]
    print(f"{'ALL non-programming':<20}{len(npv):>3}{st.median(x[1] for x in npv):>+9.3f}"
          f"{st.median(x[2] for x in npv):>9.3f}{st.median(x[3] for x in npv):>9.3f}"
          f"{st.median(x[4] for x in npv):>8.3f}")
    below = [x for x in npv if x[1] <= max(y[1] for y in pv)]
    print(f"non-programming drifts whose medR <= the LARGEST programming medR "
          f"({max(y[1] for y in pv):+.3f}): {len(below)}/{len(npv)} -> "
          + ", ".join(f"{short(x[0])}({LAB[x[0]]['domain'][:4]},{x[1]:+.3f})" for x in sorted(below, key=lambda x: x[1])))
    belowmax = [x for x in npv if x[2] <= max(y[2] for y in pv)]
    print(f"non-programming drifts whose maxR <= the LARGEST programming maxR "
          f"({max(y[2] for y in pv):.3f}): {len(belowmax)}/{len(npv)}")
    nocross = [x for x in npv if x[3] == 0.0]
    print(f"non-programming drifts with ZERO in-window crossings: {len(nocross)}/{len(npv)} -> "
          + ", ".join(f"{short(x[0])}({LAB[x[0]]['domain'][:4]})" for x in nocross))
    rep[f"domain_w{w}"] = {d: [(x[0], x[1], x[2], x[3], x[4]) for x in v] for d, v in bydom.items()}

(OUT / "refute_pertoken_analysis.json").write_text(json.dumps(rep, default=float))
print("\nwrote", OUT / "refute_pertoken_analysis.json")
