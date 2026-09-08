"""Annotator B helper: dump the final model_generation decode tokens of a trace.

Usage:
    python scripts/research_v2/labels/dump_decode_tokens_B.py <pair_group_id> [batch]

Prints one line per decode token as "idx: <repr of token text>", so that
`product_onset` can be read off in the same 0-based coordinate system as
`evidence_onset`.  Read-only; touches nothing but trace files.
"""

from __future__ import annotations

import sys
from pathlib import Path

from phase_a.routing_analysis import load_final_generation_sequence

ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    pair_group_id = sys.argv[1]
    batch = sys.argv[2] if len(sys.argv) > 2 else pair_group_id.split("-")[0]
    trace_dir = ROOT / "artifacts" / "agent_v2" / f"agent_v2_5_{batch}" / pair_group_id / "attack"
    seq = load_final_generation_sequence(trace_dir)
    print(f"# {pair_group_id} batch={batch} n_tokens={len(seq.token_texts)}")
    for idx, text in enumerate(seq.token_texts):
        print(f"{idx}: {text!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
