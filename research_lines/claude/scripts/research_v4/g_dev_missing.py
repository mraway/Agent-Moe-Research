#!/usr/bin/env python3
"""Report which (scenario, arm) pairs of a dataset G subset are still missing.

``run_agent_v3.py`` has no resume: it regenerates every (scenario, arm) it is
given and refuses to write into an existing output directory.  A crash therefore
leaves a partially filled run directory behind, and the only safe way to finish
the subset is to compute the difference between the frozen ``collection_plan``
and what is actually complete on disk, then run the remainder into a *new*
directory.

This script is that difference.  It

* reads the ``collection_plan`` of the subset config (the frozen list of
  (scenario, arm) pairs the subset is defined to contain),
* scans every run directory under ``--root`` that carries a
  ``resolved_experiment_config.json`` (skipping quarantine directories),
* treats ``trace.json`` with ``"complete": true`` as done,
* moves every incomplete trace directory, and every duplicate of an
  already-complete (scenario, arm) pair, into
  ``<root>/quarantine/<run>/<scenario>/<arm>``,
* prints the missing pairs grouped by the arm set that is missing for each
  scenario, as JSON lines, followed by a summary line.

The grouping is what the resume driver consumes: one arm set is one
``run_agent_v3.py --arms <arms> --scenario ... --scenario ...`` invocation.

Nothing here loads a model, a tokenizer or a routing tensor; only
``trace.json`` is opened, and only its ``complete`` flag is read.

The script is subset-agnostic.  ``--subset g_session`` is shorthand for the pair
``--config configs/dataset_g/g_session.json --root
artifacts/agent_v2/dataset_g/g_session``; either half can still be overridden
explicitly, and the default subset is ``g_dev``, so a call with neither flag
behaves exactly as it did when this script only knew about G-dev.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]

#: Directory names under ``--root`` that are never scanned as run directories.
QUARANTINE_DIR_NAMES = ("quarantine", "_quarantine")

#: The subset whose paths are used when neither ``--subset`` nor an explicit
#: ``--config`` / ``--root`` is given; keeps the original G-dev-only invocation
#: working unchanged.
DEFAULT_SUBSET = "g_dev"


def subset_config_path(subset: str, root: Path = ROOT) -> Path:
    """``configs/dataset_g/<subset>.json``."""

    return root / "configs" / "dataset_g" / f"{subset}.json"


def subset_run_root(subset: str, root: Path = ROOT) -> Path:
    """``artifacts/agent_v2/dataset_g/<subset>``."""

    return root / "artifacts" / "agent_v2" / "dataset_g" / subset


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--subset",
        default=DEFAULT_SUBSET,
        help=(
            "dataset G subset name; shorthand for --config configs/dataset_g/"
            "<subset>.json --root artifacts/agent_v2/dataset_g/<subset> "
            f"(default: {DEFAULT_SUBSET})"
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="subset config carrying the frozen collection_plan (default: from --subset)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="directory holding the run directories of this subset (default: from --subset)",
    )
    parser.add_argument(
        "--quarantine-dir",
        type=Path,
        default=None,
        help="where to move incomplete/duplicate traces (default <root>/quarantine)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be quarantined without moving anything",
    )
    args = parser.parse_args()
    if args.config is None:
        args.config = subset_config_path(args.subset)
    if args.root is None:
        args.root = subset_run_root(args.subset)
    return args


def required_pairs(config: dict[str, Any]) -> dict[str, list[str]]:
    """Map scenario id -> the arms the frozen collection_plan requires for it."""

    required: dict[str, list[str]] = {}
    for group in config["collection_plan"]:
        arms = list(group["arms"])
        for scenario_id in group["scenario_ids"]:
            existing = required.setdefault(scenario_id, [])
            for arm in arms:
                if arm not in existing:
                    existing.append(arm)
    return required


def is_run_dir(path: Path) -> bool:
    return path.is_dir() and (path / "resolved_experiment_config.json").is_file()


def find_run_dirs(root: Path) -> list[Path]:
    """Every run directory under ``root``, sorted by name (originals first)."""

    if not root.is_dir():
        return []
    return sorted(
        (child for child in root.iterdir() if child.name not in QUARANTINE_DIR_NAMES and is_run_dir(child)),
        key=lambda path: path.name,
    )


def trace_is_complete(trace_dir: Path) -> bool:
    trace_path = trace_dir / "trace.json"
    if not trace_path.is_file():
        return False
    try:
        payload = json.loads(trace_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return False
    return payload.get("complete") is True


def scan(root: Path) -> tuple[dict[tuple[str, str], list[Path]], dict[tuple[str, str], list[Path]]]:
    """Return (complete, incomplete) maps from (scenario, arm) to trace dirs.

    Both values are lists because the same pair can appear in more than one run
    directory after a resume; the caller quarantines every entry past the first
    complete one.
    """

    complete: dict[tuple[str, str], list[Path]] = {}
    incomplete: dict[tuple[str, str], list[Path]] = {}
    for run_dir in find_run_dirs(root):
        for scenario_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
            for arm_dir in sorted(p for p in scenario_dir.iterdir() if p.is_dir()):
                if arm_dir.name == "steps":
                    continue
                if not (arm_dir / "trace.json").is_file() and not (arm_dir / "manifest.jsonl").is_file():
                    continue
                key = (scenario_dir.name, arm_dir.name)
                bucket = complete if trace_is_complete(arm_dir) else incomplete
                bucket.setdefault(key, []).append(arm_dir)
    return complete, incomplete


def _unique_destination(base: Path) -> Path:
    if not base.exists():
        return base
    index = 1
    while True:
        candidate = base.parent / f"{base.name}.{index}"
        if not candidate.exists():
            return candidate
        index += 1


def quarantine(trace_dir: Path, root: Path, quarantine_root: Path, *, dry_run: bool) -> Path:
    """Move ``<root>/<run>/<scenario>/<arm>`` to ``<quarantine>/<run>/<scenario>/<arm>``."""

    relative = trace_dir.relative_to(root)
    destination = _unique_destination(quarantine_root / relative)
    if not dry_run:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(trace_dir), str(destination))
    return destination


def plan(
    config: dict[str, Any],
    root: Path,
    *,
    quarantine_root: Path | None = None,
    dry_run: bool = False,
    subset: str | None = None,
) -> dict[str, Any]:
    """Quarantine the unusable trace dirs and return the missing-pair report."""

    quarantine_root = quarantine_root or (root / "quarantine")
    required = required_pairs(config)
    complete, incomplete = scan(root)

    moved: list[dict[str, str]] = []
    kept: dict[tuple[str, str], Path] = {}
    for key in sorted(complete):
        directories = complete[key]
        kept[key] = directories[0]
        for duplicate in directories[1:]:
            moved.append(
                {
                    "reason": "duplicate",
                    "scenario": key[0],
                    "arm": key[1],
                    "from": str(duplicate),
                    "to": str(quarantine(duplicate, root, quarantine_root, dry_run=dry_run)),
                    "kept": str(directories[0]),
                }
            )
    for key in sorted(incomplete):
        for directory in incomplete[key]:
            moved.append(
                {
                    "reason": "duplicate_incomplete" if key in kept else "incomplete",
                    "scenario": key[0],
                    "arm": key[1],
                    "from": str(directory),
                    "to": str(quarantine(directory, root, quarantine_root, dry_run=dry_run)),
                    **({"kept": str(kept[key])} if key in kept else {}),
                }
            )

    missing_by_scenario: dict[str, list[str]] = {}
    for scenario_id in sorted(required):
        absent = [arm for arm in required[scenario_id] if (scenario_id, arm) not in kept]
        if absent:
            missing_by_scenario[scenario_id] = absent

    arm_sets: dict[tuple[str, ...], list[str]] = {}
    for scenario_id, arms in missing_by_scenario.items():
        arm_sets.setdefault(tuple(arms), []).append(scenario_id)

    unexpected = sorted(
        f"{scenario_id}/{arm}"
        for scenario_id, arm in kept
        if arm not in required.get(scenario_id, [])
    )

    groups = [
        {
            "kind": "arm_set",
            "arms": list(arms),
            "scenario_count": len(scenarios),
            "trace_count": len(scenarios) * len(arms),
            "scenarios": sorted(scenarios),
        }
        for arms, scenarios in sorted(arm_sets.items())
    ]
    summary = {
        "kind": "summary",
        "subset": subset,
        "root": str(root),
        "run_dirs": [str(path) for path in find_run_dirs(root)],
        "required_trace_count": sum(len(arms) for arms in required.values()),
        "complete_trace_count": len(kept),
        "missing_trace_count": sum(len(arms) for arms in missing_by_scenario.values()),
        "missing_scenario_count": len(missing_by_scenario),
        "quarantined_count": len(moved),
        "quarantined": moved,
        "unexpected_pairs": unexpected,
        "dry_run": bool(dry_run),
    }
    return {"groups": groups, "summary": summary}


def main() -> int:
    args = _args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    report = plan(
        config,
        args.root.resolve(),
        quarantine_root=(args.quarantine_dir.resolve() if args.quarantine_dir else None),
        dry_run=args.dry_run,
        subset=args.subset,
    )
    for group in report["groups"]:
        print(json.dumps(group, ensure_ascii=False), flush=True)
    print(json.dumps(report["summary"], ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
