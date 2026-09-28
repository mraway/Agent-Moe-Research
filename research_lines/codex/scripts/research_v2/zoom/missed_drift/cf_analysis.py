#!/usr/bin/env python3
"""Per-miss recovery accounting for the counterfactual grid."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT  # noqa: E402
from research_v2 import io as rio  # noqa: E402

BASE = {"CAND-A": "A0_base_5_15_w8", "CAND-B": "B0_base_5_11_w4"}


def hit16(row, onset):
    # trace_alarms rows: [trace_id, weight, first_alarm_end, tolerant_first, onset_count]
    first = row[2]
    return first is not None and onset <= first <= onset + 16


def main() -> None:
    cf = json.loads((OUT / "counterfactuals.json").read_text())
    misses = json.loads((OUT / "misses.json").read_text())
    batches = rio.load_core()
    onset = {t.trace_id: t.evidence_onset for b in batches.values() for t in b if t.positive}
    drift_ids = {
        case: sorted(
            t.trace_id
            for t in batches["b2" if case == "b1_to_b2" else "b1"]
            if t.positive
        )
        for case in ("b1_to_b2", "b2_to_b1")
    }
    hits = {}
    for name, block in cf.items():
        hits[name] = {}
        for case, b in block["cases"].items():
            s = set()
            for row in b["trace_alarms"]:
                tid = row[0]
                if tid in onset and hit16(row, onset[tid]):
                    s.add(tid)
            hits[name][case] = s

    out = {}
    for fam, base in BASE.items():
        out[fam] = {}
        base_miss = {
            case: [m["trace_id"] for m in misses[fam][case]["misses"]]
            for case in ("b1_to_b2", "b2_to_b1")
        }
        prefix = "A" if fam == "CAND-A" else "B"
        for name in cf:
            if not name.startswith(prefix) or name == base:
                continue
            rec = {}
            for case in ("b1_to_b2", "b2_to_b1"):
                recovered = sorted(set(base_miss[case]) & hits[name][case])
                lost = sorted(hits[base][case] - hits[name][case])
                b0 = cf[base]["cases"][case]
                bn = cf[name]["cases"][case]
                n_nd = {"b1_to_b2": 205, "b2_to_b1": 96}[case]
                rec[case] = {
                    "recovered": recovered,
                    "n_recovered": len(recovered),
                    "lost": lost,
                    "n_lost": len(lost),
                    "delta_far": round(bn["far_all"] - b0["far_all"], 4),
                    "delta_fa_traces": round((bn["far_all"] - b0["far_all"]) * n_nd, 1),
                    "far": bn["far_all"],
                    "r16": bn["r16"],
                    "r8": bn["r8"],
                }
            tot_rec = sum(rec[c]["n_recovered"] for c in rec)
            tot_lost = sum(rec[c]["n_lost"] for c in rec)
            tot_fa = sum(rec[c]["delta_fa_traces"] for c in rec)
            rec["net_recovered"] = tot_rec - tot_lost
            rec["added_false_alarm_traces"] = round(tot_fa, 1)
            rec["efficiency"] = (
                round((tot_rec - tot_lost) / tot_fa, 2) if tot_fa > 0 else None
            )
            out[fam][name] = rec
    (OUT / "cf_recovery.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    for fam, block in out.items():
        print("#####", fam)
        for name, rec in block.items():
            print(
                f"{name:24s} rec={sum(rec[c]['n_recovered'] for c in ('b1_to_b2','b2_to_b1'))} "
                f"lost={sum(rec[c]['n_lost'] for c in ('b1_to_b2','b2_to_b1'))} "
                f"net={rec['net_recovered']:+d} dFA={rec['added_false_alarm_traces']:+.1f} "
                f"eff={rec['efficiency']}"
            )
            for c in ("b1_to_b2", "b2_to_b1"):
                print(f"   {c}: FAR={rec[c]['far']:.3f} R8={rec[c]['r8']:.3f} R16={rec[c]['r16']:.3f} "
                      f"rec={[t.split('--')[0][-28:] for t in rec[c]['recovered']]} "
                      f"lost={[t.split('--')[0][-28:] for t in rec[c]['lost']]}")


if __name__ == "__main__":
    main()
