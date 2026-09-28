"""Token-identity / token-class lens on the programming blind spot.

Post-hoc diagnosis on B1+B2 development data.  Uses adjudicated labels freely
(diagnostic, not a detector).  Read-only outside
artifacts/agent_v2/research_v2/zoom_code_blindspot/.

Outputs one JSON with every number quoted in
docs/research_v2/zoom/code_blindspot/token_identity.md.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
WINDOW = 48
BAND = list(range(5, 16))  # CAND-A layer band 5-15


def jsd(p: np.ndarray, q: np.ndarray) -> np.ndarray:
    """Jensen-Shannon divergence, base 2, over the last axis.  Returns [...]"""
    p = p.astype(np.float64)
    q = q.astype(np.float64)
    p = p / p.sum(-1, keepdims=True)
    q = q / q.sum(-1, keepdims=True)
    m = 0.5 * (p + q)
    with np.errstate(divide="ignore", invalid="ignore"):
        tp = np.where(p > 0, p * np.log2(p / m), 0.0)
        tq = np.where(q > 0, q * np.log2(q / m), 0.0)
    return 0.5 * tp.sum(-1) + 0.5 * tq.sum(-1)


def q(values, probs=(0.1, 0.25, 0.5, 0.75, 0.9)):
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0:
        return {f"q{int(p*100)}": None for p in probs}
    return {f"q{int(p*100)}": float(np.quantile(values, p)) for p in probs}


def main() -> None:
    meta = json.loads((OUT / "token_cache_meta.json").read_text(encoding="utf-8"))
    cache = np.load(OUT / "token_cache.npz")
    classes = meta["classes"]
    traces = meta["traces"]
    probs = cache["probs"]           # [N,16,64] float16
    token_id = cache["token_id"]
    pos = cache["pos"]
    trace_ix = cache["trace_ix"]
    tclass = cache["tclass"]
    tclass_txt = cache["tclass_txt"]
    n_layers = probs.shape[1]

    # ---- regions ---------------------------------------------------------
    routine = [t for t in traces if t["arm_class"] in ("clean", "benign")]
    routine.sort(key=lambda t: t["trace_id"])
    fit_ix = {t["trace_ix"] for i, t in enumerate(routine) if i % 2 == 0}
    held_ix = {t["trace_ix"] for i, t in enumerate(routine) if i % 2 == 1}
    drifts = [t for t in traces if t["arm_class"] == "drift"]
    prog = [t for t in drifts if t["label_domain"] == "programming"]
    other = [t for t in drifts if t["label_domain"] != "programming"]

    def window_mask(trace, anchor_field):
        onset = trace[anchor_field]
        if onset is None:
            return np.zeros(len(pos), dtype=bool)
        end = min(onset + WINDOW, trace["token_count"])
        return (trace_ix == trace["trace_ix"]) & (pos >= onset) & (pos < end)

    mask_fit = np.isin(trace_ix, list(fit_ix))
    mask_held = np.isin(trace_ix, list(held_ix))
    mask_routine = mask_fit | mask_held

    code_masks = {t["trace_id"]: window_mask(t, "product_onset") for t in prog}
    code_masks_ev = {t["trace_id"]: window_mask(t, "evidence_onset") for t in prog}
    other_masks = {t["trace_id"]: window_mask(t, "product_onset") for t in other}
    mask_code = np.zeros(len(pos), dtype=bool)
    for m in code_masks.values():
        mask_code |= m
    mask_other = np.zeros(len(pos), dtype=bool)
    for m in other_masks.values():
        mask_other |= m

    report: dict = {
        "window": WINDOW,
        "band": BAND,
        "n_tokens_total": int(len(pos)),
        "counts": {
            "routine_traces": len(routine),
            "routine_fit_traces": len(fit_ix),
            "routine_held_traces": len(held_ix),
            "routine_tokens": int(mask_routine.sum()),
            "routine_fit_tokens": int(mask_fit.sum()),
            "routine_held_tokens": int(mask_held.sum()),
            "prog_traces": len(prog),
            "other_traces": len(other),
            "code_tokens": int(mask_code.sum()),
            "other_drift_tokens": int(mask_other.sum()),
        },
    }

    # ---- (1) class mix ---------------------------------------------------
    def mix(mask, arr=tclass):
        c = Counter(arr[mask].tolist())
        n = int(mask.sum())
        return {
            "n": n,
            "frac": {classes[i]: round(c.get(i, 0) / n, 4) if n else None for i in range(len(classes))},
            "count": {classes[i]: int(c.get(i, 0)) for i in range(len(classes))},
        }

    by_domain = defaultdict(lambda: np.zeros(len(pos), dtype=bool))
    for t in other:
        by_domain[t["label_domain"]] |= other_masks[t["trace_id"]]

    report["class_mix"] = {
        "routine_all": mix(mask_routine),
        "routine_fit": mix(mask_fit),
        "routine_held": mix(mask_held),
        "code_product": mix(mask_code),
        "code_evidence": mix(np.logical_or.reduce(list(code_masks_ev.values()))),
        "other_drift_all": mix(mask_other),
        "other_drift_by_domain": {d: mix(m) for d, m in sorted(by_domain.items())},
        "per_code_trace": {
            t["trace_id"]: mix(code_masks[t["trace_id"]]) for t in prog
        },
        "sensitivity_text_only_rule": {
            "routine_all": mix(mask_routine, tclass_txt),
            "code_product": mix(mask_code, tclass_txt),
            "other_drift_all": mix(mask_other, tclass_txt),
        },
    }

    # ---- centroids from routine FIT --------------------------------------
    fit_probs = probs[mask_fit].astype(np.float32)
    fit_class = tclass[mask_fit]
    global_centroid = fit_probs.mean(0)                     # [16,64]
    class_centroid = np.zeros((len(classes), n_layers, 64), dtype=np.float32)
    class_n = np.zeros(len(classes), dtype=np.int64)
    for ci in range(len(classes)):
        sel = fit_class == ci
        class_n[ci] = int(sel.sum())
        if class_n[ci]:
            class_centroid[ci] = fit_probs[sel].mean(0)
    report["class_centroid_n"] = {classes[i]: int(class_n[i]) for i in range(len(classes))}

    def js_to(mask, centroids_per_token):
        """centroids_per_token: [n,16,64]"""
        x = probs[mask].astype(np.float32)
        return jsd(x, centroids_per_token)   # [n,16]

    def js_class(mask):
        cl = tclass[mask]
        return js_to(mask, class_centroid[cl])

    def js_global(mask):
        n = int(mask.sum())
        return js_to(mask, np.broadcast_to(global_centroid, (n, n_layers, 64)))

    # ---- (2) per-class routing distance ----------------------------------
    def summarize(js, mask, per_class=True):
        band_mean = js[:, BAND].mean(1)
        out = {
            "n": int(mask.sum()),
            "band_mean": q(band_mean) | {"mean": float(band_mean.mean()) if band_mean.size else None},
            "per_layer_median": [float(np.median(js[:, l])) for l in range(n_layers)] if js.size else None,
        }
        if per_class:
            cl = tclass[mask]
            out["by_class"] = {}
            for ci in range(len(classes)):
                sel = cl == ci
                if not sel.any():
                    out["by_class"][classes[ci]] = {"n": 0}
                    continue
                bm = band_mean[sel]
                out["by_class"][classes[ci]] = {
                    "n": int(sel.sum()),
                    "median": float(np.median(bm)),
                    "mean": float(bm.mean()),
                    "q90": float(np.quantile(bm, 0.9)),
                    "per_layer_median": [float(np.median(js[sel, l])) for l in range(n_layers)],
                }
        return out

    js_held_c = js_class(mask_held)
    js_code_c = js_class(mask_code)
    js_other_c = js_class(mask_other)
    js_fit_c = js_class(mask_fit)

    report["js_class_conditional"] = {
        "routine_fit": summarize(js_fit_c, mask_fit),
        "routine_held": summarize(js_held_c, mask_held),
        "code": summarize(js_code_c, mask_code),
        "other_drift": summarize(js_other_c, mask_other),
        "other_drift_by_domain": {
            d: summarize(js_class(m), m) for d, m in sorted(by_domain.items())
        },
        "per_code_trace": {
            t["trace_id"]: summarize(js_class(code_masks[t["trace_id"]]), code_masks[t["trace_id"]])
            for t in prog
        },
        "per_other_trace": {
            t["trace_id"]: {
                "domain": t["label_domain"],
                "n": int(other_masks[t["trace_id"]].sum()),
                "band_median": float(
                    np.median(js_class(other_masks[t["trace_id"]])[:, BAND].mean(1))
                ) if other_masks[t["trace_id"]].any() else None,
            }
            for t in other
        },
    }

    report["js_global_centroid"] = {
        "routine_held": summarize(js_global(mask_held), mask_held, per_class=False),
        "code": summarize(js_global(mask_code), mask_code, per_class=False),
        "other_drift": summarize(js_global(mask_other), mask_other, per_class=False),
        "per_code_trace": {
            t["trace_id"]: float(np.median(js_global(code_masks[t["trace_id"]])[:, BAND].mean(1)))
            for t in prog
        },
    }

    # ---- (4) novel token ids ---------------------------------------------
    routine_ids = set(token_id[mask_routine].tolist())
    fit_ids = set(token_id[mask_fit].tolist())

    def novel_stats(mask):
        ids = token_id[mask]
        novel = np.array([int(i) not in routine_ids for i in ids])
        return {
            "n": int(mask.sum()),
            "n_novel": int(novel.sum()),
            "frac_novel": float(novel.mean()) if ids.size else None,
            "distinct_ids": int(len(set(ids.tolist()))),
            "distinct_novel_ids": int(len({int(i) for i in ids} - routine_ids)),
        }

    report["novel_ids"] = {
        "routine_vocab_size": len(routine_ids),
        "routine_fit_vocab_size": len(fit_ids),
        "routine_held_vs_fit": {
            "n": int(mask_held.sum()),
            "frac_not_in_fit": float(
                np.mean([int(i) not in fit_ids for i in token_id[mask_held]])
            ),
        },
        "code": novel_stats(mask_code),
        "other_drift": novel_stats(mask_other),
        "other_drift_by_domain": {d: novel_stats(m) for d, m in sorted(by_domain.items())},
        "per_code_trace": {t["trace_id"]: novel_stats(code_masks[t["trace_id"]]) for t in prog},
        "per_other_trace": {
            t["trace_id"]: {"domain": t["label_domain"], **novel_stats(other_masks[t["trace_id"]])}
            for t in other
        },
    }

    # novel vs known: class-conditional JS
    def known_novel_js(mask, js):
        ids = token_id[mask]
        novel = np.array([int(i) not in routine_ids for i in ids])
        bm = js[:, BAND].mean(1)
        return {
            "known": {"n": int((~novel).sum()), "median": float(np.median(bm[~novel])) if (~novel).any() else None},
            "novel": {"n": int(novel.sum()), "median": float(np.median(bm[novel])) if novel.any() else None},
        }

    report["novel_vs_known_js_class"] = {
        "code": known_novel_js(mask_code, js_code_c),
        "other_drift": known_novel_js(mask_other, js_other_c),
        "routine_held": known_novel_js(mask_held, js_held_c),
    }

    # ---- (3) token-identity: within-id spread in routine ------------------
    MIN_OCC = 8
    fit_rows = np.nonzero(mask_fit)[0]
    fit_ids_arr = token_id[mask_fit]
    order = np.argsort(fit_ids_arr, kind="stable")
    sorted_ids = fit_ids_arr[order]
    uniq, starts, counts = np.unique(sorted_ids, return_index=True, return_counts=True)
    id_centroid: dict[int, np.ndarray] = {}
    id_spread: dict[int, np.ndarray] = {}   # per-layer median of within-id JS
    within_js_all = []                      # routine fit within-id JS, band mean
    id_spread_q: dict[int, np.ndarray] = {}  # per-layer q90
    for uid, s, c in zip(uniq.tolist(), starts.tolist(), counts.tolist()):
        if c < MIN_OCC:
            continue
        rows = fit_rows[order[s : s + c]]
        x = probs[rows].astype(np.float32)
        cen = x.mean(0)
        d = jsd(x, np.broadcast_to(cen, x.shape))  # [c,16]
        id_centroid[uid] = cen
        id_spread[uid] = np.median(d, axis=0)
        id_spread_q[uid] = np.quantile(d, 0.9, axis=0)
        within_js_all.append(d[:, BAND].mean(1))
    within_js_all = np.concatenate(within_js_all) if within_js_all else np.zeros(0)

    report["within_id"] = {
        "min_occurrences": MIN_OCC,
        "n_ids_tracked": len(id_centroid),
        "routine_fit_tokens_covered": int(sum(len(v) for v in [np.zeros(0)])),  # placeholder
        "routine_fit_within_id_js_band": q(within_js_all) | {"mean": float(within_js_all.mean())},
        "per_layer_median_of_id_medians": [
            float(np.median([id_spread[u][l] for u in id_centroid])) for l in range(n_layers)
        ],
    }

    # scale reference: how far apart are two different routine token ids?
    rng = np.random.default_rng(0)
    ids_list = list(id_centroid)
    if len(ids_list) >= 2:
        a = rng.choice(len(ids_list), size=4000)
        b = rng.choice(len(ids_list), size=4000)
        keep = a != b
        ca = np.stack([id_centroid[ids_list[i]] for i in a[keep]])
        cb = np.stack([id_centroid[ids_list[i]] for i in b[keep]])
        between = jsd(ca, cb)[:, BAND].mean(1)
        report["within_id"]["between_id_js_band"] = q(between) | {"mean": float(between.mean())}

    # code / other-drift / held-out occurrences vs their routine id centroid
    def id_conditional(mask):
        rows = np.nonzero(mask)[0]
        ids = token_id[mask]
        vals, pcts, cov = [], [], 0
        per_layer = defaultdict(list)
        for r, i in zip(rows.tolist(), ids.tolist()):
            cen = id_centroid.get(int(i))
            if cen is None:
                continue
            cov += 1
            x = probs[r].astype(np.float32)
            d = jsd(x[None], cen[None])[0]      # [16]
            vals.append(float(d[BAND].mean()))
            med = id_spread[int(i)][BAND].mean()
            q9 = id_spread_q[int(i)][BAND].mean()
            pcts.append(float(vals[-1] <= q9))
            for l in range(n_layers):
                per_layer[l].append(float(d[l]))
        return {
            "n_tokens": int(mask.sum()),
            "n_covered": cov,
            "frac_covered": cov / max(1, int(mask.sum())),
            "js_to_id_centroid_band": q(vals) | ({"mean": float(np.mean(vals))} if vals else {"mean": None}),
            "frac_within_routine_id_q90": float(np.mean(pcts)) if pcts else None,
            "per_layer_median": [float(np.median(per_layer[l])) for l in range(n_layers)] if cov else None,
        }

    report["id_conditional"] = {
        "routine_held": id_conditional(mask_held),
        "code": id_conditional(mask_code),
        "other_drift": id_conditional(mask_other),
        "other_drift_by_domain": {d: id_conditional(m) for d, m in sorted(by_domain.items())},
        "per_code_trace": {t["trace_id"]: id_conditional(code_masks[t["trace_id"]]) for t in prog},
        "per_other_trace": {
            t["trace_id"]: {"domain": t["label_domain"], **id_conditional(other_masks[t["trace_id"]])}
            for t in other
        },
    }

    # variance-explained style summary: median JS to global vs class vs id centroid
    def triple(mask):
        return {
            "global": float(np.median(js_global(mask)[:, BAND].mean(1))),
            "class": float(np.median(js_class(mask)[:, BAND].mean(1))),
            "id": report_id_median(mask),
        }

    def report_id_median(mask):
        r = id_conditional(mask)
        return r["js_to_id_centroid_band"]["q50"]

    report["centroid_ladder"] = {
        "routine_held": triple(mask_held),
        "code": triple(mask_code),
        "other_drift": triple(mask_other),
        "other_drift_by_domain": {d: triple(m) for d, m in sorted(by_domain.items())},
        "per_code_trace": {t["trace_id"]: triple(code_masks[t["trace_id"]]) for t in prog},
    }

    (OUT / "token_identity_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(json.dumps(report["counts"], indent=1))
    print("wrote", OUT / "token_identity_report.json")


if __name__ == "__main__":
    main()
