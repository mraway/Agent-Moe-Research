"""Task 1 (vocabulary coverage) and Task 2 (token-identity explanatory power).
Post-hoc pilot diagnostic on development data B1/B2; not a result."""
import numpy as np, torch
from common import *

data = {b: stack_batch(load_batch(b)) for b in ("b1", "b2")}
prefill = {b: torch.load(CACHE / f"{b}_prefill_tables.pt", weights_only=False) for b in ("b1", "b2")}
tables = {b: {f: routine_tables(data[b], f) for f in ("p", "ind")} for b in data}
aug = {b: {f: augment_table(tables[b][f], prefill[b], f) for f in ("p", "ind")} for b in data}

out = {"note": "post-hoc pilot diagnostic on development data; not a result",
       "definitions": {"R": "decode tokens of clean+benign_control+resisted-attack traces (B1: brief==absent only)",
                       "DRIFT_POST": "decode tokens of drift traces with index >= boundary+8",
                       "prefill_pool": "all prefill tokens (all roles) of all 240 traces of the pool batch"},
       "coverage": {}, "explained": {}}

for b in data:
    d = data[b]
    out["coverage"][f"R_{b}"] = {
        "tokens": int((d["grp"] == 0).sum()),
        "distinct_ids": int(len(torch.unique(d["ids"][d["grp"] == 0]))),
        "drift_post_tokens": int((d["grp"] == 1).sum()),
        "drift_post_distinct_ids": int(len(torch.unique(d["ids"][d["grp"] == 1]))),
        "prefill_tokens": int(prefill[b]["n_tokens"]),
        "prefill_distinct_ids": int((prefill[b]["count"] > 0).sum()),
        "R_plus_prefill_distinct_ids": int((aug[b]["p"]["count"] > 0).sum()),
    }

for x, y in (("b1", "b2"), ("b2", "b1")):
    ids_y = data[y]["ids"][data[y]["grp"] == 1]
    for label, tab in (("R", tables[x]["p"]), ("R_plus_prefill", aug[x]["p"])):
        c = tab["count"][ids_y]
        out["coverage"][f"DRIFT_POST_{y}_vs_pool_{x}_{label}"] = {
            "n_tokens": int(len(ids_y)),
            **{f"frac_count_ge_{k}": float((c >= k).float().mean()) for k in (1, 5, 20)},
            "distinct_ids_in_drift_post": int(len(torch.unique(ids_y))),
            "distinct_ids_covered_ge1": int(len(torch.unique(ids_y[c >= 1]))),
        }
    # also routine-vs-routine coverage as a reference point
    ids_ry = data[y]["ids"][data[y]["grp"] == 0]
    c = tables[x]["p"]["count"][ids_ry]
    out["coverage"][f"R_{y}_vs_pool_{x}_R"] = {
        "n_tokens": int(len(ids_ry)),
        **{f"frac_count_ge_{k}": float((c >= k).float().mean()) for k in (1, 5, 20)}}

# Task 2: variance explained per layer, fit on R(X), evaluate on R(Y) (and on DRIFT_POST(Y) for reference)
for x, y in (("b1", "b2"), ("b2", "b1")):
    for f in ("p", "ind"):
        mu = tables[x][f]["mu"]  # global routine mean of the fitting batch (decode R only)
        for label, tab in (("R", tables[x][f]), ("R_plus_prefill", aug[x][f])):
            for eval_grp, gname in ((0, "R"), (1, "DRIFT_POST")):
                mask = data[y]["grp"] == eval_grp
                xv = data[y][f][mask]
                E = shrunk_expectation(tab, data[y]["ids"][mask], mu=mu)
                ss_res = ((xv - E) ** 2).sum(dim=(0, 2))
                ss_tot = ((xv - mu[None]) ** 2).sum(dim=(0, 2))
                ve = (1 - ss_res / ss_tot).tolist()
                out["explained"][f"fit_{x}_{label}__eval_{y}_{gname}__{f}"] = {
                    "per_layer": ve,
                    "bands": {bn: float(1 - ss_res[BANDS[bn]].sum() / ss_tot[BANDS[bn]].sum()) for bn in BANDS},
                    "n_tokens": int(mask.sum()),
                }
dump("task12_coverage_explained.json", out)
for k, v in out["coverage"].items():
    print(k, v)
for k, v in out["explained"].items():
    print(k, "bands", {a: round(c, 3) for a, c in v["bands"].items()})
    print("   per layer", [round(c, 3) for c in v["per_layer"]])
