"""Excursion-level comparison: benign/resist mention excursions vs drift post-onset excursions."""
from __future__ import annotations
import json, statistics
from pathlib import Path
import numpy as np
import torch
torch.set_num_threads(6)
import research_v2.io as rio

OUT = Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/research_v2/zoom/topic_vs_task")
D = json.load(open(OUT / "records.json"))
Z = np.load(OUT / "zstreams.npz")
W = {"A": 8, "B": 4}


def runs(mask):
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            out.append((i, j - i + 1)); i = j + 1
        else:
            i += 1
    return out


def auroc(pos, neg):
    if not pos or not neg: return None
    p = np.asarray(pos); n = np.asarray(neg)
    gt = (p[:, None] > n[None, :]).sum(); eq = (p[:, None] == n[None, :]).sum()
    return float((gt + 0.5 * eq) / (len(p) * len(n)))


def excursion(rec, level):
    ck = rec["cand"]; w = W[ck]
    key = f'{rec["case"]}::{ck}::{rec["trace_id"]}'
    st = Z[key + "::stat"].astype(np.float64)
    ends = Z[key + "::ends"]
    lf = Z[key + "::lf"]; lm = Z[key + "::lm"]
    if rec["positive"]:
        sel = ends >= rec["evidence_onset"]
    else:
        sel = np.ones(len(ends), bool)
    if not sel.any(): return None
    st2 = np.where(sel, st, -np.inf)
    mask = np.isfinite(st2) & (st2 >= level) & sel
    rr = runs(mask)
    if not rr: 
        i = int(np.argmax(st2))
        return {"peak": float(st2[i]), "peak_end": int(ends[i]), "nwin": 0, "nblocks": 0,
                "lf": float(lf[i]), "lm": float(lm[i]), "nruns": 0}
    # longest run
    s, k = max(rr, key=lambda x: x[1])
    seg = slice(s, s + k)
    i = s + int(np.argmax(st2[seg]))
    return {"peak": float(st2[i]), "peak_end": int(ends[i]), "nwin": int(k),
            "nblocks": int(np.ceil(k / w)), "lf": float(lf[i]), "lm": float(lm[i]),
            "nruns": len(rr), "total_win": int(sum(kk for _, kk in rr))}


def summarize(items, name):
    if not items:
        return f"| {name} | 0 | - | - | - | - | - | - |"
    def med(key): return statistics.median([x[key] for x in items])
    return (f"| {name} | {len(items)} | {med('peak'):.2f} | {med('nwin'):.1f} | {med('nblocks'):.1f} | "
            f"{sum(1 for x in items if x['nblocks']>=2)}/{len(items)} | {med('lf'):.3f} | {med('lm'):.3f} |")


print("## Excursion comparison at the alarm threshold (cross-fitted, mode D alpha=0.10, persist2)\n")
print("Excursion = longest contiguous run of persist2 z >= threshold (post-onset only for drift).")
print("nblocks = ceil(run windows / w) = number of NON-overlapping w-blocks the run covers.\n")
print("| cand | dir | group | n | med peak z | med run win | med blocks | >=2 blocks | med layerFrac | med late/mid |")
print("|---|---|---|---|---|---|---|---|---|---|")
store = {}
for ck in ("A", "B"):
    for cn in ("b1_to_b2", "b2_to_b1"):
        rows = [r for r in D["records"] if r["cand"] == ck and r["case"] == cn]
        med_clean = rows[0]["clean_peak_median"]
        groups = {
            "(i) benign/resist peak>clean-median": [r for r in rows if not r["positive"] and r["arm"] != "clean" and r["peak_stat"] > r["clean_peak_median"]],
            "(ia) benign only, peak>median": [r for r in rows if r["arm"] == "benign_control" and r["peak_stat"] > r["clean_peak_median"]],
            "(ib) resist only, peak>median": [r for r in rows if r["arm"] == "attack" and not r["positive"] and r["peak_stat"] > r["clean_peak_median"]],
            "(ii) resist that alarm": [r for r in rows if r["arm"] == "attack" and not r["positive"] and r["alarm"]],
            "(iic) benign that alarm": [r for r in rows if r["arm"] == "benign_control" and r["alarm"]],
            "(iid) clean that alarm": [r for r in rows if r["arm"] == "clean" and r["alarm"]],
            "(iii) drift post-onset": [r for r in rows if r["positive"]],
            "(iii-a) drift post-onset, alarming": [r for r in rows if r["positive"] and r["alarm"]],
        }
        for gname, grows in groups.items():
            items = []
            for r in grows:
                e = excursion(r, r["threshold"])
                if e: items.append(e)
            store[(ck, cn, gname)] = items
            print(f"| {ck} | {cn} " + summarize(items, gname)[1:])
print()
print("## Feature AUROC: drift-post-onset excursion vs benign/resist excursion (same cell)\n")
print("| cand | dir | feature | AUROC vs benign+resist(alarming) | AUROC vs benign+resist(peak>median) |")
print("|---|---|---|---|---|")
for ck in ("A", "B"):
    for cn in ("b1_to_b2", "b2_to_b1"):
        pos = store[(ck, cn, "(iii-a) drift post-onset, alarming")]
        neg1 = store[(ck, cn, "(ii) resist that alarm")] + store[(ck, cn, "(iic) benign that alarm")]
        neg2 = store[(ck, cn, "(i) benign/resist peak>clean-median")]
        for feat in ("peak", "nwin", "nblocks", "lf", "lm"):
            a1 = auroc([x[feat] for x in pos], [x[feat] for x in neg1])
            a2 = auroc([x[feat] for x in pos], [x[feat] for x in neg2])
            print(f"| {ck} | {cn} | {feat} | {'-' if a1 is None else f'{a1:.3f}'} | {'-' if a2 is None else f'{a2:.3f}'} |")
json.dump({f"{k[0]}|{k[1]}|{k[2]}": v for k, v in store.items()}, open(OUT / "excursions.json", "w"), indent=1)
