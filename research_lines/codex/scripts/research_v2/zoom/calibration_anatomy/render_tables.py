"""Render the markdown tables used by docs/research_v2/zoom/calibration_anatomy.md."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT  # noqa

CLASS_CN = {"protocol_json": "协议型 JSON", "degenerate": "退化(换行/重复)",
            "offtopic_disclaimer": "域外声明/拒绝", "ordinary_prose": "普通散文"}
DIR_CN = {"b1_to_b2": "B1→B2", "b2_to_b1": "B2→B1"}


def esc(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", "⏎").strip()


def main():
    top = json.loads((OUT / "step1_top_tail.json").read_text(encoding="utf-8"))
    cls = json.loads((OUT / "step4_classification.json").read_text(encoding="utf-8"))
    lookup = {(r["pool"], r["candidate"], r["direction"], r["cal_half"], r["rank_from_top"]):
              r["primary_class"] for r in cls["rows"]}
    lines = []
    for key, blk in top.items():
        for case, dblk in blk["directions"].items():
            lines.append(f"\n#### {key} {DIR_CN[case]}（{blk['label']}）\n")
            lines.append("| 校准半 | 名次 | 统计量 max | 是否定阈 | trace_id | 臂 | 域 | channel | "
                         "workflow | decode 长度 | argmax 位置 | 形态 | 最大值处文本（±32 token） |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for half, hb in dblk["halves"].items():
                for r in hb["top"][:5]:
                    c = CLASS_CN[lookup[("frozen", key, case, int(half), r["rank_from_top"])]]
                    lines.append(
                        f"| {half}(n={hb['n_calibration']}, h={hb['threshold']:.3f}) | "
                        f"#{r['rank_from_top']} | {r['statistic_max']:.3f} | "
                        f"{'**是**' if r['is_threshold_setter'] else ''} | `{r['trace_id']}` | "
                        f"{r['arm']} | {r['domain']} | {r['channel']} | {r['workflow']} | "
                        f"{r['decode_len']} | {r['argmax_end']} | {c} | {esc(r['snippet'])[:210]} |")
    (OUT / "tables_top5.md").write_text("\n".join(lines), encoding="utf-8")

    b1p = json.loads((OUT / "step3_b1present.json").read_text(encoding="utf-8"))
    lines = []
    for key, blk in b1p.items():
        lines.append(f"\n#### {key} B2→B1 加入 B1 brief=present 池后的尾部\n")
        lines.append("| 校准半 | 名次 | 统计量 max | 是否定阈 | 来源池 | trace_id | 臂 | "
                     "decode 长度 | 形态 | 文本片段 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for half, hb in blk["halves"].items():
            for r in hb["top"][:5]:
                c = CLASS_CN[lookup[("b1_present", key, "b2_to_b1", int(half), r["rank_from_top"])]]
                lines.append(
                    f"| {half}(n={hb['n_calibration']}, h={hb['threshold']:.3f}) | "
                    f"#{r['rank_from_top']} | {r['statistic_max']:.3f} | "
                    f"{'**是**' if r['is_threshold_setter'] else ''} | {r['source']} | "
                    f"`{r['trace_id']}` | {r['arm']} | {r['decode_len']} | {c} | "
                    f"{esc(r['snippet'])[:170]} |")
    (OUT / "tables_b1present.md").write_text("\n".join(lines), encoding="utf-8")

    marg = json.loads((OUT / "step4_drift_margins.json").read_text(encoding="utf-8"))
    lines = ["| 候选 | 方向 | 校准半 | trace_id | 域/workflow | decode 长度 | onset | "
             "阈值 h | onset 后最大统计量 | 余量 | 结果 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    sel = [c for c in marg if (not c["hit"] and not c["pre_alarm"]) or (c["hit"] and c["margin"] < 1.0)]
    for c in sorted(sel, key=lambda c: c["margin"]):
        res = "命中" if c["hit"] else ("边界前误报" if c["pre_alarm"] else "漏检")
        lines.append(f"| {c['candidate']} | {DIR_CN[c['direction']]} | {c['cal_half']} | "
                     f"`{c['trace_id']}` | {c['domain']}/{c['workflow']} | {c['decode_len']} | "
                     f"{c['onset']} | {c['threshold']:.3f} | {c['post_onset_max']:.3f} | "
                     f"{c['margin']:+.3f} | {res}"
                     + (f"(延迟{c['latency']})" if c["hit"] else "") + " |")
    (OUT / "tables_margins.md").write_text("\n".join(lines), encoding="utf-8")
    print("rendered", OUT)


if __name__ == "__main__":
    main()
