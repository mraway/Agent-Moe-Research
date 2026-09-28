#!/usr/bin/env python3
"""Freeze the TRM-3 horizon-384 label table (engagement + execution onsets).

Routing-blind by construction: every field here comes from the frozen sample index, the
frozen routing-blind engagement adjudications, and the decoded output token ids.  No
routing tensor value is read, and no detector score is computed, so this script is safe
to run before the TRM-3 freeze commit.

The loader ``research_v2.io.load_h384`` derives the same table on the fly and asserts it
against the per-trace ``engagement_adjudication.json`` written by
``scripts/apply_agent_v2_engagement_adjudications.py``.  This script exists to write that
table down once, with input hashes, as the reproducible asset required by preregistration
section 9.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import research_v2.io as rio  # noqa: E402

DEFAULT_OUTPUT = rio.RESEARCH_V3_LABEL_DIR / "h384_labels.json"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def build_table() -> dict[str, Any]:
    traces = rio.load_h384(verify_onsets=True)
    rows: list[dict[str, Any]] = []
    for trace in traces:
        rows.append(
            {
                "trace_id": trace.trace_id,
                "pair_group_id": trace.pair_group_id,
                "arm": trace.arm,
                "class": rio.arm_class(trace),
                "preregistered_fold": trace.fold,
                "workflow": trace.workflow,
                "attack_channel": trace.channel,
                "target_domain": trace.domain,
                "scenario_domain": trace.scenario_domain,
                "positive": trace.positive,
                "engagement_class": trace.labels.get("engagement_class"),
                "engagement_onset": trace.labels.get("engagement_onset"),
                "execution_onset": trace.labels.get("execution_onset"),
                "evidence_onset": trace.evidence_onset,
                "completion_boundary": trace.completion_boundary,
                "post192_class": trace.labels.get("post192_class"),
                "support_resumed": trace.labels.get("support_resumed"),
                "decode_token_count": trace.labels["decode_token_count"],
                "index_goal_plan_deviation_started": trace.labels.get(
                    "index_goal_plan_deviation_started"
                ),
                "behavior_label": trace.labels.get("behavior_label"),
                "stratum": trace.labels.get("stratum"),
            }
        )
    gaps = [
        row["execution_onset"] - row["engagement_onset"]
        for row in rows
        if row["positive"] and row["engagement_onset"] is not None
    ]
    flagged = [
        row["trace_id"]
        for row in rows
        if row["arm"] != rio.ATTACK_ARM and row["index_goal_plan_deviation_started"]
    ]
    return {
        "schema_version": 1,
        "pool": "h384",
        "trace_count": len(rows),
        "inputs": rio.research_v3_dataset_hashes(),
        "arm_counts": dict(sorted(Counter(row["arm"] for row in rows).items())),
        "class_counts": dict(sorted(Counter(row["class"] for row in rows).items())),
        "engagement_class_counts": dict(
            sorted(
                Counter(
                    row["engagement_class"] for row in rows if row["engagement_class"]
                ).items()
            )
        ),
        "post192_class_counts": dict(
            sorted(Counter(row["post192_class"] for row in rows if row["post192_class"]).items())
        ),
        "engagement_onset_verified_against_frozen_adjudications": True,
        "execution_minus_engagement_onset": {
            "count": len(gaps),
            "min": min(gaps) if gaps else None,
            "max": max(gaps) if gaps else None,
            "mean": (sum(gaps) / len(gaps)) if gaps else None,
        },
        "control_arm_traces_flagged_goal_plan_deviation": sorted(flagged),
        "rows": rows,
    }


def main() -> int:
    args = _args()
    table = build_table()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(table, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    summary = {key: value for key, value in table.items() if key != "rows"}
    summary["output"] = str(args.output)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
