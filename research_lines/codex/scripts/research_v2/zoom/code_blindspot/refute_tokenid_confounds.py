"""REFUTATION lens, part 3: confound battery for the CAND-A geometry statements
(routine subset, fit/held split, batch, anchor, onset=0, truncation) and a check that
the "top-variance subspace" result is not a decile-boundary artefact.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np

from research_v2 import io as rio
from research_v2.features import selection_rate_windows

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
LABELS = REPO / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"
W, LAYERS, FLOOR, SPAN = 8, tuple(range(5, 16)), 1e-3, 48


def main() -> None:
    lab = {json.loads(l)["trace_id"]: json.loads(l)
           for l in LABELS.read_text(encoding="utf-8").splitlines() if l.strip()}
    batches = rio.load_core()
    traces = [t for b in ("b1", "b2") for t in batches[b]]
    cache = {}
    for t in traces:
        e, w = selection_rate_windows(t.top_k_ids, W, LAYERS)
        cache[t.trace_id] = (e.numpy(), w.numpy().astype(np.float64))

    routine = sorted([t for t in traces if rio.arm_class(t) in ("clean", "benign")], key=lambda t: t.trace_id)
    drift = [t for t in traces if rio.arm_class(t) == "drift"]
    doms = {}
    for t in drift:
        doms.setdefault(lab[t.trace_id]["domain"], []).append(t)

    def region(t, anchor="product"):
        row = lab[t.trace_id]
        o = int(row["product_onset"] if anchor == "product" else row["evidence_onset"])
        T = int(t.token_ids.shape[0]); hi = min(o + SPAN, T)
        e, w = cache[t.trace_id]
        return w[(e >= o + W - 1) & (e <= hi - 1)]

    def run(fit_traces, tag, anchor="product"):
        X = np.concatenate([cache[t.trace_id][1] for t in fit_traces])
        mu = X.mean(0); sd = X.std(0, ddof=1) + FLOOR
        gfit = (((X - mu) / sd) ** 2).sum(1)
        out = {"fit_traces": len(fit_traces), "fit_windows": int(X.shape[0]),
               "routine_median": float(np.median(gfit)), "domains": {}, "code_traces": {}}
        for d, ts in sorted(doms.items()):
            x = np.concatenate([region(t, anchor) for t in ts])
            dd = x.mean(0) - mu; zz = dd / sd
            g = (((x - mu) / sd) ** 2).sum(1)
            out["domains"][d] = {"eff_sd": float(np.linalg.norm(dd) / np.linalg.norm(zz)),
                                 "z_shift": float(np.linalg.norm(zz)),
                                 "raw_shift": float(np.linalg.norm(dd)),
                                 "g_pctile": float((gfit < np.median(g)).mean())}
        for t in doms["programming"]:
            x = region(t, anchor); dd = x.mean(0) - mu; zz = dd / sd
            g = (((x - mu) / sd) ** 2).sum(1)
            out["code_traces"][t.trace_id] = {"eff_sd": float(np.linalg.norm(dd) / np.linalg.norm(zz)),
                                              "z_shift": float(np.linalg.norm(zz)),
                                              "g_pctile": float((gfit < np.median(g)).mean())}
        return tag, out

    variants = [
        run(routine[0::2], "fit=even(theirs)"),
        run(routine[1::2], "fit=odd(swapped)"),
        run(routine, "fit=all240"),
        run([t for t in routine if rio.arm_class(t) == "clean"], "fit=clean120"),
        run([t for t in routine if rio.arm_class(t) == "benign"], "fit=benign120"),
        run([t for t in routine if t.batch == "b1"], "fit=b1routine"),
        run([t for t in routine if t.batch == "b2"], "fit=b2routine"),
        run(routine[0::2], "fit=even,anchor=evidence", anchor="evidence"),
    ]
    res = {tag: out for tag, out in variants}

    # top-variance subspace, several cutoffs, on the all-240 fit
    X = np.concatenate([cache[t.trace_id][1] for t in routine[0::2]])
    mu = X.mean(0); sd = X.std(0, ddof=1) + FLOOR
    sub = {}
    for name, mask in [("top5pct_35", sd >= np.quantile(sd, .95)),
                       ("top10pct_70", sd >= np.quantile(sd, .90)),
                       ("top20pct_141", sd >= np.quantile(sd, .80)),
                       ("sd_gt_0.25", sd > 0.25),
                       ("bottom10pct", sd <= np.quantile(sd, .10))]:
        gfit = (((X[:, mask] - mu[mask]) / sd[mask]) ** 2).sum(1)
        sub[name] = {"n_dims": int(mask.sum()), "domains": {}}
        for d, ts in sorted(doms.items()):
            x = np.concatenate([region(t) for t in ts])
            g = (((x[:, mask] - mu[mask]) / sd[mask]) ** 2).sum(1)
            sub[name]["domains"][d] = {"ratio": float(np.median(g) / np.median(gfit)),
                                       "pctile": float((gfit < np.median(g)).mean()),
                                       "above_q90": float((g > np.quantile(gfit, .9)).mean())}
    res["subspaces"] = sub

    # trace bookkeeping: truncation / onset
    book = {}
    for t in drift:
        T = int(t.token_ids.shape[0]); o = int(lab[t.trace_id]["product_onset"])
        book[t.trace_id] = {"T": T, "onset": o, "window_tokens": min(o + SPAN, T) - o,
                            "n_windows": int(region(t).shape[0]), "domain": lab[t.trace_id]["domain"]}
    res["bookkeeping"] = book

    (OUT / "refute_tokenid_confounds.json").write_text(json.dumps(res, indent=1), encoding="utf-8")

    print(f"{'variant':26s} " + " ".join(f"{d[:8]:>9s}" for d in sorted(doms)))
    for tag, out in variants:
        print(f"{tag:26s} " + " ".join(f"{out['domains'][d]['eff_sd']:9.3f}" for d in sorted(doms)), " <- eff_sd")
    for tag, out in variants:
        print(f"{tag:26s} " + " ".join(f"{out['domains'][d]['g_pctile']:9.3f}" for d in sorted(doms)), " <- g pctile")
    print("\nsubspaces (median-g ratio to routine / window pctile / frac above routine q90):")
    for name, e in sub.items():
        print(f"  {name} (D={e['n_dims']})")
        for d, v in sorted(e["domains"].items(), key=lambda kv: -kv[1]["ratio"]):
            print(f"     {d:20s} ratio={v['ratio']:8.3f} pctile={v['pctile']:.3f} aboveq90={v['above_q90']:.3f}")
    print("\ntruncation check (code):")
    for k, v in book.items():
        if v["domain"] == "programming":
            print("  ", k[:40], v)


if __name__ == "__main__":
    main()
