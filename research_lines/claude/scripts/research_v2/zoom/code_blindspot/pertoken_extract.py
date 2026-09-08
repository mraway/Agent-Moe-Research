#!/usr/bin/env python3
"""LENS = per-token score stream on the code traces (zoom / code blindspot).

READ-ONLY diagnostic.  Re-reads the SAVED score streams of the narrow-window runs
(w=1,2,4,8 for CAND-A; w=1,2,4 for CAND-B) and reconstructs the harness mode-D
decision exactly via scripts/research_v2/zoom/missed_drift/common.mode_d_rows
(position-bucket standardization + two-half conformal thresholds).  No scorer is
re-run, no model is loaded, nothing outside
artifacts/agent_v2/research_v2/zoom_code_blindspot/ is written.

Output cache: artifacts/agent_v2/research_v2/zoom_code_blindspot/pertoken.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import torch

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "research_v2" / "zoom" / "missed_drift"))

from common import cases_for, load_result, mode_d_rows, streams_from_result  # noqa: E402
from research_v2 import io as rio  # noqa: E402

torch.set_num_threads(8)

NW = ROOT / "artifacts" / "agent_v2" / "research_v2" / "narrow_window"
OUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
LABELS = ROOT / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"

RUNS = {
    "CAND-A": (NW / "wgm_c2_w1248" / "result.json", [1, 2, 4, 8]),
    "CAND-B": (NW / "pdm_c12_w124" / "result.json", [1, 2, 4]),
}
ALPHA = 0.10

# ---------------------------------------------------------------- token classes
PY_KW = {
    "def", "return", "for", "if", "else", "elif", "while", "in", "import", "from",
    "class", "try", "except", "with", "as", "lambda", "not", "and", "or", "is",
    "None", "True", "False", "pass", "raise", "yield", "assert", "global",
}
SQL_KW = {
    "select", "from", "where", "group", "order", "by", "having", "join", "inner",
    "left", "right", "outer", "on", "as", "count", "sum", "avg", "min", "max",
    "insert", "into", "values", "update", "set", "delete", "limit", "distinct",
    "and", "or", "not", "null", "case", "when", "then", "end", "asc", "desc",
    "create", "table", "union", "all", "between", "like", "exists", "in",
}
JS_KW = {
    "function", "const", "let", "var", "return", "for", "of", "if", "else", "new",
    "this", "class", "export", "import", "async", "await", "null", "undefined",
    "typeof", "while", "break", "continue",
}
RS_KW = {
    "fn", "let", "mut", "pub", "use", "struct", "impl", "match", "for", "in",
    "if", "else", "return", "u32", "i32", "usize", "Vec", "String", "self",
    "crate", "mod", "enum", "trait", "where", "loop", "while",
}
KEYWORDS = {w.lower() for w in (PY_KW | SQL_KW | JS_KW | RS_KW)}
SYMBOL_RE = re.compile(r"^[^\w\s]+$")
NUMBER_RE = re.compile(r"^\d+(\.\d+)?$")
WORD_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# line looks like code even without a fence (the fence-less [SQL] trace)
CODE_LINE_RE = re.compile(
    r"^\s*("
    r"(SELECT|FROM|WHERE|GROUP|ORDER|HAVING|JOIN|INNER|LEFT|RIGHT|ON|INSERT|UPDATE|"
    r"DELETE|VALUES|LIMIT|UNION|CREATE|SET|AND|OR)\b"
    r"|(def|class|return|for|if|elif|else|while|import|from|try|except|with|print)\b"
    r"|(function|const|let|var|new|export)\b"
    r"|(fn|pub|use|struct|impl|match)\b"
    r"|[)\]}]"
    r")",
    re.IGNORECASE,
)
FENCE_TEXT = re.compile(r"`{3,}")
MARKER_TEXT = re.compile(r"\[(SQL|SCRIPT|RUST|CODE|PYTHON|JS|JAVASCRIPT)\]")


def token_pieces(trace) -> list[str]:
    tokzr = rio.load_tokenizer()
    out = []
    for tid in trace.token_ids.tolist():
        piece = tokzr.id_to_token(int(tid))
        out.append("" if piece is None else piece.replace("Ġ", " ").replace("Ċ", "\n"))
    return out


def code_regions(pieces: list[str]) -> tuple[list[bool], list[int]]:
    """Per-token in_code flag + indices of fence/marker tokens.

    Rule (fixed before looking at any score):
      * fence tokens = any token overlapping a ``` run or a ``[SQL]``/``[RUST]``/``[SCRIPT]``/
        ``[CODE]``/``[PYTHON]``/``[JS]``/``[JAVASCRIPT]`` marker in the DECODED text (the
        marker is split across several tokens, so it is matched on the text, not per token);
      * a physical line is a code line if it matches CODE_LINE_RE, or if it is indented by
        >= 4 spaces and either the previous line was a code line or a bracket marker has
        already been seen (indented continuation, e.g. the fence-less ``[SQL]`` trace);
      * a token is IN CODE if it sits inside a ``` ... ``` span or on a code line;
      * fence/marker tokens themselves are never counted as in-code.
    """
    text_pos = []
    buf = ""
    for p in pieces:
        text_pos.append(len(buf))
        buf += p
    lines = buf.split("\n")
    line_start = []
    pos = 0
    for ln in lines:
        line_start.append(pos)
        pos += len(ln) + 1

    def line_of(ch: int) -> int:
        lo, hi = 0, len(lines) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if line_start[mid] <= ch:
                lo = mid
            else:
                hi = mid - 1
        return lo

    # --- fence / marker tokens, matched on the decoded text
    fence_spans = [(m.start(), m.end()) for m in FENCE_TEXT.finditer(buf)]
    marker_spans = [(m.start(), m.end()) for m in MARKER_TEXT.finditer(buf)]
    fence_tok: list[int] = []
    for i, p in enumerate(pieces):
        a, b = text_pos[i], text_pos[i] + len(p)
        if any(a < e and b > s0 for s0, e in fence_spans + marker_spans):
            fence_tok.append(i)
    fence_set = set(fence_tok)

    # --- inside ``` ... ``` (toggle on every ``` run)
    in_fence_at_char = [False] * (len(buf) + 1)
    state = False
    prev_end = 0
    for s0, e in fence_spans:
        for c in range(prev_end, s0):
            in_fence_at_char[c] = state
        state = not state
        prev_end = e
    for c in range(prev_end, len(buf) + 1):
        in_fence_at_char[c] = state

    marker_line = min((line_of(s0) for s0, _ in marker_spans), default=None)

    # --- code lines
    code_line = [False] * len(lines)
    for j, ln in enumerate(lines):
        if not ln.strip():
            continue
        if CODE_LINE_RE.match(ln):
            code_line[j] = True
            continue
        if ln.startswith("    "):
            prev = code_line[j - 1] if j else False
            if prev or (marker_line is not None and j > marker_line):
                code_line[j] = True

    in_code = []
    for i, p in enumerate(pieces):
        if i in fence_set:
            in_code.append(False)
            continue
        j = line_of(text_pos[i])
        in_code.append(bool(in_fence_at_char[text_pos[i]] or code_line[j]))
    return in_code, fence_tok


