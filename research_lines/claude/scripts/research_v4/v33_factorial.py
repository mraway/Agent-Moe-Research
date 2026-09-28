#!/usr/bin/env python3
"""Drive the v3.3 G-dev factorial through the TWO-STAGE harness (DEVELOPMENT ONLY).

The grid of ``docs/research_v4/zoom_v32_improvement_space.md`` section 5.3, run as real
harness features rather than as post-hoc reconstructions:

    {S, Z1} x {H = 352, H = inf} x {debounce 1, 2} x {no strat, n_kb strat}

with ``P`` as the primary comparator and ``M`` / ``J`` as the secondary and S-J cells.

Structure.  The debounce changes no frozen THRESHOLD (the reference maxima and the channel
standardiser are untouched), but it does change the ``matched_alpha_inputs`` curve stage 1
freezes for the comparator -- freeze review S6 has stage 2 REPLAY that curve rather than
search it.  So stage 1 is run under the same ``--debounce`` as the stage 2 it serves, and
the grid is one manifest per (horizon, stratification, debounce).  ``comparison_anchored``
compares ``--statistic``'s HEAD against ``--compare-statistic``, so each stage-2
configuration is run twice -- head ``S`` and head ``Z1`` -- to get both Delta-vs-P columns.

    8 stage-1 manifests + 16 stage-2 runs.

Every run is ``--dev-smoke`` on the UNSEALED G-dev batch and writes under a NEW run root,
so no frozen artefact is touched.  Nothing here is preregistered and nothing here is
confirmatory (prereg v3.2 section 11.1).

    PYTHONPATH=$PWD/src:$PWD/scripts python scripts/research_v4/v33_factorial.py --run
    PYTHONPATH=$PWD/src:$PWD/scripts python scripts/research_v4/v33_factorial.py --plan
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "research_v4" / "run_detectors_g.py"
TARGET = "artifacts/agent_v2/dataset_g/g_dev"
LABELS = "artifacts/agent_v2/dataset_g/annotations/g_dev/final_unblinded.jsonl"
OUT_ROOT = ROOT / "artifacts" / "agent_v2" / "dataset_g" / "v3_3_dev"
SUBSET_CONFIG = "configs/dataset_g/g_dev.json"

#: every cell of every run: the two candidates, the comparator and the two Holm members
STATISTICS = ("S", "Z1", "P", "M", "J")

HORIZONS = {"h352": "352", "hinf": "inf"}
STRATA = {"nostrat": None, "nkb": "n_kb"}
DEBOUNCES = (1, 2)
HEADS = ("S", "Z1")


def common(extra: list[str]) -> list[str]:
    return [
        "--target", TARGET,
        "--target-labels", LABELS,
        "--view", "V1", "--tag-scope", "message",
        "--alpha", "0.10",
        "--window-s", "8", "--window-m", "8", "--window-p", "8", "--window-prob", "8",
        "--cal-from-target", "--cal-folds", "3", "--fold-key", "fixture_rank_mod",
        "--h-min-survivors", "90", "--bucket-size", "32", "--min-bucket-traces", "30",
        "--min-channel-windows", "30", "--min-channel-traces", "10",
        "--tolerance-bands", "0,4,5,8", "--temporal-d", "24", "--temporal-exit", "0.25",
        "--session-alpha", "0.10", "--session-turns", "4",
        "--tertile-cutpoints-from-target",
        "--require-quality-labels",
        "--attainability-floor", "1",
        "--alpha-extra", "0.02",
        "--rare-threshold", "0.02",
        "--bootstrap-replicates", "2000",
        "--outputs", "primary",
        "--output-root", str(OUT_ROOT),
        *extra,
    ]


def stage1_cmd(horizon: str, stratum: str, debounce: int) -> tuple[str, list[str]]:
    name = f"stage1_{horizon}_{stratum}_d{debounce}"
    extra = [
        "--stage", "calibrate", "--normal-only-smoke",
        "--statistic", ",".join(STATISTICS),
        "--force-h", HORIZONS[horizon],
        "--debounce", str(debounce),
        "--anchor", "e_view", "--hit-window", "anchor_plus_h", "--positives", "e_anchored",
        "--run-name", name,
        "--threshold-manifest", str(OUT_ROOT / name / "threshold_manifest.json"),
    ]
    if STRATA[stratum]:
        extra += ["--stratify-reference", STRATA[stratum],
                  "--stratify-config", SUBSET_CONFIG]
    return name, common(extra)


def stage2_cmd(horizon: str, stratum: str, debounce: int, head: str) -> tuple[str, list[str]]:
    manifest = OUT_ROOT / f"stage1_{horizon}_{stratum}_d{debounce}" / "threshold_manifest.json"
    name = f"stage2_{horizon}_{stratum}_d{debounce}_{head.lower()}"
    ordered = [head] + [s for s in STATISTICS if s != head]
    extra = [
        "--stage", "score", "--dev-smoke",
        "--statistic", ",".join(ordered), "--compare-statistic", "P",
        "--force-h", HORIZONS[horizon],
        "--debounce", str(debounce),
        "--anchor", "x", "--hit-window", "e_view_to_anchor_plus_h",
        "--positives", "injection_present",
        "--recall-horizons", "8,16,32,64,full",
        "--compare-horizons", "32,64",
        "--far-episode-census",
        "--threshold-manifest", str(manifest),
        "--run-name", name,
    ]
    if STRATA[stratum]:
        extra += ["--stratify-reference", STRATA[stratum],
                  "--stratify-config", SUBSET_CONFIG]
    return name, common(extra)


def plan() -> list[tuple[str, list[str]]]:
    jobs = [
        stage1_cmd(h, s, d) for h in HORIZONS for s in STRATA for d in DEBOUNCES
    ]
    jobs += [
        stage2_cmd(h, s, d, head)
        for h in HORIZONS
        for s in STRATA
        for d in DEBOUNCES
        for head in HEADS
    ]
    return jobs


def run(jobs, only: str | None) -> int:
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    log_dir = OUT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    failures = 0
    for name, argv in jobs:
        if only and only not in name:
            continue
        if (OUT_ROOT / name / "result.json").exists():
            print(f"[skip] {name} (already present)")
            continue
        started = time.time()
        print(f"[run ] {name} ...", flush=True)
        with (log_dir / f"{name}.log").open("w", encoding="utf-8") as handle:
            code = subprocess.run(
                [sys.executable, str(RUNNER), *argv],
                cwd=str(ROOT),
                stdout=handle,
                stderr=subprocess.STDOUT,
            ).returncode
        status = "ok" if code == 0 else f"FAILED ({code})"
        print(f"[{status}] {name} in {time.time() - started:.0f}s", flush=True)
        failures += int(code != 0)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", action="store_true", help="print the commands only")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--only", default=None, help="substring filter on the run name")
    args = parser.parse_args()
    jobs = plan()
    if args.plan or not args.run:
        for name, argv in jobs:
            if args.only and args.only not in name:
                continue
            print(f"# {name}\npython {RUNNER.relative_to(ROOT)} " + " ".join(argv) + "\n")
        print(f"# {len(jobs)} jobs")
        return 0
    return 1 if run(jobs, args.only) else 0


if __name__ == "__main__":
    raise SystemExit(main())
