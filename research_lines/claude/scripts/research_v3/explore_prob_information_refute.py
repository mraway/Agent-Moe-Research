"""EXPLORATORY / DIAGNOSTIC: adversarial re-derivation of ``explore_prob_information``.

Independent code path (no import of the other script) that recomputes, from the raw cached
router softmax and top-8 ids, the three headline items of
``docs/research_v3/explore_prob_information.md``:

 1. gate flatness (per-token statistics per layer),
 2. least-squares P ~ S per layer fitted on b2 routine, R2 on held-out routine vs programming,
 3. whitened-distance AUC of programming drift windows vs held-out routine windows on the
    indicator S, the softmax P and the residual rP = P - P_hat(S),

and then attacks them with the checks the original did not run:

 * data hygiene: float16 sum-to-1 error, cached top-8 set vs argtop-8 of the float16 softmax,
   h384 replays being bit-identical to b2 on tokens < 192 (duplicated positives), windows
   straddling the completion boundary, decode-length / token-position confound,
 * the position confound: routine traces are short (median 70-87 tokens), drift traces run to
   the horizon, and the residual distance is evaluated at positions the negatives never reach,
 * the "what is the information" question: is the residual separation carried by the known
   scalar concentration statistics (mass inside the top-8, entropy, top-1 share) that the
   lead synthesis already found to be common to all drift and unstable across batches,
 * cross-pool thresholds: q95 / q99 of the residual distance per routine pool, the FAR of one
   routine pool at another pool's q95, and the resist-arm / pre-boundary window behaviour,
 * whitening on the evaluation pool and floor sensitivity.

Development data only (b1, b2, h384, c1).  Deterministic (seed 0 for the cluster bootstrap).
Writes ``artifacts/agent_v2/research_v3/explore_prob_information_refute/{results.json,tables.md}``.
"""

from __future__ import annotations

import json
import math
import time
from collections import OrderedDict, defaultdict
from pathlib import Path

import numpy as np
import torch

from research_v2 import io as rio

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "artifacts" / "agent_v2" / "research_v3" / "explore_prob_information_refute"
W = 8
SEED = 0
N_BOOT = 200
L_ML = list(range(5, 16))
L_ALL = list(range(16))
FLOOR_S = 1e-3
FLOOR_P = 1.25e-4
PROG = "programming"
SCALARS = ("mass8", "ent8", "ent64", "top1")


# ---------------------------------------------------------------------------
# per-token representations and windows
# ---------------------------------------------------------------------------
def token_level(trace):
    p = trace.probabilities().float()  # [16,T,64]
    idx = trace.top_k_ids
    s = torch.zeros_like(p).scatter_(2, idx, 1.0)
    g = torch.gather(p, 2, idx)  # [16,T,8]
    mass = g.sum(-1)
    qn = g / mass.unsqueeze(-1)
    ent8 = -(qn * qn.clamp_min(1e-12).log()).sum(-1) / math.log(8.0)
    ent64 = -(p * p.clamp_min(1e-12).log()).sum(-1) / math.log(64.0)
    top1 = qn.max(-1).values
    scal = torch.stack([mass, ent8, ent64, top1], -1)  # [16,T,4]
    return p, s, scal


def wmean(x: torch.Tensor) -> torch.Tensor:
    """[16,T,D] -> [T-W+1,16,D] window means (window i covers tokens i..i+W-1)."""
    c = torch.cat((torch.zeros_like(x[:, :1]), x.cumsum(1)), 1)
    return ((c[:, W:] - c[:, :-W]) / W).permute(1, 0, 2).contiguous()


class Store:
    def __init__(self):
        self.P, self.S, self.SC = [], [], []
        self.pool, self.owner, self.batch, self.end, self.domain, self.pos_rel = [], [], [], [], [], []
        self.scen = []

    def add(self, trace, pool, sel, p, s, sc, cb):
        n = int(sel.size)
        if n == 0:
            return
        self.P.append(wmean(p)[sel].numpy())
        self.S.append(wmean(s)[sel].numpy())
        self.SC.append(wmean(sc)[sel].numpy())
        self.pool += [pool] * n
        self.owner += [trace.trace_id] * n
        self.batch += [trace.batch] * n
        self.domain += [trace.domain] * n
        self.end += list((sel + W - 1).astype(int))
        self.pos_rel += list((sel + W - 1 - (cb if cb is not None else 0)).astype(int))

    def finish(self):
        self.P = np.concatenate(self.P, 0)
        self.S = np.concatenate(self.S, 0)
        self.SC = np.concatenate(self.SC, 0)
        for k in ("pool", "owner", "batch", "domain", "end", "pos_rel"):
            setattr(self, k, np.asarray(getattr(self, k)))
        return self


