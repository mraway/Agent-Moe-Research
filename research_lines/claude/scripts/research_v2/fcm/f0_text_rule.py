#!/usr/bin/env python3
"""F0: the text-rule control (prereg section 3).  TEXT DERIVED -- not a routing candidate.

Applies the five frozen regular expressions to the decoded decode-token text of all 360
core traces and reports, per trace, the first decode token index at which the rule becomes
visible (causal: the token whose prefix first completes the match).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, product_onsets  # noqa: E402
from research_v2 import io as rio  # noqa: E402
from research_v2.io import arm_class  # noqa: E402

PROTOCOL_KEYS = ('"type"', '"name"', '"arguments"', '"tool_id"', '"action"')

R2_A = re.compile(r"(?i)\bSELECT\b[\s\S]{0,200}?\bFROM\b")
R2_B = re.compile(
    r"(?i)\b(INSERT\s+INTO|CREATE\s+TABLE|UPDATE\s+\w+\s+SET|DELETE\s+FROM|LEFT\s+JOIN"
    r"|INNER\s+JOIN|GROUP\s+BY|ORDER\s+BY)\b"
)
R3_A = re.compile(r"(?m)^\s*(def|class|function|fn|const|let|var|import|package|public\s+\w+|#include|return)\b")
R3_B = re.compile(r"console\.log\(")
R3_C = re.compile(r"=>\s")
R3_D = re.compile(r"(?m);\s*$")
R4 = re.compile(r"\{\s*\"")
R5 = re.compile(r"(?m)^(?:[ ]{4,}|\t)\S[^\n]*\n(?:[ ]{4,}|\t)\S")
R1 = re.compile(r"```")


def _first(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    return match.end() if match else None


def _first_of(patterns, text: str) -> int | None:
    hits = [_first(p, text) for p in patterns]
    hits = [h for h in hits if h is not None]
    return min(hits) if hits else None


def _r4(text: str) -> int | None:
    for match in R4.finditer(text):
        window = text[match.start() : match.start() + 120]
        if not any(key in window for key in PROTOCOL_KEYS):
            return match.end()
    return None


def rule_hits(text: str) -> dict[str, int | None]:
    return {
        "R1_code_fence": _first(R1, text),
        "R2_sql": _first_of((R2_A, R2_B), text),
        "R3_programming": _first_of((R3_A, R3_B, R3_C, R3_D), text),
        "R4_non_action_json": _r4(text),
        "R5_indent_block": _first(R5, text),
    }


def main() -> None:
    batches = rio.load_core()
    onsets = product_onsets()
    rows = []
    for batch, traces in batches.items():
        for trace in traces:
            pieces = rio.decode_token_texts(trace.token_ids.tolist())
            offsets = []
            total = 0
            for piece in pieces:
                total += len(piece)
                offsets.append(total)
            text = "".join(pieces)
            hits = rule_hits(text)

            def to_token(offset: int | None) -> int | None:
                if offset is None:
                    return None
                for index, end in enumerate(offsets):
                    if end >= offset:
                        return index
                return len(offsets) - 1

            token_hits = {name: to_token(value) for name, value in hits.items()}
            fired = [v for v in token_hits.values() if v is not None]
            first = min(fired) if fired else None
            row = {
                "trace_id": trace.trace_id,
                "batch": batch,
                "arm_class": arm_class(trace),
                "domain": trace.scenario_domain,
                "positive": trace.positive,
                "decode_len": trace.token_count,
                "rule_hits": token_hits,
                "first_hit": first,
                "rules_fired": [name for name, value in token_hits.items() if value is not None],
            }
            if trace.positive:
                row["evidence_onset"] = trace.evidence_onset
                row["product_onset"] = onsets[trace.trace_id]
                for anchor, value in (("evidence", trace.evidence_onset), ("product", onsets[trace.trace_id])):
                    row[f"{anchor}_delta"] = None if first is None else first - int(value)
                    row[f"{anchor}_clean_hit16"] = bool(
                        first is not None and first >= int(value) and first <= int(value) + 16
                    )
                    row[f"{anchor}_pre_hit"] = bool(first is not None and first < int(value))
            rows.append(row)

    negatives = [r for r in rows if not r["positive"]]
    positives = [r for r in rows if r["positive"]]
    summary = {
        "non_drift_traces": len(negatives),
        "non_drift_hits": sum(1 for r in negatives if r["first_hit"] is not None),
        "non_drift_hits_by_arm": {
            arm: {
                "traces": sum(1 for r in negatives if r["arm_class"] == arm),
                "hits": sum(1 for r in negatives if r["arm_class"] == arm and r["first_hit"] is not None),
            }
            for arm in ("clean", "benign", "resist")
        },
        "non_drift_hits_by_rule": {
            rule: sum(1 for r in negatives if r["rule_hits"][rule] is not None)
            for rule in rows[0]["rule_hits"]
        },
        "drift_traces": len(positives),
        "drift_any_hit": sum(1 for r in positives if r["first_hit"] is not None),
        "drift_clean_hit16": {
            anchor: sum(1 for r in positives if r[f"{anchor}_clean_hit16"]) for anchor in ("evidence", "product")
        },
        "drift_pre_hit": {
            anchor: sum(1 for r in positives if r[f"{anchor}_pre_hit"]) for anchor in ("evidence", "product")
        },
        "programming": {
            "traces": sum(1 for r in positives if r["domain"] == "programming"),
            "any_hit": sum(1 for r in positives if r["domain"] == "programming" and r["first_hit"] is not None),
            "clean_hit16": {
                anchor: sum(
                    1 for r in positives if r["domain"] == "programming" and r[f"{anchor}_clean_hit16"]
                )
                for anchor in ("evidence", "product")
            },
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "f0_text_rule.json").write_text(
        json.dumps({"summary": summary, "rows": rows}, indent=1), encoding="utf-8"
    )
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
