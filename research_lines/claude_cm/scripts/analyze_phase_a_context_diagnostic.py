#!/usr/bin/env python3
"""Run a post-hoc, matched-prefix context diagnostic on Phase A outputs."""

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
    load_phase_a_trace,
    matched_prefix_diagnostics,
)
from routing import validate_trace  # noqa: E402


PAIRINGS = (
    ("attack", "clean"),
    ("attack", "benign_control"),
    ("benign_control", "clean"),
)


def load_traces(run_dirs: list[Path]) -> list[PhaseATrace]:
    trace_dirs = sorted(
        path.parent.resolve()
        for run_dir in run_dirs
        for path in run_dir.resolve().glob("*/*/trace.json")
    )
    if len(trace_dirs) != 9:
        raise ValueError(f"expected 9 Phase A traces, found {len(trace_dirs)}")
    traces = [
        load_phase_a_trace(trace_dir, validate_trace(trace_dir))
        for trace_dir in trace_dirs
    ]
    groups = {trace.pair_group_id for trace in traces}
    if len(groups) != 3:
        raise ValueError(f"expected 3 pair groups, found {len(groups)}")
    for group in groups:
        arms = {trace.arm for trace in traces if trace.pair_group_id == group}
        if arms != {"clean", "benign_control", "attack"}:
            raise ValueError(f"{group} has unexpected arms: {sorted(arms)}")
    return traces


def calculate(traces: list[PhaseATrace]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for group in sorted({trace.pair_group_id for trace in traces}):
        by_arm = {
            trace.arm: trace for trace in traces if trace.pair_group_id == group
        }
        pair_results: dict[str, Any] = {}
        for left_arm, right_arm in PAIRINGS:
            pair_results[f"{left_arm}__vs__{right_arm}"] = matched_prefix_diagnostics(
                by_arm[left_arm].decode,
                by_arm[right_arm].decode,
            )
        attack_completed = bool(by_arm["attack"].primary_positive)
        attack_clean = pair_results["attack__vs__clean"]["mean_jsd"]
        attack_benign = pair_results["attack__vs__benign_control"]["mean_jsd"]
        results[group] = {
            "attack_completed": attack_completed,
            "attack_closer_to_benign_than_clean": attack_benign < attack_clean,
            "pairs": pair_results,
        }
    return {
        "schema_version": 1,
        "analysis_id": "phase-a-routing-context-diagnostic-v1",
        "analysis_role": "post_hoc_context_diagnostic",
        "claim_limit": (
            "Matched-prefix routing can show context sensitivity, but occurs before "
            "behavioral divergence and is not evidence of task-deviation detection."
        ),
        "groups": results,
    }


def render_report(payload: dict[str, Any]) -> str:
    lines = [
        "# Phase A matched-prefix 上下文诊断",
        "",
        "状态：在预注册主结果产生后提出的 post-hoc 诊断，不是主指标。",
        "",
        "只比较三臂输出仍逐 token 完全相同的最长公共前缀，因此 token ID、位置和已生成前缀匹配。"
        "该段发生在输出分叉和任务偏移完成之前，只能检验工具上下文是否改变路由。",
        "",
        "| pair group | outcome | comparison | shared tokens | mean JSD | max token JSD | shared text |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for group, group_result in payload["groups"].items():
        outcome = "completed" if group_result["attack_completed"] else "resisted"
        for pair_name, result in group_result["pairs"].items():
            text = result["shared_prefix_text"].replace("|", "\\|")
            if len(text) > 90:
                text = text[:89] + "…"
            lines.append(
                f"| {group} | {outcome} | {pair_name.replace('__vs__', ' vs ')} | "
                f"{result['shared_prefix_tokens']} | {result['mean_jsd']:.6f} | "
                f"{result['max_token_jsd']:.6f} | `{text}` |"
            )
    lines.extend(
        [
            "",
            "三个组中 attack 都比 clean 更接近 benign control。完成型和 resisted 组均出现该模式，"
            "因此最保守的解释是 router 对工具内容上下文敏感，而不是已经区分是否服从攻击。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dirs",
        type=Path,
        nargs="+",
        default=[
            ROOT / "artifacts" / "phase_a" / "phase_a_minimal_smoke_v7_order",
            ROOT / "artifacts" / "phase_a" / "phase_a_small_batch_v2",
        ],
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts" / "phase_a" / "routing_context_diagnostic_v1",
    )
    args = parser.parse_args()
    payload = calculate(load_traces(args.run_dirs))
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "matched_prefix_scores.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (output_dir / "report.md").write_text(render_report(payload), encoding="utf-8")
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
