"""Build the token-level cache for the token-identity / token-class lens.

Read-only on traces and labels.  Writes one .npz + one .json under
artifacts/agent_v2/research_v2/zoom_code_blindspot/.

Cache contents (one row per decode token of all 360 core traces):
  probs      [N, 16, 64] float16  full router softmax
  token_id   [N] int32
  pos        [N] int32            token index inside its trace
  trace_ix   [N] int32            index into meta["traces"]
  tclass     [N] int8             token class id (see CLASSES)
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch

from research_v2 import io as rio

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
LABELS = REPO / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"

CLASSES = (
    "whitespace",
    "code_symbol",
    "punctuation",
    "code_keyword",
    "number",
    "fragment",
    "word",
    "other",
)
CLASS_IX = {name: i for i, name in enumerate(CLASSES)}

CODE_SYM = set("{}[]()<>=_|\\/*&^~`;$#@+")
SQL_KEYWORDS = {
    "SELECT", "FROM", "WHERE", "JOIN", "GROUP", "ORDER", "BY", "HAVING", "COUNT",
    "SUM", "AVG", "MIN", "MAX", "DISTINCT", "LIMIT", "AS", "ON", "AND", "OR",
    "NOT", "INNER", "LEFT", "RIGHT", "OUTER", "INSERT", "INTO", "VALUES",
    "UPDATE", "SET", "DELETE", "CREATE", "TABLE", "WITH", "UNION", "CASE",
    "WHEN", "THEN", "ELSE", "END", "NULL", "DESC", "ASC", "BETWEEN", "LIKE", "IN",
}
CODE_KEYWORDS = {
    "def", "return", "fn", "let", "const", "function", "var", "import", "class",
    "elif", "lambda", "async", "await", "impl", "pub", "struct", "enum", "match",
    "mut", "use", "println", "int", "str", "bool", "self", "this", "new", "void",
    "static", "public", "private", "null", "undefined", "true", "false", "None",
    "True", "False", "yield", "except", "raise", "try", "catch", "throw",
    "extends", "typeof", "float", "char", "string", "vec", "Vec", "String",
    "usize", "i32", "u32", "f64", "export", "require", "module",
}


def classify(text: str, prev_text: str | None) -> str:
    stripped = text.strip()
    if stripped == "":
        return "whitespace"
    if any(ch in CODE_SYM for ch in stripped):
        return "code_symbol"
    if stripped in SQL_KEYWORDS and stripped.isupper():
        return "code_keyword"
    if stripped in CODE_KEYWORDS:
        return "code_keyword"
    if not any(ch.isalnum() for ch in stripped):
        return "punctuation"
    if any(ch.isdigit() for ch in stripped) and not any(ch.isalpha() for ch in stripped):
        return "number"
    if stripped.isalpha():
        continues = (
            prev_text is not None
            and prev_text != ""
            and prev_text[-1].isalnum()
            and not text[:1].isspace()
        )
        return "fragment" if continues else "word"
    return "other"


def classify_text_only(text: str) -> str:
    """Sensitivity variant: no context, leading space decides word vs fragment."""
    stripped = text.strip()
    if stripped == "":
        return "whitespace"
    if any(ch in CODE_SYM for ch in stripped):
        return "code_symbol"
    if (stripped in SQL_KEYWORDS and stripped.isupper()) or stripped in CODE_KEYWORDS:
        return "code_keyword"
    if not any(ch.isalnum() for ch in stripped):
        return "punctuation"
    if any(ch.isdigit() for ch in stripped) and not any(ch.isalpha() for ch in stripped):
        return "number"
    if stripped.isalpha():
        return "word" if text[:1] == " " else "fragment"
    return "other"


def load_labels() -> dict[str, dict]:
    rows = {}
    for line in LABELS.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows[row["trace_id"]] = row
    return rows


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    batches = rio.load_core()
    labels = load_labels()

    traces_meta = []
    probs_list = []
    token_ids_list = []
    pos_list = []
    trace_ix_list = []
    tclass_list = []
    tclass_txt_list = []

    for batch in ("b1", "b2"):
        for trace in batches[batch]:
            arm = rio.arm_class(trace)
            ids = trace.token_ids.tolist()
            texts = rio.decode_token_texts(ids)
            classes = []
            classes_txt = []
            prev = None
            for text in texts:
                classes.append(CLASS_IX[classify(text, prev)])
                classes_txt.append(CLASS_IX[classify_text_only(text)])
                prev = text
            n = len(ids)
            label = labels.get(trace.trace_id)
            traces_meta.append(
                {
                    "trace_ix": len(traces_meta),
                    "trace_id": trace.trace_id,
                    "batch": batch,
                    "arm_class": arm,
                    "arm": trace.arm,
                    "domain": trace.domain,
                    "workflow": trace.workflow,
                    "channel": trace.channel,
                    "positive": bool(trace.positive),
                    "token_count": n,
                    "evidence_onset": trace.evidence_onset,
                    "product_onset": None if label is None else label["product_onset"],
                    "product_class": None if label is None else label["product_class"],
                    "label_domain": None if label is None else label["domain"],
                }
            )
            probs = trace.probabilities()
            if probs.dtype != torch.float16:
                probs = probs.half()
            probs_list.append(probs.permute(1, 0, 2).contiguous().numpy())
            token_ids_list.append(np.asarray(ids, dtype=np.int32))
            pos_list.append(np.arange(n, dtype=np.int32))
            trace_ix_list.append(np.full(n, len(traces_meta) - 1, dtype=np.int32))
            tclass_list.append(np.asarray(classes, dtype=np.int8))
            tclass_txt_list.append(np.asarray(classes_txt, dtype=np.int8))
            trace._probabilities = None

    np.savez(
        OUT / "token_cache.npz",
        probs=np.concatenate(probs_list, axis=0),
        token_id=np.concatenate(token_ids_list),
        pos=np.concatenate(pos_list),
        trace_ix=np.concatenate(trace_ix_list),
        tclass=np.concatenate(tclass_list),
        tclass_txt=np.concatenate(tclass_txt_list),
    )
    (OUT / "token_cache_meta.json").write_text(
        json.dumps({"classes": list(CLASSES), "traces": traces_meta}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print("tokens:", sum(len(x) for x in token_ids_list), "traces:", len(traces_meta))


if __name__ == "__main__":
    main()
