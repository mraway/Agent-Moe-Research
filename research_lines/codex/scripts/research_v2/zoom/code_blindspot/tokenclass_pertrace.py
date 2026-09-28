"""Per-trace table for the 8 programming drifts and workflow-matched other-domain drifts."""
from __future__ import annotations

import json
from collections import Counter
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
    probs, token_id, pos, trace_ix, tclass = (
        cache["probs"], cache["token_id"], cache["pos"], cache["trace_ix"], cache["tclass"]
    )
    n_layers = probs.shape[1]
    tmeta = {t["trace_id"]: t for t in meta["traces"]}
    routine = sorted([t for t in meta["traces"] if t["arm_class"] in ("clean", "benign")], key=lambda t: t["trace_id"])
    fit_ix = {t["trace_ix"] for i, t in enumerate(routine) if i % 2 == 0}
    mask_fit = np.isin(trace_ix, list(fit_ix))
    fit_probs = probs[mask_fit].astype(np.float32)
    class_centroid = np.zeros((len(classes), n_layers, 64), np.float32)
    for ci in range(len(classes)):
        sel = tclass[mask_fit] == ci
        if sel.any():
            class_centroid[ci] = fit_probs[sel].mean(0)
    routine_ids = set(token_id[np.isin(trace_ix, [t["trace_ix"] for t in routine])].tolist())

    batches = rio.load_core()
    traces = {t.trace_id: t for b in batches.values() for t in b}
    fit_mat = np.concatenate(
        [selection_rate_windows(traces[t["trace_id"]].top_k_ids, W8, BAND)[1].numpy() for t in routine if t["trace_ix"] in fit_ix]
    )
    mu, sd = fit_mat.mean(0), fit_mat.std(0) + 1e-3
    centre = ((fit_mat - mu) / sd).mean(0)
    fit_g1 = ((((fit_mat - mu) / sd) - centre) ** 2).sum(1)

    drifts = [t for t in meta["traces"] if t["arm_class"] == "drift" and t["product_onset"] is not None]

    def row(t):
        o = t["product_onset"]
        hi = min(o + WINDOW, t["token_count"])
        m = (trace_ix == t["trace_ix"]) & (pos >= o) & (pos < hi)
        ends, w = selection_rate_windows(traces[t["trace_id"]].top_k_ids, W8, BAND)
        e = ends.numpy()
        keep = (e >= o + W8 - 1) & (e < hi)
        raw = w.numpy()[keep]
        z = (raw - mu) / sd - centre
        d_raw = raw.mean(0) - mu
        delta = z.mean(0)
        x = probs[m].astype(np.float32)
        jc = jsd(x, class_centroid[tclass[m]])[:, BAND].mean(1)
        ids = token_id[m]
        novel = np.array([int(i) not in routine_ids for i in ids])
        nrows = np.nonzero(m)[0][novel]
        nvals = []
        for r in nrows:
            xx = probs[r].astype(np.float32)
            nvals.append(float(jsd(np.broadcast_to(xx, (len(classes),) + xx.shape), class_centroid)[:, BAND].mean(1).min()))
        cm = Counter(tclass[m].tolist())
        return {
            "trace_id": t["trace_id"],
            "domain": t["label_domain"],
            "workflow": t["workflow"],
            "channel": t["channel"],
            "product_onset": o,
            "token_count": t["token_count"],
            "n_windows_w8": int(keep.sum()),
            "g1_median_w8": float(np.median((z ** 2).sum(1))),
            "raw_shift_norm": float(np.linalg.norm(d_raw)),
            "z_shift_norm": float(np.linalg.norm(delta)),
            "eff_routine_sd_along_shift": float(np.linalg.norm(d_raw) / (np.linalg.norm(delta) + 1e-12)),
            "js_class_median": float(np.median(jc)),
            "frac_novel_ids": float(novel.mean()),
            "novel_nearest_class_js_median": float(np.median(nvals)) if nvals else None,
            "class_mix": {classes[c]: round(n / int(m.sum()), 4) for c, n in sorted(cm.items())},
        }

    rows = {t["trace_id"]: row(t) for t in drifts}
    prog = [t for t in drifts if t["label_domain"] == "programming"]
    other = [t for t in drifts if t["label_domain"] != "programming"]

    matches = {}
    for t in prog:
        same_wf = [o for o in other if o["workflow"] == t["workflow"]]
        pool = same_wf if same_wf else other
        best = min(pool, key=lambda o: abs(o["product_onset"] - t["product_onset"]))
        matches[t["trace_id"]] = {"match": best["trace_id"], "same_workflow": bool(same_wf)}

    out = {
        "routine_fit_g1": {"median": float(np.median(fit_g1)), "q90": float(np.quantile(fit_g1, 0.9)), "q99": float(np.quantile(fit_g1, 0.99))},
        "rows": rows,
        "matches": matches,
    }
    (OUT / "token_identity_pertrace.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print("routine fit g1 median/q90/q99:", {k: round(v, 1) for k, v in out["routine_fit_g1"].items()})
    hdr = "trace_id | onset | g1 | raw|d| | z|d| | effsd | jsclass | novel% | novelJS"
    print(hdr)
    for t in prog:
        r = rows[t["trace_id"]]
        m = rows[matches[t["trace_id"]]["match"]]
        for tag, rr in (("CODE", r), ("  vs " + m["domain"], m)):
            print(f"{tag:>16} {rr['trace_id'][:44]:44} {rr['product_onset']:4d} {rr['g1_median_w8']:8.1f} {rr['raw_shift_norm']:6.2f} {rr['z_shift_norm']:7.2f} {rr['eff_routine_sd_along_shift']:6.3f} {rr['js_class_median']:7.4f} {rr['frac_novel_ids']:6.3f} {('%.4f'%rr['novel_nearest_class_js_median']) if rr['novel_nearest_class_js_median'] else 'n/a'}")


if __name__ == "__main__":
    main()
