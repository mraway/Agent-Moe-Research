"""Supplement to task 3: (a) retention restricted to tokens whose id is covered (count>=5) in the fitting
batch's routine pool, versus uncovered tokens; (b) pre-boundary drift-trace tokens vs routine, to interpret
the pre-boundary rise in the timing curve. Post-hoc pilot diagnostic on development data; not a result."""
import numpy as np, torch
from common import *

data = {b: stack_batch(load_batch(b)) for b in ("b1", "b2")}
rows = {b: load_batch(b) for b in ("b1", "b2")}
out = {"note": "post-hoc pilot diagnostic on development data; not a result", "covered_split": [], "pre_boundary": []}
for x, y in (("b1", "b2"), ("b2", "b1")):
    tab = routine_tables(data[x], "p"); mu = tab["mu"]
    fx = {"raw_p": data[x]["p"], "residual": data[x]["p"] - shrunk_expectation(tab, data[x]["ids"], mu=mu)}
    Ey = shrunk_expectation(tab, data[y]["ids"], mu=mu)
    fy = {"raw_p": data[y]["p"], "residual": data[y]["p"] - Ey, "E_token_only": Ey}
    fx["E_token_only"] = shrunk_expectation(tab, data[x]["ids"], mu=mu)
    cnt = tab["count"][data[y]["ids"]].numpy()
    g = data[y]["grp"].numpy(); pos = data[y]["pos"].numpy(); tr = data[y]["trace"].numpy()
    bnd = np.array([rows[y][i]["boundary"] if rows[y][i]["positive"] else -1 for i in tr])
    pre = (g == 2) & (pos < bnd)              # drift-trace tokens strictly before the boundary
    trans = (g == 2) & (pos >= bnd)           # boundary <= t < boundary+8
    for fname in ("raw_p", "residual", "E_token_only"):
        w = diag_lda(fx[fname][data[x]["grp"] == 1], fx[fname][data[x]["grp"] == 0])
        for band in ("middle", "all"):
            s = band_scores(fy[fname], w, band)
            for label, m in (("covered_ge5", cnt >= 5), ("uncovered_lt5", cnt < 5), ("covered_ge20", cnt >= 20)):
                out["covered_split"].append({"fit": x, "eval": y, "feature": fname, "band": band, "subset": label,
                                             "token_auroc": auroc(s[(g == 1) & m], s[(g == 0) & m]),
                                             "n_pos": int(((g == 1) & m).sum()), "n_neg": int(((g == 0) & m).sum())})
            out["pre_boundary"].append({"fit": x, "eval": y, "feature": fname, "band": band,
                                        "pre_boundary_vs_routine_auroc": auroc(s[pre], s[g == 0]),
                                        "transition_0_7_vs_routine_auroc": auroc(s[trans], s[g == 0]),
                                        "post_vs_pre_boundary_auroc": auroc(s[g == 1], s[pre]),
                                        "n_pre": int(pre.sum()), "n_trans": int(trans.sum())})
dump("task3b_covered_and_preboundary.json", out)
for r in out["covered_split"]:
    print(f"{r['fit']}->{r['eval']} {r['feature']:13s} {r['band']:7s} {r['subset']:14s} tokAUC={r['token_auroc']:.3f} npos={r['n_pos']} nneg={r['n_neg']}")
for r in out["pre_boundary"]:
    print(f"{r['fit']}->{r['eval']} {r['feature']:13s} {r['band']:7s} pre_vs_R={r['pre_boundary_vs_routine_auroc']:.3f} trans_vs_R={r['transition_0_7_vs_routine_auroc']:.3f} post_vs_pre={r['post_vs_pre_boundary_auroc']:.3f} npre={r['n_pre']} ntrans={r['n_trans']}")
