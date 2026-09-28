#!/usr/bin/env python3
"""Protocol/leakage/reproduction audit probe for the narrow-window round. Read-only."""
from __future__ import annotations
import json, sys
from pathlib import Path

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
ART = ROOT / "artifacts" / "agent_v2" / "research_v2"
RUNS = {
    "CAND-A/new": ART / "narrow_window" / "wgm_c2_w1248" / "result.json",
    "CAND-A/frozen": ART / "wgm" / "c2_g1_middle_late" / "result.json",
    "CAND-B/new": ART / "narrow_window" / "pdm_c12_w124" / "result.json",
    "CAND-B/frozen": ART / "pdm_d1_middle_s1" / "result.json",
}

def load(k):
    return json.loads(RUNS[k].read_text())

def config_dump():
    for k in RUNS:
        r = load(k)
        print("==", k)
        print(json.dumps({"config": r.get("config"), "commit": r.get("code_commit"),
                          "schema": r.get("schema_version"), "run_name": r.get("run_name"),
                          "n_case_runs": len(r["case_runs"]),
                          "windows": sorted({cr["window_width"] for cr in r["case_runs"]}),
                          "routines": sorted({cr["routine_definition"] for cr in r["case_runs"]}),
                          "cases": sorted({cr["case"] for cr in r["case_runs"]}),
                          "modes": sorted({c["mode"] for cr in r["case_runs"] for c in cr["candidates"]}),
                          "readings": sorted({c["reading"] for cr in r["case_runs"] for c in cr["candidates"]}),
                          }, indent=1, default=str))

def calib_dump():
    rows = []
    for k in RUNS:
        r = load(k)
        for cr in r["case_runs"]:
            for half, blk in sorted(cr["calibration"]["D"]["halves"].items()):
                rows.append({
                    "run": k, "case": cr["case"], "w": cr["window_width"], "half": half,
                    "cal_n": blk["calibration_trace_count"],
                    "cal_target": blk["calibration_from_target"],
                    "cal_extra": blk["calibration_from_extra_pool"],
                    "eval_n": blk["evaluated_trace_count"],
                    "bucket_cap": blk["bucket_cap"],
                    "bucket_trace_counts": blk["bucket_trace_counts"],
                    "bucket_window_counts": blk["bucket_window_counts"],
                    "bucket_reused": blk["bucket_reused"],
                    "thr_max_10": blk["thresholds"]["max|alpha0.1"]["threshold"],
                    "thr_p2_10": blk["thresholds"]["persist2|alpha0.1"]["threshold"],
                    "thr_max_10_n": blk["thresholds"]["max|alpha0.1"].get("trace_count") or blk["thresholds"]["max|alpha0.1"].get("n"),
                    "thr_max_10_blk": blk["thresholds"]["max|alpha0.1"],
                    "target_routine_n": cr["target_routine_trace_count"],
                    "target_n": cr["target_trace_count"],
                    "extra_cal_n": cr["extra_calibration_trace_count"],
                    "fit_n": cr["fit_trace_count"],
                    "pooling": cr["calibration"]["D"].get("pooling"),
                })
    print(json.dumps(rows, indent=1))

if __name__ == "__main__":
    {"config": config_dump, "calib": calib_dump}[sys.argv[1]]()
