#!/usr/bin/env python3
"""Build the frozen dataset G configs, fixtures and manifest.

Writes, under ``configs/dataset_g/``:

===================================  ==========================================
``{g_fit,g_cal,g_dev,g_session,``    the six subset configs, in the frozen Agent
``  g_medium,g_conf}.json``          v3 experiment schema
``agent_{subset}.json``              per-subset agent definitions (the frozen
                                     system prompt and tool schemas, only the
                                     fixture paths differ)
``fixtures/kb_{subset}.json``        the merged knowledge base of that subset's
                                     merchants
``fixtures/records_{subset}.json``   the merged support records
``model_gpt_oss_20b_medium.json``    the G-medium model config (reasoning
                                     effort medium, everything else frozen)
``manifest.json``                    sha256 of every file above
===================================  ==========================================

The build is a pure function: every date, seed, marker and wording is a literal
or is derived from one, so re-running reproduces the files byte for byte.
No model is loaded and nothing is generated.

Usage::

    factory_build_dataset_g.py                # write everything
    factory_build_dataset_g.py --summary      # write, then print the allocation
    factory_build_dataset_g.py --dry-run      # build in memory, print counts
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agent_v3 import validate_agent_v3_experiment  # noqa: E402
from agent_v3.factory.build import OUTPUT_DIR, build_all, build_plans  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        plans = build_plans()
        for subset, plan in plans.items():
            print(
                f"{subset:10s} scenarios={len(plan.scenarios):4d} "
                f"traces={plan.trace_count:4d} arms={plan.arms}"
            )
        print(f"total traces: {sum(plan.trace_count for plan in plans.values())}")
        return 0

    result = build_all(args.output_dir)
    for subset, config in result["configs"].items():
        validate_agent_v3_experiment(config)
        allocation = config["allocation"]
        print(
            f"wrote {args.output_dir / (subset + '.json')} "
            f"({allocation['scenario_count']} scenarios, "
            f"{allocation['trace_count']} planned traces)"
        )
    manifest = result["manifest"]
    print(
        f"wrote {args.output_dir / 'manifest.json'} "
        f"({len(manifest['files'])} files, {manifest['totals']['scenarios']} scenarios, "
        f"{manifest['totals']['planned_traces']} planned traces)"
    )
    if args.summary:
        for subset, config in result["configs"].items():
            print(f"\n=== {subset} ===")
            print(json.dumps(config["allocation"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
