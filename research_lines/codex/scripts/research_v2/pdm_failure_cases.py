#!/usr/bin/env python3
"""PDM failure cases and model diagnostics (spec 7.3 items 9 and 11).

Rebuilds the primary configuration's mode-D calibration exactly as the harness does
(disjoint scenario halves, position-bucket standardization, conformal threshold) and dumps

* every missed drift trace (the strict onset anchor did not produce a clean hit), with the
  decoded text at the evidence onset and the trace's peak statistic relative to threshold;
* the highest-scoring non-drift traces, with the decoded text of the peak window;
* the unseen-transition diagnostics of spec 5.7 risk 2 (how often a scored transition was
  never observed in the routine counts), split by segment;
* the false-alarm rate stratified by decode length (spec 5.7 risk 3).

    .venv/bin/python scripts/research_v2/pdm_failure_cases.py
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io as rio  # noqa: E402
from research_v2.harness import (  # noqa: E402
    build_split_cases,
    code_commit,
    conformal_threshold,
    fit_bucket_stats,
    routine_traces,
    scenario_halves,
)
from research_v2.io import arm_class  # noqa: E402
from research_v2.readings import build_readings  # noqa: E402
from research_v2.scorers import build as build_scorer  # noqa: E402
from research_v2.scorers.pdm import EXPERTS  # noqa: E402


def unseen_fractions(scorer, state, trace) -> dict[str, float]:
    """Share of scored depth / time transitions whose routine count was zero."""

    top1 = scorer._top1(trace.top_k_ids)
    depth_zero = torch.zeros(top1.shape[1], dtype=torch.float64)
    time_zero = torch.zeros(top1.shape[1], dtype=torch.float64)
    depth_total = 0
    time_total = 0
    # the log tables were built with additive smoothing; the "unseen" cell is the minimum of
    # its row, which equals log(alpha / rowsum) -- recover it by comparing to that value
    if state.log_depth is not None:
        for index in range(top1.shape[0] - 1):
            table = state.log_depth[index]
            floor = table.min(dim=1, keepdim=True).values
            hit = table[top1[index], top1[index + 1]]
            depth_zero += (hit <= floor[top1[index]].squeeze(1) + 1e-12).double()
            depth_total += 1
    if state.log_time is not None:
        for index in range(top1.shape[0]):
            table = state.log_time[index]
            floor = table.min(dim=1, keepdim=True).values
            if top1.shape[1] > 1:
                hit = table[top1[index, :-1], top1[index, 1:]]
                time_zero[1:] += (hit <= floor[top1[index, :-1]].squeeze(1) + 1e-12).double()
            time_total += 1
    return {
        "depth": depth_zero / max(depth_total, 1),
        "time": time_zero / max(time_total, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="d1d2")
    parser.add_argument("--width", type=int, default=4)
    parser.add_argument("--routine", default="cb")
    parser.add_argument("--alpha", type=float, default=0.10)
    parser.add_argument("--reading", default="persist2")
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "research_v2" / "pdm_failure_cases",
    )
    args = parser.parse_args()
    torch.set_num_threads(args.threads)
    reading = build_readings((args.reading,))[0]

    batches = rio.load_core()
    cases = build_split_cases(batches, "S1")
    payload: dict[str, object] = {
        "spec": "docs/sequential_v2_lead_proposals.md section 7.3 items 9 and 11",
        "analysis_role": "post-hoc exploratory development on B1/B2 (not confirmation)",
        "code_commit": code_commit(),
        "datasets": rio.dataset_hashes(),
        "config": {
            "model": args.model,
            "window_width": args.width,
            "routine_definition": args.routine,
            "mode": "D",
            "alpha": args.alpha,
            "reading": args.reading,
        },
        "cases": [],
    }

    for case in cases:
        scorer = build_scorer(
            "pdm", {"model": args.model, "layers": "all", "window_width": args.width}
        )
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
        evaluated: dict[str, dict] = {}
        for cal_half in (0, 1):
            calibration = [t for t in target_routine if halves[t.pair_group_id] == cal_half]
            stats = fit_bucket_stats([streams[t.trace_id] for t in calibration])
            maxima = [
                float(reading.apply(stats.standardize(*streams[t.trace_id])).max())
                for t in calibration
            ]
            threshold = conformal_threshold(maxima, args.alpha)["threshold"]
            for trace in case.target_traces:
                if halves[trace.pair_group_id] == cal_half:
                    continue
                scores, ends = streams[trace.trace_id]
                if not ends.numel():
                    continue
                statistic = reading.apply(stats.standardize(scores, ends))
                finite = statistic[torch.isfinite(statistic)]
                peak = float(finite.max()) if finite.numel() else float("nan")
                peak_index = int(torch.argmax(torch.nan_to_num(statistic, neginf=-1e30)))
                alarm = statistic >= threshold
                alarm_ends = ends[alarm]
                evaluated[trace.trace_id] = {
                    "trace_id": trace.trace_id,
                    "arm_class": arm_class(trace),
                    "domain": trace.scenario_domain,
                    "workflow": trace.workflow,
                    "channel": trace.channel,
                    "token_count": trace.token_count,
                    "evidence_onset": trace.evidence_onset,
                    "threshold": threshold,
                    "peak_statistic": peak,
                    "peak_margin": peak - threshold,
                    "peak_end": int(ends[peak_index]),
                    "first_alarm_end": int(alarm_ends[0]) if alarm_ends.numel() else None,
                    "alarm_count": int(alarm_ends.numel()),
                }

        # missed drift traces under the strict onset anchor
        misses = []
        for trace in case.target_traces:
            if not trace.positive or trace.trace_id not in evaluated:
                continue
            row = dict(evaluated[trace.trace_id])
            onset = int(trace.evidence_onset)
            first = row["first_alarm_end"]
            pre_alarm = first is not None and first < onset
            hit = first is not None and not pre_alarm
            row["pre_onset_alarm"] = pre_alarm
            row["clean_hit"] = hit
            row["latency"] = None if not hit else max(0, first - onset)
            if hit and row["latency"] is not None and row["latency"] <= 16:
                continue
            ids = trace.token_ids.tolist()
            row["miss_kind"] = (
                "pre_onset_alarm" if pre_alarm else ("late" if hit else "no_alarm")
            )
            row["onset_text"] = rio.decode_text(ids[onset : min(len(ids), onset + 24)])
            row["peak_window_text"] = rio.decode_text(
                ids[max(0, row["peak_end"] - args.width + 1) : row["peak_end"] + 1]
            )
            misses.append(row)

        negatives = [
            row for row in evaluated.values() if row["arm_class"] in ("clean", "benign", "resist")
        ]
        negatives.sort(key=lambda row: -row["peak_margin"])
        top_negatives = []
        lookup = {t.trace_id: t for t in case.target_traces}
        for row in negatives[: args.top]:
            trace = lookup[row["trace_id"]]
            ids = trace.token_ids.tolist()
            item = dict(row)
            item["false_alarm"] = row["first_alarm_end"] is not None
            item["peak_window_text"] = rio.decode_text(
                ids[max(0, row["peak_end"] - args.width + 1) : row["peak_end"] + 1]
            )
            item["peak_context_text"] = rio.decode_text(
                ids[max(0, row["peak_end"] - 20) : min(len(ids), row["peak_end"] + 8)]
            )
            top_negatives.append(item)

        # unseen-transition diagnostics
        unseen = {"drift_post": [], "drift_pre": [], "routine": [], "resist": []}
        for trace in case.target_traces:
            fractions = unseen_fractions(scorer, state, trace)
            combined = 0.5 * (fractions["depth"] + fractions["time"])
            if trace.positive:
                onset = int(trace.evidence_onset)
                unseen["drift_post"].extend(combined[onset:].tolist())
                unseen["drift_pre"].extend(combined[:onset].tolist())
            elif arm_class(trace) == "resist":
                unseen["resist"].extend(combined.tolist())
            else:
                unseen["routine"].extend(combined.tolist())
        unseen_summary = {
            key: {
                "token_count": len(values),
                "mean": float(statistics.mean(values)) if values else None,
                "median": float(statistics.median(values)) if values else None,
            }
            for key, values in unseen.items()
        }

        # false-alarm rate by decode-length tertile
        lengths = sorted(row["token_count"] for row in negatives)
        cut_low = lengths[len(lengths) // 3]
        cut_high = lengths[(2 * len(lengths)) // 3]
        strata: dict[str, list[dict]] = {"short": [], "medium": [], "long": []}
        for row in negatives:
            key = (
                "short"
                if row["token_count"] <= cut_low
                else ("medium" if row["token_count"] <= cut_high else "long")
            )
            strata[key].append(row)
        far_by_length = {
            key: {
                "trace_count": len(rows),
                "token_range": [min((r["token_count"] for r in rows), default=None),
                                max((r["token_count"] for r in rows), default=None)],
                "false_alarm_rate": (
                    sum(1 for r in rows if r["first_alarm_end"] is not None) / len(rows)
                    if rows
                    else None
                ),
            }
            for key, rows in strata.items()
        }

        payload["cases"].append(
            {
                "case": case.name,
                "fit_routine_trace_count": len(routine_traces(case.fit_traces, args.routine)),
                "routine_token_count": state.token_count,
                "unseen_depth_cell_fraction": state.unseen_depth_fraction,
                "unseen_time_cell_fraction": state.unseen_time_fraction,
                "missed_drift": misses,
                "top_non_drift": top_negatives,
                "unseen_transition_by_segment": unseen_summary,
                "false_alarm_by_length": far_by_length,
            }
        )
        print(
            f"{case.name}: misses={len(misses)} "
            f"unseen(depth cells)={state.unseen_depth_fraction:.3f} "
            f"unseen(time cells)={state.unseen_time_fraction:.3f}",
            flush=True,
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / "failure_cases.json"
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print("sha256", rio.sha256(path), path)


if __name__ == "__main__":
    main()
