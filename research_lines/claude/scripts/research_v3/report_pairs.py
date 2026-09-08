"""Paired comparisons, hit-set overlap and per-trace anchor reconstruction.

Read-only: it opens ONLY ``artifacts/agent_v2/research_v3/trm3/final_*/result.json``.
Every number it prints is either copied from that file or derived from it by the
frozen definitions (``trm3.paired_mcnemar`` is re-implemented verbatim below so
that this script needs no import of the frozen module).

Anchor reconstruction (used for the discordant-pair table): ``result.json`` stores,
per positive trace, the full ``alarm_ends`` list, and per recall block an ordered
``latencies`` list built as ``[b["latency"] for b in blocks.values() if b["hit_final"]]``
where ``blocks`` iterates the positives in a fixed order.  For a trace with
``hit_final`` the frozen definition gives ``latency = first_alarm_end - anchor``
and ``first_alarm_end = min(alarm_ends)``, so ``anchor = min(alarm_ends) - latency``.
Traces without ``hit_final`` (no alarm, or a pre-onset alarm) consume no latency.
The walk enumerates every assignment consistent with the recorded
``hit_plus_8`` flags and the recorded ``pre_onset_count``, then intersects the
candidate anchors of a trace across all variants / columns / alpha points.  A key
is reported only when a single anchor survives every constraint.
"""

from __future__ import annotations

import json
import math
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts" / "agent_v2" / "research_v3" / "trm3"
CELLS = {
    "b1": "final_b1_both",
    "b2": "final_b2_both",
    "h384": "final_h384_both",
    "c1_heldout": "final_c1_heldout_C1",
}
_CACHE: dict[str, dict] = {}


def load(cell: str) -> dict:
    if cell not in _CACHE:
        _CACHE[cell] = json.loads((BASE / CELLS[cell] / "result.json").read_text())
    return _CACHE[cell]


def mcnemar(hits_a: dict, hits_b: dict) -> dict:
    keys = sorted(set(hits_a) & set(hits_b))
    pairs = [(bool(hits_a[k]), bool(hits_b[k])) for k in keys]
    only_a = sum(1 for a, b in pairs if a and not b)
    only_b = sum(1 for a, b in pairs if b and not a)
    n = only_a + only_b
    if n == 0:
        p = 1.0
    else:
        smaller = min(only_a, only_b)
        tail = sum(math.comb(n, i) for i in range(smaller + 1)) / (2.0**n)
        p = min(1.0, 2.0 * tail)
    return {
        "pair_count": len(pairs),
        "both": sum(1 for a, b in pairs if a and b),
        "neither": sum(1 for a, b in pairs if not a and not b),
        "only_a": only_a,
        "only_b": only_b,
        "discordant": n,
        "net_gain_a_over_b": only_a - only_b,
        "p_value": float(p),
        "only_a_keys": [k for k in keys if hits_a[k] and not hits_b[k]],
        "only_b_keys": [k for k in keys if hits_b[k] and not hits_a[k]],
    }


def jaccard(hits_a: dict, hits_b: dict) -> float | None:
    a = {k for k, v in hits_a.items() if v}
    b = {k for k, v in hits_b.items() if v}
    union = a | b
    return None if not union else len(a & b) / len(union)


def block(cell: str, col: str, variant: str, set_name: str = "target") -> dict:
    return load(cell)["columns"][col]["variants"][variant]["sets"][set_name]


def matched_hits(cell: str, col: str) -> dict:
    """B-M hit map at the matched effective alpha (P1 primary column)."""
    m = load(cell)["columns"][col]["p1_matched_alpha"]["b_m_at_matched_alpha"]
    (only,) = m.values()
    return only["primary_event_hits_plus_8"] if "primary_event_hits_plus_8" in only else only


def summaries(cell: str, col: str, variant: str, set_name: str = "target") -> dict:
    return {s["key"]: s for s in block(cell, col, variant, set_name)["summaries"]}


def anchor_candidates(cell: str, col: str, variant: str, set_name: str = "target"):
    """All anchor assignments consistent with one recall block; see module docstring."""
    b = block(cell, col, variant, set_name)
    hits = b.get("primary_event_hits_plus_8") or {}
    if not hits:
        return {}
    recall = b["recall_strict"]
    lat = list(recall["latencies"])
    pre_total = recall["pre_onset_count"]
    summ = {s["key"]: s for s in b["summaries"]}
    keys = list(hits)
    out: dict[str, set[int]] = {k: set() for k in keys}
    solutions: list[dict[str, int | None]] = []

    def walk(i: int, j: int, pre: int, acc: dict[str, int | None]):
        if len(solutions) > 4096:
            return
        if i == len(keys):
            if j == len(lat) and pre == pre_total:
                solutions.append(dict(acc))
            return
        key = keys[i]
        ends = summ[key]["alarm_ends"]
        if not ends:
            if not hits[key]:
                acc[key] = None
                walk(i + 1, j, pre, acc)
                acc.pop(key)
            return
        # branch 1: this trace is a hit_final trace and consumes the next latency
        if j < len(lat) and bool(lat[j] <= 8) == bool(hits[key]):
            acc[key] = int(min(ends)) - int(lat[j])
            walk(i + 1, j + 1, pre, acc)
            acc.pop(key)
        # branch 2: this trace alarmed before its anchor (pre-onset), no latency
        if pre < pre_total and not hits[key]:
            acc[key] = None
            walk(i + 1, j, pre + 1, acc)
            acc.pop(key)

    walk(0, 0, 0, {})
    for sol in solutions:
        for k, v in sol.items():
            if v is not None:
                out[k].add(v)
    return {"solutions": len(solutions), "candidates": {k: sorted(v) for k, v in out.items()}}


