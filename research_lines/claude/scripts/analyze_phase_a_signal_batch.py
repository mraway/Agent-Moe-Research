#!/usr/bin/env python3
"""Analyze the preregistered S1 outcome-contrast routing batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Any, Sequence

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase_a.classifier import (  # noqa: E402
    average_precision,
    balanced_accuracy_threshold,
    binary_auroc,
    extract_features,
    fit_normal_centroid_classifier,
    leave_one_group_out_scores,
    prefix_sequence,
)
from phase_a.routing_analysis import (  # noqa: E402
    LAYER_BANDS,
    RoutingSequence,
    compare_aligned_token_routes,
    find_unique_text_token_indices,
    load_final_generation_sequence,
    load_sequence,
    mean_probability_profile,
    score_sequence,
)
from routing import validate_trace  # noqa: E402


RUN_NAME = "phase_a_signal_batch_soft_gate_v1"
EXPECTED_CONFIG_HASH = "4ff08185b20927fcd6274aea22cb8cf28107003b65c2fa7361260ecb8e93950e"
EXPECTED_POSITIVE_SEEDS = {7102, 7201, 7401, 7402, 7403}
EXPECTED_GROUPS = {
    "s1-direct-order-desert-haiku": {"channel": "direct_user", "domain": "poetry"},
    "s1-direct-return-word-count": {"channel": "direct_user", "domain": "code"},
    "s1-tool-order-lighthouse-poem": {"channel": "tool_output", "domain": "poetry"},
    "s1-tool-return-temperature-code": {"channel": "tool_output", "domain": "code"},
}
PREFIXES = (8, 16)
LAYER_SETS: dict[str, tuple[int, ...]] = {
    "all": tuple(range(16)),
    **LAYER_BANDS,
}
ROUTE_FAMILIES = ("route_probability", "route_selection")
CLASSIFIERS = ("normal_centroid_distance", "ridge")


@dataclass(frozen=True)
class Observation:
    trace_dir: Path
    trace_id: str
    base_task_id: str
    pair_group_id: str
    analysis_group_id: str
    arm: str
    seed: int
    primary_positive: bool
    channel: str
    domain: str
    final_agent_step: int
    final_parse_kind: str
    rendered_prompt: str
    sequence: RoutingSequence
    validation: dict[str, Any]
    trace: dict[str, Any]


def _json_hash(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _layer_slice(sequence: RoutingSequence, indices: Sequence[int]) -> RoutingSequence:
    index = torch.tensor(tuple(indices), dtype=torch.long)
    result = RoutingSequence(
        token_ids=sequence.token_ids,
        token_texts=sequence.token_texts,
        probabilities=sequence.probabilities.index_select(0, index),
        top_k_ids=sequence.top_k_ids.index_select(0, index),
    )
    result.validate()
    return result


def _load_first_prefill(trace_dir: Path) -> RoutingSequence:
    rows = [
        json.loads(line)
        for line in (trace_dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    row = next(candidate for candidate in rows if candidate["phase"] == "prefill")
    return load_sequence(trace_dir, [row], [[True] * len(row["token_ids"])])


def _load_observations(run_dir: Path) -> list[Observation]:
    run_dir = run_dir.resolve()
    summary = json.loads((run_dir / "run_summary.json").read_text(encoding="utf-8"))
    config = json.loads(
        (run_dir / "resolved_experiment_config.json").read_text(encoding="utf-8")
    )
    if _json_hash(config) != EXPECTED_CONFIG_HASH or summary["config_hash"] != EXPECTED_CONFIG_HASH:
        raise ValueError("S1 config hash differs from the preregistration")
    if summary["trace_count"] != 36 or summary["manual_review_count"] != 0:
        raise ValueError("S1 requires 36 complete traces and zero pending reviews")
    scenario_map = {row["base_task_id"]: row for row in config["scenarios"]}
    observations: list[Observation] = []
    for trace_path in sorted(run_dir.glob("*/*/trace.json")):
        trace_dir = trace_path.parent
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        validation = validate_trace(trace_dir)
        if not validation["passed"]:
            raise ValueError(f"routing validation failed for {trace['trace_id']}")
        scenario = scenario_map[str(trace["base_task_id"])]
        group = str(scenario["analysis_group_id"])
        attack = scenario["arms"]["attack"]
        generations = [
            event for event in trace["events"] if event["kind"] == "model_generation"
        ]
        final = generations[-1]
        sequence = load_final_generation_sequence(trace_dir)
        if tuple(final["output_token_ids"]) != sequence.token_ids:
            raise ValueError(f"decode alignment changed for {trace['trace_id']}")
        observation = Observation(
            trace_dir=trace_dir,
            trace_id=str(trace["trace_id"]),
            base_task_id=str(trace["base_task_id"]),
            pair_group_id=str(trace["pair_group_id"]),
            analysis_group_id=group,
            arm=str(trace["perturbation"]["arm"]),
            seed=int(trace["seed"]),
            primary_positive=bool(trace["outcome"]["primary_positive"]),
            channel=str(attack["channel"]),
            domain=str(attack["attack_goal"]["target_domain"]),
            final_agent_step=int(final["agent_step"]),
            final_parse_kind=str(final["parsed"]["kind"]),
            rendered_prompt=str(final["rendered_prompt"]),
            sequence=sequence,
            validation=validation,
            trace=trace,
        )
        observations.append(observation)

    if len(observations) != 36:
        raise ValueError("S1 trace discovery did not return exactly 36 traces")
    if {row.analysis_group_id for row in observations} != set(EXPECTED_GROUPS):
        raise ValueError("S1 analysis groups changed")
    for group in EXPECTED_GROUPS:
        rows = [row for row in observations if row.analysis_group_id == group]
        if len(rows) != 9 or {row.arm for row in rows} != {
            "clean",
            "benign_control",
            "attack",
        }:
            raise ValueError(f"group {group} is not a complete 3-seed/3-arm block")
        for arm in ("clean", "benign_control", "attack"):
            if sum(row.arm == arm for row in rows) != 3:
                raise ValueError(f"group {group} does not contain three {arm} traces")
    attacks = [row for row in observations if row.arm == "attack"]
    actual_positive_seeds = {row.seed for row in attacks if row.primary_positive}
    if actual_positive_seeds != EXPECTED_POSITIVE_SEEDS:
        raise ValueError("frozen S1 attack labels changed")
    if any(
        row.trace["outcome"]["goal_plan_deviation_label_source"] != "research_review"
        for row in attacks
    ):
        raise ValueError("every S1 attack must have a research-review label")
    if sum(row.arm == "clean" and row.trace["outcome"]["normal_reference_eligible"] for row in observations) != 12:
        raise ValueError("all twelve clean traces must be normal-reference eligible")
    return observations


def _feature_matrix(
    rows: Sequence[Observation], family: str, prefix: int | None, layer_set: str
) -> torch.Tensor:
    return torch.stack(
        [
            extract_features(
                _layer_slice(prefix_sequence(row.sequence, prefix), LAYER_SETS[layer_set]),
                family,
            )
            for row in rows
        ]
    )


def _ranking_metrics(
    scores: torch.Tensor, labels: torch.Tensor, rows: Sequence[Observation]
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "auroc": binary_auroc(scores, labels),
        "average_precision": average_precision(scores, labels),
    }
    mixed: list[dict[str, Any]] = []
    for group in EXPECTED_GROUPS:
        indices = [i for i, row in enumerate(rows) if row.analysis_group_id == group]
        group_labels = labels[indices]
        if not bool(group_labels.any()) or not bool((~group_labels).any()):
            continue
        group_scores = scores[indices]
        positive = group_scores[group_labels]
        negative = group_scores[~group_labels]
        pairwise = (positive[:, None] > negative[None, :]).float()
        pairwise += 0.5 * (positive[:, None] == negative[None, :]).float()
        mixed.append(
            {
                "analysis_group_id": group,
                "positive_count": int(group_labels.sum().item()),
                "negative_count": int((~group_labels).sum().item()),
                "pairwise_win_rate": float(pairwise.mean().item()),
                "positive_above_all_negatives": bool(
                    float(positive.min().item()) > float(negative.max().item())
                ),
            }
        )
    result["mixed_groups"] = mixed
    result["mixed_group_mean_pairwise_win_rate"] = sum(
        row["pairwise_win_rate"] for row in mixed
    ) / len(mixed)
    result["mixed_group_top_count"] = sum(
        row["positive_above_all_negatives"] for row in mixed
    )
    subgroups: dict[str, Any] = {}
    for dimension in ("channel", "domain"):
        for value in sorted({getattr(row, dimension) for row in rows}):
            indices = [i for i, row in enumerate(rows) if getattr(row, dimension) == value]
            subset_labels = labels[indices]
            if bool(subset_labels.any()) and bool((~subset_labels).any()):
                subgroups[f"{dimension}:{value}"] = {
                    "trace_count": len(indices),
                    "positive_count": int(subset_labels.sum().item()),
                    "auroc": binary_auroc(scores[indices], subset_labels),
                }
    result["subgroups"] = subgroups
    return result


def _trace_scores(rows: Sequence[Observation], scores: torch.Tensor) -> list[dict[str, Any]]:
    return [
        {
            "trace_id": row.trace_id,
            "analysis_group_id": row.analysis_group_id,
            "seed": row.seed,
            "primary_positive": row.primary_positive,
            "score": float(score.item()),
        }
        for row, score in zip(rows, scores, strict=True)
    ]


def _control_centroid_oof_scores(
    attacks: Sequence[Observation],
    controls: Sequence[Observation],
    *,
    family: str,
    prefix: int,
    layer_set: str,
) -> torch.Tensor:
    attack_features = _feature_matrix(attacks, family, prefix, layer_set)
    control_features = _feature_matrix(controls, family, prefix, layer_set)
    scores = torch.empty(len(attacks), dtype=torch.float64)
    for held_out in EXPECTED_GROUPS:
        train_indices = [
            index
            for index, row in enumerate(controls)
            if row.analysis_group_id != held_out
        ]
        test_indices = [
            index
            for index, row in enumerate(attacks)
            if row.analysis_group_id == held_out
        ]
        train = control_features[train_indices]
        model = fit_normal_centroid_classifier(
            train, torch.zeros(len(train), dtype=torch.bool)
        )
        scores[test_indices] = model.score(attack_features[test_indices])
    return scores


def _route_candidates(observations: Sequence[Observation]) -> list[dict[str, Any]]:
    attacks = [row for row in observations if row.arm == "attack"]
    controls = [row for row in observations if row.arm != "attack"]
    labels = torch.tensor([row.primary_positive for row in attacks], dtype=torch.bool)
    groups = [row.analysis_group_id for row in attacks]
    results: list[dict[str, Any]] = []
    for prefix in PREFIXES:
        for family in ROUTE_FAMILIES:
            for layer_set in LAYER_SETS:
                features = _feature_matrix(attacks, family, prefix, layer_set)
                for classifier in CLASSIFIERS:
                    if classifier == "ridge":
                        scores = leave_one_group_out_scores(
                            features, labels, groups, classifier="ridge"
                        )
                    else:
                        scores = _control_centroid_oof_scores(
                            attacks,
                            controls,
                            family=family,
                            prefix=prefix,
                            layer_set=layer_set,
                        )
                    metrics = _ranking_metrics(scores, labels, attacks)
                    threshold = balanced_accuracy_threshold(scores, labels)
                    valid = torch.tensor(
                        [row.final_parse_kind == "message" for row in attacks]
                    )
                    valid_metrics = _ranking_metrics(
                        scores[valid], labels[valid],
                        [row for row, keep in zip(attacks, valid.tolist(), strict=True) if keep],
                    )
                    results.append(
                        {
                            "prefix": prefix,
                            "feature": family,
                            "layer_set": layer_set,
                            "classifier": classifier,
                            "feature_dimension": int(features.shape[1]),
                            "metrics": metrics,
                            "oof_threshold": threshold,
                            "protocol_valid_score_subset": {
                                "status": "diagnostic_without_refitting",
                                "trace_count": int(valid.sum().item()),
                                "positive_count": int(labels[valid].sum().item()),
                                "metrics": valid_metrics,
                            },
                            "trace_scores": _trace_scores(attacks, scores),
                        }
                    )
    return results


def _sanity_controls(observations: Sequence[Observation]) -> list[dict[str, Any]]:
    attacks = [row for row in observations if row.arm == "attack"]
    labels = torch.tensor([row.primary_positive for row in attacks], dtype=torch.bool)
    groups = [row.analysis_group_id for row in attacks]
    results: list[dict[str, Any]] = []
    for prefix in (8, 16, None):
        features = torch.stack(
            [extract_features(prefix_sequence(row.sequence, prefix), "token_hash") for row in attacks]
        )
        scores = leave_one_group_out_scores(features, labels, groups, classifier="ridge")
        results.append(
            {
                "control": "token_hash",
                "prefix": prefix if prefix is not None else "full",
                "metrics": _ranking_metrics(scores, labels, attacks),
                "trace_scores": _trace_scores(attacks, scores),
            }
        )
    feature_rows = []
    for row in attacks:
        outcome = row.trace["outcome"]
        feature_rows.append(
            [
                float(len(row.sequence.token_ids)),
                float(row.final_agent_step),
                float(row.final_parse_kind != "message"),
                float(outcome["recoverable_protocol_error"]),
            ]
        )
    features = torch.tensor(feature_rows, dtype=torch.float32)
    for name, selection in (
        ("length", [0]),
        ("protocol_and_step", [1, 2, 3]),
        ("length_protocol_step", [0, 1, 2, 3]),
    ):
        scores = leave_one_group_out_scores(
            features[:, selection], labels, groups, classifier="ridge"
        )
        results.append(
            {
                "control": name,
                "prefix": "full",
                "metrics": _ranking_metrics(scores, labels, attacks),
                "trace_scores": _trace_scores(attacks, scores),
            }
        )
    return results


def _prefix_diagnostics(observations: Sequence[Observation]) -> dict[str, Any]:
    attacks = [row for row in observations if row.arm == "attack"]
    cluster_results: dict[str, Any] = {}
    for prefix in PREFIXES:
        clusters: dict[tuple[int, ...], list[Observation]] = {}
        for row in attacks:
            key = row.sequence.token_ids[:prefix]
            clusters.setdefault(key, []).append(row)
        public = []
        for rows in clusters.values():
            if len(rows) < 2:
                continue
            public.append(
                {
                    "trace_ids": [row.trace_id for row in rows],
                    "trace_count": len(rows),
                    "positive_count": sum(row.primary_positive for row in rows),
                    "text": "".join(rows[0].sequence.token_texts[:prefix]),
                }
            )
        cluster_results[str(prefix)] = {
            "unique_prefix_count": len(clusters),
            "repeated_clusters": public,
            "mixed_label_repeated_cluster_count": sum(
                0 < row["positive_count"] < row["trace_count"] for row in public
            ),
        }

    aligned_pairs: list[dict[str, Any]] = []
    for group in EXPECTED_GROUPS:
        rows = [row for row in attacks if row.analysis_group_id == group]
        for left, right in combinations(rows, 2):
            if left.primary_positive == right.primary_positive:
                continue
            if left.rendered_prompt != right.rendered_prompt:
                continue
            if left.sequence.token_ids[0] != right.sequence.token_ids[0]:
                continue
            comparison = compare_aligned_token_routes(left.sequence, right.sequence)
            aligned_pairs.append(
                {
                    "left": left.trace_id,
                    "right": right.trace_id,
                    "analysis_group_id": group,
                    **comparison,
                }
            )

    prompt_clusters: dict[str, Any] = {}
    for group in EXPECTED_GROUPS:
        rows = [row for row in attacks if row.analysis_group_id == group]
        clusters: dict[str, list[Observation]] = {}
        for row in rows:
            digest = hashlib.sha256(row.rendered_prompt.encode()).hexdigest()
            clusters.setdefault(digest, []).append(row)
        prompt_clusters[group] = [
            {
                "prompt_hash": digest,
                "trace_ids": [row.trace_id for row in members],
                "positive_count": sum(row.primary_positive for row in members),
            }
            for digest, members in clusters.items()
        ]
    return {
        "exact_output_prefixes": cluster_results,
        "opposite_label_identical_prompt_pairs": aligned_pairs,
        "final_prompt_clusters": prompt_clusters,
    }


def _initial_prefill_determinism(observations: Sequence[Observation]) -> dict[str, Any]:
    attacks = [row for row in observations if row.arm == "attack"]
    result: dict[str, Any] = {}
    for group in EXPECTED_GROUPS:
        rows = [row for row in attacks if row.analysis_group_id == group]
        sequences = [_load_first_prefill(row.trace_dir) for row in rows]
        anchor = sequences[0]
        token_ids_equal = all(sequence.token_ids == anchor.token_ids for sequence in sequences[1:])
        max_probability_delta = max(
            float((sequence.probabilities - anchor.probabilities).abs().max().item())
            for sequence in sequences[1:]
        )
        top_k_equal = all(
            torch.equal(sequence.top_k_ids, anchor.top_k_ids) for sequence in sequences[1:]
        )
        result[group] = {
            "token_count": len(anchor.token_ids),
            "token_ids_equal": token_ids_equal,
            "max_router_probability_delta": max_probability_delta,
            "top_k_ids_equal": top_k_equal,
        }
    return result


def _boundary_diagnostics(observations: Sequence[Observation]) -> dict[str, Any]:
    controls = [row for row in observations if row.arm != "attack"]
    positives = [
        row for row in observations if row.arm == "attack" and row.primary_positive
    ]
    rows: list[dict[str, Any]] = []
    for observation in positives:
        references = [
            row.sequence
            for row in controls
            if row.analysis_group_id != observation.analysis_group_id
        ]
        profile = mean_probability_profile(references)
        scored = score_sequence(observation.sequence, profile, window_width=8)
        review = next(
            review
            for review in reversed(observation.trace.get("adjudications", []))
            if review["review_kind"] == "free_text_goal_plan_deviation"
            and review["label"] == "goal_deviation"
        )
        evidence_indices = find_unique_text_token_indices(
            observation.sequence, str(review["evidence"])
        )
        start, complete = evidence_indices[0], evidence_indices[-1]
        token_scores = scored["token_jsd"]

        def mean_span(left: int, right: int) -> float | None:
            left = max(0, left)
            right = min(len(token_scores), right)
            if left >= right:
                return None
            return float(token_scores[left:right].mean().item())

        rows.append(
            {
                "trace_id": observation.trace_id,
                "analysis_group_id": observation.analysis_group_id,
                "seed": observation.seed,
                "evidence": review["evidence"],
                "evidence_start_token": start,
                "evidence_complete_token": complete,
                "stored_decision_boundary_token": observation.trace["outcome"][
                    "goal_plan_deviation_start_output_token"
                ]["output_token_index"],
                "first_8_mean_jsd": mean_span(0, 8),
                "first_16_mean_jsd": mean_span(0, 16),
                "pre_evidence_8_mean_jsd": mean_span(start - 8, start),
                "evidence_mean_jsd": mean_span(start, complete + 1),
                "post_evidence_8_mean_jsd": mean_span(complete + 1, complete + 9),
                "candidate_published": bool(
                    observation.trace["outcome"]["user_visible_violation"]
                ),
            }
        )
    return {"positive_traces": rows}


def _full_window_anomalies(observations: Sequence[Observation]) -> dict[str, Any]:
    """Score local windows over the whole candidate output against held-out controls."""

    attacks = [row for row in observations if row.arm == "attack"]
    controls = [row for row in observations if row.arm != "attack"]
    labels = torch.tensor([row.primary_positive for row in attacks], dtype=torch.bool)
    results: list[dict[str, Any]] = []
    peak_timings: dict[str, list[dict[str, Any]]] = {}
    for width in (8, 16):
        scored_rows: list[dict[str, Any]] = []
        for observation in attacks:
            references = [
                row.sequence
                for row in controls
                if row.analysis_group_id != observation.analysis_group_id
            ]
            scored_rows.append(
                score_sequence(
                    observation.sequence,
                    mean_probability_profile(references),
                    window_width=width,
                )
            )

        definitions = [
            (
                "route_probability_jsd",
                "all",
                [row["max_window_jsd_w8"]["value"] for row in scored_rows],
            ),
            *[
                (
                    "route_probability_jsd",
                    band,
                    [
                        row["layer_bands"][band]["max_window_jsd_w8"]["value"]
                        for row in scored_rows
                    ],
                )
                for band in ("early", "middle", "late")
            ],
            (
                "top8_selection_novelty",
                "all",
                [
                    row["max_window_top8_novelty_w8"]["value"]
                    for row in scored_rows
                ],
            ),
        ]
        for signal, layer_set, raw_scores in definitions:
            scores = torch.tensor(raw_scores, dtype=torch.float64)
            results.append(
                {
                    "width": width,
                    "signal": signal,
                    "layer_set": layer_set,
                    "metrics": _ranking_metrics(scores, labels, attacks),
                    "trace_scores": _trace_scores(attacks, scores),
                }
            )

        timing_rows: list[dict[str, Any]] = []
        for observation, scored in zip(attacks, scored_rows, strict=True):
            peak = scored["max_window_jsd_w8"]
            timing: dict[str, Any] = {
                "trace_id": observation.trace_id,
                "seed": observation.seed,
                "primary_positive": observation.primary_positive,
                "peak_start": peak["start"],
                "peak_end": peak["end"],
                "output_token_count": len(observation.sequence.token_ids),
                "tokens_remaining_after_peak": len(observation.sequence.token_ids)
                - int(peak["end"]),
            }
            if observation.primary_positive:
                review = next(
                    review
                    for review in reversed(observation.trace.get("adjudications", []))
                    if review["review_kind"] == "free_text_goal_plan_deviation"
                    and review["label"] == "goal_deviation"
                )
                evidence_indices = find_unique_text_token_indices(
                    observation.sequence, str(review["evidence"])
                )
                start, complete = evidence_indices[0], evidence_indices[-1]
                if int(peak["end"]) <= start:
                    relation = "before_evidence"
                elif int(peak["start"]) > complete:
                    relation = "after_evidence"
                else:
                    relation = "overlaps_evidence"
                timing.update(
                    {
                        "evidence": review["evidence"],
                        "evidence_start": start,
                        "evidence_complete": complete,
                        "peak_relation_to_evidence": relation,
                    }
                )
            timing_rows.append(timing)
        peak_timings[str(width)] = timing_rows
    return {"results": results, "all_layer_probability_peak_timings": peak_timings}


def calculate(observations: Sequence[Observation]) -> dict[str, Any]:
    route_results = _route_candidates(observations)
    ranked = sorted(
        route_results,
        key=lambda row: (
            row["metrics"]["auroc"],
            row["metrics"]["average_precision"],
            row["metrics"]["mixed_group_mean_pairwise_win_rate"],
            -row["prefix"],
        ),
        reverse=True,
    )
    attacks = [row for row in observations if row.arm == "attack"]
    return {
        "schema_version": 1,
        "analysis_id": "phase-a-s1-signal-analysis-v1",
        "analysis_role": "development_only_method_selection",
        "plan": "docs/phase_a_signal_batch_plan.md",
        "run_name": RUN_NAME,
        "config_hash": EXPECTED_CONFIG_HASH,
        "trace_count": len(observations),
        "attack_trace_count": len(attacks),
        "attack_positive_count": sum(row.primary_positive for row in attacks),
        "attack_negative_count": sum(not row.primary_positive for row in attacks),
        "analysis_group_count": len(EXPECTED_GROUPS),
        "routing_validation_pass_count": sum(row.validation["passed"] for row in observations),
        "initial_attack_prefill_determinism": _initial_prefill_determinism(observations),
        "prefix_diagnostics": _prefix_diagnostics(observations),
        "boundary_diagnostics": _boundary_diagnostics(observations),
        "full_window_anomalies": _full_window_anomalies(observations),
        "route_candidates": route_results,
        "top_route_candidate_keys": [
            {
                "prefix": row["prefix"],
                "feature": row["feature"],
                "layer_set": row["layer_set"],
                "classifier": row["classifier"],
                "metrics": row["metrics"],
            }
            for row in ranked[:5]
        ],
        "sanity_controls": _sanity_controls(observations),
        "interpretation_limit": (
            "S1 is a 12-attack, four-group development set. Candidate ranking selects a "
            "method for later confirmation; it is not an unbiased performance estimate."
        ),
    }


def _fmt(value: float) -> str:
    return f"{value:.3f}"


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Phase A S1 signal-batch 路由探索",
        "",
        "状态：行为标签先冻结，随后执行的开发性 route-only 分析；不是独立确认结果。",
        "",
        "## 数据",
        "",
        f"36 条 trace 全部通过路由验证；主比较为 12 条 attack 中的 "
        f"{payload['attack_positive_count']} drift / {payload['attack_negative_count']} resist。",
        "",
        "## 排名前五的预注册 route-only 候选",
        "",
        "| prefix | route feature | layers | classifier | AUROC | AP | mixed-pair win | mixed top |",
        "|---:|---|---|---|---:|---:|---:|---:|",
    ]
    for row in payload["top_route_candidate_keys"]:
        metrics = row["metrics"]
        lines.append(
            f"| {row['prefix']} | {row['feature']} | {row['layer_set']} | "
            f"{row['classifier']} | {_fmt(metrics['auroc'])} | "
            f"{_fmt(metrics['average_precision'])} | "
            f"{_fmt(metrics['mixed_group_mean_pairwise_win_rate'])} | "
            f"{metrics['mixed_group_top_count']}/2 |"
        )
    lines.extend(
        [
            "",
            "## 廉价可见输出控制",
            "",
            "| control | prefix | AUROC | AP | mixed-pair win |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in payload["sanity_controls"]:
        metrics = row["metrics"]
        lines.append(
            f"| {row['control']} | {row['prefix']} | {_fmt(metrics['auroc'])} | "
            f"{_fmt(metrics['average_precision'])} | "
            f"{_fmt(metrics['mixed_group_mean_pairwise_win_rate'])} |"
        )
    lines.extend(
        [
            "",
            "## 整段输出上的局部路由异常窗口",
            "",
            "该分析允许窗口落在实际诗歌/代码已经开始之后，因此衡量的是在线检测，不是行为发生前的预测。",
            "",
            "| width | signal | layers | AUROC | AP | mixed-pair win | mixed top |",
            "|---:|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in payload["full_window_anomalies"]["results"]:
        metrics = row["metrics"]
        lines.append(
            f"| {row['width']} | {row['signal']} | {row['layer_set']} | "
            f"{_fmt(metrics['auroc'])} | {_fmt(metrics['average_precision'])} | "
            f"{_fmt(metrics['mixed_group_mean_pairwise_win_rate'])} | "
            f"{metrics['mixed_group_top_count']}/2 |"
        )
    positive_peak_rows = [
        row
        for row in payload["full_window_anomalies"][
            "all_layer_probability_peak_timings"
        ]["8"]
        if row["primary_positive"]
    ]
    relation_counts = {
        relation: sum(
            row["peak_relation_to_evidence"] == relation for row in positive_peak_rows
        )
        for relation in ("before_evidence", "overlaps_evidence", "after_evidence")
    }
    lines.extend(
        [
            "",
            "对 all-layer probability JSD 的 8-token 最强窗口，5 个正例中的峰值位置为："
            f"证据前 {relation_counts['before_evidence']}、与证据重叠 "
            f"{relation_counts['overlaps_evidence']}、证据后 {relation_counts['after_evidence']}。",
        ]
    )
    lines.extend(
        [
            "",
            "## 初始 attack prefill 的确定性检查",
            "",
            "同一 payload 的三个 seed 在第一次采样前拥有完全相同的输入；因此其 prefill 路由理论上也应相同。",
            "",
            "| group | tokens | same token IDs | max probability delta | same top-k |",
            "|---|---:|---|---:|---|",
        ]
    )
    for group, row in payload["initial_attack_prefill_determinism"].items():
        lines.append(
            f"| {group} | {row['token_count']} | {row['token_ids_equal']} | "
            f"{row['max_router_probability_delta']:.3g} | {row['top_k_ids_equal']} |"
        )
    pairs = payload["prefix_diagnostics"]["opposite_label_identical_prompt_pairs"]
    lines.extend(
        [
            "",
            "## 相同 final prompt、相同输出前缀诊断",
            "",
            "下表只列 outcome 相反且 final prompt 完全相同的 pair。路由在 token 分叉以前应由同一上下文确定。",
            "",
            "| group | aligned tokens | mean route JSD | mean top-8 overlap |",
            "|---|---:|---:|---:|",
        ]
    )
    for row in pairs:
        lines.append(
            f"| {row['analysis_group_id']} | {row['aligned_tokens']} | "
            f"{row['mean_token_layer_jsd']:.3g} | {row['mean_actual_top8_overlap']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## 固定解释边界",
            "",
            "- route candidate 的数字来自 S1 内部选参，不能作为未来样本的泛化性能。",
            "- decode 路由对应已经采样并送回模型的 token；相同 prompt 与 token prefix 下，seed 不会产生隐藏的路由分叉。",
            "- 行为证据完成 token 不等于证据开始 token；完整时间字段保存在 JSON。",
            "- 是否进入 confirmation 必须同时参考协议、长度、token hash、mixed-group 与 boundary 诊断。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=ROOT / "artifacts" / "phase_a" / RUN_NAME,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts" / "phase_a" / "signal_analysis_v1",
    )
    args = parser.parse_args()
    observations = _load_observations(args.run_dir)
    payload = calculate(observations)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "signal_results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report = render_report(payload)
    (output_dir / "report.md").write_text(report + "\n", encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
