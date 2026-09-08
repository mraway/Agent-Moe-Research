from __future__ import annotations
import json
ROOT = "/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363"
OUT = f"{ROOT}/artifacts/agent_v2/research_v2/zoom/false_alarms"
d = json.load(open(f"{OUT}/false_alarm_cases.json"))
lines = ["| # | 候选/方向 | trace_id | 臂 | workflow | 域 | channel | 长度(三分位) | 首报警 end (相对位置) | 阈值 | 峰值余量 | 连续越界窗口 / 报警窗口总数 | 首报警窗口文本 | 类 | 说明 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
for i, r in enumerate(d["cases"], 1):
    txt = r["window_text"].replace("|", "\\|").replace("\n", "\\n")
    lines.append(f"| {i} | {r['cand']} {r['case']} | `{r['trace_id']}` | {r['arm']} | {r['workflow']} | {r['domain']} | "
                 f"{r['channel']} | {r['length']} (T{r['tier']}) | {r['first_alarm_end']} ({r['rel_pos']}) | {r['threshold']} | "
                 f"{r['peak_margin']} | {r['excursion']} / {r['n_alarm_windows']} | `{txt}` | ({r['class']}) | {r['note']} |")
open(f"{OUT}/false_alarm_table.md", "w").write("\n".join(lines) + "\n")
print("\n".join(lines[:4]))
print("rows:", len(d["cases"]))
