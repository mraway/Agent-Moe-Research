#!/usr/bin/env python3
"""Extra checks for the statistics lens: horizon reachability (censoring),
pooled median latency, paired flips on E0 / negatives, paired CIs on drift."""
from __future__ import annotations
import json, math, random, statistics, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from audit_statistics_recompute import RUNS, READINGS, main as build, cp, mcnemar_exact, hit

LAB = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363/docs/research_v2/labels")
FROZEN_W = {"CAND-A": 8, "CAND-B": 4}

def rj(p): return {json.loads(l)["trace_id"]: json.loads(l) for l in open(p) if l.strip()}

def main():
    vec, anchored, prod, leak, windows, cov = build()
    resist_all = rj(LAB / "topic_entry_v1_adjudicated.jsonl")
    e0_ids = {k for k, v in resist_all.items() if v["topic_entry_onset"] is None}

    # ---- ends per (candidate, window, trace) from score_streams ----
    ends = {}
    negs = {}
    alarm = {}
    for cand, (p, fw) in RUNS.items():
        d = json.loads(Path(p).read_text())
        for cr in d["case_runs"]:
            if cr["routine_definition"] != "cb":
                continue
            w = cr["window_width"]
            for tid, blk in cr["score_streams"].items():
                ends[(cand, w, tid)] = blk["ends"]
            for c in cr["candidates"]:
                if c["mode"] != "D" or c["reading"] not in READINGS or abs(c["alpha"] - 0.1) > 1e-9:
                    continue
                for r in c["trace_alarms"]:
                    alarm[(cand, w, c["reading"], r[0])] = r[2]

    print("## reachability (censoring) of the +h horizon, anchored resist, n=14")
    print("| cand | w | reachable +4 | +8 | +16 | first endpoint (min over 14) |")
    print("|---|---|---|---|---|---|")
    for cand in ("CAND-A", "CAND-B"):
        for w in windows[cand]:
            cells = []
            for h in (4, 8, 16):
                n = sum(1 for t, lab in anchored.items()
                        if any(lab["topic_entry_onset"] <= e <= lab["topic_entry_onset"] + h
                               for e in ends[(cand, w, t)]))
                cells.append(f"{n}/14")
            f0 = [ends[(cand, w, t)][0] for t in anchored]
            print(f"| {cand} | {w} | " + " | ".join(cells) + f" | {min(f0)} |")

    print()
    print("## reachability, 59 drift, product_onset anchor")
    print("| cand | w | reachable +4 | +8 | +16 |")
    print("|---|---|---|---|---|")
    for cand in ("CAND-A", "CAND-B"):
        for w in windows[cand]:
            cells = []
            for h in (4, 8, 16):
                n = sum(1 for t, lab in prod.items()
                        if any(lab["product_onset"] <= e <= lab["product_onset"] + h
                               for e in ends[(cand, w, t)]))
                cells.append(f"{n}/59")
            print(f"| {cand} | {w} | " + " | ".join(cells) + " |")

    print()
    print("## pooled median hit latency, anchored resist (recheck of the headline)")
    for cand in ("CAND-A", "CAND-B"):
        for w in windows[cand]:
            for r in READINGS:
                d = vec[(cand, w, 0.1, r, 0, "resist")]
                lat = sorted(l for h, l in d.values() if h)
                med = statistics.median(lat) if lat else None
                print(f"  {cand} w={w} {r}: n_hits={len(lat)} latencies={lat} median={med}")

    print()
    print("## E0 (47) paired flips vs frozen window, and negatives (240) paired flips, alpha 0.10")
    print("| cand | narrow w | reading | pool | frozen alarms | narrow alarms | b (lost) | c (gained) | p_exact |")
    print("|---|---|---|---|---|---|---|---|---|")
    neg_ids = [t for (c, w, rr, t) in alarm
               if c == "CAND-A" and w == 8 and rr == "max"
               and t not in resist_all and t not in prod]
    for cand in ("CAND-A", "CAND-B"):
        fw = FROZEN_W[cand]
        for w in windows[cand]:
            if w == fw: continue
            for r in READINGS:
                for pool, ids in (("E0", sorted(e0_ids)), ("clean+benign", sorted(neg_ids))):
                    b = c_ = 0
                    kf = kn = 0
                    for t in ids:
                        f = alarm[(cand, fw, r, t)] is not None
                        n = alarm[(cand, w, r, t)] is not None
                        kf += f; kn += n
                        if f and not n: b += 1
                        elif n and not f: c_ += 1
                    print(f"| {cand} | {w} | {r} | {pool} | {kf} | {kn} | {b} | {c_} | "
                          f"{mcnemar_exact(b, c_):.3f} |")

    print()
    print("## paired 95% bootstrap CI on the drift R+16 difference alone (59 traces, seed 20260905)")
    rng = random.Random(20260905)
    draws = [[rng.randrange(59) for _ in range(59)] for _ in range(1000)]
    dids = sorted(prod)
    print("| cand | narrow w | reading | frozen | narrow | obs drop | 95% CI on diff | prereg -0.10 line inside CI? |")
    print("|---|---|---|---|---|---|---|---|")
    for cand in ("CAND-A", "CAND-B"):
        fw = FROZEN_W[cand]
        for w in windows[cand]:
            if w == fw: continue
            for r in READINGS:
                fr = vec[(cand, fw, 0.1, r, 0, "drift")]; na = vec[(cand, w, 0.1, r, 0, "drift")]
                fv = [1 if (fr[t][0] and fr[t][1] <= 16) else 0 for t in dids]
                nv = [1 if (na[t][0] and na[t][1] <= 16) else 0 for t in dids]
                obs = (sum(nv) - sum(fv)) / 59
                ds = sorted(sum(nv[i] - fv[i] for i in idx) / 59 for idx in draws)
                lo, hi = ds[24], ds[974]
                inside = "yes" if lo <= -0.10 <= hi else "no"
                print(f"| {cand} | {w} | {r} | {sum(fv)}/59 | {sum(nv)}/59 | {obs:+.4f} | "
                      f"[{lo:+.3f}, {hi:+.3f}] | {inside} |")

    print()
    print("## per-direction anchored resist R+16 (n=7) with exact intervals, alpha 0.10")
    b1_ids = [t for t in anchored if t.startswith("b1-")]
    b2_ids = [t for t in anchored if t.startswith("b2-")]
    print("| cand | w | reading | b1_to_b2 (b2 traces) | b2_to_b1 (b1 traces) |")
    print("|---|---|---|---|---|")
    for cand in ("CAND-A", "CAND-B"):
        for w in windows[cand]:
            for r in READINGS:
                d = vec[(cand, w, 0.1, r, 0, "resist")]
                cells = []
                for ids in (b2_ids, b1_ids):
                    k = sum(1 for t in ids if d[t][0] and d[t][1] <= 16)
                    lo, hi = cp(k, 7)
                    cells.append(f"{k}/7 [{lo:.3f}, {hi:.3f}]")
                print(f"| {cand} | {w} | {r} | " + " | ".join(cells) + " |")

if __name__ == "__main__":
    main()
