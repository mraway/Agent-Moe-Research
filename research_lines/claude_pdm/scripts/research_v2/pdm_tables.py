#!/usr/bin/env python3
"""Build the PDM report tables from the harness run directories (spec 7.3).

Reads only the frozen run outputs under ``artifacts/agent_v2/research_v2/pdm_*`` (plus the
w=4 baseline runs) and writes one markdown file with every table the preregistration lists.

    .venv/bin/python scripts/research_v2/pdm_tables.py
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

RESULTS = ROOT / "artifacts" / "agent_v2" / "research_v2"

# candidate label -> (run directory, model, layer band, window, routine definition)
CANDIDATES: dict[str, tuple[str, str, str, int, str]] = {
    "C1 D1 w1": ("pdm_d1_s1", "D1", "all", 1, "cb"),
    "C2 D1 w4": ("pdm_d1_s1", "D1", "all", 4, "cb"),
    "C3 D1 w16": ("pdm_d1_s1", "D1", "all", 16, "cb"),
    "C4 D2 w1": ("pdm_d2_s1", "D2", "all", 1, "cb"),
    "C5 D2 w4": ("pdm_d2_s1", "D2", "all", 4, "cb"),
    "C6 D2 w16": ("pdm_d2_s1", "D2", "all", 16, "cb"),
    "C7 D1+D2 w1": ("pdm_d1d2_s1", "D1+D2", "all", 1, "cb"),
    "C8* D1+D2 w4": ("pdm_d1d2_s1", "D1+D2", "all", 4, "cb"),
    "C9 D1+D2 w16": ("pdm_d1d2_s1", "D1+D2", "all", 16, "cb"),
    "C10 D3 w4": ("pdm_d3_s1", "D3", "all", 4, "cb"),
    "C11 D1 w4 R+": ("pdm_d1_rplus_s1", "D1", "all", 4, "all_normal"),
    "C12 D1mid w4": ("pdm_d1_middle_s1", "D1", "middle", 4, "cb"),
}

BASELINES: dict[str, tuple[str, int]] = {
    "G1 w4": ("pdm_baseline_g1_w4", 4),
    "T2 w4": ("pdm_baseline_t2_w4", 4),
    "T1(full) w4": ("pdm_baseline_t1full_w4", 4),
}

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
    ("cbF", "completion_recall_final"),
    ("on/1k", "alarm_onsets_per_1000_negative_positions"),
]
DIRS = ("b1_to_b2", "b2_to_b1")


def load_summary(run: str) -> list[dict]:
    return json.loads((RESULTS / run / "summary.json").read_text(encoding="utf-8"))


def load_result(run: str) -> dict:
    return json.loads((RESULTS / run / "result.json").read_text(encoding="utf-8"))


def fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def pick(rows: list[dict], **filters) -> list[dict]:
    out = []
    for row in rows:
        if all(row.get(key) == value for key, value in filters.items()):
            out.append(row)
    return out


def one(rows: list[dict], **filters) -> dict | None:
    hits = pick(rows, **filters)
    if len(hits) > 1:
        raise ValueError(f"ambiguous selection: {filters} -> {len(hits)}")
    return hits[0] if hits else None


def table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines.extend("| " + " | ".join(cell for cell in row) + " |" for row in rows)
    return "\n".join(lines)


def metric_cells(row: dict | None) -> list[str]:
    if row is None:
        return ["-"] * len(COLUMNS)
    return [fmt(row.get(key)) for _, key in COLUMNS]


def t1_all_candidates(reading: str = "persist2") -> str:
    header = ["候选", "dir", "mode", "alpha"] + [name for name, _ in COLUMNS]
    rows: list[list[str]] = []
    cache: dict[str, list[dict]] = {}
    for label, (run, _model, _band, width, routine) in CANDIDATES.items():
        summary = cache.setdefault(run, load_summary(run))
        for direction in DIRS:
            for mode in ("D", "T"):
                for alpha in (0.10, 0.05):
                    row = one(
                        summary,
                        case=direction,
                        window_width=width,
                        routine_definition=routine,
                        mode=mode,
                        alpha=alpha,
                        reading=reading,
                    )
                    rows.append(
                        [label, direction, mode, f"{alpha:g}"] + metric_cells(row)
                    )
    return table(header, rows)


def t2_primary_readings() -> str:
    summary = load_summary("pdm_d1d2_s1")
    header = ["dir", "mode", "alpha", "reading"] + [name for name, _ in COLUMNS]
    rows = []
    for direction in DIRS:
        for mode in ("D", "T"):
            for alpha in (0.10, 0.05):
                for row in pick(
                    summary,
                    case=direction,
                    window_width=4,
                    routine_definition="cb",
                    mode=mode,
                    alpha=alpha,
                ):
                    rows.append(
                        [direction, mode, f"{alpha:g}", row["reading"]] + metric_cells(row)
                    )
    return table(header, rows)


def t3_frontier() -> str:
    summary = load_summary("pdm_d1d2_s1")
    header = ["w", "dir", "alpha", "reading"] + [name for name, _ in COLUMNS]
    rows = []
    for width in (1, 4):
        for direction in DIRS:
            for alpha in (0.10, 0.05):
                for row in pick(
                    summary,
                    case=direction,
                    window_width=width,
                    routine_definition="cb",
                    mode="D",
                    alpha=alpha,
                ):
                    rows.append(
                        [str(width), direction, f"{alpha:g}", row["reading"]] + metric_cells(row)
                    )
    return table(header, rows)


def t8_baselines() -> str:
    header = ["scorer", "dir", "mode", "alpha", "reading"] + [name for name, _ in COLUMNS]
    rows = []
    primary = load_summary("pdm_d1d2_s1")
    for direction in DIRS:
        for mode in ("D", "T"):
            for reading in ("persist2", "max"):
                row = one(
                    primary,
                    case=direction,
                    window_width=4,
                    routine_definition="cb",
                    mode=mode,
                    alpha=0.10,
                    reading=reading,
                )
                rows.append(["PDM C8* w4", direction, mode, "0.1", reading] + metric_cells(row))
    for label, (run, width) in BASELINES.items():
        summary = load_summary(run)
        for direction in DIRS:
            for mode in ("D", "T"):
                for reading in ("persist2", "max"):
                    row = one(
                        summary,
                        case=direction,
                        window_width=width,
                        routine_definition="cb",
                        mode=mode,
                        alpha=0.10,
                        reading=reading,
                    )
                    rows.append([label, direction, mode, "0.1", reading] + metric_cells(row))
    return table(header, rows)


def t6_t7_pooled(run: str, split: str) -> str:
    pooled = json.loads((RESULTS / run / "pooled.json").read_text(encoding="utf-8"))
    header = ["split", "case", "mode", "reading"] + [name for name, _ in COLUMNS]
    rows = []
    for row in pooled:
        if row["split"] != split:
            continue
        if row["reading"] not in ("persist2", "max", "cusum1", "runlen8_2"):
            continue
        rows.append([row["split"], row["case"], row["mode"], row["reading"]] + metric_cells(row))
    return table(header, rows)


def s3_by_domain(run: str, mode: str = "D", reading: str = "persist2") -> str:
    result = load_result(run)
    header = ["域", "drift 条数", "R8", "R16", "RF", "R16t", "RFt", "preS"]
    rows = []
    for case_run in result["case_runs"]:
        if case_run["split"] != "S3":
            continue
        for candidate in case_run["candidates"]:
            if candidate["mode"] != mode or candidate["reading"] != reading:
                continue
            if candidate["alpha"] != 0.10:
                continue
            metrics = candidate["metrics"]
            strict = metrics["onset_strict"]
            tolerant = metrics["onset_tolerant"]
            rows.append(
                [
                    case_run["case"].replace("lodo_", ""),
                    str(int(strict["trace_count"])),
                    fmt(strict["recall_plus_8"]),
                    fmt(strict["recall_plus_16"]),
                    fmt(strict["recall_final"]),
                    fmt(tolerant["recall_plus_16"]),
                    fmt(tolerant["recall_final"]),
                    fmt(strict["pre_alarm_rate"]),
                ]
            )
    rows.sort(key=lambda row: -float(row[4] if row[4] != "-" else 0))
    return table(header, rows)


def t9_q1(run: str, width: int) -> str:
    panel = json.loads((RESULTS / run / "q1_panel.json").read_text(encoding="utf-8"))
    header = [
        "case",
        "首个双条件分离偏移",
        "首个配对 AUROC>=0.9 偏移",
        "rise>0 比例 (n)",
        "anchor-token AUROC 中位 (n, IQR)",
    ]
    rows = []
    for key, block in sorted(panel.items()):
        if f"|w{width}|" not in key:
            continue
        auroc_first = None
        for row in block["event_curves"]:
            if (row["paired_auroc"] or 0.0) >= 0.9 and auroc_first is None:
                auroc_first = row["offset"]
        anchor = block["anchor_token_auroc"]
        rows.append(
            [
                key,
                str(block["first_separation_offset"]),
                str(auroc_first),
                f"{fmt(block['per_drift_rise_positive_fraction'])} ({len(block['per_drift_rise'])})",
                f"{fmt(anchor['median'])} ({anchor['token_count']}, {fmt(anchor['q1'])}-{fmt(anchor['q3'])})",
            ]
        )
    return table(header, rows)


def q1_curve(run: str, width: int, case: str) -> str:
    panel = json.loads((RESULTS / run / "q1_panel.json").read_text(encoding="utf-8"))
    key = [k for k in panel if f"|w{width}|" in k and case in k][0]
    header = ["offset", "n", "drift 均值", "clean 均值", "合并 SD", "配对 AUROC"]
    rows = []
    for row in panel[key]["event_curves"]:
        if row["offset"] % 4 != 0:
            continue
        rows.append(
            [
                str(row["offset"]),
                str(row["n"]),
                fmt(row["drift_mean"]),
                fmt(row["clean_mean"]),
                fmt(row["pooled_sd"]),
                fmt(row["paired_auroc"]),
            ]
        )
    return table(header, rows)


def t10_ranking(run: str, width: int) -> str:
    result = load_result(run)
    header = [
        "case",
        "w",
        "drift-post vs clean",
        "vs benign",
        "vs resist",
        "vs routine",
        "drift-pre vs routine",
        "benign vs clean",
        "resist vs clean",
    ]
    rows = []
    for case_run in result["case_runs"]:
        if case_run["window_width"] != width or "ranking" not in case_run:
            continue
        block = case_run["ranking"]
        rows.append(
            [
                case_run["case"],
                str(width),
                fmt(block.get("drift_post_vs_clean")),
                fmt(block.get("drift_post_vs_benign")),
                fmt(block.get("drift_post_vs_resist")),
                fmt(block.get("drift_post_vs_routine")),
                fmt(block.get("drift_pre_vs_routine")),
                fmt(block.get("benign_vs_clean")),
                fmt(block.get("resist_vs_clean")),
            ]
        )
    return table(header, rows)


def t11_bootstrap(run: str, width: int, reading: str = "persist2") -> str:
    result = load_result(run)
    header = ["case", "scenario 数", "FARall", "R8", "R16", "RF", "R16t"]
    rows = []
    for case_run in result["case_runs"]:
        if case_run["window_width"] != width:
            continue
        for candidate in case_run["candidates"]:
            if (
                candidate["mode"] != "D"
                or candidate["alpha"] != 0.10
                or candidate["reading"] != reading
                or "bootstrap" not in candidate
            ):
                continue
            block = candidate["bootstrap"]

            def cell(key: str) -> str:
                item = block.get(key)
                if not item:
                    return "-"
                return f"{fmt(item['point'])} [{fmt(item['low'])}, {fmt(item['high'])}]"

            rows.append(
                [
                    case_run["case"],
                    str(block["scenario_count"]),
                    cell("non_drift_false_alarm_rate"),
                    cell("recall_plus_8"),
                    cell("recall_plus_16"),
                    cell("recall_final"),
                    cell("tolerant_recall_plus_16"),
                ]
            )
    return table(header, rows)


def t12_b1present() -> str:
    header = ["scorer", "校准池", "dir", "alpha", "reading"] + [name for name, _ in COLUMNS]
    rows = []
    pairs = [
        ("C8* D1+D2 w4", "pdm_d1d2_s1", "pdm_b1present_sens"),
        ("C2 D1 w4", "pdm_d1_s1", "pdm_b1present_sens_d1"),
        ("C5 D2 w4", "pdm_d2_s1", "pdm_b1present_sens_d2"),
    ]
    for label, base_run, sens_run in pairs:
        for tag, run in (("默认(不并入)", base_run), ("+B1 present 80", sens_run)):
            summary = load_summary(run)
            for alpha in (0.10, 0.05):
                for reading in ("persist2", "max"):
                    row = one(
                        summary,
                        case="b2_to_b1",
                        window_width=4,
                        routine_definition="cb",
                        mode="D",
                        alpha=alpha,
                        reading=reading,
                    )
                    rows.append([label, tag, "b2_to_b1", f"{alpha:g}", reading] + metric_cells(row))
    return table(header, rows)


def calibration_note() -> str:
    result = load_result("pdm_d1d2_s1")
    header = ["case", "w", "校准半", "校准 trace 数", "评价 trace 数", "桶上限", "每桶 trace 数"]
    rows = []
    for case_run in result["case_runs"]:
        if case_run["window_width"] != 4:
            continue
        block = case_run["calibration"]["D"]["halves"]
        for half, item in sorted(block.items()):
            rows.append(
                [
                    case_run["case"],
                    "4",
                    half,
                    str(item["calibration_trace_count"]),
                    str(item["evaluated_trace_count"]),
                    str(item["bucket_cap"]),
                    str(item["bucket_trace_counts"]),
                ]
            )
    return table(header, rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=RESULTS / "pdm_tables" / "pdm_report_tables.md"
    )
    args = parser.parse_args()
    sections = [
        ("T-1 全部候选（读法 persist2）", t1_all_candidates("persist2")),
        ("T-1b 全部候选（读法 max）", t1_all_candidates("max")),
        ("T-2 主配置全读法", t2_primary_readings()),
        ("T-3 读法前沿（D1+D2, w=1 与 w=4, 模式 D）", t3_frontier()),
        ("T-6 S2 pooled（主配置）", t6_t7_pooled("pdm_primary_s2_s3", "S2")),
        ("T-6b S2 pooled（D1 w4）", t6_t7_pooled("pdm_d1_s2_s3", "S2")),
        ("T-6c S2 pooled（D2 w4）", t6_t7_pooled("pdm_d2_s2_s3", "S2")),
        ("T-7 S3 pooled（主配置）", t6_t7_pooled("pdm_primary_s2_s3", "S3")),
        ("T-7b S3 每域（主配置，模式 D，persist2）", s3_by_domain("pdm_primary_s2_s3")),
        ("T-7c S3 每域（主配置，模式 T，persist2）", s3_by_domain("pdm_primary_s2_s3", mode="T")),
        ("T-8 基线并排", t8_baselines()),
        ("T-9 Q1 面板摘要（w=4）", t9_q1("pdm_d1d2_s1", 4)),
        ("T-9b Q1 事件曲线（w=4, b1_to_b2）", q1_curve("pdm_d1d2_s1", 4, "b1_to_b2")),
        ("T-9c Q1 事件曲线（w=4, b2_to_b1）", q1_curve("pdm_d1d2_s1", 4, "b2_to_b1")),
        ("T-10 排序诊断（w=4）", t10_ranking("pdm_d1d2_s1", 4)),
        ("T-10b 排序诊断（w=1）", t10_ranking("pdm_d1d2_s1", 1)),
        ("T-11 bootstrap（主配置）", t11_bootstrap("pdm_d1d2_s1", 4)),
        ("T-12 B1 brief=present 校准敏感性", t12_b1present()),
        ("附 校准诊断（w=4，模式 D）", calibration_note()),
    ]
    text = "\n\n".join(f"### {title}\n\n{body}" for title, body in sections)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text + "\n", encoding="utf-8")
    print(f"wrote {args.output}")


if __name__ == "__main__":
    main()
