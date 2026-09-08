#!/usr/bin/env python3
"""Run the preregistered FCM candidate grid (S1, modes D/T, alpha 0.05/0.10, max+persist2).

Every candidate is run twice on identical score streams: once with the frozen
``evidence_onset`` anchor and once with the ``product_onset`` anchor (the drift records'
onset field is substituted before the metrics are computed; fitting and calibration use
routine traces only and are therefore bit-identical between the two runs).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    CANDIDATES,
    OUT,
    SENSITIVITY,
    anchored_traces,
    product_onsets,
)
from research_v2 import io as rio  # noqa: E402
from research_v2.harness import HarnessConfig, run_harness, write_result  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default=None, help="comma separated candidate ids")
    parser.add_argument("--sensitivity", action="store_true", help="run the post-hoc sensitivity variants")
    parser.add_argument("--threads", type=int, default=12)
    args = parser.parse_args()
    torch.set_num_threads(args.threads)

    grid = dict(SENSITIVITY) if args.sensitivity else dict(CANDIDATES)
    selected = list(grid) if not args.only else [k.strip() for k in args.only.split(",")]
    batches = rio.load_core()
    onsets = product_onsets()
    anchored = {name: anchored_traces(traces, onsets) for name, traces in batches.items()}
    print(f"loaded b1={len(batches['b1'])} b2={len(batches['b2'])}", flush=True)

    summary = {}
    for key in selected:
        spec = grid[key]
        for anchor, pool in (("evidence", batches), ("product", anchored)):
            run_name = f"{spec['run']}__{anchor}"
            config = HarnessConfig(
                scorer="fcm",
                scorer_config=dict(spec["config"]),
                windows=(int(spec["window"]),),
                routines=("cb",),
                splits=("S1",),
                modes=("D", "T"),
                alphas=(0.05, 0.10),
                readings=("max", "persist2"),
                bootstrap_draws=0,
                emit_q1=False,
                emit_audit=False,
                emit_ranking=True,
                store_score_streams=True,
            )
            start = time.time()
            result = run_harness(config, batches=pool)
            result["run_name"] = run_name
            result["fcm_candidate"] = key
            result["fcm_anchor"] = anchor
            result["fcm_label"] = spec["label"]
            result["fcm_text_derived"] = spec["text_derived"]
            result["fcm_reference"] = spec["reference"]
            result["wall_clock_seconds"] = round(time.time() - start, 2)
            manifest = write_result(result, OUT / run_name)
            summary[run_name] = manifest["files"]["result.json"]
            print(f"{key} {anchor}: {time.time() - start:.1f}s -> {run_name}", flush=True)
    (OUT / ("run_index_sensitivity.json" if args.sensitivity else "run_index.json")).write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
