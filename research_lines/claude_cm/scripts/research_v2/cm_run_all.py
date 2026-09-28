#!/usr/bin/env python3
"""Run every preregistered CM candidate (docs/research_v2/cm_prereg.md section 3).

All outputs go to ``artifacts/agent_v2/research_v2/cm/<run-name>/``; no existing
result directory is touched.  Main-table runs use ``--b1-present-calibration``
(spec 1.6); the ``*_nb1p`` runs are the preregistered sensitivity without it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = "/home/wzh/Agent-Moe-Research/.venv/bin/python"
HARNESS = ROOT / "scripts" / "research_v2" / "run_harness.py"
CONFIGS = ROOT / "scripts" / "research_v2" / "cm_configs"
OUTPUT_ROOT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "cm"

FULL_S1 = ["--splits", "S1", "--modes", "D,T", "--alphas", "0.05,0.10", "--bootstrap-draws", "500"]

# (run_name, scorer, config file or None, extra args)
RUNS: list[tuple[str, str, str | None, list[str]]] = [
    # --- candidates 1, 9, 10, 11 (window and routine-definition axes) ---------
    ("cm_c1_middle", "cm", "c1_middle.json", FULL_S1 + ["--routine", "cb,all_normal", "--windows", "4,8,16"]),
    # --- candidates 2, 3, 4, 12 ----------------------------------------------
    ("cm_c1c2_middle", "cm", "c1c2_middle.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_c1_all", "cm", "c1_all.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_c1c2_all", "cm", "c1c2_all.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_c1c2_early", "cm", "c1c2_early.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    # --- candidates 5, 6, 7, 8 (C3) ------------------------------------------
    ("cm_c1_middle_c3", "cm", "c1_middle_c3.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_c1c2_middle_c3", "cm", "c1c2_middle_c3.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_c1_all_c3", "cm", "c1_all_c3.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_c1c2_all_c3", "cm", "c1c2_all_c3.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    # --- criterion-required diagnostics (NOT candidates) ----------------------
    ("cm_cov0", "cm", "c1_middle_cov0.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_x_knn", "cm_x_knn", None, FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_c1c2_late", "cm", "c1c2_late.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    # --- comparison rows under the identical switch ---------------------------
    ("cm_ref_g1", "g1_whitened_distance", None, FULL_S1 + ["--routine", "cb,all_normal", "--windows", "8", "--no-streams"]),
    ("cm_ref_t2", "oov_fraction", None, FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_ref_t1full", "t1_embedding_knn", "t1_full.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_ref_s0", "s0_diffmeans", None, FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
]

# preregistered sensitivity: identical runs without the B1 brief=present pool
SENSITIVITY = [
    ("cm_c1_middle", "cm", "c1_middle.json", FULL_S1 + ["--routine", "cb,all_normal", "--windows", "4,8,16", "--no-streams"]),
    ("cm_c1c2_middle", "cm", "c1c2_middle.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_c1_all", "cm", "c1_all.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_c1c2_all", "cm", "c1c2_all.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_c1c2_early", "cm", "c1c2_early.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_c1_middle_c3", "cm", "c1_middle_c3.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_c1c2_middle_c3", "cm", "c1c2_middle_c3.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_c1_all_c3", "cm", "c1_all_c3.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_c1c2_all_c3", "cm", "c1c2_all_c3.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_cov0", "cm", "c1_middle_cov0.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_x_knn", "cm_x_knn", None, FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_ref_g1", "g1_whitened_distance", None, FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_ref_t2", "oov_fraction", None, FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
    ("cm_ref_t1full", "t1_embedding_knn", "t1_full.json", FULL_S1 + ["--routine", "cb", "--windows", "8", "--no-streams"]),
]

# S2 / S3 for the primary configuration and candidate 2
SPLITS_23 = [
    (
        "cm_c1_middle_s2_s3",
        "cm",
        "c1_middle.json",
        ["--splits", "S2,S3", "--modes", "D,T", "--alphas", "0.10", "--routine", "cb", "--windows", "8",
         "--readings", "max,persist2,ewma01,cusum1", "--bootstrap-draws", "0", "--no-streams"],
    ),
    (
        "cm_c1c2_middle_s2_s3",
        "cm",
        "c1c2_middle.json",
        ["--splits", "S2,S3", "--modes", "D,T", "--alphas", "0.10", "--routine", "cb", "--windows", "8",
         "--readings", "max,persist2,ewma01,cusum1", "--bootstrap-draws", "0", "--no-streams"],
    ),
    (
        "cm_ref_g1_s2_s3",
        "g1_whitened_distance",
        None,
        ["--splits", "S2,S3", "--modes", "D,T", "--alphas", "0.10", "--routine", "cb", "--windows", "8",
         "--readings", "max,persist2,ewma01,cusum1", "--bootstrap-draws", "0", "--no-streams"],
    ),
]


def run_one(name: str, scorer: str, config: str | None, extra: list[str], b1_present: bool) -> dict:
    command = [PYTHON, str(HARNESS), "--scorer", scorer, "--run-name", name, "--output-root", str(OUTPUT_ROOT)]
    if config:
        command += ["--config", str(CONFIGS / config)]
    command += extra
    if b1_present:
        command += ["--b1-present-calibration"]
    start = time.time()
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode != 0:
        print(process.stdout[-4000:])
        print(process.stderr[-4000:], file=sys.stderr)
        raise SystemExit(f"run failed: {name}")
    payload = json.loads(process.stdout[process.stdout.index("{\n  \"run_name\"") :])
    return {
        "run_name": name,
        "command": " ".join(command),
        "seconds": round(time.time() - start, 2),
        "sha256": payload["sha256"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default=None, help="comma separated run names to re-run")
    args = parser.parse_args()
    selected = set(args.only.split(",")) if args.only else None
    records = []
    plan = (
        [(name, scorer, config, extra, True) for name, scorer, config, extra in RUNS]
        + [(f"{name}_nb1p", scorer, config, extra, False) for name, scorer, config, extra in SENSITIVITY]
        + [(name, scorer, config, extra, False) for name, scorer, config, extra in SPLITS_23]
    )
    for name, scorer, config, extra, b1_present in plan:
        if selected and name not in selected:
            continue
        print(f"=== {name}", flush=True)
        record = run_one(name, scorer, config, extra, b1_present)
        print(f"    {record['seconds']}s  result.json {record['sha256']['result.json']}", flush=True)
        records.append(record)
    manifest = OUTPUT_ROOT / "run_manifest.json"
    existing = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else []
    by_name = {row["run_name"]: row for row in existing}
    for record in records:
        by_name[record["run_name"]] = record
    manifest.write_text(json.dumps(sorted(by_name.values(), key=lambda row: row["run_name"]), indent=2), encoding="utf-8")
    print(f"wrote {manifest}")


if __name__ == "__main__":
    main()
