#!/usr/bin/env python3
"""Render the markdown tables used by the zoom report."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT  # noqa: E402

SHORT = {
    "direct_user": "direct",
    "multi_turn_user": "multi",
    "tool_output": "tool",
}


def zline(series, onset, step=4):
    return " ".join(f"{e-onset:+d}:{v:.1f}" for e, v in series if (e - onset) % step == 0)


def main() -> None:
    cl = json.loads((OUT / "classified.json").read_text())
    lay = json.loads((OUT / "layer_anatomy.json").read_text())
    lines: list[str] = []
    detail: list[str] = []
    for fam in ("CAND-A", "CAND-B"):
        for case in ("b1_to_b2", "b2_to_b1"):
            rows = cl[fam][case]
            lines.append(f"\n**{fam} / {case.replace('_to_', '→').upper()}（漏检 {len(rows)} 条）**\n")
            lines.append(
                "| trace_id | 域 | 通道 | workflow | len | onset | 类型 | 机制 | 阈值 | "
                "onset..+16 最大统计量 | 余量 | 首个报警 | z(onset−8→+32, 每 4 token) |"
            )
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for r in rows:
                cls = {
                    "no_alarm": "无报警",
                    "late_alarm": "报警过晚",
                    "pre_onset_disqualified": "被前置报警取消",
                }[r["class"]]
                lines.append(
                    f"| `{r['trace_id']}` | {r['domain']} | {SHORT.get(r['channel'], r['channel'])} | "
                    f"{r['workflow']} | {r['decode_len']} | {r['onset']} | {cls} | M{r['mechanism']} | "
                    f"{r['threshold']:.2f} | {r['max_stat_in16']:.2f} | {r['margin_in16']:+.2f} | "
                    f"{r['first_alarm_end']} | {zline(r['z_series'], r['onset'])} |"
                )
            detail.append(f"\n### {fam} / {case.replace('_to_', '→').upper()}\n")
            for r in rows:
                L = lay[fam][case].get(r["trace_id"], {})
                detail.append(f"**`{r['trace_id']}`** — M{r['mechanism']}：{r['why']}")
                detail.append(
                    f"- {r['domain']} / {r['channel']} / {r['workflow']}；decode {r['decode_len']}；"
                    f"onset {r['onset']}；completion boundary {r['completion_boundary']}"
                )
                text = r["text_onset_m16_p32"].replace("\n", "\\n").replace("|", "\\|")
                detail.append(f"- onset−16→+32 文本：`{text}`")
                detail.append(f"- z(onset−8→+32)：{zline(r['z_series'], r['onset'], 2)}")
                if fam == "CAND-A":
                    if "layer_ratio_at_max_in16" in L:
                        detail.append(
                            "- 层 5–15 白化距离贡献 / routine 均值（窗口 "
                            f"{L['window_at_max_in16']}）：{L['layer_ratio_at_max_in16']}"
                        )
                    for n in L.get("nearest_routine", [])[:3]:
                        detail.append(
                            f"- onset+8 最近 routine 窗口 d={n['distance']}（自身范数 {L.get('own_norm')}）："
                            f"{n['arm']}/{n['domain']}/{n['workflow']} end={n['end']} "
                            f"`{n['text'].replace(chr(10), chr(92) + 'n')}`"
                        )
                else:
                    if "link_delta_at_max_in16" in L:
                        detail.append(
                            "- 深度链每一跳 surprisal 超出 routine 均值（nats，init,5→6,…,10→11；窗口 "
                            f"{L['window_at_max_in16']}）：{L['link_delta_at_max_in16']}"
                        )
                detail.append("")
    (OUT / "case_tables.md").write_text("\n".join(lines), encoding="utf-8")
    (OUT / "case_detail.md").write_text("\n".join(detail), encoding="utf-8")
    print("written", len(lines), len(detail))


if __name__ == "__main__":
    main()
