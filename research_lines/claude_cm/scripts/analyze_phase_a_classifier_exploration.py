#!/usr/bin/env python3
"""Explore simple classifiers over the frozen Phase A routing traces."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase_a.classifier import (  # noqa: E402
    balanced_accuracy_threshold,
    classification_metrics,
    extract_features,
    fit_normal_centroid_classifier,
    fit_ridge_classifier,
    leave_one_group_out_scores,
    prefix_sequence,
)
from phase_a.routing_analysis import (  # noqa: E402
    RoutingSequence,
    load_final_generation_sequence,
)
from routing import validate_trace  # noqa: E402


PRIMARY_RUN_NAMES = (
    "phase_a_cross_domain_pilot_v1",
    "phase_a_outcome_contrast_pilot_v1",
)
STRESS_RUN_NAME = "phase_a_scope_gate_pilot_v1"
PREFIXES: tuple[tuple[str, int | None], ...] = (
    ("first_8", 8),
    ("first_16", 16),
    ("first_32", 32),
    ("full", None),
)
FEATURE_CLASSIFIERS: dict[str, tuple[str, ...]] = {
    "route_probability": ("normal_centroid_distance", "ridge"),
    "route_selection": ("normal_centroid_distance", "ridge"),
    "token_hash": ("ridge",),
    "length": ("ridge",),
}


@dataclass(frozen=True)
class Observation:
    trace_dir: Path
    trace_id: str
    pair_group_id: str
    arm: str
    primary_positive: bool
    stratum: str
    run_name: str
    decoding_strategy: str
    system_prompt_hash: str
    final_agent_step: int
    final_parse_kind: str
    sequence: RoutingSequence
    validation: dict[str, Any]


def _load_observations(run_dirs: Sequence[Path]) -> list[Observation]:
    observations: list[Observation] = []
    for run_dir in run_dirs:
        run_dir = run_dir.resolve()
        for trace_path in sorted(run_dir.glob("*/*/trace.json")):
            trace_dir = trace_path.parent
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            generations = [
                event
                for event in trace["events"]
                if event["kind"] == "model_generation"
            ]
            if not generations:
                raise ValueError(f"{trace_dir} has no model generations")
            validation = validate_trace(trace_dir)
            if not validation["passed"]:
                raise ValueError(f"routing validation failed for {trace_dir}")
            sequence = load_final_generation_sequence(trace_dir)
            final_event = generations[-1]
            if len(sequence.token_ids) != len(final_event["output_token_ids"]):
                raise ValueError(f"final decode is not output-aligned for {trace_dir}")
            observations.append(
                Observation(
                    trace_dir=trace_dir,
                    trace_id=str(trace["trace_id"]),
                    pair_group_id=str(trace["pair_group_id"]),
                    arm=str(trace["perturbation"]["arm"]),
                    primary_positive=bool(trace["outcome"]["primary_positive"]),
                    stratum=str(trace["outcome"]["stratum"]),
                    run_name=run_dir.name,
                    decoding_strategy=str(trace["decoding"]["strategy"]),
                    system_prompt_hash=str(trace["system_prompt_hash"]),
                    final_agent_step=int(final_event["agent_step"]),
                    final_parse_kind=str(final_event["parsed"]["kind"]),
                    sequence=sequence,
                    validation=validation,
                )
            )
    return observations


def _assert_frozen_design(
    primary: Sequence[Observation], stress: Sequence[Observation]
) -> None:
    if len(primary) != 24 or sum(row.primary_positive for row in primary) != 8:
        raise ValueError("primary set must contain 24 traces and eight positives")
    if len(stress) != 12 or any(row.primary_positive for row in stress):
        raise ValueError("stress set must contain twelve negatives")
    primary_hashes = {row.system_prompt_hash for row in primary}
    stress_hashes = {row.system_prompt_hash for row in stress}
    if len(primary_hashes) != 1 or len(stress_hashes) != 1:
        raise ValueError("each analysis set must have exactly one system prompt")
    if primary_hashes == stress_hashes:
        raise ValueError("stress set unexpectedly shares the primary system prompt")
    groups: dict[str, list[Observation]] = {}
    for row in primary:
        groups.setdefault(row.pair_group_id, []).append(row)
    if len(groups) != 8:
        raise ValueError("primary set must contain eight pair groups")
    for group, rows in groups.items():
        if {row.arm for row in rows} != {"clean", "benign_control", "attack"}:
            raise ValueError(f"primary group {group} does not have all three arms")
        if sum(row.primary_positive for row in rows) != 1:
            raise ValueError(f"primary group {group} does not have one positive")
    if min(len(row.sequence.token_ids) for row in primary) < 32:
        raise ValueError("a primary trace is shorter than the frozen 32-token prefix")


def _feature_matrix(
    observations: Sequence[Observation], family: str, token_limit: int | None
) -> torch.Tensor:
    return torch.stack(
        [
            extract_features(prefix_sequence(row.sequence, token_limit), family)
            for row in observations
        ]
    )


def _fit_model(
    classifier: str, features: torch.Tensor, labels: torch.Tensor
) -> Any:
    if classifier == "ridge":
        return fit_ridge_classifier(features, labels.float() * 2.0 - 1.0)
    if classifier == "normal_centroid_distance":
        return fit_normal_centroid_classifier(features, labels)
    raise ValueError(f"unknown classifier: {classifier}")


def _trace_scores(
    observations: Sequence[Observation], scores: torch.Tensor
) -> list[dict[str, Any]]:
    return [
        {
            "trace_id": row.trace_id,
            "pair_group_id": row.pair_group_id,
            "arm": row.arm,
            "primary_positive": row.primary_positive,
            "stratum": row.stratum,
            "run": row.run_name,
            "decoding_strategy": row.decoding_strategy,
            "final_agent_step": row.final_agent_step,
            "final_parse_kind": row.final_parse_kind,
            "decode_token_count": len(row.sequence.token_ids),
            "score": float(score.item()),
        }
        for row, score in zip(observations, scores, strict=True)
    ]


def _stress_summary(
    observations: Sequence[Observation],
    scores: torch.Tensor,
    threshold: float,
    token_limit: int | None,
) -> dict[str, Any]:
    predicted = scores >= threshold
    by_stratum: dict[str, dict[str, Any]] = {}
    for stratum in sorted({row.stratum for row in observations}):
        mask = torch.tensor([row.stratum == stratum for row in observations])
        by_stratum[stratum] = {
            "trace_count": int(mask.sum().item()),
            "false_positive_count": int(predicted[mask].sum().item()),
            "mean_score": float(scores[mask].mean().item()),
            "max_score": float(scores[mask].max().item()),
        }
    capped = [
        row.trace_id
        for row in observations
        if token_limit is not None and len(row.sequence.token_ids) < token_limit
    ]
    return {
        "trace_count": len(observations),
        "false_positive_count": int(predicted.sum().item()),
        "false_positive_rate": float(predicted.float().mean().item()),
        "threshold": threshold,
        "capped_trace_ids": capped,
        "by_stratum": by_stratum,
        "trace_scores": _trace_scores(observations, scores),
    }


def _token_prefix_equivalence(
    observations: Sequence[Observation], token_limit: int | None
) -> dict[str, Any]:
    """Describe exact token-prefix clusters without using routing values."""

    clusters: dict[tuple[int, ...], list[Observation]] = {}
    for row in observations:
        token_ids = row.sequence.token_ids
        key = token_ids if token_limit is None else token_ids[:token_limit]
        clusters.setdefault(key, []).append(row)
    ordered = sorted(clusters.values(), key=len, reverse=True)
    mixed = [
        rows
        for rows in ordered
        if any(row.primary_positive for row in rows)
        and any(not row.primary_positive for row in rows)
    ]
    largest = ordered[0]
    return {
        "unique_prefix_count": len(clusters),
        "mixed_label_cluster_count": len(mixed),
        "largest_cluster_trace_count": len(largest),
        "largest_cluster_positive_count": sum(
            row.primary_positive for row in largest
        ),
        "largest_mixed_cluster_trace_count": max(
            (len(rows) for rows in mixed), default=0
        ),
    }


def calculate(
    primary: Sequence[Observation], stress: Sequence[Observation]
) -> dict[str, Any]:
    _assert_frozen_design(primary, stress)
    labels = torch.tensor([row.primary_positive for row in primary])
    groups = [row.pair_group_id for row in primary]
    group_rows = {
        group: [row for row in primary if row.pair_group_id == group]
        for group in dict.fromkeys(groups)
    }
    protocol_valid_groups = {
        group
        for group, rows in group_rows.items()
        if all(row.final_parse_kind == "message" for row in rows)
    }
    protocol_valid_indices = [
        index
        for index, row in enumerate(primary)
        if row.pair_group_id in protocol_valid_groups
    ]
    results: list[dict[str, Any]] = []

    for prefix_name, token_limit in PREFIXES:
        for family, classifiers in FEATURE_CLASSIFIERS.items():
            primary_features = _feature_matrix(primary, family, token_limit)
            stress_features = _feature_matrix(stress, family, token_limit)
            for classifier in classifiers:
                oof_scores = leave_one_group_out_scores(
                    primary_features, labels, groups, classifier=classifier
                )
                threshold_result = balanced_accuracy_threshold(oof_scores, labels)
                fitted = _fit_model(classifier, primary_features, labels)
                stress_scores = fitted.score(stress_features)

                valid_index = torch.tensor(protocol_valid_indices, dtype=torch.long)
                valid_features = primary_features.index_select(0, valid_index)
                valid_labels = labels.index_select(0, valid_index)
                valid_groups = [groups[position] for position in protocol_valid_indices]
                valid_scores = leave_one_group_out_scores(
                    valid_features,
                    valid_labels,
                    valid_groups,
                    classifier=classifier,
                )

                strategy_results: dict[str, Any] = {}
                for strategy in sorted({row.decoding_strategy for row in primary}):
                    indices = [
                        index
                        for index, row in enumerate(primary)
                        if row.decoding_strategy == strategy
                    ]
                    index = torch.tensor(indices, dtype=torch.long)
                    strategy_labels = labels.index_select(0, index)
                    strategy_groups = [groups[position] for position in indices]
                    strategy_scores = leave_one_group_out_scores(
                        primary_features.index_select(0, index),
                        strategy_labels,
                        strategy_groups,
                        classifier=classifier,
                    )
                    strategy_results[strategy] = {
                        "metrics": classification_metrics(
                            strategy_scores, strategy_labels, strategy_groups
                        ),
                        "trace_scores": _trace_scores(
                            [primary[position] for position in indices], strategy_scores
                        ),
                    }

                results.append(
                    {
                        "prefix": prefix_name,
                        "requested_token_limit": token_limit,
                        "feature": family,
                        "classifier": classifier,
                        "feature_dimension": primary_features.shape[1],
                        "primary_metrics": classification_metrics(
                            oof_scores, labels, groups
                        ),
                        "oof_threshold": threshold_result,
                        "primary_trace_scores": _trace_scores(primary, oof_scores),
                        "protocol_valid_post_hoc": {
                            "status": "post_hoc_confound_diagnostic",
                            "excluded_group_count": len(set(groups))
                            - len(protocol_valid_groups),
                            "metrics": classification_metrics(
                                valid_scores, valid_labels, valid_groups
                            ),
                            "trace_scores": _trace_scores(
                                [primary[position] for position in protocol_valid_indices],
                                valid_scores,
                            ),
                        },
                        "decoder_sensitivity": strategy_results,
                        "scope_gate_stress": _stress_summary(
                            stress,
                            stress_scores,
                            float(threshold_result["threshold"]),
                            token_limit,
                        ),
                    }
                )

    return {
        "schema_version": 1,
        "analysis_id": "phase-a-classifier-exploration-v1",
        "analysis_role": "small_sample_algorithm_exploration",
        "plan": "docs/phase_a_classifier_exploration_plan.md",
        "primary_runs": list(PRIMARY_RUN_NAMES),
        "stress_run": STRESS_RUN_NAME,
        "primary_trace_count": len(primary),
        "primary_positive_count": int(labels.sum().item()),
        "primary_group_count": len(set(groups)),
        "protocol_valid_group_count": len(protocol_valid_groups),
        "stress_trace_count": len(stress),
        "routing_validation_pass_count": sum(
            row.validation["passed"] for row in (*primary, *stress)
        ),
        "primary_system_prompt_hash": primary[0].system_prompt_hash,
        "stress_system_prompt_hash": stress[0].system_prompt_hash,
        "claim_limit": (
            "All same-prompt attacks in the primary set are positives, so attack "
            "exposure and drift remain confounded; the changed-prompt stress set "
            "diagnoses false-positive fragility but is not a generalization estimate."
        ),
        "token_prefix_equivalence": {
            prefix_name: _token_prefix_equivalence(primary, token_limit)
            for prefix_name, token_limit in PREFIXES
        },
        "results": results,
    }


def _metric_cell(value: float) -> str:
    return f"{value:.3f}"


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Phase A 路由分类算法探索",
        "",
        "状态：基于冻结的既有小样本进行 exploratory analysis，不是泛化性能结论。",
        "",
        "## 主结果：同一旧版 system prompt",
        "",
        "24 条轨迹、8 个匹配任务组、8 个行为偏移正例。所有分数均来自留一任务组交叉验证。",
        "",
        "| prefix | feature | classifier | AUROC | AP | group top-1 | mean margin |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for row in payload["results"]:
        metrics = row["primary_metrics"]
        lines.append(
            f"| {row['prefix']} | {row['feature']} | {row['classifier']} | "
            f"{_metric_cell(metrics['auroc'])} | "
            f"{_metric_cell(metrics['average_precision'])} | "
            f"{metrics['group_top1_count']}/{metrics['group_count']} | "
            f"{metrics['mean_group_margin']:.4f} |"
        )

    lines.extend(
        [
            "",
            "## 完全相同 token 前缀诊断",
            "",
            "这张表不使用路由值。first-8 中大量正负样本生成了完全相同的 token，"
            "因此早期路由若可分，来源只能是此前上下文状态，而不是这 8 个可见 token 本身。",
            "",
            "| prefix | unique token sequences | mixed-label clusters | largest cluster | positives in largest |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for prefix_name, result in payload["token_prefix_equivalence"].items():
        lines.append(
            f"| {prefix_name} | {result['unique_prefix_count']} | "
            f"{result['mixed_label_cluster_count']} | "
            f"{result['largest_cluster_trace_count']} | "
            f"{result['largest_cluster_positive_count']} |"
        )

    lines.extend(
        [
            "",
            "## 仅保留协议有效任务组（post-hoc）",
            "",
            "排除正例最终输出无法解析为协议 JSON 的 3 个任务组，剩余 5 个完整三臂组。"
            "这是协议格式混淆诊断，不属于冻结主分析。",
            "",
            "| prefix | feature | classifier | AUROC | AP | group top-1 |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    for row in payload["results"]:
        metrics = row["protocol_valid_post_hoc"]["metrics"]
        lines.append(
            f"| {row['prefix']} | {row['feature']} | {row['classifier']} | "
            f"{_metric_cell(metrics['auroc'])} | "
            f"{_metric_cell(metrics['average_precision'])} | "
            f"{metrics['group_top1_count']}/{metrics['group_count']} |"
        )

    lines.extend(
        [
            "",
            "## 解码策略敏感性（full generation）",
            "",
            "每个子集只有 4 个任务组；这些数字只用于检查信号是否完全由 greedy/sample 差异造成。",
            "",
            "| feature | classifier | strategy | AUROC | AP | group top-1 |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    for row in payload["results"]:
        if row["prefix"] != "full":
            continue
        for strategy, result in row["decoder_sensitivity"].items():
            metrics = result["metrics"]
            lines.append(
                f"| {row['feature']} | {row['classifier']} | {strategy} | "
                f"{_metric_cell(metrics['auroc'])} | "
                f"{_metric_cell(metrics['average_precision'])} | "
                f"{metrics['group_top1_count']}/{metrics['group_count']} |"
            )

    lines.extend(
        [
            "",
            "## Scope-gate prompt 误报压力测试",
            "",
            "分类器在全部 24 条旧 prompt 样本上重训；阈值只由旧 prompt 的 OOF 分数确定。"
            "下表是 12 条全负样本的误报数。system prompt 已改变，因此这是 prompt-shift 压力测试。",
            "first-32 条件中有 2 条不足 32 token，按冻结协议使用其全部可用 token；完整 ID 保存在 JSON。",
            "",
            "| prefix | feature | classifier | all FP | resisted FP | clean FP | benign FP |",
            "|---|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in payload["results"]:
        stress = row["scope_gate_stress"]
        strata = stress["by_stratum"]
        lines.append(
            f"| {row['prefix']} | {row['feature']} | {row['classifier']} | "
            f"{stress['false_positive_count']}/12 | "
            f"{strata.get('attacked_resisted', {}).get('false_positive_count', 0)}/4 | "
            f"{strata.get('clean_success', {}).get('false_positive_count', 0)}/4 | "
            f"{strata.get('benign_content_control', {}).get('false_positive_count', 0)}/4 |"
        )

    lines.extend(
        [
            "",
            "## 固定限制",
            "",
            "- 同一 prompt 的 8 个 attack 全部发生偏移，没有 resisted hard negative；当前结果仍可能识别攻击/输出内容，而非服从行为本身。",
            "- 诗歌、代码和客服输出的 token 明显不同；token 基线用于判断路由是否提供了文本之外的增量证据。",
            "- 独立任务组只有 8 个，任何接近满分的数字都可能很不稳定。",
            "- decode 第 t 个 token 的路由要在该 token 已生成后才能使用，prefix 指标不是生成前预言。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifacts-root",
        type=Path,
        default=ROOT / "artifacts" / "phase_a",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts" / "phase_a" / "classifier_exploration_v1",
    )
    args = parser.parse_args()
    primary = _load_observations(
        [args.artifacts_root / name for name in PRIMARY_RUN_NAMES]
    )
    stress = _load_observations([args.artifacts_root / STRESS_RUN_NAME])
    payload = calculate(primary, stress)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "classifier_results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report = render_report(payload)
    (output_dir / "report.md").write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
