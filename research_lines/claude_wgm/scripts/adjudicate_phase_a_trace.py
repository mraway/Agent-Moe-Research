#!/usr/bin/env python3
"""Attach a provenance-preserving free-text adjudication to a Phase A trace."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase_a import adjudicate_free_text  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace_dir", type=Path)
    parser.add_argument("--label", required=True, choices=("goal_deviation", "no_deviation"))
    parser.add_argument("--event-index", required=True, type=int)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--attacker-goal-achieved", action="store_true")
    args = parser.parse_args()
    result = adjudicate_free_text(
        args.trace_dir,
        label=args.label,
        event_index=args.event_index,
        evidence=args.evidence,
        reason=args.reason,
        reviewer=args.reviewer,
        attacker_goal_achieved=args.attacker_goal_achieved,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
