"""Task 4: anchor-token check. For token ids with >=10 occurrences in both DRIFT-POST and R (pooled over
both batches), the within-token AUROC of the raw-p diagonal-LDA score (w fitted on the OTHER batch than the
token occurrence being scored). Post-hoc pilot diagnostic on development data; not a result."""
import numpy as np, torch
from common import *

data = {b: stack_batch(load_batch(b)) for b in ("b1", "b2")}
tables = {b: routine_tables(data[b], "p") for b in data}
other = {"b1": "b2", "b2": "b1"}
MIN_N = 10

# scores per token in each batch, using w fitted on the other batch (leave-one-batch)
scores = {}
for b in data:
    o = other[b]
    E_o_on_o = shrunk_expectation(tables[o], data[o]["ids"])
    E_o_on_b = shrunk_expectation(tables[o], data[b]["ids"])
    feats_o = {"raw_p": data[o]["p"], "residual": data[o]["p"] - E_o_on_o}
    feats_b = {"raw_p": data[b]["p"], "residual": data[b]["p"] - E_o_on_b}
    scores[b] = {}
    for f in feats_o:
        w = diag_lda(feats_o[f][data[o]["grp"] == 1], feats_o[f][data[o]["grp"] == 0])
        for band in ("middle", "all"):
            scores[b][(f, band)] = band_scores(feats_b[f], w, band)

ids = np.concatenate([data[b]["ids"].numpy() for b in ("b1", "b2")])
grp = np.concatenate([data[b]["grp"].numpy() for b in ("b1", "b2")])
texts = {}
for b in ("b1", "b2"):
    for r in load_batch(b):
        for i, t in zip(r["token_ids"].tolist(), r["token_texts"]):
            texts.setdefault(i, t)

out = {"note": "post-hoc pilot diagnostic on development data; not a result",
       "criterion": f"token id with >= {MIN_N} occurrences in DRIFT_POST and >= {MIN_N} in R, pooled over B1(brief=absent)+B2",
       "scoring": "raw-p (and residual) diagonal-LDA score; each occurrence scored with w fitted on the other batch",
       "per_key": {}, "tokens": []}
uniq = np.unique(ids)
pos_counts = {i: int(((ids == i) & (grp == 1)).sum()) for i in uniq}
neg_counts = {i: int(((ids == i) & (grp == 0)).sum()) for i in uniq}
anchor_ids = [int(i) for i in uniq if pos_counts[i] >= MIN_N and neg_counts[i] >= MIN_N]
out["n_anchor_ids"] = len(anchor_ids)
out["anchor_pos_token_share"] = float(sum(pos_counts[i] for i in anchor_ids) / max(1, (grp == 1).sum()))
for key in scores["b1"]:
    s = np.concatenate([scores[b][key] for b in ("b1", "b2")])
    aucs = []
    for i in anchor_ids:
        a = auroc(s[(ids == i) & (grp == 1)], s[(ids == i) & (grp == 0)])
        aucs.append(a)
        if key == ("raw_p", "middle"):
            out["tokens"].append({"id": i, "text": texts.get(i), "n_pos": pos_counts[i], "n_neg": neg_counts[i], "auroc_raw_middle": a})
        elif key == ("residual", "middle"):
            for t in out["tokens"]:
                if t["id"] == i: t["auroc_residual_middle"] = a
    aucs = np.array(aucs)
    # pooled-token AUROC over anchor tokens (mixed identities) for comparison
    m = np.isin(ids, anchor_ids)
    out["per_key"][f"{key[0]}__{key[1]}"] = {
        "n_tokens": len(aucs), "median": float(np.median(aucs)), "q25": float(np.percentile(aucs, 25)),
        "q75": float(np.percentile(aucs, 75)), "min": float(aucs.min()), "max": float(aucs.max()),
        "frac_gt_0.5": float((aucs > 0.5).mean()), "frac_ge_0.7": float((aucs >= 0.7).mean()),
        "count_weighted_mean": float(np.average(aucs, weights=[min(pos_counts[i], neg_counts[i]) for i in anchor_ids])),
        "pooled_across_anchor_tokens_auroc": auroc(s[m & (grp == 1)], s[m & (grp == 0)]),
    }
out["tokens"].sort(key=lambda t: -t["auroc_raw_middle"])
dump("task4_anchor.json", out)
print("anchor ids", len(anchor_ids), "share of drift-post tokens", round(out["anchor_pos_token_share"], 3))
for k, v in out["per_key"].items():
    print(k, {a: round(c, 3) if isinstance(c, float) else c for a, c in v.items()})
for t in out["tokens"][:10] + out["tokens"][-10:]:
    print(t)
