"""Supplementary: anchor sensitivity, class composition of the id-conditional
deviation, routine code_keyword audit, and the identity of code's novel tokens."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from research_v2 import io as rio
from research_v2.features import selection_rate_windows

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
BAND = list(range(5, 16))
W8 = 8
WINDOW = 48


def jsd(p, q):
    p = np.asarray(p, np.float64); q = np.asarray(q, np.float64)
    p = p / p.sum(-1, keepdims=True); q = q / q.sum(-1, keepdims=True)
    m = 0.5 * (p + q)
    with np.errstate(divide="ignore", invalid="ignore"):
        tp = np.where(p > 0, p * np.log2(p / m), 0.0)
        tq = np.where(q > 0, q * np.log2(q / m), 0.0)
    return 0.5 * tp.sum(-1) + 0.5 * tq.sum(-1)


def main() -> None:
    meta = json.loads((OUT / "token_cache_meta.json").read_text(encoding="utf-8"))
    cache = np.load(OUT / "token_cache.npz")
    classes = meta["classes"]
    tmeta = {t["trace_id"]: t for t in meta["traces"]}
    probs, token_id, pos, trace_ix, tclass = (
        cache["probs"], cache["token_id"], cache["pos"], cache["trace_ix"], cache["tclass"]
    )
    n_layers = probs.shape[1]
    routine = sorted([t for t in meta["traces"] if t["arm_class"] in ("clean", "benign")], key=lambda t: t["trace_id"])
    fit_ix = {t["trace_ix"] for i, t in enumerate(routine) if i % 2 == 0}
    held_ix = {t["trace_ix"] for i, t in enumerate(routine) if i % 2 == 1}
    mask_fit = np.isin(trace_ix, list(fit_ix))
    mask_held = np.isin(trace_ix, list(held_ix))
    prog = [t for t in meta["traces"] if t["arm_class"] == "drift" and t["label_domain"] == "programming"]
    other = [t for t in meta["traces"] if t["arm_class"] == "drift" and t["label_domain"] not in (None, "programming")]

    fit_probs = probs[mask_fit].astype(np.float32)
    class_centroid = np.zeros((len(classes), n_layers, 64), np.float32)
    for ci in range(len(classes)):
        sel = tclass[mask_fit] == ci
        if sel.any():
            class_centroid[ci] = fit_probs[sel].mean(0)

    MIN_OCC = 8
    fit_rows = np.nonzero(mask_fit)[0]
    ids_arr = token_id[mask_fit]
    order = np.argsort(ids_arr, kind="stable")
    uniq, starts, counts = np.unique(ids_arr[order], return_index=True, return_counts=True)
    id_centroid, id_class = {}, {}
    for uid, s, c in zip(uniq.tolist(), starts.tolist(), counts.tolist()):
        if c < MIN_OCC:
            continue
        rows = fit_rows[order[s : s + c]]
        id_centroid[int(uid)] = probs[rows].astype(np.float32).mean(0)
        id_class[int(uid)] = int(Counter(tclass[rows].tolist()).most_common(1)[0][0])

    def wmask(t, field):
        o = t[field]
        if o is None:
            return np.zeros(len(pos), bool)
        return (trace_ix == t["trace_ix"]) & (pos >= o) & (pos < min(o + WINDOW, t["token_count"]))

    report = {}

    # --- id-conditional deviation broken down by token class ---------------
    def id_dev_by_class(mask):
        rows = np.nonzero(mask)[0]
        out = defaultdict(list)
        for r in rows:
            cen = id_centroid.get(int(token_id[r]))
            if cen is None:
                continue
            d = float(jsd(probs[r].astype(np.float32)[None], cen[None])[0][BAND].mean())
            out[classes[int(tclass[r])]].append(d)
        return {k: {"n": len(v), "median": float(np.median(v))} for k, v in sorted(out.items())}

    mask_code = np.logical_or.reduce([wmask(t, "product_onset") for t in prog])
    mask_other = np.logical_or.reduce([wmask(t, "product_onset") for t in other])
    report["id_deviation_by_class"] = {
        "routine_held": id_dev_by_class(mask_held),
        "code": id_dev_by_class(mask_code),
        "other_drift": id_dev_by_class(mask_other),
    }

    # --- anchor sensitivity: evidence vs product onset ---------------------
    batches = rio.load_core()
    traces = {t.trace_id: t for b in batches.values() for t in b}
    fit_blocks = [selection_rate_windows(traces[t["trace_id"]].top_k_ids, W8, BAND)[1].numpy() for t in routine if t["trace_ix"] in fit_ix]
    fit_mat = np.concatenate([b for b in fit_blocks if len(b)])
    mu, sd = fit_mat.mean(0), fit_mat.std(0) + 1e-3
    centre = ((fit_mat - mu) / sd).mean(0)

    def g1_median(t, field):
        o = t[field]
        if o is None:
            return None
        ends, w = selection_rate_windows(traces[t["trace_id"]].top_k_ids, W8, BAND)
        e = ends.numpy()
        keep = (e >= o + W8 - 1) & (e < min(o + WINDOW, t["token_count"]))
        if not keep.any():
            return None
        z = (w.numpy()[keep] - mu) / sd - centre
        return float(np.median((z ** 2).sum(1)))

    def js_class_median(mask):
        x = probs[mask].astype(np.float32)
        return float(np.median(jsd(x, class_centroid[tclass[mask]])[:, BAND].mean(1)))

    report["anchor_sensitivity"] = {
        t["trace_id"]: {
            "product_onset": t["product_onset"],
            "evidence_onset": t["evidence_onset"],
            "g1_median_product": g1_median(t, "product_onset"),
            "g1_median_evidence": g1_median(t, "evidence_onset"),
            "js_class_median_product": js_class_median(wmask(t, "product_onset")),
            "js_class_median_evidence": js_class_median(wmask(t, "evidence_onset")),
        }
        for t in prog
    }
    report["routine_g1"] = {
        "fit_median": float(np.median(((fit_mat - mu) / sd - centre) ** 2 @ np.ones(fit_mat.shape[1]))),
        "fit_q90": float(np.quantile((((fit_mat - mu) / sd - centre) ** 2).sum(1), 0.9)),
    }

    # --- routine code_keyword audit + code novel-token identity ------------
    ci_kw = classes.index("code_keyword")
    kw_rows = np.nonzero(mask_fit & (tclass == ci_kw))[0]
    kw_counter = Counter(rio.decode_token_texts(token_id[kw_rows].tolist()))
    report["routine_code_keyword_tokens"] = kw_counter.most_common(25)
    code_kw_rows = np.nonzero(mask_code & (tclass == ci_kw))[0]
    report["code_window_code_keyword_tokens"] = Counter(rio.decode_token_texts(token_id[code_kw_rows].tolist())).most_common(25)

    routine_ids = set(token_id[mask_fit | mask_held].tolist())
    novel_rows = [r for r in np.nonzero(mask_code)[0] if int(token_id[r]) not in routine_ids]
    report["code_novel_tokens"] = Counter(rio.decode_token_texts([int(token_id[r]) for r in novel_rows])).most_common(40)
    report["code_novel_class_mix"] = {
        classes[c]: n for c, n in Counter(int(tclass[r]) for r in novel_rows).most_common()
    }
    other_novel_rows = [r for r in np.nonzero(mask_other)[0] if int(token_id[r]) not in routine_ids]
    report["other_drift_novel_class_mix"] = {
        classes[c]: n for c, n in Counter(int(tclass[r]) for r in other_novel_rows).most_common()
    }
    # novel tokens: distance to the nearest routine class centroid, per region
    def nearest_class_js(rows):
        vals = []
        for r in rows:
            x = probs[r].astype(np.float32)
            d = jsd(np.broadcast_to(x, (len(classes),) + x.shape), class_centroid)[:, BAND].mean(1)
            vals.append(float(d.min()))
        return {"n": len(vals), "median": float(np.median(vals))} if vals else {"n": 0}

    report["novel_nearest_class_js"] = {
        "code": nearest_class_js(novel_rows),
        "other_drift": nearest_class_js(other_novel_rows),
        "routine_held_all": nearest_class_js(list(np.nonzero(mask_held)[0][::20])),
    }

    (OUT / "token_identity_supp.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["anchor_sensitivity"], indent=1))
    print(json.dumps(report["id_deviation_by_class"], indent=1))
    print("routine code_keyword:", report["routine_code_keyword_tokens"][:15])
    print("code novel class mix:", report["code_novel_class_mix"], "other:", report["other_drift_novel_class_mix"])
    print("novel nearest class js:", report["novel_nearest_class_js"])
    print("code novel tokens:", report["code_novel_tokens"][:25])


if __name__ == "__main__":
    main()
