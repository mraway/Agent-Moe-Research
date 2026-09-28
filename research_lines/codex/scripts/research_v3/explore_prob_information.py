"""EXPLORATORY / DIAGNOSTIC: how much routing information does the top-k 0/1 view discard?

Question (user): the top-k 0/1 indicator (a) discards information relative to the gate
weights and (b) distorts the computation, since the weights enter the expert-weighted sum.
This script quantifies both on the OLMoE development traces (b1, b2, h384, c1).

Representations per token and layer (64 experts, top-8 routing, ``norm_topk_prob=False`` so
the gate weight of a selected expert *is* its softmax probability):

* ``P``  full router softmax                 (sums to 1)
* ``S``  top-8 indicator                     (sums to 8)   -- what CAND-A / WGM see
* ``Q``  renormalised top-8 weights          (sums to 1, zero outside the selected set)

Everything is computed on w=8 window means.  Routine pools (b1 routine, b2 routine, c1 =
clean + benign_control arms) use every decode token with stride 2; drift pools (b1 / b2 /
h384 ``arm_class == drift``) use the windows whose end lies at or after the completion
boundary (``goal_plan_deviation_start_output_token``; on h384 the execution onset), stride 1,
split by ``target_domain``.

Items (numbering follows the task brief):
 1. gate flatness per layer (per-token statistics on routine tokens),
 2. least-squares fit P ~ S per layer on b2 routine, R2 on held-out pools (full vs diagonal),
 3. where the code separation lives: whitened distances on the residual P - P_hat, on S and
    on P, AUC of programming-domain drift windows vs routine windows,
 4. cross-pool stability of routine means under P vs S (shift / within-pool spread, margin
    ratio for code),
 5. items 2 and 3 repeated with Q as the target (loss inside vs outside the selected set).

Nothing here is a detector; all window sets are selected with post-hoc anchors and labels.
Development data only.  Deterministic (the only randomness is a seeded trace-level bootstrap).
"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import OrderedDict, defaultdict
from pathlib import Path

import numpy as np
import torch

from research_v2 import io as rio

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "artifacts" / "agent_v2" / "research_v3" / "explore_prob_information"

W = 8
ROUTINE_STRIDE = 2
DRIFT_STRIDE = 1
SEED = 0
N_BOOT = 200
LAYERS_ALL = tuple(range(16))
LAYERS_ML = tuple(range(5, 16))
LAYER_SETS = OrderedDict([("L5-15", LAYERS_ML), ("all-16", LAYERS_ALL)])
FIT_POOL = "b2_routine"
HELD_ROUTINE = ("b1_routine", "c1")
ROUTINE_POOLS = ("b1_routine", "b2_routine", "c1")
PROG = "programming"
# CAND-A uses sd + 1e-3 on selection rates (natural scale 8/64).  The same *relative* floor
# is applied to the probability-scale representations (natural scale 1/64): 1e-3 / 8.
FLOOR = {"S": 1e-3, "P": 1.25e-4, "Q": 1.25e-4}
REPS = ("P", "S", "Q", "Pout")
KNN_K = 20


# ---------------------------------------------------------------------------
# window construction
# ---------------------------------------------------------------------------
def window_means(x: torch.Tensor) -> torch.Tensor:
    """x [16, T, 64] -> [T-W+1, 16, 64]; window i ends at token index i + W - 1."""
    c = torch.cat((torch.zeros(16, 1, 64, dtype=x.dtype), x.cumsum(1)), dim=1)
    m = (c[:, W:, :] - c[:, :-W, :]) / float(W)
    return m.permute(1, 0, 2).contiguous()


def token_reps(trace) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, dict]:
    """Per-token P, S, Q [16, T, 64] plus per-token flatness statistics [16, T]."""
    p = trace.probabilities().float()
    idx = trace.top_k_ids
    s = torch.zeros_like(p)
    s.scatter_(2, idx, 1.0)
    g = torch.gather(p, 2, idx)  # [16, T, 8], sorted descending
    mass = g.sum(-1)  # [16, T]
    qn = g / mass.unsqueeze(-1)
    q = torch.zeros_like(p)
    q.scatter_(2, idx, qn)
    pout = p * (1.0 - s)  # softmax mass on the experts the 0/1 view drops
    ent8 = -(qn * torch.log(qn.clamp_min(1e-12))).sum(-1) / math.log(8.0)
    ent64 = -(p * torch.log(p.clamp_min(1e-12))).sum(-1) / math.log(64.0)
    top1 = qn[..., 0]
    top1_raw = g[..., 0]
    stats = {
        "ent8": ent8,
        "ent64": ent64,
        "top1_share": top1,
        "top1_gt_half": (top1 > 0.5).float(),
        "top8_mass": mass,
        "top1_raw": top1_raw,
        "eff_k": torch.exp(ent8 * math.log(8.0)),  # exp(H) of the renormalised weights
    }
    return p, s, q, pout, stats


def pool_key(trace) -> str | None:
    cls = rio.arm_class(trace)
    if cls in ("clean", "benign"):
        return "c1" if trace.batch == "c1" else f"{trace.batch}_routine"
    if cls == "drift":
        return f"{trace.batch}_drift:{trace.domain}"
    return None  # resist arms are not used here


def build(with_pools: dict[str, tuple]) -> tuple[dict, dict, dict]:
    """Return (windows, owners, flatness) keyed by pool."""
    windows: dict[str, dict[str, list[np.ndarray]]] = defaultdict(lambda: {r: [] for r in REPS})
    owners: dict[str, list[str]] = defaultdict(list)
    flat_sum: dict[str, dict[str, torch.Tensor]] = {}
    flat_n: dict[str, int] = defaultdict(int)
    trace_counts: dict[str, int] = defaultdict(int)
    for batch, traces in with_pools.items():
        for t in traces:
            key = pool_key(t)
            if key is None or t.token_count < W:
                t._probabilities = None
                continue
            p, s, q, pout, stats = token_reps(t)
            is_drift = ":" in key
            if is_drift:
                cb = int(t.completion_boundary)
                ends = np.arange(W - 1, t.token_count)
                sel = np.where(ends >= cb)[0][::DRIFT_STRIDE]
                if sel.size == 0:
                    sel = np.array([ends.size - 1])
                tok_lo = cb
            else:
                sel = np.arange(0, t.token_count - W + 1, ROUTINE_STRIDE)
                tok_lo = 0
            # flatness statistics accumulate over the tokens the windows cover
            fkey = key.split(":")[0] + (":prog" if key.endswith(PROG) else (":other" if is_drift else ""))
            if fkey not in flat_sum:
                flat_sum[fkey] = {k: torch.zeros(16) for k in stats}
            for k, v in stats.items():
                flat_sum[fkey][k] += v[:, tok_lo:].sum(1)
            flat_n[fkey] += int(t.token_count - tok_lo)
            for name, arr in (("P", p), ("S", s), ("Q", q), ("Pout", pout)):
                wm = window_means(arr)[sel].numpy().astype(np.float32)
                windows[key][name].append(wm)
            # h384 replays reuse the b2 trace ids, so owners are pool-qualified; the
            # bootstrap clusters on the bare id (a replay and its b2 original are one unit).
            owners[key] += [f"{t.batch}:{t.trace_id}"] * int(sel.size)
            trace_counts[key] += 1
            t._probabilities = None
    out_w = {k: {r: np.concatenate(v[r], 0) for r in REPS} for k, v in windows.items()}
    flat = {k: {s: (v[s] / flat_n[k]).numpy() for s in v} | {"n_tokens": flat_n[k]} for k, v in flat_sum.items()}
    return out_w, dict(owners), flat, dict(trace_counts)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """Mann-Whitney AUC (ties count 1/2)."""
    pos = np.asarray(pos, dtype=np.float64)
    neg = np.asarray(neg, dtype=np.float64)
    allv = np.concatenate([pos, neg])
    order = np.argsort(allv, kind="mergesort")
    ranks = np.empty_like(allv)
    sorted_v = allv[order]
    i = 0
    n = allv.size
    while i < n:
        j = i
        while j + 1 < n and sorted_v[j + 1] == sorted_v[i]:
            j += 1
        ranks[order[i : j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    rp = ranks[: pos.size].sum()
    return float((rp - pos.size * (pos.size + 1) / 2.0) / (pos.size * neg.size))


def js_divergence(a: np.ndarray, b: np.ndarray) -> float:
    a = np.clip(a, 1e-12, None)
    a = a / a.sum()
    b = np.clip(b, 1e-12, None)
    b = b / b.sum()
    m = 0.5 * (a + b)
    return float(0.5 * (a * np.log(a / m)).sum() + 0.5 * (b * np.log(b / m)).sum())


def fit_linear(X: np.ndarray, Y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Least-squares Y ~ X B + c (min-norm solution: X is rank deficient because sum S = 8)."""
    Xa = np.concatenate([X, np.ones((X.shape[0], 1), dtype=X.dtype)], 1).astype(np.float64)
    coef, *_ = np.linalg.lstsq(Xa, Y.astype(np.float64), rcond=None)
    return coef[:-1], coef[-1]


