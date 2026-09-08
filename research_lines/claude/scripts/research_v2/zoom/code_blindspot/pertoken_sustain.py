#!/usr/bin/env python3
"""Sustained-excursion structure + routine-percentile placement of the code block.

Read-only; consumes the cache written by pertoken_extract.py.
"""
from __future__ import annotations

import bisect
import json
import statistics as st
from pathlib import Path

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
OUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
NW = ROOT / "artifacts" / "agent_v2" / "research_v2" / "narrow_window"

CACHE = json.loads((OUT / "pertoken.json").read_text(encoding="utf-8"))
ANA = json.loads((OUT / "pertoken_analysis.json").read_text(encoding="utf-8"))
META, RUNS = CACHE["meta"], CACHE["runs"]
WIDTHS = {"CAND-A": [1, 2, 4, 8], "CAND-B": [1, 2, 4]}

THR = {}
for cand, sub in (("CAND-A", "wgm_c2_w1248"), ("CAND-B", "pdm_c12_w124")):
    res = json.loads((NW / sub / "result.json").read_text(encoding="utf-8"))
    for cr in res["case_runs"]:
        if cr["window_width"] in WIDTHS[cand]:
            for h in ("0", "1"):
                THR[(cand, cr["window_width"], cr["case"], int(h))] = float(
                    cr["calibration"]["D"]["halves"][h]["thresholds"]["max|alpha0.1"]["threshold"]
                )


def sh(t):
    return "-".join(t.split("-")[:3])


def rmap(cand, w, tid):
    b = RUNS[cand][str(w)][tid]
    thr = THR[(cand, w, b["case"], b["cal_half"])]
    return {e: v / thr for v, e in zip(b["z"], b["ends"])}


def longest_run(vals, bar):
    best = cur = 0
    for v in vals:
        cur = cur + 1 if v >= bar else 0
        best = max(best, cur)
    return best


def routine_pool(cand, w):
    vals = []
    for tid in CACHE["routine"]:
        b = RUNS[cand][str(w)][tid]
        thr = THR[(cand, w, b["case"], b["cal_half"])]
        vals.extend(v / thr for v in b["z"])
    vals.sort()
    return vals


def main():
    pairs = ANA["matched_pairs"]
    report = {}
    for cand in WIDTHS:
        for w in WIDTHS[cand]:
            key = f"{cand}|w{w}"
            pool = routine_pool(cand, w)
            n = len(pool)
            rows = {}
            for p in pairs:
                for tag, tid in (("code", p["code"]), ("control", p["control"])):
                    m = META[tid]
                    o = m["product_onset"]
                    rm = rmap(cand, w, tid)
                    idx = [i for i in range(o, min(o + 48, m["T"])) if i in rm]
                    vals = [rm[i] for i in idx]
                    med = st.median(vals)
                    rows.setdefault(tag, {})[tid] = {
                        "median_ratio": round(med, 3),
                        "routine_percentile_of_median": round(
                            100.0 * bisect.bisect_left(pool, med) / n, 1
                        ),
                        "longest_run_ge_0.5": longest_run(vals, 0.5),
                        "longest_run_ge_1.0": longest_run(vals, 1.0),
                        "frac_ge_0.5": round(sum(1 for v in vals if v >= 0.5) / len(vals), 3),
                    }
            report[key] = rows
    (OUT / "pertoken_sustain.json").write_text(json.dumps(report, indent=1), encoding="utf-8")

    for key in ("CAND-A|w1", "CAND-A|w8", "CAND-B|w1", "CAND-B|w4"):
        print("=" * 100)
        print(key)
        print(f"{'':5s} {'trace':14s} {'medR':>7s} {'pctile of routine':>18s} {'frac>=0.5':>10s} "
              f"{'run>=0.5':>9s} {'run>=1.0':>9s}")
        for tag in ("code", "control"):
            for tid, v in report[key][tag].items():
                print(f"{tag[:4]:5s} {sh(tid):14s} {v['median_ratio']:7.3f} "
                      f"{v['routine_percentile_of_median']:18.1f} {v['frac_ge_0.5']:10.3f} "
                      f"{v['longest_run_ge_0.5']:9d} {v['longest_run_ge_1.0']:9d}")


if __name__ == "__main__":
    main()
