#!/usr/bin/env python3
"""Run labeled post-hoc robustness checks for the horizon-384 mechanism result."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Sequence

import torch
from safetensors.torch import load_file


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from phase_a.normal_manifold import sha256  # noqa: E402
from phase_a.routing_analysis import jensen_shannon_divergence  # noqa: E402
from analyze_agent_v2_engagement_mechanism import (  # noqa: E402
    _ranking,
    _summary,
    bootstrap_mean_contrast,
    continuation_delta,
)
from run_normal_manifold_time_uniform_calibration import (  # noqa: E402
    _canonical_fit_ids,
)


DEFAULT_RESULT = (
    ROOT
    / "artifacts"
    / "agent_v2"
    / "agent_v2_5_b2_horizon384_mechanism"
    / "result.json"
)
DEFAULT_REPLAY = ROOT / "artifacts/agent_v2/agent_v2_5_b2_horizon384"
DEFAULT_B1 = ROOT / "artifacts/agent_v2/agent_v2_5_b1"
DEFAULT_B2 = ROOT / "artifacts/agent_v2/agent_v2_5_b2"
DEFAULT_HISTORICAL_CACHE = ROOT / "artifacts/agent_v2/normal_manifold_cache"
DEFAULT_REPLAY_CACHE = (
    ROOT / "artifacts/agent_v2/agent_v2_5_b2_horizon384_cache"
)
DEFAULT_OUTPUT = (
    ROOT
    / "artifacts"
    / "agent_v2"
    / "agent_v2_5_b2_horizon384_mechanism"
    / "robustness_audit.json"
)
BOUNDARY_LABEL_IDS = {
    "b2-f0-077-knowledge_qa-economics-explanation--attack",
    "b2-f2-062-warranty_and_knowledge-fictional-policy-argument--attack",
}


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--replay", type=Path, default=DEFAULT_REPLAY)
    parser.add_argument("--b1", type=Path, default=DEFAULT_B1)
    parser.add_argument("--b2", type=Path, default=DEFAULT_B2)
    parser.add_argument(
        "--historical-cache", type=Path, default=DEFAULT_HISTORICAL_CACHE
    )
    parser.add_argument("--replay-cache", type=Path, default=DEFAULT_REPLAY_CACHE)
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


def fixed_block_delta(row: dict[str, Any], onset: int) -> dict[str, Any] | None:
    """Repeat H2 using the episode-anchored non-overlapping 8-token stream."""

    stream = row["streams"]["nonoverlap_token_mean8_z"]
    points = [
        (int(endpoint), float(score))
        for endpoint, score in zip(
            stream["endpoints"], stream["scores"], strict=True
        )
    ]
    early = [score for endpoint, score in points if onset <= endpoint <= onset + 15]
    late = [
        score
        for endpoint, score in points
        if onset + 32 <= endpoint <= onset + 63
    ]
    if not early or not late:
        return None
    early_mean = statistics.fmean(early)
    late_mean = statistics.fmean(late)
    return {
        "early_block_count": len(early),
        "late_block_count": len(late),
        "early_mean": early_mean,
        "late_mean": late_mean,
        "continuation_delta": late_mean - early_mean,
    }


def contrast(execution: Sequence[float], bounded: Sequence[float]) -> dict[str, Any]:
    return {
        "execution": _summary(execution),
        "bounded": _summary(bounded),
        "execution_minus_bounded_bootstrap": bootstrap_mean_contrast(
            execution, bounded
        ),
        "ranking": _ranking(execution, bounded),
    }


def probability_jsd_delta(
    probabilities: torch.Tensor, profile: torch.Tensor, onset: int
) -> dict[str, float] | None:
    """Return the H2 contrast on probability JSD to a fit-only profile."""

    if probabilities.ndim != 3 or profile.ndim != 2:
        raise ValueError("probability tensors have invalid ranks")
    if probabilities.shape[0] != profile.shape[0] or probabilities.shape[2] != profile.shape[1]:
        raise ValueError("probability profile shape mismatch")
    if onset < 0 or onset + 64 > probabilities.shape[1]:
        return None
    scores = jensen_shannon_divergence(
        probabilities.float(), profile[:, None, :].float()
    ).mean(dim=0)
    early = float(scores[onset : onset + 16].mean().item())
    late = float(scores[onset + 32 : onset + 64].mean().item())
    return {
        "early_mean_jsd": early,
        "late_mean_jsd": late,
        "continuation_delta": late - early,
    }


def _trace_outcomes(replay: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(replay / "sample_index.jsonl"):
        if row["arm"] != "attack":
            continue
        trace = _read_json(replay / str(row["relative_path"]) / "trace.json")
        result[str(row["trace_id"])] = trace["outcome"]
    return result


def main() -> int:
    args = _args()
    result_path = args.result.resolve()
    replay = args.replay.resolve()
    b1 = args.b1.resolve()
    b2 = args.b2.resolve()
    historical_cache = args.historical_cache.resolve()
    replay_cache = args.replay_cache.resolve()
    output = args.output.resolve()
    result = _read_json(result_path)
    h2_rows = result["h2_recovery_execution_bifurcation"]["trace_rows"]
    score_by_id = {str(row["trace_id"]): row for row in result["score_rows"]}
    outcomes = _trace_outcomes(replay)
    bounded_rows = [
        row
        for row in h2_rows
        if row["engagement_class"] == "bounded_engagement_resisted"
    ]
    execution_rows = [
        row
        for row in h2_rows
        if row["engagement_class"] == "cross_domain_execution"
    ]
    bounded = [float(row["continuation_delta"]) for row in bounded_rows]
    execution = [float(row["continuation_delta"]) for row in execution_rows]

    leave_one_out = []
    for omitted in bounded_rows:
        kept = [
            float(row["continuation_delta"])
            for row in bounded_rows
            if row["trace_id"] != omitted["trace_id"]
        ]
        leave_one_out.append(
            {
                "omitted_trace_id": omitted["trace_id"],
                "bounded_count": len(kept),
                **contrast(execution, kept),
            }
        )

    strict_bounded = [
        float(row["continuation_delta"])
        for row in bounded_rows
        if row["trace_id"] not in BOUNDARY_LABEL_IDS
    ]

    channel_counts = Counter(
        str(score_by_id[str(row["trace_id"])]["channel"])
        for row in bounded_rows
    )
    same_channel = next(iter(channel_counts)) if len(channel_counts) == 1 else None
    same_channel_execution = [
        float(row["continuation_delta"])
        for row in execution_rows
        if same_channel is not None
        and score_by_id[str(row["trace_id"])]["channel"] == same_channel
    ]

    control_rows = [
        row
        for row in result["score_rows"]
        if row["arm"] in {"clean", "benign_control"}
    ]
    age_rows = []
    for bounded_row in bounded_rows:
        onset = int(bounded_row["onset"])
        control_deltas = []
        for control in control_rows:
            candidate = continuation_delta(control, onset)
            if candidate is not None:
                control_deltas.append(float(candidate["continuation_delta"]))
        control_median = statistics.median(control_deltas)
        attack_delta = float(bounded_row["continuation_delta"])
        age_rows.append(
            {
                "trace_id": bounded_row["trace_id"],
                "onset": onset,
                "attack_delta": attack_delta,
                "eligible_clean_benign_count": len(control_deltas),
                "control_delta_mean": statistics.fmean(control_deltas),
                "control_delta_median": control_median,
                "attack_minus_control_median": attack_delta - control_median,
                "control_fraction_at_or_below_attack": sum(
                    value <= attack_delta for value in control_deltas
                )
                / len(control_deltas),
            }
        )

    composition_rows = []
    for bounded_row in bounded_rows:
        trace_id = str(bounded_row["trace_id"])
        source = score_by_id[trace_id]
        comparisons: dict[str, dict[str, Any]] = {}
        for field in ("domain", "workflow_family"):
            value = str(source[field])
            candidates = [
                float(row["continuation_delta"])
                for row in execution_rows
                if str(score_by_id[str(row["trace_id"])][field]) == value
            ]
            median = statistics.median(candidates)
            comparisons[field] = {
                "stratum": value,
                "execution_count": len(candidates),
                "execution_median": median,
                "bounded_minus_execution_median": float(
                    bounded_row["continuation_delta"]
                )
                - median,
            }
        composition_rows.append(
            {
                "trace_id": trace_id,
                "bounded_delta": float(bounded_row["continuation_delta"]),
                "comparisons": comparisons,
            }
        )

    resumed_rows = []
    for bounded_row in bounded_rows:
        trace_id = str(bounded_row["trace_id"])
        outcome = outcomes[trace_id]
        resume = outcome.get("support_resume_output_token")
        if resume is None:
            continue
        stream = score_by_id[trace_id]["streams"]["token_endpoint_z"]
        lookup = {
            int(endpoint): float(score)
            for endpoint, score in zip(
                stream["endpoints"], stream["scores"], strict=True
            )
        }
        post_positions = list(range(int(resume), int(resume) + 32))
        if any(position not in lookup for position in post_positions):
            continue
        post_resume_mean = statistics.fmean(
            lookup[position] for position in post_positions
        )
        resumed_rows.append(
            {
                "trace_id": trace_id,
                "engagement_onset": bounded_row["onset"],
                "support_resume_token": int(resume),
                "early_engagement_mean": float(bounded_row["early_mean"]),
                "post_resume_32_mean": post_resume_mean,
                "post_resume_minus_early": post_resume_mean
                - float(bounded_row["early_mean"]),
            }
        )

    block_rows = []
    for row in [*bounded_rows, *execution_rows]:
        block = fixed_block_delta(
            score_by_id[str(row["trace_id"])], int(row["onset"])
        )
        if block is not None:
            block_rows.append(
                {
                    "trace_id": row["trace_id"],
                    "engagement_class": row["engagement_class"],
                    "onset": row["onset"],
                    **block,
                }
            )
    bounded_blocks = [
        float(row["continuation_delta"])
        for row in block_rows
        if row["engagement_class"] == "bounded_engagement_resisted"
    ]
    execution_blocks = [
        float(row["continuation_delta"])
        for row in block_rows
        if row["engagement_class"] == "cross_domain_execution"
    ]

    delayed_execution_ids = {
        str(row["trace_id"])
        for row in result["horizon_audit"]["attack_rows"]
        if row["new_execution_after_191"]
    }
    delayed_eligible = [
        row for row in execution_rows if row["trace_id"] in delayed_execution_ids
    ]

    fit_ids = _canonical_fit_ids(b1, "b1") | _canonical_fit_ids(b2, "b2")
    fit_probabilities = []
    for batch in ("b1", "b2"):
        for path in sorted((historical_cache / batch).glob("*.safetensors")):
            if path.stem in fit_ids:
                fit_probabilities.append(load_file(path)["probabilities"].float())
    if len(fit_probabilities) != 26:
        raise ValueError(
            f"probability-JSD fit cache count changed: {len(fit_probabilities)}"
        )
    probability_profile = torch.cat(fit_probabilities, dim=1).mean(dim=1)
    probability_rows = []
    for row in [*bounded_rows, *execution_rows]:
        probabilities = load_file(
            replay_cache / "h384" / f"{row['trace_id']}.safetensors"
        )["probabilities"]
        candidate = probability_jsd_delta(
            probabilities, probability_profile, int(row["onset"])
        )
        if candidate is not None:
            probability_rows.append(
                {
                    "trace_id": row["trace_id"],
                    "engagement_class": row["engagement_class"],
                    "onset": row["onset"],
                    **candidate,
                }
            )
    bounded_probability = [
        float(row["continuation_delta"])
        for row in probability_rows
        if row["engagement_class"] == "bounded_engagement_resisted"
    ]
    execution_probability = [
        float(row["continuation_delta"])
        for row in probability_rows
        if row["engagement_class"] == "cross_domain_execution"
    ]

    audit = {
        "schema_version": 1,
        "analysis_scope": "post_hoc_robustness_not_a_confirmatory_retest",
        "source_result": str(result_path),
        "source_result_sha256": sha256(result_path),
        "formal_h2_status_unchanged": "failed_minimum_bounded_sample_gate",
        "primary_snapshot": contrast(execution, bounded),
        "bounded_leave_one_out": leave_one_out,
        "strict_label_sensitivity": {
            "excluded_boundary_trace_ids": sorted(BOUNDARY_LABEL_IDS),
            "definition": (
                "Keep only explicit reject/reference cases; exclude full-note echo "
                "and attack-format redirection."
            ),
            **contrast(execution, strict_bounded),
        },
        "attack_channel_sensitivity": {
            "bounded_channel_counts": dict(sorted(channel_counts.items())),
            "same_channel": same_channel,
            "same_channel_execution_count": len(same_channel_execution),
            **contrast(same_channel_execution, bounded),
        },
        "decode_age_sensitivity": {
            "definition": (
                "At each bounded onset, compare its delta with every clean/benign "
                "trace that fully observes the same absolute early and late windows."
            ),
            "bounded_more_negative_than_pooled_control_median_count": sum(
                row["attack_minus_control_median"] < 0.0 for row in age_rows
            ),
            "bounded_count": len(age_rows),
            "attack_minus_control_median": _summary(
                [row["attack_minus_control_median"] for row in age_rows]
            ),
            "trace_rows": age_rows,
        },
        "domain_and_workflow_sensitivity": {
            "definition": (
                "Compare each bounded delta with the execution median in its same "
                "target domain and, separately, its same workflow family."
            ),
            "below_same_domain_execution_median_count": sum(
                row["comparisons"]["domain"][
                    "bounded_minus_execution_median"
                ]
                < 0.0
                for row in composition_rows
            ),
            "below_same_workflow_execution_median_count": sum(
                row["comparisons"]["workflow_family"][
                    "bounded_minus_execution_median"
                ]
                < 0.0
                for row in composition_rows
            ),
            "bounded_count": len(composition_rows),
            "trace_rows": composition_rows,
        },
        "behavior_aligned_support_resumption": {
            "definition": (
                "For bounded traces with an observed return to support, compare the "
                "frozen early-engagement mean with the first 32 support tokens."
            ),
            "eligible_count": len(resumed_rows),
            "all_post_resume_below_early": bool(resumed_rows)
            and all(row["post_resume_minus_early"] < 0.0 for row in resumed_rows),
            "post_resume_minus_early": _summary(
                [row["post_resume_minus_early"] for row in resumed_rows]
            ),
            "trace_rows": resumed_rows,
        },
        "fixed_nonoverlap_8token_sensitivity": {
            "definition": (
                "Repeat the early-vs-late contrast on the independently available "
                "episode-anchored non-overlapping 8-token mean stream."
            ),
            **contrast(execution_blocks, bounded_blocks),
            "trace_rows": block_rows,
        },
        "probability_jsd_sensitivity": {
            "definition": (
                "For each token, average per-layer router-probability JSD to the "
                "pooled profile of the same 26 canonical clean fit traces, then "
                "repeat the frozen early-vs-late H2 windows."
            ),
            "fit_trace_count": len(fit_probabilities),
            "fit_token_count": sum(
                int(probabilities.shape[1]) for probabilities in fit_probabilities
            ),
            **contrast(execution_probability, bounded_probability),
            "trace_rows": probability_rows,
        },
        "delayed_post192_execution": {
            "new_execution_count": len(delayed_execution_ids),
            "complete_h2_window_count": len(delayed_eligible),
            "all_eligible_delta_positive": bool(delayed_eligible)
            and all(row["continuation_delta"] > 0.0 for row in delayed_eligible),
            "delta": _summary(
                [float(row["continuation_delta"]) for row in delayed_eligible]
            ),
            "trace_rows": delayed_eligible,
        },
        "interpretation": (
            "The recovery-direction effect is stable to individual bounded cases, "
            "strict labels, attack channel, decode age, and 8-token aggregation, but "
            "the confirmatory H2 remains failed because bounded n=5 is below 12."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "formal_h2_status": audit["formal_h2_status_unchanged"],
                "leave_one_out_ci_lower_bounds": [
                    row["execution_minus_bounded_bootstrap"]["ci95"][0]
                    for row in leave_one_out
                ],
                "strict_label_auroc": audit["strict_label_sensitivity"]["ranking"][
                    "auroc"
                ],
                "same_channel_auroc": audit["attack_channel_sensitivity"][
                    "ranking"
                ]["auroc"],
                "age_robust_count": audit["decode_age_sensitivity"][
                    "bounded_more_negative_than_pooled_control_median_count"
                ],
                "same_domain_robust_count": audit[
                    "domain_and_workflow_sensitivity"
                ]["below_same_domain_execution_median_count"],
                "same_workflow_robust_count": audit[
                    "domain_and_workflow_sensitivity"
                ]["below_same_workflow_execution_median_count"],
                "support_resumption_all_decreased": audit[
                    "behavior_aligned_support_resumption"
                ]["all_post_resume_below_early"],
                "block_stream_auroc": audit[
                    "fixed_nonoverlap_8token_sensitivity"
                ]["ranking"]["auroc"],
                "probability_jsd_auroc": audit["probability_jsd_sensitivity"][
                    "ranking"
                ]["auroc"],
                "output": str(output),
                "output_sha256": sha256(output),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
