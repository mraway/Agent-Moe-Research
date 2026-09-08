"""Auxiliary counterfactual stream: CAND-A with sqrt(selection-rate) (Hellinger-like) features."""
from __future__ import annotations
from pathlib import Path
import numpy as np, torch
torch.set_num_threads(6)
import research_v2.io as rio
from research_v2.harness import routine_traces, build_split_cases
from research_v2.scorers.wgm import WGMScorer

OUT = Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/research_v2/zoom/topic_vs_task")

def main():
    batches = rio.load_core()
    flat = {}
    for case in build_split_cases(batches, "S1"):
        fit_routine = routine_traces(case.fit_traces, "cb")
        s = WGMScorer(window_width=8, layers="middle_late", metric="g1", transform="sqrt")
        st = s.fit(fit_routine)
        for trace in case.target_traces:
            sc, ends = s.score(st, trace)
            flat[f"{case.name}::{trace.trace_id}::agg_asqrt"] = sc.numpy().astype(np.float32)
        print(case.name, "done", flush=True)
    np.savez_compressed(OUT / "streams_sqrt.npz", **flat)

if __name__ == "__main__":
    main()
