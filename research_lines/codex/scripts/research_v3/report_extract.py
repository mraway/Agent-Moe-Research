"""Read-only extractor for the TRM-3 experiment report.

Reads ONLY ``artifacts/agent_v2/research_v3/trm3/final_*/result.json`` (and, with
``--outputs``, the matching ``outputs.jsonl``).  It never touches traces, labels,
prereg files or any frozen source; it computes nothing new beyond regrouping and
plain arithmetic on numbers the frozen runner already wrote.

Usage:
    PYTHONPATH=src:scripts python scripts/research_v3/report_extract.py <section> [args]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts" / "agent_v2" / "research_v3" / "trm3"
CELLS = {
    "b1": "final_b1_both",
    "b2": "final_b2_both",
    "h384": "final_h384_both",
    "c1_heldout": "final_c1_heldout_C1",
}


def load(cell: str) -> dict:
    return json.loads((BASE / CELLS[cell] / "result.json").read_text())


def fmt(x, nd=3):
    if x is None:
        return "-"
    if isinstance(x, bool):
        return "yes" if x else "NO"
    if isinstance(x, float):
        return f"{x:.{nd}f}"
    return str(x)


def iter_blocks(cell: str):
    """(cell, column, variant, set_name, block)."""
    d = load(cell)
    for col, cdata in d["columns"].items():
        for variant, vdata in cdata["variants"].items():
            for set_name, block in vdata["sets"].items():
                yield cell, col, variant, set_name, vdata, block


def main() -> int:
    what = sys.argv[1] if len(sys.argv) > 1 else "dump"
    if what == "dump":
        out = {}
        for cell in CELLS:
            d = load(cell)
            c = out[cell] = {
                "target": d["target"],
                "code_commit": d["code_commit"],
                "dirty": d["dirty"],
                "data_discipline": d["data_discipline"],
                "prereg": d["prereg"],
                "inputs_sha256": d["inputs_sha256"],
                "label_counts": d["label_counts"],
                "anchor_policy": d["anchor_policy"],
                "spontaneous_drift_group": d["spontaneous_drift_group"],
                "g5": d.get("g5"),
                "columns": {},
            }
            for col, cdata in d["columns"].items():
                cc = c["columns"][col] = {
                    "pools": cdata["pools"],
                    "channel_fit_seconds": cdata["channel_fit_seconds"],
                    "g8_reference": cdata["g8_reference"]["frozen_cand_a"],
                    "gates": cdata["gates"],
                    "mcnemar": cdata["mcnemar"],
                    "p1_matched_alpha": {
                        k: v for k, v in cdata["p1_matched_alpha"].items()
                        if k != "b_m_at_matched_alpha"
                    },
                    "variants": {},
                }
                for variant, vdata in cdata["variants"].items():
                    vv = cc["variants"][variant] = {
                        "alpha_budget": {
                            k: v for k, v in vdata["alpha_budget"].items()
                            if k in ("alpha_eff", "alpha_eff_min", "alpha_matched", "by_half")
                        },
                        "cost": vdata["cost"],
                        "calibration_version": vdata["calibration"]["version"],
                        "k_cal": vdata["calibration"]["k_cal"],
                        "regime": vdata["calibration"].get("regime"),
                        "sets": {},
                    }
                    for set_name, block in vdata["sets"].items():
                        vv["sets"][set_name] = {
                            k: block[k] for k in (
                                "trace_count", "far", "far_by_calibration_half",
                                "far_half_gap", "endpoint", "horizon",
                                "silent_alarm_rate", "silent_count",
                                "positive_set", "recall_strict", "recall_tolerant",
                                "recall_strict_secondary", "recall_tolerant_secondary",
                                "spontaneous_drift", "temporal", "worst_group",
                                "attribution", "alpha_grid",
                            ) if k in block
                        }
                        vv["sets"][set_name]["positive_set"] = {
                            k: v for k, v in (block.get("positive_set") or {}).items()
                            if k != "anchored_keys"
                        }
        json.dump(out, sys.stdout, ensure_ascii=False)
        return 0
    if what == "summaries":
        cell, col, variant, set_name = sys.argv[2:6]
        d = load(cell)
        block = d["columns"][col]["variants"][variant]["sets"][set_name]
        json.dump(block["summaries"], sys.stdout, ensure_ascii=False)
        return 0
    if what == "hits":
        cell, col, variant, set_name = sys.argv[2:6]
        d = load(cell)
        block = d["columns"][col]["variants"][variant]["sets"][set_name]
        json.dump(
            {
                "primary": block["primary_event_hits_plus_8"],
                "secondary": block.get("primary_event_hits_plus_8_secondary_anchor"),
                "matched": (
                    d["columns"][col]["p1_matched_alpha"]
                    .get("b_m_at_matched_alpha", {})
                ),
            },
            sys.stdout,
            ensure_ascii=False,
        )
        return 0
    print(f"unknown section {what!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
