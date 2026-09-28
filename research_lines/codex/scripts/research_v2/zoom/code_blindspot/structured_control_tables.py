"""Matched control group for critic E7 -- markdown table bodies for the report."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

REPO = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
OUT = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot/structured_control"


def fmt(x, n=3):
    return "-" if x is None else f"{x:.{n}f}"


def main() -> None:
    a = json.loads((OUT / "analysis.json").read_text())
    rows = a["rows"]
    lines = []

    lines.append("### T1 per-trace table (all 59 drifts, sorted by structure score)\n")
    lines.append("| trace | group | domain | PC | onset | n | structure | nl | br | dg | co | lm | "
                 "medR/q95 | medR/q99 | max16/q95 | rare | rmL11 | rmL12 | rmL13 | rm11-13 | A off | B off |")
    lines.append("|" + "---|" * 22)
    for r in rows:
        pc = r["per_class"]
        A = r["candA_alarm"]["offset_from_product_onset"]
        B = r["candB_alarm"]["offset_from_product_onset"]
        star = "**" if r["domain"] == "programming" else ""
        lines.append(
            f"| {star}{r['short']}{star} | {r['group']} | {r['domain']} | {r['product_class']} | "
            f"{r['product_onset']} | {r['n_tokens'] if 'n_tokens' in r else r['window_hi']-r['product_onset']} | "
            f"{r['structure_score']:.4f} | {pc['newline']:.3f} | {pc['bracket']:.3f} | {pc['digit']:.3f} | "
            f"{pc['colon']:.3f} | {pc['list_marker']:.3f} | "
            f"{fmt(r['candA']['median_over_q95'])} | {fmt(r['candA']['median_over_q99'])} | "
            f"{fmt(r['candA']['max16_over_q95'])} | {r['rare_expert_mass']:.4f} | "
            f"{r['rmass']['L11']:.3f} | {r['rmass']['L12']:.3f} | {r['rmass']['L13']:.3f} | "
            f"{r['rmass']['L11_13_mean']:.3f} | {A if A is not None else 'none'} | "
            f"{B if B is not None else 'none'} |")

    g = a["group_summary"]
    lines.append("\n### T2 group summary (median [min, max])\n")
    lines.append("| group | n | structure | medR/q95 | max16/q95 | rare | rmass L11-13 |")
    lines.append("|" + "---|" * 7)
    for k in ("CODE", "LITCODE", "CODE_PROSE", "STRUCT", "PROSE", "PROSE_STRICT"):
        s = g[k]
        cells = []
        for m in ("structure_score", "candA_med_q95", "candA_max16_q95", "rare", "rmass_L11_13"):
            d = 4 if m == "rare" else 3
            cells.append(f"{s[m]['median']:.{d}f} [{s[m]['min']:.{d}f}, {s[m]['max']:.{d}f}]")
        lines.append(f"| {k} | {s['structure_score']['n']} | " + " | ".join(cells) + " |")

    lines.append("\n### T3 frozen alarm behaviour (mode D, alpha 0.10, persist2)\n")
    lines.append("| group | cand | n | never | pre-onset | in +16 | in +48 | after +48 | median post-onset offset |")
    lines.append("|" + "---|" * 9)
    for k in ("CODE", "LITCODE", "STRUCT", "PROSE", "PROSE_STRICT"):
        mem = set(a["group_members"][k])
        rs = [r for r in rows if r["short"] in mem]
        for cand in ("candA", "candB"):
            off = [r[f"{cand}_alarm"]["offset_from_product_onset"] for r in rs]
            fired = [o for o in off if o is not None]
            post = [o for o in fired if o >= 0]
            lines.append(
                f"| {k} | {'CAND-A' if cand=='candA' else 'CAND-B'} | {len(rs)} | "
                f"{sum(1 for o in off if o is None)} | {sum(1 for o in fired if o < 0)} | "
                f"{sum(1 for o in post if o <= 16)} | {sum(1 for o in post if o <= 48)} | "
                f"{sum(1 for o in post if o > 48)} | {np.median(post):.1f} |" if post else
                f"| {k} | {cand} | {len(rs)} | - | - | - | - | - | - |")

    (OUT / "tables.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
