"""Task 3: signal retention after token-identity residualization. Diagonal LDA fitted on batch X,
evaluated on batch Y; per-token AUROC and 16-token causal window AUROC; layer bands.
Post-hoc pilot diagnostic on development data B1/B2; not a result."""
import numpy as np, torch
from common import *

data = {b: stack_batch(load_batch(b)) for b in ("b1", "b2")}
prefill = {b: torch.load(CACHE / f"{b}_prefill_tables.pt", weights_only=False) for b in ("b1", "b2")}
rows = {b: load_batch(b) for b in ("b1", "b2")}


def window_labels(d, rowsb):
    """Window label per token-end t: 1 = drift window fully post-boundary (start>=boundary, so end>=boundary+15),
    0 = routine-trace full window, -1 otherwise."""
    lab = np.full(len(d["pos"]), -1)
    pos = d["pos"].numpy(); tr = d["trace"].numpy(); grp = d["grp"].numpy()
    for i, r in enumerate(rowsb):
        m = tr == i
        if r["positive"]:
            b = r["boundary"]
            lab[m & (pos - (WIN - 1) >= b)] = 1
        elif grp[m][0] == 0:
            lab[m & (pos >= WIN - 1)] = 0
    return lab


def features(d, table_p, mu):
    E = shrunk_expectation(table_p, d["ids"], mu=mu)
    return {"raw_p": d["p"], "residual": d["p"] - E, "E_token_only": E}


results = {"note": "post-hoc pilot diagnostic on development data; not a result",
           "window_definition": "16-token causal window; positive windows have start >= boundary (end >= boundary+15); "
                                "negative windows are all full windows in routine traces (clean+benign+resisted)",
           "token_definition": "positive tokens index >= boundary+8 in drift traces; negatives all routine tokens",
           "rows": []}
for x, y in (("b1", "b2"), ("b2", "b1")):
    for pool_label in ("R", "R_plus_prefill"):
        tab = routine_tables(data[x], "p")
        mu = tab["mu"]
        if pool_label == "R_plus_prefill":
            tab = augment_table(tab, prefill[x], "p")
        fx = features(data[x], tab, mu)
        fy = features(data[y], tab, mu)
        wl_y = window_labels(data[y], rows[y])
        for fname in ("raw_p", "residual", "E_token_only"):
            w = diag_lda(fx[fname][data[x]["grp"] == 1], fx[fname][data[x]["grp"] == 0])
            for band in BANDS:
                s = band_scores(fy[fname], w, band)
                g = data[y]["grp"].numpy()
                tok_auc = auroc(s[g == 1], s[g == 0])
                wm = causal_window_means(s, data[y]["trace"])
                win_auc = auroc(wm[wl_y == 1], wm[wl_y == 0])
                results["rows"].append({"fit": x, "eval": y, "pool": pool_label, "feature": fname, "band": band,
                                        "token_auroc": tok_auc, "window16_auroc": win_auc,
                                        "n_pos_tokens": int((g == 1).sum()), "n_neg_tokens": int((g == 0).sum()),
                                        "n_pos_windows": int((wl_y == 1).sum()), "n_neg_windows": int((wl_y == 0).sum())})
        # in-sample sanity (fit and eval on X) for the raw feature only, to show the optimism gap
        for fname in ("raw_p", "residual"):
            w = diag_lda(fx[fname][data[x]["grp"] == 1], fx[fname][data[x]["grp"] == 0])
            s = band_scores(fx[fname], w, "all"); g = data[x]["grp"].numpy()
            results.setdefault("in_sample_all_layers", []).append(
                {"fit_eval": x, "pool": pool_label, "feature": fname, "token_auroc": auroc(s[g == 1], s[g == 0])})
dump("task3_retention.json", results)
print(f"{'fit->eval':10s} {'pool':15s} {'feature':13s} {'band':7s} {'tokAUC':>7s} {'winAUC':>7s}")
for r in results["rows"]:
    print(f"{r['fit']}->{r['eval']:5s} {r['pool']:15s} {r['feature']:13s} {r['band']:7s} {r['token_auroc']:7.3f} {r['window16_auroc']:7.3f}")
print(results["in_sample_all_layers"])
