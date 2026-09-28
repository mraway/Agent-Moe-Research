#!/usr/bin/env python3
"""Build the routing-blind annotation packet for one or more dataset G runs.

    packets_build.py --run artifacts/agent_v2/dataset_g/g_fit --subset g_fit \
        --packet-dir artifacts/agent_v2/dataset_g/packets \
        --private-dir artifacts/agent_v2/dataset_g/private

Writes ``<packet-dir>/<subset>/packet.jsonl`` (blind, shuffled) and
``<private-dir>/<subset>/case_mapping.jsonl`` (case_id -> trace).  The two are
never written into the same directory: the packet is what an annotator reads.
No model, no tokenizer, no routing tensor is touched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agent_v3.packets import build, schema  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, action="append")
    parser.add_argument("--subset", required=True, action="append")
    parser.add_argument("--packet-dir", type=Path, required=True)
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument(
        "--merge-runs",
        action="store_true",
        help=(
            "treat every --run as one group of a single subset (one --subset) and "
            "shuffle across all of them, so packet order cannot reveal the group"
        ),
    )
    parser.add_argument(
        "--hide-attack-metadata",
        action="store_true",
        help=(
            "attack-arm blindness: drop the tool result's injection_applied flag "
            "and enforce schema.ATTACK_FORBIDDEN_PACKET_KEYS as well"
        ),
    )
    parser.add_argument(
        "--redact-tool-result-keys",
        action="append",
        metavar="KEY",
        help=(
            "harness perturbation marker to strip out of the tool-result text, "
            "repeatable; ON by default with "
            + " ".join(build.DEFAULT_TOOL_RESULT_REDACT_KEYS)
            + ". These keys are written into the tool result by the injection "
            "harness, are read by the model, and are present in exactly the "
            "injected arms, so their presence alone separates the arms."
        ),
    )
    parser.add_argument(
        "--no-redact-tool-result-keys",
        action="store_true",
        help="turn the redaction off and rebuild the pre-redaction bytes",
    )
    parser.add_argument(
        "--redaction-style",
        choices=build.REDACTION_STYLES,
        default="remove",
        help=(
            "remove (default): drop the key/value pair, so neither the value nor "
            "the presence of the marker survives. placeholder: keep the key and "
            "write the literal "
            + build.REDACTION_PLACEHOLDER
            + " as its value -- this does NOT blind the packet, because the key "
            "is then present in exactly the injected rows"
        ),
    )
    return parser.parse_args()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    args = _args()
    if args.merge_runs:
        if len(args.subset) != 1:
            print("--merge-runs takes exactly one --subset", file=sys.stderr)
            return 2
        jobs = [(list(args.run), args.subset[0])]
    else:
        if len(args.run) != len(args.subset):
            print("--run and --subset must be given the same number of times", file=sys.stderr)
            return 2
        jobs = [([run], subset) for run, subset in zip(args.run, args.subset, strict=True)]
    if args.no_redact_tool_result_keys:
        if args.redact_tool_result_keys:
            print(
                "--redact-tool-result-keys and --no-redact-tool-result-keys conflict",
                file=sys.stderr,
            )
            return 2
        redact_keys: tuple[str, ...] = ()
    elif args.redact_tool_result_keys:
        redact_keys = tuple(args.redact_tool_result_keys)
    else:
        redact_keys = tuple(build.DEFAULT_TOOL_RESULT_REDACT_KEYS)
    report = {
        "packet_version": schema.PACKET_VERSION,
        "hide_attack_metadata": bool(args.hide_attack_metadata),
        "redact_tool_result_keys": list(redact_keys),
        "redaction_style": args.redaction_style if redact_keys else None,
        "subsets": {},
    }
    failed = False
    for run_roots, subset in jobs:
        packet_rows, mapping_rows = build.build_runs(
            run_roots,
            subset,
            hide_attack_metadata=args.hide_attack_metadata,
            redact_keys=redact_keys,
            redaction_style=args.redaction_style,
        )
        run_root = run_roots[0] if len(run_roots) == 1 else run_roots
        packet_path = args.packet_dir / subset / "packet.jsonl"
        mapping_path = args.private_dir / subset / "case_mapping.jsonl"
        build.write_jsonl(packet_path, packet_rows)
        build.write_jsonl(mapping_path, mapping_rows)
        traces = len({row["trace_id"] for row in mapping_rows})
        redaction = _redaction_report(
            packet_path, packet_rows, mapping_rows, redact_keys, args.redaction_style
        )
        failed = failed or not redaction["passed"]
        report["subsets"][subset] = {
            "run": str(run_root) if len(run_roots) == 1 else [str(r) for r in run_roots],
            "run_groups": sorted({str(row.get("run_group")) for row in mapping_rows}),
            "hide_attack_metadata": bool(args.hide_attack_metadata),
            "trace_count": traces,
            "episode_count": len(packet_rows),
            "packet": str(packet_path),
            "packet_sha256": _sha256(packet_path),
            "packet_bytes": packet_path.stat().st_size,
            "private_mapping": str(mapping_path),
            "private_mapping_sha256": _sha256(mapping_path),
            "redaction": redaction,
        }
        print(
            f"{subset}: {traces} traces -> {len(packet_rows)} blind cases "
            f"({packet_path})",
            flush=True,
        )
        print(
            f"{subset}: redaction {'PASS' if redaction['passed'] else 'FAIL'} "
            f"style={redaction['style']} keys={','.join(redaction['keys']) or '-'}; "
            f"{redaction['rows_redacted']} rows, {redaction['occurrences']} occurrences; "
            f"residual structural hits {len(redaction['residual_structural_hits'])}, "
            f"raw key substrings left in the file "
            f"{redaction['residual_raw_substrings']}",
            flush=True,
        )
    args.packet_dir.mkdir(parents=True, exist_ok=True)
    schema_path = args.packet_dir / "annotation_schema.json"
    rendered = (
        json.dumps(
            _annotation_schema(redact_keys, args.redaction_style), indent=2, ensure_ascii=False
        )
        + "\n"
    )
    if not schema_path.exists() or schema_path.read_text(encoding="utf-8") != rendered:
        schema_path.write_text(rendered, encoding="utf-8")
    out = args.packet_dir / (
        f"packet_build_report_{'_'.join(args.subset)}.json"
        if args.merge_runs
        else "packet_build_report.json"
    )
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if failed:
        print("redaction self-check FAILED; see the build report", file=sys.stderr)
        return 3
    return 0


def _redaction_report(
    packet_path: Path,
    packet_rows: list[dict],
    mapping_rows: list[dict],
    keys: tuple[str, ...],
    style: str,
) -> dict:
    """Self-check the written packet: what was taken out and what is left.

    ``residual_structural_hits`` re-walks the parsed rows for the redacted key
    names outside the protected (model-output) fields; with the default
    ``remove`` style it must be empty.  ``residual_raw_substrings`` is the plain
    ``grep -c`` of the key name over the whole file, protected fields included,
    so a word the *model* wrote is visible rather than hidden.
    """

    residual: list[str] = []
    for row in packet_rows:
        for path in build.residual_key_hits(row, keys):
            residual.append(f"{row['case_id']}: {path}")
    text = packet_path.read_text(encoding="utf-8")
    rows_redacted = sum(1 for row in mapping_rows if row.get("redactions"))
    occurrences = sum(
        record["count"] for row in mapping_rows for record in row.get("redactions") or ()
    )
    by_key: dict[str, int] = {}
    for row in mapping_rows:
        for record in row.get("redactions") or ():
            by_key[str(record["key"])] = by_key.get(str(record["key"]), 0) + record["count"]
    return {
        "keys": list(keys),
        "style": style if keys else None,
        "rows_redacted": rows_redacted,
        "occurrences": occurrences,
        "occurrences_by_key": dict(sorted(by_key.items())),
        "residual_structural_hits": residual[:20],
        "residual_structural_hit_count": len(residual),
        "residual_raw_substrings": {key: text.count(key) for key in keys},
        "passed": (not keys) or (style == "placeholder") or not residual,
    }


def _annotation_schema(redact_keys: tuple[str, ...] = (), style: str = "remove") -> dict:
    """The field schema a reviewer file must satisfy, straight from the module."""

    return {
        "annotation_version": schema.ANNOTATION_VERSION,
        "packet_redactions": {
            "keys": list(redact_keys),
            "style": style if redact_keys else None,
            "note": (
                "harness-written bookkeeping inside a tool result (an 'this result "
                "was modified' flag) is removed from every packet row before it is "
                "written. Nothing the model read or wrote is otherwise altered: the "
                "tool-result facts, any external note, the system prompt and every "
                "channel text and token array are verbatim."
            ),
        },
        "protocol": [
            "docs/agent_v2_onset_reliability_audit_plan.md sections 3, 4, 6",
            "docs/research_v4/agent_v3_dataset_design.md sections 2.3, 4, 15.1",
        ],
        "one_row_per": "packet case_id (= one episode = one user turn)",
        "required_fields": list(schema.ANNOTATION_REQUIRED_FIELDS),
        "optional_fields": list(schema.ANNOTATION_OPTIONAL_FIELDS),
        "trajectory_class": list(schema.TRAJECTORY_CLASSES),
        "events": {
            "keys": list(schema.EVENT_KEYS),
            "value": "null, or an object with " + ", ".join(schema.EVENT_REQUIRED_FIELDS),
            "channel_pinning": schema.EVENT_PINNED_CHANNEL,
            "evidence": (
                "an exact, unique substring of channel_text[<channel>]; the validator "
                "aligns it to a character span and to a span on the episode's global "
                "generated-token axis"
            ),
            "ordering": "earliest E <= C <= X on the global token axis; X implies C",
            "onset_interval": schema.ONSET_INTERVAL_DOC,
        },
        "recovery_spans": {
            "fields": list(schema.RECOVERY_REQUIRED_FIELDS),
            "note": "annotated for every E sample, not only after X (design section 4)",
        },
        "over_refusal_sub_label": {
            "field": schema.OVER_REFUSAL_SUBLABEL,
            "rule": "required iff trajectory_class == over_refusal; true means no E evidence",
        },
        "quality": {
            "fields": list(schema.QUALITY_FIELDS),
            "behavior": list(schema.BEHAVIOR),
            "engagement": list(schema.ENGAGEMENT),
            "coverage": list(schema.COVERAGE),
            "citation": list(schema.CITATION),
            "list_fields": list(schema.QUALITY_LIST_FIELDS),
        },
        "confidence": list(schema.CONFIDENCE),
        "derived_by_the_validator_do_not_supply": [
            "e_view", "analysis_only_engagement", "analysis_only_engagement_events",
            "filter_pass", "silent", "x_tool_only",
            "quality.unauthorized_tool_attempt", "every span", "interval_span",
        ],
        "canonicalisation_for_research_v2.io_g": {
            "behavior": schema.BEHAVIOR_CANONICAL,
            "coverage": schema.COVERAGE_CANONICAL,
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
