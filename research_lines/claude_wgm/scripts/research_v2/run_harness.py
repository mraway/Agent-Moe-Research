#!/usr/bin/env python3
"""CLI for the research-v2 shared evaluation harness (protocol v2, spec section 1).

Example
-------
    PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python scripts/research_v2/run_harness.py \
        --scorer g1_whitened_distance --run-name g1_primary \
        --splits S1 --modes D,T --alphas 0.10 --routine cb --windows 8,16
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
from research_v2.scorers import available  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scorer", required=True, help=f"registered scorer; available: {available()}")
    parser.add_argument("--config", type=Path, default=None, help="JSON file with the scorer config")
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--splits", default="S1")
    parser.add_argument("--modes", default="D")
    parser.add_argument("--alphas", default="0.10")
    parser.add_argument("--routine", default="cb", help="cb | all_normal (comma separated for both)")
    parser.add_argument("--windows", default="8")
    parser.add_argument("--readings", default=None, help="comma separated; default = all of spec 1.5")
    parser.add_argument("--comparison", default="ge", choices=("ge", "gt"))
    parser.add_argument("--pooling", default="disjoint", choices=("disjoint", "pilot"))
    parser.add_argument("--bucket-cap", type=int, default=None)
    parser.add_argument("--bucket-min-criterion", default="traces", choices=("traces", "windows"))
    parser.add_argument("--bootstrap-draws", type=int, default=1000)
    parser.add_argument("--b1-present-calibration", action="store_true")
    parser.add_argument("--no-q1", action="store_true")
    parser.add_argument("--no-audit", action="store_true")
    parser.add_argument("--no-streams", action="store_true")
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
        routines=tuple(value.strip() for value in args.routine.split(",") if value.strip()),
        splits=tuple(value.strip() for value in args.splits.split(",") if value.strip()),
        modes=tuple(value.strip() for value in args.modes.split(",") if value.strip()),
        alphas=tuple(float(value) for value in args.alphas.split(",") if value),
        readings=tuple(value.strip() for value in args.readings.split(",")) if args.readings else None,
        comparison=args.comparison,
        pooling=args.pooling,
        bucket_cap=args.bucket_cap,
        bucket_min_criterion=args.bucket_min_criterion,
        bootstrap_draws=args.bootstrap_draws,
        b1_present_calibration=args.b1_present_calibration,
        emit_q1=not args.no_q1,
        emit_audit=not args.no_audit,
        store_score_streams=not args.no_streams,
    )
    start = time.time()
    batches = rio.load_core()
    print(f"loaded b1={len(batches['b1'])} b2={len(batches['b2'])} traces", flush=True)
    result = run_harness(config, batches=batches, progress=lambda text: print(f"  {text}", flush=True))
    result["run_name"] = args.run_name
    result["wall_clock_seconds"] = round(time.time() - start, 2)
    manifest = write_result(result, args.output_root / args.run_name)
    print(json.dumps({"run_name": args.run_name, **manifest}, indent=2), flush=True)


if __name__ == "__main__":
    main()
