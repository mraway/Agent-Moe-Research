"""Counterfactual layer-band / component variants suggested by the per-layer timing.

Diagnostic only: these bands are chosen after seeing the per-layer crossing times of the
two frozen candidates, so nothing here is validated -- it is evidence about *where* the
timing signal lives, to be preregistered (or not) for B3.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Sequence

import torch

from timing_lib import (
    CandA,
    CandB,
    evaluate_stat,
    headline,
    load_cases,
    routine_traces,
)
from research_v2.scorers.wgm import WGMScorer

OUT = Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/research_v2/zoom/timing")


class CandAband(CandA):
    def __init__(self, window_width: int, layers: Sequence[int]) -> None:
        self.window_width = window_width
        self.layers = tuple(layers)
        self.scorer = WGMScorer(window_width=window_width, layers=self.layers, metric="g1")
        self.key = f"WGM-g1 L{self.layers[0]}-{self.layers[-1]} w{window_width}"


class CandBparts(CandB):
    """d1 depth chain restricted to a subset of its per-layer surprisal parts."""

    def __init__(self, window_width: int, parts: Sequence[int], label: str) -> None:
        super().__init__(window_width=window_width)
        self.parts_index = tuple(parts)
        self.key = label

    def stream(self, trace):
        per, ends = self.layer_stream(trace)
        if not ends.numel():
            return torch.empty(0, dtype=torch.float64), ends
        return per[list(self.parts_index)].sum(0), ends


def run(case, cand, tag, out):
    fit_pool = routine_traces(case.fit_traces, "cb")
    cand.fit(fit_pool)
    raw = {t.trace_id: cand.stream(t) for t in case.target_traces}
    tr = [t for t in routine_traces(case.target_traces, "cb") if raw[t.trace_id][1].numel()]
    for reading in ("persist2", "max"):
        rows, _ = evaluate_stat(case, tr, raw, reading_name=reading)
        h = headline(rows)
        h["latency_iqr"] = list(h["latency_iqr"])
        h.pop("latencies")
        out[f"{tag}|{reading}"] = h


def main() -> None:
    t0 = time.time()
    _, cases = load_cases()
    result: dict[str, Any] = {}
    for case in cases:
        out: dict[str, Any] = {}
        for w in (4, 8):
            run(case, CandAband(w, range(12, 16)), f"A-late(L12-15) w{w}", out)
            run(case, CandAband(w, range(11, 16)), f"A-late(L11-15) w{w}", out)
        run(case, CandAband(8, range(5, 12)), "A-mid(L5-11) w8", out)
        for w in (1, 4):
            run(case, CandBparts(w, [0], f"B-init@L5 w{w}"), f"B-init@L5 w{w}", out)
            run(case, CandBparts(w, list(range(1, 7)), f"B-trans w{w}"), f"B-trans w{w}", out)
        result[case.name] = out
        print(case.name, "done", f"{time.time()-t0:.1f}s", flush=True)
    result["wall_clock_seconds"] = time.time() - t0
    (OUT / "layerband_cf.json").write_text(json.dumps(result, indent=1))
    print("wrote", OUT / "layerband_cf.json")


if __name__ == "__main__":
    main()
