#!/usr/bin/env python3
"""Audit lens (c): false-alarm anatomy under narrow windows.

Read-only.  Recomputes clean/benign false-alarm sets from the STORED trace_alarms of the
two narrow-window runs (and of the two frozen runs), independently of
scripts/research_v2/narrow_window/evaluate.py, and dumps per-trace evidence
(first alarm end, alarm window decode text, +-32 token context, calibration half).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import torch

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "research_v2" / "zoom" / "missed_drift"))

from common import cases_for, load_result, snippet  # noqa: E402
from research_v2 import io as rio  # noqa: E402
from research_v2.harness import scenario_halves  # noqa: E402

torch.set_num_threads(8)

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/nw_fa")
OUT.mkdir(parents=True, exist_ok=True)

RUNS = {
    "CAND-A": {
        "narrow": ROOT / "artifacts/agent_v2/research_v2/narrow_window/wgm_c2_w1248/result.json",
        "frozen": ROOT / "artifacts/agent_v2/research_v2/wgm/c2_g1_middle_late/result.json",
        "frozen_window": 8,
    },
    "CAND-B": {
        "narrow": ROOT / "artifacts/agent_v2/research_v2/narrow_window/pdm_c12_w124/result.json",
        "frozen": ROOT / "artifacts/agent_v2/research_v2/pdm_d1_middle_s1/result.json",
        "frozen_window": 4,
    },
}
READINGS = ("max", "persist2")
ALPHA = 0.1

batches = rio.load_core()
cases = cases_for(batches)
trace_of = {}
for cname, case in cases.items():
    for t in case.target_traces:
        trace_of[(cname, t.trace_id)] = t

rows = []
summary = {}
for cand, spec in RUNS.items():
    for tag, path in (("narrow", spec["narrow"]), ("frozen", spec["frozen"])):
        res = load_result(path)
        for cr in res["case_runs"]:
            if cr["split"] != "S1" or cr["routine_definition"] != "cb":
                continue
            w = cr["window_width"]
            if tag == "frozen" and w != spec["frozen_window"]:
                continue
            case = cases[cr["case"]]
            halves = scenario_halves(case.target_traces)
            ends_by = {tid: b["ends"] for tid, b in cr["score_streams"].items()}
            for c in cr["candidates"]:
                if c["mode"] != "D" or c["reading"] not in READINGS or c["alpha"] != ALPHA:
                    continue
                key = (cand, tag, w, c["reading"], cr["case"])
                n_c = n_b = fa_c = fa_b = 0
                half_tot = {0: 0, 1: 0}
                half_fa = {0: 0, 1: 0}
                for row in c["trace_alarms"]:
                    tid = row[0]
                    tr = trace_of.get((cr["case"], tid))
                    if tr is None:
                        continue
                    cls = rio.arm_class(tr)
                    if cls not in ("clean", "benign"):
                        continue
                    # calibration half whose threshold evaluated this trace
                    own = halves[tr.pair_group_id]
                    cal_half = 1 - own
                    if cls == "clean":
                        n_c += 1
                    else:
                        n_b += 1
                    half_tot[cal_half] += 1
                    if row[2] is not None:
                        half_fa[cal_half] += 1
                        if cls == "clean":
                            fa_c += 1
                        else:
                            fa_b += 1
                        first = int(row[2])
                        ends = ends_by[tid]
                        rows.append({
                            "candidate": cand, "tag": tag, "window": w,
                            "reading": c["reading"], "case": cr["case"],
                            "trace_id": tid, "arm_class": cls, "arm": tr.arm,
                            "pair_group_id": tr.pair_group_id,
                            "workflow": getattr(tr, "workflow", None),
                            "domain": getattr(tr, "domain", None),
                            "channel": getattr(tr, "channel", None),
                            "decode_len": int(tr.token_count),
                            "first_alarm_end": first,
                            "rel_pos": first / max(1, int(tr.token_count)),
                            "n_alarm_endpoints": int(sum(1 for e in ends if e is not None)),
                            "alarm_onset_count": row[4],
                            "own_half": own, "cal_half": cal_half,
                            "window_text": snippet(tr, first - w + 1, first + 1),
                            "tail8": snippet(tr, first - 7, first + 1),
                            "context": snippet(tr, first - 32, first + 33),
                        })
                summary[".".join(map(str, key))] = {
                    "n_clean": n_c, "n_benign": n_b,
                    "fa_clean": fa_c, "fa_benign": fa_b,
                    "far_clean": fa_c / n_c if n_c else None,
                    "far_benign": fa_b / n_b if n_b else None,
                    "far_pooled": (fa_c + fa_b) / (n_c + n_b) if (n_c + n_b) else None,
                    "half": {str(h): {"n": half_tot[h], "fa": half_fa[h],
                                       "far": half_fa[h] / half_tot[h] if half_tot[h] else None}
                              for h in (0, 1)},
                    "stored_by_arm": {
                        k: c["metrics"]["by_arm"][k] for k in ("clean", "benign")
                    },
                }
            del ends_by
        del res

(OUT / "fa_rows.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
(OUT / "fa_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
print("rows", len(rows), "cells", len(summary))
