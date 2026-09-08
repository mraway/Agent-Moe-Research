#!/usr/bin/env python3
"""REFUTER: sustained-excursion check on the FROZEN readings (persist2) + token identity."""
from __future__ import annotations
import json, sys
from pathlib import Path
import numpy as np

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
sys.path.insert(0, str(ROOT / "src"))
from research_v2 import io as rio  # noqa: E402

OUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
C = json.loads((OUT / "refute_pertoken_cache.json").read_text(encoding="utf-8"))
META, LAB, Z = C["meta"], C["labels"], C["z"]
sh = lambda t: "-".join(t.split("-")[:3])
PROG = sorted([t for t, r in LAB.items() if r["domain"] == "programming"],
              key=lambda t: LAB[t]["product_onset"])
NONPROG = [t for t in LAB if LAB[t]["domain"] != "programming"]
WIN = 48


def persist2(z, ends):
    zz, ee = [], []
    for i in range(1, len(z)):
        if ends[i] == ends[i - 1] + 1:
            zz.append(min(z[i], z[i - 1])); ee.append(ends[i])
    return np.array(zz), np.array(ee, dtype=int)


def runs(idx):
    if len(idx) == 0: return []
    out, cur = [], 1
    for a, b in zip(idx[:-1], idx[1:]):
        if b == a + 1: cur += 1
        else: out.append(cur); cur = 1
    out.append(cur); return out


print("=" * 90)
print("BLOCK 5 -- FROZEN reading (persist2, mode D, alpha=0.10): sustained excursions on code traces")
for cand, w in [("CAND-A", 8), ("CAND-B", 4)]:
    print(f"\n--- {cand} w={w} (this is the FROZEN detector configuration)")
    print(f"{'trace':<11}{'ons':>4}{'p2 cross (all)':>15}{'first_end':>10}{'in-window':>10}{'maxrun_all':>11}{'maxrun_win':>11}{'maxRp2':>8}")
    tot = []
    for t in PROG:
        b = Z[f"{cand}|{w}"][t]
        z = np.asarray(b["z"]); e = np.asarray(b["ends"]); thr = b["thr_p2"]
        pz, pe = persist2(z, e)
        cr = pe[pz >= thr]
        o = LAB[t]["product_onset"]; hi = min(o + WIN, META[t]["T"])
        inw = cr[(cr >= o) & (cr < hi)]
        print(f"{sh(t):<11}{o:>4}{len(cr):>15}{(int(cr[0]) if len(cr) else -1):>10}{len(inw):>10}"
              f"{max(runs(cr), default=0):>11}{max(runs(inw), default=0):>11}{float(pz.max()/thr):>8.2f}")
        tot.append((sh(t), len(cr), len(inw)))
    print(f"code traces with >=1 persist2 crossing anywhere: {sum(1 for _,n,_ in tot if n)}/8 ; "
          f"inside onset..+48: {sum(1 for _,_,n in tot if n)}/8")
    # non-programming comparison
    npc = []
    for t in NONPROG:
        b = Z[f"{cand}|{w}"][t]
        z = np.asarray(b["z"]); e = np.asarray(b["ends"]); thr = b["thr_p2"]
        pz, pe = persist2(z, e); cr = pe[pz >= thr]
        o = LAB[t]["product_onset"]; hi = min(o + WIN, META[t]["T"])
        npc.append((len(cr) > 0, ((cr >= o) & (cr < hi)).sum() > 0))
    print(f"non-programming: >=1 anywhere {sum(a for a,_ in npc)}/{len(npc)} ; in-window {sum(b for _,b in npc)}/{len(npc)}")

