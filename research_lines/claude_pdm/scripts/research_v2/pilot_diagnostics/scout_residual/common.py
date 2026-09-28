"""Shared helpers for the token-identity residual scout (post-hoc pilot diagnostics)."""
import json
from pathlib import Path
import numpy as np
import torch

S = Path(__file__).resolve().parent
CACHE = S / "cache"
RESULTS = S / "results"
RESULTS.mkdir(exist_ok=True)
V = 50304
M_SHRINK = 5.0
BANDS = {"early": list(range(0, 5)), "middle": list(range(5, 12)),
         "late": list(range(12, 16)), "all": list(range(16))}
POST_OFFSET = 8       # DRIFT-POST tokens: index >= boundary + 8
WIN = 16


def load_batch(batch):
    rows = torch.load(CACHE / f"{batch}_decode.pt", weights_only=False)
    if batch == "b1":
        rows = [r for r in rows if r["brief"] == "absent"]
    return rows


def token_group(row):
    """Return per-token group codes: 0 = routine (clean/benign/resisted), 1 = drift-post,
    2 = drift pre/transition (boundary <= t < boundary+8 or t < boundary), -1 unused."""
    T = row["p"].shape[1]
    g = np.full(T, -1, dtype=np.int64)
    if row["arm"] in ("clean", "benign_control") or (row["arm"] == "attack" and not row["positive"]):
        g[:] = 0
    elif row["positive"]:
        b = row["boundary"]
        g[:] = 2
        g[b + POST_OFFSET:] = 1
    return g


def stack_batch(rows):
    """Concatenate tokens: returns dict of arrays over all tokens of the subset."""
    ps, inds, ids, tr, pos, grp = [], [], [], [], [], []
    for i, r in enumerate(rows):
        T = r["p"].shape[1]
        ps.append(r["p"].permute(1, 0, 2))      # [T,16,64]
        inds.append(r["ind"].permute(1, 0, 2))
        ids.append(r["token_ids"])
        tr.append(torch.full((T,), i, dtype=torch.long))
        pos.append(torch.arange(T))
        grp.append(torch.tensor(token_group(r)))
    return {
        "p": torch.cat(ps).contiguous(), "ind": torch.cat(inds).contiguous(),
        "ids": torch.cat(ids), "trace": torch.cat(tr), "pos": torch.cat(pos), "grp": torch.cat(grp),
    }


def routine_tables(data, feature):
    """Per-token-id sums and counts of feature over routine tokens (grp==0)."""
    mask = data["grp"] == 0
    x = data[feature][mask]
    ids = data["ids"][mask]
    s = torch.zeros(V, 16, 64, dtype=torch.float32)
    s.index_add_(0, ids, x)
    c = torch.zeros(V, dtype=torch.float64)
    c.index_add_(0, ids, torch.ones(len(ids), dtype=torch.float64))
    mu = x.mean(0)
    return {"sum": s, "count": c, "mu": mu, "n": int(mask.sum())}


def shrunk_expectation(table, ids, mu=None, m=M_SHRINK):
    """E[x|token] = (n*mean_tok + m*mu)/(n+m), evaluated at ids -> [N,16,64]."""
    mu = table["mu"] if mu is None else mu
    n = table["count"][ids].float()[:, None, None]
    s = table["sum"][ids]
    return (s + m * mu[None]) / (n + m)


def augment_table(table, prefill, feature):
    """Add prefill accumulators (all 240 traces, all roles) to a routine table."""
    key = "sum_p" if feature == "p" else "sum_ind"
    return {"sum": table["sum"] + prefill[key], "count": table["count"] + prefill["count"],
            "mu": table["mu"], "n": table["n"] + int(prefill["n_tokens"])}


def auroc(pos, neg):
    """Mann-Whitney AUROC with tie handling (pure numpy)."""
    pos = np.asarray(pos, dtype=np.float64); neg = np.asarray(neg, dtype=np.float64)
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    allv = np.concatenate([pos, neg])
    order = np.argsort(allv, kind="mergesort")
    ranks = np.empty(len(allv), dtype=np.float64)
    sorted_v = allv[order]
    i = 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and sorted_v[j + 1] == sorted_v[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    rp = ranks[:len(pos)].sum()
    return float((rp - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg)))


def diag_lda(x_pos, x_neg, eps=1e-6):
    """Diagonal LDA direction over [N,16,64] features: (mean_pos - mean_neg)/pooled_std."""
    mp, mn = x_pos.mean(0), x_neg.mean(0)
    vp, vn = x_pos.var(0, unbiased=False), x_neg.var(0, unbiased=False)
    return (mp - mn) / (torch.sqrt(0.5 * (vp + vn)) + eps)


def band_scores(x, w, band):
    """Token scores: sum over band layers and experts of w * x."""
    L = torch.tensor(BANDS[band])
    return (x[:, L, :] * w[L][None]).sum(dim=(1, 2)).numpy()


def causal_window_means(scores, trace, width=WIN, full_only=True):
    """Per-token causal window mean. If full_only, returns nan for positions < width-1."""
    out = np.full(len(scores), np.nan)
    trace = trace.numpy()
    starts = np.flatnonzero(np.r_[True, trace[1:] != trace[:-1]])
    ends = np.r_[starts[1:], len(scores)]
    for s, e in zip(starts, ends):
        seg = scores[s:e]
        cs = np.concatenate([[0.0], np.cumsum(seg)])
        T = e - s
        t = np.arange(T)
        lo = np.maximum(0, t - width + 1)
        m = (cs[t + 1] - cs[lo]) / (t + 1 - lo)
        if full_only:
            m[t < width - 1] = np.nan
        out[s:e] = m
    return out


def dump(name, obj):
    path = RESULTS / name
    path.write_text(json.dumps(obj, indent=1, default=float))
    print("wrote", path)
