#!/usr/bin/env python3
"""Audit lens (c): write the full per-row false-alarm evidence table for the narrow-window runs."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
OUT = ROOT / "artifacts/agent_v2/research_v2/narrow_window/audit_false_alarm_anatomy"
S = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/nw_fa")


def esc(s: str) -> str:
    return repr(s)[1:-1].replace("|", "\\|")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    nar = json.loads((S / "fa_rows_classified.json").read_text(encoding="utf-8"))
    (OUT / "fa_rows_classified.json").write_text(json.dumps(nar, indent=1), encoding="utf-8")
    nar.sort(key=lambda r: (r["candidate"], r["window"], r["reading"], r["case"], r["trace_id"]))
    L = ["# Narrow-window false-alarm rows (clean + benign_control, mode D, alpha 0.10)", "",
         "Every clean / benign_control trace with a first alarm, in every (candidate, window, "
         "reading) cell of the two narrow-window runs.  `first` = stored `trace_alarms` "
         "first_alarm_end (the harness decision).  `window text` = the w decoded tokens ending at "
         "`first`; `tail8` = the 8 tokens ending at `first`, for cross-window comparability.  "
         "`cal half` = the half whose conformal threshold judged this trace.  Classes a-f are "
         "those of docs/research_v2/zoom/false_alarms.md section 1.1.", "",
         "| cand | w | reading | dir | trace | arm | len | first | rel | cal half | class | "
         "window text | tail8 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in nar:
        L.append(
            f"| {r['candidate']} | {r['window']} | {r['reading']} | {r['case']} | "
            f"`{r['trace_id']}` | {r['arm_class']} | {r['decode_len']} | {r['first_alarm_end']} | "
            f"{r['rel_pos']:.3f} | {r['cal_half']} | {r['fa_class']} | "
            f"`{esc(r['window_text'])}` | `{esc(r['tail8'])}` |"
        )
    (OUT / "fa_table.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("wrote", OUT / "fa_table.md", len(nar), "rows")


if __name__ == "__main__":
    main()
