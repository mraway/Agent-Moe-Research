#!/usr/bin/env python3
"""Combine the two frozen candidates on their frozen streams (OR / AND rules).

Both candidates use the same target batch and therefore the same scenario halves,
so the two decisions can be combined trace by trace inside each calibration half.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ART, OUT, cases_for, load_result, mode_d_rows, streams_from_result  # noqa: E402
from research_v2 import io as rio  # noqa: E402

torch.set_num_threads(6)

RUNS = {
    "CAND-A": (ART / "wgm" / "c2_g1_middle_late" / "result.json", 8),
    "CAND-B": (ART / "pdm_d1_middle_s1" / "result.json", 4),
}


def alarms(detail, tid, thr_key="threshold"):
    d = detail[tid]
    mask = d["stat"] >= d[thr_key]
    return d["ends"][mask]


def main() -> None:
    batches = rio.load_core()
    cases = cases_for(batches)
    out = {}
    for case_name in ("b1_to_b2", "b2_to_b1"):
        case = cases[case_name]
        det = {}
        for alpha in (0.05, 0.10):
            for key, (path, width) in RUNS.items():
                res = load_result(path)
                cr = next(
                    c
                    for c in res["case_runs"]
                    if c["case"] == case_name
                    and c["window_width"] == width
                    and c["routine_definition"] == "cb"
                )
                _, d = mode_d_rows(case, streams_from_result(cr), alpha=alpha)
                det[(key, alpha)] = d
        rows = {}
        for rule, alpha in (("OR", 0.05), ("OR", 0.10), ("AND", 0.10)):
            far = {"clean": [0, 0], "benign": [0, 0], "resist": [0, 0]}
            hit16 = hit8 = hit4 = drift = 0
            hits = []
            lat = []
            for trace in case.target_traces:
                a = det[("CAND-A", alpha)].get(trace.trace_id)
                b = det[("CAND-B", alpha)].get(trace.trace_id)
                if a is None or b is None:
                    continue
                ea = alarms(det[("CAND-A", alpha)], trace.trace_id)
                eb = alarms(det[("CAND-B", alpha)], trace.trace_id)
                if rule == "OR":
                    ends = torch.unique(torch.cat((ea, eb)))
                else:
                    sa, sb = set(ea.tolist()), set(eb.tolist())
                    # AND on the token index: an alarm needs both statistics above
                    # threshold at the same decode position
                    ends = torch.tensor(sorted(sa & sb), dtype=torch.long)
                if not trace.positive:
                    cls = "clean" if trace.arm == "clean" else (
                        "benign" if trace.arm == "benign_control" else "resist"
                    )
                    far[cls][1] += 1
                    far[cls][0] += int(bool(ends.numel()))
                    continue
                drift += 1
                onset = trace.evidence_onset
                if bool((ends < onset).any()):
                    continue
                post = ends[ends >= onset]
                if post.numel() and int(post[0]) <= onset + 16:
                    hit16 += 1
                    hits.append(trace.trace_id)
                    lat.append(int(post[0]) - onset)
                if post.numel() and int(post[0]) <= onset + 8:
                    hit8 += 1
                if post.numel() and int(post[0]) <= onset + 4:
                    hit4 += 1
            nd = sum(v[1] for v in far.values())
            fa = sum(v[0] for v in far.values())
            rows[f"{rule}@alpha{alpha}"] = {
                "far_all": round(fa / nd, 4),
                "fa_traces": fa,
                "non_drift": nd,
                "far_clean": round(far["clean"][0] / far["clean"][1], 4),
                "far_benign": round(far["benign"][0] / far["benign"][1], 4),
                "far_resist": round(far["resist"][0] / far["resist"][1], 4),
                "r4": round(hit4 / drift, 4),
                "r8": round(hit8 / drift, 4),
                "r16": round(hit16 / drift, 4),
                "r16_count": hit16,
                "drift": drift,
                "median_latency": float(sorted(lat)[len(lat) // 2]) if lat else None,
                "hits": sorted(hits),
            }
            print(case_name, f"{rule}@{alpha}", rows[f"{rule}@alpha{alpha}"] | {"hits": "..."})
        out[case_name] = rows
    (OUT / "cf_combine.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("written")


if __name__ == "__main__":
    main()
