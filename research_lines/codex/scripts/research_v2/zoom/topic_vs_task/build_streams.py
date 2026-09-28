"""Zoom audit: topic mention vs task execution.

Builds, for both frozen candidates and both S1 directions:
  * the frozen aggregate score stream (re-computed, verified against the frozen result.json),
  * a per-layer decomposition of the same score family over layers 5-15,
all fitted on the SOURCE batch routine (clean+benign) only.

Counterfactual diagnostic only; no new result is claimed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import torch

torch.set_num_threads(6)

import research_v2.io as rio
from research_v2.features import selection_rate_windows, window_means
from research_v2.harness import routine_traces, build_split_cases
from research_v2.scorers.wgm import WGMScorer
from research_v2.scorers.pdm import PdmScorer

OUT = Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/research_v2/zoom/topic_vs_task")
OUT.mkdir(parents=True, exist_ok=True)

BAND = tuple(range(5, 16))       # layers 5-15 for the per-layer diagnostic
MID = tuple(range(5, 12))
LATE = tuple(range(12, 16))


def wgm_per_layer(scorer: WGMScorer, state, trace):
    """G1 squared whitened distance split by layer (layers 5-15, 64 experts each)."""
    ends, windows = scorer._windows(trace)
    if not ends.numel():
        return ends, torch.empty((0, len(scorer.layers)))
    z = (windows - state.mu) / state.sd
    centred = (z - state.centre) ** 2
    per_layer = centred.reshape(centred.shape[0], len(scorer.layers), 64).sum(-1)
    return ends, per_layer


def pdm_per_layer_components(scorer: PdmScorer, state, top_k_ids):
    """D1 depth-chain surprisal split by depth step; component j attributed to layer layers[j]."""
    top1 = scorer._top1(top_k_ids)
    parts = [-state.log_depth_initial[top1[0]]]
    for index in range(top1.shape[0] - 1):
        parts.append(-state.log_depth[index][top1[index], top1[index + 1]])
    return torch.stack(parts, dim=1)  # [T, len(layers)]


def main() -> None:
    batches = rio.load_core()
    cases = build_split_cases(batches, "S1")
    payload = {}
    for case in cases:
        fit_routine = routine_traces(case.fit_traces, "cb")
        rec = {}

        # ---- CAND-A: wgm g1 middle_late w=8 -------------------------------
        a = WGMScorer(window_width=8, layers="middle_late", metric="g1")
        assert a.layers == BAND, a.layers
        sa = a.fit(fit_routine)
        # ---- CAND-B: pdm d1 middle w=4 ------------------------------------
        b = PdmScorer(window_width=4, model="d1", layers="middle")
        sb = b.fit(fit_routine)
        # ---- auxiliary wide D1 chain over 5-15 (diagnostic feature only) ---
        bw = PdmScorer(window_width=4, model="d1", layers=BAND)
        sbw = bw.fit(fit_routine)

        for trace in case.target_traces:
            ends_a, pl_a = wgm_per_layer(a, sa, trace)
            agg_a, ends_a2 = a.score(sa, trace)
            assert torch.equal(ends_a, ends_a2)
            # per-layer for pdm (own band 5-11) and wide band 5-15
            if trace.top_k_ids.shape[1] >= b.window_width:
                comp_b = pdm_per_layer_components(b, sb, trace.top_k_ids)
                ends_b, pl_b = window_means(comp_b, b.window_width)
                agg_b, ends_b2 = b.score(sb, trace)
                comp_bw = pdm_per_layer_components(bw, sbw, trace.top_k_ids)
                ends_bw, pl_bw = window_means(comp_bw, bw.window_width)
            else:
                ends_b = torch.empty(0, dtype=torch.long)
                pl_b = torch.empty((0, len(MID)))
                agg_b = torch.empty(0)
                pl_bw = torch.empty((0, len(BAND)))
            rec[trace.trace_id] = {
                "ends_a": ends_a.numpy().astype(np.int32),
                "agg_a": agg_a.numpy().astype(np.float32),
                "pl_a": pl_a.numpy().astype(np.float32),
                "ends_b": ends_b.numpy().astype(np.int32),
                "agg_b": np.asarray(agg_b, dtype=np.float32),
                "pl_b": pl_b.numpy().astype(np.float32),
                "pl_bw": pl_bw.numpy().astype(np.float32),
            }
        payload[case.name] = rec
        print(case.name, "fit routine", len(fit_routine), "target", len(case.target_traces), flush=True)

    flat = {}
    for case_name, rec in payload.items():
        for tid, blocks in rec.items():
            for key, arr in blocks.items():
                flat[f"{case_name}::{tid}::{key}"] = arr
    np.savez_compressed(OUT / "streams.npz", **flat)
    print("wrote", OUT / "streams.npz")


if __name__ == "__main__":
    main()
