"""Aggregates over ``outputs.jsonl`` (the per-endpoint output contract).

Read-only: opens ONLY ``artifacts/agent_v2/research_v3/trm3/final_*/outputs.jsonl``
(the frozen runner writes the ``trm3`` variant there) and, for the per-trace class
and hit flags, the matching ``result.json``.  Nothing is re-scored.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts" / "agent_v2" / "research_v3" / "trm3"
CELLS = {
    "b1": "final_b1_both",
    "b2": "final_b2_both",
    "h384": "final_h384_both",
    "c1_heldout": "final_c1_heldout_C1",
}


def main() -> int:
    cell = sys.argv[1]
    result = json.loads((BASE / CELLS[cell] / "result.json").read_text())
    per_col_hits = {}
    per_col_pos = {}
    for col, cdata in result["columns"].items():
        b = cdata["variants"]["trm3"]["sets"]["target"]
        per_col_hits[col] = b.get("primary_event_hits_plus_8") or {}
        per_col_pos[col] = set((b.get("positive_set") or {}).get("anchored_keys") or [])

    # attribution over CONFIRMED endpoints, regime_flag over all endpoints
    attr = collections.defaultdict(collections.Counter)      # (col, group) -> channel
    first_attr = collections.defaultdict(collections.Counter)
    regime = collections.defaultdict(lambda: [0, 0])          # (col, group) -> [true, total]
    regime_trace = collections.defaultdict(lambda: [0, 0])    # per trace majority
    seen_first = set()
    trace_regime = collections.defaultdict(lambda: [0, 0])

    with (BASE / CELLS[cell] / "outputs.jsonl").open() as handle:
        for line in handle:
            row = json.loads(line)
            col = row["calibration_column"]
            klass = row["class"]
            key = row["key"]
            group = "positive" if key in per_col_pos.get(col, ()) else klass
            if row["state"] == "CONFIRMED":
                attr[(col, group)][row["attribution"]] += 1
                if (col, key) not in seen_first:
                    seen_first.add((col, key))
                    first_attr[(col, group)][row["attribution"]] += 1
            flag = row.get("regime_flag")
            if flag is not None:
                regime[(col, group)][1] += 1
                regime[(col, group)][0] += int(bool(flag))
                trace_regime[(col, key)][1] += 1
                trace_regime[(col, key)][0] += int(bool(flag))

    out = {
        "attribution_confirmed_endpoints": {
            f"{c}|{g}": dict(v) for (c, g), v in attr.items()
        },
        "attribution_first_confirmed": {
            f"{c}|{g}": dict(v) for (c, g), v in first_attr.items()
        },
        "regime_flag_endpoints": {
            f"{c}|{g}": {"true": v[0], "total": v[1], "rate": (v[0] / v[1] if v[1] else None)}
            for (c, g), v in regime.items()
        },
        "regime_flag_vs_miss": {},
    }
    for col, hits in per_col_hits.items():
        buckets = {True: [0, 0], False: [0, 0]}
        for key, hit in hits.items():
            tr = trace_regime.get((col, key))
            if tr is None or tr[1] == 0:
                continue
            rate = tr[0] / tr[1]
            buckets[bool(hit)][0] += rate
            buckets[bool(hit)][1] += 1
        out["regime_flag_vs_miss"][col] = {
            "hit_plus_8": {
                "traces": buckets[True][1],
                "mean_regime_rate": (buckets[True][0] / buckets[True][1]) if buckets[True][1] else None,
            },
            "missed_plus_8": {
                "traces": buckets[False][1],
                "mean_regime_rate": (buckets[False][0] / buckets[False][1]) if buckets[False][1] else None,
            },
        }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
