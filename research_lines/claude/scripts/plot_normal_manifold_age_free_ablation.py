#!/usr/bin/env python3
"""Plot the absolute-age-free normal-manifold ablation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULT = (
    ROOT
    / "artifacts"
    / "agent_v2"
    / "normal_manifold_age_free_ablation"
    / "result.json"
)
DEFAULT_OUTPUT = DEFAULT_RESULT.parent / "age_free_ablation_summary.png"
SCORES = ("raw_knn", "global_robust_z", "local_density_z")
SCORE_LABELS = ("Raw kNN", "Global robust z", "Local-density z")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _phase_means(direction: dict[str, Any]) -> tuple[list[list[float]], list[str]]:
    phases = ("normal", "pre", "transition", "fully post")
    values: dict[str, list[float]] = {phase: [] for phase in phases}
    for row in direction["target_trace_results"]:
        scores = row["scores"]["global_robust_z"]
        ends = row["endpoints"]
        if not row["positive"]:
            values["normal"].append(sum(scores) / len(scores))
            continue
        onset = int(row["evidence_onset"])
        buckets = {"pre": [], "transition": [], "fully post": []}
        for end, score in zip(ends, scores, strict=True):
            if end < onset:
                buckets["pre"].append(score)
            elif end < onset + 7:
                buckets["transition"].append(score)
            else:
                buckets["fully post"].append(score)
        for phase, current in buckets.items():
            if current:
                values[phase].append(sum(current) / len(current))
    return [values[phase] for phase in phases], list(phases)


def plot(result: dict[str, Any], output: Path) -> None:
    import matplotlib.pyplot as plt
    import numpy as np

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 9.5))
    directions = (
        ("b1_to_b2", "B1 fit/cal -> B2 target"),
        ("b2_to_b1", "B2 fit/cal -> B1 target"),
    )
    colors = ("#4C78A8", "#F2CF5B", "#F58518", "#E45756")
    for axis, (direction_name, title) in zip(axes[0], directions, strict=True):
        values, labels = _phase_means(result["directions"][direction_name])
        box = axis.boxplot(
            values, tick_labels=labels, patch_artist=True, showfliers=False
        )
        for patch, color in zip(box["boxes"], colors, strict=True):
            patch.set_facecolor(color)
            patch.set_alpha(0.75)
        for index, current in enumerate(values, start=1):
            jitter = np.linspace(-0.12, 0.12, len(current)) if current else []
            axis.scatter(
                index + jitter,
                current,
                s=11,
                alpha=0.45,
                color=colors[index - 1],
                edgecolors="none",
            )
        axis.axhline(0.0, color="#555555", linewidth=0.8)
        axis.set_title(f"{title}: trace-balanced global z")
        axis.set_ylabel("Mean age-free score")

    axis = axes[1, 0]
    x = np.arange(len(SCORES))
    width = 0.34
    for offset, (direction_name, title) in zip(
        (-width / 2, width / 2), directions, strict=True
    ):
        metrics = result["directions"][direction_name]["representation_metrics"]
        values = [
            metrics[score]["normal_vs_fully_post_trace_mean_ranking"]["auroc"]
            for score in SCORES
        ]
        axis.bar(x + offset, values, width, label=title)
    axis.set_xticks(x, SCORE_LABELS, rotation=15, ha="right")
    axis.set_ylim(0.5, 1.02)
    axis.set_ylabel("Normal vs fully-post trace-mean AUROC")
    axis.set_title("Representation signal survives without absolute age")
    axis.legend(fontsize=8)

    axis = axes[1, 1]
    metric_names = ("non_drift_trace_false_alarm_rate", "clean_hit_recall_plus_8", "clean_hit_recall_full")
    metric_labels = ("Normal FAR", "Recall +8", "Full recall")
    x = np.arange(len(metric_names))
    width = 0.18
    series = (
        ("b1_to_b2", "raw_knn", "B1->B2 raw"),
        ("b2_to_b1", "raw_knn", "B2->B1 raw"),
        ("b1_to_b2", "local_density_z", "B1->B2 local"),
        ("b2_to_b1", "local_density_z", "B2->B1 local"),
    )
    for index, (direction_name, score_name, label) in enumerate(series):
        metrics = result["directions"][direction_name]["stopping_diagnostics"][
            score_name
        ]["legacy_trace_max"]["target_metrics"]
        values = [metrics[name] for name in metric_names]
        axis.bar(x + (index - 1.5) * width, values, width, label=label)
    axis.set_xticks(x, metric_labels)
    axis.set_ylim(0.0, 0.9)
    axis.set_ylabel("Rate")
    axis.set_title("Endpoint stopping is a secondary stress diagnostic")
    axis.legend(fontsize=8)

    fig.suptitle("Absolute-age-free normal-manifold ablation", fontsize=15)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=180, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    args = _args()
    result = json.loads(args.result.read_text(encoding="utf-8"))
    plot(result, args.output)
    print(args.output)


if __name__ == "__main__":
    main()
