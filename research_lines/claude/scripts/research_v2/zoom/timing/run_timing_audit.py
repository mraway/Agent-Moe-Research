"""TIMING ANATOMY OF HITS -- main diagnostic run.

Writes everything under artifacts/agent_v2/research_v2/zoom/timing/.
Nothing here is a new result: every table is a counterfactual re-scoring of the two frozen
candidates under the harness mode-D protocol (alpha = 0.10, persist2 unless stated).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import torch

from timing_lib import (  # noqa: E402
    ALPHA,
    CandA,
    CandB,
    Stream,
    aggregate_rows,
    alarm_row,
    block_subsample,
    build_readings,
    conformal_threshold,
    evaluate_stat,
    evaluate_vote,
    headline,
    iqr,
    load_cases,
    median_or_none,
    mode_d_halves,
    routine_traces,
    scenario_halves,
    snippet,
    token_texts,
)

OUT = Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/research_v2/zoom/timing")
OUT.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------


def latency_decomposition(case, target_routine, raw, cand, reading_name="persist2"):
    """Per-drift-trace timing anatomy under the frozen mode-D configuration."""
    reading = build_readings((reading_name,))[0]
    raw_reading = build_readings(("max",))[0]
    halves = scenario_halves(case.target_traces)
    records: list[dict[str, Any]] = []
    for ctx in mode_d_halves(case, target_routine, raw):
        maxima = [
            float(reading.apply(ctx.stats.standardize(s, e)).max())
            for s, e in ctx.cal_streams
            if e.numel()
        ]
        h = float(conformal_threshold(maxima, ALPHA)["threshold"])
        med = ctx.routine_median_z
        for trace in ctx.evaluated:
            if not trace.positive:
                continue
            s, e = raw[trace.trace_id]
            z = ctx.stats.standardize(s, e)
            stat = reading.apply(z)
            row = alarm_row(trace, e, stat, h, comparison="ge")
            block = row["anchors"]["onset_strict"]
            onset = trace.evidence_onset
            ends = e.tolist()
            zl = z.tolist()

            def first_end(mask) -> int | None:
                idx = [i for i, flag in enumerate(mask) if flag]
                return ends[idx[0]] if idx else None

            post = [i for i, end in enumerate(ends) if end >= onset]
            t_med = first_end([end >= onset and zl[i] >= med for i, end in enumerate(ends)])
            t_raw = None
            if t_med is not None:
                t_raw = first_end([end >= t_med and zl[i] >= h for i, end in enumerate(ends)])
            t_alarm = block["first_alarm_end"]
            texts = token_texts(trace)
            rec = {
                "candidate": cand.key,
                "case": case.name,
                "trace_id": trace.trace_id,
                "arm": trace.arm,
                "domain": trace.scenario_domain,
                "channel": trace.channel,
                "workflow": trace.workflow,
                "decode_len": trace.token_count,
                "onset": onset,
                "completion_boundary": trace.completion_boundary,
                "cal_half": ctx.cal_half,
                "threshold": h,
                "routine_median_z": med,
                "pre_onset_alarm": block["pre_alarm"],
                "hit": block["hit"],
                "latency": block["latency"],
                "alarm_end": t_alarm,
                "t_median_cross": t_med,
                "t_raw_cross": t_raw,
                "comp_rise": None if t_med is None else t_med - onset,
                "comp_margin": None if (t_med is None or t_raw is None) else t_raw - t_med,
                "comp_persist": None if (t_raw is None or t_alarm is None) else t_alarm - t_raw,
                "window_fill_at_alarm": (
                    None if t_alarm is None else max(0, min(cand.window_width, t_alarm - onset + 1))
                ),
                "window_fill_at_raw_cross": (
                    None if t_raw is None else max(0, min(cand.window_width, t_raw - onset + 1))
                ),
                "alarm_token": (
                    texts[t_alarm] if t_alarm is not None and t_alarm < len(texts) else None
                ),
                "alarm_snippet": (
                    None
                    if t_alarm is None
                    else snippet(trace, t_alarm - cand.window_width + 1, t_alarm + 3)
                ),
                "onset_snippet": snippet(trace, onset, onset + 12),
                "traj_ends": [end for end in ends if onset - 8 <= end <= onset + 16],
                "traj_z": [
                    round(zl[i], 3) for i, end in enumerate(ends) if onset - 8 <= end <= onset + 16
                ],
                "traj_stat": [
                    (None if stat[i].isinf() else round(float(stat[i]), 3))
                    for i, end in enumerate(ends)
                    if onset - 8 <= end <= onset + 16
                ],
                "z_at_onset": round(zl[post[0]], 3) if post else None,
            }
            records.append(rec)
    return records


def per_layer_crossings(case, target_routine, layer_raw, cand, layer_alpha=ALPHA):
    """First crossing of each layer's own cross-fitted tail, relative to onset."""
    halves = scenario_halves(case.target_traces)
    n_layers = next(iter(layer_raw.values()))[0].shape[0]
    names = cand.layer_names()
    records: list[dict[str, Any]] = []
    for cal_half in (0, 1):
        calibration = [t for t in target_routine if halves[t.pair_group_id] == cal_half]
        stats_l, quant_l = [], []
        for li in range(n_layers):
            streams = [(layer_raw[t.trace_id][0][li], layer_raw[t.trace_id][1]) for t in calibration]
            from timing_lib import BUCKET_KW, fit_bucket_stats

            st = fit_bucket_stats(streams, **BUCKET_KW)
            stats_l.append(st)
            maxima = [float(st.standardize(s, e).max()) for s, e in streams if e.numel()]
            quant_l.append(float(conformal_threshold(maxima, layer_alpha)["threshold"]))
        for trace in case.target_traces:
            if halves[trace.pair_group_id] == cal_half or not trace.positive:
                continue
            per, e = layer_raw[trace.trace_id]
            if not e.numel():
                continue
            onset = trace.evidence_onset
            ends = e.tolist()
            row = {
                "candidate": cand.key,
                "case": case.name,
                "trace_id": trace.trace_id,
                "onset": onset,
                "layers": {},
            }
            for li in range(n_layers):
                z = stats_l[li].standardize(per[li], e)
                q = quant_l[li]
                pre = any(z[i] >= q for i, end in enumerate(ends) if end < onset)
                first = None
                for i, end in enumerate(ends):
                    if end >= onset and float(z[i]) >= q:
                        first = end
                        break
                row["layers"][names[li]] = {
                    "pre_onset": bool(pre),
                    "first_offset": None if first is None else first - onset,
                }
            records.append(row)
    return records