def fit_diagonal(X: np.ndarray, Y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    X = X.astype(np.float64)
    Y = Y.astype(np.float64)
    xm, ym = X.mean(0), Y.mean(0)
    vx = ((X - xm) ** 2).mean(0)
    cov = ((X - xm) * (Y - ym)).mean(0)
    slope = np.where(vx > 1e-12, cov / np.maximum(vx, 1e-12), 0.0)
    return slope, ym - slope * xm


def r2(Y: np.ndarray, Yhat: np.ndarray, centre: np.ndarray | None = None) -> float:
    Y = Y.astype(np.float64)
    ss_res = ((Y - Yhat) ** 2).sum()
    c = Y.mean(0) if centre is None else centre
    ss_tot = ((Y - c) ** 2).sum()
    return float(1.0 - ss_res / ss_tot)


def whitened_sq(X: np.ndarray, mu: np.ndarray, sd: np.ndarray) -> np.ndarray:
    """per-window sum over coords of ((x - mu)/sd)^2 ; X [N, 64]."""
    z = (X.astype(np.float64) - mu) / sd
    return (z * z).sum(1)


def boot_ci(pos: np.ndarray, neg: np.ndarray, pos_own: list[str], neg_own: list[str], rng) -> tuple[float, float]:
    """trace-level bootstrap of the AUC (resample positive traces and negative traces).

    Cluster unit = bare trace id, so an h384 replay and its b2 original are resampled together.
    """
    pos_own = [o.split(":", 1)[1] for o in pos_own]
    neg_own = [o.split(":", 1)[1] for o in neg_own]
    pos_ids = sorted(set(pos_own))
    neg_ids = sorted(set(neg_own))
    pos_idx = {i: np.where(np.asarray(pos_own) == i)[0] for i in pos_ids}
    neg_idx = {i: np.where(np.asarray(neg_own) == i)[0] for i in neg_ids}
    vals = []
    for _ in range(N_BOOT):
        ps = rng.choice(len(pos_ids), len(pos_ids), replace=True)
        ns = rng.choice(len(neg_ids), len(neg_ids), replace=True)
        pi = np.concatenate([pos_idx[pos_ids[k]] for k in ps])
        ni = np.concatenate([neg_idx[neg_ids[k]] for k in ns])
        vals.append(auc(pos[pi], neg[ni]))
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def knn_residuals(windows: dict, k: int) -> dict[str, dict[str, np.ndarray]]:
    """Residual of P and Q after a k-nearest-neighbour regression on S (fit pool = b2 routine).

    Per layer: neighbours are found in the 64-dim S window-mean space of the fit pool
    (Euclidean); the prediction is the mean P (or Q) of the k neighbours.  Fit-pool windows
    exclude themselves.  Deterministic.
    """
    out = {"P": {}, "Q": {}}
    S_fit = torch.from_numpy(windows[FIT_POOL]["S"])  # [N,16,64]
    Y_fit = {t: torch.from_numpy(windows[FIT_POOL][t]) for t in ("P", "Q")}
    for key, reps in windows.items():
        S = torch.from_numpy(reps["S"])
        R = {t: np.empty_like(reps[t]) for t in ("P", "Q")}
        for l in LAYERS_ALL:
            ref = S_fit[:, l, :]
            for lo in range(0, S.shape[0], 4096):
                q = S[lo : lo + 4096, l, :]
                d = torch.cdist(q, ref)
                if key == FIT_POOL:
                    idx = torch.arange(lo, lo + q.shape[0])
                    d[torch.arange(q.shape[0]), idx] = float("inf")
                nn = d.topk(k, dim=1, largest=False).indices  # [n,k]
                for t in ("P", "Q"):
                    pred = Y_fit[t][:, l, :][nn].mean(1)
                    R[t][lo : lo + q.shape[0], l, :] = (torch.from_numpy(reps[t][lo : lo + q.shape[0], l, :]) - pred).numpy()
        for t in ("P", "Q"):
            out[t][key] = R[t]
    return out


# ---------------------------------------------------------------------------
# main analysis
# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(8)
    t0 = time.time()

    core = rio.load_core()
    pools = {"b1": core["b1"], "b2": core["b2"], "h384": rio.load_h384(), "c1": rio.load_c1()}
    windows, owners, flat, trace_counts = build(pools)
    print(f"[load+windows] {time.time() - t0:.0f}s", flush=True)

    drift_keys = sorted(k for k in windows if ":" in k)
    prog_keys = [k for k in drift_keys if k.endswith(":" + PROG)]
    other_keys = [k for k in drift_keys if not k.endswith(":" + PROG)]

    def cat(keys, rep):
        return np.concatenate([windows[k][rep] for k in keys], 0)

    def cat_own(keys):
        return [o for k in keys for o in owners[k]]

    counts = {k: {"n_windows": int(windows[k]["P"].shape[0]), "n_traces": trace_counts[k]} for k in windows}
    counts["prog_all"] = {"n_windows": int(cat(prog_keys, "P").shape[0]), "n_traces": sum(trace_counts[k] for k in prog_keys)}
    counts["other_all"] = {"n_windows": int(cat(other_keys, "P").shape[0]), "n_traces": sum(trace_counts[k] for k in other_keys)}
    counts["held_routine"] = {"n_windows": int(cat(HELD_ROUTINE, "P").shape[0]), "n_traces": sum(trace_counts[k] for k in HELD_ROUTINE)}
    results: dict = {
        "meta": {
            "w": W, "routine_stride": ROUTINE_STRIDE, "drift_stride": DRIFT_STRIDE, "seed": SEED,
            "n_boot": N_BOOT, "fit_pool": FIT_POOL, "held_routine": list(HELD_ROUTINE),
            "floors": FLOOR, "layer_sets": {k: list(v) for k, v in LAYER_SETS.items()},
            "drift_window_rule": "window end >= completion_boundary (b1/b2: goal_plan_deviation_start_output_token; h384: execution onset)",
            "status": "EXPLORATORY / DIAGNOSTIC, development data only",
        },
        "counts": counts,
    }

    # ---- item 1: gate flatness --------------------------------------------------
    results["item1_flatness"] = {k: {s: (v[s].tolist() if s != "n_tokens" else v[s]) for s in v} for k, v in flat.items()}

    # ---- item 2 / 5: regression of P (and Q) on S -------------------------------
    eval_pools = OrderedDict()
    eval_pools["b2_routine (fit, in-sample)"] = [FIT_POOL]
    eval_pools["b1_routine"] = ["b1_routine"]
    eval_pools["c1"] = ["c1"]
    eval_pools["drift prog (b1+b2+h384)"] = prog_keys
    eval_pools["drift other (b1+b2+h384)"] = other_keys
    for dk in drift_keys:
        eval_pools[dk] = [dk]
    # per-domain pooled across batches
    domains = sorted({k.split(":")[1] for k in drift_keys})
    for d in domains:
        eval_pools[f"drift domain {d} (all batches)"] = [k for k in drift_keys if k.endswith(":" + d)]

    S_fit = windows[FIT_POOL]["S"]
    fits: dict[str, dict] = {}
    residuals: dict[str, dict[str, np.ndarray]] = {}  # target -> pool_key -> [N,16,64]
    for target in ("P", "Q", "Pout"):
        Y_fit = windows[FIT_POOL][target]
        per_layer = {"full": defaultdict(list), "diag": defaultdict(list), "resid_energy_rel_fit": defaultdict(list)}
        coefs = []
        for l in LAYERS_ALL:
            B, c = fit_linear(S_fit[:, l, :], Y_fit[:, l, :])
            slope, icpt = fit_diagonal(S_fit[:, l, :], Y_fit[:, l, :])
            coefs.append((B, c, slope, icpt))
            fit_centre = Y_fit[:, l, :].mean(0).astype(np.float64)
            ss_tot_fit_per_window = ((Y_fit[:, l, :] - fit_centre) ** 2).sum(1).mean()
            for name, keys in eval_pools.items():
                X = cat(keys, "S")[:, l, :]
                Y = cat(keys, target)[:, l, :]
                Yh_full = X.astype(np.float64) @ B + c
                Yh_diag = X.astype(np.float64) * slope + icpt
                per_layer["full"][name].append(r2(Y, Yh_full))
                per_layer["diag"][name].append(r2(Y, Yh_diag))
                per_layer["resid_energy_rel_fit"][name].append(
                    float(((Y - Yh_full) ** 2).sum(1).mean() / ss_tot_fit_per_window)
                )
        fits[target] = {kind: {name: v for name, v in d.items()} for kind, d in per_layer.items()}
        # residuals for every pool (full fit)
        residuals[target] = {}
        for key in windows:
            X = windows[key]["S"]
            Y = windows[key][target]
            R = np.empty_like(Y, dtype=np.float32)
            for l in LAYERS_ALL:
                B, c, _, _ = coefs[l]
                R[:, l, :] = (Y[:, l, :].astype(np.float64) - (X[:, l, :].astype(np.float64) @ B + c)).astype(np.float32)
            residuals[target][key] = R
    results["item2_fit_P_on_S"] = fits["P"]
    results["item5_fit_Q_on_S"] = fits["Q"]
    results["item5_fit_Pout_on_S"] = fits["Pout"]
    print(f"[fits] {time.time() - t0:.0f}s", flush=True)

    # ---- item 3 / 5: where does the code separation live --------------------------
    # score families: S (WGM-style), P (full softmax means), Q (renormalised top-8),
    # rP (residual of P after S), rQ (residual of Q after S), plus the out-of-set mass part
    families: dict[str, dict[str, np.ndarray]] = {
        "S": {k: windows[k]["S"] for k in windows},
        "P": {k: windows[k]["P"] for k in windows},
        "Q": {k: windows[k]["Q"] for k in windows},
        "Pout": {k: windows[k]["Pout"] for k in windows},
        "rP": residuals["P"],
        "rQ": residuals["Q"],
        "rPout": residuals["Pout"],
    }
    # (a) nonlinear control: kNN (k=KNN_K) regression of P / Q on S in the fit pool, per layer.
    # If the residual of a *local* predictor still separates code, the information is absent
    # from S itself and not merely from a linear read-out of S.
    knn_res = knn_residuals(windows, KNN_K)
    families["rP_knn"] = knn_res["P"]
    families["rQ_knn"] = knn_res["Q"]
    fam_floor = {"S": FLOOR["S"], "P": FLOOR["P"], "Q": FLOOR["Q"], "Pout": FLOOR["P"], "rP": FLOOR["P"], "rQ": FLOOR["Q"],
                 "rPout": FLOOR["P"], "rP_knn": FLOOR["P"], "rQ_knn": FLOOR["Q"]}
    rng = np.random.default_rng(SEED)
    item3 = {}
    per_layer_auc = {}
    neg_sets = OrderedDict([
        ("held (b1_routine+c1)", list(HELD_ROUTINE)),
        ("b1_routine", ["b1_routine"]),
        ("c1", ["c1"]),
        ("b2_routine (in-sample)", [FIT_POOL]),
    ])
    pos_sets = OrderedDict([
        ("prog (b1+b2+h384)", prog_keys),
        ("other (b1+b2+h384)", other_keys),
    ])
    for pk in prog_keys:
        pos_sets[pk] = [pk]
    for fam, data in families.items():
        F = data[FIT_POOL]
        mu = F.astype(np.float64).mean(0)  # [16,64]
        sd = F.astype(np.float64).std(0) + fam_floor[fam]
        # per-window per-layer whitened squared distances
        per_layer_d = {k: np.stack([whitened_sq(v[:, l, :], mu[l], sd[l]) for l in LAYERS_ALL], 1) for k, v in data.items()}
        item3[fam] = {}
        for ls_name, ls in LAYER_SETS.items():
            item3[fam][ls_name] = {}
            for pname, pkeys in pos_sets.items():
                pos = np.concatenate([per_layer_d[k][:, list(ls)].sum(1) for k in pkeys])
                pos_own = cat_own(pkeys)
                for nname, nkeys in neg_sets.items():
                    neg = np.concatenate([per_layer_d[k][:, list(ls)].sum(1) for k in nkeys])
                    entry = {"auc": auc(pos, neg), "n_pos": int(pos.size), "n_neg": int(neg.size)}
                    if pname in ("prog (b1+b2+h384)", "other (b1+b2+h384)") and nname == "held (b1_routine+c1)":
                        entry["ci95_trace_boot"] = boot_ci(pos, neg, pos_own, cat_own(nkeys), rng)
                        entry["n_pos_clusters"] = len({o.split(":", 1)[1] for o in pos_own})
                        entry["median_pos_over_q95_neg"] = float(np.median(pos) / np.quantile(neg, 0.95))
                    item3[fam][ls_name][f"{pname} vs {nname}"] = entry
        # per single layer, prog / other vs held routine
        per_layer_auc[fam] = {}
        neg_layers = np.concatenate([per_layer_d[k] for k in HELD_ROUTINE])
        for pname, pkeys in list(pos_sets.items())[:2]:
            pos_layers = np.concatenate([per_layer_d[k] for k in pkeys])
            per_layer_auc[fam][pname] = [auc(pos_layers[:, l], neg_layers[:, l]) for l in LAYERS_ALL]
    results["item3_auc"] = item3
    results["item3_per_layer_auc_vs_held"] = per_layer_auc
    print(f"[auc] {time.time() - t0:.0f}s", flush=True)

    # residual energy share: what fraction of the whitened P-distance of a window is residual?
    # (decomposition is not exact because whitening differs; report both distances' medians)
    share = {}
    for ls_name, ls in LAYER_SETS.items():
        share[ls_name] = {}
        for pname, pkeys in list(pos_sets.items())[:2] + [("held routine", list(HELD_ROUTINE))]:
            row = {}
            for fam in families:
                F = families[fam][FIT_POOL]
                mu = F.astype(np.float64).mean(0)
                sd = F.astype(np.float64).std(0) + fam_floor[fam]
                d = np.concatenate([np.stack([whitened_sq(families[fam][k][:, l, :], mu[l], sd[l]) for l in ls], 1).sum(1) for k in pkeys])
                row[fam] = {"median": float(np.median(d)), "mean": float(d.mean())}
            share[ls_name][pname] = row
    results["item3_distance_levels"] = share

    # ---- per-trace view of the programming drift traces ---------------------------
    per_trace = {}
    for fam in families:
        F = families[fam][FIT_POOL]
        mu = F.astype(np.float64).mean(0)
        sd = F.astype(np.float64).std(0) + fam_floor[fam]
        ls = LAYERS_ML
        neg = np.concatenate([np.stack([whitened_sq(families[fam][k][:, l, :], mu[l], sd[l]) for l in ls], 1).sum(1) for k in HELD_ROUTINE])
        q95 = float(np.quantile(neg, 0.95))
        for k in prog_keys:
            d = np.stack([whitened_sq(families[fam][k][:, l, :], mu[l], sd[l]) for l in ls], 1).sum(1)
            own = np.asarray(owners[k])
            for tid in sorted(set(own)):
                m = own == tid
                per_trace.setdefault(tid, {"pool": k, "n_windows": int(m.sum())})
                per_trace[tid][fam] = {"median_over_q95": float(np.median(d[m]) / q95),
                                       "frac_over_q95": float((d[m] > q95).mean()),
                                       "auc_vs_held": auc(d[m], neg)}
    results["prog_per_trace_L5-15"] = per_trace

    # ---- item 4: cross-pool stability --------------------------------------------
    item4 = {}
    pairs = [("b1_routine", "b2_routine"), ("b2_routine", "c1"), ("b1_routine", "c1")]
    for rep in ("P", "S", "Q", "rP", "rQ", "rP_knn"):
        data = families[rep]
        rows = {"pairs": {}, "code": {}, "other": {}}
        means = {k: data[k].astype(np.float64).mean(0) for k in ROUTINE_POOLS}  # [16,64]
        prog_mean = np.concatenate([data[k] for k in prog_keys], 0).astype(np.float64).mean(0)
        other_mean = np.concatenate([data[k] for k in other_keys], 0).astype(np.float64).mean(0)
        for a, b in pairs:
            l2, js, ratio = [], [], []
            for l in LAYERS_ALL:
                diff = means[a][l] - means[b][l]
                n = np.linalg.norm(diff)
                u = diff / max(n, 1e-12)
                sa = (data[a][:, l, :].astype(np.float64) @ u).std()
                sb = (data[b][:, l, :].astype(np.float64) @ u).std()
                l2.append(float(n))
                ratio.append(float(n / (0.5 * (sa + sb))))
                js.append(js_divergence(means[a][l], means[b][l]) if rep in ("P", "Q") else None)
            rows["pairs"][f"{a} vs {b}"] = {"l2": l2, "js": js, "shift_over_within_sd": ratio}
        mean_shift = np.mean([rows["pairs"][f"{a} vs {b}"]["l2"] for a, b in pairs], 0)
        for label, m in (("code", prog_mean), ("other", other_mean)):
            sep, sep_sd, margin = [], [], []
            for l in LAYERS_ALL:
                diff = m[l] - means[FIT_POOL][l]
                n = np.linalg.norm(diff)
                u = diff / max(n, 1e-12)
                sfit = (data[FIT_POOL][:, l, :].astype(np.float64) @ u).std()
                sep.append(float(n))
                sep_sd.append(float(n / sfit))
                margin.append(float(n / mean_shift[l]))
            rows[label] = {"sep_l2_vs_b2_routine": sep, "sep_over_within_sd": sep_sd, "margin_ratio_sep_over_mean_pool_shift": margin}
        item4[rep] = rows
    results["item4_cross_pool"] = item4
    print(f"[item4] {time.time() - t0:.0f}s", flush=True)

    # ---- item 5 extra: is the loss inside or outside the selected set -------------
    # decompose P window mean into in-set part (P*S per token) and out-of-set part.
    # Since Q is only in-set, R2 of Q~S vs R2 of P~S already answers it; add the out-of-set
    # mass level for context.
    results["item5_top8_mass_by_pool"] = {k: v["top8_mass"].tolist() for k, v in flat.items()}

    (out / "results.json").write_text(json.dumps(results, indent=1))
    write_tables(results, out / "tables.md")
    print(f"[done] {time.time() - t0:.0f}s -> {out}", flush=True)


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------
def fmt(x, nd=3):
    if x is None:
        return "-"
    return f"{x:.{nd}f}"


def write_tables(res: dict, path: Path) -> None:
    L = []
    meta = res["meta"]
    L.append("# explore_prob_information -- auto-generated tables (EXPLORATORY / DIAGNOSTIC)\n")
    L.append(f"w={meta['w']}, routine stride {meta['routine_stride']}, drift stride {meta['drift_stride']}, seed {meta['seed']}, "
             f"bootstrap {meta['n_boot']} trace-level resamples, fit pool {meta['fit_pool']}, floors {meta['floors']}.\n")
    L.append("## Pool sizes\n")
    L.append("| pool | traces | windows |\n|---|---:|---:|")
    for k, v in res["counts"].items():
        L.append(f"| {k} | {v['n_traces']} | {v['n_windows']} |")
    L.append("")

    L.append("## Item 1: gate flatness per layer (per-token, tokens covered by the pools)\n")
    fl = res["item1_flatness"]
    for stat, title in (("ent8", "entropy of renormalised top-8 / log 8"), ("top1_share", "mean top-1 share of the top-8 mass"),
                        ("top1_gt_half", "fraction of tokens with top-1 share > 0.5"), ("top8_mass", "total softmax mass inside the top-8"),
                        ("ent64", "entropy of the full softmax / log 64"), ("eff_k", "effective number of experts exp(H) inside the top-8")):
        L.append(f"### {title}\n")
        pools = sorted(fl, key=lambda k: (":" in k, k))
        L.append("| layer | " + " | ".join(pools) + " |\n|---:|" + "---:|" * len(pools))
        for l in range(16):
            L.append(f"| {l} | " + " | ".join(fmt(fl[p][stat][l]) for p in pools) + " |")
        L.append("| mean L0-15 | " + " | ".join(fmt(float(np.mean(fl[p][stat]))) for p in pools) + " |")
        L.append("| mean L5-15 | " + " | ".join(fmt(float(np.mean(fl[p][stat][5:]))) for p in pools) + " |")
        L.append("| n tokens | " + " | ".join(str(fl[p]["n_tokens"]) for p in pools) + " |")
        L.append("")

    for item, target in (("item2_fit_P_on_S", "P (full softmax)"), ("item5_fit_Q_on_S", "Q (renormalised top-8)"),
                         ("item5_fit_Pout_on_S", "Pout (softmax mass outside the top-8, per expert)")):
        d = res[item]
        L.append(f"## {item}: least squares {target} ~ S per layer, fitted on b2 routine\n")
        for kind, title in (("full", "R2 (own-mean), full 64x64 linear fit"), ("diag", "R2 (own-mean), diagonal fit (each p_e from s_e only)"),
                            ("resid_energy_rel_fit", "residual energy / fit-pool total variance (per window, full fit)")):
            L.append(f"### {title}\n")
            names = list(d[kind])
            L.append("| pool | " + " | ".join(f"L{l}" for l in range(16)) + " | mean L5-15 | mean all |\n|---|" + "---:|" * 18)
            for n in names:
                v = d[kind][n]
                L.append(f"| {n} | " + " | ".join(fmt(x, 2) for x in v) + f" | {fmt(float(np.mean(v[5:])))} | {fmt(float(np.mean(v)))} |")
            L.append("")

    L.append("## Item 3 / 5: AUC of whitened squared distance (fit pool = b2 routine)\n")
    L.append("Families: S = indicator means (WGM-style), P = full softmax means, Q = renormalised top-8 means, "
             "rP = residual of P after the linear fit on S, rQ = residual of Q after the fit on S.\n")
    a3 = res["item3_auc"]
    fams = list(a3)
    for ls_name in a3[fams[0]]:
        L.append(f"### layer set {ls_name}\n")
        keys = list(a3[fams[0]][ls_name])
        L.append("| positives vs negatives | n_pos | n_neg | " + " | ".join(fams) + " |\n|---|---:|---:|" + "---:|" * len(fams))
        for k in keys:
            e0 = a3[fams[0]][ls_name][k]
            cells = []
            for f in fams:
                e = a3[f][ls_name][k]
                c = fmt(e["auc"])
                if "ci95_trace_boot" in e:
                    c += f" [{e['ci95_trace_boot'][0]:.2f},{e['ci95_trace_boot'][1]:.2f}]"
                cells.append(c)
            L.append(f"| {k} | {e0['n_pos']} | {e0['n_neg']} | " + " | ".join(cells) + " |")
        L.append("")
        L.append("median(pos) / q95(held routine) of the distance:\n")
        L.append("| positives | " + " | ".join(fams) + " |\n|---|" + "---:|" * len(fams))
        for k in keys:
            if "median_pos_over_q95_neg" in a3[fams[0]][ls_name][k]:
                L.append(f"| {k} | " + " | ".join(fmt(a3[f][ls_name][k]["median_pos_over_q95_neg"], 2) for f in fams) + " |")
        L.append("")
    L.append("### per-layer AUC vs held routine (b1_routine + c1), single layer\n")
    pl = res["item3_per_layer_auc_vs_held"]
    for pname in pl[fams[0]]:
        L.append(f"positives: {pname}\n")
        L.append("| family | " + " | ".join(f"L{l}" for l in range(16)) + " |\n|---|" + "---:|" * 16)
        for f in fams:
            L.append(f"| {f} | " + " | ".join(fmt(x, 2) for x in pl[f][pname]) + " |")
        L.append("")
    L.append("### distance levels (median of whitened squared distance)\n")
    dl = res["item3_distance_levels"]
    for ls_name, rows in dl.items():
        L.append(f"layer set {ls_name}\n")
        L.append("| windows | " + " | ".join(fams) + " |\n|---|" + "---:|" * len(fams))
        for pname, row in rows.items():
            L.append(f"| {pname} | " + " | ".join(fmt(row[f]["median"], 1) for f in fams) + " |")
        L.append("")

    L.append("### programming drift traces, one row per trace (L5-15, negatives = held routine)\n")
    pt = res["prog_per_trace_L5-15"]
    L.append("| trace | pool | windows | " + " | ".join(f"{f} med/q95" for f in fams) + " | " + " | ".join(f"{f} frac>q95" for f in fams) + " |\n|---|---|---:|" + "---:|" * (2 * len(fams)))
    for tid in sorted(pt, key=lambda t: (pt[t]["pool"], t)):
        r = pt[tid]
        L.append(f"| {tid} | {r['pool']} | {r['n_windows']} | " + " | ".join(fmt(r[f]["median_over_q95"], 2) for f in fams) + " | " + " | ".join(fmt(r[f]["frac_over_q95"], 2) for f in fams) + " |")
    L.append("")
    L.append("## Item 4: cross-pool stability of routine means, per layer\n")
    i4 = res["item4_cross_pool"]
    for rep in i4:
        L.append(f"### representation {rep}\n")
        L.append("| quantity | " + " | ".join(f"L{l}" for l in range(16)) + " | mean L5-15 |\n|---|" + "---:|" * 17)
        for pair, v in i4[rep]["pairs"].items():
            L.append(f"| {pair}: L2 shift | " + " | ".join(fmt(x, 4) for x in v["l2"]) + f" | {fmt(float(np.mean(v['l2'][5:])), 4)} |")
            if v["js"][0] is not None:
                L.append(f"| {pair}: JS (nats) | " + " | ".join(fmt(x, 4) for x in v["js"]) + f" | {fmt(float(np.mean(v['js'][5:])), 4)} |")
            L.append(f"| {pair}: shift / within sd | " + " | ".join(fmt(x, 2) for x in v["shift_over_within_sd"]) + f" | {fmt(float(np.mean(v['shift_over_within_sd'][5:])), 2)} |")
        for lab in ("code", "other"):
            v = i4[rep][lab]
            L.append(f"| {lab} sep L2 vs b2 routine | " + " | ".join(fmt(x, 4) for x in v["sep_l2_vs_b2_routine"]) + f" | {fmt(float(np.mean(v['sep_l2_vs_b2_routine'][5:])), 4)} |")
            L.append(f"| {lab} sep / within sd (b2) | " + " | ".join(fmt(x, 2) for x in v["sep_over_within_sd"]) + f" | {fmt(float(np.mean(v['sep_over_within_sd'][5:])), 2)} |")
            L.append(f"| {lab} margin ratio (sep / mean pool shift) | " + " | ".join(fmt(x, 2) for x in v["margin_ratio_sep_over_mean_pool_shift"]) + f" | {fmt(float(np.mean(v['margin_ratio_sep_over_mean_pool_shift'][5:])), 2)} |")
        L.append("")
    path.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
