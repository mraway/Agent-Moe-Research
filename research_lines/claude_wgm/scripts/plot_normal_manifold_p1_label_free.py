#!/usr/bin/env python3
"""Create compact tables and a figure for the P1-LF result."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULT = (
    ROOT / "artifacts" / "agent_v2" / "normal_manifold_p1_label_free" / "result.json"
)
DEFAULT_OUTPUT = ROOT / "artifacts" / "agent_v2" / "normal_manifold_p1_label_free"
METHOD_LABELS = {
    "endpoint_z": "Endpoint z",
    "rolling_mean_4": "Rolling mean 4",
    "rolling_mean_8": "Rolling mean 8",
    "cusum_0_5": "CUSUM",
    "leaky_cusum_0_95_0_5": "Leaky CUSUM",
}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _phase_values(direction: dict, phase: str) -> list[float]:
    values: list[float] = []
    for row in direction["base_trace_results"]:
        onset = row["evidence_onset"]
        for end, value in zip(
            row["endpoints"], row["standardized_scores"], strict=True
        ):
            if phase == "normal" and not row["positive"]:
                values.append(float(value))
            elif phase == "pre" and row["positive"] and end < onset:
                values.append(float(value))
            elif phase == "post" and row["positive"] and end >= onset:
                values.append(float(value))
    return values


def _write_tables(payload: dict, output_dir: Path) -> None:
    method_path = output_dir / "method_metrics.csv"
    with method_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "direction",
                "method",
                "threshold",
                "normal_false_alarms",
                "normal_traces",
                "normal_far",
                "pre_onset_alarms",
                "positive_traces",
                "recall_plus_4",
                "recall_plus_8",
                "recall_plus_16",
                "recall_full",
                "median_latency",
            ),
        )
        writer.writeheader()
        for direction_name, direction in payload["directions"].items():
            for method in payload["detector"]["methods"]:
                metrics = direction["metrics"][method]
                writer.writerow(
                    {
                        "direction": direction_name,
                        "method": method,
                        "threshold": direction["calibration"][method]["threshold"],
                        "normal_false_alarms": metrics["non_drift_false_alarm_count"],
                        "normal_traces": metrics["non_drift_trace_count"],
                        "normal_far": metrics["non_drift_trace_false_alarm_rate"],
                        "pre_onset_alarms": metrics["drift_pre_onset_alarm_count"],
                        "positive_traces": metrics["positive_trace_count"],
                        "recall_plus_4": metrics["clean_hit_recall_plus_4"],
                        "recall_plus_8": metrics["clean_hit_recall_plus_8"],
                        "recall_plus_16": metrics["clean_hit_recall_plus_16"],
                        "recall_full": metrics["clean_hit_recall_full"],
                        "median_latency": metrics["clean_hit_median_latency"],
                    }
                )

    phase_path = output_dir / "score_phase_summary.csv"
    with phase_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("direction", "phase", "count", "mean", "q50", "q90", "q95"),
        )
        writer.writeheader()
        for direction_name, direction in payload["directions"].items():
            for phase, summary in direction["neighbor_audit"][
                "standardized_score_by_phase"
            ].items():
                writer.writerow({"direction": direction_name, "phase": phase, **summary})


def _metric_panel(axis: plt.Axes, direction: dict, title: str) -> None:
    methods = list(METHOD_LABELS)
    x = np.arange(len(methods))
    width = 0.25
    far = [
        direction["metrics"][method]["non_drift_trace_false_alarm_rate"]
        for method in methods
    ]
    plus8 = [
        direction["metrics"][method]["clean_hit_recall_plus_8"]
        for method in methods
    ]
    full = [
        direction["metrics"][method]["clean_hit_recall_full"] for method in methods
    ]
    axis.bar(x - width, far, width, label="Normal FAR", color="#d95f5f")
    axis.bar(x, plus8, width, label="Drift recall +8", color="#e0a63b")
    axis.bar(x + width, full, width, label="Drift recall full", color="#3977a8")
    axis.axhline(0.15, color="#d95f5f", linestyle=":", linewidth=1)
    axis.axhline(0.35, color="#e0a63b", linestyle=":", linewidth=1)
    axis.set_xticks(
        x, [METHOD_LABELS[method] for method in methods], rotation=25, ha="right"
    )
    axis.set_ylim(0, 1.0)
    axis.set_ylabel("Trace-level rate")
    axis.set_title(title)
    axis.grid(axis="y", alpha=0.2)


def main() -> None:
    args = _args()
    payload = json.loads(args.result.read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_tables(payload, args.output_dir)

    figure, axes = plt.subplots(2, 2, figsize=(13, 9), constrained_layout=True)
    _metric_panel(
        axes[0, 0],
        payload["directions"]["b1_to_b2"],
        "B1 fit/calibration -> B2 target",
    )
    _metric_panel(
        axes[0, 1],
        payload["directions"]["b2_to_b1"],
        "B2 fit/calibration -> B1 target",
    )
    axes[0, 0].legend(loc="upper left", ncol=3, frameon=False, fontsize=9)

    positions = []
    datasets = []
    tick_labels = []
    colors = []
    palette = {"normal": "#4c78a8", "pre": "#72b7b2", "post": "#f58518"}
    position = 1
    for direction_name in ("b1_to_b2", "b2_to_b1"):
        direction = payload["directions"][direction_name]
        for phase in ("normal", "pre", "post"):
            datasets.append(_phase_values(direction, phase))
            positions.append(position)
            tick_labels.append(
                ("B1 -> B2\n" if direction_name == "b1_to_b2" else "B2 -> B1\n")
                + phase
                if phase == "normal"
                else phase
            )
            colors.append(palette[phase])
            position += 1
        position += 1
    violins = axes[1, 0].violinplot(
        datasets, positions=positions, showmeans=True, showextrema=False, widths=0.8
    )
    for body, color in zip(violins["bodies"], colors, strict=True):
        body.set_facecolor(color)
        body.set_edgecolor("black")
        body.set_alpha(0.75)
    axes[1, 0].set_xticks(positions, tick_labels)
    axes[1, 0].set_ylim(-3.5, 8.0)
    axes[1, 0].set_ylabel("Label-free standardized kNN distance z")
    axes[1, 0].set_title("Window-score distributions separate after drift onset")
    axes[1, 0].grid(axis="y", alpha=0.2)

    names = ("b1_to_b2", "b2_to_b1")
    audit = [payload["directions"][name]["neighbor_audit"] for name in names]
    x = np.arange(2)
    axes[1, 1].bar(
        x - 0.18,
        [row["random_bank_draw_same_workflow_family_rate"] for row in audit],
        0.36,
        label="Random bank draw",
        color="#bbbbbb",
    )
    axes[1, 1].bar(
        x + 0.18,
        [row["top5_same_workflow_family_rate"] for row in audit],
        0.36,
        label="Observed top-5 neighbors",
        color="#54a24b",
    )
    axes[1, 1].set_xticks(x, ["B1 -> B2", "B2 -> B1"])
    axes[1, 1].set_ylim(0, 0.7)
    axes[1, 1].set_ylabel("Same-family fraction (audit only)")
    axes[1, 1].set_title("Unsupervised neighborhoods are structured but mixed")
    axes[1, 1].legend(frameon=False)
    axes[1, 1].grid(axis="y", alpha=0.2)

    output = args.output_dir / "p1_label_free_summary.png"
    figure.savefig(output, dpi=180)
    plt.close(figure)
    print(
        json.dumps(
            {
                "figure": str(output),
                "tables": [
                    str(args.output_dir / "method_metrics.csv"),
                    str(args.output_dir / "score_phase_summary.csv"),
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
