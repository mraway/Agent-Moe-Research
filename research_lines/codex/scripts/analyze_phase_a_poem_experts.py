#!/usr/bin/env python3
"""Zoom in on expert routing for poem versus customer-service spans."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase_a.routing_analysis import (  # noqa: E402
    PhaseATrace,
    RoutingSequence,
    compare_aligned_token_routes,
    compare_routing_profiles,
    concatenate_routing_sequences,
    find_unique_text_token_indices,
    load_phase_a_trace,
    subset_routing_sequence,
)
from routing import validate_trace  # noqa: E402


POEM_TEXT = (
    "Moonlight crosses the quiet sea. Silver stars wake one by one. "
    "Night winds carry dreams ashore. Dawn waits beyond the horizon."
)
ATTACK_SERVICE_PREFIX = (
    "The current status of order ORD-204 is delayed at the regional hub."
)
ATTACK_SERVICE_ESTIMATE = "Estimated delivery: 2026-09-05."
ATTACK_SERVICE_ORDER_ID = "Order ID: ORD-204"
CONTROL_SHARED_SERVICE = (
    "The current status of order ORD-204 is as follows: Estimated delivery date is "
    "2026-09-05. The order is currently delayed at the regional hub."
)


def _message_text(trace: PhaseATrace) -> str:
    matches = [
        event["parsed"]["message"]
        for event in trace.trace["events"]
        if event["kind"] == "model_generation"
        and event.get("agent_step") == 1
        and event.get("parsed", {}).get("kind") == "message"
    ]
    if len(matches) != 1:
        raise ValueError(f"{trace.trace_id} has {len(matches)} step-1 messages")
    return str(matches[0])


def _text_subset(sequence: RoutingSequence, text: str) -> tuple[RoutingSequence, dict[str, Any]]:
    indices = find_unique_text_token_indices(sequence, text)
    subset = subset_routing_sequence(sequence, indices)
    return subset, {
        "target_text": text,
        "token_count": len(indices),
        "first_token_index": indices[0],
        "last_token_index_inclusive": indices[-1],
        "overlapping_token_text": "".join(subset.token_texts),
        "token_ids": list(subset.token_ids),
    }


def load_group(group_dir: Path) -> dict[str, PhaseATrace]:
    traces = {
        arm: load_phase_a_trace(group_dir / arm, validate_trace(group_dir / arm))
        for arm in ("clean", "benign_control", "attack")
    }
    expected_group = "phase-a-order-status-204"
    if {trace.pair_group_id for trace in traces.values()} != {expected_group}:
        raise ValueError("input directory is not the frozen poem pair group")
    if not traces["attack"].primary_positive:
        raise ValueError("poem attack trace is no longer a completed positive")
    if traces["clean"].primary_positive or traces["benign_control"].primary_positive:
        raise ValueError("poem control label changed")
    return traces


def calculate(traces: dict[str, PhaseATrace]) -> dict[str, Any]:
    attack = traces["attack"]
    poem, poem_span = _text_subset(attack.decode, POEM_TEXT)
    service_prefix, service_prefix_span = _text_subset(
        attack.decode, ATTACK_SERVICE_PREFIX
    )
    service_estimate, service_estimate_span = _text_subset(
        attack.decode, ATTACK_SERVICE_ESTIMATE
    )
    service_order_id, service_order_id_span = _text_subset(
        attack.decode, ATTACK_SERVICE_ORDER_ID
    )
    attack_service = concatenate_routing_sequences(
        (service_prefix, service_estimate, service_order_id)
    )

    clean_service, clean_span = _text_subset(
        traces["clean"].decode, _message_text(traces["clean"])
    )
    benign_service, benign_span = _text_subset(
        traces["benign_control"].decode, _message_text(traces["benign_control"])
    )
    control_service = concatenate_routing_sequences((clean_service, benign_service))

    clean_shared, clean_shared_span = _text_subset(
        traces["clean"].decode, CONTROL_SHARED_SERVICE
    )
    benign_shared, benign_shared_span = _text_subset(
        traces["benign_control"].decode, CONTROL_SHARED_SERVICE
    )

    attack_prefill_poem, attack_prefill_span = _text_subset(
        traces["attack"].tool_prefill, POEM_TEXT
    )
    benign_prefill_poem, benign_prefill_span = _text_subset(
        traces["benign_control"].tool_prefill, POEM_TEXT
    )

    comparisons = {
        "poem_vs_same_trace_service": compare_routing_profiles(poem, attack_service),
        "poem_vs_control_service": compare_routing_profiles(poem, control_service),
        "same_trace_service_vs_control_service": compare_routing_profiles(
            attack_service, control_service
        ),
        "clean_vs_benign_identical_service_text": compare_aligned_token_routes(
            clean_shared, benign_shared
        ),
    }
    same_poem = {
        "decode_vs_attack_prefill": compare_aligned_token_routes(
            poem, attack_prefill_poem
        ),
        "decode_vs_benign_prefill": compare_aligned_token_routes(
            poem, benign_prefill_poem
        ),
        "attack_vs_benign_prefill": compare_aligned_token_routes(
            attack_prefill_poem, benign_prefill_poem
        ),
    }
    return {
        "schema_version": 1,
        "analysis_id": "phase-a-poem-expert-zoom-v1",
        "analysis_role": "post_hoc_domain_and_context_diagnostic",
        "claim_limit": (
            "Poem and service spans use different tokens. Aggregate expert changes show "
            "domain/content routing and are not evidence that routing encodes authorization."
        ),
        "trace_ids": {arm: trace.trace_id for arm, trace in traces.items()},
        "spans": {
            "poem_decode": poem_span,
            "attack_service_prefix": service_prefix_span,
            "attack_service_estimate": service_estimate_span,
            "attack_service_order_id": service_order_id_span,
            "clean_service": clean_span,
            "benign_service": benign_span,
            "clean_shared_service": clean_shared_span,
            "benign_shared_service": benign_shared_span,
            "attack_prefill_poem": attack_prefill_span,
            "benign_prefill_poem": benign_prefill_span,
        },
        "comparisons": comparisons,
        "same_poem_token_controls": same_poem,
    }


def render_report(payload: dict[str, Any]) -> str:
    comparison = payload["comparisons"]["poem_vs_same_trace_service"]
    external = payload["comparisons"]["poem_vs_control_service"]
    service_control = payload["comparisons"]["same_trace_service_vs_control_service"]
    same_poem = payload["same_poem_token_controls"]["decode_vs_attack_prefill"]
    prefill_control = payload["same_poem_token_controls"]["attack_vs_benign_prefill"]
    lines = [
        "# Phase A 诗歌专家选择 zoom-in",
        "",
        "状态：预注册主分析之后的 post-hoc 机制诊断。",
        "",
        "## 汇总",
        "",
        "| comparison | mean layer JSD | max layer | max JSD | top-8 overlap |",
        "|---|---:|---:|---:|---:|",
        (
            "| poem vs same-trace service | "
            f"{comparison['mean_layer_centroid_jsd']:.6f} | "
            f"{comparison['max_jsd_layer']} | {comparison['max_layer_centroid_jsd']:.6f} | "
            f"{comparison['mean_frequent_top8_overlap']:.2f}/8 |"
        ),
        (
            "| poem vs clean+benign service | "
            f"{external['mean_layer_centroid_jsd']:.6f} | "
            f"{external['max_jsd_layer']} | {external['max_layer_centroid_jsd']:.6f} | "
            f"{external['mean_frequent_top8_overlap']:.2f}/8 |"
        ),
        (
            "| attack service vs clean+benign service | "
            f"{service_control['mean_layer_centroid_jsd']:.6f} | "
            f"{service_control['max_jsd_layer']} | "
            f"{service_control['max_layer_centroid_jsd']:.6f} | "
            f"{service_control['mean_frequent_top8_overlap']:.2f}/8 |"
        ),
        "",
        "这里的 top-8 是每层按实际入选频率最高的 8 个专家集合。",
        "",
        "## 逐层：诗歌与同 trace 客服片段",
        "",
        "| layer | centroid JSD | frequent top-8 overlap | same poem decode/prefill JSD | "
        "same poem actual top-8 overlap |",
        "|---:|---:|---:|---:|---:|",
    ]
    for layer in range(16):
        lines.append(
            f"| {layer} | {comparison['per_layer_centroid_jsd'][layer]:.6f} | "
            f"{comparison['per_layer_frequent_top8_overlap'][layer]}/8 | "
            f"{same_poem['per_layer_mean_jsd'][layer]:.6f} | "
            f"{same_poem['per_layer_actual_top8_overlap'][layer]:.2f}/8 |"
        )

    lines.extend(
        [
            "",
            "## 最大的专家选择率变化",
            "",
            "以下是 poem 减 same-trace service 的绝对变化最大项；选择率表示该专家在多少比例 token 的 "
            "top-8 中出现。",
            "",
            "| layer | expert | poem | service | delta |",
            "|---:|---:|---:|---:|---:|",
        ]
    )
    for row in comparison["largest_selection_rate_changes"][:12]:
        lines.append(
            f"| {row['layer']} | {row['expert']} | {row['left']:.1%} | "
            f"{row['right']:.1%} | {row['delta']:+.1%} |"
        )

    lines.extend(
        [
            "",
            "## 相同诗歌 token 对照",
            "",
            f"Decode 与 attack prefill 有 {same_poem['aligned_tokens']} 个完全相同 token 可对齐；"
            f"平均 JSD={same_poem['mean_token_layer_jsd']:.6f}，实际 top-8 平均重合 "
            f"{same_poem['mean_actual_top8_overlap']:.2f}/8。",
            "",
            f"同一首诗在 attack 与 benign prefill 之间平均 JSD="
            f"{prefill_control['mean_token_layer_jsd']:.6f}，实际 top-8 平均重合 "
            f"{prefill_control['mean_actual_top8_overlap']:.2f}/8。",
            "",
            "前几层的同诗 decode/prefill 路由接近，差异随深度增大。最保守解释是：专家选择对"
            "诗歌词汇/领域非常敏感；深层还受到生成阶段、位置和上下文影响。当前样本不能把这些因素"
            "进一步拆成“任务意图”或“授权状态”。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--group-dir",
        type=Path,
        default=(
            ROOT
            / "artifacts"
            / "phase_a"
            / "phase_a_minimal_smoke_v7_order"
            / "phase-a-order-status-204"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts" / "phase_a" / "poem_expert_zoom_v1",
    )
    args = parser.parse_args()
    payload = calculate(load_group(args.group_dir.resolve()))
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "poem_expert_scores.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "report.md").write_text(render_report(payload), encoding="utf-8")
    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "poem_vs_same_trace_service": payload["comparisons"][
                    "poem_vs_same_trace_service"
                ],
                "same_poem_decode_vs_attack_prefill": payload[
                    "same_poem_token_controls"
                ]["decode_vs_attack_prefill"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
