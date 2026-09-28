#!/usr/bin/env python3
"""Statistics lens for the narrow-window round: exact intervals, paired flips,
sensitivity, and a paired bootstrap.  Read-only."""
from __future__ import annotations

import json, math, random, statistics, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_statistics_recompute import (RUNS, READINGS, main as build_vectors,
                                        cp, mcnemar_exact, read_jsonl, hit, LAB, NW)

FROZEN_W = {"CAND-A": 8, "CAND-B": 4}


def counts(d, h=None):
    return sum(1 for hh, l in d.values() if hh and (h is None or l <= h))


def flips(nar, fro, h=None):
    """(b = frozen hit -> narrow miss, c = frozen miss -> narrow hit, ids)."""
    b, c, bl, cl = 0, 0, [], []
    for t in fro:
        fh = fro[t][0] and (h is None or fro[t][1] <= h)
        nh = nar[t][0] and (h is None or nar[t][1] <= h)
        if fh and not nh:
            b += 1; bl.append(t)
        elif nh and not fh:
            c += 1; cl.append(t)
    return b, c, bl, cl


def fmt(x):
    return "n/a" if x is None else f"{x:.3f}"


def main():
    vec, anchored, prod, leak, windows, cov = build_vectors()
    leak_id = leak[0]
    out = {}

    lines = []
    P = lines.append

    # ---------- (1) Clopper-Pearson ----------
    P("## (1) Exact (Clopper-Pearson) 95% intervals, anchored resist, n=14, mode D, alpha 0.10\n")
    P("| cand | w | reading | R+4 | R+8 | R+16 | R_final |")
    P("|---|---|---|---|---|---|---|")
    cp_rows = []
    for cand in ("CAND-A", "CAND-B"):
        for w in windows[cand]:
            for r in READINGS:
                d = vec[(cand, w, 0.1, r, 0, "resist")]
                cells = []
                for h in (4, 8, 16, None):
                    k = counts(d, h)
                    lo, hi = cp(k, 14)
                    cells.append(f"{k}/14 = {k/14:.3f} [{lo:.3f}, {hi:.3f}]")
                    cp_rows.append(dict(cand=cand, w=w, reading=r,
                                        horizon=("final" if h is None else h),
                                        k=k, n=14, rate=k/14, lo=lo, hi=hi))
                tag = " (frozen)" if w == FROZEN_W[cand] else ""
                P(f"| {cand}{tag} | {w} | {r} | " + " | ".join(cells) + " |")
    out["cp_resist"] = cp_rows

    P("")
    P("### Same, alpha 0.05 (secondary)\n")
    P("| cand | w | reading | R+8 | R+16 | R_final |")
    P("|---|---|---|---|---|---|")
    for cand in ("CAND-A", "CAND-B"):
        for w in windows[cand]:
            for r in READINGS:
                d = vec[(cand, w, 0.05, r, 0, "resist")]
                cells = []
                for h in (8, 16, None):
                    k = counts(d, h); lo, hi = cp(k, 14)
                    cells.append(f"{k}/14 [{lo:.3f}, {hi:.3f}]")
                P(f"| {cand} | {w} | {r} | " + " | ".join(cells) + " |")

    # ---------- prereg bar power ----------
    P("")
    P("### What the 5/14 prereg bar can and cannot separate\n")
    for k in (2, 3, 4, 5, 6):
        lo, hi = cp(k, 14)
        P(f"- {k}/14 = {k/14:.3f}, exact 95% CI [{lo:.3f}, {hi:.3f}]")
    # one-sided binomial: P(X >= 5 | p = 2/14) and P(X >= 5 | p = 4/14)
    def pge(k, n, p):
        return sum(math.comb(n, i) * p**i * (1-p)**(n-i) for i in range(k, n+1))
    P(f"- P(X >= 5 | n=14, p = 2/14 frozen rate) = {pge(5,14,2/14):.4f}")
    P(f"- P(X >= 5 | n=14, p = 0.30) = {pge(5,14,0.30):.4f}   (power of the bar against a true 30% rate)")
    P(f"- P(X >= 5 | n=14, p = 0.40) = {pge(5,14,0.40):.4f}")
    P(f"- P(X >= 5 | n=14, p = 0.50) = {pge(5,14,0.50):.4f}")
    P(f"- P(X >= 6 in at least one of 308 independent cells | p = 2/14) approx "
      f"{1-(1-pge(6,14,2/14))**308:.3f}  (upper-bound multiplicity check, cells are NOT independent)")

    # ---------- (2) paired vs frozen, resist ----------
    P("")
    P("## (2) Paired comparison narrow vs frozen window, same 14 traces (mode D, alpha 0.10)\n")
    P("b = hit at frozen w, miss at narrow w; c = miss at frozen, hit at narrow; p = exact McNemar (2-sided).\n")
    P("| cand | narrow w | reading | horizon | frozen k | narrow k | b | c | p_exact | traces flipping |")
    P("|---|---|---|---|---|---|---|---|---|---|")
    paired = []
    for cand in ("CAND-A", "CAND-B"):
        fw = FROZEN_W[cand]
        for w in windows[cand]:
            if w == fw:
                continue
            for r in READINGS:
                fro = vec[(cand, fw, 0.1, r, 0, "resist")]
                nar = vec[(cand, w, 0.1, r, 0, "resist")]
                for h in (8, 16, None):
                    b, c, bl, cl = flips(nar, fro, h)
                    p = mcnemar_exact(b, c)
                    hn = "final" if h is None else f"+{h}"
                    ids = "; ".join([f"-{t.split('--')[0]}" for t in bl] +
                                    [f"+{t.split('--')[0]}" for t in cl]) or "none"
                    P(f"| {cand} | {w} | {r} | {hn} | {counts(fro,h)} | {counts(nar,h)} | {b} | {c} | {p:.3f} | {ids} |")
                    paired.append(dict(cand=cand, narrow_w=w, reading=r,
                                       horizon=("final" if h is None else h),
                                       frozen_k=counts(fro,h), narrow_k=counts(nar,h),
                                       b=b, c=c, p_exact=p, lost=bl, gained=cl))
    out["paired_resist"] = paired

    # ---------- (3) paired vs frozen, drift ----------
    P("")
    P("## (3) Paired comparison on the 59 drift traces (product_onset anchor)\n")
    P("| cand | narrow w | reading | horizon | frozen k | narrow k | b (lost) | c (gained) | p_exact |")
    P("|---|---|---|---|---|---|---|---|---|")
    paired_d = []
    for cand in ("CAND-A", "CAND-B"):
        fw = FROZEN_W[cand]
        for w in windows[cand]:
            if w == fw:
                continue
            for r in READINGS:
                fro = vec[(cand, fw, 0.1, r, 0, "drift")]
                nar = vec[(cand, w, 0.1, r, 0, "drift")]
                for h in (8, 16, None):
                    b, c, bl, cl = flips(nar, fro, h)
                    p = mcnemar_exact(b, c)
                    hn = "final" if h is None else f"+{h}"
                    P(f"| {cand} | {w} | {r} | {hn} | {counts(fro,h)} | {counts(nar,h)} | {b} | {c} | {p:.3f} |")
                    paired_d.append(dict(cand=cand, narrow_w=w, reading=r,
                                         horizon=("final" if h is None else h),
                                         frozen_k=counts(fro,h), narrow_k=counts(nar,h),
                                         b=b, c=c, p_exact=p))
    out["paired_drift"] = paired_d

    # ---------- (4) sensitivity ----------
    P("")
    P("## (4) Sensitivity: strict vs +-5 band, and dropping the topic_word_leak trace\n")
    diff_band = []
    for cand in ("CAND-A", "CAND-B"):
        for w in windows[cand]:
            for a in (0.05, 0.1):
                for r in READINGS:
                    s = vec[(cand, w, a, r, 0, "resist")]
                    t = vec[(cand, w, a, r, 5, "resist")]
                    for h in (4, 8, 16, None):
                        if counts(s, h) != counts(t, h):
                            diff_band.append((cand, w, a, r, h, counts(s,h), counts(t,h)))
    P(f"- Anchored-resist cells (2 cand x windows x 2 alpha x 2 readings x 4 horizons = "
      f"{sum(len(windows[c]) for c in windows)*2*2*4}) where band-5 differs from strict: "
      f"**{len(diff_band)}**. {diff_band if diff_band else 'Confirmed inert.'}")
    diff_band_d = []
    for cand in ("CAND-A", "CAND-B"):
        for w in windows[cand]:
            for r in READINGS:
                s = vec[(cand, w, 0.1, r, 0, "drift")]
                t = vec[(cand, w, 0.1, r, 5, "drift")]
                for h in (8, 16, None):
                    if counts(s, h) != counts(t, h):
                        diff_band_d.append((cand, w, r, h, counts(s,h), counts(t,h)))
    P(f"- Drift (product_onset) cells where band-5 differs from strict (alpha 0.10): {diff_band_d}")
    P("")
    P("| cand | w | reading | R+16 all 14 | R+16 without leak (n=13) | R_final all 14 | R_final n=13 |")
    P("|---|---|---|---|---|---|---|")
    for cand in ("CAND-A", "CAND-B"):
        for w in windows[cand]:
            for r in READINGS:
                d = vec[(cand, w, 0.1, r, 0, "resist")]
                d13 = {k: v for k, v in d.items() if k != leak_id}
                k16, k16b = counts(d, 16), counts(d13, 16)
                kf, kfb = counts(d), counts(d13)
                lo, hi = cp(k16b, 13)
                P(f"| {cand} | {w} | {r} | {k16}/14 | {k16b}/13 [{lo:.3f}, {hi:.3f}] | {kf}/14 | {kfb}/13 |")

    # ---------- traces that never alarm anywhere ----------
    never = []
    for t in anchored:
        anyhit = False
        for key, d in vec.items():
            if key[5] != "resist":
                continue
            if d[t][0]:
                anyhit = True
                break
        if not anyhit:
            never.append(t)
    P("")
    P(f"- Anchored-resist traces with NO eligible alarm in ANY mode-D primary cell "
      f"(both alphas, both readings, all windows, both bands): {len(never)}")
    for t in sorted(never):
        P(f"  - {t}")

    # ---------- (5) bootstrap ----------
    P("")
    P("## (5) Paired bootstrap over traces (1000 draws, seed 20260905)\n")
    P("Resample the 73 positives (14 resist + 59 drift) WITH replacement, stratified by pool, "
      "recompute pooled R+16 for narrow and frozen on the SAME resample, report the paired difference.\n")
    P("| cand | narrow w | reading | pooled R+16 frozen | narrow | obs diff | boot mean diff | 95% pct CI | P(diff>0) |")
    P("|---|---|---|---|---|---|---|---|---|")
    boot_rows = []
    resist_ids = sorted(anchored)
    drift_ids = sorted(prod)
    rng = random.Random(20260905)
    draws = [([rng.randrange(14) for _ in range(14)], [rng.randrange(59) for _ in range(59)])
             for _ in range(1000)]
    for cand in ("CAND-A", "CAND-B"):
        fw = FROZEN_W[cand]
        for w in windows[cand]:
            if w == fw:
                continue
            for r in READINGS:
                fr_r = vec[(cand, fw, 0.1, r, 0, "resist")]; fr_d = vec[(cand, fw, 0.1, r, 0, "drift")]
                na_r = vec[(cand, w, 0.1, r, 0, "resist")];  na_d = vec[(cand, w, 0.1, r, 0, "drift")]
                def h16(d, t): return 1 if (d[t][0] and d[t][1] <= 16) else 0
                fvec = [h16(fr_r, t) for t in resist_ids] + [h16(fr_d, t) for t in drift_ids]
                nvec = [h16(na_r, t) for t in resist_ids] + [h16(na_d, t) for t in drift_ids]
                obs = (sum(nvec) - sum(fvec)) / 73
                diffs = []
                for ri, di in draws:
                    idx = ri + [14 + j for j in di]
                    diffs.append(sum(nvec[i] - fvec[i] for i in idx) / 73)
                diffs.sort()
                lo, hi = diffs[24], diffs[974]
                pgt = sum(1 for x in diffs if x > 0) / 1000
                P(f"| {cand} | {w} | {r} | {sum(fvec)}/73 | {sum(nvec)}/73 | {obs:+.3f} | "
                  f"{statistics.mean(diffs):+.3f} | [{lo:+.3f}, {hi:+.3f}] | {pgt:.3f} |")
                boot_rows.append(dict(cand=cand, narrow_w=w, reading=r, frozen_k=sum(fvec),
                                      narrow_k=sum(nvec), obs_diff=obs, boot_lo=lo, boot_hi=hi,
                                      p_gt0=pgt))
    out["bootstrap"] = boot_rows

    # ---------- resist-only bootstrap ----------
    P("")
    P("### Resist-only (n=14) paired bootstrap on R+16, same draws\n")
    P("| cand | narrow w | reading | frozen | narrow | obs diff | 95% pct CI | P(diff>0) |")
    P("|---|---|---|---|---|---|---|---|")
    for cand in ("CAND-A", "CAND-B"):
        fw = FROZEN_W[cand]
        for w in windows[cand]:
            if w == fw: continue
            for r in READINGS:
                fr = vec[(cand, fw, 0.1, r, 0, "resist")]; na = vec[(cand, w, 0.1, r, 0, "resist")]
                fvec = [1 if (fr[t][0] and fr[t][1] <= 16) else 0 for t in resist_ids]
                nvec = [1 if (na[t][0] and na[t][1] <= 16) else 0 for t in resist_ids]
                obs = (sum(nvec) - sum(fvec)) / 14
                diffs = sorted(sum(nvec[i]-fvec[i] for i in ri)/14 for ri, _ in draws)
                P(f"| {cand} | {w} | {r} | {sum(fvec)}/14 | {sum(nvec)}/14 | {obs:+.3f} | "
                  f"[{diffs[24]:+.3f}, {diffs[974]:+.3f}] | {sum(1 for x in diffs if x>0)/1000:.3f} |")

    # ---------- H1 latency, paired ----------
    P("")
    P("## (6) H1 latency, paired on traces hit at BOTH windows (alpha 0.10)\n")
    P("| cand | narrow w | reading | n paired | median latency frozen | narrow | median paired delta | predicted (8-w or 4-w) | sign test p |")
    P("|---|---|---|---|---|---|---|---|---|")
    for cand in ("CAND-A", "CAND-B"):
        fw = FROZEN_W[cand]
        for w in windows[cand]:
            if w == fw: continue
            for r in READINGS:
                fr = vec[(cand, fw, 0.1, r, 0, "resist")]; na = vec[(cand, w, 0.1, r, 0, "resist")]
                pairs = [(fr[t][1], na[t][1]) for t in resist_ids if fr[t][0] and na[t][0]]
                if not pairs:
                    P(f"| {cand} | {w} | {r} | 0 | - | - | - | {fw-w} | - |")
                    continue
                deltas = [n - f for f, n in pairs]
                neg = sum(1 for d in deltas if d < 0); pos = sum(1 for d in deltas if d > 0)
                p = mcnemar_exact(neg, pos)
                P(f"| {cand} | {w} | {r} | {len(pairs)} | {statistics.median([f for f,_ in pairs]):.1f} | "
                  f"{statistics.median([n for _,n in pairs]):.1f} | {statistics.median(deltas):+.1f} | "
                  f"-{fw-w} | {p:.3f} |")

    txt = "\n".join(lines)
    Path("/tmp/claude-1000/-home-wzh-Agent-Moe-Research--claude-worktrees-algorithm-research-proposals-427363/"
         "7e87c1f8-78bb-4b9f-9189-12780e6e8833/scratchpad/stats_body.md").write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    main()
