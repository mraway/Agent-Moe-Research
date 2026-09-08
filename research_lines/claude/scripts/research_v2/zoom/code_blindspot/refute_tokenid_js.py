"""REFUTATION lens, part 2: independent recomputation of the per-token router-JS
claims (class-conditional JS, within-token-id deviation, novel-token nearest-class JS)
plus the controls the claim is missing:
  * a NOVEL-vs-NOVEL control (routine-held tokens whose id is unseen in routine-fit),
    instead of the all-token routine control;
  * class-stratified novel-token comparison (code vs other-drift inside one class);
  * distinct-id (unweighted) versions;
  * per-trace values for all 59 drift traces.
Own implementation; only the definitions were read from the colleague's scripts.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import numpy as np
import torch

from research_v2 import io as rio

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
LABELS = REPO / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"
LAYERS = list(range(5, 16))
SPAN = 48

SYM = set("{}[]()<>=_|\\/*&^~`;$#@+")
KW = {"SELECT","FROM","WHERE","JOIN","GROUP","ORDER","BY","HAVING","COUNT","SUM","AVG",
      "MIN","MAX","DISTINCT","LIMIT","AS","ON","AND","OR","NOT","INNER","LEFT","RIGHT",
      "OUTER","INSERT","INTO","VALUES","UPDATE","SET","DELETE","CREATE","TABLE","WITH",
      "UNION","CASE","WHEN","THEN","ELSE","END","NULL","DESC","ASC","BETWEEN","LIKE","IN",
      "def","return","fn","let","const","function","var","import","class","elif","lambda",
      "async","await","impl","pub","struct","enum","match","mut","use","println","int",
      "str","bool","self","this","new","void","static","public","private","null",
      "undefined","true","false","None","True","False","yield","except","raise","try",
      "catch","throw","extends","typeof","float","char","string","vec","Vec","String",
      "usize","i32","u32","f64","export","require","module"}
CLASSES = ("whitespace", "code_symbol", "code_keyword", "punctuation", "number", "word", "other")


def klass(text: str) -> str:
    s = text.strip()
    if s == "":
        return "whitespace"
    if any(c in SYM for c in s):
        return "code_symbol"
    if s in KW:
        return "code_keyword"
    if not any(c.isalnum() for c in s):
        return "punctuation"
    if any(c.isdigit() for c in s) and not any(c.isalpha() for c in s):
        return "number"
    if s.isalpha():
        return "word"
    return "other"


def js(p: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Jensen-Shannon divergence base 2, per layer, averaged over layers.
    p [N, L, 64], q [L, 64] or [N, L, 64] -> [N]"""
    m = 0.5 * (p + q)
    def kl(a, b):
        mask = a > 0
        out = np.zeros_like(a)
        out[mask] = a[mask] * (np.log2(a[mask]) - np.log2(b[mask]))
        return out.sum(-1)
    d = 0.5 * kl(p, m) + 0.5 * kl(np.broadcast_to(q, p.shape).copy(), m)
    return d.mean(-1)


