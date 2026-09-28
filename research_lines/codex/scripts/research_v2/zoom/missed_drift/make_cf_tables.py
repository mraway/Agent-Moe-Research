#!/usr/bin/env python3
"""Render counterfactual markdown tables."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT  # noqa: E402

NAMES = {
    "A0_base_5_15_w8": "CAND-A 基线：层 5–15，w=8，top-8 入选率",
    "A1_all16_w8": "层带 = 全 16 层",
    "A2_5_11_w8": "层带 = 5–11",
    "A3_11_15_w8": "层带 = 11–15",
    "A4_5_15_w4": "w=4",
    "A5_prob_5_15_w8": "特征 = router 概率（非 top-8 入选率）",
    "A6_maxlayer_5_15_w8": "分数 = 逐层白化距离的 max-of-layers，w=8",
    "A7_maxlayer_5_15_w4": "分数 = max-of-layers，w=4",
    "B0_base_5_11_w4": "CAND-B 基线：D1 深度链，层 5–11，w=4",
    "B1_all16_w4": "层带 = 全 16 层",
    "B2_5_15_w4": "层带 = 5–15",
    "B3_11_15_w4": "层带 = 11–15",
    "B4_5_11_w8": "w=8",
    "B5_maxlink_5_11_w4": "分数 = 逐跳 surprisal 的 max-of-links，层 5–11",
    "B6_maxlink_5_15_w4": "分数 = max-of-links，层 5–15",
}


def main() -> None:
    cf = json.loads((OUT / "counterfactuals.json").read_text())
    rec = json.loads((OUT / "cf_recovery.json").read_text())
    wu = json.loads((OUT / "cf_warmup.json").read_text())
    cb = json.loads((OUT / "cf_combine.json").read_text())
    out = []
    for fam, prefix, base in (("CAND-A", "A", "A0_base_5_15_w8"), ("CAND-B", "B", "B0_base_5_11_w4")):
        out.append(f"\n**{fam} 反事实网格**（模式 D，α=0.10，persist2，routine=cb，S1 两方向）\n")
        out.append(
            "| 变体 | B1→B2 FAR / R8 / R16 | B2→B1 FAR / R8 / R16 | 找回原漏检 | 新增漏检 | 净 | "
            "新增误报 trace 数（两方向合计） | 每新增 1 条误报找回 |"
        )
        out.append("|---|---|---|---|---|---|---|---|")
        for name in cf:
            if not name.startswith(prefix):
                continue
            c1 = cf[name]["cases"]["b1_to_b2"]
            c2 = cf[name]["cases"]["b2_to_b1"]
            if name == base:
                out.append(
                    f"| {NAMES[name]} | {c1['far_all']:.3f} / {c1['r8']:.3f} / {c1['r16']:.3f} | "
                    f"{c2['far_all']:.3f} / {c2['r8']:.3f} / {c2['r16']:.3f} | – | – | – | – | – |"
                )
                continue
            r = rec[fam][name]
            nrec = sum(r[c]["n_recovered"] for c in ("b1_to_b2", "b2_to_b1"))
            nlost = sum(r[c]["n_lost"] for c in ("b1_to_b2", "b2_to_b1"))
            out.append(
                f"| {NAMES[name]} | {c1['far_all']:.3f} / {c1['r8']:.3f} / {c1['r16']:.3f} | "
                f"{c2['far_all']:.3f} / {c2['r8']:.3f} / {c2['r16']:.3f} | {nrec} | {nlost} | "
                f"{r['net_recovered']:+d} | {r['added_false_alarm_traces']:+.1f} | "
                f"{r['efficiency'] if r['efficiency'] is not None else '—'} |"
            )
    out.append("\n**报警预热（warm-up）：忽略 end < W0 的全部报警**（同一冻结分数流，仅改判决规则）\n")
    out.append("| 候选 | 方向 | W0 | FAR（条/总） | 严格 pre-onset | onset+16 召回（条/35 或 24） |")
    out.append("|---|---|---|---|---|---|")
    for fam in wu:
        for case in wu[fam]:
            for w0, b in wu[fam][case].items():
                out.append(
                    f"| {fam} | {case.replace('_to_', '→')} | {w0} | "
                    f"{b['far_all']:.3f}（{b['fa_traces']}/{b['non_drift']}） | {b['pre_strict']} | "
                    f"{b['r16']:.3f}（{b['r16_count']}/{b['drift']}） |"
                )
    out.append("\n**两候选组合（同一冻结分数流，同一 scenario 半切；OR = 任一统计量越界，AND = 同一 token 上两者同时越界）**\n")
    out.append(
        "| 方向 | 规则 | FAR / clean / benign / resist | R4 | R8 | R16 | 中位延迟 |"
    )
    out.append("|---|---|---|---|---|---|---|")
    for case in cb:
        for rule, b in cb[case].items():
            out.append(
                f"| {case.replace('_to_', '→')} | {rule} | {b['far_all']:.3f} / {b['far_clean']:.3f} / "
                f"{b['far_benign']:.3f} / {b['far_resist']:.3f} | {b['r4']:.3f} | {b['r8']:.3f} | "
                f"{b['r16']:.3f}（{b['r16_count']}/{b['drift']}） | {b['median_latency']} |"
            )
    (OUT / "cf_tables.md").write_text("\n".join(out), encoding="utf-8")
    print("\n".join(out))


if __name__ == "__main__":
    main()
