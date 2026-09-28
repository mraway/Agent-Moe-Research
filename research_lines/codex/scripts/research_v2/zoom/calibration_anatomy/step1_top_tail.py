"""Step 1: who sets the alpha=0.10 conformal threshold in each mode-D calibration half."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    ALPHA, CANDIDATES, OUT, calibration_view, classify_output, decode_window,
    full_text, load_case_streams, rio, target_batch_of,
)

TOP_N = 8


def main() -> None:
    batches = rio.load_core()
    report: dict[str, dict] = {}
    for key, cfg in CANDIDATES.items():
        report[key] = {"label": cfg["label"], "directions": {}}
        for case in ("b1_to_b2", "b2_to_b1"):
            payload, run, streams = load_case_streams(cfg["result"], case, cfg["window_width"])
            traces = batches[target_batch_of(case)]
            view, halves, target_routine = calibration_view(case, streams, traces)
            dir_block = {"halves": {}}
            for half in (0, 1):
                blk = view[half]
                ordered = sorted(blk["maxima"], key=lambda r: -r["max"])
                rank_setter = blk["threshold_block"]["order_statistic_rank"]
                n = blk["threshold_block"]["trace_count"]
                setter_from_top = n - rank_setter + 1  # 1-based index from the top
                rows = []
                for idx, rec in enumerate(ordered[:TOP_N], start=1):
                    t = rec["trace"]
                    text = full_text(t)
                    rows.append({
                        "rank_from_top": idx,
                        "is_threshold_setter": idx == setter_from_top,
                        "trace_id": t.trace_id,
                        "arm": t.arm,
                        "domain": t.scenario_domain,
                        "channel": t.channel,
                        "workflow": t.workflow,
                        "workflow_family": t.workflow_family,
                        "decode_len": t.token_count,
                        "statistic_max": rec["max"],
                        "argmax_end": rec["argmax_end"],
                        "shape_class": classify_output(text),
                        "snippet": decode_window(t, rec["argmax_end"], 32),
                        "tail": text[-160:],
                    })
                dir_block["halves"][half] = {
                    "n_calibration": n,
                    "order_statistic_rank": rank_setter,
                    "setter_index_from_top": setter_from_top,
                    "threshold": blk["threshold_block"]["threshold"],
                    "bucket_cap": blk["stats"].cap,
                    "bucket_trace_counts": blk["stats"].trace_counts,
                    "all_maxima_sorted_desc": [r["max"] for r in ordered],
                    "top": rows,
                }
            report[key]["directions"][case] = dir_block
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "step1_top_tail.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    for key, block in report.items():
        for case, dblk in block["directions"].items():
            for half, hb in dblk["halves"].items():
                print(f"\n=== {key} {case} half{half} n={hb['n_calibration']} "
                      f"h={hb['threshold']:.3f} setter=top{hb['setter_index_from_top']} "
                      f"bucket_cap={hb['bucket_cap']} counts={hb['bucket_trace_counts']}")
                for r in hb["top"]:
                    mark = "*" if r["is_threshold_setter"] else " "
                    print(f" {mark}#{r['rank_from_top']} {r['statistic_max']:.3f} {r['trace_id']} "
                          f"{r['arm']:>14} {r['domain']:>18} {r['workflow']:>26} len={r['decode_len']} "
                          f"argmax_end={r['argmax_end']} [{r['shape_class']}]")
                    print(f"      snip: {r['snippet']!r}")


if __name__ == "__main__":
    main()
