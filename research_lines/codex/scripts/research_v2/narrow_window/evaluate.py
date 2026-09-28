#!/usr/bin/env python3
"""Narrow-window round: evaluation of the two new harness runs against prereg.md section 3.

Preregistration: docs/research_v2/narrow_window/prereg.md (written before these runs existed).

What this script does
---------------------
Pure re-reading of the SAVED score streams / stored candidates of

  artifacts/agent_v2/research_v2/narrow_window/wgm_c2_w1248   (CAND-A, WGM C2 g1_middle_late)
  artifacts/agent_v2/research_v2/narrow_window/pdm_c12_w124   (CAND-B, PDM C12 d1_middle)

No scorer is re-run.  For mode D the harness decision is reconstructed exactly
(``fit_bucket_stats`` on the calibration half of the routine=cb pool, disjoint pooling,
stored conformal thresholds re-derived and checked against ``case_run['calibration']``),
which yields the FULL alarm-endpoint vector per trace.  For mode T the source-side routine
streams are not stored in result.json (``run_case`` only persists target-trace streams), so
mode T is read off the stored ``trace_alarms`` first_alarm_end; that is sufficient because
both the strict and the +-band anchor bookkeeping depend only on the FIRST alarm endpoint
(see ``_block_from_first``).

Bookkeeping semantics are those of scripts/research_v2/labels/topic_entry_agreement.py
(``anchor_block`` / ``metrics_from_blocks``): ``metrics_from_blocks`` is imported from that
module unchanged; the anchor block itself is taken from ``research_v2.harness._anchor_block``
(``topic_entry_agreement.anchor_block`` is that function with band=0 inlined) so that the
tolerant band=5 variant required by prereg section 3 uses the identical harness semantics.
That script's main() is never called (its default output path is the frozen A-label effect
file).

Anchors are EVALUATION bookkeeping only; the detector is causal and sees no anchor.

Usage:
  cd <worktree>
  PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python scripts/research_v2/narrow_window/evaluate.py
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any

import torch

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "research_v2" / "zoom" / "missed_drift"))
sys.path.insert(0, str(ROOT / "scripts" / "research_v2" / "labels"))

from common import cases_for, load_result, streams_from_result  # noqa: E402
from research_v2 import io as rio  # noqa: E402
from research_v2.harness import (  # noqa: E402
    COMPARISONS,
    _anchor_block,
    conformal_threshold,
    fit_bucket_stats,
    routine_traces,
    scenario_halves,
)
from research_v2.readings import build_readings  # noqa: E402
from topic_entry_agreement import anchor_block as _te_anchor_block  # noqa: E402
from topic_entry_agreement import metrics_from_blocks  # noqa: E402

torch.set_num_threads(8)

LAB = ROOT / "docs" / "research_v2" / "labels"
NW = ROOT / "artifacts" / "agent_v2" / "research_v2" / "narrow_window"
DOCS = ROOT / "docs" / "research_v2" / "narrow_window"

BUCKET_SIZE = 32
MIN_BUCKET_TRACES = 30
BUCKET_CAP = None
BUCKET_MIN_CRITERION = "traces"
COMPARISON = "ge"
TOLERANT_BAND = 5
HORIZONS = (4, 8, 16)
PRIMARY_READINGS = ("max", "persist2")

RUNS = {
    "CAND-A": {
        "run_dir": NW / "wgm_c2_w1248",
        "scorer": "wgm / g1_middle_late (metric g1, layers 5-15)",
        "windows": [1, 2, 4, 8],
        "frozen_window": 8,
        "frozen_result": ROOT / "artifacts/agent_v2/research_v2/wgm/c2_g1_middle_late/result.json",
    },
    "CAND-B": {
        "run_dir": NW / "pdm_c12_w124",
        "scorer": "pdm / d1_middle (model d1, layers 5-11)",
        "windows": [1, 2, 4],
        "frozen_window": 4,
        "frozen_result": ROOT / "artifacts/agent_v2/research_v2/pdm_d1_middle_s1/result.json",
    },
}

# Reproduction check performed by the (read-only) checkers
# scripts/research_v2/narrow_window/check_reproduction_{wgm,pdm}.py, results recorded here.
REPRODUCTION = {
    "CAND-A": {
        "frozen_result": "artifacts/agent_v2/research_v2/wgm/c2_g1_middle_late/result.json",
        "window_checked": 8,
        "traces_compared": 360,
        "mismatches_max_alpha010_modeD": 0,
        "mismatches_persist2_alpha010_modeD": 0,
        "checker": "scripts/research_v2/narrow_window/check_reproduction_wgm.py",
        "note": (
            "All 360 trace_alarms rows per reading identical on first_alarm_end, tolerant band "
            "endpoint, weight and alarm_onset_count; all 16 stored conformal thresholds "
            "bit-identical; pooled.md w=8 mode D alpha 0.10 rows identical.  code_commit differs "
            "(frozen 1f27888 vs new 69869f6) but the intervening src/research_v2 commits are "
            "additive scorers or gated by b1_present_calibration, which this run does not set."
        ),
    },
    "CAND-B": {
        "frozen_result": "artifacts/agent_v2/research_v2/pdm_d1_middle_s1/result.json",
        "window_checked": 4,
        "traces_compared": 360,
        "mismatches_max_alpha010_modeD": 0,
        "mismatches_persist2_alpha010_modeD": 0,
        "checker": "scripts/research_v2/narrow_window/check_reproduction_pdm.py",
        "note": (
            "0 mismatches on first_alarm_end, tolerant band endpoint and alarm_onset_count; all "
            "stored conformal threshold blocks bit-identical; all 88 w=4 rows of tables.md "
            "byte-identical to the frozen run.  code_commit differs "
            "(frozen 2aace83 vs new 69869f6)."
        ),
    },
}


# ---------------------------------------------------------------------------
# labels
# ---------------------------------------------------------------------------


def read_jsonl(path: Path) -> dict[str, dict]:
    return {
        json.loads(line)["trace_id"]: json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


# ---------------------------------------------------------------------------
# anchor blocks
# ---------------------------------------------------------------------------


def _block_from_first(
    ends: torch.Tensor, first_alarm_end: int | None, anchor: int, band: int
) -> dict[str, Any]:
    """``_anchor_block`` computed from the FIRST alarm endpoint only.

    Exact for every field ``metrics_from_blocks`` consumes:
      * ``pre_alarm`` = (first < anchor - band)  -- alarm endpoints are ascending, so the
        first one is below the limit iff any is;
      * when ``pre_alarm`` is False the first eligible alarm IS the first alarm;
      * ``hit`` requires ``not pre_alarm``, so the "first eligible after a pre-alarm" value
        (the only field that can differ) never enters a metric.
    ``first_alarm_end_exact`` records that caveat.
    """
    limit = anchor - band
    pre_eligible = bool((ends < limit).any())
    pre_alarm = first_alarm_end is not None and first_alarm_end < limit
    first = None
    if first_alarm_end is not None and first_alarm_end >= limit:
        first = int(first_alarm_end)
    latency = None if first is None else max(0, first - anchor)
    return {
        "anchor": anchor,
        "band": band,
        "pre_eligible": pre_eligible,
        "pre_alarm": pre_alarm,
        "first_alarm_end": first,
        "first_alarm_end_exact": not pre_alarm,
        "latency": latency,
        "hit": (not pre_alarm) and first is not None,
        "reachable": {
            str(h): bool(((ends >= anchor) & (ends <= anchor + h)).any()) for h in (4, 8, 16, 32)
        },
    }


def counts_only(metrics: dict) -> dict:
    """metrics_from_blocks output trimmed to the fields prereg section 3 asks for."""
    out = {
        "n": metrics["n"],
        "pre_onset_count": metrics["pre_alarm_count"],
        "pre_onset_denominator": metrics["pre_alarm_denominator"],
        "pre_onset_rate_all": metrics["pre_alarm_rate_all"],
        "any_alarm_count": metrics["any_alarm_count"],
        "recall_final_count": metrics["recall_final_count"],
        "recall_final": metrics["recall_final"],
        "median_latency": metrics["median_latency"],
        "latencies": metrics["latencies"],
    }
    for h in HORIZONS:
        out[f"recall_plus_{h}_count"] = metrics[f"recall_plus_{h}_count"]
        out[f"recall_plus_{h}"] = metrics[f"recall_plus_{h}"]
        out[f"reachable_plus_{h}"] = metrics[f"reachable_plus_{h}"]
    return out


# ---------------------------------------------------------------------------
# reconstruction of one case run
# ---------------------------------------------------------------------------


def mode_d_alarms(case, streams, readings, alphas, stored_calibration):
    """Reconstruct the harness mode-D decision; returns per (reading, alpha) alarm data."""
    halves = scenario_halves(case.target_traces)
    target_routine = [
        t for t in routine_traces(case.target_traces, "cb") if streams[t.trace_id][1].numel()
    ]
    out: dict[tuple[str, float], dict[str, dict]] = {}
    threshold_check: list[Any] = []
    ends_by_trace: dict[str, torch.Tensor] = {}
    cal_half_of_trace: dict[str, int] = {}
    for cal_half in (0, 1):
        calibration = [t for t in target_routine if halves[t.pair_group_id] == cal_half]
        cal_streams = [streams[t.trace_id] for t in calibration]
        stats = fit_bucket_stats(
            cal_streams,
            bucket_size=BUCKET_SIZE,
            min_bucket_traces=MIN_BUCKET_TRACES,
            bucket_cap=BUCKET_CAP,
            min_criterion=BUCKET_MIN_CRITERION,
        )
        z_cal = [stats.standardize(s, e) for s, e in cal_streams]
        evaluated = [
            t
            for t in case.target_traces
            if halves[t.pair_group_id] != cal_half and streams[t.trace_id][1].numel()
        ]
        z_eval = {}
        for trace in evaluated:
            scores, ends = streams[trace.trace_id]
            z_eval[trace.trace_id] = stats.standardize(scores, ends)
            ends_by_trace[trace.trace_id] = ends
            cal_half_of_trace[trace.trace_id] = cal_half
        stored = stored_calibration["halves"][str(cal_half)]["thresholds"]
        for reading in readings:
            maxima = [float(reading.apply(z).max()) for z in z_cal if z.numel()]
            stats_eval = {tid: reading.apply(z) for tid, z in z_eval.items()}
            for alpha in alphas:
                if reading.threshold_source == "fixed":
                    threshold = float(reading.fixed_threshold)
                else:
                    threshold = float(conformal_threshold(maxima, alpha)["threshold"])
                key = f"{reading.name}|alpha{alpha:g}"
                stored_thr = float(stored[key]["threshold"])
                threshold_check.append(
                    [cal_half, key, stored_thr, threshold, stored_thr - threshold]
                )
                bucket = out.setdefault((reading.name, alpha), {})
                for tid, stat in stats_eval.items():
                    ends = ends_by_trace[tid]
                    states = COMPARISONS[COMPARISON](stat, stored_thr)
                    alarm_ends = ends[states]
                    bucket[tid] = {
                        "first": int(alarm_ends[0]) if alarm_ends.numel() else None,
                        "alarm_ends": alarm_ends,
                        "threshold": stored_thr,
                        "cal_half": cal_half,
                    }
    return out, ends_by_trace, cal_half_of_trace, threshold_check


def mode_t_alarms(case_run, alphas):
    """Read mode-T first alarm endpoints off the stored candidates (streams not persisted)."""
    out: dict[tuple[str, float], dict[str, dict]] = {}
    for cand in case_run["candidates"]:
        if cand["mode"] != "T" or cand["alpha"] not in alphas:
            continue
        bucket = out.setdefault((cand["reading"], cand["alpha"]), {})
        for row in cand["trace_alarms"]:
            bucket[row[0]] = {"first": row[2], "alarm_ends": None}
    return out


# ---------------------------------------------------------------------------
# metric assembly for one (case, mode, reading, alpha)
# ---------------------------------------------------------------------------


def evaluate_cell(
    case,
    alarms: dict[str, dict],
    ends_by_trace: dict[str, torch.Tensor],
    cal_half_of_trace: dict[str, int],
    resist_lab: dict[str, dict],
    prod_lab: dict[str, dict],
    stored_first: bool,
) -> dict:
    from research_v2.harness import arm_class

    groups = {"clean": [], "benign": [], "resist": [], "drift": []}
    for trace in case.target_traces:
        if trace.trace_id not in alarms:
            continue
        cls = arm_class(trace)
        if trace.positive:
            groups["drift"].append(trace)
        elif cls in groups:
            groups[cls].append(trace)

    def block(trace, anchor, band):
        info = alarms[trace.trace_id]
        ends = ends_by_trace[trace.trace_id]
        return _block_from_first(ends, info["first"], int(anchor), band)

    resist_anchored = [
        t for t in groups["resist"] if resist_lab[t.trace_id]["topic_entry_onset"] is not None
    ]
    resist_e0 = [
        t for t in groups["resist"] if resist_lab[t.trace_id]["topic_entry_onset"] is None
    ]
    leak = [t for t in resist_anchored if resist_lab[t.trace_id].get("flag") == "topic_word_leak"]
    leak_ids = {t.trace_id for t in leak}
    resist_no_leak = [t for t in resist_anchored if t.trace_id not in leak_ids]

    def metrics(traces, anchor_of, band):
        return counts_only(metrics_from_blocks([block(t, anchor_of(t), band) for t in traces]))

    def far(traces):
        if not traces:
            return None
        return sum(1 for t in traces if alarms[t.trace_id]["first"] is not None) / len(traces)

    def far_count(traces):
        return sum(1 for t in traces if alarms[t.trace_id]["first"] is not None)

    te = lambda t: resist_lab[t.trace_id]["topic_entry_onset"]  # noqa: E731
    prod = lambda t: prod_lab[t.trace_id]["product_onset"]  # noqa: E731
    evid = lambda t: t.evidence_onset  # noqa: E731

    negatives = groups["clean"] + groups["benign"]
    half_far = None
    if cal_half_of_trace:
        half_far = {}
        for h in (0, 1):
            sel = [t for t in negatives if cal_half_of_trace.get(t.trace_id) == h]
            half_far[str(h)] = {
                "n": len(sel),
                "far": far(sel),
                "far_clean": far([t for t in groups["clean"] if cal_half_of_trace.get(t.trace_id) == h]),
                "far_benign": far([t for t in groups["benign"] if cal_half_of_trace.get(t.trace_id) == h]),
                "note": "half = calibration half whose conformal threshold evaluated these traces",
            }

    cell = {
        "counts": {k: len(v) for k, v in groups.items()},
        "resist_anchored_n": len(resist_anchored),
        "resist_E0_n": len(resist_e0),
        "first_alarm_end_source": "stored trace_alarms (harness output)" if stored_first else "n/a",
        "negatives": {
            "n_clean": len(groups["clean"]),
            "n_benign": len(groups["benign"]),
            "far_clean": far(groups["clean"]),
            "far_benign": far(groups["benign"]),
            "far_clean_count": far_count(groups["clean"]),
            "far_benign_count": far_count(groups["benign"]),
            "n_pooled": len(negatives),
            "far_pooled": far(negatives),
            "far_pooled_count": far_count(negatives),
            "far_by_calibration_half": half_far,
        },
        "resist_anchored": {
            "strict": metrics(resist_anchored, te, 0),
            "tolerant5": metrics(resist_anchored, te, TOLERANT_BAND),
            "trace_ids": sorted(t.trace_id for t in resist_anchored),
        },
        "resist_anchored_no_topic_word_leak": {
            "strict": metrics(resist_no_leak, te, 0),
            "tolerant5": metrics(resist_no_leak, te, TOLERANT_BAND),
            "excluded_trace_ids": sorted(t.trace_id for t in leak),
        },
        "drift_product_onset": {
            "strict": metrics(groups["drift"], prod, 0),
            "tolerant5": metrics(groups["drift"], prod, TOLERANT_BAND),
        },
        "drift_evidence_onset": {
            "strict": metrics(groups["drift"], evid, 0),
            "tolerant5": metrics(groups["drift"], evid, TOLERANT_BAND),
        },
        "resist_E0": {
            "n": len(resist_e0),
            "alarm_at_all_count": far_count(resist_e0),
            "alarm_at_all_rate": far(resist_e0),
            "first_alarm_ends": {
                t.trace_id: alarms[t.trace_id]["first"]
                for t in resist_e0
                if alarms[t.trace_id]["first"] is not None
            },
        },
    }
    return cell


def pool_cells(cells: list[dict]) -> dict:
    """Pool two direction cells (disjoint trace sets) into one block."""

    def sum_metrics(path: str, band: str) -> dict:
        blocks = [c[path][band] for c in cells]
        out = {
            "n": sum(b["n"] for b in blocks),
            "pre_onset_count": sum(b["pre_onset_count"] for b in blocks),
            "pre_onset_denominator": sum(b["pre_onset_denominator"] for b in blocks),
            "any_alarm_count": sum(b["any_alarm_count"] for b in blocks),
            "recall_final_count": sum(b["recall_final_count"] for b in blocks),
        }
        out["recall_final"] = out["recall_final_count"] / out["n"] if out["n"] else None
        for h in HORIZONS:
            c = sum(b[f"recall_plus_{h}_count"] for b in blocks)
            out[f"recall_plus_{h}_count"] = c
            out[f"recall_plus_{h}"] = c / out["n"] if out["n"] else None
        lat = sorted(v for b in blocks for v in b["latencies"])
        out["latencies"] = lat
        out["median_latency"] = float(statistics.median(lat)) if lat else None
        return out

    pooled: dict[str, Any] = {}
    for path in (
        "resist_anchored",
        "resist_anchored_no_topic_word_leak",
        "drift_product_onset",
        "drift_evidence_onset",
    ):
        pooled[path] = {b: sum_metrics(path, b) for b in ("strict", "tolerant5")}
    n_clean = sum(c["negatives"]["n_clean"] for c in cells)
    n_benign = sum(c["negatives"]["n_benign"] for c in cells)
    fc = sum(c["negatives"]["far_clean_count"] for c in cells)
    fb = sum(c["negatives"]["far_benign_count"] for c in cells)
    half_diffs = []
    for c in cells:
        hf = c["negatives"]["far_by_calibration_half"]
        if hf and hf["0"]["far"] is not None and hf["1"]["far"] is not None:
            half_diffs.append(abs(hf["0"]["far"] - hf["1"]["far"]))
    pooled["negatives"] = {
        "n_clean": n_clean,
        "n_benign": n_benign,
        "far_clean_count": fc,
        "far_benign_count": fb,
        "far_clean": fc / n_clean if n_clean else None,
        "far_benign": fb / n_benign if n_benign else None,
        "n_pooled": n_clean + n_benign,
        "far_pooled_count": fc + fb,
        "far_pooled": (fc + fb) / (n_clean + n_benign) if (n_clean + n_benign) else None,
        "far_half_abs_diff_per_case": half_diffs or None,
        "far_half_max_abs_diff": max(half_diffs) if half_diffs else None,
    }
    pooled["resist_E0"] = {
        "n": sum(c["resist_E0"]["n"] for c in cells),
        "alarm_at_all_count": sum(c["resist_E0"]["alarm_at_all_count"] for c in cells),
    }
    pooled["resist_E0"]["alarm_at_all_rate"] = (
        pooled["resist_E0"]["alarm_at_all_count"] / pooled["resist_E0"]["n"]
        if pooled["resist_E0"]["n"]
        else None
    )
    return pooled


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    resist_lab = read_jsonl(LAB / "topic_entry_v1_adjudicated.jsonl")
    prod_lab = read_jsonl(LAB / "product_onset_v1_adjudicated.jsonl")
    batches = rio.load_core()
    cases = cases_for(batches)
    readings = build_readings()
    alphas = [0.05, 0.1]

    # sanity: the imported band-0 bookkeeping equals harness._anchor_block(band=0)
    _e = torch.tensor([1, 5, 9], dtype=torch.long)
    _a = torch.tensor([5, 9], dtype=torch.long)
    _x, _y = _te_anchor_block(_e, _a, 6), _anchor_block(_e, _a, 6, 0)
    assert all(_x[k] == _y[k] for k in _x), "anchor_block semantics drifted"

    report: dict[str, Any] = {
        "what_this_is": (
            "Narrow-window round evaluation, prereg docs/research_v2/narrow_window/prereg.md "
            "section 3.  Re-reading of saved harness output only; no scorer re-run, no anchor "
            "visible to any detector.  B1/B2 are development data: nothing here is an "
            "independent confirmation."
        ),
        "prereg": "docs/research_v2/narrow_window/prereg.md",
        "bookkeeping_source": (
            "scripts/research_v2/labels/topic_entry_agreement.py (metrics_from_blocks imported "
            "unchanged; anchor blocks from research_v2.harness._anchor_block, which "
            "topic_entry_agreement.anchor_block reproduces at band=0)"
        ),
        "definitions": {
            "positives_resist_anchored": (
                "14 resisted attack traces with non-null topic_entry_onset in "
                "topic_entry_v1_adjudicated.jsonl (7 b1 evaluated in case b2_to_b1, 7 b2 in "
                "b1_to_b2)"
            ),
            "positives_drift": (
                "59 drift traces; primary anchor product_onset "
                "(product_onset_v1_adjudicated.jsonl), secondary anchor evidence_onset from the "
                "trace itself"
            ),
            "negatives": "clean + benign_control",
            "reported_separately": "47 E0 (silent) resisted traces: alarm-at-all counts only",
            "strict": "band 0: any alarm ending before the anchor => pre_onset, trace is not a hit",
            "tolerant5": (
                "band 5: alarms ending within 5 tokens before the anchor count as hits with "
                "latency 0; alarms earlier than that remain pre_onset"
            ),
            "mode_T_caveat": (
                "result.json stores score streams for target traces only, so the mode-T "
                "source-side bucket statistics cannot be refitted; mode T is read off the stored "
                "trace_alarms first_alarm_end.  Every metric below depends only on the first "
                "alarm endpoint, so strict and tolerant metrics are exact; only the reported "
                "'first eligible alarm end' of a trace that already pre-alarmed is unavailable."
            ),
        },
        "reproduction": REPRODUCTION,
        "label_counts": {
            "resist_total": len(resist_lab),
            "resist_anchored": sum(
                1 for v in resist_lab.values() if v["topic_entry_onset"] is not None
            ),
            "resist_E0": sum(1 for v in resist_lab.values() if v["topic_entry_onset"] is None),
            "topic_word_leak": [
                k for k, v in resist_lab.items() if v.get("flag") == "topic_word_leak"
            ],
            "drift_total": len(prod_lab),
        },
        "candidates": {},
    }

    per_trace_rows: list[dict] = []

    for cand_name, spec in RUNS.items():
        result = load_result(spec["run_dir"] / "result.json")
        cand_block: dict[str, Any] = {
            "run_dir": str(spec["run_dir"]),
            "scorer": spec["scorer"],
            "frozen_window": spec["frozen_window"],
            "frozen_result": str(spec["frozen_result"]),
            "code_commit": result.get("code_commit"),
            "config": result["config"],
            "windows": {},
        }
        report["candidates"][cand_name] = cand_block

        for case_run in result["case_runs"]:
            if case_run["split"] != "S1" or case_run["routine_definition"] != "cb":
                continue
            width = case_run["window_width"]
            case = cases[case_run["case"]]
            streams = streams_from_result(case_run)
            d_alarms, ends_by_trace, cal_half, thr_check = mode_d_alarms(
                case, streams, readings, alphas, case_run["calibration"]["D"]
            )
            t_alarms = mode_t_alarms(case_run, alphas)
            ends_all = {
                tid: torch.tensor(b["ends"], dtype=torch.long)
                for tid, b in case_run["score_streams"].items()
            }

            # Verification against the stored compact trace_alarms (mode D, every
            # reading/alpha), then the STORED first_alarm_end is installed as authoritative:
            # score_streams are persisted rounded to 6 decimals, so a refit can flip a
            # window that sits within ~1e-7 of the stored conformal threshold.
            mism = []
            for cand in case_run["candidates"]:
                if cand["mode"] != "D":
                    continue
                key = (cand["reading"], cand["alpha"])
                mine = d_alarms.get(key)
                if mine is None:
                    mism.append([cand["candidate_id"], "missing_reconstruction"])
                    continue
                for row in cand["trace_alarms"]:
                    got = mine.get(row[0])
                    if got is None:
                        mism.append([cand["candidate_id"], row[0], "missing_trace"])
                        continue
                    if got["first"] != row[2]:
                        mism.append(
                            [cand["candidate_id"], row[0], "stored", row[2], "refit", got["first"]]
                        )
                    got["first_refit"] = got["first"]
                    got["first"] = row[2]

            wblock = cand_block["windows"].setdefault(
                str(width),
                {
                    "window_width": width,
                    "is_frozen_reference_window": width == spec["frozen_window"],
                    "reconstruction": {
                        "note": (
                            "Diagnostic only.  Stored score_streams are rounded to 6 decimals, "
                            "so a refit of the bucket statistics reproduces the conformal "
                            "thresholds only to ~1e-7.  All metrics below use the STORED "
                            "trace_alarms first_alarm_end, i.e. the harness decision itself."
                        ),
                        "mode_D_refit_vs_stored_first_alarm_end_mismatches": 0,
                        "mode_D_refit_threshold_max_abs_diff": 0.0,
                        "detail": [],
                    },
                    "modes": {},
                },
            )
            wblock["reconstruction"]["mode_D_refit_vs_stored_first_alarm_end_mismatches"] += len(
                mism
            )
            wblock["reconstruction"]["mode_D_refit_threshold_max_abs_diff"] = max(
                wblock["reconstruction"]["mode_D_refit_threshold_max_abs_diff"],
                max((abs(t[4]) for t in thr_check), default=0.0),
            )
            wblock["reconstruction"]["detail"].extend(mism[:20])

            for mode, alarm_map, exact in (("D", d_alarms, True), ("T", t_alarms, True)):
                mblock = wblock["modes"].setdefault(mode, {})
                for (rname, alpha), alarms in alarm_map.items():
                    ab = mblock.setdefault(f"alpha{alpha:g}", {}).setdefault(rname, {"cases": {}})
                    ab["cases"][case_run["case"]] = evaluate_cell(
                        case,
                        alarms,
                        ends_by_trace if mode == "D" else ends_all,
                        cal_half if mode == "D" else {},
                        resist_lab,
                        prod_lab,
                        exact,
                    )

            # ---- per-trace table for the 14 anchored resist traces (alpha 0.10, max/persist2)
            for rname in PRIMARY_READINGS:
                alarms = d_alarms[(rname, 0.1)]
                for trace in case.target_traces:
                    lab = resist_lab.get(trace.trace_id)
                    if lab is None or lab["topic_entry_onset"] is None:
                        continue
                    info = alarms[trace.trace_id]
                    onset = int(lab["topic_entry_onset"])
                    first = info["first"]
                    if first is None:
                        status = "NONE"
                        rel = None
                    elif first < onset:
                        status = f"PRE-{onset - first}"
                        rel = first - onset
                    else:
                        status = f"+{first - onset}"
                        rel = first - onset
                    blk = _block_from_first(ends_by_trace[trace.trace_id], first, onset, 0)
                    blk5 = _block_from_first(
                        ends_by_trace[trace.trace_id], first, onset, TOLERANT_BAND
                    )
                    per_trace_rows.append(
                        {
                            "candidate": cand_name,
                            "window": width,
                            "reading": rname,
                            "alpha": 0.1,
                            "case": case_run["case"],
                            "trace_id": trace.trace_id,
                            "topic_entry_class": lab["topic_entry_class"],
                            "confidence": lab["confidence"],
                            "flag": lab.get("flag"),
                            "topic_entry_onset": onset,
                            "topic_span_end": lab["topic_span_end"],
                            "decode_token_count": trace.token_count,
                            "first_alarm_end_global": first,
                            "status": status,
                            "relative_to_onset": rel,
                            "strict_hit": blk["hit"],
                            "strict_latency": blk["latency"],
                            "tolerant5_hit": blk5["hit"],
                            "tolerant5_latency": blk5["latency"],
                            "alarm_end_count_refit": int(info["alarm_ends"].numel()),
                        }
                    )
            del streams, ends_all, d_alarms
            print(f"  {cand_name} w={width} {case_run['case']}: refit-vs-stored "
                  f"first_alarm_end mismatches {len(mism)}, max |threshold refit diff| "
                  f"{max((abs(t[4]) for t in thr_check), default=0.0):.2e}", flush=True)

        # pool directions
        for width_key, wblock in cand_block["windows"].items():
            for mode, mblock in wblock["modes"].items():
                for akey, ablock in mblock.items():
                    for rname, rb in ablock.items():
                        rb["pooled_both_directions"] = pool_cells(list(rb["cases"].values()))

    report["per_trace_anchored_resist"] = per_trace_rows

    # ---- per-direction anchored-resist R+16 (prereg H2 conclusion rule) -----
    per_dir = {}
    for cand_name, cand_block in report["candidates"].items():
        per_dir[cand_name] = {}
        for width_key, wblock in cand_block["windows"].items():
            entry = {}
            for rname in PRIMARY_READINGS:
                rb = wblock["modes"]["D"]["alpha0.1"][rname]
                entry[rname] = {
                    case_name: {
                        "n": cell["resist_anchored"]["strict"]["n"],
                        "R_plus_16_strict": cell["resist_anchored"]["strict"]["recall_plus_16_count"],
                        "R_plus_16_tolerant5": cell["resist_anchored"]["tolerant5"][
                            "recall_plus_16_count"
                        ],
                        "R_final_strict": cell["resist_anchored"]["strict"]["recall_final_count"],
                        "pre_onset": cell["resist_anchored"]["strict"]["pre_onset_count"],
                    }
                    for case_name, cell in rb["cases"].items()
                }
            per_dir[cand_name][width_key] = entry
    report["per_direction_anchored_resist_alpha0.10_modeD"] = per_dir

    NW.mkdir(parents=True, exist_ok=True)
    out = NW / "effect.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print("written", out)
    return report





# ---------------------------------------------------------------------------
# markdown tables (docs/research_v2/narrow_window/tables.md)
# ---------------------------------------------------------------------------


def _f(x, nd=3):
    return "-" if x is None else f"{x:.{nd}f}"


def _row(cand, width, reading, blk):
    ra = blk["resist_anchored"]["strict"]
    rt = blk["resist_anchored"]["tolerant5"]
    dp = blk["drift_product_onset"]["strict"]
    de = blk["drift_evidence_onset"]["strict"]
    ng = blk["negatives"]
    e0 = blk["resist_E0"]
    return [
        cand,
        str(width),
        reading,
        f"{ra['recall_plus_4_count']}/{ra['n']}",
        f"{ra['recall_plus_8_count']}/{ra['n']}",
        f"{ra['recall_plus_16_count']}/{ra['n']}",
        f"{ra['recall_final_count']}/{ra['n']}",
        f"{rt['recall_plus_16_count']}/{rt['n']}",
        str(ra["pre_onset_count"]),
        _f(ra["median_latency"], 1),
        f"{dp['recall_plus_8_count']}/{dp['n']}",
        f"{dp['recall_plus_16_count']}/{dp['n']}",
        f"{dp['recall_final_count']}/{dp['n']}",
        str(dp["pre_onset_count"]),
        f"{de['recall_plus_16_count']}/{de['n']}",
        _f(ng["far_clean"]),
        _f(ng["far_benign"]),
        _f(ng["far_pooled"]),
        _f(ng["far_half_max_abs_diff"]),
        f"{e0['alarm_at_all_count']}/{e0['n']}",
    ]


HEAD = [
    "cand", "w", "reading",
    "res R+4", "res R+8", "res R+16", "res Rfin", "res R+16 tol5", "res pre", "res med lat",
    "drift R+8", "drift R+16", "drift Rfin", "drift pre", "drift R+16 (evid)",
    "FAR clean", "FAR benign", "FAR pooled", "FAR half \\|diff\\|", "E0 alarm",
]


def _table(rows, head=HEAD):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def write_tables(report: dict) -> Path:
    L: list[str] = []
    A = report["candidates"]
    order = [("CAND-A", w) for w in ("1", "2", "4", "8")] + [
        ("CAND-B", w) for w in ("1", "2", "4")
    ]

    L.append("# 窄窗口轮次：评估表（mode D 为主）\n")
    L.append(
        "生成脚本 `scripts/research_v2/narrow_window/evaluate.py`；机器可读全量 "
        "`artifacts/agent_v2/research_v2/narrow_window/effect.json`。\n"
        "口径 = `docs/research_v2/narrow_window/prereg.md` 第 3 节，逐条执行；记账语义取自 "
        "`scripts/research_v2/labels/topic_entry_agreement.py`（`metrics_from_blocks` 原样导入）。\n"
    )
    L.append(
        "**B1/B2 是开发数据，本文件中的任何数字都不是独立验证。** 14 条有锚点抵御样本上没有做任何选择："
        "所有窗宽、所有读数、两个 α 全部报告。\n"
    )
    L.append("## 0. 复现对照\n")
    L.append(
        "| 候选 | 冻结窗宽 | 冻结 result.json | 比较条数 | max α=0.10 mismatch | persist2 α=0.10 mismatch |"
    )
    L.append("|---|---|---|---|---|---|")
    for k, v in report["reproduction"].items():
        L.append(
            f"| {k} | w={v['window_checked']} | `{v['frozen_result']}` | {v['traces_compared']} | "
            f"{v['mismatches_max_alpha010_modeD']} | {v['mismatches_persist2_alpha010_modeD']} |"
        )
    L.append("")
    L.append(
        "复现通过。附加自检（本脚本）：冻结窗宽的 drift `evidence_onset` R+8 / R+16 与 "
        "`topic_entry_agreement.py` 里写死的冻结参考值逐位相同（CAND-A w=8 persist2 "
        "b1→b2 0.429/0.600、b2→b1 0.500/0.625；CAND-B w=4 persist2 b1→b2 0.486/0.514、"
        "b2→b1 0.500/0.625），有锚点抵御 R+16 / R_final 与 E0 报警数也与 "
        "`topic_entry_v1_adjudication.md` §3 的表相同（CAND-A 2/14、4/14、E0 2/47；"
        "CAND-B 2/14、2/14、E0 4/47）。\n"
    )
    for k, v in report["candidates"].items():
        rec = [v["windows"][w]["reconstruction"] for w in v["windows"]]
        mm = sum(r["mode_D_refit_vs_stored_first_alarm_end_mismatches"] for r in rec)
        md = max(r["mode_D_refit_threshold_max_abs_diff"] for r in rec)
        L.append(
            f"- {k}：把保存的分数流重新拟合并复算 mode D，与存储 `trace_alarms` 的 "
            f"`first_alarm_end` 相比共 {mm} 条不一致，重算阈值与存储阈值最大绝对差 {md:.2e}"
            "（分数流按 6 位小数保存，故只能复现到 ~1e-7）。**下面所有数字用的是存储的"
            "`first_alarm_end`，即 harness 自己的判定。**"
        )
    L.append("")

    L.append("## 1. 主表：mode D，α = 0.10，max / persist2（两方向合并）\n")
    L.append(
        "有锚点抵御 n = 14（`topic_entry_onset`），drift n = 59（主锚点 `product_onset`，"
        "末列给出副锚点 `evidence_onset` 的 R+16）；负例 = clean + benign_control（n = 240）；"
        "E0 = 47 条沉默抵御，只数“有没有报警”。`tol5` = 锚点前 ≤5 token 的报警算命中（延迟 0）。"
        "`FAR half |diff|` = 两个方向里“两校准半份 FAR 之差”的最大值。\n"
    )
    rows = []
    for cand, w in order:
        for rd in PRIMARY_READINGS:
            rows.append(
                _row(cand, w, rd, A[cand]["windows"][w]["modes"]["D"]["alpha0.1"][rd]["pooled_both_directions"])
            )
    L.append(_table(rows))
    L.append("")
    L.append("冻结参照行：CAND-A w=8、CAND-B w=4。\n")

    L.append("## 2. 分方向有锚点抵御 R+16（预注册 H2 判据，α=0.10，mode D，严格口径）\n")
    L.append("| cand | w | reading | b1→b2 (n=7) | b2→b1 (n=7) | 合并 | tol5 合并 |")
    L.append("|---|---|---|---|---|---|---|")
    for cand, w in order:
        for rd in PRIMARY_READINGS:
            e = report["per_direction_anchored_resist_alpha0.10_modeD"][cand][w][rd]
            a, b = e["b1_to_b2"], e["b2_to_b1"]
            tot = a["R_plus_16_strict"] + b["R_plus_16_strict"]
            tol = a["R_plus_16_tolerant5"] + b["R_plus_16_tolerant5"]
            L.append(
                f"| {cand} | {w} | {rd} | {a['R_plus_16_strict']}/7 | {b['R_plus_16_strict']}/7 |"
                f" {tot}/14 | {tol}/14 |"
            )
    L.append("")
    L.append(
        "预注册结论规则：某个 w 只有在**两个方向各自**满足 H2 才进入 B3；单方向成立只记为"
        "“待 B3 验证”。H2 的合并门槛是 R+16 ≥ 5/14。\n"
    )

    L.append("## 3. 14 条有锚点抵御样本逐条（α = 0.10，mode D）\n")
    L.append(
        "格子 = 第一个**合格**报警端点相对 `topic_entry_onset` 的位置：`+k` 命中（延迟 k）、"
        "`PRE-k` 表示第一个报警落在锚点前 k 个 token（严格口径判为 pre-onset、不算命中）、"
        "`NONE` 无报警。`m`=max，`p`=persist2。\n"
    )
    rows_by = {}
    for r in report["per_trace_anchored_resist"]:
        rows_by[(r["trace_id"], r["candidate"], r["window"], r["reading"])] = r["status"]
    meta = {}
    for r in report["per_trace_anchored_resist"]:
        meta[r["trace_id"]] = r
    for cand, widths in (("CAND-A", [1, 2, 4, 8]), ("CAND-B", [1, 2, 4])):
        cols = [f"w{w} {s}" for w in widths for s in ("m", "p")]
        head = ["trace", "dir", "class", "conf", "onset", "span_end", "len"] + cols
        rws = []
        for tid in sorted(meta):
            m = meta[tid]
            rws.append(
                [
                    tid.replace("--attack", ""),
                    m["case"],
                    m["topic_entry_class"],
                    m["confidence"],
                    str(m["topic_entry_onset"]),
                    "-" if m["topic_span_end"] is None else str(m["topic_span_end"]),
                    str(m["decode_token_count"]),
                ]
                + [
                    rows_by[(tid, cand, w, rd)]
                    for w in widths
                    for rd in ("max", "persist2")
                ]
            )
        L.append(f"### {cand}\n")
        L.append(_table(rws, head))
        L.append("")

    L.append("## 4. `topic_word_leak` 敏感性（去掉 1 条，n = 13）\n")
    L.append("| cand | w | reading | R+16 含 | R+16 去 | R_final 含 | R_final 去 |")
    L.append("|---|---|---|---|---|---|---|")
    for cand, w in order:
        for rd in PRIMARY_READINGS:
            p = A[cand]["windows"][w]["modes"]["D"]["alpha0.1"][rd]["pooled_both_directions"]
            i, o = p["resist_anchored"]["strict"], p["resist_anchored_no_topic_word_leak"]["strict"]
            L.append(
                f"| {cand} | {w} | {rd} | {i['recall_plus_16_count']}/{i['n']} |"
                f" {o['recall_plus_16_count']}/{o['n']} | {i['recall_final_count']}/{i['n']} |"
                f" {o['recall_final_count']}/{o['n']} |"
            )
    L.append("")
    L.append(
        "被排除的样本："
        + ", ".join(f"`{t}`" for t in report["label_counts"]["topic_word_leak"])
        + "（E0 组仍按 47 条计；`topic_entry_v1_adjudication.md` §3 的另一种读法是把它并入 E0 得 48 条）。\n"
    )

    # ---------------- appendix ----------------
    L.append("## 附录 A：mode D，α = 0.05，max / persist2\n")
    rows = []
    for cand, w in order:
        for rd in PRIMARY_READINGS:
            rows.append(
                _row(cand, w, rd, A[cand]["windows"][w]["modes"]["D"]["alpha0.05"][rd]["pooled_both_directions"])
            )
    L.append(_table(rows))
    L.append("")

    for alpha_key, title in (("alpha0.1", "α = 0.10"), ("alpha0.05", "α = 0.05")):
        L.append(f"## 附录 B：mode D，{title}，其余全部读数\n")
        rows = []
        for cand, w in order:
            for rd in sorted(A[cand]["windows"][w]["modes"]["D"][alpha_key]):
                if rd in PRIMARY_READINGS:
                    continue
                rows.append(
                    _row(cand, w, rd, A[cand]["windows"][w]["modes"]["D"][alpha_key][rd]["pooled_both_directions"])
                )
        L.append(_table(rows))
        L.append("")
    L.append(
        "`runlen*` 读数用固定阈值（不受 α 控制），其 FAR 是被测量而不是被钉住的。\n"
    )

    L.append("## 附录 C：mode T（次要），α = 0.10，max / persist2\n")
    L.append(
        "result.json 只保存目标侧分数流，源侧 routine 流没有保存，因此 mode T 无法重拟合；"
        "这里的报警端点直接读存储的 `trace_alarms.first_alarm_end`。所有严格 / 容差指标只依赖"
        "第一个报警端点，故数值精确；唯一取不到的是“已经 pre-onset 的样本之后的第一个合格报警端点”，"
        "而它不进入任何指标。mode T 没有校准半份，`FAR half |diff|` 记为 `-`。\n"
    )
    rows = []
    for cand, w in order:
        for rd in PRIMARY_READINGS:
            rows.append(
                _row(cand, w, rd, A[cand]["windows"][w]["modes"]["T"]["alpha0.1"][rd]["pooled_both_directions"])
            )
    L.append(_table(rows))
    L.append("")

    L.append("## 附录 D：分校准半份 FAR（mode D，α = 0.10）\n")
    L.append(
        "half = 用哪一半的保形阈值来判这批样本（`scenario_halves` 的确定性划分，"
        "result.json 的 calibration 块只存了半份的计数与阈值，成员关系由同一函数复算）。\n"
    )
    L.append("| cand | w | reading | dir | half0 n | half0 FAR | half1 n | half1 FAR | \\|diff\\| |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for cand, w in order:
        for rd in PRIMARY_READINGS:
            cases = A[cand]["windows"][w]["modes"]["D"]["alpha0.1"][rd]["cases"]
            for cname, cell in cases.items():
                hf = cell["negatives"]["far_by_calibration_half"]
                d = abs(hf["0"]["far"] - hf["1"]["far"])
                L.append(
                    f"| {cand} | {w} | {rd} | {cname} | {hf['0']['n']} | {_f(hf['0']['far'])} |"
                    f" {hf['1']['n']} | {_f(hf['1']['far'])} | {_f(d)} |"
                )
    L.append("")

    DOCS.mkdir(parents=True, exist_ok=True)
    out = DOCS / "tables.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("written", out)
    return out


if __name__ == "__main__":
    write_tables(main())
