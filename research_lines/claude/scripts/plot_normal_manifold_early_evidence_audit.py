#!/usr/bin/env python3
"""Plot the behavior-relative early-evidence audit."""

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
    / "normal_manifold_early_evidence_audit"
    / "result.json"
)
DEFAULT_OUTPUT = DEFAULT_RESULT.parent / "early_evidence_audit_summary.png"
HORIZONS = ("plus_0", "plus_4", "plus_8", "plus_16", "plus_32", "full")
HORIZON_LABELS = ("+0", "+4", "+8", "+16", "+32", "full")
COLORS = {"b1": "#4C78A8", "b2": "#F58518"}


def plot(result: dict[str, Any], output: Path) -> None:
    import matplotlib.pyplot as plt
    import numpy as np

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(2, 2, figsize=(13.5, 9.5))
    x = np.arange(len(HORIZONS))

    for batch in ("b1", "b2"):
        matched = result["batches"][batch]["methods"]["token_endpoint_z"][
            "matched_windows"
        ]
        axes[0, 0].plot(
            x,
            [matched[h]["ranking"]["auroc"] for h in HORIZONS],
            marker="o",
            color=COLORS[batch],
            label=batch.upper(),
        )
        axes[0, 1].plot(
            x,
            [matched[h]["paired_positive_wilson_95"]["rate"] for h in HORIZONS],
            marker="o",
            color=COLORS[batch],
            label=f"{batch.upper()} paired win",
        )
        axes[0, 1].plot(
            x,
            [matched[h]["eligible_rate"] for h in HORIZONS],
            marker="s",
            linestyle="--",
            color=COLORS[batch],
            label=f"{batch.upper()} coverage",
        )
    axes[0, 0].axhline(0.80, color="#777777", linestyle="--", linewidth=1)
    axes[0, 0].set_xticks(x, HORIZON_LABELS)
    axes[0, 0].set_ylim(0.5, 1.01)
    axes[0, 0].set_ylabel("Drift vs matched-control AUROC")
    axes[0, 0].set_title("Threshold-free token ranking emerges after onset")
    axes[0, 0].legend()
    axes[0, 1].axhline(0.75, color="#777777", linestyle=":", linewidth=1)
    axes[0, 1].axhline(0.80, color="#999999", linestyle="--", linewidth=1)
    axes[0, 1].set_xticks(x, HORIZON_LABELS)
    axes[0, 1].set_ylim(0.5, 1.01)
    axes[0, 1].set_ylabel("Rate")
    axes[0, 1].set_title("B2 fails the preregistered coverage requirement")
    axes[0, 1].legend(fontsize=8)

    line_styles = {"static_path_max": "-", "risk_clock_normalized": "--"}
    boundary_labels = {"static_path_max": "Static", "risk_clock_normalized": "Risk-clock"}
    for batch in ("b1", "b2"):
        boundaries = result["batches"][batch]["methods"]["token_endpoint_z"][
            "calibrated_boundaries"
        ]
        for boundary in ("static_path_max", "risk_clock_normalized"):
            axes[1, 0].plot(
                x,
                [100.0 * boundaries[boundary][h]["clean_hit_recall"] for h in HORIZONS],
                marker="o",
                linestyle=line_styles[boundary],
                color=COLORS[batch],
                label=f"{batch.upper()} / {boundary_labels[boundary]}",
            )
    axes[1, 0].axhline(35.0, color="#777777", linestyle=":", linewidth=1)
    axes[1, 0].set_xticks(x, HORIZON_LABELS)
    axes[1, 0].set_ylabel("C1-threshold clean recall (%)")
    axes[1, 0].set_title("Global normal-tail thresholds suppress early recall")
    axes[1, 0].legend(fontsize=8)

    state_rates = [
        100.0
        * result["batches"][batch]["generic_state_audit"][
            "three_arm_signature_identical_rate"
        ]
        for batch in ("b1", "b2")
    ]
    axes[1, 1].bar(
        np.arange(2), state_rates, color=[COLORS["b1"], COLORS["b2"]]
    )
    axes[1, 1].axhline(90.0, color="#777777", linestyle="--", linewidth=1)
    axes[1, 1].set_xticks(np.arange(2), ("B1", "B2"))
    axes[1, 1].set_ylim(0, 105)
    axes[1, 1].set_ylabel("Three-arm structural signatures identical (%)")
    axes[1, 1].set_title("Generic controller structure has no matched outcome signal")

    fig.suptitle(
        "Early evidence audit: relative routing signal exists, deployable coverage does not",
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
