"""Turn ``pre_sweep_filter_pass_lists.json`` into the ``args.consensus`` object the
``g-conf2-attack-annotation`` workflow accepts (so the LLM consensus step is skipped).

Usage: python scripts/research_v4/annotation_consensus_args.py <annotations_dir> > consensus_args.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    ann = Path(argv[1])
    lists = json.loads((ann / "pre_sweep_filter_pass_lists.json").read_text(encoding="utf-8"))
    summary = lists["summary"]
    if summary["n_disputed_without_adjudication"]:
        raise SystemExit(
            f"{summary['n_disputed_without_adjudication']} disputed cases still lack adjudication; "
            "finish adjudication and re-run annotation_consensus.py without --partial first"
        )
    batches = [
        {"bid": bid, "case_ids": b["true"], "case_ids_fp_false_by_quality": b["false_by_material_errors_only"]}
        for bid, b in sorted(lists["batches"].items())
    ]
    out = {
        "consensus": {
            "consensus_file": str(ann / "pre_sweep_consensus.jsonl"),
            "n_rows": summary["n_rows"],
            "n_filter_pass": summary["n_filter_pass"],
            "n_fp_false_by_quality_only": summary["n_false_by_material_errors_only"],
            "filter_pass_rule": summary["filter_pass_rule"],
            "batches": batches,
            "problems": "deterministic consensus (scripts/research_v4/annotation_consensus.py); no LLM",
        }
    }
    json.dump(out, sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
