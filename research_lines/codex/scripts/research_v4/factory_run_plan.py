#!/usr/bin/env python3
"""Print the exact run_agent_v3.py invocations a dataset G subset needs.

A subset's ``collection_plan`` lists which arms are budgeted for which
scenarios: G-fit collects clean and benign_control only, the G-dev supplement
collects the attack arm only, and so on. Running a config without the plan would
collect arms the subset does not budget for, so this script turns the plan into
argv.

Usage::

    factory_run_plan.py g_dev                       # all groups
    factory_run_plan.py g_dev --group core_72_cells # one group
    factory_run_plan.py g_dev --shell               # copy-pasteable commands
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "configs" / "dataset_g"
RUNNER = "scripts/research_v4/run_agent_v3.py"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subset")
    parser.add_argument("--group")
    parser.add_argument("--config-dir", type=Path, default=CONFIG_DIR)
    parser.add_argument("--shell", action="store_true")
    args = parser.parse_args()

    path = args.config_dir / f"{args.subset}.json"
    if not path.exists():
        print(f"no such subset config: {path}", file=sys.stderr)
        return 2
    config = json.loads(path.read_text(encoding="utf-8"))
    relative = path.relative_to(ROOT).as_posix()

    for group in config["collection_plan"]:
        if args.group and group["group"] != args.group:
            continue
        argv = [
            "python",
            RUNNER,
            "--config",
            relative,
            "--output-dir",
            f"{config['suggested_output_root']}/{group['group']}",
            "--local-files-only",
            "--arms",
            *group["arms"],
        ]
        for scenario_id in group["scenario_ids"]:
            argv += ["--scenario", scenario_id]
        line = " ".join(shlex.quote(token) for token in argv)
        if args.shell:
            print(f"# {args.subset}/{group['group']}: {group['trace_count']} traces")
            print(line)
            print()
        else:
            print(json.dumps({"group": group["group"], "argv": argv}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
