#!/usr/bin/env python3
"""Routine-only check: TRM-3 ``m_only`` vs the FROZEN harness mode-D reading.

Prereg v1.1 amendment 2 says the sequential reference set is the frozen mode-D
construction, so the ``m_only`` variant (channel M = frozen CAND-A: WGM g1, layers 5-15,
w = 8) must reproduce the frozen candidate
``S1:b1_to_b2|w8|routine=cb|mode=D|alpha=0.1|reading=max`` on the B2 routine traces.

This script compares, on the clean + benign_control arms of B2 only:

* the trace-level alarm SET (and each trace's first alarm end);
* the resulting false alarm rate (frozen: 17 / 160 = 0.10625);
* a ladder of from-scratch replicas of the frozen harness pipeline, each one turning on a
  single documented TRM-3 deviation, so the difference between the frozen alarm set and
  the ``m_only`` alarm set is ATTRIBUTED rather than merely counted.

Prereg v1.2 makes the ``m_only`` alarm set a **documented difference from the frozen
mode-D run, not an error**.  Two protocol decisions separate them, and this script prices
each one:

1. **bucket index** (prereg section 2, protocol decision 1, v1.2 section 12 item 9): TRM-3
   buckets on the endpoint ordinal ``k = end - (w - 1)``, the frozen harness on the token
   index ``end``.  Measured before the freeze: 16/160 vs 17/160 alarms, one set difference
   and seven +-1 first-alarm shifts.
2. **bucket source** (prereg v1.2 amendment 3): TRM-3 fits the position-bucket mu/sigma on
   the routine FITTING pool N_fit, the frozen harness on the calibration half itself.  The
   freeze review measured the in-sample variant at about +0.01 absolute FAR on 30k
   synthetic episodes; here it is priced on the real B2 routine traces.

The ladder is: frozen artifact -> harness replica (token buckets, half source, threshold
rule) -> the same with the TRM-3 p-value rule -> the same with the fit-pool bucket source
-> the ``m_only`` cell (k buckets, fit-pool source).  Every step changes exactly one thing.
Only step 1 is an integrity check that must reproduce the frozen artifact exactly; the
remaining steps are expected to differ and the script exits 0 when they do.

No attack / drift material is read: the frozen artifact's rows are filtered to the routine
arms before anything is compared, and no metric of a positive trace is computed or printed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "research_v3"))

from research_v2 import harness, scorers, trm3  # noqa: E402
import run_trm3  # noqa: E402

FROZEN = ROOT / "artifacts" / "agent_v2" / "research_v2" / "wgm" / "c2_g1_middle_late" / "result.json"
ALPHA = 0.10


def frozen_rows(case: str = "b1_to_b2", alpha: float = ALPHA) -> dict[str, dict[str, Any]]:
    payload = json.loads(FROZEN.read_text(encoding="utf-8"))
    run = next(r for r in payload["case_runs"] if r["case"] == case)
    candidate = next(
        c
        for c in run["candidates"]
        if c["mode"] == "D"
        and c["reading"] == "max"
        and float(c["alpha"]) == alpha
        and not c.get("reference_only")
    )
    rows = {}
    for trace_id, _weight, first_alarm_end, _band, onsets in candidate["trace_alarms"]:
        rows[str(trace_id)] = {
            "alarm": first_alarm_end is not None,
            "first_alarm_end": first_alarm_end,
            "onsets": onsets,
        }
    return rows


def trm3_alarms(routine_fit, calibration_pool, targets) -> tuple[dict[str, dict[str, Any]], Any]:
    config = trm3.config_for_variant("m_only")
    states = trm3.fit_channels(routine_fit, list(config.channels))
    streams = {trm3.trace_key(t): trm3.channel_streams(states, t) for t in calibration_pool}
    fit_streams = {trm3.trace_key(t): trm3.channel_streams(states, t) for t in routine_fit}
    calibration = trm3.calibrate(
        calibration_pool,
        states,
        config,
        # prereg v1.2 amendment 3: bucket mu/sigma from the FITTING pool
        fit_pool=routine_fit,
        fit_streams=fit_streams,
        pool="b2_routine",
        streams=streams,
    )
    result: dict[str, dict[str, Any]] = {}
    for trace in targets:
        half = calibration.select_half(trace)
        stream = streams.get(trm3.trace_key(trace)) or trm3.channel_streams(states, trace)
        outputs = trm3.online(stream, calibration, config, trace=trace, half=half)
        alarm_ends = [o.end for o in outputs if o.state == trm3.STATE_CONFIRMED]
        result[str(trace.trace_id)] = {
            "alarm": bool(alarm_ends),
            "first_alarm_end": alarm_ends[0] if alarm_ends else None,
            "onsets": sum(
                1
                for i, o in enumerate(outputs)
                if o.state == trm3.STATE_CONFIRMED
                and (i == 0 or outputs[i - 1].state != trm3.STATE_CONFIRMED)
            ),
            "half": half,
        }
    return result, calibration


def harness_replica(
    routine_fit,
    target_traces,
    routine_targets,
    *,
    rule: str = "threshold",
    bucket_source: str = "half",
) -> dict[str, dict[str, Any]]:
    """The frozen mode-D pipeline, rebuilt from the harness primitives (token buckets).

    ``rule="threshold"`` is the frozen reading (trace max ``>=`` the conformal threshold).
    ``rule="p_value"`` runs the TRM-3 sequential rule instead -- running max against the
    fixed full-path-maximum reference, alarm iff ``p <= alpha`` -- on exactly the same
    (token-bucket) standardized streams.  ``bucket_source="fit_pool"`` additionally fits
    the position buckets on the routine FITTING pool instead of on the calibration half
    (prereg v1.2 amendment 3).  Each argument isolates one documented deviation; what is
    then left between ``rule="p_value", bucket_source="fit_pool"`` and the ``m_only`` cell
    is the position-bucket INDEX alone (``k`` vs the token index ``end``).
    """

    scorer = scorers.build("wgm", dict(trm3.WGM_CONFIG))
    state = scorer.fit(routine_fit)
    streams = {}
    for trace in target_traces:
        scores, ends = scorer.score(state, trace)
        streams[trace.trace_id] = (scores, ends)
    fit_streams = [scorer.score(state, trace) for trace in routine_fit]
    fit_streams = [(scores, ends) for scores, ends in fit_streams]
    halves = harness.scenario_halves(target_traces)
    result: dict[str, dict[str, Any]] = {}
    for cal_half in (0, 1):
        calibration = [t for t in routine_targets if halves[t.pair_group_id] == cal_half]
        bucket_input = (
            fit_streams
            if bucket_source == "fit_pool"
            else [streams[t.trace_id] for t in calibration]
        )
        stats = harness.fit_bucket_stats(
            bucket_input,
            bucket_size=32,
            min_bucket_traces=30,
            bucket_cap=None,
            min_criterion="traces",
        )
        maxima = [
            float(stats.standardize(*streams[t.trace_id]).max())
            for t in calibration
            if streams[t.trace_id][1].numel()
        ]
        threshold = harness.conformal_threshold(maxima, ALPHA)["threshold"]
        reference = trm3.ChannelReference(
            stats=None,
            path_maxima=np.sort(np.array(maxima, dtype=np.float64)),
            lengths=np.array([], dtype=np.int64),
            window_z_sorted=np.array([], dtype=np.float64),
        )
        for trace in routine_targets:
            if halves[trace.pair_group_id] == cal_half:
                continue
            scores, ends = streams[trace.trace_id]
            if not ends.numel():
                continue
            z = stats.standardize(scores, ends)
            if rule == "p_value":
                running = torch.cummax(z, dim=0).values
                p = [reference.p_value(float(v)) for v in running]
                alarm_index = [i for i, value in enumerate(p) if value <= ALPHA + 1e-15]
                alarms = ends[alarm_index] if alarm_index else ends[:0]
            else:
                alarms = ends[z >= threshold]
            result[trace.trace_id] = {
                "alarm": bool(alarms.numel()),
                "first_alarm_end": int(alarms[0]) if alarms.numel() else None,
                "threshold": threshold,
                "max_z": float(z.max()),
                "half": cal_half,
            }
    return result


def compare(
    name: str,
    mine: dict[str, dict[str, Any]],
    frozen: dict[str, dict[str, Any]],
    ids,
    *,
    expected: str = "documented difference",
    reference_name: str = "frozen",
):
    """Alarm-set delta of one ladder step against the frozen run.

    ``expected`` says how the delta is to be read: ``"identical"`` for the pure harness
    replica (an integrity check -- any delta there is a real bug) and
    ``"documented difference"`` for every step that turns on a preregistered deviation.
    """

    mismatches = []
    for trace_id in sorted(ids):
        a = mine.get(trace_id, {})
        b = frozen.get(trace_id, {})
        if bool(a.get("alarm")) != bool(b.get("alarm")) or a.get("first_alarm_end") != b.get(
            "first_alarm_end"
        ):
            mismatches.append(
                {
                    "trace_id": trace_id,
                    "mine": {k: a.get(k) for k in ("alarm", "first_alarm_end", "half")},
                    reference_name: {k: b.get(k) for k in ("alarm", "first_alarm_end")},
                }
            )
    far = sum(1 for t in ids if mine.get(t, {}).get("alarm")) / len(ids)
    set_only = [
        row
        for row in mismatches
        if bool(row["mine"].get("alarm")) != bool(row[reference_name].get("alarm"))
    ]
    print(f"[{name}] FAR = {far:.5f} ({sum(1 for t in ids if mine.get(t, {}).get('alarm'))}/{len(ids)})")
    print(
        f"[{name}] vs {reference_name} ({expected}): {len(mismatches)} trace(s) differ, "
        f"{len(set_only)} of them in the alarm SET"
    )
    for row in mismatches:
        print("   ", json.dumps(row, sort_keys=True))
    return {
        "far": far,
        "reference": reference_name,
        "expected": expected,
        "difference_count": len(mismatches),
        "alarm_set_difference_count": len(set_only),
        "mismatch_count": len(mismatches),  # kept for readers of the v1.1 payload
        "mismatches": mismatches,
    }


def main() -> None:
    torch.set_num_threads(8)
    b1 = run_trm3.load_pool("b1")
    b2 = run_trm3.load_pool("b2")
    routine_fit = run_trm3.routine_only(b1)
    routine_b2 = run_trm3.routine_only(b2)
    ids = sorted(t.trace_id for t in routine_b2)
    frozen = frozen_rows()
    frozen_routine = {t: frozen[t] for t in ids if t in frozen}
    if len(frozen_routine) != len(ids):
        raise SystemExit("frozen artifact does not cover every B2 routine trace")
    frozen_far = sum(1 for t in ids if frozen_routine[t]["alarm"]) / len(ids)
    print(f"[frozen ] FAR = {frozen_far:.5f} over {len(ids)} clean+benign traces")

    halves_all = harness.scenario_halves(b2)
    halves_routine = harness.scenario_halves(routine_b2)
    print(
        "scenario halves identical (all-target vs routine-only): "
        f"{halves_all == halves_routine} ({len(halves_all)} scenarios)"
    )

    mine, calibration = trm3_alarms(routine_fit, routine_b2, routine_b2)
    # step 1: pure replica of the frozen pipeline -- the only step that must be identical
    replica = harness_replica(routine_fit, b2, routine_b2)
    replica_block = compare("replica", replica, frozen_routine, ids, expected="identical")
    # step 2: + the TRM-3 sequential p-value rule (token buckets, calibration-half source)
    pvalue = harness_replica(routine_fit, b2, routine_b2, rule="p_value")
    pvalue_block = compare("p<=a   ", pvalue, frozen_routine, ids)
    # step 3: + the v1.2 fit-pool bucket source (still token buckets)
    fit_pool = harness_replica(
        routine_fit, b2, routine_b2, rule="p_value", bucket_source="fit_pool"
    )
    fit_pool_block = compare("fitpool", fit_pool, frozen_routine, ids)
    # step 4: the m_only cell itself (k buckets + fit-pool source)
    trm3_block = compare("trm3   ", mine, frozen_routine, ids)
    if replica_block["difference_count"]:
        print(
            "WARNING: the harness replica no longer reproduces the frozen artifact; that "
            "step is an integrity check, not a documented difference"
        )
    bucket_index_only = compare(
        "k-index",
        mine,
        fit_pool,
        ids,
        expected="bucket index alone",
        reference_name="token_buckets_fit_pool",
    )

    buckets = {
        str(half): {
            name: {
                "bucket_cap": ref.stats.cap,
                "bucket_trace_counts": ref.stats.trace_counts,
                "bucket_window_counts": ref.stats.window_counts,
                "reference_trace_count": ref.n_reference,
                "threshold_alpha_0.10": ref.threshold(ALPHA),
            }
            for name, ref in block.channels.items()
        }
        for half, block in calibration.halves.items()
    }
    print(json.dumps({"buckets": buckets}, indent=1))
    payload = {
        "frozen_far": frozen_far,
        "status": (
            "documented difference (prereg v1.2 section 12 items 3 and 9), not an error"
        ),
        "ladder": [
            "frozen artifact",
            "harness replica (token buckets, half source, threshold rule) -- must match",
            "+ TRM-3 p-value rule",
            "+ fit-pool bucket source (v1.2 amendment 3)",
            "+ k-index buckets = the m_only cell (v1.2 section 12 item 9: k buckets kept)",
        ],
        "trm3_m_only": trm3_block,
        "harness_replica": replica_block,
        "trm3_rule_on_token_buckets": pvalue_block,
        "trm3_rule_on_token_buckets_fit_pool_source": fit_pool_block,
        "bucket_index_difference_only": bucket_index_only,
        "documented_differences": {
            "bucket_index": (
                "prereg section 2 buckets on the endpoint ordinal k = end - (w - 1); the "
                "frozen harness buckets on the token index end.  Kept as k (v1.2 section "
                "12 item 9)."
            ),
            "bucket_source": (
                "prereg v1.2 amendment 3 fits the position-bucket mu/sigma on the routine "
                "FITTING pool N_fit; the frozen harness fits them on the calibration half "
                "itself (in-sample, measured at about +0.01 absolute FAR)."
            ),
            "p_value_convention": (
                "the frozen reading alarms on max z >= threshold, TRM-3 on p <= alpha; the "
                "two differ only on an exact tie with a calibration maximum."
            ),
            "integrity_check": (
                "harness_replica must reproduce the frozen artifact exactly; every other "
                "row is expected to differ"
            ),
        },
        "halves_identical": halves_all == halves_routine,
        "buckets": buckets,
    }
    out = ROOT / "artifacts" / "agent_v2" / "research_v3" / "trm3" / "m_only_vs_frozen.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
