#!/usr/bin/env python3
"""Sample-level audit (lens b) of the narrow-window round.

Read-only.  For each of the 14 anchored resisted traces and each
(candidate, window) at mode D / alpha 0.10 / readings max and persist2:

  * take the STORED trace_alarms first_alarm_end (authoritative harness decision);
  * classify the alarm window [end-w+1, end] as NONE / PRE_ONSET / ON_SPAN / AFTER_SPAN
    against the adjudicated [topic_entry_onset, topic_span_end];
  * decode the alarm-window tokens (plus context) from the trace itself;
  * refit the mode-D statistic from the stored score streams to measure whether the
    statistic RISES at the onset at all (peak in span / in the +16 horizon minus the
    pre-onset baseline, expressed in threshold units).

Also: every E0 (silent) resisted trace that alarms at w<=2 but not at the candidate's
frozen window, with the decoded text its first alarm sits on.

Usage:
  cd <worktree>
  PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python \
      scripts/research_v2/narrow_window/audit_sample_level_traces.py
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

from common import cases_for, load_result, streams_from_result  # noqa: E402
from research_v2 import io as rio  # noqa: E402
from research_v2.harness import (  # noqa: E402
    HarnessConfig,
    alarm_row,
    conformal_threshold,
    fit_bucket_stats,
    routine_traces,
    scenario_halves,
)
from research_v2.readings import build_readings  # noqa: E402

torch.set_num_threads(8)

LAB = ROOT / "docs" / "research_v2" / "labels"
NW = ROOT / "artifacts" / "agent_v2" / "research_v2" / "narrow_window"
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/sample_level.json")

RUNS = {
    "CAND-A": {"run_dir": NW / "wgm_c2_w1248", "windows": [1, 2, 4, 8], "frozen_window": 8},
    "CAND-B": {"run_dir": NW / "pdm_c12_w124", "windows": [1, 2, 4], "frozen_window": 4},
}
READINGS = ("max", "persist2")
ALPHA = 0.10


def read_jsonl(p: Path) -> dict[str, dict]:
    return {json.loads(l)["trace_id"]: json.loads(l)
            for l in p.read_text(encoding="utf-8").splitlines() if l.strip()}


def refit(case, streams, reading_name, alpha=ALPHA):
    """Exact mode-D reconstruction; returns per-trace detail dict."""
    config = HarnessConfig(scorer="x", scorer_config={})
    reading = {r.name: r for r in build_readings()}[reading_name]
    target_routine = routine_traces(case.target_traces, "cb")
    halves = scenario_halves(case.target_traces)
    detail = {}
    for cal_half in (0, 1):
        calibration = [t for t in target_routine if halves[t.pair_group_id] == cal_half]
        cal_streams = [streams[t.trace_id] for t in calibration]
        stats = fit_bucket_stats(
            cal_streams,
            bucket_size=config.bucket_size,
            min_bucket_traces=config.min_bucket_traces,
            bucket_cap=config.bucket_cap,
            min_criterion=config.bucket_min_criterion,
        )
        maxima = [float(reading.apply(stats.standardize(s, e)).max()) for s, e in cal_streams]
        threshold = float(conformal_threshold(maxima, alpha)["threshold"])
        for trace in case.target_traces:
            if halves[trace.pair_group_id] == cal_half:
                continue
            scores, ends = streams[trace.trace_id]
            if not ends.numel():
                continue
            z = stats.standardize(scores, ends)
            stat = reading.apply(z)
            row = alarm_row(trace, ends, stat, threshold, comparison="ge")
            detail[trace.trace_id] = {
                "stat": stat, "ends": ends, "threshold": threshold,
                "cal_half": cal_half,
                "refit_first_alarm_end": row["first_alarm_end"],
                "refit_alarm_ends": [int(e) for e in ends[stat >= threshold].tolist()],
            }
    return detail


def classify(first_end, onset, span_end, w):
    if first_end is None:
        return "NONE"
    if first_end < onset:
        return "PRE_ONSET"
    start = max(0, first_end - w + 1)
    if start <= span_end:
        return "ON_SPAN"
    return "AFTER_SPAN"


def main() -> None:
    resist = read_jsonl(LAB / "topic_entry_v1_adjudicated.jsonl")
    anchored = {tid: r for tid, r in resist.items() if r["topic_entry_onset"] is not None}
    e0 = {tid: r for tid, r in resist.items() if r["topic_entry_class"] == "E0"}
    assert len(anchored) == 14 and len(e0) == 47, (len(anchored), len(e0))

    batches = rio.load_core()
    cases = cases_for(batches)
    trace_lookup = {}
    for case in cases.values():
        for t in case.target_traces:
            trace_lookup[t.trace_id] = t

    texts = {}
    for tid in list(anchored) + list(e0):
        t = trace_lookup[tid]
        texts[tid] = rio.decode_token_texts(t.token_ids.tolist())

    out = {"anchored": {}, "e0_new": {}, "refit_disagreements": [], "meta": {}}
    for tid, lab in anchored.items():
        out["anchored"][tid] = {
            "pair_group_id": lab["pair_group_id"],
            "batch": lab["batch"],
            "class": lab["topic_entry_class"],
            "confidence": lab["confidence"],
            "onset": lab["topic_entry_onset"],
            "span_end": lab["topic_span_end"],
            "span_len": lab["topic_span_end"] - lab["topic_entry_onset"] + 1,
            "decode_len": len(texts[tid]),
            "decode_truncated": lab.get("decode_truncated"),
            "onset_text": lab.get("topic_entry_text"),
            "span_text": "".join(texts[tid][lab["topic_entry_onset"]: lab["topic_span_end"] + 1]),
            "cells": {},
        }

    for cand, spec in RUNS.items():
        res = load_result(spec["run_dir"] / "result.json")
        for cr in res["case_runs"]:
            w = cr["window_width"]
            case = cases[cr["case"]]
            streams = streams_from_result(cr)
            for reading in READINGS:
                cid = [c for c in cr["candidates"]
                       if c["mode"] == "D" and abs(c["alpha"] - ALPHA) < 1e-9
                       and c["reading"] == reading]
                assert len(cid) == 1, cid
                stored = {r[0]: r for r in cid[0]["trace_alarms"]}
                det = refit(case, streams, reading)
                for tid in stored:
                    if tid in det and det[tid]["refit_first_alarm_end"] != stored[tid][2]:
                        out["refit_disagreements"].append(
                            {"cand": cand, "case": cr["case"], "w": w, "reading": reading,
                             "trace": tid, "stored": stored[tid][2],
                             "refit": det[tid]["refit_first_alarm_end"]})
                key = f"{cand}|w{w}|{reading}"
                # anchored resisted traces
                for tid, lab in anchored.items():
                    if tid not in stored:
                        continue
                    onset, span_end = lab["topic_entry_onset"], lab["topic_span_end"]
                    first = stored[tid][2]
                    tk = texts[tid]
                    cls = classify(first, onset, span_end, w)
                    cell = {"first_alarm_end": first, "class": cls,
                            "onset_offset": None if first is None else first - onset,
                            "alarm_onset_count": stored[tid][4]}
                    if first is not None:
                        s = max(0, first - w + 1)
                        cell["alarm_window_tokens"] = [s, first]
                        cell["alarm_window_text"] = "".join(tk[s: first + 1])
                        cell["alarm_context_text"] = "".join(tk[max(0, s - 8): min(len(tk), first + 9)])
                    d = det.get(tid)
                    if d is not None:
                        stat, ends, thr = d["stat"], d["ends"], d["threshold"]
                        pre = stat[ends < onset]
                        inspan = stat[(ends >= onset) & (ends - w + 1 <= span_end)]
                        h16 = stat[(ends >= onset) & (ends <= onset + 16)]
                        post = stat[ends > span_end]
                        def f(x):
                            return None if x.numel() == 0 else float(x)
                        cell.update({
                            "threshold": thr,
                            "cal_half": d["cal_half"],
                            "pre_n": int(pre.numel()),
                            "pre_median": f(pre.median()) if pre.numel() else None,
                            "pre_max": f(pre.max()) if pre.numel() else None,
                            "peak_span": f(inspan.max()) if inspan.numel() else None,
                            "peak_h16": f(h16.max()) if h16.numel() else None,
                            "peak_post_span": f(post.max()) if post.numel() else None,
                            "trace_max": f(stat.max()) if stat.numel() else None,
                            "refit_first_alarm_end": d["refit_first_alarm_end"],
                            "refit_alarm_ends_head": d["refit_alarm_ends"][:12],
                        })
                        if inspan.numel() and pre.numel():
                            cell["rise_span_over_thr"] = (float(inspan.max()) - float(pre.median())) / thr
                        if h16.numel() and pre.numel():
                            cell["rise_h16_over_thr"] = (float(h16.max()) - float(pre.median())) / thr
                        if inspan.numel():
                            cell["peak_span_over_thr"] = float(inspan.max()) / thr
                        if h16.numel():
                            cell["peak_h16_over_thr"] = float(h16.max()) / thr
                    out["anchored"][tid]["cells"][key] = cell
                # E0 traces
                for tid in e0:
                    if tid not in stored:
                        continue
                    first = stored[tid][2]
                    rec = out["e0_new"].setdefault(tid, {"cells": {}, "decode_len": len(texts[tid]),
                                                         "decode_truncated": resist[tid].get("decode_truncated")})
                    entry = {"first_alarm_end": first}
                    if first is not None:
                        tk = texts[tid]
                        s = max(0, first - w + 1)
                        entry["alarm_window_text"] = "".join(tk[s: first + 1])
                        entry["alarm_context_text"] = "".join(tk[max(0, s - 8): min(len(tk), first + 9)])
                        entry["alarm_window_tokens"] = [s, first]
                    rec["cells"][key] = entry

    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    print("wrote", OUT, "disagreements", len(out["refit_disagreements"]))


if __name__ == "__main__":
    main()
