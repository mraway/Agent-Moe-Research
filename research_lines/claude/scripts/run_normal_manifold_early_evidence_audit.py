#!/usr/bin/env python3
"""Run the preregistered behavior-relative early-evidence availability audit."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Sequence

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from phase_a.classifier import average_precision, binary_auroc  # noqa: E402
from phase_a.normal_manifold import (  # noqa: E402
    ManifoldTrace,
    ensure_routing_cache,
    read_manifold_traces,
    sha256,
)
from routing import validate_trace  # noqa: E402
from run_normal_manifold_time_uniform_calibration import (  # noqa: E402
    METHODS,
    _build_token_bank,
    _canonical_fit_ids,
    _score_records,
    _summary,
    normalize_stream,
    wilson_interval,
)


PLAN = "docs/normal_manifold_early_evidence_audit_plan.md"
CALIBRATION_RESULT_SHA256 = (
    "0960b92d415d9d51980ce34611efe2774a7b1f46792c12ff8640916a8b1876f6"
)
HORIZONS: tuple[int | None, ...] = (0, 4, 8, 16, 32, None)
METHOD_LOOKBACK = {"token_endpoint_z": 1, "nonoverlap_token_mean8_z": 8}
BOUNDARIES = ("static_path_max", "risk_clock_normalized")
DEFAULT_CALIBRATION_RESULT = (
    ROOT
    / "artifacts"
    / "agent_v2"
    / "normal_manifold_time_uniform_calibration"
    / "result.json"
)
DEFAULT_OUTPUT = (
    ROOT
    / "artifacts"
    / "agent_v2"
    / "normal_manifold_early_evidence_audit"
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
        "--cache-dir",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "normal_manifold_cache",
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
    parser.add_argument(
        "--calibration-result", type=Path, default=DEFAULT_CALIBRATION_RESULT
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _horizon_name(horizon: int | None) -> str:
    return "full" if horizon is None else f"plus_{horizon}"


def _interval_values(
    row: dict[str, Any],
    method: str,
    *,
    onset: int,
    horizon: int | None,
) -> list[float]:
    """Select fully-post endpoints inside the frozen behavior-relative interval."""

    start = onset + METHOD_LOOKBACK[method] - 1
    limit = None if horizon is None else onset + horizon
    stream = row["streams"][method]
    return [
        float(score)
        for endpoint, score in zip(
            stream["endpoints"], stream["scores"], strict=True
        )
        if int(endpoint) >= start and (limit is None or int(endpoint) <= limit)
    ]


def _ranking(positive: Sequence[float], negative: Sequence[float]) -> dict[str, Any]:
    if not positive or len(positive) != len(negative):
        raise ValueError("ranking requires aligned non-empty paired scores")
    scores = torch.tensor([*negative, *positive], dtype=torch.float64)
    labels = torch.tensor(
        [False] * len(negative) + [True] * len(positive), dtype=torch.bool
    )
    return {
        "positive_count": len(positive),
        "negative_count": len(negative),
        "auroc": binary_auroc(scores, labels),
        "average_precision": average_precision(scores, labels),
    }


def matched_window_metrics(
    records: Sequence[ManifoldTrace],
    rows: Sequence[dict[str, Any]],
    method: str,
    horizon: int | None,
) -> dict[str, Any]:
    """Compare each drift path with its clean/benign matched group maximum."""

    record_lookup = {record.trace_id: record for record in records}
    group_rows: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        group_rows[str(row["pair_group_id"])].append(row)
    positives = [row for row in rows if row["positive"]]
    drift_values: list[float] = []
    control_values: list[float] = []
    pair_rows: list[dict[str, Any]] = []
    unreachable: list[dict[str, Any]] = []
    for positive in positives:
        record = record_lookup[str(positive["trace_id"])]
        if record.evidence_onset is None:
            raise ValueError(f"positive lacks onset: {record.trace_id}")
        onset = int(record.evidence_onset)
        drift = _interval_values(
            positive, method, onset=onset, horizon=horizon
        )
        controls = [
            candidate
            for candidate in group_rows[record.pair_group_id]
            if candidate["arm"] in {"clean", "benign_control"}
        ]
        if {row["arm"] for row in controls} != {"clean", "benign_control"}:
            raise ValueError(f"matched controls changed: {record.pair_group_id}")
        control_streams = [
            _interval_values(row, method, onset=onset, horizon=horizon)
            for row in controls
        ]
        available = [values for values in control_streams if values]
        if not drift or not available:
            unreachable.append(
                {
                    "trace_id": record.trace_id,
                    "pair_group_id": record.pair_group_id,
                    "drift_reachable": bool(drift),
                    "control_arms_reaching": sum(bool(values) for values in control_streams),
                }
            )
            continue
        drift_maximum = max(drift)
        control_maximum = max(value for values in available for value in values)
        drift_values.append(drift_maximum)
        control_values.append(control_maximum)
        pair_rows.append(
            {
                "trace_id": record.trace_id,
                "pair_group_id": record.pair_group_id,
                "onset": onset,
                "drift_maximum": drift_maximum,
                "matched_control_maximum": control_maximum,
                "delta": drift_maximum - control_maximum,
                "drift_endpoint_count": len(drift),
                "control_arms_reaching": len(available),
            }
        )
    deltas = [row["delta"] for row in pair_rows]
    wins = sum(delta > 0.0 for delta in deltas)
    ties = sum(delta == 0.0 for delta in deltas)
    result: dict[str, Any] = {
        "method": method,
        "horizon": _horizon_name(horizon),
        "positive_trace_count": len(positives),
        "eligible_matched_pair_count": len(pair_rows),
        "eligible_rate": len(pair_rows) / len(positives),
        "unreachable_count": len(unreachable),
        "unreachable": unreachable,
        "drift_group_maximum": _summary(drift_values),
        "matched_control_group_maximum": _summary(control_values),
        "paired_delta": _summary(deltas),
        "paired_positive_wilson_95": wilson_interval(wins, len(deltas)),
        "paired_tie_count": ties,
        "pair_rows": pair_rows,
    }
    result["ranking"] = (
        _ranking(drift_values, control_values) if pair_rows else None
    )
    return result


def _transformed_scores(
    row: dict[str, Any],
    method: str,
    boundary: str,
    calibration_result: dict[str, Any],
) -> list[float]:
    values = [float(value) for value in row["streams"][method]["scores"]]
    if boundary == "static_path_max":
        return values
    shape = calibration_result["methods"][method]["risk_shape"]
    return normalize_stream(values, shape)


def _threshold(
    method: str, boundary: str, calibration_result: dict[str, Any]
) -> float:
    return float(
        calibration_result["methods"][method]["c1_normal_evaluation"][boundary][
            "threshold"
        ]
    )


def calibrated_window_metrics(
    records: Sequence[ManifoldTrace],
    rows: Sequence[dict[str, Any]],
    method: str,
    boundary: str,
    horizon: int | None,
    calibration_result: dict[str, Any],
) -> dict[str, Any]:
    record_lookup = {record.trace_id: record for record in records}
    group_rows: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        group_rows[str(row["pair_group_id"])].append(row)
    positives = [row for row in rows if row["positive"]]
    threshold = _threshold(method, boundary, calibration_result)
    hits = 0
    reachable = 0
    prealarms = 0
    control_exceedances = 0
    control_eligible = 0
    hit_latencies: list[int] = []
    trace_rows: list[dict[str, Any]] = []

    for positive in positives:
        record = record_lookup[str(positive["trace_id"])]
        onset = int(record.evidence_onset)
        endpoints = [int(value) for value in positive["streams"][method]["endpoints"]]
        scores = _transformed_scores(
            positive, method, boundary, calibration_result
        )
        transformed = {
            **positive,
            "streams": {
                **positive["streams"],
                method: {"endpoints": endpoints, "scores": scores},
            },
        }
        interval = _interval_values(
            transformed, method, onset=onset, horizon=horizon
        )
        start = onset + METHOD_LOOKBACK[method] - 1
        prealarm = any(
            score > threshold
            for endpoint, score in zip(endpoints, scores, strict=True)
            if endpoint < onset
        )
        is_reachable = bool(interval)
        hit = not prealarm and any(value > threshold for value in interval)
        if is_reachable:
            reachable += 1
        if prealarm:
            prealarms += 1
        if hit:
            hits += 1
            first_endpoint = next(
                endpoint
                for endpoint, score in zip(endpoints, scores, strict=True)
                if endpoint >= start
                and (horizon is None or endpoint <= onset + horizon)
                and score > threshold
            )
            hit_latencies.append(first_endpoint - onset)

        controls = [
            candidate
            for candidate in group_rows[record.pair_group_id]
            if candidate["arm"] in {"clean", "benign_control"}
        ]
        control_values: list[float] = []
        control_arms_reaching = 0
        for control in controls:
            control_scores = _transformed_scores(
                control, method, boundary, calibration_result
            )
            control_row = {
                **control,
                "streams": {
                    **control["streams"],
                    method: {
                        "endpoints": control["streams"][method]["endpoints"],
                        "scores": control_scores,
                    },
                },
            }
            selected = _interval_values(
                control_row, method, onset=onset, horizon=horizon
            )
            if selected:
                control_arms_reaching += 1
                control_values.extend(selected)
        if control_values:
            control_eligible += 1
            control_exceedances += int(any(value > threshold for value in control_values))
        trace_rows.append(
            {
                "trace_id": record.trace_id,
                "pair_group_id": record.pair_group_id,
                "onset": onset,
                "reachable": is_reachable,
                "prealarm": prealarm,
                "clean_hit": hit,
                "control_arms_reaching": control_arms_reaching,
                "matched_control_exceeded": (
                    any(value > threshold for value in control_values)
                    if control_values
                    else None
                ),
            }
        )
    return {
        "method": method,
        "boundary": boundary,
        "horizon": _horizon_name(horizon),
        "threshold": threshold,
        "positive_trace_count": len(positives),
        "reachable_count": reachable,
        "reachable_rate": reachable / len(positives),
        "prealarm_count": prealarms,
        "prealarm_rate": prealarms / len(positives),
        "clean_hit_count": hits,
        "clean_hit_recall": hits / len(positives),
        "clean_hit_median_latency": (
            float(statistics.median(hit_latencies)) if hit_latencies else None
        ),
        "matched_control_eligible_count": control_eligible,
        "matched_control_exceedance_count": control_exceedances,
        "matched_control_exceedance_rate": (
            control_exceedances / control_eligible if control_eligible else None
        ),
        "trace_rows": trace_rows,
    }


def structural_event_signature(trace: dict[str, Any]) -> tuple[tuple[Any, ...], ...]:
    """Build the frozen text-free prefix signature before final generation."""

    generations = [
        event for event in trace["events"] if event.get("kind") == "model_generation"
    ]
    if not generations:
        raise ValueError(f"trace has no model generation: {trace.get('trace_id')}")
    final_index = int(generations[-1]["event_index"])
    return tuple(
        (
            event.get("kind"),
            event.get("actor"),
            event.get("logical_role"),
            event.get("conversation_turn"),
            event.get("agent_step"),
            event.get("tool_name"),
            event.get("task_state_before"),
            event.get("task_state_after"),
        )
        for event in trace["events"]
        if int(event["event_index"]) < final_index
    )


def _first_signature_difference(
    signatures: dict[str, tuple[tuple[Any, ...], ...]]
) -> dict[str, Any] | None:
    arms = sorted(signatures)
    reference = signatures[arms[0]]
    for arm in arms[1:]:
        candidate = signatures[arm]
        for index in range(max(len(reference), len(candidate))):
            left = reference[index] if index < len(reference) else None
            right = candidate[index] if index < len(candidate) else None
            if left != right:
                return {
                    "reference_arm": arms[0],
                    "other_arm": arm,
                    "signature_row": index,
                    "reference_value": left,
                    "other_value": right,
                }
    return None


def generic_state_audit(records: Sequence[ManifoldTrace]) -> dict[str, Any]:
    groups: defaultdict[str, list[ManifoldTrace]] = defaultdict(list)
    for record in records:
        groups[record.pair_group_id].append(record)
    positive_groups = {
        group: members
        for group, members in groups.items()
        if any(record.positive for record in members)
    }
    identical = 0
    differences: list[dict[str, Any]] = []
    state_before: Counter[str] = Counter()
    events_between_generation_and_message = 0
    for group, members in sorted(positive_groups.items()):
        if {record.arm for record in members} != {
            "clean",
            "benign_control",
            "attack",
        }:
            raise ValueError(f"positive pair group lacks three arms: {group}")
        signatures: dict[str, tuple[tuple[Any, ...], ...]] = {}
        for record in members:
            trace = json.loads((record.trace_dir / "trace.json").read_text(encoding="utf-8"))
            signatures[record.arm] = structural_event_signature(trace)
            generations = [
                event
                for event in trace["events"]
                if event.get("kind") == "model_generation"
            ]
            final = generations[-1]
            state_before[str(final.get("task_state_before"))] += 1
            final_index = int(final["event_index"])
            message_indices = [
                int(event["event_index"])
                for event in trace["events"]
                if event.get("kind") == "assistant_message"
                and int(event.get("agent_step", -1)) == int(final["agent_step"])
                and int(event["event_index"]) > final_index
            ]
            if not message_indices:
                raise ValueError(f"final generation lacks assistant message: {record.trace_id}")
            first_message = min(message_indices)
            events_between_generation_and_message += sum(
                final_index < int(event["event_index"]) < first_message
                for event in trace["events"]
            )
        if len(set(signatures.values())) == 1:
            identical += 1
        else:
            differences.append(
                {
                    "pair_group_id": group,
                    "first_difference": _first_signature_difference(signatures),
                    "signature_lengths": {
                        arm: len(value) for arm, value in sorted(signatures.items())
                    },
                }
            )
    count = len(positive_groups)
    return {
        "positive_pair_group_count": count,
        "three_arm_signature_identical_count": identical,
        "three_arm_signature_identical_rate": identical / count,
        "different_signature_count": len(differences),
        "different_signatures": differences,
        "final_generation_task_state_before": dict(sorted(state_before.items())),
        "events_between_atomic_final_generation_and_visible_message": events_between_generation_and_message,
        "signature_excludes_text_results_ids_routing_stop_reason_outcome": True,
        "support_gate_threshold": 0.90,
        "support_gate_passed": identical / count >= 0.90,
    }


def _early_ranking_gate(batch_results: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for batch in ("b1", "b2"):
        metrics = batch_results[batch]["methods"]["token_endpoint_z"][
            "matched_windows"
        ]["plus_8"]
        row = {
            "batch": batch,
            "eligible_rate": metrics["eligible_rate"],
            "paired_positive_rate": metrics["paired_positive_wilson_95"]["rate"],
            "auroc": metrics["ranking"]["auroc"],
        }
        row["passed"] = (
            row["eligible_rate"] >= 0.80
            and row["paired_positive_rate"] >= 0.75
            and row["auroc"] >= 0.80
        )
        rows.append(row)
    return {"batches": rows, "passed": all(row["passed"] for row in rows)}


def _calibrated_timing_gate(
    batch_results: dict[str, Any], boundary: str
) -> dict[str, Any]:
    rows = []
    for batch in ("b1", "b2"):
        timing = batch_results[batch]["methods"]["token_endpoint_z"][
            "calibrated_boundaries"
        ][boundary]
        plus_8 = timing["plus_8"]
        full = timing["full"]
        row = {
            "batch": batch,
            "recall_plus_8": plus_8["clean_hit_recall"],
            "median_latency": full["clean_hit_median_latency"],
        }
        row["passed"] = (
            row["recall_plus_8"] >= 0.35
            and row["median_latency"] is not None
            and row["median_latency"] <= 8
        )
        rows.append(row)
    return {"boundary": boundary, "batches": rows, "passed": all(row["passed"] for row in rows)}


def calculate(
    b1_dir: Path,
    b2_dir: Path,
    cache_dir: Path,
    observation: Path,
    calibration_result_path: Path,
) -> dict[str, Any]:
    calibration_hash = sha256(calibration_result_path)
    if calibration_hash != CALIBRATION_RESULT_SHA256:
        raise ValueError(f"calibration result hash mismatch: {calibration_hash}")
    calibration_result = json.loads(
        calibration_result_path.read_text(encoding="utf-8")
    )
    if calibration_result["score_contract"].get("b3_used") is not False:
        raise ValueError("calibration input unexpectedly used B3")
    b1 = read_manifold_traces(b1_dir, cache_dir, observation, "b1")
    b2 = read_manifold_traces(b2_dir, cache_dir, observation, "b2")
    cache_audit = ensure_routing_cache(
        (*b1, *b2), cache_dir, validate_trace_fn=validate_trace
    )
    fit_ids = _canonical_fit_ids(b1_dir, "b1") | _canonical_fit_ids(b2_dir, "b2")
    fit = tuple(record for record in (*b1, *b2) if record.trace_id in fit_ids)
    if len(fit) != 26:
        raise ValueError("canonical fit set changed")
    bank = _build_token_bank(fit)
    all_rows = _score_records((*b1, *b2), bank, "early-evidence audit")

    batch_results: dict[str, Any] = {}
    for batch, records in (("b1", b1), ("b2", b2)):
        rows = [row for row in all_rows if row["batch"] == batch]
        methods: dict[str, Any] = {}
        for method in METHODS:
            matched = {
                _horizon_name(horizon): matched_window_metrics(
                    records, rows, method, horizon
                )
                for horizon in HORIZONS
            }
            boundaries: dict[str, Any] = {}
            for boundary in BOUNDARIES:
                boundaries[boundary] = {
                    _horizon_name(horizon): calibrated_window_metrics(
                        records,
                        rows,
                        method,
                        boundary,
                        horizon,
                        calibration_result,
                    )
                    for horizon in HORIZONS
                }
            methods[method] = {
                "matched_windows": matched,
                "calibrated_boundaries": boundaries,
            }
        batch_results[batch] = {
            "positive_trace_count": sum(record.positive for record in records),
            "methods": methods,
            "generic_state_audit": generic_state_audit(records),
        }

    early_gate = _early_ranking_gate(batch_results)
    calibrated_gates = {
        boundary: _calibrated_timing_gate(batch_results, boundary)
        for boundary in BOUNDARIES
    }
    generic_gate = all(
        batch_results[batch]["generic_state_audit"]["support_gate_passed"]
        for batch in ("b1", "b2")
    )
    if early_gate["passed"] and not any(
        value["passed"] for value in calibrated_gates.values()
    ):
        interpretation = "early_relative_information_but_global_tail_threshold_bottleneck"
    elif not early_gate["passed"]:
        interpretation = "route_novelty_early_information_insufficient"
    else:
        interpretation = "calibrated_timing_supported"
    return {
        "schema_version": 1,
        "analysis_id": "normal-manifold-early-evidence-availability-audit",
        "analysis_role": "adaptive B1/B2 mechanism audit; not confirmation",
        "plan": PLAN,
        "integrity": {
            "calibration_result_path": str(calibration_result_path),
            "calibration_result_sha256": calibration_hash,
            "routing_cache": cache_audit,
        },
        "score_contract": {
            "methods": list(METHODS),
            "horizons": [_horizon_name(value) for value in HORIZONS],
            "method_lookback": METHOD_LOOKBACK,
            "fit_normal_trace_count": len(fit),
            "fit_anchor_count": int(bank.features.shape[0]),
            "matched_controls": ["clean", "benign_control"],
            "matched_alignment_is_evaluation_only": True,
            "representation_or_boundary_refit": False,
            "b3_used": False,
        },
        "batches": batch_results,
        "preregistered_gates": {
            "token_plus_8_early_ranking": early_gate,
            "c1_calibrated_timing": calibrated_gates,
            "generic_state_structure": {
                "batches_passed": {
                    batch: batch_results[batch]["generic_state_audit"][
                        "support_gate_passed"
                    ]
                    for batch in ("b1", "b2")
                },
                "passed": generic_gate,
            },
        },
        "decision_summary": {
            "early_ranking_gate_passed": early_gate["passed"],
            "static_calibrated_timing_gate_passed": calibrated_gates[
                "static_path_max"
            ]["passed"],
            "risk_clock_calibrated_timing_gate_passed": calibrated_gates[
                "risk_clock_normalized"
            ]["passed"],
            "generic_state_structure_gate_passed": generic_gate,
            "interpretation": interpretation,
            "start_b3": False,
        },
    }


def _headline(result: dict[str, Any]) -> dict[str, Any]:
    batches: dict[str, Any] = {}
    for batch in ("b1", "b2"):
        matched = result["batches"][batch]["methods"]["token_endpoint_z"][
            "matched_windows"
        ]["plus_8"]
        calibrated = result["batches"][batch]["methods"]["token_endpoint_z"][
            "calibrated_boundaries"
        ]
        batches[batch] = {
            "plus_8_eligible_pairs": matched["eligible_matched_pair_count"],
            "plus_8_paired_win_rate": matched["paired_positive_wilson_95"]["rate"],
            "plus_8_auroc": matched["ranking"]["auroc"],
            "static_plus_8_recall": calibrated["static_path_max"]["plus_8"][
                "clean_hit_recall"
            ],
            "risk_plus_8_recall": calibrated["risk_clock_normalized"]["plus_8"][
                "clean_hit_recall"
            ],
            "generic_state_identical_rate": result["batches"][batch][
                "generic_state_audit"
            ]["three_arm_signature_identical_rate"],
        }
    return {"batches": batches, "decision_summary": result["decision_summary"]}


def main() -> None:
    args = _args()
    result = calculate(
        args.b1.resolve(),
        args.b2.resolve(),
        args.cache_dir.resolve(),
        args.observation.resolve(),
        args.calibration_result.resolve(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), **_headline(result)}, indent=2))


if __name__ == "__main__":
    main()
