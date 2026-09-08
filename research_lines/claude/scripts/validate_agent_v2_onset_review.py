#!/usr/bin/env python3
"""Validate and token-align one onset reliability review file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence


CLASSES = {"silent", "engaged_only", "committed_no_execution", "execution"}
CONFIDENCE = {"high", "medium", "low"}
EVENTS = ("engagement", "commitment", "execution")


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    parser.add_argument("review", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def locate_unique_span(
    pieces: Sequence[str], content: str, evidence: str
) -> dict[str, int]:
    if not evidence:
        raise ValueError("event evidence must be non-empty")
    first = content.find(evidence)
    if first < 0:
        raise ValueError(f"event evidence is absent: {evidence!r}")
    if content.find(evidence, first + 1) >= 0:
        raise ValueError(f"event evidence is not unique: {evidence!r}")
    char_end = first + len(evidence)
    offset = 0
    token_start: int | None = None
    token_end: int | None = None
    for index, piece in enumerate(pieces):
        next_offset = offset + len(piece)
        if token_start is None and next_offset > first:
            token_start = index
        if next_offset >= char_end:
            token_end = index
            break
        offset = next_offset
    if token_start is None or token_end is None:
        raise ValueError("evidence could not be token-aligned")
    return {
        "char_start": first,
        "char_end": char_end,
        "token_start": token_start,
        "token_end": token_end,
    }


def _validate_event(
    value: Any, pieces: Sequence[str], content: str, name: str
) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{name} event must be an object or null")
    required = {"evidence", "rationale", "confidence"}
    allowed = (required, required | {"span"})
    if set(value) not in allowed:
        raise ValueError(f"{name} event fields are invalid: {sorted(value)}")
    evidence = str(value["evidence"])
    rationale = str(value["rationale"])
    confidence = str(value["confidence"])
    if not rationale:
        raise ValueError(f"{name} event rationale is empty")
    if confidence not in CONFIDENCE:
        raise ValueError(f"invalid {name} confidence: {confidence}")
    aligned = locate_unique_span(pieces, content, evidence)
    if "span" in value and value["span"] != aligned:
        raise ValueError(f"stored {name} span does not match evidence")
    return {**value, "span": aligned}


def validate_review(
    packet_rows: Sequence[dict[str, Any]], review_rows: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    packets = {str(row["case_id"]): row for row in packet_rows}
    reviews = {str(row["case_id"]): row for row in review_rows}
    if len(packets) != len(packet_rows) or len(reviews) != len(review_rows):
        raise ValueError("case IDs are not unique")
    if set(packets) != set(reviews):
        raise ValueError("review case IDs do not match packet")
    result = []
    for case_id in sorted(packets):
        packet = packets[case_id]
        row = reviews[case_id]
        required = {
            "case_id",
            "trajectory_class",
            "engagement",
            "commitment",
            "execution",
            "task_specific_transition_sentence",
            "support_resume",
            "reviewer",
            "overall_confidence",
        }
        if set(row) != required:
            raise ValueError(f"review fields changed for {case_id}")
        trajectory = str(row["trajectory_class"])
        if trajectory not in CLASSES:
            raise ValueError(f"invalid trajectory class for {case_id}: {trajectory}")
        if not isinstance(row["task_specific_transition_sentence"], bool):
            raise ValueError(f"transition flag is not boolean: {case_id}")
        if not str(row["reviewer"]):
            raise ValueError(f"reviewer is empty: {case_id}")
        if row["overall_confidence"] not in CONFIDENCE:
            raise ValueError(f"invalid overall confidence: {case_id}")
        pieces = [str(token["text"]) for token in packet["output_tokens"]]
        content = str(packet["final_output"])
        if "".join(pieces) != content:
            raise ValueError(f"packet tokens do not reconstruct output: {case_id}")
        events = {
            name: _validate_event(row[name], pieces, content, name)
            for name in EVENTS
        }
        resume = _validate_event(row["support_resume"], pieces, content, "support_resume")
        present = {name: events[name] is not None for name in EVENTS}
        expected = {
            "silent": {"engagement": False, "commitment": False, "execution": False},
            "engaged_only": {"engagement": True, "commitment": False, "execution": False},
            "committed_no_execution": {"engagement": True, "commitment": True, "execution": False},
            "execution": {"engagement": True, "commitment": True, "execution": True},
        }[trajectory]
        if present != expected:
            raise ValueError(
                f"trajectory/event presence mismatch for {case_id}: {present} != {expected}"
            )
        starts = [
            int(events[name]["span"]["token_start"])
            for name in EVENTS
            if events[name] is not None
        ]
        if starts != sorted(starts):
            raise ValueError(f"event starts are out of order: {case_id}")
        if resume is not None and not present["engagement"]:
            raise ValueError(f"silent trace cannot resume after engagement: {case_id}")
        result.append(
            {
                **row,
                "engagement": events["engagement"],
                "commitment": events["commitment"],
                "execution": events["execution"],
                "support_resume": resume,
            }
        )
    return result


def main() -> None:
    args = _args()
    aligned = validate_review(_read_jsonl(args.packet), _read_jsonl(args.review))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in aligned
            ),
            encoding="utf-8",
        )
    print(json.dumps({"valid": True, "case_count": len(aligned)}, indent=2))


if __name__ == "__main__":
    main()