def alpha_sweep(case, target_routine, raw, reading_name="persist2", alphas=None):
    alphas = alphas or [round(0.02 + 0.01 * i, 2) for i in range(29)]
    out = []
    for a in alphas:
        rows, _ = evaluate_stat(case, target_routine, raw, reading_name=reading_name, alpha=a)
        h = headline(rows)
        h["alpha"] = a
        h.pop("latencies")
        out.append(h)
    return out


# ---------------------------------------------------------------------------


def main() -> None:
    t0 = time.time()
    batches, cases = load_cases()
    result: dict[str, Any] = {"alpha": ALPHA, "reading": "persist2", "cases": {}}

    for case in cases:
        block: dict[str, Any] = {}
        for cand_cls, widths, primary_w in ((CandA, (1, 4, 8), 8), (CandB, (1, 4, 8), 4)):
            cand_block: dict[str, Any] = {"width_sweep": {}}
            key = cand_cls(primary_w).key
            fit_pool = routine_traces(case.fit_traces, "cb")

            # -- width sweep (harness-equivalent mode-D re-run) --------------
            for w in widths:
                cand = cand_cls(w)
                cand.fit(fit_pool)
                raw = {t.trace_id: cand.stream(t) for t in case.target_traces}
                tr = [t for t in routine_traces(case.target_traces, "cb") if raw[t.trace_id][1].numel()]
                for reading in ("persist2", "max"):
                    rows, det = evaluate_stat(case, tr, raw, reading_name=reading)
                    h = headline(rows)
                    h["latency_iqr"] = list(h["latency_iqr"])
                    h.pop("latencies")
                    cand_block["width_sweep"][f"w{w}|{reading}"] = h
                if w == primary_w:
                    cand_block["primary_detail"] = det
                    cand_block["timing"] = latency_decomposition(case, tr, raw, cand)
                    layer_raw = {t.trace_id: cand.layer_stream(t) for t in case.target_traces}
                    cand_block["layer_names"] = cand.layer_names()
                    cand_block["layer_crossings"] = per_layer_crossings(case, tr, layer_raw, cand)
                    # -- vote statistic ---------------------------------------
                    cand_block["vote"] = {}
                    for la in (0.10, 0.25, 0.50):
                        for reading in ("persist2", "max"):
                            vrows, vdet = evaluate_vote(
                                case, tr, layer_raw, layer_alpha=la, reading_name=reading
                            )
                            hv = headline(vrows)
                            hv["latency_iqr"] = list(hv["latency_iqr"])
                            hv.pop("latencies")
                            hv["k_thresholds"] = [vdet[k]["vote_threshold_k"] for k in ("0", "1")]
                            cand_block["vote"][f"layer_alpha{la}|{reading}"] = hv
                    # -- non-overlapping blocks -------------------------------
                    cand_block["blocks"] = {}
                    braw = block_subsample(raw, w)
                    btr = [t for t in tr if braw[t.trace_id][1].numel()]
                    for reading in ("persist2", "max"):
                        brows, _ = evaluate_stat(case, btr, braw, reading_name=reading)
                        hb = headline(brows)
                        hb["latency_iqr"] = list(hb["latency_iqr"])
                        hb.pop("latencies")
                        cand_block["blocks"][f"block_w{w}|{reading}"] = hb
                    # -- alpha sweep for matched-FAR comparisons --------------
                    cand_block["alpha_sweep"] = {
                        "persist2": alpha_sweep(case, tr, raw, "persist2"),
                        "max": alpha_sweep(case, tr, raw, "max"),
                    }
            block[key] = cand_block
        result["cases"][case.name] = block
        print(f"{case.name} done at {time.time()-t0:.1f}s", flush=True)

    result["wall_clock_seconds"] = time.time() - t0
    (OUT / "timing_audit.json").write_text(json.dumps(result, indent=1))
    print("wrote", OUT / "timing_audit.json", f"{time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
