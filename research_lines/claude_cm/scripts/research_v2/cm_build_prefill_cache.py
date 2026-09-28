#!/usr/bin/env python3
"""Build the CM prefill-summary cache (spec 4.3, C3).

The frozen routing cache holds decode routing only, so the per-trace prefill
route-selection mean has to be extracted from the original prefill shards once.
Output: ``artifacts/agent_v2/research_v2/cm/_prefill_cache/<batch>/<trace_id>.safetensors``
holding a [16, 64] float32 mean top-8 selection rate.  Nothing else is written.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io as rio  # noqa: E402
from research_v2.scorers.cm import PREFILL_CACHE, _final_prefill_row, prefill_summary  # noqa: E402


def main() -> None:
    torch.set_num_threads(8)
    start = time.time()
    batches = rio.load_core()
    rows = []
    for batch, traces in batches.items():
        for index, trace in enumerate(traces, start=1):
            summary = prefill_summary(trace)
            manifest_row = _final_prefill_row(Path(trace.record.trace_dir))
            rows.append(
                {
                    "trace_id": trace.trace_id,
                    "batch": batch,
                    "prefill_step_index": manifest_row["step_index"],
                    "prefill_token_count": len(manifest_row["token_ids"]),
                    "selection_sum": round(float(summary.sum()), 4),
                }
            )
            if index % 60 == 0:
                print(f"{batch}: {index}/{len(traces)}", flush=True)
    report = {
        "cache_dir": str(PREFILL_CACHE),
        "trace_count": len(rows),
        "prefill_token_count_min": min(row["prefill_token_count"] for row in rows),
        "prefill_token_count_max": max(row["prefill_token_count"] for row in rows),
        "prefill_step_index_values": sorted({row["prefill_step_index"] for row in rows}),
        "selection_sum_min": min(row["selection_sum"] for row in rows),
        "selection_sum_max": max(row["selection_sum"] for row in rows),
        "wall_clock_seconds": round(time.time() - start, 2),
        "rows": rows,
    }
    out = PREFILL_CACHE / "manifest.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
