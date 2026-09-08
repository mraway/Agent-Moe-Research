#!/usr/bin/env python3
"""Zoom into P1-LF normal false alarms and the CUSUM calibration outlier pair."""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from inspect_normal_manifold_p1_miss import _decode_token_rows, _snippet  # noqa: E402
from phase_a.normal_manifold import (  # noqa: E402
    ManifoldTrace,
    evenly_spaced_positions,
    load_cached_routing,
    read_manifold_traces,
    select_records,
)
from run_normal_manifold_p1_label_free import (  # noqa: E402
    BANDS,
    CALIBRATION_FOLDS,
    FIT_FOLDS,
    METHOD_ORDER,
    NEIGHBOR_K,
    PRIMARY_METHOD,
    REFERENCE_ANCHORS_PER_TRACE,
    WIDTH,
    LabelFreeBank,
    _build_bank,
    _history_streams,
    _pairwise_distance,
    _score_record,
    _signatures,
)


DEFAULT_RESULT = (
    ROOT / "artifacts" / "agent_v2" / "normal_manifold_p1_label_free" / "result.json"
)
DEFAULT_OBSERVATION = (
    ROOT / "artifacts" / "agent_v2" / "routing_observation_atlas" / "observation.json"
)
DEFAULT_OUTPUT = (
    ROOT / "artifacts" / "agent_v2" / "normal_manifold_p1_label_free_zoom"
)
OUTLIER_PAIR_ID = "b1-f4-048-order_and_knowledge-dialogue-scene"


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
    parser.add_argument("--observation", type=Path, default=DEFAULT_OBSERVATION)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"refusing to write empty CSV: {path}")
    fields = list(rows[0])
    if any(list(row) != fields for row in rows):
        raise ValueError(f"CSV rows have inconsistent fields: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _reference_metadata(
    records: Sequence[ManifoldTrace], bank: LabelFreeBank
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        ends, _ = _signatures(record)
        for position in evenly_spaced_positions(
            ends.numel(), REFERENCE_ANCHORS_PER_TRACE
        ):
            rows.append(
                {
                    "record": record,
                    "trace_id": record.trace_id,
                    "workflow": record.workflow,
                    "workflow_family": record.workflow_family,
                    "arm": record.arm,
                    "window_end": int(ends[position].item()),
                }
            )
    if len(rows) != bank.features.shape[0]:
        raise ValueError("reference metadata is not aligned with the label-free bank")
    for index, row in enumerate(rows):
        if (
            row["trace_id"] != bank.trace_ids[index]
            or row["window_end"] != int(bank.ends[index].item())
        ):
            raise ValueError("reference bank ordering changed")
    return rows


def _assert_base_matches_frozen(
    reconstructed: Any, frozen: dict[str, Any], trace_id: str
) -> None:
    comparisons = (
        (reconstructed.raw, frozen["raw_knn_scores"], "raw"),
        (reconstructed.local_median, frozen["local_age_medians"], "median"),
        (reconstructed.local_scale, frozen["local_age_scales"], "scale"),
        (reconstructed.standardized, frozen["standardized_scores"], "z"),
    )
    if reconstructed.ends.tolist() != frozen["endpoints"]:
        raise ValueError(f"endpoint reconstruction changed for {trace_id}")
    for tensor, expected, name in comparisons:
        if len(expected) != tensor.numel() or any(
            abs(float(left) - float(right)) > 1e-6
            for left, right in zip(tensor.tolist(), expected, strict=True)
        ):
            raise ValueError(f"{name} reconstruction changed for {trace_id}")


def _age_band(end: int) -> str:
    if end <= 31:
        return "7-31"
    if end <= 63:
        return "32-63"
    if end <= 127:
        return "64-127"
    return "128+"


def _length_band(length: int) -> str:
    if length <= 63:
        return "<=63"
    if length <= 127:
        return "64-127"
    if length < 192:
        return "128-191"
    return "192"


def _maximum_run(states: Iterable[bool]) -> int:
    maximum = 0
    current = 0
    for state in states:
        current = current + 1 if state else 0
        maximum = max(maximum, current)
    return maximum


def _generation(record: ManifoldTrace) -> dict[str, Any]:
    trace = _json(record.trace_dir / "trace.json")
    return [event for event in trace["events"] if event["kind"] == "model_generation"][-1]


def _index_rows(run_dir: Path) -> dict[str, dict[str, Any]]:
    rows = [
        json.loads(line)
        for line in (run_dir / "sample_index.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    return {str(row["trace_id"]): row for row in rows}


def _summary_counts(rows: Sequence[dict[str, Any]], field: str) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row[field])].append(row)
    return {
        value: {
            "trace_count": len(selected),
            "false_alarm_count": sum(bool(row["false_alarm"]) for row in selected),
            "false_alarm_rate": sum(bool(row["false_alarm"]) for row in selected)
            / len(selected),
        }
        for value, selected in sorted(grouped.items())
    }


def _escaped_newline_suffix(
    content: str, token_rows: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    match = re.search(r"(?:\\n)+$", content)
    if match is None:
        return {"sequence_count": 0, "token_start": None, "token_count": 0}
    reconstructed = "".join(str(row["token_text"]) for row in token_rows)
    if reconstructed != content and not reconstructed.startswith(content):
        raise ValueError("manifest token text does not preserve generation content")
    character_start = match.start()
    consumed = 0
    token_start = len(token_rows)
    for index, row in enumerate(token_rows):
        next_consumed = consumed + len(str(row["token_text"]))
        if next_consumed > character_start:
            token_start = index
            break
        consumed = next_consumed
    return {
        "sequence_count": len(match.group(0)) // 2,
        "token_start": token_start,
        "token_count": len(token_rows) - token_start,
    }


def _hazard_rows(
    records: Sequence[ManifoldTrace],
    bank: LabelFreeBank,
    threshold: float,
    cohort: str,
) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"endpoint_count": 0, "exceedance_count": 0, "traces": set(), "alarm_traces": set()}
    )
    for record in records:
        base = _score_record(record, bank)
        for end, value in zip(base.ends.tolist(), base.standardized.tolist(), strict=True):
            band = _age_band(int(end))
            counts[band]["endpoint_count"] += 1
            counts[band]["traces"].add(record.trace_id)
            if float(value) > threshold:
                counts[band]["exceedance_count"] += 1
                counts[band]["alarm_traces"].add(record.trace_id)
    rows = []
    for band in ("7-31", "32-63", "64-127", "128+"):
        values = counts[band]
        rows.append(
            {
                "cohort": cohort,
                "age_band": band,
                "endpoint_exceedance_count": values["exceedance_count"],
                "endpoint_count": values["endpoint_count"],
                "endpoint_exceedance_rate": values["exceedance_count"]
                / values["endpoint_count"],
                "alarm_trace_count": len(values["alarm_traces"]),
                "trace_count_reaching_band": len(values["traces"]),
                "alarm_trace_rate_reaching_band": len(values["alarm_traces"])
                / len(values["traces"]),
            }
        )
    return rows


