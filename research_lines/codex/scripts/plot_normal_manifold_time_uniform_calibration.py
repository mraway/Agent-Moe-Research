#!/usr/bin/env python3
"""Plot the C1 group-aware time-uniform calibration result."""

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
    / "normal_manifold_time_uniform_calibration"
    / "result.json"
)
DEFAULT_OUTPUT = DEFAULT_RESULT.parent / "time_uniform_calibration_summary.png"
METHODS = ("token_endpoint_z", "nonoverlap_token_mean8_z")
METHOD_LABELS = {"token_endpoint_z": "Token endpoint", "nonoverlap_token_mean8_z": "Token block mean-8"}
BOUNDARIES = ("static_path_max", "risk_clock_normalized")
BOUNDARY_LABELS = {"static_path_max": "Static path-max", "risk_clock_normalized": "Risk-clock normalized"}
COLORS = {"static_path_max": "#4C78A8", "risk_clock_normalized": "#F58518"}


def plot(result: dict[str, Any], output: Path) -> None:
    import matplotlib.pyplot as plt
    import numpy as np

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 9.5))
    x = np.arange(len(METHODS))
    width = 0.34

    for offset, boundary in zip((-width / 2, width / 2), BOUNDARIES, strict=True):
        rates = [
            100.0
            * result["methods"][method]["c1_normal_evaluation"][boundary][
                "held_out_group_far_wilson_95"
            ]["rate"]
            for method in METHODS
        ]
        axes[0, 0].bar(
            x + offset,
            rates,
            width,
            label=BOUNDARY_LABELS[boundary],
            color=COLORS[boundary],
        )
    axes[0, 0].axhline(15.0, color="#777777", linestyle="--", linewidth=1)
    axes[0, 0].set_xticks(x, [METHOD_LABELS[m] for m in METHODS])
    axes[0, 0].set_ylabel("Held-out pair-group FAR (%)")
    axes[0, 0].set_title("New C1 normal-risk gate")
    axes[0, 0].legend(fontsize=8)

    risk_x = np.arange(6)
    risk_labels = ("8", "16", "32", "64", "128", "full")
    linestyles = {"token_endpoint_z": "-", "nonoverlap_token_mean8_z": "--"}
    for method in METHODS:
        for boundary in BOUNDARIES:
            curve = result["methods"][method]["c1_normal_evaluation"][boundary][
                "risk_step_curve"
            ]
            axes[0, 1].plot(
                risk_x,
                [100.0 * row["alarm_by_step_rate_all_groups"] for row in curve],
                color=COLORS[boundary],
                linestyle=linestyles[method],
                marker="o",
                label=f"{METHOD_LABELS[method]} / {BOUNDARY_LABELS[boundary]}",
            )
    axes[0, 1].axhline(15.0, color="#777777", linestyle=":", linewidth=1)
    axes[0, 1].set_xticks(risk_x, risk_labels)
    axes[0, 1].set_xlabel("Eligible risk step")
    axes[0, 1].set_ylabel("Alarmed held-out groups (%)")
    axes[0, 1].set_title("Normal cumulative alarm incidence")
    axes[0, 1].legend(fontsize=7)

    utility_x = np.arange(4)
    utility_labels = ("Token/B1", "Token/B2", "Block/B1", "Block/B2")
    for offset, boundary in zip((-width / 2, width / 2), BOUNDARIES, strict=True):
        recalls = []
        latencies = []
        for method in METHODS:
            utility = result["methods"][method]["adaptive_historical_utility"][boundary]
            for batch in ("b1", "b2"):
                metrics = utility[batch]["metrics"]
                recalls.append(100.0 * metrics["clean_hit_recall_plus_8"])
                latencies.append(metrics["clean_hit_median_latency"])
        axes[1, 0].bar(
            utility_x + offset,
            recalls,
            width,
            label=BOUNDARY_LABELS[boundary],
            color=COLORS[boundary],
        )
        axes[1, 1].bar(
            utility_x + offset,
            latencies,
            width,
            label=BOUNDARY_LABELS[boundary],
            color=COLORS[boundary],
        )
    axes[1, 0].axhline(35.0, color="#777777", linestyle="--", linewidth=1)
    axes[1, 0].set_xticks(utility_x, utility_labels, fontsize=8)
    axes[1, 0].set_ylabel("Clean recall by onset +8 (%)")
    axes[1, 0].set_title("Historical drift remains too late")
    axes[1, 0].legend(fontsize=8)
    axes[1, 1].axhline(8.0, color="#777777", linestyle="--", linewidth=1)
    axes[1, 1].set_xticks(utility_x, utility_labels, fontsize=8)
    axes[1, 1].set_ylabel("Median latency among clean hits (tokens)")
    axes[1, 1].set_title("Latency remains above the prior detector target")
    axes[1, 1].legend(fontsize=8)

    fig.suptitle(
        "Group-aware normal calibration: risk control improves, timely detection does not",
        fontsize=14,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=180, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    plot(json.loads(args.result.read_text(encoding="utf-8")), args.output)
    print(args.output)


if __name__ == "__main__":
    main()
