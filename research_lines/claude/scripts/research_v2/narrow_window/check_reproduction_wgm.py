#!/usr/bin/env python3
"""Reproduction check: window 8 mode D alpha 0.10 (max, persist2) alarm endpoints
of the narrow-window WGM run vs the frozen CAND-A run. Read-only."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
ART = ROOT / "artifacts" / "agent_v2" / "research_v2"
FROZEN = ART / "wgm" / "c2_g1_middle_late" / "result.json"
NEW = ART / "narrow_window" / "wgm_c2_w1248" / "result.json"


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def case_run(res, case, width=8):
    for cr in res["case_runs"]:
        if (cr["split"] == "S1" and cr["case"] == case
                and cr["window_width"] == width and cr["routine_definition"] == "cb"):
            return cr
    raise KeyError((case, width))


def alarms(cr, reading):
    cand = next(c for c in cr["candidates"]
                if c["candidate_id"].endswith(f"mode=D|alpha=0.1|reading={reading}"))
    return {r[0]: r for r in cand["trace_alarms"]}


def main():
    frozen, new = load(FROZEN), load(NEW)
    report = {"frozen": str(FROZEN), "new": str(NEW),
              "frozen_commit": frozen.get("code_commit"),
              "new_commit": new.get("code_commit"),
              "window": 8, "mode": "D", "alpha": 0.10, "cases": {}}
    for case in ("b1_to_b2", "b2_to_b1"):
        fcr, ncr = case_run(frozen, case), case_run(new, case)
        block = {"readings": {}, "thresholds": {}}
        for reading in ("max", "persist2"):
            fa, na = alarms(fcr, reading), alarms(ncr, reading)
            ids = sorted(set(fa) | set(na))
            mism = []
            for tid in ids:
                f, n = fa.get(tid), na.get(tid)
                if f is None or n is None:
                    mism.append({"trace_id": tid, "frozen": f, "new": n})
                elif f[2] != n[2] or f[3] != n[3] or f[4] != n[4] or f[1] != n[1]:
                    mism.append({"trace_id": tid, "frozen": f, "new": n})
            block["readings"][reading] = {
                "traces_frozen": len(fa), "traces_new": len(na),
                "traces_compared": len(set(fa) & set(na)),
                "mismatches": len(mism), "first_5": mism[:5],
                "endpoint_only_mismatches": sum(
                    1 for m in mism if m.get("frozen") and m.get("new")
                    and m["frozen"][2] != m["new"][2]),
            }
        # conformal thresholds
        for half in ("0", "1"):
            ft = fcr["calibration"]["D"]["halves"][half]["thresholds"]
            nt = ncr["calibration"]["D"]["halves"][half]["thresholds"]
            for key in ("max|alpha0.1", "persist2|alpha0.1", "max|alpha0.05", "persist2|alpha0.05"):
                block["thresholds"][f"half{half}|{key}"] = {
                    "frozen": ft[key]["threshold"], "new": nt[key]["threshold"],
                    "equal": ft[key]["threshold"] == nt[key]["threshold"],
                }
        report["cases"][case] = block
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