def build():
    core = rio.load_core()
    pools = {"b1": core["b1"], "b2": core["b2"], "h384": rio.load_h384(), "c1": rio.load_c1()}
    st = Store()
    flat = defaultdict(lambda: [torch.zeros(16, 4), 0])
    hyg = {"sum_err_max": 0.0, "set_mismatch": [0, 0], "set_mismatch_massdiff_max": 0.0,
           "straddle_windows": 0, "drift_windows": 0}
    lengths = defaultdict(list)
    prefix = {}
    for b, traces in pools.items():
        for t in traces:
            cls = rio.arm_class(t)
            if t.token_count < W:
                continue
            p, s, sc = token_level(t)
            T = t.token_count
            ends = np.arange(W - 1, T)
            lengths[f"{b}_{cls}"].append(T)
            if b == "b1" and cls in ("clean", "benign"):
                prefix[t.trace_id] = rio.decode_text(t.token_ids[:14].tolist()).replace("\n", " ")[:60]
            hyg["sum_err_max"] = max(hyg["sum_err_max"], float((p.sum(-1) - 1).abs().max()))
            if cls in ("clean", "benign"):
                pool = "c1" if b == "c1" else f"{b}_routine"
                sel = np.arange(0, T - W + 1, 2)
                st.add(t, pool, sel, p, s, sc, None)
                flat[pool][0] += sc.sum(1)
                flat[pool][1] += T
                if b != "h384":
                    top = p.topk(8, -1).indices
                    s2 = torch.zeros_like(p).scatter_(2, top, 1.0)
                    bad = (s != s2).sum(-1) > 0
                    hyg["set_mismatch"][0] += int(bad.sum())
                    hyg["set_mismatch"][1] += int(bad.numel())
                    md = (torch.gather(p, 2, top).sum(-1) - torch.gather(p, 2, t.top_k_ids).sum(-1)).abs().max()
                    hyg["set_mismatch_massdiff_max"] = max(hyg["set_mismatch_massdiff_max"], float(md))
            elif cls == "drift":
                cb = int(t.completion_boundary)
                sel = np.where(ends >= cb)[0]
                if sel.size == 0:
                    sel = np.array([ends.size - 1])
                st.add(t, f"{b}_drift", sel, p, s, sc, cb)
                hyg["straddle_windows"] += int(((ends[sel] - W + 1) < cb).sum())
                hyg["drift_windows"] += int(sel.size)
                fk = f"{b}_drift_{'prog' if t.domain == PROG else 'other'}"
                flat[fk][0] += sc[:, cb:].sum(1)
                flat[fk][1] += T - cb
                pre = np.where(ends < cb)[0]  # windows entirely before the boundary
                st.add(t, f"{b}_pre", pre, p, s, sc, cb)
            elif cls == "resist":
                sel = np.arange(0, T - W + 1, 2)
                st.add(t, f"{b}_resist", sel, p, s, sc, None)
            t._probabilities = None
    flat_out = {k: (v[0] / v[1]).numpy() for k, v in flat.items()}
    flat_n = {k: v[1] for k, v in flat.items()}
    st = st.finish()
    st.prefix = prefix
    return st, flat_out, flat_n, hyg, {k: (len(v), int(np.median(v)), int(np.mean(np.array(v) >= 192) * 100)) for k, v in lengths.items()}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def auc(pos, neg):
    pos = np.asarray(pos, np.float64)
    neg = np.asarray(neg, np.float64)
    if pos.size == 0 or neg.size == 0:
        return float("nan")
    allv = np.concatenate([pos, neg])
    r = rankdata(allv)
    return float((r[: pos.size].sum() - pos.size * (pos.size + 1) / 2) / (pos.size * neg.size))


def rankdata(a):
    """Average ranks with ties (1-based), numpy only."""
    a = np.asarray(a, np.float64)
    order = np.argsort(a, kind="mergesort")
    sv = a[order]
    ranks = np.empty(a.size, np.float64)
    i = 0
    while i < a.size:
        j = i
        while j + 1 < a.size and sv[j + 1] == sv[i]:
            j += 1
        ranks[order[i:j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    return ranks


def spearman(a, b):
    ra, rb = rankdata(a), rankdata(b)
    return float(np.corrcoef(ra, rb)[0, 1])


def boot(pos, neg, pos_own, neg_own, rng, n=N_BOOT):
    po = np.asarray(pos_own)
    no = np.asarray(neg_own)
    pids = sorted(set(po))
    nids = sorted(set(no))
    pidx = {i: np.where(po == i)[0] for i in pids}
    nidx = {i: np.where(no == i)[0] for i in nids}
    vals = []
    for _ in range(n):
        ps = rng.choice(len(pids), len(pids))
        ns = rng.choice(len(nids), len(nids))
        vals.append(auc(pos[np.concatenate([pidx[pids[k]] for k in ps])], neg[np.concatenate([nidx[nids[k]] for k in ns])]))
    return [float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))]


def lstsq_fit(X, Y):
    Xa = np.concatenate([X, np.ones((X.shape[0], 1))], 1).astype(np.float64)
    coef, *_ = np.linalg.lstsq(Xa, Y.astype(np.float64), rcond=None)
    return coef


