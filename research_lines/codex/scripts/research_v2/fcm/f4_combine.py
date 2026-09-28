#!/usr/bin/env python3
"""F4 = F1 OR F3, each at its own conformal threshold (prereg section 4).

The two parts have different scales, so the combination is done at the decision level:
the combined statistic is ``max_p (S_p - h_p)`` and the alarm rule is ``>= 0``.  Both
calibration modes are covered: mode D calibrates each part on the target-side routine half,
mode T on the source-side cross-fit maxima.  Scores are recomputed here (rather than read
from the stored streams) because mode T needs the source-side routine streams too.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    CANDIDATES,
    COMBINER,
    OUT,
    anchored_case,
    cases_for,
    product_onsets,
)
from research_v2 import io as rio  # noqa: E402
from research_v2.harness import (  # noqa: E402
    HarnessConfig,
    aggregate_rows,
    alarm_row,
    conformal_threshold,
    fit_bucket_stats,
    routine_traces,
    scenario_halves,
)
from research_v2.readings import build_readings  # noqa: E402
from research_v2.scorers import build  # noqa: E402

torch.set_num_threads(12)
PARTS = COMBINER["F4"]["parts"]
ALPHAS = (0.05, 0.10)
READINGS = ("max", "persist2")


def _streams(scorer, state, traces):
    out = {}
    for trace in traces:
        scores, ends = scorer.score(state, trace)
        out[trace.trace_id] = (scores.detach().to(torch.float64), ends.long())
    return out


def _bucket(config, streams):
    return fit_bucket_stats(
        streams,
        bucket_size=config.bucket_size,
        min_bucket_traces=config.min_bucket_traces,
        bucket_cap=config.bucket_cap,
        min_criterion=config.bucket_min_criterion,
    )


def main() -> None:
    config = HarnessConfig(scorer="fcm", scorer_config={})
    readings = {r.name: r for r in build_readings(READINGS)}
    batches = rio.load_core()
    onsets = product_onsets()
    cases = cases_for(batches)
    out: dict[str, dict] = {"metrics": {}, "detail": {}}

    for case_name, case in cases.items():
        fit_routine = routine_traces(case.fit_traces, "cb")
        source_routine = routine_traces(case.source_traces, "cb")
        target_routine = routine_traces(case.target_traces, "cb")
        halves = scenario_halves(case.target_traces)
        source_halves = scenario_halves(source_routine)

        parts = {}
        for key in PARTS:
            spec = CANDIDATES[key]
            scorer = build("fcm", dict(spec["config"], window_width=spec["window"]))
            state = scorer.fit(fit_routine)
            parts[key] = {
                "target": _streams(scorer, state, case.target_traces),
                "source": _streams(scorer, state, source_routine),
            }

        anchored = {"evidence": case, "product": anchored_case(case, onsets)}
        for reading_name in READINGS:
            reading = readings[reading_name]
            for alpha in ALPHAS:
                # ---- mode D ----
                combined: dict[str, tuple[torch.Tensor, torch.Tensor]] = {}
                thresholds: dict[str, dict[str, float]] = {}
                for cal_half in (0, 1):
                    calibration = [t for t in target_routine if halves[t.pair_group_id] == cal_half]
                    part_thresholds = {}
                    part_stats = {}
                    for key in PARTS:
                        streams = [parts[key]["target"][t.trace_id] for t in calibration]
                        streams = [s for s in streams if s[1].numel()]
                        stats = _bucket(config, streams)
                        maxima = [float(reading.apply(stats.standardize(s, e)).max()) for s, e in streams]
                        part_thresholds[key] = float(conformal_threshold(maxima, alpha)["threshold"])
                        part_stats[key] = stats
                    for trace in case.target_traces:
                        if halves[trace.pair_group_id] == cal_half:
                            continue
                        margins = []
                        ends_ref = None
                        for key in PARTS:
                            scores, ends = parts[key]["target"][trace.trace_id]
                            if not ends.numel():
                                margins = []
                                break
                            statistic = reading.apply(part_stats[key].standardize(scores, ends))
                            margins.append(statistic - part_thresholds[key])
                            ends_ref = ends
                        if not margins:
                            continue
                        size = min(m.numel() for m in margins)
                        stacked = torch.stack([m[:size] for m in margins])
                        combined[trace.trace_id] = (stacked.amax(dim=0), ends_ref[:size])
                        thresholds[trace.trace_id] = dict(part_thresholds, half=cal_half)

                # ---- mode T ----
                combined_t: dict[str, tuple[torch.Tensor, torch.Tensor]] = {}
                part_full = {}
                part_thr_t = {}
                for key in PARTS:
                    source_streams = [
                        parts[key]["source"][t.trace_id]
                        for t in source_routine
                        if parts[key]["source"][t.trace_id][1].numel()
                    ]
                    full = _bucket(config, source_streams)
                    half_stats = {}
                    for half in (0, 1):
                        subset = [
                            parts[key]["source"][t.trace_id]
                            for t in source_routine
                            if source_halves[t.pair_group_id] == half
                            and parts[key]["source"][t.trace_id][1].numel()
                        ]
                        half_stats[half] = _bucket(config, subset)
                    cross = []
                    for trace in source_routine:
                        scores, ends = parts[key]["source"][trace.trace_id]
                        if not ends.numel():
                            continue
                        other = 1 - source_halves[trace.pair_group_id]
                        cross.append(float(reading.apply(half_stats[other].standardize(scores, ends)).max()))
                    part_full[key] = full
                    part_thr_t[key] = float(conformal_threshold(cross, alpha)["threshold"])
                for trace in case.target_traces:
                    margins = []
                    ends_ref = None
                    for key in PARTS:
                        scores, ends = parts[key]["target"][trace.trace_id]
                        if not ends.numel():
                            margins = []
                            break
                        statistic = reading.apply(part_full[key].standardize(scores, ends))
                        margins.append(statistic - part_thr_t[key])
                        ends_ref = ends
                    if not margins:
                        continue
                    size = min(m.numel() for m in margins)
                    combined_t[trace.trace_id] = (
                        torch.stack([m[:size] for m in margins]).amax(dim=0),
                        ends_ref[:size],
                    )

                for mode, table in (("D", combined), ("T", combined_t)):
                    for anchor, acase in anchored.items():
                        rows = []
                        for trace in acase.target_traces:
                            if trace.trace_id not in table:
                                continue
                            statistic, ends = table[trace.trace_id]
                            rows.append(alarm_row(trace, ends, statistic, 0.0, comparison=config.comparison))
                        key = f"{case_name}|{mode}|{reading_name}|{alpha}|{anchor}"
                        out["metrics"][key] = aggregate_rows(rows)
                if reading_name == "persist2" and alpha == 0.05:
                    out["detail"][case_name] = {
                        trace_id: {
                            "ends": [int(v) for v in ends.tolist()],
                            "margin": [round(float(v), 4) for v in statistic.tolist()],
                            "thresholds": thresholds[trace_id],
                        }
                        for trace_id, (statistic, ends) in combined.items()
                    }
        print("done", case_name, flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "f4_combine.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("written", OUT / "f4_combine.json")


if __name__ == "__main__":
    main()
