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

    factory_build_dataset_g.py                     # write everything
    factory_build_dataset_g.py --summary           # write, then print the allocation
    factory_build_dataset_g.py --dry-run           # build in memory, print counts
    factory_build_dataset_g.py --subset-only g_conf2   # append-only: write ONLY that
                                                       # subset's files (+ the manifest)
                                                       # and PROVE every other file is
                                                       # already byte identical

``--subset-only`` is the append-only mode used to add G-conf-2 without touching a
frozen file (docs/research_v4/g_conf2_build_log.md).  Every file is still rendered
in memory; a file owned by another subset is compared against the bytes on disk and
the build aborts if they differ, so "nothing else changed" is checked, not assumed.
The manifest is always rewritten because it must name every file; with
``--subset-only`` it can only gain entries.
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
    parser.add_argument(
        "--subset-only",
        action="append",
        default=None,
        metavar="SUBSET",
        help="append-only mode: write only this subset's files (repeatable); every "
        "other generated file is verified byte for byte instead of being rewritten",
    )
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

    only = None if args.subset_only is None else tuple(args.subset_only)
    result = build_all(args.output_dir, only=only)
    written = set(result["written"])
    for subset, config in result["configs"].items():
        validate_agent_v3_experiment(config)
        allocation = config["allocation"]
        verb = "wrote" if f"{subset}.json" in written else "verified unchanged"
        print(
            f"{verb} {args.output_dir / (subset + '.json')} "
            f"({allocation['scenario_count']} scenarios, "
            f"{allocation['trace_count']} planned traces)"
        )
    if only is not None:
        print(
            f"append-only mode only={sorted(only)}: "
            f"{len(result['written'])} files written, "
            f"{len(result['verified_unchanged'])} files verified byte-identical"
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
