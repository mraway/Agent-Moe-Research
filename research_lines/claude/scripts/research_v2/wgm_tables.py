#!/usr/bin/env python3
"""Assemble the WGM report tables from the harness run directories.

Emits markdown to stdout; the report copies the tables it needs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "artifacts" / "agent_v2" / "research_v2" / "wgm"

CANDIDATES: list[tuple[str, str, dict[str, Any]]] = [
    ("C1", "c1_c7_c8_c9_g1_middle", {"window_width": 8, "routine_definition": "cb"}),
    ("C2", "c2_g1_middle_late", {"window_width": 8, "routine_definition": "cb"}),
    ("C3", "c3_g2_middle", {"window_width": 8, "routine_definition": "cb"}),
    ("C4", "c4_g2_middle_late", {"window_width": 8, "routine_definition": "cb"}),
    ("C5", "c5_g3_middle", {"window_width": 8, "routine_definition": "cb"}),
    ("C6", "c6_g3_middle_late", {"window_width": 8, "routine_definition": "cb"}),
    ("C7", "c1_c7_c8_c9_g1_middle", {"window_width": 4, "routine_definition": "cb"}),
    ("C8", "c1_c7_c8_c9_g1_middle", {"window_width": 16, "routine_definition": "cb"}),
    ("C9", "c1_c7_c8_c9_g1_middle", {"window_width": 8, "routine_definition": "all_normal"}),
    ("C10", "c10_g1_middle_workflow", {"window_width": 8, "routine_definition": "cb"}),
    ("C11", "c11_g2_middle_r32", {"window_width": 8, "routine_definition": "cb"}),
    ("C12", "c12_g1_middle_sqrt", {"window_width": 8, "routine_definition": "cb"}),
    ("S-tw", "sens_trace_equal_weight", {"window_width": 8, "routine_definition": "cb"}),
    ("S-b1p", "sens_b1_present", {"window_width": 8, "routine_definition": "cb"}),
]

COLUMNS = [
    ("FARall", "far_all"),
    ("FARc", "far_clean"),
    ("FARb", "far_benign"),
    ("FARr", "far_resist"),
    ("FARcb", "far_clean_benign"),
    ("preS", "pre_alarm_rate"),
    ("preTol", "tolerant_pre_alarm_rate"),
    ("R4", "recall_plus_4"),
    ("R8", "recall_plus_8"),
    ("R16", "recall_plus_16"),
    ("RF", "recall_final"),
    ("R16t", "tolerant_recall_plus_16"),
    ("RFt", "tolerant_recall_final"),
    ("lat", "median_latency"),
    ("cb+16", "completion_recall_plus_16"),
    ("on/1k", "alarm_onsets_per_1000_negative_positions"),
]


def summaries(run: str) -> list[dict[str, Any]]:
    return json.loads((RUNS / run / "summary.json").read_text(encoding="utf-8"))


def fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}" if abs(value) < 100 else f"{value:.1f}"
    return str(value)


def table(rows: Iterable[dict[str, Any]], lead: list[tuple[str, str]]) -> str:
    lead_names = [name for name, _ in lead]
    header = "| " + " | ".join(lead_names + [n for n, _ in COLUMNS]) + " |"
    divider = "|" + "|".join("---" for _ in lead_names + COLUMNS) + "|"
    lines = [header, divider]
    for row in rows:
        cells = [fmt(row.get(key)) for _, key in lead] + [fmt(row.get(key)) for _, key in COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def pick(rows, **filters):
    out = []
    for row in rows:
        if all(row.get(k) == v for k, v in filters.items()):
            out.append(row)
    return out


def main() -> None:
    what = sys.argv[1] if len(sys.argv) > 1 else "all"

    if what in ("all", "primary"):
        print("### T-A primary configuration C1 (all readings, both modes, both alphas)\n")
        rows = summaries("c1_c7_c8_c9_g1_middle")
        selected = pick(rows, window_width=8, routine_definition="cb")
        selected.sort(key=lambda r: (r["case"], r["mode"], r["alpha"], r["reading"]))
        for row in selected:
            row["label"] = "C1"
        print(
            table(
                selected,
                [("dir", "case"), ("mode", "mode"), ("alpha", "alpha"), ("read", "reading")],
            )
        )
        print()

    if what in ("all", "candidates"):
        for reading in ("persist2", "max", "cusum1"):
            for mode in ("D", "T"):
                for alpha in (0.10, 0.05):
                    print(f"\n### all candidates -- mode {mode}, alpha {alpha}, reading {reading}\n")
                    rows = []
                    for label, run, filters in CANDIDATES:
                        found = pick(
                            summaries(run), mode=mode, alpha=alpha, reading=reading, **filters
                        )
                        for row in found:
                            row["label"] = label
                            rows.append(row)
                    rows.sort(key=lambda r: (r["case"], CANDIDATES.index(
                        next(c for c in CANDIDATES if c[0] == r["label"]))))
                    print(table(rows, [("cand", "label"), ("dir", "case")]))

    if what in ("all", "s2s3"):
        for label, run in (("C1", "c1_s2_s3"), ("C3", "c3_s2_s3"), ("C5", "c5_s2_s3")):
            print(f"\n### {label} pooled S2/S3\n")
            pooled = json.loads((RUNS / run / "pooled.json").read_text(encoding="utf-8"))
            for row in pooled:
                row["label"] = label
            print(table(pooled, [("cand", "label"), ("split", "split"), ("case", "case"),
                                 ("mode", "mode"), ("read", "reading")]))
            print(f"\n#### {label} per-domain (S3, mode D, persist2)\n")
            rows = pick(summaries(run), split="S3", mode="D", reading="persist2")
            rows.sort(key=lambda r: r["case"])
            print(table(rows, [("case", "case")]))


if __name__ == "__main__":
    main()
