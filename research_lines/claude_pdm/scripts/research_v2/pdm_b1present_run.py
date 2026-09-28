#!/usr/bin/env python3
"""B1 ``brief=present`` calibration sensitivity for PDM (spec 1.6).

Spec 1.6 lets the B2->B1 direction enlarge the deployment-side calibration pool with the
B1 ``response_brief_condition == present`` traces.  Two facts about that pool are only
visible in the frozen index:

* 3 of its 120 traces are drift (``goal_plan_deviation_started``) and must never calibrate;
* 37 more are resisted-attack traces, which belong to the ``all_normal`` routine definition
  but not to the ``cb`` (clean + benign_control) definition used by the main tables.

This runner therefore adds only the 40 clean + 40 benign_control brief=present traces, so
the enlarged pool keeps the ``cb`` definition (40 target + 40 extra per calibration half).

Usage
-----
    .venv/bin/python scripts/research_v2/pdm_b1present_run.py --config <scorer.json> \
        --run-name pdm_b1present_sens --windows 4
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io as rio  # noqa: E402
from research_v2.harness import HarnessConfig, run_harness, write_result  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scorer", default="pdm")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--windows", default="4")
    parser.add_argument("--alphas", default="0.05,0.10")
    parser.add_argument("--routine", default="cb")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "research_v2",
    )
    return parser.parse_args()


def main() -> None:
    args = _args()
    torch.set_num_threads(args.threads)
    scorer_config = json.loads(args.config.read_text(encoding="utf-8")) if args.config else {}
    config = HarnessConfig(
        scorer=args.scorer,
        scorer_config=scorer_config,
        windows=tuple(int(value) for value in args.windows.split(",") if value),
        routines=(args.routine,),
        splits=("S1",),
        modes=("D",),
        alphas=tuple(float(value) for value in args.alphas.split(",") if value),
        b1_present_calibration=True,
        bootstrap_draws=0,
        emit_q1=False,
        store_score_streams=False,
    )
    start = time.time()
    extra = rio.load_b1_present(arms=("clean", "benign_control"))
    print(f"extra calibration pool: {len(extra)} b1 brief=present traces", flush=True)
    batches = rio.load_core()
    result = run_harness(
        config,
        batches=batches,
        extra_calibration=extra,
        progress=lambda text: print(f"  {text}", flush=True),
    )
    result["run_name"] = args.run_name
    result["extra_calibration_pool"] = {
        "source": "b1 response_brief_condition == present",
        "arms": ["clean", "benign_control"],
        "trace_count": len(extra),
        "trace_ids": [trace.trace_id for trace in extra],
        "note": (
            "3 of the 120 brief=present traces are drift and are excluded by io.load_b1_present; "
            "37 resisted-attack traces are excluded here to keep the cb routine definition"
        ),
    }
    result["wall_clock_seconds"] = round(time.time() - start, 2)
    manifest = write_result(result, args.output_root / args.run_name)
    print(json.dumps({"run_name": args.run_name, **manifest}, indent=2), flush=True)


if __name__ == "__main__":
    main()