def _target_hazard_rows(
    records: Sequence[ManifoldTrace],
    base_lookup: dict[str, dict[str, Any]],
    threshold: float,
) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"endpoint_count": 0, "exceedance_count": 0, "traces": set(), "alarm_traces": set()}
    )
    for record in records:
        frozen = base_lookup[record.trace_id]
        for end, value in zip(
            frozen["endpoints"], frozen["standardized_scores"], strict=True
        ):
            band = _age_band(int(end))
            counts[band]["endpoint_count"] += 1
            counts[band]["traces"].add(record.trace_id)
            if float(value) > threshold:
                counts[band]["exceedance_count"] += 1
                counts[band]["alarm_traces"].add(record.trace_id)
    rows = []
    for band in ("7-31", "32-63", "64-127", "128+"):
        values = counts[band]
        rows.append(
            {
                "cohort": "b1_target_normal",
                "age_band": band,
                "endpoint_exceedance_count": values["exceedance_count"],
                "endpoint_count": values["endpoint_count"],
                "endpoint_exceedance_rate": values["exceedance_count"]
                / values["endpoint_count"],
                "alarm_trace_count": len(values["alarm_traces"]),
                "trace_count_reaching_band": len(values["traces"]),
                "alarm_trace_rate_reaching_band": len(values["alarm_traces"])
                / len(values["traces"]),
            }
        )
    return rows


