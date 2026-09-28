#!/usr/bin/env python3
"""Acceptance (a): recompute the frozen P1 kNN aggregates with the research-v2 metric layer.

P1 (``artifacts/agent_v2/normal_manifold_p1_knn/result.json``) stored, per trace, the
persistence-2 score stream, its endpoints and the calibrated threshold.  Feeding those
streams into ``research_v2.harness.alarm_row`` / ``aggregate_rows`` with P1's strict ``>``
comparison must reproduce the numbers of ``docs/normal_manifold_p1_knn_report.md``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io as rio  # noqa: E402
from research_v2.harness import aggregate_rows, alarm_row  # noqa: E402

EXPECTED = {
    "b1_to_b2::selection_middle_late": {
        "non_drift_false_alarm_count": 7,
        "non_drift_trace_count": 205,
        "recall_plus_16_count": 7,
        "recall_final_count": 16,
        "pre_alarm_count": 6,
        "drift_trace_count": 35,
        "median_latency": 19.0,
    }
}


def recompute(result_path: Path, direction: str) -> dict:
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    block = payload["directions"][direction]
    threshold = float(block["calibration"]["threshold"])
    target_batch = block["target_batch"]
    traces = {trace.trace_id: trace for trace in rio.load_batch(target_batch)}
    rows = []
    for stored in block["trace_results"]:
        trace = traces[stored["trace_id"]]
        rows.append(
            alarm_row(
                trace,
                torch.tensor(stored["score_endpoints"], dtype=torch.long),
                torch.tensor(stored["scores"], dtype=torch.float64),
                threshold,
                comparison="gt",
            )
        )
    aggregate = aggregate_rows(rows)
    strict = aggregate["onset_strict"]
    return {
        "direction": direction,
        "threshold": threshold,
        "non_drift_false_alarm_count": aggregate["non_drift_false_alarm_count"],
        "non_drift_trace_count": aggregate["non_drift_trace_count"],
        "non_drift_false_alarm_rate": aggregate["non_drift_false_alarm_rate"],
        "drift_trace_count": aggregate["drift_trace_count"],
        "recall_plus_4_count": strict["recall_plus_4_count"],
        "recall_plus_8_count": strict["recall_plus_8_count"],
        "recall_plus_16_count": strict["recall_plus_16_count"],
        "recall_final_count": strict["recall_final_count"],
        "pre_alarm_count": strict["pre_alarm_count"],
        "pre_alarm_denominator": strict["pre_alarm_denominator"],
        "median_latency": strict["median_latency"],
        "by_arm": {
            name: (value["false_alarm_count"], value["trace_count"])
            for name, value in aggregate["by_arm"].items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--p1-result",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "normal_manifold_p1_knn" / "result.json",
    )
    parser.add_argument("--direction", default="b1_to_b2::selection_middle_late")
    args = parser.parse_args()
    torch.set_num_threads(8)
    computed = recompute(args.p1_result, args.direction)
    expected = EXPECTED.get(args.direction, {})
    mismatches = {
        key: (value, computed[key]) for key, value in expected.items() if computed[key] != value
    }
    print(json.dumps({"computed": computed, "expected": expected, "mismatches": mismatches}, indent=2))
    if mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