def reconstruct_anchors(cell: str) -> dict[str, int]:
    """Intersect the anchor candidate sets over every variant / column of one cell."""
    d = load(cell)
    inter: dict[str, set[int]] = {}
    for col, cdata in d["columns"].items():
        for variant in cdata["variants"]:
            for set_name in cdata["variants"][variant]["sets"]:
                res = anchor_candidates(cell, col, variant, set_name)
                if not res or res["solutions"] == 0:
                    continue
                for key, cands in res["candidates"].items():
                    if not cands:
                        continue
                    cur = inter.get(key)
                    inter[key] = set(cands) if cur is None else (cur & set(cands))
    return {k: sorted(v)[0] for k, v in inter.items() if len(v) == 1}


def first_alarm_attribution(cell: str, col: str) -> dict[str, str]:
    """Channel that carried the first CONFIRMED endpoint of each trace (trm3 variant).

    ``outputs.jsonl`` holds the ``trm3`` variant only, which is exactly the (a)
    side of the P1 cell.
    """
    out: dict[str, str] = {}
    path = BASE / CELLS[cell] / "outputs.jsonl"
    with path.open() as handle:
        for line in handle:
            row = json.loads(line)
            if row["calibration_column"] != col or row["state"] != "CONFIRMED":
                continue
            out.setdefault(row["key"], row["attribution"])
    return out


def discordant(cell: str, col: str, matched: bool) -> list[dict]:
    anchors = reconstruct_anchors(cell)
    ha = block(cell, col, "trm3")["primary_event_hits_plus_8"]
    hb = matched_hits(cell, col) if matched else block(cell, col, "m_only")["primary_event_hits_plus_8"]
    sa = summaries(cell, col, "trm3")
    sb = summaries(cell, col, "m_only")
    attr = first_alarm_attribution(cell, col)
    rows = []
    for key in sorted(set(ha) & set(hb)):
        if bool(ha[key]) == bool(hb[key]):
            continue
        a, b = sa[key], sb[key]
        anchor = anchors.get(key)
        rows.append(
            {
                "key": key,
                "side": "only_trm3" if ha[key] else "only_b_m",
                "arm_class": a["arm_class"],
                "behaviour_class": a["behaviour_class"],
                "workflow": a["workflow"],
                "domain": a["domain"],
                "channel": a["channel"],
                "token_count": a["token_count"],
                "anchor": anchor,
                "trm3_first_alarm_end": a["first_alarm_end"],
                "trm3_latency": (
                    None if (anchor is None or a["first_alarm_end"] is None)
                    else a["first_alarm_end"] - anchor
                ),
                "trm3_channel": attr.get(key),
                "trm3_temporal_state": a["temporal_state"],
                "b_m_first_alarm_end": b["first_alarm_end"],
                "b_m_latency": (
                    None if (anchor is None or b["first_alarm_end"] is None)
                    else b["first_alarm_end"] - anchor
                ),
                "b_m_nominal_alarm": b["alarm"],
            }
        )
    return rows


def main() -> int:
    what = sys.argv[1]
    if what == "anchors":
        cell = sys.argv[2]
        json.dump(reconstruct_anchors(cell), sys.stdout, ensure_ascii=False, indent=1)
        return 0
    if what == "pairwise":
        cell, col = sys.argv[2], sys.argv[3]
        d = load(cell)["columns"][col]["variants"]
        names = [v for v in d if "primary_event_hits_plus_8" in d[v]["sets"]["target"]]
        rows = {}
        for a, b in combinations(names, 2):
            ha = block(cell, col, a)["primary_event_hits_plus_8"]
            hb = block(cell, col, b)["primary_event_hits_plus_8"]
            m = mcnemar(ha, hb)
            m["jaccard"] = jaccard(ha, hb)
            rows[f"{a}|{b}"] = m
        json.dump(rows, sys.stdout, ensure_ascii=False)
        return 0
    if what == "discordant":
        cell, col = sys.argv[2], sys.argv[3]
        matched = len(sys.argv) > 4 and sys.argv[4] == "matched"
        json.dump(discordant(cell, col, matched), sys.stdout, ensure_ascii=False, indent=1)
        return 0
    if what == "vs":
        cell, col, a, b = sys.argv[2:6]
        ha = block(cell, col, a)["primary_event_hits_plus_8"]
        hb = (
            matched_hits(cell, col)
            if b == "b_m_matched"
            else block(cell, col, b)["primary_event_hits_plus_8"]
        )
        m = mcnemar(ha, hb)
        m["jaccard"] = jaccard(ha, hb)
        json.dump(m, sys.stdout, ensure_ascii=False, indent=1)
        return 0
    print(f"unknown section {what!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
