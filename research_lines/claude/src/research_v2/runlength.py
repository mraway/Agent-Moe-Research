"""Run-length statistics of a standardized score stream (spec 5.3, "read"-study).

E13 of the spec measured, with a supervised score, "the longest streak of consecutive
windows above the clean-arm q90".  This module is the one-class, cross-fitted recomputation
of that statistic: the deviation threshold ``c`` is a quantile of the *calibration-half*
clean-arm window ``z`` values, so it never touches the trace being measured, and never
touches a drift or resist trace.

Contributed to ``src/research_v2/`` so the other proposals can reuse it; ``harness.py`` is
not modified.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import torch

SEGMENTS = ("drift_post", "drift_pre", "benign", "resist", "clean")


def maximal_run_lengths(mask: torch.Tensor) -> list[int]:
    """Lengths of every maximal streak of ``True`` in a 1-D boolean tensor."""

    runs: list[int] = []
    current = 0
    for flag in mask.tolist():
        if flag:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return runs


def longest_run(z: torch.Tensor, threshold: float) -> int:
    if not z.numel():
        return 0
    runs = maximal_run_lengths(z >= threshold)
    return max(runs) if runs else 0


def quantile(values: Sequence[float], q: float) -> float:
    """Nearest-rank quantile (deterministic, no interpolation)."""

    ordered = sorted(values)
    if not ordered:
        raise ValueError("empty sample")
    index = max(0, min(len(ordered) - 1, int(round(q * (len(ordered) - 1)))))
    return float(ordered[index])


def deviation_threshold(z_streams: Iterable[torch.Tensor], q: float = 0.90) -> float:
    """``c`` = the q-quantile of all window z values pooled over the given streams."""

    values: list[float] = []
    for stream in z_streams:
        values.extend(float(v) for v in stream.tolist())
    return quantile(values, q)


@dataclass
class RunLengthRow:
    trace_id: str
    segment: str
    arm_class: str
    window_count: int
    longest_run: int
    threshold: float
    calibration_half: int


def trace_segments(
    z: torch.Tensor,
    ends: torch.Tensor,
    *,
    arm_class: str,
    evidence_onset: int | None,
) -> dict[str, tuple[torch.Tensor, torch.Tensor]]:
    """Split one trace's (z, ends) into the spec's five evaluation segments."""

    if arm_class == "drift":
        if evidence_onset is None:
            raise ValueError("drift trace without evidence onset")
        post = ends >= int(evidence_onset)
        return {
            "drift_post": (z[post], ends[post]),
            "drift_pre": (z[~post], ends[~post]),
        }
    return {arm_class: (z, ends)}


def summarize(rows: Sequence[RunLengthRow], min_run: int = 8) -> dict[str, Any]:
    """Median / q75 / q90 / max of the per-trace longest run, and the ``>= min_run`` share."""

    lengths = [row.longest_run for row in rows]
    if not lengths:
        return {
            "trace_count": 0,
            "median": None,
            "q75": None,
            "q90": None,
            "max": None,
            f"fraction_ge_{min_run}": None,
            f"count_ge_{min_run}": 0,
        }
    return {
        "trace_count": len(lengths),
        "median": float(statistics.median(lengths)),
        "q75": quantile([float(v) for v in lengths], 0.75),
        "q90": quantile([float(v) for v in lengths], 0.90),
        "max": int(max(lengths)),
        f"fraction_ge_{min_run}": sum(1 for v in lengths if v >= min_run) / len(lengths),
        f"count_ge_{min_run}": sum(1 for v in lengths if v >= min_run),
    }
