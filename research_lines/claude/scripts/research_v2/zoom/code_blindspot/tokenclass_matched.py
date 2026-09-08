"""Second pass of the token-identity lens.

(a) class-matched routine baselines: what per-token routing distance would a routine
    token pool with the *code* class mix show?
(b) per-class distance to the GLOBAL routine centroid (the geometry CAND-A actually
    sees), routine vs code vs other-drift.
(c) window-level aggregate: 48-token mean routing vs the routine window pool, plus a
    token-identity surrogate (each token replaced by its routine id-centroid) that
    separates "which tokens were emitted" from "how they were routed in context".
(d) residual coherence: is the code residual (token routing minus routine id-centroid)
    a consistent direction, or noise?

Post-hoc diagnosis; labels used freely.  Writes JSON only.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
WINDOW = 48
BAND = list(range(5, 16))


def jsd(p, q):
    p = np.asarray(p, dtype=np.float64)
    q = np.asarray(q, dtype=np.float64)
    p = p / p.sum(-1, keepdims=True)
    q = q / q.sum(-1, keepdims=True)
    m = 0.5 * (p + q)
    with np.errstate(divide="ignore", invalid="ignore"):
        tp = np.where(p > 0, p * np.log2(p / m), 0.0)
        tq = np.where(q > 0, q * np.log2(q / m), 0.0)
    return 0.5 * tp.sum(-1) + 0.5 * tq.sum(-1)


def qd(v, probs=(0.25, 0.5, 0.75, 0.9)):
    v = np.asarray(v, dtype=np.float64)
    if v.size == 0:
        return {}
    return {f"q{int(p*100)}": float(np.quantile(v, p)) for p in probs}


def main() -> None:
    meta = json.loads((OUT / "token_cache_meta.json").read_text(encoding="utf-8"))
    cache = np.load(OUT / "token_cache.npz")
    classes = meta["classes"]
    traces = meta["traces"]
    probs = cache["probs"]
    token_id = cache["token_id"]
    pos = cache["pos"]
    trace_ix = cache["trace_ix"]
    tclass = cache["tclass"]
    n_layers = probs.shape[1]

    routine = sorted(
        [t for t in traces if t["arm_class"] in ("clean", "benign")], key=lambda t: t["trace_id"]
    )
    fit_ix = {t["trace_ix"] for i, t in enumerate(routine) if i % 2 == 0}
    held_ix = {t["trace_ix"] for i, t in enumerate(routine) if i % 2 == 1}
    drifts = [t for t in traces if t["arm_class"] == "drift"]
    prog = [t for t in drifts if t["label_domain"] == "programming"]
    other = [t for t in drifts if t["label_domain"] != "programming"]

    mask_fit = np.isin(trace_ix, list(fit_ix))
    mask_held = np.isin(trace_ix, list(held_ix))

    def wmask(t, field):
        o = t[field]
        if o is None:
            return np.zeros(len(pos), dtype=bool)
        return (trace_ix == t["trace_ix"]) & (pos >= o) & (pos < min(o + WINDOW, t["token_count"]))

    code_masks = {t["trace_id"]: wmask(t, "product_onset") for t in prog}
    other_masks = {t["trace_id"]: wmask(t, "product_onset") for t in other}
    mask_code = np.logical_or.reduce(list(code_masks.values()))
    mask_other = np.logical_or.reduce(list(other_masks.values()))

    fit_probs = probs[mask_fit].astype(np.float32)
    fit_class = tclass[mask_fit]
    global_centroid = fit_probs.mean(0)
    class_centroid = np.zeros((len(classes), n_layers, 64), dtype=np.float32)
    for ci in range(len(classes)):
        sel = fit_class == ci
        if sel.any():
            class_centroid[ci] = fit_probs[sel].mean(0)

    report: dict = {"window": WINDOW, "band": BAND}

    # --- (b) per-class distance to the GLOBAL routine centroid ---------------
    def js_glob(mask):
        x = probs[mask].astype(np.float32)
        return jsd(x, np.broadcast_to(global_centroid, x.shape))[:, BAND].mean(1)

    def js_cls(mask):
        x = probs[mask].astype(np.float32)
        return jsd(x, class_centroid[tclass[mask]])[:, BAND].mean(1)

    per_class = {}
    for ci, cname in enumerate(classes):
        row = {}
        for label, mask in (("routine_held", mask_held), ("code", mask_code), ("other_drift", mask_other)):
            sel = mask & (tclass == ci)
            n = int(sel.sum())
            row[label] = {
                "n": n,
                "global_median": float(np.median(js_glob(sel))) if n else None,
                "class_median": float(np.median(js_cls(sel))) if n else None,
            }
        per_class[cname] = row
    report["per_class_global_and_class"] = per_class

    # --- (a) class-matched routine baselines --------------------------------
    def mixfrac(mask):
        n = int(mask.sum())
        return np.array([float((mask & (tclass == ci)).sum()) / n for ci in range(len(classes))])

    def matched_expectation(target_mask, stat="global"):
        w = mixfrac(target_mask)
        vals = []
        for ci in range(len(classes)):
            sel = mask_held & (tclass == ci)
            if not sel.any():
                vals.append(0.0)
                continue
            v = js_glob(sel) if stat == "global" else js_cls(sel)
            vals.append(float(np.median(v)))
        return float((w * np.array(vals)).sum())

    report["class_matched_baseline"] = {
        "routine_held_median_global": float(np.median(js_glob(mask_held))),
        "routine_held_median_class": float(np.median(js_cls(mask_held))),
        "code_median_global": float(np.median(js_glob(mask_code))),
        "code_median_class": float(np.median(js_cls(mask_code))),
        "other_median_global": float(np.median(js_glob(mask_other))),
        "other_median_class": float(np.median(js_cls(mask_other))),
        "matched_routine_under_code_mix_global": matched_expectation(mask_code, "global"),
        "matched_routine_under_code_mix_class": matched_expectation(mask_code, "class"),
        "matched_routine_under_other_mix_global": matched_expectation(mask_other, "global"),
        "matched_routine_under_other_mix_class": matched_expectation(mask_other, "class"),
        "per_code_trace": {
            tid: {
                "actual_global": float(np.median(js_glob(m))),
                "matched_global": matched_expectation(m, "global"),
                "actual_class": float(np.median(js_cls(m))),
                "matched_class": matched_expectation(m, "class"),
            }
            for tid, m in code_masks.items()
        },
    }

    # --- token-id centroids (routine fit, >=8 occurrences) ------------------
    MIN_OCC = 8
    fit_rows = np.nonzero(mask_fit)[0]
    ids_arr = token_id[mask_fit]
    order = np.argsort(ids_arr, kind="stable")
    uniq, starts, counts = np.unique(ids_arr[order], return_index=True, return_counts=True)
    id_centroid = {}
    for uid, s, c in zip(uniq.tolist(), starts.tolist(), counts.tolist()):
        if c < MIN_OCC:
            continue
        id_centroid[int(uid)] = probs[fit_rows[order[s : s + c]]].astype(np.float32).mean(0)

    # --- (c) window-level aggregate ----------------------------------------
    routine_window_means = []
    for t in routine:
        m0 = trace_ix == t["trace_ix"]
        rows = np.nonzero(m0)[0]
        for start in range(0, max(1, len(rows) - WINDOW + 1), WINDOW // 2):
            sel = rows[start : start + WINDOW]
            if len(sel) < WINDOW:
                break
            routine_window_means.append(probs[sel].astype(np.float32).mean(0))
    routine_window_means = np.stack(routine_window_means)
    routine_window_centroid = routine_window_means.mean(0)
    rw_js = jsd(routine_window_means, np.broadcast_to(routine_window_centroid, routine_window_means.shape))[:, BAND].mean(1)

    def surrogate(mask):
        """Identity surrogate: replace each token by its routine id-centroid; novel ids
        fall back to the routine class centroid."""
        rows = np.nonzero(mask)[0]
        out = np.zeros((len(rows), n_layers, 64), dtype=np.float32)
        for k, r in enumerate(rows):
            cen = id_centroid.get(int(token_id[r]))
            out[k] = cen if cen is not None else class_centroid[tclass[r]]
        return out

    def window_row(mask):
        x = probs[mask].astype(np.float32)
        actual = x.mean(0)
        sur = surrogate(mask).mean(0)
        return {
            "n": int(mask.sum()),
            "actual_vs_routine_windows": float(jsd(actual, routine_window_centroid)[BAND].mean()),
            "identity_surrogate_vs_routine_windows": float(jsd(sur, routine_window_centroid)[BAND].mean()),
            "actual_vs_identity_surrogate": float(jsd(actual, sur)[BAND].mean()),
        }

    report["window_level"] = {
        "routine_windows": {
            "n_windows": int(routine_window_means.shape[0]),
            "js_to_routine_window_centroid": qd(rw_js) | {"mean": float(rw_js.mean())},
        },
        "per_code_trace": {tid: window_row(m) for tid, m in code_masks.items()},
        "per_other_trace": {
            t["trace_id"]: {"domain": t["label_domain"], **window_row(other_masks[t["trace_id"]])}
            for t in other
            if other_masks[t["trace_id"]].any()
        },
    }
    # percentile of each window inside the routine window distribution
    def pct(v):
        return float((rw_js < v).mean())

    for tid, row in report["window_level"]["per_code_trace"].items():
        row["pct_in_routine_windows"] = pct(row["actual_vs_routine_windows"])
        row["pct_identity_surrogate"] = pct(row["identity_surrogate_vs_routine_windows"])
    od_by_dom = defaultdict(list)
    for tid, row in report["window_level"]["per_other_trace"].items():
        row["pct_in_routine_windows"] = pct(row["actual_vs_routine_windows"])
        row["pct_identity_surrogate"] = pct(row["identity_surrogate_vs_routine_windows"])
        od_by_dom[row["domain"]].append(row)
    report["window_level"]["other_drift_domain_medians"] = {
        d: {
            "n_traces": len(rows),
            "actual": float(np.median([r["actual_vs_routine_windows"] for r in rows])),
            "surrogate": float(np.median([r["identity_surrogate_vs_routine_windows"] for r in rows])),
            "residual": float(np.median([r["actual_vs_identity_surrogate"] for r in rows])),
            "pct": float(np.median([r["pct_in_routine_windows"] for r in rows])),
        }
        for d, rows in sorted(od_by_dom.items())
    }
    code_rows = list(report["window_level"]["per_code_trace"].values())
    report["window_level"]["code_medians"] = {
        "n_traces": len(code_rows),
        "actual": float(np.median([r["actual_vs_routine_windows"] for r in code_rows])),
        "surrogate": float(np.median([r["identity_surrogate_vs_routine_windows"] for r in code_rows])),
        "residual": float(np.median([r["actual_vs_identity_surrogate"] for r in code_rows])),
        "pct": float(np.median([r["pct_in_routine_windows"] for r in code_rows])),
    }

    # --- (d) residual coherence --------------------------------------------
    def residual_stats(mask):
        rows = np.nonzero(mask)[0]
        rows = [r for r in rows if int(token_id[r]) in id_centroid]
        if len(rows) < 5:
            return {"n": len(rows)}
        x = probs[rows].astype(np.float32)
        cen = np.stack([id_centroid[int(token_id[r])] for r in rows])
        res = (x - cen)[:, BAND, :].reshape(len(rows), -1)
        mean_res = res.mean(0)
        norms = np.linalg.norm(res, axis=1)
        mn = np.linalg.norm(mean_res)
        cos = (res @ mean_res) / (norms * mn + 1e-12)
        return {
            "n": len(rows),
            "mean_token_residual_norm": float(norms.mean()),
            "norm_of_mean_residual": float(mn),
            "coherence_ratio": float(mn / norms.mean()),
            "median_cosine_to_mean_residual": float(np.median(cos)),
        }

    report["residual_coherence"] = {
        "routine_held": residual_stats(mask_held),
        "code": residual_stats(mask_code),
        "other_drift": residual_stats(mask_other),
        "other_drift_by_domain": {
            d: residual_stats(np.logical_or.reduce([other_masks[t["trace_id"]] for t in other if t["label_domain"] == d]))
            for d in sorted({t["label_domain"] for t in other})
        },
        "per_code_trace": {tid: residual_stats(m) for tid, m in code_masks.items()},
    }
    # cross-region alignment of the mean residual: does code's mean residual point the
    # same way as other-drift's?
    def mean_res_vec(mask):
        rows = [r for r in np.nonzero(mask)[0] if int(token_id[r]) in id_centroid]
        if len(rows) < 5:
            return None
        x = probs[rows].astype(np.float32)
        cen = np.stack([id_centroid[int(token_id[r])] for r in rows])
        return (x - cen)[:, BAND, :].reshape(len(rows), -1).mean(0)

    vc, vo, vh = mean_res_vec(mask_code), mean_res_vec(mask_other), mean_res_vec(mask_held)
    def cosv(a, b):
        return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))
    report["residual_coherence"]["cos_code_vs_other"] = cosv(vc, vo)
    report["residual_coherence"]["cos_code_vs_routine_held"] = cosv(vc, vh)
    report["residual_coherence"]["cos_other_vs_routine_held"] = cosv(vo, vh)

    (OUT / "token_identity_matched.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print("wrote", OUT / "token_identity_matched.json")


if __name__ == "__main__":
    main()
