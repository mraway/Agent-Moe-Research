#!/usr/bin/env python3
"""Narrow entry point: frozen Q2 conditions, normal arms only, new roots only."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_astra_stage2_q2 import CONDITIONS, config_path, run_path, verify_lock  # noqa: E402


def runner_arguments(condition: str, validate_only: bool = False) -> list[str]:
    args = ["run_agent_v2.py", "--config", config_path(condition), "--output-dir", run_path(condition),
            "--local-files-only", "--arms", "clean", "benign_control"]
    if validate_only:
        args.append("--validate-only")
    return args


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--condition", choices=CONDITIONS, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    verify_lock()
    if not args.validate_only and (ROOT / run_path(args.condition)).exists():
        raise FileExistsError("Do not overwrite or resample an existing condition")
    import run_agent_v2
    sys.argv = runner_arguments(args.condition, args.validate_only)
    raise SystemExit(run_agent_v2.main())
