#!/usr/bin/env python3
"""Build the CM report tables from the harness run outputs (docs/research_v2/cm_prereg.md section 6).

Reads ``summary.json`` / ``pooled.json`` under ``artifacts/agent_v2/research_v2/cm/`` and
writes ``artifacts/agent_v2/research_v2/cm/cm_tables/tables.md`` plus ``tables.json``.
No metric is recomputed here -- this is a formatter.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
CM_ROOT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "cm"
OUTPUT = CM_ROOT / "cm_tables"

# label -> (run name, window, routine definition)
CANDIDATES: list[tuple[str, str, int, str]] = [
    ("1 C1 middle w8 (PRIMARY)", "cm_c1_middle", 8, "cb"),
    ("2 C1+C2 middle w8", "cm_c1c2_middle", 8, "cb"),
    ("3 C1 all w8", "cm_c1_all", 8, "cb"),
    ("4 C1+C2 all w8", "cm_c1c2_all", 8, "cb"),
    ("5 C1 middle w8 +C3", "cm_c1_middle_c3", 8, "cb"),
    ("6 C1+C2 middle w8 +C3", "cm_c1c2_middle_c3", 8, "cb"),
    ("7 C1 all w8 +C3", "cm_c1_all_c3", 8, "cb"),
    ("8 C1+C2 all w8 +C3", "cm_c1c2_all_c3", 8, "cb"),
    ("9 C1 middle w4", "cm_c1_middle", 4, "cb"),
    ("10 C1 middle w16", "cm_c1_middle", 16, "cb"),
    ("11 C1 middle w8 R+", "cm_c1_middle", 8, "all_normal"),
    ("12 C1+C2 early w8", "cm_c1c2_early", 8, "cb"),
]

DIAGNOSTICS: list[tuple[str, str, int, str]] = [
    ("D1 C1 middle w8 covered-only", "cm_cov0", 8, "cb"),
    ("D2 x_t kNN text control", "cm_x_knn", 8, "cb"),
    ("D3 C1+C2 late w8", "cm_c1c2_late", 8, "cb"),
]

REFERENCES: list[tuple[str, str, int, str]] = [
    ("G1 whitened distance", "cm_ref_g1", 8, "cb"),
    ("G1 whitened distance R+", "cm_ref_g1", 8, "all_normal"),
    ("T2 oov fraction", "cm_ref_t2", 8, "cb"),
    ("T1(full) embedding kNN", "cm_ref_t1full", 8, "cb"),
    ("S0* supervised diffmeans", "cm_ref_s0", 8, "cb"),
]

COLUMNS = [
    ("FARall", "far_all"),
    ("FARcb", "far_clean_benign"),
    ("FARc", "far_clean"),
    ("FARb", "far_benign"),
    ("FARr", "far_resist"),
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


def load_summary(run: str) -> list[dict[str, Any]]:
    path = CM_ROOT / run / "summary.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def load_pooled(run: str) -> list[dict[str, Any]]:
    path = CM_ROOT / run / "pooled.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def pick(
    rows: Iterable[dict[str, Any]],
    *,
    case: str,
    window: int,
    routine: str,
    mode: str,
    alpha: float,
    reading: str,
) -> dict[str, Any] | None:
    for row in rows:
        if (
            row.get("case") == case
            and row.get("window_width") == window
            and row.get("routine_definition") == routine
            and row.get("mode") == mode
            and abs(row.get("alpha", -1) - alpha) < 1e-9
            and row.get("reading") == reading
        ):
            return row
    return None


def fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def render(rows: list[list[str]], header: list[str]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def metric_row(label: str, direction: str, summary_row: dict[str, Any] | None) -> list[str]:
    if summary_row is None:
        return [label, direction] + ["-"] * len(COLUMNS)
    return [label, direction] + [fmt(summary_row.get(key)) for _, key in COLUMNS]


def block(
    entries: list[tuple[str, str, int, str]],
    *,
    suffix: str,
    mode: str,
    alpha: float,
    reading: str,
) -> str:
    header = ["candidate", "dir"] + [name for name, _ in COLUMNS]
    rows: list[list[str]] = []
    for label, run, window, routine in entries:
        summary = load_summary(run + suffix)
        for case in ("b1_to_b2", "b2_to_b1"):
            rows.append(
                metric_row(
                    label,
                    case,
                    pick(
                        summary,
                        case=case,
                        window=window,
                        routine=routine,
                        mode=mode,
                        alpha=alpha,
                        reading=reading,
                    ),
                )
            )
    return render(rows, header)


def all_readings_block(run: str, window: int, routine: str, suffix: str = "") -> str:
    summary = load_summary(run + suffix)
    header = ["reading", "dir", "mode", "alpha"] + [name for name, _ in COLUMNS]
    readings = [
        "max",
        "persist2",
        "ewma01",
        "ewma02",
        "cusum05",
        "cusum1",
        "cusum2",
        "runlen4_1",
        "runlen8_1",
        "runlen4_2",
        "runlen8_2",
    ]
    rows: list[list[str]] = []
    for reading in readings:
        for mode in ("D", "T"):
            for alpha in (0.10, 0.05):
                for case in ("b1_to_b2", "b2_to_b1"):
                    row = pick(
                        summary,
                        case=case,
                        window=window,
                        routine=routine,
                        mode=mode,
                        alpha=alpha,
                        reading=reading,
                    )
                    if row is None:
                        continue
                    rows.append(
                        [reading, case, mode, f"{alpha:g}"]
                        + [fmt(row.get(key)) for _, key in COLUMNS]
                    )
    return render(rows, header)


def pooled_block(runs: list[tuple[str, str]]) -> str:
    header = ["scorer", "split", "case", "mode", "reading"] + [name for name, _ in COLUMNS]
    rows: list[list[str]] = []
    for label, run in runs:
        for row in load_pooled(run):
            if row.get("reading") not in ("max", "persist2"):
                continue
            rows.append(
                [label, row.get("split", "-"), row.get("case", "-"), row.get("mode", "-"), row.get("reading", "-")]
                + [fmt(row.get(key)) for _, key in COLUMNS]
            )
    return render(rows, header)


def per_domain_block(run: str) -> str:
    result = json.loads((CM_ROOT / run / "result.json").read_text(encoding="utf-8"))
    header = ["domain", "drift n", "mode", "R8", "R16", "RF", "R16tol", "RFtol"]
    rows: list[list[str]] = []
    for case_run in result["case_runs"]:
        if case_run["split"] != "S3":
            continue
        domain = case_run["case"].replace("s3_", "")
        for candidate in case_run["candidates"]:
            if candidate["reading"] != "persist2" or abs(candidate["alpha"] - 0.10) > 1e-9:
                continue
            strict = candidate["metrics"]["onset_strict"]
            tolerant = candidate["metrics"]["onset_tolerant"]
            rows.append(
                [
                    domain,
                    str(candidate["metrics"]["drift_trace_count"]),
                    candidate["mode"],
                    fmt(strict.get("recall_plus_8")),
                    fmt(strict.get("recall_plus_16")),
                    fmt(strict.get("recall_final")),
                    fmt(tolerant.get("recall_plus_16")),
                    fmt(tolerant.get("recall_final")),
                ]
            )
    return render(rows, header)


def q1_block(runs: list[tuple[str, str]]) -> str:
    header = [
        "scorer",
        "dir",
        "first separation (2SD & AUROC>=0.9)",
        "first paired AUROC>=0.9",
        "rise>0 fraction (n)",
        "anchor AUROC median (tokens, IQR)",
    ]
    rows: list[list[str]] = []
    for label, run in runs:
        path = CM_ROOT / run / "q1_panel.json"
        if not path.exists():
            continue
        panels = json.loads(path.read_text(encoding="utf-8"))
        for key, panel in sorted(panels.items()):
            if not key.endswith("|w8|routine=cb"):
                continue
            case = key.split(":")[1].split("|")[0]
            anchor = panel["anchor_token_auroc"]
            first_auroc = next(
                (row["offset"] for row in panel["event_curves"] if (row["paired_auroc"] or 0) >= 0.9),
                None,
            )
            rows.append(
                [
                    label,
                    case,
                    str(panel["first_separation_offset"]),
                    str(first_auroc),
                    f"{fmt(panel['per_drift_rise_positive_fraction'])} ({len(panel['per_drift_rise'])})",
                    f"{fmt(anchor['median'])} ({anchor['token_count']}, {fmt(anchor['q1'])}-{fmt(anchor['q3'])})",
                ]
            )
    return render(rows, header)


def event_curve_block(run: str, case: str) -> str:
    path = CM_ROOT / run / "q1_panel.json"
    panels = json.loads(path.read_text(encoding="utf-8"))
    panel = panels[f"S1:{case}|w8|routine=cb"]
    header = ["offset", "n", "drift mean", "drift median", "clean mean", "pooled sd", "paired AUROC"]
    rows = [
        [
            str(row["offset"]),
            str(row["n"]),
            fmt(row["drift_mean"]),
            fmt(row["drift_median"]),
            fmt(row["clean_mean"]),
            fmt(row["pooled_sd"]),
            fmt(row["paired_auroc"]),
        ]
        for row in panel["event_curves"]
        if row["offset"] % 4 == 0
    ]
    return render(rows, header)


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    sections: list[str] = []
    for suffix, title in (("", "main table (with --b1-present-calibration)"), ("_nb1p", "sensitivity (without)")):
        for mode in ("D", "T"):
            for alpha in (0.10, 0.05):
                for reading in ("persist2", "max"):
                    sections.append(
                        f"### candidates | {title} | mode {mode} | alpha {alpha:g} | reading {reading}\n\n"
                        + block(CANDIDATES, suffix=suffix, mode=mode, alpha=alpha, reading=reading)
                    )
                    sections.append(
                        f"### diagnostics+references | {title} | mode {mode} | alpha {alpha:g} | reading {reading}\n\n"
                        + block(DIAGNOSTICS + REFERENCES, suffix=suffix, mode=mode, alpha=alpha, reading=reading)
                    )
    sections.append("### primary candidate, all readings (with b1-present)\n\n" + all_readings_block("cm_c1_middle", 8, "cb"))
    sections.append("### primary candidate, all readings (without b1-present)\n\n" + all_readings_block("cm_c1_middle", 8, "cb", "_nb1p"))
    sections.append(
        "### S2 / S3 pooled\n\n"
        + pooled_block([("CM C1", "cm_c1_middle_s2_s3"), ("CM C1+C2", "cm_c1c2_middle_s2_s3"), ("G1", "cm_ref_g1_s2_s3")])
    )
    sections.append("### S3 per domain, CM C1 primary\n\n" + per_domain_block("cm_c1_middle_s2_s3"))
    sections.append(
        "### Q1 panel\n\n"
        + q1_block(
            [
                ("CM C1", "cm_c1_middle"),
                ("CM C1+C2", "cm_c1c2_middle"),
                ("CM covered-only", "cm_cov0"),
                ("CM x_t kNN", "cm_x_knn"),
                ("G1", "cm_ref_g1"),
                ("T2", "cm_ref_t2"),
            ]
        )
    )
    for case in ("b1_to_b2", "b2_to_b1"):
        sections.append(f"### event curve, CM C1 primary, {case}\n\n" + event_curve_block("cm_c1_middle", case))
    text = "# CM tables (generated by scripts/research_v2/cm_tables.py)\n\n" + "\n\n".join(sections) + "\n"
    (OUTPUT / "tables.md").write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT / 'tables.md'} ({len(text)} chars)")


if __name__ == "__main__":
    main()