def predict(coef, X):
    return np.concatenate([X, np.ones((X.shape[0], 1))], 1).astype(np.float64) @ coef


def r2(Y, Yh):
    Y = Y.astype(np.float64)
    return float(1 - ((Y - Yh) ** 2).sum() / ((Y - Y.mean(0)) ** 2).sum())


def wdist(X, mu, sd, layers):
    """X [N,16,D] -> per-window whitened squared distance summed over layers."""
    z = (X[:, layers, :].astype(np.float64) - mu[layers]) / sd[layers]
    return (z * z).sum((1, 2))


def wdist_layer(X, mu, sd):
    z = (X.astype(np.float64) - mu) / sd
    return (z * z).sum(2)  # [N,16]


# ---------------------------------------------------------------------------
def main():
    torch.set_num_threads(8)
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    st, flat, flat_n, hyg, lengths = build()
    print(f"[build] {time.time() - t0:.0f}s, {st.P.shape[0]} windows", flush=True)
    res: dict = {"meta": {"status": "EXPLORATORY / DIAGNOSTIC, development data only; independent re-derivation",
                          "w": W, "routine_stride": 2, "drift_stride": 1, "seed": SEED, "n_boot": N_BOOT}}
    res["hygiene"] = {**hyg, "set_mismatch_rate": hyg["set_mismatch"][0] / max(hyg["set_mismatch"][1], 1),
                      "lengths(n, median, pct>=192)": lengths}

    pool = st.pool
    m = {k: pool == k for k in set(pool)}
    isprog = st.domain == PROG
    prog = ((pool == "b1_drift") | (pool == "b2_drift") | (pool == "h384_drift")) & isprog
    other = ((pool == "b1_drift") | (pool == "b2_drift") | (pool == "h384_drift")) & ~isprog
    held = m["b1_routine"] | m["c1"]
    fit = m["b2_routine"]
    # duplicate positives: h384 replays are bit-identical to b2 for tokens < 192
    dup = (st.batch == "h384") & (st.end < 192) & np.isin(st.owner, np.unique(st.owner[st.batch == "b2"]))
    prog_dedup = prog & ~dup
    res["counts"] = {"prog_windows": int(prog.sum()), "prog_windows_dedup": int(prog_dedup.sum()),
                     "prog_h384_windows_end_ge_192": int((prog & (st.end >= 192)).sum()),
                     "prog_traces": len(set(st.owner[prog])), "held_windows": int(held.sum()),
                     "fit_windows": int(fit.sum()), "other_windows": int(other.sum()),
                     "resist_windows": int(np.isin(pool, ["b1_resist", "b2_resist", "h384_resist"]).sum()),
                     "pre_boundary_windows": int(np.isin(pool, ["b1_pre", "b2_pre", "h384_pre"]).sum()),
                     "held_windows_end_ge_100": int((held & (st.end >= 100)).sum()),
                     "held_windows_end_ge_150": int((held & (st.end >= 150)).sum()),
                     "median_end_held": float(np.median(st.end[held])), "median_end_prog": float(np.median(st.end[prog]))}

    # ---- item 1 ---------------------------------------------------------------
    res["item1"] = {k: {"mass8": v[:, 0].tolist(), "ent8": v[:, 1].tolist(), "ent64": v[:, 2].tolist(), "top1": v[:, 3].tolist(),
                        "L5_15": {s: float(v[5:, i].mean()) for i, s in enumerate(SCALARS)}, "n_tokens": flat_n[k]} for k, v in flat.items()}

    # ---- item 2 ---------------------------------------------------------------
    coefs = [lstsq_fit(st.S[fit, l, :], st.P[fit, l, :]) for l in L_ALL]
    Phat = np.stack([predict(coefs[l], st.S[:, l, :]) for l in L_ALL], 1)
    R = (st.P.astype(np.float64) - Phat).astype(np.float32)  # rP, everywhere
    sets2 = OrderedDict([("b2_routine_in_sample", fit), ("b1_routine", m["b1_routine"]), ("c1", m["c1"]),
                         ("h384_routine", m["h384_routine"]), ("prog", prog), ("prog_dedup", prog_dedup), ("other", other),
                         ("resist", np.isin(pool, ["b1_resist", "b2_resist", "h384_resist"])),
                         ("pre_boundary_all", np.isin(pool, ["b1_pre", "b2_pre", "h384_pre"])),
                         ("pre_boundary_prog_traces", np.isin(pool, ["b1_pre", "b2_pre", "h384_pre"]) & isprog)])
    item2 = {}
    for name, mk in sets2.items():
        per = [r2(st.P[mk, l, :], Phat[mk, l, :]) for l in L_ALL]
        item2[name] = {"per_layer": per, "L5_15": float(np.mean(per[5:])), "n": int(mk.sum())}
    # reverse fit: c1 -> b2 / b1, and trace-level 5-fold CV on b2 alone
    coefs_c1 = [lstsq_fit(st.S[m["c1"], l, :], st.P[m["c1"], l, :]) for l in L_ALL]
    Phat_c1 = np.stack([predict(coefs_c1[l], st.S[:, l, :]) for l in L_ALL], 1)
    item2["fit_on_c1"] = {n: float(np.mean([r2(st.P[mk, l, :], Phat_c1[mk, l, :]) for l in L_ML]))
                          for n, mk in (("c1_in_sample", m["c1"]), ("b2_routine", fit), ("b1_routine", m["b1_routine"]), ("prog", prog))}
    rng = np.random.default_rng(SEED)
    owners_fit = np.unique(st.owner[fit])
    folds = rng.permutation(len(owners_fit)) % 5
    cv = []
    for f in range(5):
        tr = fit & np.isin(st.owner, owners_fit[folds != f])
        te = fit & np.isin(st.owner, owners_fit[folds == f])
        cv.append(np.mean([r2(st.P[te, l, :], predict(lstsq_fit(st.S[tr, l, :], st.P[tr, l, :]), st.S[te, l, :])) for l in L_ML]))
    item2["b2_trace_5fold_cv_L5_15"] = float(np.mean(cv))
    # position dependence of R2 on routine
    for lo, hi in ((7, 40), (40, 100), (100, 192)):
        mk = held & (st.end >= lo) & (st.end < hi)
        item2[f"held_end_{lo}_{hi}"] = {"L5_15": float(np.mean([r2(st.P[mk, l, :], Phat[mk, l, :]) for l in L_ML])), "n": int(mk.sum())}
    res["item2"] = item2
    print(f"[item2] {time.time() - t0:.0f}s", flush=True)

    # ---- item 3 ---------------------------------------------------------------
    fams = {"S": (st.S, FLOOR_S), "P": (st.P, FLOOR_P), "rP": (R, FLOOR_P)}
    D = {}
    for fam, (X, fl) in fams.items():
        mu = X[fit].astype(np.float64).mean(0)
        sd = X[fit].astype(np.float64).std(0) + fl
        D[fam] = wdist_layer(X, mu, sd)  # [N,16]
    d = {fam: D[fam][:, L_ML].sum(1) for fam in fams}
    rng = np.random.default_rng(SEED)
    item3 = {}
    for fam in fams:
        e = {"auc_prog_vs_held": auc(d[fam][prog], d[fam][held]),
             "ci": boot(d[fam][prog], d[fam][held], st.owner[prog], st.owner[held], rng),
             "auc_prog_dedup_vs_held": auc(d[fam][prog_dedup], d[fam][held]),
             "auc_prog_vs_b1": auc(d[fam][prog], d[fam][m["b1_routine"]]),
             "auc_prog_vs_c1": auc(d[fam][prog], d[fam][m["c1"]]),
             "auc_other_vs_held": auc(d[fam][other], d[fam][held]),
             "median_prog_over_q95_held": float(np.median(d[fam][prog]) / np.quantile(d[fam][held], 0.95)),
             "per_layer_auc_prog_vs_held": [auc(D[fam][prog, l], D[fam][held, l]) for l in L_ALL]}
        item3[fam] = e
    res["item3"] = item3
    print(f"[item3] {time.time() - t0:.0f}s", flush=True)

    # ---- attack A: token position ----------------------------------------------
    A = {}
    for fam in fams:
        x = d[fam]
        row = {}
        # does the distance of *routine* windows grow with position?
        for lo, hi in ((7, 40), (40, 100), (100, 150), (150, 192)):
            mk = held & (st.end >= lo) & (st.end < hi)
            row[f"held_median_end_{lo}_{hi}"] = float(np.median(x[mk]))
        row["spearman_end_vs_dist_held"] = spearman(st.end[held], x[held])
        row["auc_held_late(end>=100)_vs_held_early(end<40)"] = auc(x[held & (st.end >= 100)], x[held & (st.end < 40)])
        # matched-position comparisons
        for lo, hi in ((7, 100), (100, 192)):
            pm = prog & (st.end >= lo) & (st.end < hi)
            hm = held & (st.end >= lo) & (st.end < hi)
            row[f"auc_prog_vs_held_end_{lo}_{hi}"] = {"auc": auc(x[pm], x[hm]), "n_pos": int(pm.sum()), "n_neg": int(hm.sum())}
        # h384 prog windows past token 192 have no negatives in held at all; use h384 routine >= 192
        pm = prog & (st.end >= 192)
        hm = m["h384_routine"] & (st.end >= 192)
        row["auc_prog_end_ge_192_vs_h384_routine_end_ge_192"] = {"auc": auc(x[pm], x[hm]), "n_pos": int(pm.sum()), "n_neg": int(hm.sum())}
        row["auc_prog_end_ge_192_vs_held(all)"] = auc(x[pm], x[held])
        row["auc_prog_end_lt_192_vs_h384_routine_end_lt_192"] = auc(x[prog & (st.end < 192)], x[m["h384_routine"] & (st.end < 192)])
        row["auc_h384_routine_end_ge_192_vs_held"] = auc(x[hm], x[held])
        A[fam] = row
    res["attackA_position"] = A

    # ---- attack B: is the residual information the scalar concentration statistics? -----
    B = {}
    SC = st.SC  # [N,16,4] window means of mass8, ent8, ent64, top1
    mu_sc = SC[fit].astype(np.float64).mean(0)
    sd_sc = SC[fit].astype(np.float64).std(0) + 1e-6
    z_sc = (SC.astype(np.float64) - mu_sc) / sd_sc
    for i, s in enumerate(SCALARS):
        signed = z_sc[:, L_ML, i].mean(1)
        sq = (z_sc[:, L_ML, i] ** 2).sum(1)
        B[f"scalar_{s}"] = {"auc_signed_prog_vs_held": auc(signed[prog], signed[held]),
                            "auc_sq_prog_vs_held": auc(sq[prog], sq[held]),
                            "auc_signed_other_vs_held": auc(signed[other], signed[held]),
                            "mean_z_prog": float(signed[prog].mean()), "mean_z_other": float(signed[other].mean()),
                            "mean_z_b1_routine": float(signed[m["b1_routine"]].mean()), "mean_z_c1": float(signed[m["c1"]].mean())}
    sq_all = (z_sc[:, L_ML, :] ** 2).sum((1, 2))
    B["all4_scalars_sq_auc_prog_vs_held"] = auc(sq_all[prog], sq_all[held])
    B["all4_scalars_sq_auc_other_vs_held"] = auc(sq_all[other], sq_all[held])
    B["all4_scalars_ci"] = boot(sq_all[prog], sq_all[held], st.owner[prog], st.owner[held], rng)
    # residual of rP after also regressing on the 4 scalars (per layer, fit pool)
    R2 = np.empty_like(R)
    for l in L_ALL:
        Xs = SC[:, l, :].astype(np.float64)
        c = lstsq_fit(Xs[fit], R[fit, l, :])
        R2[:, l, :] = (R[:, l, :].astype(np.float64) - predict(c, Xs)).astype(np.float32)
    mu = R2[fit].astype(np.float64).mean(0)
    sd = R2[fit].astype(np.float64).std(0) + FLOOR_P
    d_r2 = wdist(R2, mu, sd, L_ML)
    B["rP_minus_scalars"] = {"auc_prog_vs_held": auc(d_r2[prog], d_r2[held]), "ci": boot(d_r2[prog], d_r2[held], st.owner[prog], st.owner[held], rng),
                             "auc_other_vs_held": auc(d_r2[other], d_r2[held]),
                             "median_prog_over_q95_held": float(np.median(d_r2[prog]) / np.quantile(d_r2[held], 0.95))}
    # and the converse: P regressed on [S, scalars] directly
    R3 = np.empty_like(R)
    for l in L_ALL:
        Xs = np.concatenate([st.S[:, l, :], SC[:, l, :]], 1).astype(np.float64)
        c = lstsq_fit(Xs[fit], st.P[fit, l, :])
        R3[:, l, :] = (st.P[:, l, :].astype(np.float64) - predict(c, Xs)).astype(np.float32)
    B["R2_P_on_S_plus_scalars_L5_15"] = {n: float(np.mean([r2(st.P[mk, l, :], st.P[mk, l, :] - R3[mk, l, :]) for l in L_ML]))
                                         for n, mk in (("b2_in_sample", fit), ("b1_routine", m["b1_routine"]), ("c1", m["c1"]), ("prog", prog), ("other", other))}
    # how much of rP's whitened distance shift is along the "mass moves into the selected set" direction?
    # in-set residual mass per window per layer = sum_e S_e * rP_e (window-mean approximation)
    inset = (st.S * R).sum(2) / 8.0  # [N,16]  (S is window-mean indicator, R residual)
    mu_i = inset[fit].mean(0)
    sd_i = inset[fit].std(0) + 1e-6
    zi = (inset - mu_i) / sd_i
    B["inset_residual_mass"] = {"auc_signed_prog_vs_held": auc(zi[prog][:, L_ML].mean(1), zi[held][:, L_ML].mean(1)),
                                "auc_signed_other_vs_held": auc(zi[other][:, L_ML].mean(1), zi[held][:, L_ML].mean(1)),
                                "mean_z_prog": float(zi[prog][:, L_ML].mean()), "mean_z_b1": float(zi[m['b1_routine']][:, L_ML].mean())}
    res["attackB_scalars"] = B
    print(f"[attackB] {time.time() - t0:.0f}s", flush=True)

    # ---- attack C: whitening choice and floors --------------------------------------
    C = {}
    for fl in (0.0, FLOOR_P, 1e-3):
        mu = R[fit].astype(np.float64).mean(0)
        sd = R[fit].astype(np.float64).std(0) + fl
        x = wdist(R, mu, sd, L_ML)
        C[f"rP_floor_{fl}"] = {"auc": auc(x[prog], x[held]), "median_prog_over_q95_held": float(np.median(x[prog]) / np.quantile(x[held], 0.95))}
    for wp in ("c1", "b1_routine", "h384_routine"):
        mu = R[m[wp]].astype(np.float64).mean(0)
        sd = R[m[wp]].astype(np.float64).std(0) + FLOOR_P
        x = wdist(R, mu, sd, L_ML)
        C[f"rP_whiten_on_{wp}"] = {"auc_prog_vs_held": auc(x[prog], x[held]), "auc_prog_vs_b1": auc(x[prog], x[m['b1_routine']]),
                                   "auc_prog_vs_c1": auc(x[prog], x[m['c1']])}
    # residual sd per coordinate: how many coordinates are at the floor?
    sd0 = R[fit].astype(np.float64).std(0)
    C["rP_fit_sd_per_coord"] = {"median": float(np.median(sd0[L_ML])), "q05": float(np.quantile(sd0[L_ML], 0.05)),
                                "frac_below_floor": float((sd0[L_ML] < FLOOR_P).mean()), "floor": FLOOR_P}
    # residual energy ratio held / fit (per window mean of sum of squares)
    en = (R.astype(np.float64) ** 2)[:, L_ML, :].sum((1, 2))
    C["resid_energy_mean"] = {n: float(en[mk].mean()) for n, mk in (("b2_in_sample", fit), ("b1_routine", m["b1_routine"]), ("c1", m["c1"]), ("h384_routine", m["h384_routine"]), ("prog", prog), ("other", other))}
    res["attackC_whitening"] = C

    # ---- attack D: cross-pool thresholds, resist arms, pre-boundary windows ---------------
    Dd = {}
    for fam in fams:
        x = d[fam]
        row = {"q95": {}, "q99": {}, "far_at_other_pool_q95": {}, "far_at_other_pool_q99": {}}
        rp = OrderedDict([("b2_routine_in_sample", fit), ("b1_routine", m["b1_routine"]), ("c1", m["c1"]), ("h384_routine", m["h384_routine"])])
        for n, mk in rp.items():
            row["q95"][n] = float(np.quantile(x[mk], 0.95))
            row["q99"][n] = float(np.quantile(x[mk], 0.99))
        for cal in ("b2_routine_in_sample", "c1"):
            for ev in ("b1_routine", "c1", "h384_routine"):
                if ev == cal:
                    continue
                row["far_at_other_pool_q95"][f"cal={cal},eval={ev}"] = float((x[rp[ev]] > row["q95"][cal]).mean())
                row["far_at_other_pool_q99"][f"cal={cal},eval={ev}"] = float((x[rp[ev]] > row["q99"][cal]).mean())
        q95h = float(np.quantile(x[held], 0.95))
        row["frac_over_held_q95"] = {n: float((x[mk] > q95h).mean()) for n, mk in (
            ("prog", prog), ("prog_dedup", prog_dedup), ("other", other), ("b1_routine", m["b1_routine"]), ("c1", m["c1"]),
            ("b2_routine_in_sample", fit), ("h384_routine", m["h384_routine"]),
            ("resist_b1", m["b1_resist"]), ("resist_b2", m["b2_resist"]), ("resist_h384", m["h384_resist"]),
            ("pre_boundary_all", sets2["pre_boundary_all"]), ("pre_boundary_prog_traces", sets2["pre_boundary_prog_traces"]),
            ("pre_boundary_other_traces", sets2["pre_boundary_all"] & ~isprog))}
        row["median_over_held_q95"] = {n: float(np.median(x[mk]) / q95h) for n, mk in (
            ("prog", prog), ("other", other), ("b1_routine", m["b1_routine"]), ("c1", m["c1"]), ("resist_all", sets2["resist"]),
            ("pre_boundary_prog_traces", sets2["pre_boundary_prog_traces"]))}
        row["auc_prog_vs_resist_all"] = auc(x[prog], x[sets2["resist"]])
        row["auc_resist_vs_held"] = auc(x[sets2["resist"]], x[held])
        row["auc_pre_boundary_prog_traces_vs_held"] = auc(x[sets2["pre_boundary_prog_traces"]], x[held])
        row["auc_pre_boundary_all_vs_held"] = auc(x[sets2["pre_boundary_all"]], x[held])
        # per-trace (per scenario) view; h384 replays merged with their b2 original by bare id
        per = {}
        for tid in sorted(set(st.owner[prog])):
            mk = prog & (st.owner == tid)
            per[tid] = {"n": int(mk.sum()), "auc_vs_held": auc(x[mk], x[held]), "frac_over_q95": float((x[mk] > q95h).mean()),
                        "median_over_q95": float(np.median(x[mk]) / q95h),
                        "pre_frac_over_q95": float((x[sets2["pre_boundary_all"] & (st.owner == tid)] > q95h).mean()) if (sets2["pre_boundary_all"] & (st.owner == tid)).any() else None}
        row["per_prog_trace"] = per
        Dd[fam] = row
    res["attackD_crosspool"] = Dd
    print(f"[attackD] {time.time() - t0:.0f}s", flush=True)

    # ---- attack E: mean-shift ratios re-derived (item 4) for P / S / rP -------------------
    E = {}
    for fam, (X, _) in fams.items():
        row = {}
        for a, b in (("b1_routine", "b2_routine"), ("b2_routine", "c1"), ("b1_routine", "c1")):
            vals = []
            for l in L_ML:
                diff = X[m[a], l, :].astype(np.float64).mean(0) - X[m[b], l, :].astype(np.float64).mean(0)
                u = diff / max(np.linalg.norm(diff), 1e-12)
                sa = (X[m[a], l, :] @ u).std()
                sb = (X[m[b], l, :] @ u).std()
                vals.append(np.linalg.norm(diff) / (0.5 * (sa + sb)))
            row[f"{a}_vs_{b}_shift_over_sd_L5_15"] = float(np.mean(vals))
        vals = []
        for l in L_ML:
            diff = X[prog, l, :].astype(np.float64).mean(0) - X[fit, l, :].astype(np.float64).mean(0)
            u = diff / max(np.linalg.norm(diff), 1e-12)
            vals.append(np.linalg.norm(diff) / (X[fit, l, :] @ u).std())
        row["code_sep_over_sd_L5_15"] = float(np.mean(vals))
        E[fam] = row
    res["attackE_meanshift"] = E

    # ---- attack F: is rP's power the 1-D "mass into the selected set" direction? ----------
    F = {}
    # per window and layer, remove the projection of the residual onto span{1, S_w}
    Rp = R.astype(np.float64).copy()
    Sw = st.S.astype(np.float64)
    ones = np.ones(64)
    u1 = ones / np.sqrt(64.0)
    Rp -= (Rp @ u1)[..., None] * u1  # (already ~0: residual sums to ~0)
    S2 = Sw - Sw.mean(2, keepdims=True)
    S2 /= np.maximum(np.linalg.norm(S2, axis=2, keepdims=True), 1e-12)
    Rp -= (Rp * S2).sum(2, keepdims=True) * S2
    Rp = Rp.astype(np.float32)
    mu = Rp[fit].astype(np.float64).mean(0)
    sd = Rp[fit].astype(np.float64).std(0) + FLOOR_P
    x = wdist(Rp, mu, sd, L_ML)
    F["rP_minus_span(1,S)"] = {"auc_prog_vs_held": auc(x[prog], x[held]), "ci": boot(x[prog], x[held], st.owner[prog], st.owner[held], rng),
                               "auc_other_vs_held": auc(x[other], x[held]),
                               "median_prog_over_q95_held": float(np.median(x[prog]) / np.quantile(x[held], 0.95)),
                               "auc_prog_vs_held_end_100_192": auc(x[prog & (st.end >= 100) & (st.end < 192)], x[held & (st.end >= 100)]),
                               "spearman_end_vs_dist_held": spearman(st.end[held], x[held])}
    # the 1-D in-set residual mass per band
    zi_m = zi[:, L_ML].mean(1)
    F["inset_residual_mass_1d"] = {"auc_prog_vs_held": auc(zi_m[prog], zi_m[held]),
                                   "auc_prog_vs_held_end_100_192": auc(zi_m[prog & (st.end >= 100) & (st.end < 192)], zi_m[held & (st.end >= 100)]),
                                   "spearman_end_vs_z_held": spearman(st.end[held], zi_m[held]),
                                   "auc_other_vs_held": auc(zi_m[other], zi_m[held]),
                                   "auc_resist_vs_held": auc(zi_m[sets2["resist"]], zi_m[held]),
                                   "q95_transfer_far_b2cal_b1eval": float((zi_m[m["b1_routine"]] > np.quantile(zi_m[fit], 0.95)).mean())}
    # position-fair thresholds: q95 of late routine windows
    late = held & (st.end >= 100)
    for fam in fams:
        xx = d[fam]
        q95_all = float(np.quantile(xx[held], 0.95))
        q95_late = float(np.quantile(xx[late], 0.95))
        pl = prog & (st.end >= 100)
        F[f"{fam}_position_fair"] = {"q95_held_all": q95_all, "q95_held_end_ge_100": q95_late, "ratio": q95_late / q95_all,
                                     "prog_median_over_q95_all": float(np.median(xx[prog]) / q95_all),
                                     "prog(end>=100)_median_over_q95_late": float(np.median(xx[pl]) / q95_late),
                                     "prog(end>=100)_frac_over_q95_late": float((xx[pl] > q95_late).mean()),
                                     "prog(end>=100)_frac_over_q95_all": float((xx[pl] > q95_all).mean()),
                                     "b1_late_q95_over_c1_late_q95": float(np.quantile(xx[m["b1_routine"] & (st.end >= 100)], 0.95) / np.quantile(xx[m["c1"] & (st.end >= 100)], 0.95))}
    # b1 routine tail traces under rP vs S
    for fam in ("rP", "S"):
        xx = d[fam]
        q99_b2 = float(np.quantile(xx[fit], 0.99))
        rows = []
        for tid in np.unique(st.owner[m["b1_routine"]]):
            mk = m["b1_routine"] & (st.owner == tid)
            rows.append((float((xx[mk] > q99_b2).mean()), tid, int(mk.sum()), float(xx[mk].max() / q99_b2)))
        rows.sort(reverse=True)
        F[f"{fam}_b1_tail_traces_over_b2_q99"] = {"n_b1_traces_with_any_window_over_b2_q99": int(sum(r[0] > 0 for r in rows)),
                                                  "share_of_b1_over_q99_windows_from_top3_traces": float(sum(r[0] * r[2] for r in rows[:3]) / max(sum(r[0] * r[2] for r in rows), 1e-9)),
                                                  "top": [{"trace": r[1], "frac_over_b2_q99": r[0], "n": r[2], "max_over_q99": r[3], "prefix": st.prefix.get(r[1], "")} for r in rows[:6]]}
    # in-sample R2 by position band
    for lo, hi in ((7, 40), (40, 100), (100, 192)):
        mk = fit & (st.end >= lo) & (st.end < hi)
        F[f"R2_b2_in_sample_end_{lo}_{hi}"] = float(np.mean([r2(st.P[mk, l, :], Phat[mk, l, :]) for l in L_ML]))
    res["attackF_direction_position_tail"] = F

    (OUT / "results.json").write_text(json.dumps(res, indent=1, default=float))
    write_tables(res, OUT / "tables.md")
    print(f"[done] {time.time() - t0:.0f}s -> {OUT}", flush=True)


