"""Matched control group for critic E7: structured / list-like NON-code deliverables.

Step 1 of 3 (read-only diagnosis, post-hoc, development batches B1/B2).

Builds an EXPLICIT decoded-text structure score for every one of the 59 drift traces
over its product window ``product_onset .. min(product_onset+48, T)`` and dumps the
decoded window text so the rule can be audited by eye.

Nothing here is a detector: the window is anchored on the adjudicated ``product_onset``
label and the domain label is used for grouping.  No improvement is claimed.

Output: artifacts/agent_v2/research_v2/zoom_code_blindspot/structured_control/structure_scores.json
        artifacts/agent_v2/research_v2/zoom_code_blindspot/structured_control/windows.txt
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from research_v2 import io as rio

REPO = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
LABELS = REPO / "docs/research_v2/labels/product_onset_v1_adjudicated.jsonl"
OUT = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot/structured_control"
WINDOW_SPAN = 48

PROG_IDS = (
    "b1-f2-012", "b1-f2-014", "b1-f3-016",
    "b2-f2-011", "b2-f2-012", "b2-f2-014", "b2-f2-015", "b2-f3-020",
)
# depth_profile.md sec.6 / critic sec.1 E6: literal code inside the product window.
LITERAL_CODE_IDS = ("b1-f2-012", "b1-f2-014", "b1-f3-016", "b2-f2-011", "b2-f3-020")

BRACKETS = set("()[]{}<>")
COLONS = set(":")
# "list marker" = a token whose stripped text is exactly one of these, or that starts a
# numbered item ("1.", "2)", "3:") -- checked on the stripped token text.
LIST_MARKER_LITERALS = {"-", "*", "+", "•", "·", "–", "—", "#", "##", "###", "|", ">"}
NUMBERED = re.compile(r"^\d+[.):]$")


def token_structure_flags(tok: str) -> dict[str, bool]:
    """The five character/marker classes of the rule, evaluated on one decoded token."""
    core = tok.strip()
    return {
        "newline": "\n" in tok,
        "bracket": any(c in BRACKETS for c in tok),
        "digit": any(c.isdigit() for c in tok),
        "colon": any(c in COLONS for c in tok),
        "list_marker": (core in LIST_MARKER_LITERALS) or bool(NUMBERED.match(core)),
    }


def structure_score(texts: list[str]) -> dict:
    """RULE: structure_score = (# tokens carrying >=1 of the 5 classes) / (# tokens).

    Classes: newline, bracket ()[]{}<>, digit, colon, list marker.
    Per-class rates are reported alongside for auditability.
    """
    n = len(texts)
    counts = {"newline": 0, "bracket": 0, "digit": 0, "colon": 0, "list_marker": 0}
    any_hits = 0
    for tok in texts:
        f = token_structure_flags(tok)
        for k, v in f.items():
            counts[k] += int(v)
        any_hits += int(any(f.values()))
    return {
        "n_tokens": n,
        "structure_score": any_hits / n,
        "per_class": {k: v / n for k, v in counts.items()},
        "per_class_counts": counts,
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = {}
    for line in LABELS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["trace_id"]] = r
    batches = rio.load_core()
    out = []
    dump = []
    for bkey in ("b1", "b2"):
        for tr in batches[bkey]:
            if rio.arm_class(tr) != "drift":
                continue
            lab = rows[tr.trace_id]
            short = "-".join(tr.trace_id.split("-")[:3])
            onset = int(lab["product_onset"])
            T = int(tr.token_count)
            hi = min(onset + WINDOW_SPAN, T)
            ids = tr.token_ids.tolist()[onset:hi]
            texts = rio.decode_token_texts(ids)
            sc = structure_score(texts)
            rec = {
                "short": short, "trace_id": tr.trace_id, "batch": bkey,
                "domain": lab["domain"], "product_class": lab["product_class"],
                "product_onset": onset, "token_count": T, "window_hi": hi,
                "is_code": short in PROG_IDS, "is_literal_code": short in LITERAL_CODE_IDS,
                **sc,
            }
            out.append(rec)
            dump.append(
                f"=== {short} {lab['domain']} {lab['product_class']} onset={onset} "
                f"n={sc['n_tokens']} score={sc['structure_score']:.4f}\n"
                + "".join(texts).replace("\n", "\\n\n") + "\n"
            )
    out.sort(key=lambda r: -r["structure_score"])
    (OUT / "structure_scores.json").write_text(json.dumps(out, indent=2))
    (OUT / "windows.txt").write_text("\n".join(dump))
    for r in out:
        pc = r["per_class"]
        print(f"{r['short']:10s} {r['domain']:18s} {r['product_class']} "
              f"score={r['structure_score']:.4f} nl={pc['newline']:.3f} br={pc['bracket']:.3f} "
              f"dg={pc['digit']:.3f} co={pc['colon']:.3f} lm={pc['list_marker']:.3f} "
              f"{'CODE' if r['is_code'] else ''}")
    print("wrote", OUT / "structure_scores.json")


if __name__ == "__main__":
    main()