print("\n" + "=" * 90)
print("BLOCK 6 -- token identity of every CAND-A w=1 whole-trace crossing on the 8 code traces")
batches = rio.load_core()
traces = {t.trace_id: t for v in batches.values() for t in v}
for t in PROG:
    b = Z["CAND-A|1"][t]
    z = np.asarray(b["z"]); e = np.asarray(b["ends"])
    idx = np.flatnonzero(z >= b["thr_max"])
    if not len(idx):
        continue
    pieces = rio.decode_token_texts(traces[t].token_ids.tolist())
    o = LAB[t]["product_onset"]
    for i in idx:
        end = int(e[i])
        ctx = "".join(pieces[max(0, end - 8):end + 3]).replace("\n", "\\n")
        print(f"{sh(t):<11} end={end:>4} offset={end-o:>+5} R={z[i]/b['thr_max']:.3f} "
              f"token={pieces[end]!r:<14} ctx=...{ctx}...")
print("\nCAND-B w=1 crossings (all 8 code traces):")
for t in PROG:
    b = Z["CAND-B|1"][t]
    z = np.asarray(b["z"]); e = np.asarray(b["ends"])
    idx = np.flatnonzero(z >= b["thr_max"])
    if not len(idx): continue
    pieces = rio.decode_token_texts(traces[t].token_ids.tolist())
    o = LAB[t]["product_onset"]
    inw = [i for i in idx if o <= e[i] < min(o + WIN, META[t]["T"])]
    print(f"{sh(t):<11} whole={len(idx)} in-window={len(inw)} tokens="
          + " ".join(f"{pieces[int(e[i])]!r}@{int(e[i])-o:+d}({z[i]/b['thr_max']:.2f})" for i in idx))

print("\n" + "=" * 90)
print("BLOCK 7 -- WITHIN-TRACE paired test: pre-onset customer-service prose vs the code window")
print("(immune to position-bucket / trace-level / batch confounds; needs >=16 pre-onset tokens)")
for cand, w in [("CAND-A", 1), ("CAND-A", 8), ("CAND-B", 1), ("CAND-B", 4)]:
    print(f"\n--- {cand} w={w}")
    print(f"{'trace':<11}{'ons':>4}{'n_pre':>6}{'medR_pre':>10}{'n_win':>6}{'medR_win':>10}{'delta':>9}"
          f" | {'ctrl':<11}{'medR_pre':>10}{'medR_win':>10}{'delta':>9}")
    dpos = dtot = 0
    for t in PROG:
        b = Z[f"{cand}|{w}"][t]
        z = np.asarray(b["z"]) / b["thr_max"]; e = np.asarray(b["ends"])
        o = LAB[t]["product_onset"]
        pre = z[(e >= max(0, o - 48)) & (e < o)]
        win = z[(e >= o) & (e < min(o + WIN, META[t]["T"]))]
        if len(pre) < 16:
            print(f"{sh(t):<11}{o:>4}{len(pre):>6}{'--':>10}{len(win):>6}{np.median(win):>+10.3f}{'--':>9}")
            continue
        d = float(np.median(win) - np.median(pre))
        dtot += 1; dpos += d > 0
        c = None
        for k, v in {"b2-f3-020": "b1-f2-062", "b2-f2-014": "b2-f4-023", "b1-f2-014": "b2-f4-050",
                     "b1-f3-016": "b1-f1-032", "b2-f2-012": "b2-f3-043"}.items():
            if sh(t) == k:
                c = [x for x in LAB if sh(x) == v][0]
        cs = ""
        if c:
            bc = Z[f"{cand}|{w}"][c]
            zc = np.asarray(bc["z"]) / bc["thr_max"]; ec = np.asarray(bc["ends"])
            oc = LAB[c]["product_onset"]
            prec = zc[(ec >= max(0, oc - 48)) & (ec < oc)]
            winc = zc[(ec >= oc) & (ec < min(oc + WIN, META[c]["T"]))]
            cs = (f" | {sh(c):<11}{np.median(prec):>+10.3f}{np.median(winc):>+10.3f}"
                  f"{float(np.median(winc)-np.median(prec)):>+9.3f}")
        print(f"{sh(t):<11}{o:>4}{len(pre):>6}{np.median(pre):>+10.3f}{len(win):>6}"
              f"{np.median(win):>+10.3f}{d:>+9.3f}{cs}")
    print(f"code traces with medR(code window) > medR(own pre-onset prose): {dpos}/{dtot}")