def f3(x):
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.3f}"


def write_tables(res, path):
    L = ["# explore_prob_information_refute -- auto-generated tables (EXPLORATORY / DIAGNOSTIC)\n"]
    L.append("## hygiene\n```\n" + json.dumps(res["hygiene"], indent=1) + "\n```\n")
    L.append("## counts\n```\n" + json.dumps(res["counts"], indent=1) + "\n```\n")
    L.append("## item 1 (L5-15 means per pool)\n\n| pool | mass8 | ent8 | ent64 | top1 | n tokens |\n|---|---:|---:|---:|---:|---:|")
    for k, v in sorted(res["item1"].items()):
        L.append(f"| {k} | " + " | ".join(f3(v["L5_15"][s]) for s in SCALARS) + f" | {v['n_tokens']} |")
    L.append("\n## item 2: R2 of P ~ S (fit b2 routine), L5-15 mean\n\n| pool | R2 L5-15 | n |\n|---|---:|---:|")
    for k, v in res["item2"].items():
        if isinstance(v, dict) and "L5_15" in v:
            L.append(f"| {k} | {f3(v['L5_15'])} | {v.get('n', '')} |")
    L.append(f"\nfit on c1: {res['item2']['fit_on_c1']}; b2 trace-level 5-fold CV: {f3(res['item2']['b2_trace_5fold_cv_L5_15'])}\n")
    L.append("## item 3: AUC (L5-15) prog vs held\n\n| family | AUC | CI | dedup | vs b1 | vs c1 | other vs held | med/q95 |\n|---|---:|---:|---:|---:|---:|---:|---:|")
    for k, v in res["item3"].items():
        L.append(f"| {k} | {f3(v['auc_prog_vs_held'])} | [{v['ci'][0]:.2f},{v['ci'][1]:.2f}] | {f3(v['auc_prog_dedup_vs_held'])} | {f3(v['auc_prog_vs_b1'])} | {f3(v['auc_prog_vs_c1'])} | {f3(v['auc_other_vs_held'])} | {v['median_prog_over_q95_held']:.2f} |")
    L.append("\nper-layer AUC prog vs held:\n\n| family | " + " | ".join(f"L{l}" for l in L_ALL) + " |\n|---|" + "---:|" * 16)
    for k, v in res["item3"].items():
        L.append(f"| {k} | " + " | ".join(f"{x:.2f}" for x in v["per_layer_auc_prog_vs_held"]) + " |")
    for key in ("attackA_position", "attackB_scalars", "attackC_whitening", "attackE_meanshift", "attackF_direction_position_tail"):
        L.append(f"\n## {key}\n```\n" + json.dumps(res[key], indent=1, default=float) + "\n```\n")
    L.append("## attackD_crosspool\n")
    for fam, row in res["attackD_crosspool"].items():
        slim = {k: v for k, v in row.items() if k != "per_prog_trace"}
        L.append(f"### {fam}\n```\n" + json.dumps(slim, indent=1, default=float) + "\n```\n")
        L.append("| trace | n | auc vs held | frac>q95 | med/q95 | pre-boundary frac>q95 |\n|---|---:|---:|---:|---:|---:|")
        for tid, v in row["per_prog_trace"].items():
            L.append(f"| {tid} | {v['n']} | {f3(v['auc_vs_held'])} | {f3(v['frac_over_q95'])} | {f3(v['median_over_q95'])} | {f3(v['pre_frac_over_q95'])} |")
        L.append("")
    path.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
