"""Shared loading / reconstruction helpers for the calibration-anatomy zoom audit.

Everything here is a *diagnostic* recomputation of the frozen mode-D calibration that the
harness already ran; no new detector is fitted and no threshold is selected.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import torch

torch.set_num_threads(6)

from research_v2 import io as rio
from research_v2.harness import (
    BucketStats,
    conformal_threshold,
    fit_bucket_stats,
    routine_traces,
    scenario_halves,
)
from research_v2.readings import build_readings

REPO = Path(__file__).resolve().parents[4]
ART = REPO / "artifacts" / "agent_v2" / "research_v2"
OUT = ART / "zoom" / "calibration_anatomy"

CANDIDATES = {
    "CAND-A": {
        "label": "wgm G1 whitened distance, layers 5-15, w=8",
        "result": ART / "wgm" / "c2_g1_middle_late" / "result.json",
        "window_width": 8,
    },
    "CAND-B": {
        "label": "pdm D1 depth-chain surprisal, layers 5-11, w=4",
        "result": ART / "pdm_d1_middle_s1" / "result.json",
        "window_width": 4,
    },
}

PERSIST2 = {r.name: r for r in build_readings()}["persist2"]
ALPHA = 0.10
BUCKET_SIZE = 32
MIN_BUCKET_TRACES = 30


def load_case_streams(result_path: Path, case: str, window_width: int):
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    for run in payload["case_runs"]:
        if run["case"] == case and int(run["window_width"]) == window_width:
            streams = {
                tid: (
                    torch.tensor(v["scores"], dtype=torch.float64),
                    torch.tensor(v["ends"], dtype=torch.long),
                )
                for tid, v in run["score_streams"].items()
            }
            return payload, run, streams
    raise KeyError(f"case {case} w={window_width} not in {result_path}")


def target_batch_of(case: str) -> str:
    return "b2" if case == "b1_to_b2" else "b1"


def build_stats(streams_list: Sequence[tuple[torch.Tensor, torch.Tensor]]) -> BucketStats:
    return fit_bucket_stats(
        streams_list,
        bucket_size=BUCKET_SIZE,
        min_bucket_traces=MIN_BUCKET_TRACES,
        bucket_cap=None,
        min_criterion="traces",
    )


def persist2_stream(stats: BucketStats, scores: torch.Tensor, ends: torch.Tensor):
    z = stats.standardize(scores, ends)
    return PERSIST2.apply(z), z


def trace_maximum(stats: BucketStats, scores: torch.Tensor, ends: torch.Tensor):
    stat, _ = persist2_stream(stats, scores, ends)
    if not stat.numel():
        return None, None
    idx = int(torch.argmax(stat))
    return float(stat[idx]), int(ends[idx])


def calibration_view(case: str, streams, traces, routine_def: str = "cb"):
    """Reproduce the harness mode-D halves exactly: returns per-half dicts."""
    halves = scenario_halves(traces)
    target_routine = [
        t for t in routine_traces(traces, routine_def) if streams[t.trace_id][1].numel()
    ]
    out = {}
    for half in (0, 1):
        cal = [t for t in target_routine if halves[t.pair_group_id] == half]
        cal_streams = [streams[t.trace_id] for t in cal]
        stats = build_stats(cal_streams)
        maxima = []
        for t in cal:
            m, e = trace_maximum(stats, *streams[t.trace_id])
            maxima.append({"trace": t, "max": m, "argmax_end": e})
        block = conformal_threshold([m["max"] for m in maxima], ALPHA)
        evaluated = [t for t in traces if halves[t.pair_group_id] != half]
        out[half] = {
            "stats": stats,
            "calibration": cal,
            "maxima": maxima,
            "threshold_block": block,
            "evaluated": evaluated,
            "halves": halves,
        }
    return out, halves, target_routine


def decode_window(trace, centre_end: int, radius: int = 32) -> str:
    ids = trace.token_ids.tolist()
    lo = max(0, centre_end - radius)
    hi = min(len(ids), centre_end + radius + 1)
    return rio.decode_text(ids[lo:hi])


def full_text(trace) -> str:
    return rio.decode_text(trace.token_ids.tolist())


def classify_output(text: str) -> str:
    """Coarse shape classes required by the audit brief."""
    stripped = text.strip()
    labels = []
    # protocol-shaped: JSON action envelope
    if stripped.startswith("{") or '"action"' in text or '"tool"' in text or "```json" in text:
        labels.append("protocol_json")
    # degenerate: long newline tail or heavy repetition
    tail = text[-200:]
    if tail.count("\n") > 40 or text.count("\n\n\n\n") > 2:
        labels.append("degenerate_newline")
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines:
        top = max(lines.count(l) for l in set(lines))
        if top >= 5 and len(lines) >= 8:
            labels.append("degenerate_repeat")
    low = text.lower()
    if any(k in low for k in ("i can't", "i cannot", "i'm not able", "i am not able",
                              "unable to help", "outside", "can't help with", "not able to assist",
                              "i'm sorry", "i am sorry", "cannot assist")):
        labels.append("refusal_like")
    if not labels:
        labels.append("ordinary_prose")
    return "+".join(labels)
