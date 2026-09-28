"""Shared helpers for the missed-drift-anatomy zoom audit.

Reconstructs the exact harness mode-D decision (position-bucket z, conformal
threshold, persist2 reading) from a stored result.json score stream, so that
misses can be inspected without re-running any scorer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Sequence

import torch

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from research_v2 import io as rio  # noqa: E402
from research_v2.harness import (  # noqa: E402
    HarnessConfig,
    alarm_row,
    build_split_cases,
    fit_bucket_stats,
    routine_traces,
    scenario_halves,
)
from research_v2.readings import build_readings  # noqa: E402

ART = ROOT / "artifacts" / "agent_v2" / "research_v2"
OUT = ART / "zoom" / "missed_drift"


def load_result(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def streams_from_result(case_run: dict[str, Any]) -> dict[str, tuple[torch.Tensor, torch.Tensor]]:
    out = {}
    for tid, block in case_run["score_streams"].items():
        out[tid] = (
            torch.tensor(block["scores"], dtype=torch.float64),
            torch.tensor(block["ends"], dtype=torch.long),
        )
    return out


def mode_d_rows(
    case,
    streams: dict[str, tuple[torch.Tensor, torch.Tensor]],
    routine_def: str = "cb",
    reading_name: str = "persist2",
    alpha: float = 0.10,
    config: HarnessConfig | None = None,
):
    """Reproduce the harness mode-D disjoint decision; returns (rows, per_trace_z)."""

    config = config or HarnessConfig(scorer="x", scorer_config={})
    reading = {r.name: r for r in build_readings()}[reading_name]
    target_routine = routine_traces(case.target_traces, routine_def)
    halves = scenario_halves(case.target_traces)
    rows: list[dict[str, Any]] = []
    detail: dict[str, dict[str, Any]] = {}
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
        from research_v2.harness import conformal_threshold

        block = conformal_threshold(maxima, alpha)
        threshold = float(block["threshold"])
        evaluated = [t for t in case.target_traces if halves[t.pair_group_id] != cal_half]
        for trace in evaluated:
            scores, ends = streams[trace.trace_id]
            if not ends.numel():
                continue
            z = stats.standardize(scores, ends)
            stat = reading.apply(z)
            rows.append(alarm_row(trace, ends, stat, threshold, comparison=config.comparison))
            detail[trace.trace_id] = {
                "z": z,
                "stat": stat,
                "ends": ends,
                "threshold": threshold,
                "cal_half": cal_half,
                "bucket_stats": stats,
            }
    return rows, detail


def hit_within(row: dict[str, Any], horizon: int = 16) -> bool:
    block = row["anchors"]["onset_strict"]
    if block["pre_alarm"]:
        return False
    first = block["first_alarm_end"]
    return first is not None and first <= row["evidence_onset"] + horizon


def miss_class(row: dict[str, Any], horizon: int = 16) -> str:
    block = row["anchors"]["onset_strict"]
    if block["pre_alarm"]:
        return "pre_onset_disqualified"
    first = block["first_alarm_end"]
    if first is None:
        return "no_alarm"
    return "late_alarm"


_TOKENIZER = None


def token_texts(trace) -> list[str]:
    global _TOKENIZER
    if _TOKENIZER is None:
        _TOKENIZER = rio.load_tokenizer()
    out = []
    for tid in trace.token_ids.tolist():
        piece = _TOKENIZER.id_to_token(int(tid))
        out.append("" if piece is None else piece.replace("Ġ", " ").replace("Ċ", "\n"))
    return out


def snippet(trace, lo: int, hi: int) -> str:
    ids = trace.token_ids.tolist()
    lo = max(0, lo)
    hi = min(len(ids), hi)
    if lo >= hi:
        return ""
    global _TOKENIZER
    if _TOKENIZER is None:
        _TOKENIZER = rio.load_tokenizer()
    return _TOKENIZER.decode([int(v) for v in ids[lo:hi]])


def cases_for(batches, split: str = "S1"):
    return {c.name: c for c in build_split_cases(batches, split)}
