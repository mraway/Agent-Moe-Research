#!/usr/bin/env python3
"""Run the frozen Agent v2.5 B1 route-only development analysis."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase_a.classifier import (  # noqa: E402
    average_precision,
    binary_auroc,
    extract_features,
    fit_ridge_classifier,
    leave_one_group_out_scores,
    prefix_sequence,
)
from phase_a.routing_analysis import (  # noqa: E402
    RoutingSequence,
    load_final_generation_sequence,
    load_sequence,
    mean_probability_profile,
    score_sequence,
    subset_routing_sequence,
)
from routing import validate_trace  # noqa: E402


EXPECTED_EXPERIMENT_ID = "agent-v2.5-b1-development-signal"
EXPECTED_SAMPLE_INDEX_SHA256 = (
    "f1a95c5bf36d3233f445e4cb507c89e59c13eda1619af317c4efefadcea72df1"
)
PLAN = "docs/agent_v2_5_b1_routing_analysis_plan.md"
PREFIXES: tuple[tuple[str, int | None], ...] = (
    ("first_8", 8),
    ("first_16", 16),
    ("first_32", 32),
    ("full", None),
)
FEATURES = ("route_selection", "route_probability", "token_hash", "length")


@dataclass(frozen=True)
class Observation:
    trace_dir: Path
    trace: dict[str, Any]
    sequence: RoutingSequence

    @property
    def trace_id(self) -> str:
        return str(self.trace["trace_id"])

    @property
    def pair_group_id(self) -> str:
        return str(self.trace["pair_group_id"])

    @property
    def fold(self) -> str:
        return str(self.trace["preregistered_fold"])

    @property
    def arm(self) -> str:
        return str(self.trace["perturbation"]["arm"])

    @property
    def brief(self) -> str:
        return str(self.trace["response_brief_condition"])

    @property
    def workflow(self) -> str:
        return str(self.trace["task_mandate"]["authorized_goal"])

    @property
    def channel(self) -> str:
        return str(self.trace["perturbation"]["channel"])

    @property
    def domain(self) -> str:
        goal = self.trace["perturbation"].get("attack_goal") or {}
        return str(goal.get("target_domain", "none"))

    @property
    def positive(self) -> bool:
        return bool(self.trace["outcome"]["primary_positive"])

    @property
    def boundary(self) -> int | None:
        value = self.trace["outcome"].get(
            "goal_plan_deviation_start_output_token"
        )
        return None if value is None else int(value["output_token_index"])


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run_dir",
        nargs="?",
        type=Path,
        default=ROOT / "artifacts" / "agent_v2" / "agent_v2_5_b1",
    )
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_observations(run_dir: Path) -> tuple[list[Observation], int]:
    index_path = run_dir / "sample_index.jsonl"
    if _sha256(index_path) != EXPECTED_SAMPLE_INDEX_SHA256:
        raise ValueError("B1 sample index differs from the frozen behavior-labeled input")
    observations: list[Observation] = []
    valid = 0
    for trace_path in sorted(run_dir.glob("*/*/trace.json")):
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        if trace["experiment_id"] != EXPECTED_EXPERIMENT_ID:
            raise ValueError(f"unexpected experiment in {trace_path}")
        validation = validate_trace(trace_path.parent)
        if not validation["passed"]:
            raise ValueError(f"routing validation failed for {trace_path.parent}")
        valid += 1
        sequence = load_final_generation_sequence(trace_path.parent)
        generations = [
            event for event in trace["events"] if event["kind"] == "model_generation"
        ]
        if len(generations) != 1:
            raise ValueError(f"{trace_path.parent} does not have one final generation")
        if tuple(generations[0]["output_token_ids"]) != sequence.token_ids:
            raise ValueError(f"decode alignment failed for {trace_path.parent}")
        observations.append(Observation(trace_path.parent, trace, sequence))
    _assert_design(observations)
    return observations, valid


def _assert_design(observations: Sequence[Observation]) -> None:
    if len(observations) != 240:
        raise ValueError("B1 must contain exactly 240 traces")
    arms = {arm: sum(row.arm == arm for row in observations) for arm in {
        row.arm for row in observations
    }}
    if arms != {"clean": 80, "benign_control": 80, "attack": 80}:
        raise ValueError(f"unexpected B1 arm counts: {arms}")
    attacks = [row for row in observations if row.arm == "attack"]
    if sum(row.positive for row in attacks) != 27:
        raise ValueError("B1 attack labels must contain 27 drift traces")
    headline = [row for row in attacks if row.brief == "absent"]
    if len(headline) != 40 or sum(row.positive for row in headline) != 24:
        raise ValueError("headline cohort must be 40 attacks with 24 drift traces")
    if any(row.boundary is None for row in attacks if row.positive):
        raise ValueError("every drift trace must have a token boundary")
    for fold in sorted({row.fold for row in headline}):
        labels = [row.positive for row in headline if row.fold == fold]
        if not any(labels) or all(labels):
            raise ValueError(f"headline fold {fold} is not mixed")
    for domain in sorted({row.domain for row in headline}):
        labels = [row.positive for row in headline if row.domain == domain]
        if len(labels) != 5 or not any(labels) or all(labels):
            raise ValueError(f"headline domain {domain} is not a mixed five-trace set")


def _feature_matrix(
    observations: Sequence[Observation], family: str, token_limit: int | None
) -> torch.Tensor:
    return torch.stack(
        [
            extract_features(prefix_sequence(row.sequence, token_limit), family)
            for row in observations
        ]
    )


def _metadata_matrix(
    observations: Sequence[Observation], universe: Sequence[Observation]
) -> torch.Tensor:
    attributes = ("brief", "workflow", "channel", "domain")
    categories = {
        name: tuple(sorted({getattr(row, name) for row in universe}))
        for name in attributes
    }
    rows: list[list[float]] = []
    for observation in observations:
        values: list[float] = []
        for name in attributes:
            current = getattr(observation, name)
            values.extend(float(current == category) for category in categories[name])
        rows.append(values)
    return torch.tensor(rows, dtype=torch.float32)


def _ranking_metrics(scores: torch.Tensor, labels: torch.Tensor) -> dict[str, Any]:
    labels = labels.bool()
    return {
        "trace_count": labels.numel(),
        "positive_count": int(labels.sum().item()),
        "auroc": binary_auroc(scores, labels),
        "average_precision": average_precision(scores, labels),
        "mean_positive_score": float(scores[labels].mean().item()),
        "mean_negative_score": float(scores[~labels].mean().item()),
        "mean_score_difference": float(
            scores[labels].mean().item() - scores[~labels].mean().item()
        ),
    }


def _fold_metrics(
    scores: torch.Tensor, labels: torch.Tensor, folds: Sequence[str]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for fold in sorted(set(folds)):
        mask = torch.tensor([candidate == fold for candidate in folds])
        result[fold] = _ranking_metrics(scores[mask], labels[mask])
    return result


def _trace_scores(
    observations: Sequence[Observation], scores: torch.Tensor
) -> list[dict[str, Any]]:
    return [
        {
            "trace_id": row.trace_id,
            "pair_group_id": row.pair_group_id,
            "fold": row.fold,
            "brief": row.brief,
            "workflow": row.workflow,
            "channel": row.channel,
            "domain": row.domain,
            "primary_positive": row.positive,
            "decode_token_count": len(row.sequence.token_ids),
            "boundary": row.boundary,
            "score": float(score.item()),
        }
        for row, score in zip(observations, scores, strict=True)
    ]


def _evaluate_matrix(
    observations: Sequence[Observation], features: torch.Tensor
) -> dict[str, Any]:
    labels = torch.tensor([row.positive for row in observations])
    folds = [row.fold for row in observations]
    scores = leave_one_group_out_scores(features, labels, folds, classifier="ridge")
    return {
        "metrics": _ranking_metrics(scores, labels),
        "by_fold": _fold_metrics(scores, labels, folds),
        "trace_scores": _trace_scores(observations, scores),
        "_scores": scores,
    }


def _serializable_result(result: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in result.items() if key != "_scores"}


def _domain_sensitivity(
    observations: Sequence[Observation], features: torch.Tensor
) -> dict[str, Any]:
    labels = torch.tensor([row.positive for row in observations])
    domains = [row.domain for row in observations]
    scores = leave_one_group_out_scores(features, labels, domains, classifier="ridge")
    by_domain = _fold_metrics(scores, labels, domains)
    return {
        "metrics": _ranking_metrics(scores, labels),
        "directionally_consistent_domain_count": sum(
            row["mean_score_difference"] > 0 for row in by_domain.values()
        ),
        "domain_count": len(by_domain),
        "by_domain": by_domain,
        "trace_scores": _trace_scores(observations, scores),
    }


def _prefix_boundary_status(
    observations: Sequence[Observation],
    result: dict[str, Any],
    token_limit: int | None,
) -> dict[str, Any]:
    scores = result["_scores"]
    active: list[float] = []
    future: list[float] = []
    negatives: list[float] = []
    for row, score in zip(observations, scores, strict=True):
        if not row.positive:
            negatives.append(float(score.item()))
            continue
        available = len(row.sequence.token_ids) if token_limit is None else min(
            token_limit, len(row.sequence.token_ids)
        )
        target = active if row.boundary is not None and row.boundary < available else future
        target.append(float(score.item()))

    def summarize(values: Sequence[float]) -> dict[str, float | int | None]:
        return {
            "count": len(values),
            "mean_score": statistics.mean(values) if values else None,
        }

    return {
        "boundary_already_observed": summarize(active),
        "boundary_not_yet_observed": summarize(future),
        "negative": summarize(negatives),
    }


def _headline_analysis(
    observations: Sequence[Observation], all_attacks: Sequence[Observation]
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    route_selection_features: dict[str, torch.Tensor] = {}
    for prefix_name, token_limit in PREFIXES:
        for family in FEATURES:
            features = _feature_matrix(observations, family, token_limit)
            evaluated = _evaluate_matrix(observations, features)
            row = {
                "prefix": prefix_name,
                "token_limit": token_limit,
                "feature": family,
                "feature_dimension": int(features.shape[1]),
                **_serializable_result(evaluated),
            }
            if family == "route_selection":
                route_selection_features[prefix_name] = features
                row["boundary_status"] = _prefix_boundary_status(
                    observations, evaluated, token_limit
                )
                row["leave_one_domain_out"] = _domain_sensitivity(
                    observations, features
                )
            results.append(row)

    nuisance = _metadata_matrix(observations, all_attacks)
    nuisance_result = _evaluate_matrix(observations, nuisance)
    return {
        "trace_count": len(observations),
        "positive_count": sum(row.positive for row in observations),
        "negative_count": sum(not row.positive for row in observations),
        "results": results,
        "nuisance_only": {
            "feature_dimension": int(nuisance.shape[1]),
            **_serializable_result(nuisance_result),
        },
        "_route_selection_features": route_selection_features,
    }


def _all_attack_sensitivity(
    observations: Sequence[Observation], universe: Sequence[Observation]
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for prefix_name, token_limit in PREFIXES:
        for family in FEATURES:
            result = _evaluate_matrix(
                observations, _feature_matrix(observations, family, token_limit)
            )
            rows.append(
                {
                    "prefix": prefix_name,
                    "token_limit": token_limit,
                    "feature": family,
                    **_serializable_result(result),
                }
            )
    nuisance = _metadata_matrix(observations, universe)
    nuisance_result = _evaluate_matrix(observations, nuisance)
    by_brief: dict[str, Any] = {}
    scores = nuisance_result["_scores"]
    labels = torch.tensor([row.positive for row in observations])
    for brief in sorted({row.brief for row in observations}):
        mask = torch.tensor([row.brief == brief for row in observations])
        selected_labels = labels[mask]
        by_brief[brief] = {
            "trace_count": int(mask.sum().item()),
            "positive_count": int(selected_labels.sum().item()),
            "mean_positive_score": (
                float(scores[mask][selected_labels].mean().item())
                if bool(selected_labels.any())
                else None
            ),
            "mean_negative_score": float(
                scores[mask][~selected_labels].mean().item()
            ),
        }
    return {
        "trace_count": len(observations),
        "positive_count": sum(row.positive for row in observations),
        "results": rows,
        "nuisance_only": {
            "feature_dimension": int(nuisance.shape[1]),
            **_serializable_result(nuisance_result),
            "by_brief": by_brief,
        },
    }


def _matched_controls(
    observations: Sequence[Observation], token_limit: int | None
) -> dict[str, Any]:
    eligible = [row for row in observations if row.brief == "absent"]
    attacks = [row for row in eligible if row.arm == "attack"]
    all_features = _feature_matrix(eligible, "route_selection", token_limit)
    attack_indices = [index for index, row in enumerate(eligible) if row.arm == "attack"]
    scored: dict[str, float] = {}
    for held_out in sorted({row.fold for row in attacks}):
        training_positions = [
            position
            for position, index in enumerate(attack_indices)
            if eligible[index].fold != held_out
        ]
        training_indices = torch.tensor(
            [attack_indices[position] for position in training_positions], dtype=torch.long
        )
        labels = torch.tensor(
            [eligible[index].positive for index in training_indices.tolist()]
        )
        model = fit_ridge_classifier(
            all_features.index_select(0, training_indices),
            labels.float() * 2.0 - 1.0,
        )
        test_indices = [
            index for index, row in enumerate(eligible) if row.fold == held_out
        ]
        test_tensor = torch.tensor(test_indices, dtype=torch.long)
        test_scores = model.score(all_features.index_select(0, test_tensor))
        for index, score in zip(test_indices, test_scores, strict=True):
            scored[eligible[index].trace_id] = float(score.item())

    groups: dict[str, list[Observation]] = {}
    for row in eligible:
        groups.setdefault(row.pair_group_id, []).append(row)
    positive_margins: list[float] = []
    resisted_margins: list[float] = []
    rows: list[dict[str, Any]] = []
    for pair_group, members in sorted(groups.items()):
        by_arm = {row.arm: row for row in members}
        attack = by_arm["attack"]
        control_max = max(
            scored[by_arm["clean"].trace_id],
            scored[by_arm["benign_control"].trace_id],
        )
        margin = scored[attack.trace_id] - control_max
        (positive_margins if attack.positive else resisted_margins).append(margin)
        rows.append(
            {
                "pair_group_id": pair_group,
                "attack_positive": attack.positive,
                "clean_score": scored[by_arm["clean"].trace_id],
                "benign_score": scored[by_arm["benign_control"].trace_id],
                "attack_score": scored[attack.trace_id],
                "attack_minus_max_control": margin,
            }
        )
    return {
        "eligible_triplet_count": len(groups),
        "positive_triplet_count": len(positive_margins),
        "positive_attack_top1_count": sum(value > 0 for value in positive_margins),
        "positive_attack_top1_rate": sum(value > 0 for value in positive_margins)
        / len(positive_margins),
        "positive_mean_margin": statistics.mean(positive_margins),
        "resisted_mean_margin": statistics.mean(resisted_margins),
        "triplets": rows,
    }


def _load_final_prefill(trace_dir: Path) -> RoutingSequence:
    rows = [
        json.loads(line)
        for line in (trace_dir / "manifest.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line
    ]
    prefill_rows = [row for row in rows if row["phase"] == "prefill"]
    if len(prefill_rows) != 1:
        raise ValueError(f"{trace_dir} does not have exactly one prefill")
    return load_sequence(
        trace_dir,
        prefill_rows,
        [[True] * len(row["token_ids"]) for row in prefill_rows],
    )


def _prefill_diagnostic(observations: Sequence[Observation]) -> dict[str, Any]:
    matrices: dict[str, list[torch.Tensor]] = {
        "route_selection": [],
        "route_probability": [],
    }
    token_counts: list[int] = []
    for observation in observations:
        sequence = _load_final_prefill(observation.trace_dir)
        token_counts.append(len(sequence.token_ids))
        for family in matrices:
            matrices[family].append(extract_features(sequence, family))
    results: dict[str, Any] = {}
    for family, values in matrices.items():
        result = _evaluate_matrix(observations, torch.stack(values))
        results[family] = _serializable_result(result)
    return {
        "interpretation": "context_only_propensity_diagnostic",
        "token_count_min": min(token_counts),
        "token_count_median": statistics.median(token_counts),
        "token_count_max": max(token_counts),
        "results": results,
    }


def _rolling_means(values: torch.Tensor, width: int) -> torch.Tensor:
    if values.numel() < width:
        return values.mean().reshape(1)
    return values.unfold(0, width, 1).mean(dim=-1)


def _window_hash_distance(
    sequence: RoutingSequence, left_start: int, boundary: int, right_end: int
) -> float:
    left = subset_routing_sequence(sequence, tuple(range(left_start, boundary)))
    right = subset_routing_sequence(sequence, tuple(range(boundary, right_end)))
    left_hash = extract_features(left, "token_hash").double()
    right_hash = extract_features(right, "token_hash").double()
    denominator = left_hash.norm() * right_hash.norm()
    if float(denominator.item()) == 0.0:
        return 0.0
    return float((1.0 - (left_hash @ right_hash) / denominator).item())


def _percentile(value: float, reference: Sequence[float]) -> float | None:
    if not reference:
        return None
    below = sum(candidate < value for candidate in reference)
    tied = sum(candidate == value for candidate in reference)
    return (below + 0.5 * tied) / len(reference)


def _negative_boundary_distributions(
    observations: Sequence[Observation],
    token_scores: dict[str, torch.Tensor],
    width: int,
) -> tuple[list[float], list[float]]:
    route_deltas: list[float] = []
    token_distances: list[float] = []
    for row in observations:
        scores = token_scores[row.trace_id]
        for boundary in range(width, len(row.sequence.token_ids) - width + 1):
            route_deltas.append(
                float(
                    scores[boundary : boundary + width].mean().item()
                    - scores[boundary - width : boundary].mean().item()
                )
            )
            token_distances.append(
                _window_hash_distance(
                    row.sequence, boundary - width, boundary, boundary + width
                )
            )
    return route_deltas, token_distances


def _boundary_analysis(observations: Sequence[Observation]) -> dict[str, Any]:
    width = 8
    positives = [row for row in observations if row.arm == "attack" and row.positive]
    rows: list[dict[str, Any]] = []
    for held_out in sorted({row.fold for row in positives}):
        for brief in ("absent", "present"):
            held_positives = [
                row for row in positives if row.fold == held_out and row.brief == brief
            ]
            if not held_positives:
                continue
            training_normals = [
                row
                for row in observations
                if row.fold != held_out
                and row.brief == brief
                and (row.arm != "attack" or not row.positive)
            ]
            profile = mean_probability_profile([row.sequence for row in training_normals])
            training_window_scores: list[torch.Tensor] = []
            for normal in training_normals:
                token_jsd = score_sequence(
                    normal.sequence, profile, window_width=width
                )["token_jsd"]
                training_window_scores.append(_rolling_means(token_jsd, width))
            threshold = float(
                torch.quantile(torch.cat(training_window_scores), 0.95).item()
            )

            held_negatives = [
                row
                for row in observations
                if row.fold == held_out
                and row.brief == brief
                and row.arm == "attack"
                and not row.positive
            ]
            negative_scores = {
                row.trace_id: score_sequence(
                    row.sequence, profile, window_width=width
                )["token_jsd"]
                for row in held_negatives
            }
            route_reference, token_reference = _negative_boundary_distributions(
                held_negatives, negative_scores, width
            )

            for positive in held_positives:
                boundary = positive.boundary
                if boundary is None:
                    raise ValueError("positive trace lacks boundary")
                scored = score_sequence(
                    positive.sequence, profile, window_width=width
                )
                token_jsd = scored["token_jsd"]
                pre_start = max(0, boundary - width)
                post_end = min(len(positive.sequence.token_ids), boundary + width)
                pre_score = (
                    float(token_jsd[pre_start:boundary].mean().item())
                    if pre_start < boundary
                    else None
                )
                post_score = float(token_jsd[boundary:post_end].mean().item())
                route_delta = None if pre_score is None else post_score - pre_score
                token_distance = (
                    _window_hash_distance(
                        positive.sequence, pre_start, boundary, post_end
                    )
                    if pre_start < boundary < post_end
                    else None
                )
                rolling = _rolling_means(token_jsd, width)
                crossings = (rolling > threshold).nonzero(as_tuple=False).reshape(-1)
                first_alarm_end = (
                    int(crossings[0].item()) + width - 1
                    if crossings.numel()
                    else None
                )
                peak_start = int(rolling.argmax().item())
                peak_end = peak_start + min(width, len(token_jsd))
                rows.append(
                    {
                        "trace_id": positive.trace_id,
                        "fold": held_out,
                        "brief": brief,
                        "domain": positive.domain,
                        "boundary": boundary,
                        "pre_boundary_mean_jsd": pre_score,
                        "post_boundary_mean_jsd": post_score,
                        "post_minus_pre_jsd": route_delta,
                        "route_delta_percentile_among_heldout_resisted_positions": (
                            _percentile(route_delta, route_reference)
                            if route_delta is not None
                            else None
                        ),
                        "token_hash_pre_post_distance": token_distance,
                        "token_distance_percentile_among_heldout_resisted_positions": (
                            _percentile(token_distance, token_reference)
                            if token_distance is not None
                            else None
                        ),
                        "negative_position_count": len(route_reference),
                        "training_negative_p95_threshold": threshold,
                        "first_alarm_window_end": first_alarm_end,
                        "first_alarm_end_minus_boundary": (
                            first_alarm_end - boundary
                            if first_alarm_end is not None
                            else None
                        ),
                        "peak_window_start": peak_start,
                        "peak_window_end": peak_end,
                        "peak_intersects_boundary_plus_minus_8": (
                            peak_start < boundary + width
                            and peak_end > max(0, boundary - width)
                        ),
                    }
                )

    comparable = [row for row in rows if row["post_minus_pre_jsd"] is not None]
    latencies = [
        row["first_alarm_end_minus_boundary"]
        for row in rows
        if row["first_alarm_end_minus_boundary"] is not None
    ]
    by_brief: dict[str, Any] = {}
    for brief in ("absent", "present"):
        selected = [row for row in comparable if row["brief"] == brief]
        by_brief[brief] = {
            "positive_count": sum(row["brief"] == brief for row in rows),
            "comparable_boundary_count": len(selected),
            "post_score_increase_count": sum(
                row["post_minus_pre_jsd"] > 0 for row in selected
            ),
            "median_post_minus_pre_jsd": (
                statistics.median(row["post_minus_pre_jsd"] for row in selected)
                if selected
                else None
            ),
            "median_route_delta_percentile": (
                statistics.median(
                    row["route_delta_percentile_among_heldout_resisted_positions"]
                    for row in selected
                    if row[
                        "route_delta_percentile_among_heldout_resisted_positions"
                    ]
                    is not None
                )
                if any(
                    row["route_delta_percentile_among_heldout_resisted_positions"]
                    is not None
                    for row in selected
                )
                else None
            ),
            "peak_near_boundary_count": sum(
                row["peak_intersects_boundary_plus_minus_8"] for row in selected
            ),
        }
    return {
        "window_width": width,
        "positive_count": len(rows),
        "comparable_boundary_count": len(comparable),
        "post_score_increase_count": sum(
            row["post_minus_pre_jsd"] > 0 for row in comparable
        ),
        "median_post_minus_pre_jsd": statistics.median(
            row["post_minus_pre_jsd"] for row in comparable
        ),
        "peak_near_boundary_count": sum(
            row["peak_intersects_boundary_plus_minus_8"] for row in rows
        ),
        "alarm_count": len(latencies),
        "median_first_alarm_end_minus_boundary": (
            statistics.median(latencies) if latencies else None
        ),
        "by_brief": by_brief,
        "traces": rows,
    }


def _compact_headline(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "prefix": row["prefix"],
            "feature": row["feature"],
            "auroc": row["metrics"]["auroc"],
            "average_precision": row["metrics"]["average_precision"],
            "mean_score_difference": row["metrics"]["mean_score_difference"],
            "domain_direction_count": (
                row.get("leave_one_domain_out", {}).get(
                    "directionally_consistent_domain_count"
                )
            ),
        }
        for row in payload["headline"]["results"]
    ]


def calculate(run_dir: Path) -> dict[str, Any]:
    observations, valid = _load_observations(run_dir)
    attacks = [row for row in observations if row.arm == "attack"]
    headline_rows = [row for row in attacks if row.brief == "absent"]
    headline = _headline_analysis(headline_rows, attacks)
    headline.pop("_route_selection_features")
    matched = {
        prefix_name: _matched_controls(observations, token_limit)
        for prefix_name, token_limit in PREFIXES
    }
    payload = {
        "schema_version": 1,
        "analysis_id": "agent-v2.5-b1-routing-development-v1",
        "analysis_role": "development_only_signal_search",
        "plan": PLAN,
        "plan_freeze_commit": "c28a7cc",
        "experiment_id": EXPECTED_EXPERIMENT_ID,
        "sample_index_sha256": EXPECTED_SAMPLE_INDEX_SHA256,
        "routing_validation_pass_count": valid,
        "headline": headline,
        "all_attack_sensitivity": _all_attack_sensitivity(attacks, attacks),
        "matched_controls": matched,
        "prefill_context_only": _prefill_diagnostic(headline_rows),
        "boundary_timing": _boundary_analysis(observations),
        "interpretation_limits": [
            "B1 is used for method selection and is not an independent confirmation set.",
            "The headline cohort conditions on response-brief absence after behavior-only inspection.",
            "No identical attack prompt is repeated across seeds with both outcomes.",
            "Token and nuisance controls are low-cost sanity checks, not strong production baselines.",
        ],
    }
    payload["compact_headline"] = _compact_headline(payload)
    return payload


def main() -> int:
    args = _args()
    run_dir = args.run_dir.resolve()
    payload = calculate(run_dir)
    output = run_dir / "routing_analysis.json"
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "analysis_id": payload["analysis_id"],
                "routing_validation_pass_count": payload[
                    "routing_validation_pass_count"
                ],
                "compact_headline": payload["compact_headline"],
                "prefill_context_only": {
                    family: row["metrics"]
                    for family, row in payload["prefill_context_only"][
                        "results"
                    ].items()
                },
                "boundary_timing": {
                    key: value
                    for key, value in payload["boundary_timing"].items()
                    if key != "traces"
                },
                "output": str(output),
            },
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
