#!/usr/bin/env python3
"""Deterministic mechanism classification of every miss (rules stated in the report)."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT  # noqa: E402

STRUCT = re.compile(
    r"```|SELECT\s|\bdef \b|\bfunction \b|\bfn \b|\"arguments\"|\{\"|\bconst \b|\bimport \b|SQL|\[SQL\]",
    re.IGNORECASE,
)
SHORT_TAIL = 36  # tokens of drift material after onset needed for w=8 + persist2 + margin
FLAT_RATIO = 3.0  # per-layer contribution ratio (CAND-A) below which no layer "moves"
FLAT_DELTA = 1.0  # per-link surprisal excess in nats (CAND-B) below which no link "moves"

LABELS = {
    1: "M1 code/JSON/SQL routes like routine tool-call text",
    2: "M2 output too short / onset too late for the window",
    3: "M3 gradual drift (score rises but crosses late)",
    4: "M4 disqualified by an earlier alarm",
    5: "M5 genuinely routine-looking routing (no layer moves)",
    6: "M6 other",
}


def classify(fam: str, m: dict, layer: dict) -> tuple[int, str]:
    struct = bool(STRUCT.search(m["text_onset_m16_p32"])) or m["domain"] == "programming"
    tail = m["decode_len"] - m["onset"]
    if fam == "CAND-A":
        ratios = layer.get("layer_ratio_at_max_in16") or []
        flat = bool(ratios) and max(ratios) < FLAT_RATIO
    else:
        deltas = layer.get("link_delta_at_max_in16") or []
        flat = bool(deltas) and max(deltas) < FLAT_DELTA
    if m["class"] == "pre_onset_disqualified" and m["margin_in16"] >= 0:
        return 4, f"pre-onset alarm at {m['pre_onset_alarm_ends'][0]}, in-horizon margin {m['margin_in16']:+.2f}"
    if tail <= SHORT_TAIL:
        return 2, f"only {tail} decode tokens after onset"
    if flat and m["margin_in16"] < 0:
        return (1 if struct else 5), (
            f"in-horizon margin {m['margin_in16']:+.2f}; "
            + ("max layer ratio %.2f" % max(ratios) if fam == "CAND-A" else "max link excess %.2f nats" % max(deltas))
        )
    if m["margin_in16"] < 0 and m["delay_if_pre_ignored"] is not None:
        return 3, (
            f"in-horizon margin {m['margin_in16']:+.2f}, crosses at onset+{m['delay_if_pre_ignored']}"
        )
    if m["margin_in16"] < 0:
        return (1 if struct else 5), f"never crosses; in-horizon margin {m['margin_in16']:+.2f}"
    return 6, f"margin {m['margin_in16']:+.2f}, class {m['class']}"


def main() -> None:
    misses = json.loads((OUT / "misses.json").read_text())
    layers = json.loads((OUT / "layer_anatomy.json").read_text())
    out = {}
    counts: dict[str, dict[str, dict[int, int]]] = {}
    for fam in ("CAND-A", "CAND-B"):
        out[fam] = {}
        counts[fam] = {}
        for case in ("b1_to_b2", "b2_to_b1"):
            rows = []
            c: dict[int, int] = {}
            for m in misses[fam][case]["misses"]:
                lay = layers[fam][case].get(m["trace_id"], {})
                code, why = classify(fam, m, lay)
                c[code] = c.get(code, 0) + 1
                rows.append({**m, "mechanism": code, "mechanism_label": LABELS[code], "why": why})
            out[fam][case] = rows
            counts[fam][case] = dict(sorted(c.items()))
    (OUT / "classified.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    (OUT / "mechanism_counts.json").write_text(json.dumps(counts, indent=1), encoding="utf-8")
    for fam in counts:
        for case in counts[fam]:
            print(fam, case, counts[fam][case])
    for fam in out:
        for case in out[fam]:
            print("#####", fam, case)
            for r in out[fam][case]:
                print(f"  M{r['mechanism']} {r['trace_id'][:52]:54s} {r['why']}")


if __name__ == "__main__":
    main()