def main() -> None:
    lab = {json.loads(l)["trace_id"]: json.loads(l)
           for l in LABELS.read_text(encoding="utf-8").splitlines() if l.strip()}
    batches = rio.load_core()
    traces = [t for b in ("b1", "b2") for t in batches[b]]

    rows = []          # per trace: dict with probs [T,L,64], ids, classes
    for t in traces:
        p = t.probabilities()[LAYERS].to(torch.float32).permute(1, 0, 2).numpy()  # [T,L,64]
        p = p / p.sum(-1, keepdims=True)
        ids = t.token_ids.numpy().astype(np.int64)
        texts = rio.decode_token_texts(t.token_ids.tolist())
        rows.append({"trace": t, "p": p, "ids": ids,
                     "cls": np.array([CLASSES.index(klass(x)) for x in texts]),
                     "arm": rio.arm_class(t)})
        t._probabilities = None

    routine = sorted([r for r in rows if r["arm"] in ("clean", "benign")],
                     key=lambda r: r["trace"].trace_id)
    # NOTE: sorting by trace_id makes routine[0::2] exactly the 120 benign_control
    # traces and routine[1::2] exactly the 120 clean traces (see report section 3).
    mode = sys.argv[1] if len(sys.argv) > 1 else "benign_fit"
    if mode == "clean_fit":
        fit, held = routine[1::2], routine[0::2]
    else:
        fit, held = routine[0::2], routine[1::2]
    print("MODE", mode, "fit arms", {r["arm"] for r in fit}, "held arms", {r["arm"] for r in held})
    drift = [r for r in rows if r["arm"] == "drift"]

    def stack(rs, sl=None):
        P = np.concatenate([r["p"] for r in rs]); I = np.concatenate([r["ids"] for r in rs])
        C = np.concatenate([r["cls"] for r in rs])
        return P, I, C

    Pf, If, Cf = stack(fit)
    Ph, Ih, Ch = stack(held)

    # class centroids from routine-fit
    cent = np.stack([Pf[Cf == c].mean(0) if (Cf == c).sum() else np.full(Pf.shape[1:], 1 / 64)
                     for c in range(len(CLASSES))])
    glob = Pf.mean(0)
    # id centroids (>=8 occurrences in fit)
    uid, cnt = np.unique(If, return_counts=True)
    keep = uid[cnt >= 8]
    idcent = {int(i): Pf[If == i].mean(0) for i in keep}
    fit_vocab = set(int(x) for x in np.unique(If))
    routine_vocab = set(int(x) for x in np.unique(np.concatenate([Pf and If, Ih]))) if False else \
        set(int(x) for x in np.unique(np.concatenate([If, Ih])))

    def metrics(P, I, C):
        own = js(P, np.zeros(1))  # placeholder
        return None

    def class_cond(P, C):
        return js(P, cent[C]) if False else np.array(
            [js(P[C == c], cent[c]) for c in range(len(CLASSES)) if (C == c).sum()], dtype=object)

    def js_to_class(P, C):
        out = np.empty(P.shape[0])
        for c in range(len(CLASSES)):
            m = C == c
            if m.any():
                out[m] = js(P[m], cent[c])
        return out

    def js_to_nearest_class(P):
        cand = np.stack([js(P, cent[c]) for c in range(len(CLASSES))], 1)
        return cand.min(1)

    def js_to_id(P, I):
        m = np.array([int(i) in idcent for i in I])
        out = np.full(P.shape[0], np.nan)
        for i in np.unique(I[m]):
            sel = (I == i) & m
            out[sel] = js(P[sel], idcent[int(i)])
        return out, m

    res = {}

    # ---------- region definitions ----------
    def region_idx(r, anchor="product"):
        row = lab[r["trace"].trace_id]
        o = int(row["product_onset"] if anchor == "product" else row["evidence_onset"])
        T = r["p"].shape[0]
        return np.arange(o, min(o + SPAN, T))

    regions = {}
    for r in drift:
        ix = region_idx(r)
        d = lab[r["trace"].trace_id]["domain"]
        regions.setdefault("CODE" if d == "programming" else d, []).append(
            (r["trace"].trace_id, r["p"][ix], r["ids"][ix], r["cls"][ix]))
    regions["routine_held"] = [(r["trace"].trace_id, r["p"], r["ids"], r["cls"]) for r in held]
    regions["routine_fit"] = [(r["trace"].trace_id, r["p"], r["ids"], r["cls"]) for r in fit]
    regions["OTHER_DRIFT"] = [x for k, v in regions.items()
                              if k not in ("CODE", "routine_held", "routine_fit") for x in v]

    def q(a, p):
        a = np.asarray(a, float); a = a[~np.isnan(a)]
        return float(np.quantile(a, p)) if a.size else float("nan")

    summary = {}
    for name, items in regions.items():
        P = np.concatenate([x[1] for x in items]); I = np.concatenate([x[2] for x in items])
        C = np.concatenate([x[3] for x in items])
        jc = js_to_class(P, C); jg = js(P, glob); jn = js_to_nearest_class(P)
        jid, cov = js_to_id(P, I)
        novel = np.array([int(i) not in fit_vocab for i in I])
        entry = {
            "n_tokens": int(P.shape[0]),
            "class_mix": {CLASSES[c]: float((C == c).mean()) for c in range(len(CLASSES))},
            "js_class_median": q(jc, .5), "js_class_q90": q(jc, .9),
            "js_global_median": q(jg, .5),
            "js_nearest_class_median": q(jn, .5),
            "id_cond_coverage": float(cov.mean()),
            "id_cond_median": q(jid[cov], .5),
            "novel_frac_vs_fit": float(novel.mean()),
            "novel_nearest_class_median": q(jn[novel], .5) if novel.any() else None,
            "known_nearest_class_median": q(jn[~novel], .5),
            "novel_class_mix": {CLASSES[c]: float((C[novel] == c).mean()) for c in range(len(CLASSES))} if novel.any() else None,
            "novel_nearest_class_by_class": {
                CLASSES[c]: {"n": int(((C == c) & novel).sum()),
                             "median": q(jn[(C == c) & novel], .5)}
                for c in range(len(CLASSES)) if ((C == c) & novel).sum() >= 5},
            "js_class_by_class": {
                CLASSES[c]: {"n": int((C == c).sum()), "median": q(jc[C == c], .5)}
                for c in range(len(CLASSES)) if (C == c).sum() >= 5},
        }
        summary[name] = entry
    res["regions"] = summary

    # ---------- the missing control: routine-held tokens NOVEL vs routine-fit -------
    Pn = Ph[np.array([int(i) not in fit_vocab for i in Ih])]
    Cn = Ch[np.array([int(i) not in fit_vocab for i in Ih])]
    jn_h = js_to_nearest_class(Pn)
    res["routine_held_novel_control"] = {
        "n": int(Pn.shape[0]), "median": q(jn_h, .5),
        "class_mix": {CLASSES[c]: float((Cn == c).mean()) for c in range(len(CLASSES))},
        "by_class": {CLASSES[c]: {"n": int((Cn == c).sum()), "median": q(jn_h[Cn == c], .5)}
                     for c in range(len(CLASSES)) if (Cn == c).sum() >= 5},
    }
    # all-token routine-held control (their control, every 20th token)
    res["routine_held_alltoken_control"] = {
        "median_every20": q(js_to_nearest_class(Ph[::20]), .5),
        "median_all": q(js_to_nearest_class(Ph), .5)}

    # ---------- per drift trace ----------
    per = {}
    for r in drift:
        ix = region_idx(r)
        P, I, C = r["p"][ix], r["ids"][ix], r["cls"][ix]
        jn = js_to_nearest_class(P); jc = js_to_class(P, C)
        jid, cov = js_to_id(P, I)
        novel = np.array([int(i) not in fit_vocab for i in I])
        per[r["trace"].trace_id] = {
            "domain": lab[r["trace"].trace_id]["domain"], "batch": r["trace"].batch,
            "product_class": lab[r["trace"].trace_id]["product_class"],
            "n": int(P.shape[0]),
            "js_class_median": q(jc, .5), "novel_frac": float(novel.mean()),
            "novel_nearest_class_median": q(jn[novel], .5) if novel.any() else None,
            "novel_word_median": q(jn[novel & (C == CLASSES.index("word"))], .5),
            "n_novel_word": int((novel & (C == CLASSES.index("word"))).sum()),
            "id_cond_median": q(jid[cov], .5), "id_cov": float(cov.mean()),
            "word_frac": float((C == CLASSES.index("word")).mean()),
        }
    res["per_trace"] = per

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"refute_tokenid_js_{mode}.json").write_text(json.dumps(res, indent=1), encoding="utf-8")

    for k in ("routine_fit", "routine_held", "CODE", "OTHER_DRIFT"):
        e = summary[k]
        print(f"{k:14s} n={e['n_tokens']:6d} jsClass={e['js_class_median']:.4f} jsGlob={e['js_global_median']:.4f} "
              f"jsNear={e['js_nearest_class_median']:.4f} idCond={e['id_cond_median']:.4f} (cov {e['id_cond_coverage']:.3f}) "
              f"novelFrac={e['novel_frac_vs_fit']:.3f} novelNear={e['novel_nearest_class_median']}")
    print("\nroutine-held NOVEL control:", res["routine_held_novel_control"]["n"],
          res["routine_held_novel_control"]["median"])
    print("routine-held all-token control:", res["routine_held_alltoken_control"])
    print("\nnovel nearest-class JS by class:")
    for k in ("CODE", "OTHER_DRIFT"):
        print(" ", k, summary[k]["novel_nearest_class_by_class"])
    print("  routine_held_novel", res["routine_held_novel_control"]["by_class"])
    print("\nnovel class mix: CODE", summary["CODE"]["novel_class_mix"])
    print("novel class mix: OTHER", summary["OTHER_DRIFT"]["novel_class_mix"])
    print("novel class mix: routine-held", res["routine_held_novel_control"]["class_mix"])


if __name__ == "__main__":
    main()
