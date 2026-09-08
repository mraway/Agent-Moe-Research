#!/usr/bin/env python3
"""Reproduction check: PDM C12 w=4 narrow-window rerun vs the frozen run.

Compares per-trace alarm endpoints (candidates[*].trace_alarms) and the stored
conformal thresholds for mode D, alpha 0.10, readings max / persist2, in both
S1 cases.  Read-only.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FROZEN = ROOT / "artifacts/agent_v2/research_v2/pdm_d1_middle_s1/result.json"
NEW = ROOT / "artifacts/agent_v2/research_v2/narrow_window/pdm_c12_w124/result.json"


def case_runs(res, width):
    return {cr["case"]: cr for cr in res["case_runs"]
            if cr["split"] == "S1" and cr["window_width"] == width
            and cr["routine_definition"] == "cb"}


def cand(cr, mode, alpha, reading):
    for c in cr["candidates"]:
        if c["mode"] == mode and abs(c["alpha"] - alpha) < 1e-12 and c["reading"] == reading:
            return c
    raise KeyError((mode, alpha, reading))


def main() -> None:
    frozen = json.loads(FROZEN.read_text())
    new = json.loads(NEW.read_text())
    out = {"frozen_commit": frozen.get("code_commit"), "new_commit": new.get("code_commit"),
           "wall_clock_seconds": new.get("wall_clock_seconds"), "cases": {}}
    fr = case_runs(frozen, 4)
    nr = case_runs(new, 4)
    for case in ("b1_to_b2", "b2_to_b1"):
        block = {"readings": {}, "thresholds": {}}
        for reading in ("max", "persist2"):
            fc = cand(fr[case], "D", 0.1, reading)
            nc = cand(nr[case], "D", 0.1, reading)
            f = {r[0]: r for r in fc["trace_alarms"]}
            n = {r[0]: r for r in nc["trace_alarms"]}
            ids = sorted(set(f) | set(n))
            mism = []
            for t in ids:
                a, b = f.get(t), n.get(t)
                if a is None or b is None or a[2] != b[2] or a[3] != b[3] or a[4] != b[4]:
                    mism.append({"trace_id": t, "frozen": a, "new": b})
            block["readings"][reading] = {
                "traces_frozen": len(f), "traces_new": len(n), "traces_compared": len(ids),
                "endpoint_mismatches": sum(1 for m in mism if m["frozen"] and m["new"]
                                           and m["frozen"][2] != m["new"][2]),
                "any_field_mismatches": len(mism),
                "first_5": mism[:5],
            }
        # thresholds
        for half in sorted(fr[case]["calibration"]["D"]["halves"]):
            ft = fr[case]["calibration"]["D"]["halves"][half]["thresholds"]
            nt = nr[case]["calibration"]["D"]["halves"][half]["thresholds"]
            for key in ("max|alpha0.1", "persist2|alpha0.1"):
                block["thresholds"][f"half{half}|{key}"] = {
                    "frozen": ft[key]["threshold"], "new": nt[key]["threshold"],
                    "equal": ft[key] == nt[key],
                }
        out["cases"][case] = block
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
