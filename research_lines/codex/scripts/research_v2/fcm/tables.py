#!/usr/bin/env python3
"""Markdown tables for the FCM report (prereg section 7)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import CANDIDATES, COMBINER, OUT, SENSITIVITY  # noqa: E402

ORDER = list(CANDIDATES) + ["F4"] + list(SENSITIVITY)
ALL_COLUMNS = ORDER + ["CAND-A", "CAND-B"]
DIRECTIONS = ("b1_to_b2", "b2_to_b1")


def fmt(value, digits: int = 3) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        if value == float("-inf"):
            return "-inf"
        return f"{value:.{digits}f}"
    return str(value)


def global_table(rows, *, mode="D", alpha=0.10, readings=("max", "persist2")) -> str:
    header = (
        "| cand | text | anchor | dir | read | FARall | FARc | FARb | FARr | preS | R4 | R8 | R16 | RF | lat |"
    )
    lines = [header, "|" + "---|" * 15]
    for candidate in ALL_COLUMNS:
        for anchor in ("evidence", "product"):
            for case in DIRECTIONS:
                for reading in readings:
                    match = [
                        r for r in rows
                        if r["candidate"] == candidate and r["anchor"] == anchor and r["case"] == case
                        and r["mode"] == mode and abs(r["alpha"] - alpha) < 1e-9 and r["reading"] == reading
                    ]
                    if not match:
                        continue
                    r = match[0]
                    lines.append(
                        "| "
                        + " | ".join(
                            [
                                candidate,
                                "T" if r.get("text_derived") else "R",
                                anchor,
                                case.replace("_to_", "->"),
                                reading,
                                fmt(r["far_all"]),
                                fmt(r["far_clean"]),
                                fmt(r["far_benign"]),
                                fmt(r["far_resist"]),
                                fmt(r["pre_alarm_rate"]),
                                fmt(r["recall_plus_4"]),
                                fmt(r["recall_plus_8"]),
                                fmt(r["recall_plus_16"]),
                                fmt(r["recall_final"]),
                                fmt(r["median_latency"], 1),
                            ]
                        )
                        + " |"
                    )
    return "\n".join(lines)


def per_trace_table(payload, anchor: str) -> str:
    per_trace = payload["per_trace"]
    order = sorted(per_trace, key=lambda t: (per_trace[t]["meta"]["domain"], t))
    header = "| trace | domain | dir | onset | reasons | " + " | ".join(ALL_COLUMNS) + " |"
    lines = [header, "|" + "---|" * (5 + len(ALL_COLUMNS))]
    for trace_id in order:
        entry = per_trace[trace_id]
        meta = entry["meta"]
        onset = meta["evidence_onset"] if anchor == "evidence" else meta["product_onset"]
        cells = []
        for candidate in ALL_COLUMNS:
            block = entry["candidates"].get(candidate, {}).get(anchor)
            if block is None:
                cells.append("-")
                continue
            margin = block["margin"]
            first = block["first_alarm_end"]
            flag = "H" if block["clean_hit16"] else ("P" if block["pre_alarm"] else ("L" if first is not None else "N"))
            cells.append(f"{fmt(margin, 2)} / {fmt(first)} {flag}")
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{trace_id}`",
                    meta["domain"],
                    meta["case"].replace("_to_", "->"),
                    str(onset),
                    "+".join(sorted(set(meta["reasons"]))),
                    *cells,
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def recovery_table(payload) -> str:
    lines = [
        "| cand | dir | anchor | ref | FARall/b/r | ref FARall/b/r | in tolerance | recovered | prog recovered | recovered traces |",
        "|" + "---|" * 10,
    ]
    for candidate in ORDER:
        for case in DIRECTIONS:
            for anchor in ("evidence", "product"):
                block = payload["recovery"][candidate][f"{case}|{anchor}"]
                far = block["far"]
                ref = block["reference_far"]
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            candidate,
                            case.replace("_to_", "->"),
                            anchor,
                            block["reference"],
                            f"{fmt(far['far_all'])}/{fmt(far['far_benign'])}/{fmt(far['far_resist'])}",
                            f"{fmt(ref['far_all'])}/{fmt(ref['far_benign'])}/{fmt(ref['far_resist'])}",
                            fmt(block["within_tolerance"]),
                            str(block["recovered_count"]),
                            str(len(block["programming_recovered"])),
                            ", ".join(f"`{t}`" for t in block["recovered"]) or "-",
                        ]
                    )
                    + " |"
                )
    return "\n".join(lines)


def programming_table(payload) -> str:
    lines = ["| cand | anchor | clean +16 hits / 8 | traces |", "|---|---|---|---|"]
    for candidate in ALL_COLUMNS:
        for anchor in ("evidence", "product"):
            block = payload["programming_clean_hits"][candidate][anchor]
            lines.append(
                f"| {candidate} | {anchor} | {block['count']}/8 | "
                + (", ".join(f"`{t}`" for t in block["traces"]) or "-")
                + " |"
            )
    return "\n".join(lines)


def main() -> None:
    payload = json.loads((OUT / "fcm_analysis.json").read_text(encoding="utf-8"))
    rows = payload["global_rows"]
    frozen = payload["frozen_reference"]
    for key, block in frozen.items():
        candidate, case, anchor = key.split("|")
        rows.append(
            {
                "candidate": candidate, "anchor": anchor, "text_derived": False, "case": case,
                "mode": "D", "alpha": 0.10, "reading": "persist2",
                "far_all": block["far_all"], "far_clean": block["far_clean"],
                "far_benign": block["far_benign"], "far_resist": block["far_resist"],
                "pre_alarm_rate": block["pre_alarm_rate"], "recall_plus_4": None,
                "recall_plus_8": block["recall_plus_8"], "recall_plus_16": block["recall_plus_16"],
                "recall_final": block["recall_final"], "median_latency": block["median_latency"],
            }
        )
    blocks = {
        "global_D_alpha010.md": global_table(rows, mode="D", alpha=0.10),
        "global_D_alpha005.md": global_table(rows, mode="D", alpha=0.05),
        "global_T_alpha010.md": global_table(rows, mode="T", alpha=0.10),
        "global_T_alpha005.md": global_table(rows, mode="T", alpha=0.05),
        "per_trace_evidence.md": per_trace_table(payload, "evidence"),
        "per_trace_product.md": per_trace_table(payload, "product"),
        "recovery.md": recovery_table(payload),
        "programming.md": programming_table(payload),
    }
    for name, text in blocks.items():
        (OUT / name).write_text(text + "\n", encoding="utf-8")
        print("written", OUT / name)


if __name__ == "__main__":
    main()
