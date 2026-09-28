"""Dump the decoded token sequence of a drift trace's final model_generation.

Read-only tooling for the ``product_onset`` second-pass annotation
(``docs/research_v2/labels/product_onset_rules.md``).  It prints one line per
decode token as ``idx: <repr of token text>`` so an annotator can locate the
first token of the out-of-domain deliverable itself (P1-P4) and, optionally,
the announcement sentence that precedes it.

The token index is 0-based into the final ``model_generation`` decode sequence,
i.e. the same coordinate system as ``evidence_onset`` /
``completion_boundary`` in ``docs/research_v2/labels/drift_trace_list.jsonl``.

Usage
-----
    PYTHONPATH=src:scripts python scripts/research_v2/labels/dump_decode_tokens.py \
        --batch b2 --pair-group b2-f0-003-support_case_status-free-verse

    # or drive the whole frozen list
    PYTHONPATH=src:scripts python scripts/research_v2/labels/dump_decode_tokens.py --all

Nothing is written; this script only reads ``trace.json`` / decode manifests
under ``artifacts/agent_v2/agent_v2_5_<batch>/<pair_group_id>/attack``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from phase_a.routing_analysis import load_final_generation_sequence

REPO_ROOT = Path(__file__).resolve().parents[3]
TRACE_LIST = REPO_ROOT / "docs/research_v2/labels/drift_trace_list.jsonl"


def trace_dir(batch: str, pair_group_id: str) -> Path:
    return (
        REPO_ROOT
        / "artifacts/agent_v2"
        / f"agent_v2_5_{batch}"
        / pair_group_id
        / "attack"
    )


def load_rows() -> list[dict]:
    return [
        json.loads(line)
        for line in TRACE_LIST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def token_texts(batch: str, pair_group_id: str) -> tuple[str, ...]:
    return load_final_generation_sequence(trace_dir(batch, pair_group_id)).token_texts


def dump(row: dict, *, joined: bool = False) -> None:
    texts = token_texts(row["batch"], row["pair_group_id"])
    print("=" * 88)
    print(
        f"trace_id={row['trace_id']} domain={row.get('domain')} "
        f"channel={row.get('channel')} n_tokens={len(texts)} "
        f"evidence_onset={row.get('evidence_onset')} "
        f"completion_boundary={row.get('completion_boundary')}"
    )
    print("-" * 88)
    if joined:
        print("".join(texts))
        print("-" * 88)
    for idx, text in enumerate(texts):
        print(f"{idx}: {text!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", choices=["b1", "b2"])
    parser.add_argument("--pair-group")
    parser.add_argument("--trace-id")
    parser.add_argument("--all", action="store_true", help="dump every trace in the frozen list")
    parser.add_argument("--joined", action="store_true", help="also print the full decoded text")
    args = parser.parse_args()

    if args.all or args.trace_id:
        rows = load_rows()
        if args.trace_id:
            rows = [r for r in rows if r["trace_id"] == args.trace_id]
            if not rows:
                raise SystemExit(f"trace_id not in {TRACE_LIST}: {args.trace_id}")
        for row in rows:
            dump(row, joined=args.joined)
        return

    if not (args.batch and args.pair_group):
        raise SystemExit("give --all, --trace-id, or both --batch and --pair-group")
    dump(
        {
            "batch": args.batch,
            "pair_group_id": args.pair_group,
            "trace_id": args.pair_group + "--attack",
            "domain": None,
            "channel": None,
            "evidence_onset": None,
            "completion_boundary": None,
        },
        joined=args.joined,
    )


if __name__ == "__main__":
    main()
