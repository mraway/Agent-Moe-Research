#!/usr/bin/env python3
"""Validate one annotator's output file against its blind packet.

    packets_validate.py --packet .../packet.jsonl --annotation reviewer_a.jsonl \
        [--mapping .../case_mapping.jsonl] [--output aligned_a.jsonl] [--partial]

Every E / C / X evidence string is re-located in its channel, aligned to a
character span and to a span on the episode's global generated-token axis, and
checked against the trajectory class.  ``--mapping`` un-blinds the result for
``research_v2.io_g``; leave it out while annotation is still in progress.

Un-blinding also copies :data:`MAPPING_PASSTHROUGH` out of the private mapping
onto every row.  ``normal_variant`` is the one that matters (freeze review,
blocking item ARM IDENTITY): G-dev's ``benign_lexical`` and
``legitimate_refusal`` scenarios were COLLECTED under the arm name ``clean``, so
without this field nothing downstream -- ``g_dev_data_gates.py`` included -- can
tell those 48 episodes from the 192 real ``clean`` ones.  This is OUTPUT
ENRICHMENT ONLY: ``agent_v3.packets.validate`` and its acceptance rules are not
touched, so an annotation package that validated before validates identically
now, byte for byte in every field it already had.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agent_v3.packets import build, validate  # noqa: E402

#: private-mapping fields copied onto an un-blinded row.  Additive: the validator's own
#: output fields are unchanged, and a mapping without one of these leaves it out.
MAPPING_PASSTHROUGH = ("normal_variant", "scenario_role", "arm_name")


def enrich_with_mapping(
    rows: list[dict], mapping_rows: list[dict] | None
) -> list[dict]:
    """Copy :data:`MAPPING_PASSTHROUGH` from the private mapping onto each row."""

    if mapping_rows is None:
        return rows
    mapping = {str(row["case_id"]): row for row in mapping_rows}
    for row in rows:
        entry = mapping.get(str(row.get("case_id")))
        if entry is None:
            continue
        for field in MAPPING_PASSTHROUGH:
            if field in entry and entry[field] is not None:
                row[field] = entry[field]
    return rows


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--annotation", type=Path, required=True)
    parser.add_argument("--mapping", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--partial",
        action="store_true",
        help="accept an annotation that does not yet cover every packet case",
    )
    return parser.parse_args()


def main() -> int:
    args = _args()
    packet_rows = build.read_jsonl(args.packet)
    annotation_rows = build.read_jsonl(args.annotation)
    mapping_rows = build.read_jsonl(args.mapping) if args.mapping else None
    try:
        rows = validate.validate_file(
            packet_rows,
            annotation_rows,
            mapping_rows=mapping_rows,
            require_complete=not args.partial,
        )
    except validate.AnnotationError as error:
        print(json.dumps({"valid": False, "error": str(error)}, ensure_ascii=False, indent=2))
        return 1
    rows = enrich_with_mapping(rows, mapping_rows)
    if args.output is not None:
        build.write_jsonl(args.output, rows)
    print(
        json.dumps(
            {
                "valid": True,
                "packet_cases": len(packet_rows),
                "annotated_cases": len(rows),
                "unblinded": mapping_rows is not None,
                "mapping_passthrough": (
                    []
                    if mapping_rows is None
                    else sorted(
                        {f for row in rows for f in MAPPING_PASSTHROUGH if f in row}
                    )
                ),
                "summary": validate.summarise(rows),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
