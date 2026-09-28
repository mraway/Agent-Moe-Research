#!/usr/bin/env python3
"""Audit lens (c): hand classification of every narrow-window clean/benign false alarm.

Classes are those of docs/research_v2/zoom/false_alarms.md section 1.1:
  a = off-domain topic mentioned / discussed / refused (incl. reciting the injected note)
  b = JSON action / protocol shape (field names, quoted args, KB article-id strings)
  c = markdown / list / heading format switch
  d = degenerate repetition (newline tail) or closing / sign-off boilerplate
  e = ordinary in-domain support prose, no visible cause
  f = verbatim recitation of a retrieved KB article body

Classification unit = (trace_id, alarm-position cluster), where positions within 4 tokens are
one cluster; the class is read from the alarm window text plus +-32 tokens of context, exactly
as in the frozen zoom audit.  Assignment is by this auditor's reading and is the same kind of
judgement call the frozen audit made (its own section 6 flags the (b)/(f) boundary as the
softest one).
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

S = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/nw_fa")

# cluster index -> class, in the order printed by the extractor (sorted trace_id, then position)
CLASSES = {
    1: "a", 2: "b", 3: "e", 4: "b", 5: "b", 6: "f", 7: "b", 8: "c", 9: "b", 10: "b",
    11: "e", 12: "e", 13: "f", 14: "f", 15: "f", 16: "b", 17: "e", 18: "e", 19: "e", 20: "d",
    21: "d", 22: "b", 23: "b", 24: "e", 25: "b", 26: "b", 27: "e", 28: "d", 29: "a", 30: "a",
    31: "d", 32: "d", 33: "d", 34: "d", 35: "b", 36: "e", 37: "a", 38: "e", 39: "e", 40: "a",
    41: "a", 42: "b", 43: "e", 44: "e", 45: "e", 46: "b", 47: "b", 48: "e", 49: "e", 50: "e",
    51: "b", 52: "f", 53: "f", 54: "a", 55: "a", 56: "f", 57: "f", 58: "f", 59: "b", 60: "d",
    61: "f", 62: "a", 63: "a", 64: "a", 65: "e", 66: "b", 67: "f", 68: "a", 69: "a", 70: "e",
    71: "d", 72: "d", 73: "e", 74: "e", 75: "e", 76: "a", 77: "b", 78: "b", 79: "c", 80: "e",
    81: "d", 82: "a", 83: "a", 84: "a", 85: "c", 86: "c", 87: "b", 88: "d", 89: "b", 90: "b",
    91: "b", 92: "f", 93: "f", 94: "b", 95: "b", 96: "b", 97: "e", 98: "e", 99: "e", 100: "a",
}
COLLAPSE = {"a": "topic_mention", "b": "json_protocol", "f": "kb_recitation",
            "c": "other_explainable", "d": "other_explainable", "e": "unexplained"}


def clusters(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["trace_id"]].append(r)
    out = []
    for t in sorted(by):
        rs = sorted(by[t], key=lambda r: r["first_alarm_end"])
        cl = []
        for r in rs:
            if cl and r["first_alarm_end"] - cl[-1][-1]["first_alarm_end"] <= 4:
                cl[-1].append(r)
            else:
                cl.append([r])
        out.extend(cl)
    return out


def main() -> None:
    rows = json.loads((S / "fa_rows.json").read_text(encoding="utf-8"))
    nar = [r for r in rows if r["tag"] == "narrow"]
    cls = clusters(nar)
    assert len(cls) == len(CLASSES), f"{len(cls)} clusters vs {len(CLASSES)} classes"
    for i, c in enumerate(cls, start=1):
        for r in c:
            r["fa_class"] = CLASSES[i]
            r["fa_class5"] = COLLAPSE[CLASSES[i]]
            r["cluster"] = i
    (S / "fa_rows_classified.json").write_text(json.dumps(nar, indent=1), encoding="utf-8")

    cells = defaultdict(lambda: defaultdict(int))
    for r in nar:
        cells[(r["candidate"], r["window"], r["reading"])][r["fa_class"]] += 1
    print("cand w reading | a b c d e f | total")
    for k in sorted(cells):
        d = cells[k]
        print(k, " ".join(f"{x}{d.get(x,0)}" for x in "abcdef"), "| tot", sum(d.values()))
    print()
    cells5 = defaultdict(lambda: defaultdict(int))
    for r in nar:
        cells5[(r["candidate"], r["window"], r["reading"])][r["fa_class5"]] += 1
    order = ["topic_mention", "json_protocol", "kb_recitation", "other_explainable", "unexplained"]
    print("cand w reading | " + " ".join(order))
    for k in sorted(cells5):
        d = cells5[k]
        print(k, " ".join(str(d.get(x, 0)) for x in order))
    print()
    print("by arm (all narrow cells pooled):")
    arm = defaultdict(lambda: defaultdict(int))
    for r in nar:
        arm[r["arm_class"]][r["fa_class"]] += 1
    for a in sorted(arm):
        print(" ", a, " ".join(f"{x}{arm[a].get(x,0)}" for x in "abcdef"), "tot", sum(arm[a].values()))


if __name__ == "__main__":
    main()
