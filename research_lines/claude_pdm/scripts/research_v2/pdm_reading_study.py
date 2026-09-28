#!/usr/bin/env python3
"""PDM reading study (spec 5.3): cross-fitted run-length distribution by arm.

For each S1 direction and each window width, the target batch's scenarios are split into
two halves exactly as the harness mode-D ``disjoint`` pooling does.  With half ``h`` as the
calibration pool we

1. fit the position-bucket statistics on the calibration half's routine (cb) traces,
2. set the deviation threshold ``c`` = the 90th percentile of the *clean-arm* window ``z``
   values of that same half (the one-class, cross-fitted version of E13's clean-q90),
3. measure, for every trace whose scenario lies in the *other* half, the longest streak of
   consecutive windows with ``z >= c``, separately for the five segments
   drift-post / drift-pre / benign / resist / clean.

Nothing here touches a drift or resist trace during fitting or thresholding.

Usage
-----
    .venv/bin/python scripts/research_v2/pdm_reading_study.py \
        --model d1d2 --widths 1,4 --output-dir artifacts/agent_v2/research_v2/pdm_reading_study
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
from research_v2.harness import (  # noqa: E402
    build_split_cases,
    code_commit,
    fit_bucket_stats,
    routine_traces,
    scenario_halves,
)
from research_v2.io import arm_class  # noqa: E402
from research_v2.runlength import (  # noqa: E402
    SEGMENTS,
    RunLengthRow,
    deviation_threshold,
    longest_run,
    summarize,
    trace_segments,
)
from research_v2.scorers import build as build_scorer  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", default="d1d2")
    parser.add_argument("--layers", default="all")
    parser.add_argument("--widths", default="1,4")
    parser.add_argument("--routine", default="cb")
    parser.add_argument("--quantile", type=float, default=0.90)
    parser.add_argument("--min-run", type=int, default=8)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "research_v2" / "pdm_reading_study",
    )
    return parser.parse_args()


def main() -> None:
    args = _args()
    torch.set_num_threads(args.threads)
    widths = [int(value) for value in args.widths.split(",") if value]
    batches = rio.load_core()
    cases = build_split_cases(batches, "S1")

    payload: dict[str, object] = {
        "spec": "docs/sequential_v2_lead_proposals.md section 5.3 (reading study)",
        "analysis_role": "post-hoc exploratory development on B1/B2 (not confirmation)",
        "code_commit": code_commit(),
        "datasets": rio.dataset_hashes(),
        "config": {
            "model": args.model,
            "layers": args.layers,
            "widths": widths,
            "routine_definition": args.routine,
            "deviation_quantile": args.quantile,
            "min_run": args.min_run,
            "threshold_rule": "q90 of calibration-half clean-arm window z (cross-fitted)",
            "pooling": "disjoint (harness mode-D default)",
        },
        "cells": [],
    }

    for width in widths:
        scorer = build_scorer(
            "pdm",
            {"model": args.model, "layers": args.layers, "window_width": width},
        )
        for case in cases:
            state = scorer.fit(routine_traces(case.fit_traces, args.routine))
            streams = {}
            for trace in case.target_traces:
                scores, ends = scorer.score(state, trace)
                streams[trace.trace_id] = (scores.to(torch.float64), ends.long())
            target_routine = [
                t
                for t in routine_traces(case.target_traces, args.routine)
                if streams[t.trace_id][1].numel()
            ]
            halves = scenario_halves(case.target_traces)
            rows: list[RunLengthRow] = []
            half_diagnostics = []
            for cal_half in (0, 1):
                calibration = [t for t in target_routine if halves[t.pair_group_id] == cal_half]
                stats = fit_bucket_stats([streams[t.trace_id] for t in calibration])
                clean_z = [
                    stats.standardize(*streams[t.trace_id])
                    for t in calibration
                    if arm_class(t) == "clean"
                ]
                threshold = deviation_threshold(clean_z, args.quantile)
                half_diagnostics.append(
                    {
                        "calibration_half": cal_half,
                        "calibration_trace_count": len(calibration),
                        "clean_trace_count": len(clean_z),
                        "clean_window_count": sum(int(z.numel()) for z in clean_z),
                        "threshold_c": threshold,
                        "bucket_cap": stats.cap,
                    }
                )
                for trace in case.target_traces:
                    if halves[trace.pair_group_id] == cal_half:
                        continue
                    scores, ends = streams[trace.trace_id]
                    if not ends.numel():
                        continue
                    z = stats.standardize(scores, ends)
                    for segment, (segment_z, _) in trace_segments(
                        z,
                        ends,
                        arm_class=arm_class(trace),
                        evidence_onset=trace.evidence_onset,
                    ).items():
                        rows.append(
                            RunLengthRow(
                                trace_id=trace.trace_id,
                                segment=segment,
                                arm_class=arm_class(trace),
                                window_count=int(segment_z.numel()),
                                longest_run=longest_run(segment_z, threshold),
                                threshold=threshold,
                                calibration_half=cal_half,
                            )
                        )
            cell = {
                "case": case.name,
                "window_width": width,
                "model": args.model,
                "halves": half_diagnostics,
                "segments": {
                    segment: summarize(
                        [row for row in rows if row.segment == segment], min_run=args.min_run
                    )
                    for segment in SEGMENTS
                },
                "rows": [row.__dict__ for row in rows],
            }
            payload["cells"].append(cell)
            print(
                f"w={width} {case.name}: "
                + " ".join(
                    f"{segment}={cell['segments'][segment][f'fraction_ge_{args.min_run}']}"
                    for segment in SEGMENTS
                ),
                flush=True,
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    result_path = args.output_dir / "run_length.json"
    result_path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")

    lines = [
        "| model | dir | w | segment | traces | median | q75 | q90 | max | "
        f">= {args.min_run} | frac >= {args.min_run} |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for cell in payload["cells"]:
        for segment in SEGMENTS:
            block = cell["segments"][segment]
            lines.append(
                "| {model} | {case} | {w} | {seg} | {n} | {med} | {q75} | {q90} | {mx} | {ge} | {frac} |".format(
                    model=cell["model"],
                    case=cell["case"],
                    w=cell["window_width"],
                    seg=segment,
                    n=block["trace_count"],
                    med=block["median"],
                    q75=block["q75"],
                    q90=block["q90"],
                    mx=block["max"],
                    ge=block[f"count_ge_{args.min_run}"],
                    frac=(
                        f"{block[f'fraction_ge_{args.min_run}']:.3f}"
                        if block[f"fraction_ge_{args.min_run}"] is not None
                        else "-"
                    ),
                )
            )
    table_path = args.output_dir / "run_length.md"
    table_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {
        "files": {"run_length.json": str(result_path), "run_length.md": str(table_path)},
        "sha256": {
            "run_length.json": rio.sha256(result_path),
            "run_length.md": rio.sha256(table_path),
        },
    }
    (args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
