"""REFUTER step F: how code-specific are the three legs of claim (a)?  Counts over all
59 drift traces, plus the literal/prose split and the sub-sampling sensitivity of the
w=8 '5/8' count.  Diagnostic only.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot/refute_depth_profile"
WIN, STRIDE = 48, 8
BAND = list(range(11, 15))
LITERAL = {"b1-f2-012", "b1-f2-014", "b1-f3-016", "b2-f2-011", "b2-f3-020"}


def main():
    z = np.load(OUT / "tokens.npz")
    meta = json.loads((OUT / "meta.json").read_text())
    tr = meta["traces"]
    off = np.cumsum([0] + [m["T"] for m in tr])
    rm, ent, top1 = z["rmass_fit"], z["ent"], z["top1"]

    NR, NE, NT = [], [], []
    for i, m in enumerate(tr):
        if not m["in_held"]:
            continue
        s, T = off[i], m["T"]
        for a in range(0, max(1, T - WIN + 1), STRIDE):
            b = min(a + WIN, T)
            if b - a < 8:
                continue
            NR.append(np.median(rm[s + a:s + b], axis=0)[BAND].mean())
            NE.append(np.median(ent[s + a:s + b], axis=0)[13])
            NT.append(np.median(top1[s + a:s + b], axis=0)[13])
    NR, NE, NT = np.array(NR), np.array(NE), np.array(NT)

    rows = []
    for i, m in enumerate(tr):
        if m["arm_class"] != "drift":
            continue
        s = off[i]
        a = max(0, min(int(m["product_onset"]), m["T"] - 1)); b = min(a + WIN, m["T"])
        r = float(np.median(rm[s + a:s + b], axis=0)[BAND].mean())
        e = float(np.median(ent[s + a:s + b], axis=0)[13])
        t = float(np.median(top1[s + a:s + b], axis=0)[13])
        rows.append({"short": m["short"], "dom": "programming" if m["is_code"] else m["domain"],
                     "code": m["is_code"], "rmass": r, "ent": e, "top1": t,
                     "ent_pct": float((NE < e).mean() * 100),
                     "top1_pct": float((NT < t).mean() * 100),
                     "rmass_pct": float((NR < r).mean() * 100)})
    code = [r for r in rows if r["code"]]
    oth = [r for r in rows if not r["code"]]
    print("## how code-specific is each leg of claim (a)?  (n=8 code, n=51 other drift)")
    print("code entropy(L13) percentiles:", sorted(round(r["ent_pct"]) for r in code))
    n = sum(1 for r in oth if r["ent_pct"] <= 9)
    print(f"  non-code drift traces with entropy percentile <= 9 (code's worst): {n}/51")
    print("  by domain:", {d: (sum(1 for r in oth if r['dom'] == d and r['ent_pct'] <= 9),
                               sum(1 for r in oth if r['dom'] == d))
                           for d in sorted({r['dom'] for r in oth})})
    print("code top-1(L13) percentiles:", sorted(round(r["top1_pct"]) for r in code))
    n = sum(1 for r in oth if r["top1_pct"] >= 52)
    print(f"  non-code drift traces with top-1 percentile >= 52 (code's worst): {n}/51")
    print("  by domain:", {d: (sum(1 for r in oth if r['dom'] == d and r['top1_pct'] >= 52),
                               sum(1 for r in oth if r['dom'] == d))
                           for d in sorted({r['dom'] for r in oth})})
    print("code rmass(L11-14) percentiles:", sorted(round(r["rmass_pct"]) for r in code))
    n = sum(1 for r in oth if r["rmass_pct"] >= 88)
    print(f"  non-code drift traces with rmass percentile >= 88 (code's worst): {n}/51 -> "
          + ", ".join(f"{r['short']}({r['dom']},{r['rmass_pct']:.0f})" for r in oth
                      if r["rmass_pct"] >= 88))
    n = sum(1 for r in oth if r["rmass"] >= min(x["rmass"] for x in code))
    print(f"  non-code drift traces with rmass >= the code minimum (0.236): {n}/51")
    # separation of rmass: code vs other drift vs routine
    def auc(pos, neg):
        a = np.concatenate([pos, neg]); o = np.argsort(a, kind="mergesort"); sa = a[o]
        r = np.empty(len(a)); i = 0
        while i < len(a):
            j = i
            while j + 1 < len(a) and sa[j + 1] == sa[i]:
                j += 1
            r[o[i:j + 1]] = (i + j) / 2.0; i = j + 1
        return (r[:len(pos)].sum() - len(pos) * (len(pos) - 1) / 2.0) / (len(pos) * len(neg))
    cr = np.array([r["rmass"] for r in code])
    print(f"\nAUC(code rmass vs 851 routine windows) = {auc(cr, NR):.3f}; "
          f"vs 51 other-drift = {auc(cr, np.array([r['rmass'] for r in oth])):.3f}")
    ce = np.array([r["ent"] for r in code])
    print(f"AUC(code low entropy vs routine windows) = {auc(-ce, -NE):.3f}; "
          f"vs other-drift = {auc(-ce, -np.array([r['ent'] for r in oth])):.3f}")
    print(f"AUC(code top1 vs routine) = {auc(np.array([r['top1'] for r in code]), NT):.3f}; "
          f"vs other-drift = {auc(np.array([r['top1'] for r in code]), np.array([r['top1'] for r in oth])):.3f}")

    print("\n## literal (5) vs prose-about-SQL (3), all three legs")
    for r in sorted(code, key=lambda x: x["short"]):
        print(f"  {r['short']:11s} {'literal' if r['short'] in LITERAL else 'prose  '} "
              f"rmass {r['rmass']:.3f} (p{r['rmass_pct']:.0f})  ent {r['ent']:.3f} "
              f"(p{r['ent_pct']:.0f})  top1 {r['top1']:.3f} (p{r['top1_pct']:.0f})")
    lit = [r for r in code if r["short"] in LITERAL]
    pro = [r for r in code if r["short"] not in LITERAL]
    print(f"  literal median rmass {np.median([r['rmass'] for r in lit]):.3f} vs "
          f"prose {np.median([r['rmass'] for r in pro]):.3f}")
    print(f"  literal median ent   {np.median([r['ent'] for r in lit]):.3f} vs "
          f"prose {np.median([r['ent'] for r in pro]):.3f}")


if __name__ == "__main__":
    main()