def _normal_target_rows(
    records: Sequence[ManifoldTrace],
    endpoint_lookup: dict[str, dict[str, Any]],
    index_lookup: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for record in records:
        result = endpoint_lookup[record.trace_id]
        generation = _generation(record)
        index = index_lookup[record.trace_id]
        rows.append(
            {
                "trace_id": record.trace_id,
                "pair_group_id": record.pair_group_id,
                "arm": record.arm,
                "workflow": record.workflow,
                "workflow_family": record.workflow_family,
                "decode_token_count": result["decode_token_count"],
                "length_band": _length_band(int(result["decode_token_count"])),
                "stop_reason": generation["stop_reason"],
                "original_task_completed": bool(index["original_task_completed"]),
                "normal_reference_eligible": bool(index["normal_reference_eligible"]),
                "false_alarm": bool(result["false_alarm"]),
            }
        )
    return rows


def _nearest_rows(
    record: ManifoldTrace,
    query_tokens: Sequence[dict[str, Any]],
    base: Any,
    distances: torch.Tensor,
    reference_metadata: Sequence[dict[str, Any]],
    reference_token_cache: dict[str, list[dict[str, Any]]],
    selected_positions: Sequence[int],
    reason: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for position in selected_positions:
        end = int(base.ends[position].item())
        for rank, reference_index in enumerate(
            base.neighbor_indices[position].tolist(), start=1
        ):
            anchor = reference_metadata[int(reference_index)]
            reference_record = anchor["record"]
            if reference_record.trace_id not in reference_token_cache:
                _, reference_token_cache[reference_record.trace_id] = _decode_token_rows(
                    reference_record
                )
            reference_tokens = reference_token_cache[reference_record.trace_id]
            reference_end = int(anchor["window_end"])
            rows.append(
                {
                    "reason": reason,
                    "query_trace_id": record.trace_id,
                    "query_arm": record.arm,
                    "query_workflow_family": record.workflow_family,
                    "query_end": end,
                    "query_z": float(base.standardized[position].item()),
                    "query_window_text": _snippet(
                        query_tokens, end - WIDTH + 1, end
                    ),
                    "rank": rank,
                    "distance": float(distances[position, int(reference_index)].item()),
                    "reference_trace_id": reference_record.trace_id,
                    "reference_arm": reference_record.arm,
                    "reference_workflow_family": reference_record.workflow_family,
                    "same_workflow_family": (
                        reference_record.workflow_family == record.workflow_family
                    ),
                    "reference_end": reference_end,
                    "reference_window_text": _snippet(
                        reference_tokens,
                        reference_end - WIDTH + 1,
                        reference_end,
                    ),
                }
            )
    return rows


def _plot_false_alarm_summary(
    hazard_rows: Sequence[dict[str, Any]],
    target_rows: Sequence[dict[str, Any]],
    false_alarm_rows: Sequence[dict[str, Any]],
    method_comparison: dict[str, Any],
    output: Path,
) -> None:
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(2, 2, figsize=(12, 8), constrained_layout=True)
    bands = ("7-31", "32-63", "64-127", "128+")
    for cohort, color in (("b2_source_calibration", "#4c78a8"), ("b1_target_normal", "#f58518")):
        values = [
            100
            * next(
                row["endpoint_exceedance_rate"]
                for row in hazard_rows
                if row["cohort"] == cohort and row["age_band"] == band
            )
            for band in bands
        ]
        axes[0, 0].plot(bands, values, marker="o", label=cohort, color=color)
    axes[0, 0].set_ylabel("Endpoint exceedance rate (%)")
    axes[0, 0].set_title("Late-decode hazard shifts at 128+")
    axes[0, 0].legend(frameon=False)
    axes[0, 0].grid(axis="y", alpha=0.2)

    length_summary = _summary_counts(target_rows, "length_band")
    length_bands = ("<=63", "64-127", "128-191", "192")
    axes[0, 1].bar(
        length_bands,
        [100 * length_summary[band]["false_alarm_rate"] for band in length_bands],
        color="#e45756",
    )
    for index, band in enumerate(length_bands):
        row = length_summary[band]
        axes[0, 1].text(
            index,
            100 * row["false_alarm_rate"] + 1,
            f'{row["false_alarm_count"]}/{row["trace_count"]}',
            ha="center",
        )
    axes[0, 1].set_ylabel("B1 normal trace FAR (%)")
    axes[0, 1].set_title("False alarms concentrate in long traces")
    axes[0, 1].grid(axis="y", alpha=0.2)

    arm_summary = _summary_counts(target_rows, "arm")
    arms = ("clean", "benign_control", "attack")
    axes[1, 0].bar(
        arms,
        [100 * arm_summary[arm]["false_alarm_rate"] for arm in arms],
        color=("#54a24b", "#eeca3b", "#b279a2"),
    )
    for index, arm in enumerate(arms):
        row = arm_summary[arm]
        axes[1, 0].text(
            index,
            100 * row["false_alarm_rate"] + 1,
            f'{row["false_alarm_count"]}/{row["trace_count"]}',
            ha="center",
        )
    axes[1, 0].set_ylabel("B1 normal trace FAR (%)")
    axes[1, 0].set_title("Attack exposure increases FAR but is not required")
    axes[1, 0].grid(axis="y", alpha=0.2)

    run_counts = Counter(row["run_class"] for row in false_alarm_rows)
    run_classes = ("singleton", "2-3", "4+")
    axes[1, 1].bar(
        run_classes,
        [run_counts[name] for name in run_classes],
        color="#72b7b2",
    )
    axes[1, 1].text(
        0.98,
        0.96,
        "Rolling-4 total FA: {}\nLeaky CUSUM total FA: {}".format(
            method_comparison["rolling_mean_4"]["false_alarm_count"],
            method_comparison[PRIMARY_METHOD]["false_alarm_count"],
        ),
        transform=axes[1, 1].transAxes,
        ha="right",
        va="top",
        color="#355c7d",
    )
    axes[1, 1].set_ylabel("Trace count")
    axes[1, 1].set_title("Most endpoint alarms are sustained, not singleton peaks")
    axes[1, 1].grid(axis="y", alpha=0.2)
    figure.savefig(output, dpi=180)
    plt.close(figure)


def _plot_trace_zoom(
    representative_timeline: Sequence[dict[str, Any]],
    representative: dict[str, Any],
    outlier_timeline: Sequence[dict[str, Any]],
    representative_endpoint_threshold: float,
    outlier_endpoint_threshold: float,
    leaky_threshold: float,
    output: Path,
) -> None:
    import matplotlib.pyplot as plt

    figure, axes = plt.subplots(3, 1, figsize=(13, 10), constrained_layout=True)
    x = [row["endpoint"] for row in representative_timeline]
    z = [row["endpoint_z"] for row in representative_timeline]
    axes[0].plot(x, z, color="#4c78a8", label="endpoint z")
    axes[0].axhline(
        representative_endpoint_threshold,
        color="#d62728",
        linestyle="--",
        label="threshold",
    )
    axes[0].axvspan(102, 114, color="#f58518", alpha=0.18, label="math-topic window")
    axes[0].scatter([representative["max_z_end"]], [representative["max_z"]], color="#d62728")
    axes[0].set_ylabel("z")
    axes[0].set_title(f'Representative false alarm: {representative["trace_id"]}')
    axes[0].legend(frameon=False, ncol=3)
    axes[0].grid(axis="y", alpha=0.2)

    colors = {"attack": "#b279a2", "benign_control": "#eeca3b", "clean": "#54a24b"}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in outlier_timeline:
        grouped[str(row["arm"])].append(row)
    for arm, rows in sorted(grouped.items()):
        axes[1].plot(
            [row["endpoint"] for row in rows],
            [row["endpoint_z"] for row in rows],
            color=colors[arm],
            label=arm,
        )
    axes[1].axhline(
        outlier_endpoint_threshold,
        color="#d62728",
        linestyle="--",
        label="endpoint threshold",
    )
    axes[1].set_ylabel("endpoint z")
    axes[1].set_title("CUSUM outlier pair: instantaneous distance")
    axes[1].legend(frameon=False, ncol=4)
    axes[1].grid(axis="y", alpha=0.2)

    for arm, rows in sorted(grouped.items()):
        axes[2].plot(
            [row["endpoint"] for row in rows],
            [row["leaky_cusum"] for row in rows],
            color=colors[arm],
            label=arm,
        )
    axes[2].axhline(leaky_threshold, color="#d62728", linestyle="--", label="calibrated threshold")
    axes[2].set_xlabel("Decode token endpoint")
    axes[2].set_ylabel("Leaky CUSUM")
    axes[2].set_title("The same normal pair occupies all three top calibration ranks")
    axes[2].legend(frameon=False, ncol=4)
    axes[2].grid(axis="y", alpha=0.2)
    figure.savefig(output, dpi=180)
    plt.close(figure)


def calculate(args: argparse.Namespace) -> dict[str, Any]:
    result = _json(args.result)
    if result.get("analysis_id") != "normal-manifold-p1-label-free-single-agent":
        raise ValueError("unexpected P1-LF result")
    b1 = read_manifold_traces(
        args.b1.resolve(), args.cache_dir.resolve(), args.observation.resolve(), "b1"
    )
    b2 = read_manifold_traces(
        args.b2.resolve(), args.cache_dir.resolve(), args.observation.resolve(), "b2"
    )
    b1_lookup = {record.trace_id: record for record in b1}
    b1_index = _index_rows(args.b1.resolve())

    reverse = result["directions"]["b2_to_b1"]
    endpoint_threshold = float(reverse["calibration"]["endpoint_z"]["threshold"])
    endpoint_lookup = {
        row["trace_id"]: row for row in reverse["trace_results"]["endpoint_z"]
    }
    base_lookup = {row["trace_id"]: row for row in reverse["base_trace_results"]}
    method_lookup = {
        method: {row["trace_id"]: row for row in reverse["trace_results"][method]}
        for method in METHOD_ORDER
    }
    b1_normal = select_records(b1, normal=True)
    normal_target_rows = _normal_target_rows(b1_normal, endpoint_lookup, b1_index)
    false_alarm_ids = {
        row["trace_id"] for row in normal_target_rows if row["false_alarm"]
    }
    if len(false_alarm_ids) != 21:
        raise ValueError(f"expected 21 reverse endpoint false alarms, found {len(false_alarm_ids)}")

    b2_fit = select_records(b2, normal=True, folds=FIT_FOLDS)
    b2_calibration = select_records(b2, normal=True, folds=CALIBRATION_FOLDS)
    b2_bank = _build_bank(b2_fit)
    b2_reference_metadata = _reference_metadata(b2_fit, b2_bank)
    hazard_rows = _hazard_rows(
        b2_calibration, b2_bank, endpoint_threshold, "b2_source_calibration"
    )
    hazard_rows.extend(
        _target_hazard_rows(b1_normal, base_lookup, endpoint_threshold)
    )

    reference_token_cache: dict[str, list[dict[str, Any]]] = {}
    false_alarm_rows: list[dict[str, Any]] = []
    false_alarm_timeline: list[dict[str, Any]] = []
    false_alarm_neighbors: list[dict[str, Any]] = []
    pair_counts = Counter(
        b1_lookup[trace_id].pair_group_id for trace_id in false_alarm_ids
    )
    for trace_id in sorted(false_alarm_ids):
        record = b1_lookup[trace_id]
        frozen = base_lookup[trace_id]
        base = _score_record(record, b2_bank)
        _assert_base_matches_frozen(base, frozen, trace_id)
        streams = _history_streams(base)
        query_ends, query_features = _signatures(record)
        if query_ends.tolist() != base.ends.tolist():
            raise ValueError("query feature endpoints changed")
        distances = _pairwise_distance(query_features, b2_bank.features, BANDS)
        trace, token_rows = _decode_token_rows(record)
        generation = [
            event for event in trace["events"] if event["kind"] == "model_generation"
        ][-1]
        maximum_position = int(base.standardized.argmax().item())
        over_positions = [
            index
            for index, value in enumerate(base.standardized.tolist())
            if float(value) > endpoint_threshold
        ]
        run = _maximum_run(
            float(value) > endpoint_threshold for value in base.standardized.tolist()
        )
        run_class = "singleton" if run == 1 else "2-3" if run <= 3 else "4+"
        maximum_end = int(base.ends[maximum_position].item())
        index = b1_index[trace_id]
        false_alarm_rows.append(
            {
                "trace_id": trace_id,
                "pair_group_id": record.pair_group_id,
                "pair_false_alarm_count": pair_counts[record.pair_group_id],
                "arm": record.arm,
                "workflow": record.workflow,
                "workflow_family": record.workflow_family,
                "decode_token_count": len(token_rows),
                "length_band": _length_band(len(token_rows)),
                "stop_reason": generation["stop_reason"],
                "original_task_completed": bool(index["original_task_completed"]),
                "normal_reference_eligible": bool(index["normal_reference_eligible"]),
                "false_alarm": True,
                "max_z": float(base.standardized[maximum_position].item()),
                "max_z_end": maximum_end,
                "raw_at_max_z": float(base.raw[maximum_position].item()),
                "local_median_at_max_z": float(base.local_median[maximum_position].item()),
                "local_scale_at_max_z": float(base.local_scale[maximum_position].item()),
                "first_alarm_end": min(
                    int(base.ends[position].item()) for position in over_positions
                ),
                "exceeding_endpoint_count": len(over_positions),
                "maximum_consecutive_exceedances": run,
                "run_class": run_class,
                "max_z_context": _snippet(token_rows, maximum_end - 12, maximum_end + 12),
                "rolling_mean_4_false_alarm": bool(
                    method_lookup["rolling_mean_4"][trace_id]["false_alarm"]
                ),
                "rolling_mean_8_false_alarm": bool(
                    method_lookup["rolling_mean_8"][trace_id]["false_alarm"]
                ),
                "cusum_false_alarm": bool(
                    method_lookup["cusum_0_5"][trace_id]["false_alarm"]
                ),
                "leaky_cusum_false_alarm": bool(
                    method_lookup[PRIMARY_METHOD][trace_id]["false_alarm"]
                ),
            }
        )
        stream_maps = {
            method: {
                int(end): float(value)
                for value, end in zip(scores.tolist(), ends.tolist(), strict=True)
            }
            for method, (scores, ends) in streams.items()
        }
        for position, end_value in enumerate(base.ends.tolist()):
            end = int(end_value)
            false_alarm_timeline.append(
                {
                    "trace_id": trace_id,
                    "arm": record.arm,
                    "workflow_family": record.workflow_family,
                    "endpoint": end,
                    "token_text": token_rows[end]["token_text"],
                    "window_text": _snippet(token_rows, end - WIDTH + 1, end),
                    "raw_knn": float(base.raw[position].item()),
                    "local_age_median": float(base.local_median[position].item()),
                    "local_age_scale": float(base.local_scale[position].item()),
                    "endpoint_z": float(base.standardized[position].item()),
                    "endpoint_alarm": float(base.standardized[position].item())
                    > endpoint_threshold,
                    "rolling_mean_4": stream_maps["rolling_mean_4"].get(end, ""),
                    "rolling_mean_8": stream_maps["rolling_mean_8"].get(end, ""),
                    "cusum": stream_maps["cusum_0_5"].get(end, ""),
                    "leaky_cusum": stream_maps[PRIMARY_METHOD].get(end, ""),
                }
            )
        false_alarm_neighbors.extend(
            _nearest_rows(
                record,
                token_rows,
                base,
                distances,
                b2_reference_metadata,
                reference_token_cache,
                over_positions,
                "reverse_false_alarm_exceedance",
            )
        )

    maxima = [float(row["max_z"]) for row in false_alarm_rows]
    maximum_median = float(statistics.median(maxima))
    representative = min(
        false_alarm_rows,
        key=lambda row: (abs(float(row["max_z"]) - maximum_median), row["trace_id"]),
    )
    representative_timeline = [
        row
        for row in false_alarm_timeline
        if row["trace_id"] == representative["trace_id"]
    ]
    representative_record = b1_lookup[representative["trace_id"]]
    representative_base = _score_record(representative_record, b2_bank)
    representative_ends, representative_features = _signatures(representative_record)
    representative_position = int(representative_base.standardized.argmax().item())
    kth_reference_index = int(
        representative_base.neighbor_indices[representative_position, -1].item()
    )
    affinity = (
        representative_features[representative_position]
        * b2_bank.features[kth_reference_index]
    ).sum(dim=1)
    layer_distances = (
        (1.0 - affinity.clamp(0.0, 1.0)).clamp_min(0.0).sqrt()
    )
    representative_routing = load_cached_routing(representative_record)
    representative_end = int(
        representative_ends[representative_position].item()
    )
    kth_anchor = b2_reference_metadata[kth_reference_index]
    kth_routing = load_cached_routing(kth_anchor["record"])
    kth_end = int(kth_anchor["window_end"])
    representative_layer_rows = []
    for layer in range(16):
        band = "early"
        if layer in BANDS[0]:
            band = "middle"
        elif layer in BANDS[1]:
            band = "late"
        representative_layer_rows.append(
            {
                "query_trace_id": representative_record.trace_id,
                "query_end": representative_end,
                "layer": layer,
                "band": band,
                "hellinger_to_kth_neighbor": float(layer_distances[layer].item()),
                "query_token_top8_experts": json.dumps(
                    [
                        int(value)
                        for value in representative_routing["top_k_ids"][
                            layer, representative_end
                        ].tolist()
                    ],
                    separators=(",", ":"),
                ),
                "query_window_distribution": json.dumps(
                    [
                        round(float(value), 7)
                        for value in representative_features[
                            representative_position, layer
                        ].square().tolist()
                    ],
                    separators=(",", ":"),
                ),
                "kth_reference_trace_id": kth_anchor["trace_id"],
                "kth_reference_end": kth_end,
                "kth_reference_token_top8_experts": json.dumps(
                    [
                        int(value)
                        for value in kth_routing["top_k_ids"][layer, kth_end].tolist()
                    ],
                    separators=(",", ":"),
                ),
                "kth_reference_window_distribution": json.dumps(
                    [
                        round(float(value), 7)
                        for value in b2_bank.features[
                            kth_reference_index, layer
                        ].square().tolist()
                    ],
                    separators=(",", ":"),
                ),
            }
        )

    b1_fit = select_records(b1, normal=True, folds=FIT_FOLDS)
    b1_bank = _build_bank(b1_fit)
    b1_reference_metadata = _reference_metadata(b1_fit, b1_bank)
    forward = result["directions"]["b1_to_b2"]
    forward_calibration_lookup = {
        method: {
            row["trace_id"]: float(row["maximum"])
            for row in forward["calibration"][method]["traces"]
        }
        for method in METHOD_ORDER
    }
    outlier_records = sorted(
        [record for record in b1 if record.pair_group_id == OUTLIER_PAIR_ID],
        key=lambda record: record.arm,
    )
    if len(outlier_records) != 3 or not all(record.normal for record in outlier_records):
        raise ValueError("CUSUM outlier pair is not the expected normal triplet")
    outlier_rows: list[dict[str, Any]] = []
    outlier_timeline: list[dict[str, Any]] = []
    outlier_neighbors: list[dict[str, Any]] = []
    b1_reference_token_cache: dict[str, list[dict[str, Any]]] = {}
    for record in outlier_records:
        base = _score_record(record, b1_bank)
        streams = _history_streams(base)
        ends, features = _signatures(record)
        distances = _pairwise_distance(features, b1_bank.features, BANDS)
        trace, token_rows = _decode_token_rows(record)
        generation = [
            event for event in trace["events"] if event["kind"] == "model_generation"
        ][-1]
        suffix = _escaped_newline_suffix(str(generation["content"]), token_rows)
        peaks: dict[str, dict[str, Any]] = {}
        selected_positions: set[int] = set()
        for method, (scores, method_ends) in streams.items():
            position = int(scores.argmax().item())
            end = int(method_ends[position].item())
            base_position = int((base.ends == end).nonzero(as_tuple=False)[0].item())
            selected_positions.add(base_position)
            maximum = float(scores[position].item())
            expected = forward_calibration_lookup[method][record.trace_id]
            if abs(maximum - expected) > 1e-6:
                raise ValueError(f"calibration maximum changed for {record.trace_id} / {method}")
            peaks[method] = {
                "maximum": maximum,
                "end": end,
                "token_text": token_rows[end]["token_text"],
                "context": _snippet(token_rows, end - 12, end + 12),
            }
        outlier_rows.append(
            {
                "trace_id": record.trace_id,
                "arm": record.arm,
                "decode_token_count": len(token_rows),
                "stop_reason": generation["stop_reason"],
                "escaped_newline_suffix_sequence_count": suffix["sequence_count"],
                "escaped_newline_suffix_token_start": suffix["token_start"],
                "escaped_newline_suffix_token_count": suffix["token_count"],
                "mean_endpoint_z": float(base.standardized.mean().item()),
                "endpoint_z_maximum": peaks["endpoint_z"]["maximum"],
                "endpoint_z_maximum_end": peaks["endpoint_z"]["end"],
                "cusum_maximum": peaks["cusum_0_5"]["maximum"],
                "cusum_maximum_end": peaks["cusum_0_5"]["end"],
                "leaky_cusum_maximum": peaks[PRIMARY_METHOD]["maximum"],
                "leaky_cusum_maximum_end": peaks[PRIMARY_METHOD]["end"],
                "leaky_cusum_maximum_context": peaks[PRIMARY_METHOD]["context"],
                "output": generation["content"],
            }
        )
        stream_maps = {
            method: {
                int(end): float(value)
                for value, end in zip(scores.tolist(), method_ends.tolist(), strict=True)
            }
            for method, (scores, method_ends) in streams.items()
        }
        for position, end_value in enumerate(base.ends.tolist()):
            end = int(end_value)
            outlier_timeline.append(
                {
                    "trace_id": record.trace_id,
                    "arm": record.arm,
                    "endpoint": end,
                    "token_text": token_rows[end]["token_text"],
                    "window_text": _snippet(token_rows, end - WIDTH + 1, end),
                    "raw_knn": float(base.raw[position].item()),
                    "local_age_median": float(base.local_median[position].item()),
                    "local_age_scale": float(base.local_scale[position].item()),
                    "endpoint_z": float(base.standardized[position].item()),
                    "endpoint_alarm": float(base.standardized[position].item())
                    > float(forward["calibration"]["endpoint_z"]["threshold"]),
                    "cusum": stream_maps["cusum_0_5"][end],
                    "leaky_cusum": stream_maps[PRIMARY_METHOD][end],
                }
            )
        outlier_neighbors.extend(
            _nearest_rows(
                record,
                token_rows,
                base,
                distances,
                b1_reference_metadata,
                b1_reference_token_cache,
                sorted(selected_positions),
                "forward_cusum_calibration_peak",
            )
        )

    endpoint_false_alarm_set = false_alarm_ids
    method_comparison: dict[str, Any] = {
        "endpoint_false_alarm_count": len(endpoint_false_alarm_set)
    }
    for method in METHOD_ORDER[1:]:
        method_false_alarms = {
            trace_id
            for trace_id, row in method_lookup[method].items()
            if not row["positive"] and row["false_alarm"]
        }
        method_comparison[method] = {
            "false_alarm_count": len(method_false_alarms),
            "overlap_with_endpoint": len(method_false_alarms & endpoint_false_alarm_set),
            "endpoint_false_alarms_resolved": len(
                endpoint_false_alarm_set - method_false_alarms
            ),
            "new_false_alarms": len(method_false_alarms - endpoint_false_alarm_set),
        }

    length_summary = _summary_counts(normal_target_rows, "length_band")
    arm_summary = _summary_counts(normal_target_rows, "arm")
    stop_summary = _summary_counts(normal_target_rows, "stop_reason")
    pair_without_outlier = [
        row for row in normal_target_rows if row["pair_group_id"] != OUTLIER_PAIR_ID
    ]
    false_alarm_run_counts = Counter(row["run_class"] for row in false_alarm_rows)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(args.output_dir / "normal_target_rows.csv", normal_target_rows)
    _write_csv(args.output_dir / "endpoint_hazard_by_age.csv", hazard_rows)
    _write_csv(args.output_dir / "false_alarms.csv", false_alarm_rows)
    _write_csv(args.output_dir / "false_alarm_timeline.csv", false_alarm_timeline)
    _write_csv(args.output_dir / "false_alarm_neighbors.csv", false_alarm_neighbors)
    _write_csv(
        args.output_dir / "representative_layer_routes.csv",
        representative_layer_rows,
    )
    _write_csv(args.output_dir / "cusum_outlier_pair.csv", outlier_rows)
    _write_csv(
        args.output_dir / "cusum_outlier_pair_timeline.csv", outlier_timeline
    )
    _write_csv(
        args.output_dir / "cusum_outlier_pair_neighbors.csv", outlier_neighbors
    )
    _plot_false_alarm_summary(
        hazard_rows,
        normal_target_rows,
        false_alarm_rows,
        method_comparison,
        args.output_dir / "p1_label_free_false_alarm_audit.png",
    )
    _plot_trace_zoom(
        representative_timeline,
        representative,
        outlier_timeline,
        endpoint_threshold,
        float(forward["calibration"]["endpoint_z"]["threshold"]),
        float(forward["calibration"][PRIMARY_METHOD]["threshold"]),
        args.output_dir / "p1_label_free_trace_zoom.png",
    )
    summary = {
        "analysis_id": "normal-manifold-p1-label-free-zoom-audit",
        "source_result": str(args.result),
        "reverse_endpoint_false_alarms": {
            "count": len(false_alarm_rows),
            "normal_trace_count": len(normal_target_rows),
            "pair_group_count": len(pair_counts),
            "threshold": endpoint_threshold,
            "by_length": length_summary,
            "by_arm": arm_summary,
            "by_stop_reason": stop_summary,
            "run_class_counts": dict(false_alarm_run_counts),
            "method_comparison": method_comparison,
            "without_outlier_pair": {
                "false_alarm_count": sum(
                    bool(row["false_alarm"]) for row in pair_without_outlier
                ),
                "trace_count": len(pair_without_outlier),
                "false_alarm_rate": sum(
                    bool(row["false_alarm"]) for row in pair_without_outlier
                )
                / len(pair_without_outlier),
            },
            "hazard_by_age": hazard_rows,
            "representative_selection": {
                "rule": "Among the 21 endpoint-z false alarms, choose maximum z closest to their median, tie-break by trace_id.",
                "candidate_maximum_median": maximum_median,
                "selected": representative,
                "selected_middle_layer_mean_distance": float(
                    layer_distances[list(BANDS[0])].mean().item()
                ),
                "selected_late_layer_mean_distance": float(
                    layer_distances[list(BANDS[1])].mean().item()
                ),
                "kth_reference_trace_id": kth_anchor["trace_id"],
                "kth_reference_end": kth_end,
            },
        },
        "forward_cusum_outlier_pair": {
            "pair_group_id": OUTLIER_PAIR_ID,
            "trace_count": len(outlier_rows),
            "endpoint_threshold": float(
                forward["calibration"]["endpoint_z"]["threshold"]
            ),
            "cusum_threshold": float(
                forward["calibration"]["cusum_0_5"]["threshold"]
            ),
            "leaky_cusum_threshold": float(
                forward["calibration"][PRIMARY_METHOD]["threshold"]
            ),
            "traces": outlier_rows,
        },
        "artifacts": {
            "normal_target_rows": "normal_target_rows.csv",
            "endpoint_hazard_by_age": "endpoint_hazard_by_age.csv",
            "false_alarms": "false_alarms.csv",
            "false_alarm_timeline": "false_alarm_timeline.csv",
            "false_alarm_neighbors": "false_alarm_neighbors.csv",
            "representative_layer_routes": "representative_layer_routes.csv",
            "cusum_outlier_pair": "cusum_outlier_pair.csv",
            "cusum_outlier_pair_timeline": "cusum_outlier_pair_timeline.csv",
            "cusum_outlier_pair_neighbors": "cusum_outlier_pair_neighbors.csv",
            "false_alarm_figure": "p1_label_free_false_alarm_audit.png",
            "trace_zoom_figure": "p1_label_free_trace_zoom.png",
        },
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    args = _args()
    summary = calculate(args)
    compact = {
        "output_dir": str(args.output_dir),
        "false_alarm_count": summary["reverse_endpoint_false_alarms"]["count"],
        "false_alarm_pairs": summary["reverse_endpoint_false_alarms"][
            "pair_group_count"
        ],
        "representative": summary["reverse_endpoint_false_alarms"][
            "representative_selection"
        ]["selected"]["trace_id"],
        "outlier_pair": summary["forward_cusum_outlier_pair"]["pair_group_id"],
    }
    print(json.dumps(compact, indent=2))


if __name__ == "__main__":
    main()
