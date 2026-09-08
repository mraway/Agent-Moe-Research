#!/usr/bin/env python3
"""FCM analysis: global tables, the per-trace target table, recovery counts and the
mechanism verdicts of the preregistration (fcm_prereg.md sections 5-8).

Reads the stored score streams of the candidate runs (and of the two frozen candidates),
replays the harness mode-D decision to obtain per-trace margins, and writes
``fcm_analysis.json`` plus the markdown tables of the report.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    ANCHORS,
    CANDIDATES,
    COMBINER,
    FROZEN,
    FROZEN_WINDOW,
    MISSES,
    OUT,
    SENSITIVITY,
    anchored_case,
    case_runs,
    cases_for,
    load_result,
    product_onsets,
    replay_mode_d,
    streams_from_result,
)
from research_v2 import io as rio  # noqa: E402
from research_v2.harness import aggregate_rows  # noqa: E402

torch.set_num_threads(12)

PRIMARY_READING = "persist2"
PRIMARY_ALPHA = 0.10
DIRECTIONS = ("b1_to_b2", "b2_to_b1")
TOLERANCE = 0.02
GRID = {**CANDIDATES, **SENSITIVITY}


# ---------------------------------------------------------------------------
# target sample (prereg section 8)
# ---------------------------------------------------------------------------


def target_traces(batches) -> dict[str, dict]:
    misses = json.loads(MISSES.read_text(encoding="utf-8"))
    common: dict[str, set[str]] = {}
    for case in DIRECTIONS:
        a = {m["trace_id"] for m in misses["CAND-A"][case]["misses"]}
        b = {m["trace_id"] for m in misses["CAND-B"][case]["misses"]}
        common[case] = a & b
    labels = {
        json.loads(line)["trace_id"]: json.loads(line)
        for line in (Path(__file__).resolve().parents[3] / "docs/research_v2/labels/product_onset_v1_adjudicated.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    }
    by_id = {t.trace_id: t for traces in batches.values() for t in traces}
    selected: dict[str, dict] = {}
    for case, ids in common.items():
        for trace_id in ids:
            selected.setdefault(trace_id, {"reasons": [], "case": case})
            selected[trace_id]["reasons"].append("common_miss")
    for trace_id, row in labels.items():
        trace = by_id[trace_id]
        case = "b1_to_b2" if trace.batch == "b2" else "b2_to_b1"
        if row["domain"] == "programming":
            selected.setdefault(trace_id, {"reasons": [], "case": case})
            selected[trace_id]["reasons"].append("programming")
        if row["product_class"] == "P3":
            selected.setdefault(trace_id, {"reasons": [], "case": case})
            selected[trace_id]["reasons"].append("P3_tool_product")
    for trace_id, block in selected.items():
        trace = by_id[trace_id]
        row = labels[trace_id]
        block.update(
            {
                "domain": trace.scenario_domain,
                "channel": trace.channel,
                "workflow": trace.workflow,
                "decode_len": trace.token_count,
                "evidence_onset": trace.evidence_onset,
                "product_onset": int(row["product_onset"]),
                "product_class": row["product_class"],
                "case": "b1_to_b2" if trace.batch == "b2" else "b2_to_b1",
            }
        )
    return selected


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


def candidate_streams(batches) -> dict[str, dict[str, dict]]:
    """{candidate: {case: {"streams": ..., "harness_alarms": ...}}} for all runs."""

    out: dict[str, dict[str, dict]] = {}
    for key, spec in GRID.items():
        result = load_result(OUT / f"{spec['run']}__evidence" / "result.json")
        runs = case_runs(result, spec["window"])
        out[key] = {}
        for case_name, case_run in runs.items():
            alarms = {}
            for candidate in case_run["candidates"]:
                if (
                    candidate["mode"] == "D"
                    and candidate["alpha"] == PRIMARY_ALPHA
                    and candidate["reading"] == PRIMARY_READING
                ):
                    alarms = {row[0]: row for row in candidate["trace_alarms"]}
            out[key][case_name] = {
                "streams": streams_from_result(case_run),
                "harness_alarms": alarms,
            }
    for key, path in FROZEN.items():
        result = load_result(path)
        runs = case_runs(result, FROZEN_WINDOW[key])
        name = f"CAND-{key}"
        out[name] = {}
        for case_name, case_run in runs.items():
            alarms = {}
            for candidate in case_run["candidates"]:
                if (
                    candidate["mode"] == "D"
                    and candidate["alpha"] == PRIMARY_ALPHA
                    and candidate["reading"] == PRIMARY_READING
                ):
                    alarms = {row[0]: row for row in candidate["trace_alarms"]}
            out[name][case_name] = {
                "streams": streams_from_result(case_run),
                "harness_alarms": alarms,
            }
    return out


def replay_all(batches, streams_by_candidate):
    cases = cases_for(batches)
    onsets = product_onsets()
    anchored = {
        "evidence": cases,
        "product": {name: anchored_case(case, onsets) for name, case in cases.items()},
    }
    replay: dict[str, dict] = {}
    mismatches: list[str] = []
    for key, per_case in streams_by_candidate.items():
        replay[key] = {}
        for case_name, block in per_case.items():
            replay[key][case_name] = {}
            for anchor in ANCHORS:
                rows, detail = replay_mode_d(
                    anchored[anchor][case_name],
                    block["streams"],
                    reading_name=PRIMARY_READING,
                    alpha=PRIMARY_ALPHA,
                )
                replay[key][case_name][anchor] = {"rows": rows, "detail": detail}
                if anchor == "evidence" and block["harness_alarms"]:
                    for row in rows:
                        stored = block["harness_alarms"].get(row["trace_id"])
                        if stored is None or stored[2] != row["first_alarm_end"]:
                            mismatches.append(f"{key}|{case_name}|{row['trace_id']}")
    return replay, mismatches


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------


def far_block(rows) -> dict[str, float]:
    metrics = aggregate_rows(rows)
    return {
        "far_all": metrics["non_drift_false_alarm_rate"],
        "far_clean": metrics["by_arm"]["clean"]["false_alarm_rate"],
        "far_benign": metrics["by_arm"]["benign"]["false_alarm_rate"],
        "far_resist": metrics["by_arm"]["resist"]["false_alarm_rate"],
        "recall_plus_8": metrics["onset_strict"]["recall_plus_8"],
        "recall_plus_16": metrics["onset_strict"]["recall_plus_16"],
        "recall_final": metrics["onset_strict"]["recall_final"],
        "pre_alarm_rate": metrics["onset_strict"]["pre_alarm_rate"],
        "median_latency": metrics["onset_strict"]["median_latency"],
        "programming": metrics["by_domain"].get("programming"),
    }


def global_rows() -> list[dict]:
    rows: list[dict] = []
    for key, spec in GRID.items():
        for anchor in ANCHORS:
            summary = json.loads((OUT / f"{spec['run']}__{anchor}" / "summary.json").read_text("utf-8"))
            for entry in summary:
                rows.append(
                    {
                        "candidate": key,
                        "anchor": anchor,
                        "text_derived": spec["text_derived"],
                        **{k: entry[k] for k in (
                            "case", "mode", "alpha", "reading", "far_all", "far_clean", "far_benign",
                            "far_resist", "pre_alarm_rate", "recall_plus_4", "recall_plus_8",
                            "recall_plus_16", "recall_final", "median_latency",
                        )},
                    }
                )
    combine = json.loads((OUT / "f4_combine.json").read_text("utf-8"))
    for key, metrics in combine["metrics"].items():
        case, mode, reading, alpha, anchor = key.split("|")
        strict = metrics["onset_strict"]
        rows.append(
            {
                "candidate": "F4",
                "anchor": anchor,
                "text_derived": True,
                "case": case,
                "mode": mode,
                "alpha": float(alpha),
                "reading": reading,
                "far_all": metrics["non_drift_false_alarm_rate"],
                "far_clean": metrics["by_arm"]["clean"]["false_alarm_rate"],
                "far_benign": metrics["by_arm"]["benign"]["false_alarm_rate"],
                "far_resist": metrics["by_arm"]["resist"]["false_alarm_rate"],
                "pre_alarm_rate": strict["pre_alarm_rate"],
                "recall_plus_4": strict["recall_plus_4"],
                "recall_plus_8": strict["recall_plus_8"],
                "recall_plus_16": strict["recall_plus_16"],
                "recall_final": strict["recall_final"],
                "median_latency": strict["median_latency"],
            }
        )
    return rows


def frozen_reference_rows(replay) -> dict[tuple[str, str, str], dict]:
    """FAR / recall of CAND-A and CAND-B at the primary operating point, both anchors."""

    out = {}
    for key in ("CAND-A", "CAND-B"):
        for case_name in DIRECTIONS:
            for anchor in ANCHORS:
                out[(key, case_name, anchor)] = far_block(replay[key][case_name][anchor]["rows"])
    return out


def main() -> None:
    batches = rio.load_core()
    targets = target_traces(batches)
    streams_by_candidate = candidate_streams(batches)
    replay, mismatches = replay_all(batches, streams_by_candidate)
    print(f"replay verification mismatches: {len(mismatches)}", flush=True)

    frozen = frozen_reference_rows(replay)
    combine = json.loads((OUT / "f4_combine.json").read_text("utf-8"))

    # ---- per-trace target table -------------------------------------------
    per_trace: dict[str, dict] = {}
    for trace_id, meta in targets.items():
        case_name = meta["case"]
        entry = {"meta": meta, "candidates": {}}
        for key in list(GRID) + ["CAND-A", "CAND-B"]:
            block = replay[key][case_name]
            row_by_anchor = {}
            for anchor in ANCHORS:
                rows = {r["trace_id"]: r for r in block[anchor]["rows"]}
                row = rows.get(trace_id)
                detail = block[anchor]["detail"].get(trace_id)
                if row is None or detail is None:
                    continue
                onset = meta["evidence_onset"] if anchor == "evidence" else meta["product_onset"]
                ends = detail["ends"]
                statistic = detail["statistic"]
                mask = (ends >= onset) & (ends <= onset + 16)
                in_horizon = float(statistic[mask].max()) if bool(mask.any()) else float("-inf")
                strict = row["anchors"]["onset_strict"]
                row_by_anchor[anchor] = {
                    "threshold": detail["threshold"],
                    "max_in_horizon": in_horizon,
                    "margin": in_horizon - detail["threshold"],
                    "first_alarm_end": row["first_alarm_end"],
                    "pre_alarm": strict["pre_alarm"],
                    "first_post_alarm": strict["first_alarm_end"],
                    "latency": strict["latency"],
                    "clean_hit16": bool(strict["hit"] and strict["latency"] is not None and strict["latency"] <= 16),
                }
            entry["candidates"][key] = row_by_anchor
        # F4 (alpha 0.05 each part, persist2)
        detail = combine["detail"][case_name].get(trace_id)
        if detail is not None:
            ends = torch.tensor(detail["ends"], dtype=torch.long)
            margin = torch.tensor(detail["margin"], dtype=torch.float64)
            alarms = ends[margin >= 0]
            f4 = {}
            for anchor in ANCHORS:
                onset = meta["evidence_onset"] if anchor == "evidence" else meta["product_onset"]
                mask = (ends >= onset) & (ends <= onset + 16)
                in_horizon = float(margin[mask].max()) if bool(mask.any()) else float("-inf")
                pre = bool((alarms < onset).any())
                post = alarms[alarms >= onset]
                first_post = int(post[0]) if post.numel() else None
                f4[anchor] = {
                    "threshold": 0.0,
                    "max_in_horizon": in_horizon,
                    "margin": in_horizon,
                    "first_alarm_end": int(alarms[0]) if alarms.numel() else None,
                    "pre_alarm": pre,
                    "first_post_alarm": first_post,
                    "latency": None if first_post is None else first_post - onset,
                    "clean_hit16": bool((not pre) and first_post is not None and first_post - onset <= 16),
                }
            entry["candidates"]["F4"] = f4
        per_trace[trace_id] = entry

    # ---- FAR tolerance and recovery ---------------------------------------
    f4_far = {}
    for key, metrics in combine["metrics"].items():
        case, mode, reading, alpha, anchor = key.split("|")
        if mode == "D" and reading == "persist2" and float(alpha) == 0.05:
            f4_far[(case, anchor)] = {
                "far_all": metrics["non_drift_false_alarm_rate"],
                "far_benign": metrics["by_arm"]["benign"]["false_alarm_rate"],
                "far_resist": metrics["by_arm"]["resist"]["false_alarm_rate"],
            }

    recovery: dict[str, dict] = {}
    for key in list(GRID) + ["F4"]:
        spec = GRID.get(key) or COMBINER[key]
        reference = f"CAND-{spec['reference']}"
        recovery[key] = {}
        for case_name in DIRECTIONS:
            for anchor in ANCHORS:
                if key == "F4":
                    far = f4_far[(case_name, anchor)]
                else:
                    far = far_block(replay[key][case_name][anchor]["rows"])
                ref = frozen[(reference, case_name, anchor)]
                within = (
                    far["far_all"] <= ref["far_all"] + TOLERANCE
                    and far["far_benign"] <= ref["far_benign"] + TOLERANCE
                    and far["far_resist"] <= ref["far_resist"] + TOLERANCE
                )
                recovered = []
                still_missed = []
                for trace_id, entry in per_trace.items():
                    if entry["meta"]["case"] != case_name:
                        continue
                    block = entry["candidates"].get(key, {}).get(anchor)
                    ref_block = entry["candidates"].get(reference, {}).get(anchor)
                    if block is None or ref_block is None:
                        continue
                    if block["clean_hit16"] and not ref_block["clean_hit16"]:
                        recovered.append(trace_id)
                    elif not block["clean_hit16"]:
                        still_missed.append(trace_id)
                recovery[key][f"{case_name}|{anchor}"] = {
                    "reference": reference,
                    "far": far,
                    "reference_far": {k: ref[k] for k in ("far_all", "far_benign", "far_resist")},
                    "within_tolerance": bool(within),
                    "recovered": sorted(recovered),
                    "recovered_count": len(recovered),
                    "recovered_within_tolerance": sorted(recovered) if within else [],
                    "programming_recovered": sorted(
                        t for t in recovered if per_trace[t]["meta"]["domain"] == "programming"
                    ),
                    "still_missed": sorted(still_missed),
                }
    # programming clean-hit counts per candidate (all 8 programming drifts)
    programming = [t for t, e in per_trace.items() if e["meta"]["domain"] == "programming"]
    prog_hits: dict[str, dict] = {}
    for key in list(GRID) + ["F4", "CAND-A", "CAND-B"]:
        prog_hits[key] = {}
        for anchor in ANCHORS:
            hits = [
                t for t in programming
                if per_trace[t]["candidates"].get(key, {}).get(anchor, {}).get("clean_hit16")
            ]
            prog_hits[key][anchor] = {"count": len(hits), "traces": sorted(hits)}

    payload = {
        "targets": targets,
        "per_trace": {
            trace_id: {
                "meta": entry["meta"],
                "candidates": entry["candidates"],
            }
            for trace_id, entry in per_trace.items()
        },
        "recovery": recovery,
        "programming_clean_hits": prog_hits,
        "frozen_reference": {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in frozen.items()},
        "global_rows": global_rows(),
        "replay_verification_mismatches": mismatches,
        "tolerance": TOLERANCE,
        "primary": {"reading": PRIMARY_READING, "alpha": PRIMARY_ALPHA, "mode": "D", "routine": "cb"},
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "fcm_analysis.json").write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    print("written", OUT / "fcm_analysis.json")
    print("targets:", len(targets), "programming:", len(programming))
    for key, block in prog_hits.items():
        print(f"  {key}: programming clean +16 hits evidence={block['evidence']['count']}/8 product={block['product']['count']}/8")


if __name__ == "__main__":
    main()
