#!/usr/bin/env python3
"""Plot the finite-memory state/transition trajectory ablation."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULT = (
    ROOT
    / "artifacts"
    / "agent_v2"
    / "normal_manifold_trajectory_ablation"
    / "result.json"
)
DEFAULT_OUTPUT = DEFAULT_RESULT.parent / "trajectory_ablation_summary.png"
METHODS = (
    "state_endpoint_z",
    "transition_endpoint_z",
    "state_floor_4",
    "transition_floor_4",
    "joint_floor_4",
)
SHORT_METHODS = ("State", "Transition", "State floor", "Transition floor", "Joint")
STRATA = ("clean", "benign_control", "resisted_attack", "drift")
STRATUM_LABELS = ("Clean", "Benign", "Resisted", "Drift")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def _stratum(row: dict[str, Any]) -> str:
    if row["positive"]:
        return "drift"
    if row["arm"] == "attack":
        return "resisted_attack"
    return str(row["arm"])


def _path_maxima(
    direction: dict[str, Any], method: str
) -> dict[str, list[float]]:
    result: dict[str, list[float]] = defaultdict(list)
    for row in direction["target_trace_results"]:
        result[_stratum(row)].append(max(row["streams"][method]["scores"]))
    return result


def plot(result: dict[str, Any], output: Path) -> None:
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.lines import Line2D

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(2, 2, figsize=(14.5, 10.0))
    directions = (
        ("b1_to_b2", "B1 fit/cal → B2 target"),
        ("b2_to_b1", "B2 fit/cal → B1 target"),
    )
    colors = {"state_endpoint_z": "#4C78A8", "state_floor_4": "#F58518"}

    for axis, (direction_name, title) in zip(axes[0], directions, strict=True):
        direction = result["directions"][direction_name]
        positions = []
        values = []
        box_colors = []
        for index, stratum in enumerate(STRATA, start=1):
            for offset, method in ((-0.16, "state_endpoint_z"), (0.16, "state_floor_4")):
                positions.append(index + offset)
                values.append(_path_maxima(direction, method)[stratum])
                box_colors.append(colors[method])
        box = axis.boxplot(
            values,
            positions=positions,
            widths=0.26,
            patch_artist=True,
            showfliers=False,
        )
        for patch, color in zip(box["boxes"], box_colors, strict=True):
            patch.set_facecolor(color)
            patch.set_alpha(0.72)
        axis.set_xticks(range(1, len(STRATA) + 1), STRATUM_LABELS)
        axis.set_ylabel("Full-path maximum z")
        axis.set_title(title)
        axis.plot([], [], color=colors["state_endpoint_z"], linewidth=8, label="State")
        axis.plot([], [], color=colors["state_floor_4"], linewidth=8, label="State q25/4")
        axis.legend(fontsize=8)

    axis = axes[1, 0]
    x = np.arange(len(METHODS))
    width = 0.36
    for offset, (direction_name, title) in zip(
        (-width / 2, width / 2), directions, strict=True
    ):
        direction = result["directions"][direction_name]
        values = [
            direction["behavior_specificity"][method]["drift_vs_resisted_attack"][
                "auroc"
            ]
            for method in METHODS
        ]
        axis.bar(x + offset, values, width, label=title)
    axis.set_xticks(x, SHORT_METHODS, rotation=17, ha="right")
    axis.set_ylim(0.85, 1.0)
    axis.set_ylabel("Drift vs resisted path-max AUROC")
    axis.set_title("Finite-memory gains are small")
    axis.legend(fontsize=8)

    axis = axes[1, 1]
    markers = ("o", "s", "^", "D", "P")
    direction_colors = ("#54A24B", "#E45756")
    for (direction_name, title), color in zip(directions, direction_colors, strict=True):
        direction = result["directions"][direction_name]
        for method, label, marker in zip(METHODS, SHORT_METHODS, markers, strict=True):
            metrics = direction["stopping_diagnostics"][method]["pair_group_max"][
                "target_metrics"
            ]
            axis.scatter(
                metrics["non_drift_trace_false_alarm_rate"],
                metrics["clean_hit_recall_plus_8"],
                s=65,
                marker=marker,
                color=color,
                edgecolor="white",
                linewidth=0.7,
            )
    direction_handles = [
        Line2D([], [], marker="o", linestyle="", color=color, label=title)
        for (_, title), color in zip(directions, direction_colors, strict=True)
    ]
    method_handles = [
        Line2D(
            [],
            [],
            marker=marker,
            linestyle="",
            color="#666666",
            label=label,
        )
        for marker, label in zip(markers, SHORT_METHODS, strict=True)
    ]
    axis.set_xlabel("Pair-group calibrated normal trace FAR")
    axis.set_ylabel("Clean recall by onset +8")
    axis.set_title("No frozen stopping-rule winner")
    direction_legend = axis.legend(
        handles=direction_handles, fontsize=8, loc="upper left"
    )
    axis.add_artist(direction_legend)
    axis.legend(handles=method_handles, fontsize=7, loc="lower right", ncol=2)

    fig.suptitle("Finite-memory state/transition trajectory ablation", fontsize=15)
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
