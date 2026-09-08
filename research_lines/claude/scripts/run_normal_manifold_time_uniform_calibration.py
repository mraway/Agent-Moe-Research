#!/usr/bin/env python3
"""Run the preregistered C1 group-aware time-uniform calibration study."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Sequence

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from phase_a.normal_manifold import (  # noqa: E402
    B1_INDEX_SHA256,
    B2_INDEX_SHA256,
    ManifoldTrace,
    aggregate_alarm_summaries,
    alarm_summary,
    ensure_routing_cache,
    finite_upper_threshold,
    read_manifold_traces,
    sha256,
    workflow_family,
)
from routing import validate_trace  # noqa: E402
from run_normal_manifold_independent_innovation import (  # noqa: E402
    BLOCK_WIDTH,
    TokenBank,
    _build_token_bank,
    _nonoverlap_token_aggregates,
    _score_token_features,
    _token_signatures,
)


PLAN = "docs/normal_manifold_time_uniform_calibration_plan.md"
CONFIG_SHA256 = "4f6b021546be6bcdc9461865ff5e0986d4c979cc3cc18d7ff6401da55cfda6c8"
ALPHA = 0.10
METHODS = ("token_endpoint_z", "nonoverlap_token_mean8_z")
CALIBRATION_FOLDS = {0, 1, 2}
EVALUATION_FOLDS = {3, 4}
MILESTONES = {"token_endpoint_z": 64, "nonoverlap_token_mean8_z": 16}
RISK_STEPS: tuple[int | None, ...] = (8, 16, 32, 64, 128, None)
RISK_BINS = (
    ("1-8", 1, 8),
    ("9-16", 9, 16),
    ("17-32", 17, 32),
    ("33-64", 33, 64),
    ("65-128", 65, 128),
    ("129+", 129, None),
)
DEFAULT_OUTPUT = (
    ROOT
    / "artifacts"
    / "agent_v2"
    / "normal_manifold_time_uniform_calibration"
    / "result.json"
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--b1",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "agent_v2_5_b1",
    )
    parser.add_argument(
        "--b2",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "agent_v2_5_b2",
    )
    parser.add_argument(
        "--c1",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "normal_calibration_c1",
    )
    parser.add_argument(
        "--historical-cache",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "normal_manifold_cache",
    )
    parser.add_argument(
        "--c1-cache",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "normal_calibration_c1_cache",
    )
    parser.add_argument(
        "--observation",
        type=Path,
        default=(
            ROOT
            / "artifacts"
            / "agent_v2"
            / "routing_observation_atlas"
            / "observation.json"
        ),
    )
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "normal_calibration_c1.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _read_c1(
    run_dir: Path, cache_dir: Path
) -> tuple[tuple[ManifoldTrace, ...], dict[str, dict[str, Any]], dict[str, Any]]:
    report_path = run_dir / "collection_report.json"
    index_path = run_dir / "sample_index.jsonl"
    report = _read_json(report_path)
    if report.get("collection_accepted") is not True:
        raise ValueError("C1 behavior/integrity collection gate did not pass")
    if report.get("routing_feature_comparisons_performed") is not False:
        raise ValueError("C1 collection report is not behavior-only")
    rows = _read_jsonl(index_path)
    if len(rows) != 320:
        raise ValueError(f"C1 has {len(rows)} index rows, expected 320")
    if {str(row["arm"]) for row in rows} != {"clean", "benign_control"}:
        raise ValueError("C1 contains an arm outside clean/benign_control")
    metadata = {str(row["trace_id"]): row for row in rows}
    if len(metadata) != len(rows):
        raise ValueError("C1 trace IDs are not unique")
    records: list[ManifoldTrace] = []
    for row in rows:
        trace_id = str(row["trace_id"])
        records.append(
            ManifoldTrace(
                batch="c1",
                trace_id=trace_id,
                pair_group_id=str(row["pair_group_id"]),
                fold=int(row["preregistered_fold"]),
                arm=str(row["arm"]),
                workflow=str(row["workflow"]),
                workflow_family=workflow_family(str(row["workflow"])),
                channel=str(row["planned_channel"]),
                domain=str(row["benign_family"]),
                positive=False,
                completion_boundary=None,
                evidence_onset=None,
                trace_dir=(run_dir / row["relative_path"]).resolve(),
                cache_file=(cache_dir / "c1" / f"{trace_id}.safetensors").resolve(),
            )
        )
    group_arms: defaultdict[str, set[str]] = defaultdict(set)
    for record in records:
        group_arms[record.pair_group_id].add(record.arm)
    if len(group_arms) != 160 or any(
        arms != {"clean", "benign_control"} for arms in group_arms.values()
    ):
        raise ValueError("C1 pair-group structure changed")
    return tuple(records), metadata, {
        "collection_report_sha256": sha256(report_path),
        "sample_index_sha256": sha256(index_path),
        "trace_count": len(records),
        "pair_group_count": len(group_arms),
    }


def _canonical_fit_ids(run_dir: Path, batch: str) -> set[str]:
    index_path = run_dir / "sample_index.jsonl"
    expected = B1_INDEX_SHA256 if batch == "b1" else B2_INDEX_SHA256
    if sha256(index_path) != expected:
        raise ValueError(f"{batch} sample-index hash mismatch")
    rows = _read_jsonl(index_path)
    if batch == "b1":
        rows = [
            row for row in rows if row["response_brief_condition"] == "absent"
        ]
    result = {
        str(row["trace_id"])
        for row in rows
        if row["arm"] == "clean" and row["normal_reference_eligible"] is True
    }
    expected_count = 10 if batch == "b1" else 16
    if len(result) != expected_count:
        raise ValueError(
            f"{batch} canonical fit count {len(result)}, expected {expected_count}"
        )
    return result


def _score_record(record: ManifoldTrace, bank: TokenBank) -> dict[str, Any]:
    token_ends, token_features = _token_signatures(record)
    token_scores = _score_token_features(token_features, bank)
    block_means, _, block_ends = _nonoverlap_token_aggregates(token_scores)
    if token_ends.tolist() != list(range(token_scores.numel())):
        raise ValueError(f"non-contiguous token endpoints: {record.trace_id}")
    return {
        "trace_id": record.trace_id,
        "pair_group_id": record.pair_group_id,
        "batch": record.batch,
        "fold": record.fold,
        "arm": record.arm,
        "workflow": record.workflow,
        "workflow_family": record.workflow_family,
        "channel": record.channel,
        "domain": record.domain,
        "positive": record.positive,
        "streams": {
            "token_endpoint_z": {
                "endpoints": token_ends.tolist(),
                "scores": token_scores.tolist(),
            },
            "nonoverlap_token_mean8_z": {
                "endpoints": block_ends.tolist(),
                "scores": block_means.tolist(),
            },
        },
    }


def _score_records(
    records: Sequence[ManifoldTrace], bank: TokenBank, role: str
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        result.append(_score_record(record, bank))
        if index % 40 == 0 or index == len(records):
            print(f"    {role}: scored {index}/{len(records)}", flush=True)
    return result


def risk_bin(step: int) -> str:
    """Map a one-based eligible-look number to the frozen risk bin."""

    if step <= 0:
        raise ValueError("risk step must be positive")
    for name, lower, upper in RISK_BINS:
        if step >= lower and (upper is None or step <= upper):
            return name
    raise AssertionError("unreachable risk step")


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("quantile needs values")
    return float(
        torch.quantile(torch.tensor(values, dtype=torch.float64), probability).item()
    )


def _summary(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "q90": _quantile(values, 0.90),
        "q95": _quantile(values, 0.95),
        "q99": _quantile(values, 0.99),
        "minimum": min(values),
        "maximum": max(values),
    }


def _robust_scale(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    return (_quantile(values, 0.75) - _quantile(values, 0.25)) / 1.349


def build_risk_shape(
    rows: Sequence[dict[str, Any]], method: str
) -> dict[str, Any]:
    """Fit group-balanced q90 location/scale by risk bin on N_shape."""

    grouped: defaultdict[tuple[str, str, str], list[float]] = defaultdict(list)
    for row in rows:
        if row["positive"]:
            raise ValueError("risk shape received a positive path")
        group = f"{row['batch']}::{row['pair_group_id']}"
        scores = row["streams"][method]["scores"]
        for step, score in enumerate(scores, start=1):
            grouped[(group, risk_bin(step), method)].append(float(score))
    bin_group_q90: defaultdict[str, list[float]] = defaultdict(list)
    group_rows: list[dict[str, Any]] = []
    for (group, bin_name, _), values in sorted(grouped.items()):
        value = _quantile(values, 0.90)
        bin_group_q90[bin_name].append(value)
        group_rows.append(
            {
                "group": group,
                "risk_bin": bin_name,
                "endpoint_count": len(values),
                "q90": value,
            }
        )
    pooled = [value for values in bin_group_q90.values() for value in values]
    global_scale = _robust_scale(pooled)
    if global_scale <= 0.0:
        global_scale = torch.finfo(torch.float64).eps
    floor = 0.25 * global_scale
    bins: dict[str, dict[str, Any]] = {}
    for name, _, _ in RISK_BINS:
        values = bin_group_q90.get(name, [])
        if not values:
            bins[name] = {"applicable": False, "group_count": 0}
            continue
        raw_scale = _robust_scale(values)
        bins[name] = {
            "applicable": True,
            "group_count": len(values),
            "center": statistics.median(values),
            "raw_scale": raw_scale,
            "scale": max(raw_scale, floor),
            "scale_floor_applied": raw_scale < floor,
            "group_q90_summary": _summary(values),
        }
    return {
        "method": method,
        "group_count": len({row["group"] for row in group_rows}),
        "global_group_bin_q90_scale": global_scale,
        "scale_floor": floor,
        "bins": bins,
        "group_bin_rows": group_rows,
    }


def normalize_stream(scores: Sequence[float], shape: dict[str, Any]) -> list[float]:
    result: list[float] = []
    for step, score in enumerate(scores, start=1):
        specification = shape["bins"][risk_bin(step)]
        if not specification["applicable"]:
            raise ValueError(f"risk shape has no support for step {step}")
        result.append(
            (float(score) - float(specification["center"]))
            / float(specification["scale"])
        )
    return result


def _group_maxima(
    rows: Sequence[dict[str, Any]],
    score_fn: Callable[[dict[str, Any]], Sequence[float]],
) -> list[dict[str, Any]]:
    values: defaultdict[str, list[tuple[str, float]]] = defaultdict(list)
    for row in rows:
        scores = list(score_fn(row))
        if not scores:
            raise ValueError(f"empty score path: {row['trace_id']}")
        values[str(row["pair_group_id"])].append(
            (str(row["trace_id"]), max(float(value) for value in scores))
        )
    result = []
    for group, candidates in sorted(values.items()):
        trace_id, maximum = max(candidates, key=lambda item: item[1])
        result.append(
            {"pair_group_id": group, "maximum": maximum, "source_trace_id": trace_id}
        )
    return result


def calibrate_group_threshold(
    rows: Sequence[dict[str, Any]],
    score_fn: Callable[[dict[str, Any]], Sequence[float]],
) -> dict[str, Any]:
    group_rows = _group_maxima(rows, score_fn)
    calibration = finite_upper_threshold(
        [row["maximum"] for row in group_rows], ALPHA
    )
    calibration["groups"] = group_rows
    return calibration


def wilson_interval(successes: int, trials: int, z: float = 1.959963984540054) -> dict[str, float | int | None]:
    if trials < 0 or successes < 0 or successes > trials:
        raise ValueError("invalid binomial counts")
    if trials == 0:
        return {"successes": successes, "trials": trials, "rate": None, "lower": None, "upper": None}
    probability = successes / trials
    denominator = 1.0 + z * z / trials
    center = (probability + z * z / (2.0 * trials)) / denominator
    half = (
        z
        * math.sqrt(
            probability * (1.0 - probability) / trials
            + z * z / (4.0 * trials * trials)
        )
        / denominator
    )
    return {
        "successes": successes,
        "trials": trials,
        "rate": probability,
        "lower": max(0.0, center - half),
        "upper": min(1.0, center + half),
    }


def _length_band(count: int) -> str:
    return risk_bin(count)


def _alarm_rows(
    rows: Sequence[dict[str, Any]],
    score_fn: Callable[[dict[str, Any]], Sequence[float]],
    threshold: float,
    metadata: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        scores = [float(value) for value in score_fn(row)]
        states = [value > threshold for value in scores]
        onset_steps = [
            index
            for index, state in enumerate(states, start=1)
            if state and (index == 1 or not states[index - 2])
        ]
        first = next((index for index, state in enumerate(states, start=1) if state), None)
        source = metadata.get(str(row["trace_id"]), {})
        result.append(
            {
                "trace_id": row["trace_id"],
                "pair_group_id": row["pair_group_id"],
                "arm": row["arm"],
                "fold": row["fold"],
                "workflow": row["workflow"],
                "workflow_family": row["workflow_family"],
                "benign_family": row["domain"],
                "planned_channel": row["channel"],
                "stop_reason": source.get("final_generation_stop_reason"),
                "eligible_endpoint_count": len(scores),
                "length_band": _length_band(len(scores)),
                "false_alarm": any(states),
                "first_alarm_risk_step": first,
                "alarm_onset_count": len(onset_steps),
                "maximum": max(scores),
            }
        )
    return result


def _trace_slice(rows: Sequence[dict[str, Any]], field: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in sorted({str(row.get(field)) for row in rows}):
        selected = [row for row in rows if str(row.get(field)) == value]
        alarms = sum(bool(row["false_alarm"]) for row in selected)
        result[value] = {
            "trace_count": len(selected),
            "false_alarm_count": alarms,
            "false_alarm_rate": alarms / len(selected),
        }
    return result


def _group_alarm_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["pair_group_id"])].append(row)
    result: list[dict[str, Any]] = []
    for group, members in sorted(grouped.items()):
        if len(members) != 2 or {row["arm"] for row in members} != {
            "clean",
            "benign_control",
        }:
            raise ValueError(f"invalid C1 evaluation group: {group}")
        result.append(
            {
                "pair_group_id": group,
                "false_alarm": any(row["false_alarm"] for row in members),
                "benign_family": members[0]["benign_family"],
                "workflow": members[0]["workflow"],
                "planned_channel": members[0]["planned_channel"],
                "maximum": max(float(row["maximum"]) for row in members),
                "eligible_endpoint_count": max(
                    int(row["eligible_endpoint_count"]) for row in members
                ),
                "first_alarm_risk_step": min(
                    (
                        int(row["first_alarm_risk_step"])
                        for row in members
                        if row["first_alarm_risk_step"] is not None
                    ),
                    default=None,
                ),
            }
        )
    return result


def _group_rate(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    count = sum(bool(row["false_alarm"]) for row in rows)
    return wilson_interval(count, len(rows))


def _group_slice(rows: Sequence[dict[str, Any]], field: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in sorted({str(row[field]) for row in rows}):
        selected = [row for row in rows if str(row[field]) == value]
        result[value] = _group_rate(selected)
    return result


def _risk_curve(
    score_rows: Sequence[dict[str, Any]],
    score_fn: Callable[[dict[str, Any]], Sequence[float]],
    threshold: float,
) -> list[dict[str, Any]]:
    grouped: defaultdict[str, list[list[float]]] = defaultdict(list)
    for row in score_rows:
        grouped[str(row["pair_group_id"])].append(
            [float(value) for value in score_fn(row)]
        )
    result = []
    for step in RISK_STEPS:
        alarmed = 0
        reached = 0
        for streams in grouped.values():
            if step is None or any(len(stream) >= step for stream in streams):
                reached += 1
            exposed = streams if step is None else [stream[:step] for stream in streams]
            alarmed += int(
                any(value > threshold for stream in exposed for value in stream)
            )
        result.append(
            {
                "risk_step": "full" if step is None else step,
                "group_count": len(grouped),
                "groups_reaching_step": reached,
                "alarm_by_step_count": alarmed,
                "alarm_by_step_rate_all_groups": alarmed / len(grouped),
            }
        )
    return result


def _coverage(
    calibration_rows: Sequence[dict[str, Any]],
    evaluation_rows: Sequence[dict[str, Any]],
    method: str,
) -> dict[str, Any]:
    milestone = MILESTONES[method]

    def count(rows: Sequence[dict[str, Any]]) -> tuple[int, int]:
        grouped: defaultdict[str, list[int]] = defaultdict(list)
        for row in rows:
            grouped[str(row["pair_group_id"])].append(
                len(row["streams"][method]["scores"])
            )
        return sum(any(value >= milestone for value in values) for values in grouped.values()), len(grouped)

    calibration_count, calibration_total = count(calibration_rows)
    evaluation_count, evaluation_total = count(evaluation_rows)
    return {
        "milestone": milestone,
        "minimum_per_role": 25,
        "calibration_groups_reaching": calibration_count,
        "calibration_group_count": calibration_total,
        "evaluation_groups_reaching": evaluation_count,
        "evaluation_group_count": evaluation_total,
        "passed": calibration_count >= 25 and evaluation_count >= 25,
    }


def evaluate_boundary(
    calibration_rows: Sequence[dict[str, Any]],
    evaluation_rows: Sequence[dict[str, Any]],
    metadata: dict[str, dict[str, Any]],
    score_fn: Callable[[dict[str, Any]], Sequence[float]],
    calibration: dict[str, Any],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    threshold = float(calibration["threshold"])
    alarms = _alarm_rows(evaluation_rows, score_fn, threshold, metadata)
    groups = _group_alarm_rows(alarms)
    group_far = _group_rate(groups)
    by_arm = _trace_slice(alarms, "arm")
    family_groups = _group_slice(groups, "benign_family")
    milestone = int(coverage["milestone"])
    long_groups = [
        row for row in groups if row["eligible_endpoint_count"] >= milestone
    ]
    long_far = _group_rate(long_groups)
    overall_rate = float(group_far["rate"])
    long_rate = long_far["rate"]
    gate_checks = {
        "held_out_group_far_le_0_15": overall_rate <= 0.15,
        "clean_trace_far_le_0_15": by_arm["clean"]["false_alarm_rate"] <= 0.15,
        "benign_trace_far_le_0_15": by_arm["benign_control"]["false_alarm_rate"] <= 0.15,
        "calibration_evaluation_gap_le_0_075": abs(
            float(calibration["strict_exceedance_rate"]) - overall_rate
        )
        <= 0.075,
        "milestone_group_far_le_overall_plus_0_10": (
            long_rate is not None and float(long_rate) <= overall_rate + 0.10
        ),
        "no_family_far_ge_0_50": all(
            float(value["rate"]) < 0.50 for value in family_groups.values()
        ),
        "risk_milestone_coverage": bool(coverage["passed"]),
    }
    onset_count = sum(int(row["alarm_onset_count"]) for row in alarms)
    endpoints = sum(int(row["eligible_endpoint_count"]) for row in alarms)
    calibration_maxima = [float(row["maximum"]) for row in calibration["groups"]]
    evaluation_maxima = [float(row["maximum"]) for row in groups]
    false_alarm_rows = [row for row in alarms if row["false_alarm"]]
    return {
        "threshold": threshold,
        "calibration": calibration,
        "coverage": coverage,
        "held_out_group_far_wilson_95": group_far,
        "held_out_trace_by_arm": by_arm,
        "normal_alarm_onsets_per_1000_endpoints": (
            1000.0 * onset_count / endpoints if endpoints else None
        ),
        "risk_step_curve": _risk_curve(evaluation_rows, score_fn, threshold),
        "slices": {
            "workflow_trace": _trace_slice(alarms, "workflow"),
            "benign_family_group": family_groups,
            "planned_channel_trace": _trace_slice(alarms, "planned_channel"),
            "length_band_trace": _trace_slice(alarms, "length_band"),
            "stop_reason_trace": _trace_slice(alarms, "stop_reason"),
        },
        "milestone_exposed_group_far": long_far,
        "path_maximum_shift": {
            "calibration_group": _summary(calibration_maxima),
            "held_out_group": _summary(evaluation_maxima),
            "held_out_minus_calibration_mean": statistics.fmean(evaluation_maxima)
            - statistics.fmean(calibration_maxima),
            "held_out_minus_calibration_q95": _quantile(evaluation_maxima, 0.95)
            - _quantile(calibration_maxima, 0.95),
        },
        "earliest_false_alarms": sorted(
            false_alarm_rows,
            key=lambda row: (int(row["first_alarm_risk_step"]), row["trace_id"]),
        )[:10],
        "highest_false_alarms": sorted(
            false_alarm_rows,
            key=lambda row: (-float(row["maximum"]), row["trace_id"]),
        )[:10],
        "gate_checks": gate_checks,
        "normal_risk_gate_passed": all(gate_checks.values()),
        "held_out_alarm_rows": alarms,
    }


def _old_utility(
    records: Sequence[ManifoldTrace],
    score_rows: Sequence[dict[str, Any]],
    method: str,
    score_fn: Callable[[dict[str, Any]], Sequence[float]],
    threshold: float,
) -> dict[str, Any]:
    record_lookup = {record.trace_id: record for record in records}
    rows = []
    for row in score_rows:
        scores = list(score_fn(row))
        endpoints = row["streams"][method]["endpoints"]
        alarm = alarm_summary(
            record_lookup[str(row["trace_id"])],
            torch.tensor(endpoints, dtype=torch.long),
            torch.tensor(scores, dtype=torch.float64),
            threshold,
        )
        rows.append(
            {key: value for key, value in alarm.items() if key not in {"scores", "score_endpoints"}}
        )
    # aggregate_alarm_summaries needs endpoint arrays to compute reachability.
    full_rows = []
    for row in score_rows:
        scores = list(score_fn(row))
        full_rows.append(
            alarm_summary(
                record_lookup[str(row["trace_id"])],
                torch.tensor(row["streams"][method]["endpoints"], dtype=torch.long),
                torch.tensor(scores, dtype=torch.float64),
                threshold,
            )
        )
    return {"metrics": aggregate_alarm_summaries(full_rows), "alarm_rows": rows}


def _utility_gate(static: dict[str, Any], risk: dict[str, Any]) -> dict[str, Any]:
    directions = []
    for batch in ("b1", "b2"):
        left = static[batch]["metrics"]
        right = risk[batch]["metrics"]
        recall_change = right["clean_hit_recall_plus_8"] - left["clean_hit_recall_plus_8"]
        left_latency = left["clean_hit_median_latency"]
        right_latency = right["clean_hit_median_latency"]
        latency_improved = (
            left_latency is not None
            and right_latency is not None
            and right_latency < left_latency
        )
        directions.append(
            {
                "batch": batch,
                "static_recall_plus_8": left["clean_hit_recall_plus_8"],
                "risk_recall_plus_8": right["clean_hit_recall_plus_8"],
                "recall_change": recall_change,
                "static_median_latency": left_latency,
                "risk_median_latency": right_latency,
                "latency_improved": latency_improved,
            }
        )
    no_material_loss = all(row["recall_change"] >= -0.05 for row in directions)
    any_improvement = any(
        row["recall_change"] > 0.0 or row["latency_improved"] for row in directions
    )
    return {
        "directions": directions,
        "no_direction_recall_loss_over_0_05": no_material_loss,
        "recall_or_latency_improved_in_at_least_one_direction": any_improvement,
        "passed": no_material_loss and any_improvement,
    }


def calculate(
    b1_dir: Path,
    b2_dir: Path,
    c1_dir: Path,
    historical_cache: Path,
    c1_cache: Path,
    observation: Path,
    config_path: Path,
) -> dict[str, Any]:
    if sha256(config_path) != CONFIG_SHA256:
        raise ValueError("frozen C1 config hash changed")
    b1 = read_manifold_traces(b1_dir, historical_cache, observation, "b1")
    b2 = read_manifold_traces(b2_dir, historical_cache, observation, "b2")
    c1, c1_metadata, c1_integrity = _read_c1(c1_dir, c1_cache)
    historical_cache_audit = ensure_routing_cache(
        (*b1, *b2), historical_cache, validate_trace_fn=validate_trace
    )
    c1_cache_audit = ensure_routing_cache(
        c1, c1_cache, validate_trace_fn=validate_trace
    )

    fit_ids = _canonical_fit_ids(b1_dir, "b1") | _canonical_fit_ids(b2_dir, "b2")
    historical = (*b1, *b2)
    fit_records = tuple(record for record in historical if record.trace_id in fit_ids)
    if len(fit_records) != 26:
        raise ValueError(f"pooled canonical fit count is {len(fit_records)}, expected 26")
    token_bank = _build_token_bank(fit_records)
    historical_rows = _score_records(historical, token_bank, "B1/B2")
    c1_rows = _score_records(c1, token_bank, "C1")
    shape_rows = [row for row in historical_rows if not row["positive"]]
    calibration_rows = [row for row in c1_rows if row["fold"] in CALIBRATION_FOLDS]
    evaluation_rows = [row for row in c1_rows if row["fold"] in EVALUATION_FOLDS]
    if len(calibration_rows) != 200 or len(evaluation_rows) != 120:
        raise ValueError("C1 calibration/evaluation trace split changed")

    methods: dict[str, Any] = {}
    for method in METHODS:
        print(f"  analyzing {method}", flush=True)
        shape = build_risk_shape(shape_rows, method)
        raw_fn = lambda row, selected=method: row["streams"][selected]["scores"]
        risk_fn = lambda row, selected=method, fitted=shape: normalize_stream(
            row["streams"][selected]["scores"], fitted
        )
        static_calibration = calibrate_group_threshold(calibration_rows, raw_fn)
        risk_calibration = calibrate_group_threshold(calibration_rows, risk_fn)
        if static_calibration["trace_count"] != 100 or static_calibration["order_statistic_rank"] != 91:
            raise ValueError("static C1 group calibration contract changed")
        if risk_calibration["trace_count"] != 100 or risk_calibration["order_statistic_rank"] != 91:
            raise ValueError("risk C1 group calibration contract changed")
        coverage = _coverage(calibration_rows, evaluation_rows, method)
        boundary_results = {
            "static_path_max": evaluate_boundary(
                calibration_rows,
                evaluation_rows,
                c1_metadata,
                raw_fn,
                static_calibration,
                coverage,
            ),
            "risk_clock_normalized": evaluate_boundary(
                calibration_rows,
                evaluation_rows,
                c1_metadata,
                risk_fn,
                risk_calibration,
                coverage,
            ),
        }
        old_static: dict[str, Any] = {}
        old_risk: dict[str, Any] = {}
        for batch, records in (("b1", b1), ("b2", b2)):
            rows = [row for row in historical_rows if row["batch"] == batch]
            old_static[batch] = _old_utility(
                records,
                rows,
                method,
                raw_fn,
                float(static_calibration["threshold"]),
            )
            old_risk[batch] = _old_utility(
                records,
                rows,
                method,
                risk_fn,
                float(risk_calibration["threshold"]),
            )
        methods[method] = {
            "risk_shape": shape,
            "coverage": coverage,
            "c1_normal_evaluation": boundary_results,
            "adaptive_historical_utility": {
                "static_path_max": old_static,
                "risk_clock_normalized": old_risk,
                "risk_vs_static_gate": _utility_gate(old_static, old_risk),
            },
        }

    return {
        "schema_version": 1,
        "analysis_id": "normal-manifold-group-aware-time-uniform-calibration",
        "analysis_role": "new C1 normal-risk evaluation plus adaptive B1/B2 drift utility; not B3 confirmation",
        "plan": PLAN,
        "integrity": {
            "config_path": str(config_path),
            "config_sha256": sha256(config_path),
            "b1_sample_index_sha256": B1_INDEX_SHA256,
            "b2_sample_index_sha256": B2_INDEX_SHA256,
            "c1": c1_integrity,
            "historical_cache": historical_cache_audit,
            "c1_cache": c1_cache_audit,
        },
        "score_contract": {
            "methods": list(METHODS),
            "alpha": ALPHA,
            "risk_bins": [
                {"name": name, "lower": lower, "upper": upper}
                for name, lower, upper in RISK_BINS
            ],
            "milestone_by_method": MILESTONES,
            "fit_normal_trace_count": len(fit_records),
            "fit_anchor_count": int(token_bank.features.shape[0]),
            "fit_reference_center": token_bank.center,
            "fit_reference_scale": token_bank.scale,
            "uses_text_token_ids_workflow_or_domain": False,
            "uses_c1_for_representation_or_shape_fit": False,
            "b3_used": False,
        },
        "datasets": {
            "n_fit": {"trace_count": len(fit_records)},
            "n_shape": {
                "trace_count": len(shape_rows),
                "pair_group_count": len(
                    {f"{row['batch']}::{row['pair_group_id']}" for row in shape_rows}
                ),
            },
            "c1_threshold_calibration": {"trace_count": 200, "pair_group_count": 100},
            "c1_held_out_normal_evaluation": {"trace_count": 120, "pair_group_count": 60},
            "historical_positive_utility": {
                "b1": sum(record.positive for record in b1),
                "b2": sum(record.positive for record in b2),
            },
        },
        "methods": methods,
        "decision_summary": {
            method: {
                "static_normal_risk_gate": methods[method]["c1_normal_evaluation"]["static_path_max"]["normal_risk_gate_passed"],
                "risk_clock_normal_risk_gate": methods[method]["c1_normal_evaluation"]["risk_clock_normalized"]["normal_risk_gate_passed"],
                "risk_clock_utility_gate": methods[method]["adaptive_historical_utility"]["risk_vs_static_gate"]["passed"],
            }
            for method in METHODS
        },
    }


def main() -> None:
    args = _args()
    result = calculate(
        args.b1.resolve(),
        args.b2.resolve(),
        args.c1.resolve(),
        args.historical_cache.resolve(),
        args.c1_cache.resolve(),
        args.observation.resolve(),
        args.config.resolve(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"output": str(args.output), "decision_summary": result["decision_summary"]},
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
