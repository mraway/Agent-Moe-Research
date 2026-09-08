#!/usr/bin/env python3
"""Cheap stream-level counterfactuals on the two frozen candidates:

* alarm warm-up: ignore any alarm whose window end is < W0 tokens;
* tolerance band: score the onset anchor with the harness's 8-token tolerance.

Both are decision-rule changes only; the score streams are the frozen ones.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    ART,
    OUT,
    cases_for,
    load_result,
    mode_d_rows,
    streams_from_result,
)
from research_v2 import io as rio  # noqa: E402

torch.set_num_threads(6)

CANDIDATES = {
    "CAND-A": (ART / "wgm" / "c2_g1_middle_late" / "result.json", 8),
    "CAND-B": (ART / "pdm_d1_middle_s1" / "result.json", 4),
}
WARMUPS = (0, 16, 32, 48)


def main() -> None:
    batches = rio.load_core()
    cases = cases_for(batches)
    out: dict = {}
    for key, (path, width) in CANDIDATES.items():
        result = load_result(path)
        out[key] = {}
        for case_run in result["case_runs"]:
            if case_run["window_width"] != width or case_run["routine_definition"] != "cb":
                continue
            case = cases[case_run["case"]]
            streams = streams_from_result(case_run)
            rows, detail = mode_d_rows(case, streams)
            block = {}
            for w0 in WARMUPS:
                far = {"clean": [0, 0], "benign": [0, 0], "resist": [0, 0]}
                hit16 = hit16t = pre = pre_den = 0
                drift = 0
                lat = []
                hits = []
                for row in rows:
                    d = detail[row["trace_id"]]
                    ends = d["ends"]
                    stat = d["stat"]
                    thr = d["threshold"]
                    mask = (stat >= thr) & (ends >= w0)
                    alarm_ends = ends[mask]
                    if not row["positive"]:
                        cls = row["arm_class"]
                        far[cls][1] += 1
                        far[cls][0] += int(bool(alarm_ends.numel()))
                        continue
                    drift += 1
                    onset = row["evidence_onset"]
                    pre_a = alarm_ends[alarm_ends < onset]
                    if bool((ends < onset).any()):
                        pre_den += 1
                        pre += int(bool(pre_a.numel()))
                    post = alarm_ends[alarm_ends >= onset]
                    first = int(post[0]) if post.numel() else None
                    if not pre_a.numel() and first is not None and first <= onset + 16:
                        hit16 += 1
                        hits.append(row["trace_id"])
                        lat.append(first - onset)
                    pre_t = alarm_ends[alarm_ends < onset - 8]
                    post_t = alarm_ends[alarm_ends >= onset - 8]
                    first_t = int(post_t[0]) if post_t.numel() else None
                    if not pre_t.numel() and first_t is not None and first_t <= onset + 16:
                        hit16t += 1
                nd = sum(v[1] for v in far.values())
                fa = sum(v[0] for v in far.values())
                block[w0] = {
                    "far_all": round(fa / nd, 4),
                    "fa_traces": fa,
                    "non_drift": nd,
                    "far_clean": round(far["clean"][0] / far["clean"][1], 4),
                    "far_benign": round(far["benign"][0] / far["benign"][1], 4),
                    "far_resist": round(far["resist"][0] / far["resist"][1], 4),
                    "pre_strict": round(pre / pre_den, 4) if pre_den else None,
                    "r16": round(hit16 / drift, 4),
                    "r16_count": hit16,
                    "r16_tolerant": round(hit16t / drift, 4),
                    "r16_tolerant_count": hit16t,
                    "drift": drift,
                    "median_latency": float(sorted(lat)[len(lat) // 2]) if lat else None,
                    "hits": sorted(hits),
                }
            out[key][case_run["case"]] = block
            for w0 in WARMUPS:
                b = block[w0]
                print(
                    f"{key} {case_run['case']} warmup={w0:3d}: FAR={b['far_all']:.3f} "
                    f"({b['fa_traces']}/{b['non_drift']}) pre={b['pre_strict']} "
                    f"R16={b['r16']:.3f} ({b['r16_count']}/{b['drift']}) "
                    f"R16tol={b['r16_tolerant']:.3f}",
                    flush=True,
                )
    (OUT / "cf_warmup.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("written")


if __name__ == "__main__":
    main()
