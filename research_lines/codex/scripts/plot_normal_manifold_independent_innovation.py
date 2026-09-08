#!/usr/bin/env python3
"""Plot the independent routing-innovation experiment."""

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
    / "normal_manifold_independent_innovation"
    / "result.json"
)
DEFAULT_OUTPUT = DEFAULT_RESULT.parent / "independent_innovation_summary.png"
METHODS = (
    "sliding_state_z",
    "nonoverlap_state_z",
    "staggered_state_current_z",
    "staggered_state_min2_z",
    "token_endpoint_z",
    "nonoverlap_token_mean8_z",
    "nonoverlap_token_q25_8_z",
)
SHORT_METHODS = (
    "Sliding\nstate",
    "Non-overlap\nstate",
    "Staggered\ncurrent",
    "Staggered\nmin-2",
    "Token\ninnovation",
    "Token block\nmean",
    "Token block\nq25",
)
DIRECTIONS = (
    ("b1_to_b2", "B1 fit/cal → B2 target"),
    ("b2_to_b1", "B2 fit/cal → B1 target"),
)


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def plot(result: dict[str, Any], output: Path) -> None:
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.lines import Line2D

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(2, 2, figsize=(15.0, 10.0))
    x = np.arange(len(METHODS))
    width = 0.36
    colors = ("#4C78A8", "#F58518")

    for offset, (direction_name, label), color in zip(
        (-width / 2, width / 2), DIRECTIONS, colors, strict=True
    ):
        direction = result["directions"][direction_name]
        aucs = [
            direction["representation_metrics"][method][
                "normal_vs_fully_post_trace_mean_ranking"
            ]["auroc"]
            for method in METHODS
        ]
        axes[0, 0].bar(x + offset, aucs, width, label=label, color=color)
        behavior_aucs = [
            direction["behavior_specificity"][method][
                "drift_vs_resisted_attack"
            ]["auroc"]
            for method in METHODS
        ]
        axes[0, 1].bar(
            x + offset, behavior_aucs, width, label=label, color=color
        )
        adjacent = [
            direction["representation_metrics"][method][
                "adjacent_observation_score_spearman"
            ]
            for method in METHODS
        ]
        axes[1, 0].bar(x + offset, adjacent, width, label=label, color=color)

    axes[0, 0].axhline(0.90, color="#777777", linestyle="--", linewidth=1)
    axes[0, 0].set_ylim(0.85, 1.005)
    axes[0, 0].set_ylabel("Normal vs fully-post AUROC")
    axes[0, 0].set_title("Representation signal survives independent units")

    axes[0, 1].set_ylim(0.85, 1.005)
    axes[0, 1].set_ylabel("Drift vs resisted path-max AUROC")
    axes[0, 1].set_title("Independent persistence does not meet its gain gate")

    axes[1, 0].set_ylim(0.0, 1.0)
    axes[1, 0].set_ylabel("Adjacent-score Spearman correlation")
    axes[1, 0].set_title("Token innovation sharply reduces endpoint redundancy")

    for axis in (axes[0, 0], axes[0, 1], axes[1, 0]):
        axis.set_xticks(x, SHORT_METHODS, fontsize=8)
        axis.legend(fontsize=8)

    markers = ("o", "s", "^", "D", "P", "X", "v")
    for (direction_name, label), color in zip(DIRECTIONS, colors, strict=True):
        direction = result["directions"][direction_name]
        for method, marker in zip(METHODS, markers, strict=True):
            metrics = direction["stopping_diagnostics"][method]["pair_group_max"][
                "target_metrics"
            ]
            axes[1, 1].scatter(
                metrics["non_drift_trace_false_alarm_rate"],
                metrics["clean_hit_recall_plus_8"],
                color=color,
                marker=marker,
                edgecolor="white",
                linewidth=0.7,
                s=70,
            )
    axes[1, 1].axvline(0.15, color="#777777", linestyle="--", linewidth=1)
    axes[1, 1].axhline(0.35, color="#777777", linestyle="--", linewidth=1)
    axes[1, 1].set_xlim(-0.005, 0.16)
    axes[1, 1].set_ylim(-0.01, 0.38)
    axes[1, 1].set_xlabel("Pair-group calibrated normal trace FAR")
    axes[1, 1].set_ylabel("Clean recall by onset +8")
    axes[1, 1].set_title("No method enters the detector-go region")
    direction_handles = [
        Line2D([], [], marker="o", linestyle="", color=color, label=label)
        for (_, label), color in zip(DIRECTIONS, colors, strict=True)
    ]
    method_handles = [
        Line2D(
            [], [], marker=marker, linestyle="", color="#666666", label=label
        )
        for marker, label in zip(markers, SHORT_METHODS, strict=True)
    ]
    direction_legend = axes[1, 1].legend(
        handles=direction_handles, fontsize=8, loc="upper left"
    )
    axes[1, 1].add_artist(direction_legend)
    axes[1, 1].legend(
        handles=method_handles, fontsize=7, loc="lower right", ncol=2
    )

    fig.suptitle("Independent routing innovation: representation yes, detector no", fontsize=15)
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
