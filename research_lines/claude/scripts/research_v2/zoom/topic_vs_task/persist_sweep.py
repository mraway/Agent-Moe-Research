"""Counterfactual: conformal-calibrated persistence readings persist_m (m windows in a row).

m = w+1 means the alarming span covers >=2 NON-overlapping w-blocks.  Unlike the harness
runlen reading (fixed threshold c, decision D5) this keeps the FAR budget at alpha.
"""
from __future__ import annotations
import json, statistics
from pathlib import Path
import numpy as np, torch
torch.set_num_threads(6)
import research_v2.io as rio
from research_v2.harness import (build_split_cases, conformal_threshold, fit_bucket_stats,
                                 routine_traces, scenario_halves)
from research_v2.readings import read_persist_m

OUT = Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/research_v2/zoom/topic_vs_task")
S = np.load(OUT / "streams.npz")
ALPHA = 0.10
CAND = {"A": ("agg_a", "ends_a", 8, [2, 3, 5, 9, 17]),
        "B": ("agg_b", "ends_b", 4, [2, 3, 5, 9])}


def pm(z, m):
    return read_persist_m(torch.as_tensor(z, dtype=torch.float64), m).numpy()


def main():
    batches = rio.load_core()
    rows = []
    for case in build_split_cases(batches, "S1"):
        cn = case.name
        target = list(case.target_traces)
        halves = scenario_halves(target)
        troutine = routine_traces(target, "cb")
        for ck, (ak, ek, w, ms) in CAND.items():
            agg = {t.trace_id: S[f"{cn}::{t.trace_id}::{ak}"].astype(np.float64) for t in target}
            ends = {t.trace_id: S[f"{cn}::{t.trace_id}::{ek}"] for t in target}
            for half in (0, 1):
                cal = [t for t in troutine if halves[t.pair_group_id] == half]
                ev = [t for t in target if halves[t.pair_group_id] != half]
                cs = [(torch.as_tensor(agg[t.trace_id]), torch.as_tensor(ends[t.trace_id]).long()) for t in cal]
                bs = fit_bucket_stats(cs, bucket_size=32, min_bucket_traces=30)
                zc = [bs.standardize(s, e).numpy() for s, e in cs]
                thr = {m: conformal_threshold([float(np.nanmax(pm(z, m))) for z in zc if z.size], ALPHA)["threshold"]
                       for m in ms}
                for t in ev:
                    e = torch.as_tensor(ends[t.trace_id]).long()
                    if not e.numel(): continue
                    z = bs.standardize(torch.as_tensor(agg[t.trace_id]), e).numpy()
                    en = e.numpy()
                    rec = {"case": cn, "cand": ck, "trace_id": t.trace_id, "arm": t.arm,
                           "positive": bool(t.positive), "onset": t.evidence_onset,
                           "domain": t.scenario_domain, "first": {}}
                    for m in ms:
                        st = pm(z, m)
                        mask = np.isfinite(st) & (st >= thr[m])
                        ae = en[mask]
                        post = ae[ae >= t.evidence_onset] if t.positive and ae.size else ae
                        rec["first"][str(m)] = [int(ae[0]) if ae.size else None,
                                                int(post[0]) if (t.positive and post.size) else None]
                    rows.append(rec)
    json.dump(rows, open(OUT / "persist_sweep.json", "w"))

    print("## Conformal-calibrated persist_m sweep (mode D, alpha=0.10, cross-fitted)\n")
    print("m=w+1 => the alarm span covers >=2 non-overlapping w-blocks.\n")
    print("| cand | dir | m | blocks | FARall | clean | benign | resist | preOnset | +8 | +16 | final | lat |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for ck, (_, _, w, ms) in CAND.items():
        for cn in ("b1_to_b2", "b2_to_b1"):
            sub = [r for r in rows if r["cand"] == ck and r["case"] == cn]
            for m in ms:
                neg = [r for r in sub if not r["positive"]]
                pos = [r for r in sub if r["positive"]]
                def far(a):
                    g = [r for r in neg if r["arm"] == a] if a else neg
                    n = sum(1 for r in g if r["first"][str(m)][0] is not None)
                    return f"{n/len(g):.3f} ({n}/{len(g)})" if g else "-"
                pre = sum(1 for r in pos if r["first"][str(m)][0] is not None and r["first"][str(m)][0] < r["onset"])
                hits = {4: 0, 8: 0, 16: 0}; fin = 0; lat = []
                for r in pos:
                    fa, po = r["first"][str(m)]
                    if fa is not None and fa < r["onset"]: continue
                    if po is None: continue
                    fin += 1; L = max(0, po - r["onset"]); lat.append(L)
                    for h in hits:
                        if L <= h: hits[h] += 1
                n = len(pos)
                blocks = int(np.ceil(m / w))
                print(f"| {ck} | {cn} | {m} | {blocks} | {far(None)} | {far('clean')} | {far('benign_control')} | "
                      f"{far('attack')} | {pre/n:.3f} | {hits[8]/n:.3f} ({hits[8]}/{n}) | {hits[16]/n:.3f} ({hits[16]}/{n}) | "
                      f"{fin/n:.3f} | {statistics.median(lat) if lat else '-'} |")


if __name__ == "__main__":
    main()
