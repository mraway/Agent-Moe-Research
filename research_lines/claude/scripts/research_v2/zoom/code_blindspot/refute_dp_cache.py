"""REFUTER cache for depth_profile: independent recomputation of the per-token
rmass / entropy / top-1 / jaccard statistics under several routine references.

Read-only on traces, labels, frozen results and existing scripts.  Writes only under
artifacts/agent_v2/research_v2/zoom_code_blindspot/refute_depth_profile/.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch

from research_v2 import io as rio

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot/refute_depth_profile"
LABELS = REPO / "docs/research_v2/labels/product_onset_v1_adjudicated.jsonl"
EPS = 1e-12
L, E = 16, 64

PROG = ("b1-f2-012", "b1-f2-014", "b1-f3-016", "b2-f2-011",
        "b2-f2-012", "b2-f2-014", "b2-f2-015", "b2-f3-020")
LITERAL = ("b1-f2-012", "b1-f2-014", "b1-f3-016", "b2-f2-011", "b2-f3-020")
PROSE = ("b2-f2-012", "b2-f2-014", "b2-f2-015")

# --- token classes (same rules as tokenclass_cache.py, copied so this file is standalone)
CLASSES = ("whitespace", "code_symbol", "punctuation", "code_keyword",
           "number", "fragment", "word", "other")
CLASS_IX = {n: i for i, n in enumerate(CLASSES)}
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
    s = text.strip()
    if s == "":
        return "whitespace"
    if any(c in CODE_SYM for c in s):
        return "code_symbol"
    if (s in SQL_KEYWORDS and s.isupper()) or s in CODE_KEYWORDS:
        return "code_keyword"
    if not any(c.isalnum() for c in s):
        return "punctuation"
    if any(c.isdigit() for c in s) and not any(c.isalpha() for c in s):
        return "number"
    if s.isalpha():
        cont = (prev_text is not None and prev_text != ""
                and prev_text[-1].isalnum() and not text[:1].isspace())
        return "fragment" if cont else "word"
    return "other"


def short(tid: str) -> str:
    return "-".join(tid.split("-")[:3])


def top8_mask(traces) -> torch.Tensor:
    """[16,64] long mask of the 8 most-selected experts per layer over `traces`."""
    sel = torch.zeros(L, E, dtype=torch.float64)
    for t in traces:
        oh = torch.zeros(L, t.token_count, E)
        oh.scatter_(2, t.top_k_ids, 1.0)
        sel += oh.sum(1).double()
    rank = sel.argsort(dim=1, descending=True)
    m = torch.zeros(L, E, dtype=torch.long)
    m.scatter_(1, rank[:, :8], 1)
    return m


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    labels = {}
    for line in LABELS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            labels[r["trace_id"]] = r
    batches = rio.load_core()
    traces = [t for b in ("b1", "b2") for t in batches[b]]
    routine = sorted([t for t in traces if rio.arm_class(t) in ("clean", "benign")],
                     key=lambda t: t.trace_id)

    # depth_profile's deterministic stratified halves
    grp = defaultdict(list)
    for t in routine:
        grp[(t.batch, t.arm)].append(t)
    fit, held = [], []
    for k in sorted(grp):
        for i, t in enumerate(grp[k]):
            (fit if i % 2 == 0 else held).append(t)

    refs = {
        "fit": top8_mask(fit),
        "b1": top8_mask([t for t in routine if t.batch == "b1"]),
        "b2": top8_mask([t for t in routine if t.batch == "b2"]),
        "b1fit": top8_mask([t for t in fit if t.batch == "b1"]),
        "b2fit": top8_mask([t for t in fit if t.batch == "b2"]),
    }
    # token-mean router distribution on the fit half (for JS)
    acc = torch.zeros(L, E, dtype=torch.float64)
    ntok = 0
    for t in fit:
        p = t.probabilities().float()
        acc += p.sum(1).double()
        ntok += p.shape[1]
        t._probabilities = None
    ref_p = (acc / ntok).float()

    meta = []
    store = {k: [] for k in ("rmass_fit", "rmass_b1", "rmass_b2",
                             "rmass_b1fit", "rmass_b2fit", "ent", "top1", "jac", "js")}
    tokmeta = {"pos": [], "tclass": [], "trace_ix": [], "token_id": []}
    for ix, t in enumerate(traces):
        p = t.probabilities().float()
        T = p.shape[1]
        m = 0.5 * (p + ref_p.unsqueeze(1))
        lm = torch.log2(m + EPS)
        js = 0.5 * ((p * (torch.log2(p + EPS) - lm)).sum(-1)
                    + (ref_p.unsqueeze(1) * (torch.log2(ref_p.unsqueeze(1) + EPS) - lm)).sum(-1))
        ent = -(p * torch.log2(p + EPS)).sum(-1)
        top1 = p.max(-1).values
        fm = refs["fit"]
        inter = torch.gather(fm.unsqueeze(1).expand(-1, T, -1), 2, t.top_k_ids).sum(-1).float()
        jac = inter / (16.0 - inter)
        for name in ("fit", "b1", "b2", "b1fit", "b2fit"):
            store[f"rmass_{name}"].append(
                (p * refs[name].unsqueeze(1).float()).sum(-1).numpy().astype(np.float32))
        store["ent"].append(ent.numpy().astype(np.float32))
        store["top1"].append(top1.numpy().astype(np.float32))
        store["jac"].append(jac.numpy().astype(np.float32))
        store["js"].append(js.numpy().astype(np.float32))
        texts = rio.decode_token_texts(t.token_ids.tolist())
        cls = [CLASS_IX[classify(txt, texts[i - 1] if i else None)] for i, txt in enumerate(texts)]
        tokmeta["pos"].append(np.arange(T, dtype=np.int32))
        tokmeta["tclass"].append(np.array(cls, dtype=np.int8))
        tokmeta["trace_ix"].append(np.full(T, ix, dtype=np.int32))
        tokmeta["token_id"].append(t.token_ids.numpy().astype(np.int32))
        lab = labels.get(t.trace_id)
        meta.append({
            "trace_ix": ix, "trace_id": t.trace_id, "short": short(t.trace_id),
            "batch": t.batch, "arm": t.arm, "arm_class": rio.arm_class(t),
            "T": int(T), "domain": (lab or {}).get("domain"),
            "product_onset": (lab or {}).get("product_onset"),
            "product_class": (lab or {}).get("product_class"),
            "evidence_onset": t.evidence_onset,
            "is_code": short(t.trace_id) in PROG,
            "workflow": t.workflow, "channel": t.channel,
            "in_fit": t.trace_id in {x.trace_id for x in fit},
            "in_held": t.trace_id in {x.trace_id for x in held},
        })
        t._probabilities = None
        if ix % 60 == 0:
            print("cached", ix, flush=True)

    off = np.cumsum([0] + [m["T"] for m in meta]).astype(np.int64)
    arrs = {k: np.concatenate([a.T for a in v], axis=0) for k, v in store.items()}  # [N,16]
    arrs.update({k: np.concatenate(v) for k, v in tokmeta.items()})
    arrs["offsets"] = off
    for name, mk in refs.items():
        arrs[f"ref_top8_{name}"] = mk.numpy().astype(np.int8)
    arrs["ref_p"] = ref_p.numpy()
    np.savez_compressed(OUT / "tokens.npz", **arrs)
    (OUT / "meta.json").write_text(json.dumps(
        {"traces": meta, "fit_ids": [t.trace_id for t in fit],
         "held_ids": [t.trace_id for t in held], "n_fit_tokens": int(ntok),
         "classes": list(CLASSES)}), encoding="utf-8")
    print("wrote", OUT / "tokens.npz", arrs["ent"].shape)
    same = {n: int((refs["fit"] == refs[n]).all(1).sum()) for n in refs}
    print("layers where ref top-8 set identical to fit-ref:", same)


if __name__ == "__main__":
    main()