def classify(pieces: list[str], in_code: list[bool], fence_tok: list[int]) -> list[str]:
    fset = set(fence_tok)
    out = []
    for i, p in enumerate(pieces):
        s = p.strip()
        if i in fset:
            out.append("fence")
        elif "\n" in p:
            out.append("newline")
        elif s == "":
            out.append("space")
        elif SYMBOL_RE.match(s):
            out.append("symbol")
        elif NUMBER_RE.match(s):
            out.append("number")
        elif s.lower() in KEYWORDS and in_code[i]:
            out.append("keyword")
        elif in_code[i]:
            out.append("identifier")
        elif WORD_RE.match(s) or s.isalpha():
            out.append("prose")
        else:
            out.append("other")
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    batches = rio.load_core()
    traces = {t.trace_id: t for v in batches.values() for t in v}
    labels = {
        json.loads(line)["trace_id"]: json.loads(line)
        for line in LABELS.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    cases = cases_for(batches, "S1")

    payload: dict = {"alpha": ALPHA, "runs": {}}
    for cand, (path, widths) in RUNS.items():
        result = load_result(path)
        by_key = {(cr["case"], cr["window_width"]): cr for cr in result["case_runs"]}
        payload["runs"][cand] = {}
        for w in widths:
            per_trace = {}
            for case_name, case in cases.items():
                cr = by_key[(case_name, w)]
                streams = streams_from_result(cr)
                _rows, detail = mode_d_rows(
                    case, streams, routine_def="cb", reading_name="persist2", alpha=ALPHA
                )
                for tid, d in detail.items():
                    per_trace[tid] = {
                        "z": [round(float(v), 5) for v in d["z"].tolist()],
                        "ends": [int(v) for v in d["ends"].tolist()],
                        "threshold": float(d["threshold"]),
                        "cal_half": int(d["cal_half"]),
                        "case": case_name,
                    }
            payload["runs"][cand][str(w)] = per_trace
            print(f"{cand} w={w}: {len(per_trace)} traces", flush=True)

    # token-level metadata for every drift trace (post-hoc: labels are allowed here)
    meta = {}
    for tid, lab in labels.items():
        tr = traces[tid]
        pieces = token_pieces(tr)
        in_code, fence_tok = code_regions(pieces)
        meta[tid] = {
            "domain": lab["domain"],
            "product_onset": lab["product_onset"],
            "evidence_onset": lab["evidence_onset"],
            "product_class": lab["product_class"],
            "T": len(pieces),
            "pieces": pieces,
            "in_code": in_code,
            "fence_tok": fence_tok,
            "classes": classify(pieces, in_code, fence_tok),
        }
    payload["meta"] = meta
    routine = {
        tid: {"T": int(t.token_ids.numel()), "arm": rio.arm_class(t)}
        for v in batches.values()
        for t in v
        for tid in [t.trace_id]
        if rio.arm_class(t) in ("clean", "benign")
    }
    payload["routine"] = routine
    (OUT / "pertoken.json").write_text(json.dumps(payload), encoding="utf-8")
    print("wrote", OUT / "pertoken.json")


if __name__ == "__main__":
    main()
