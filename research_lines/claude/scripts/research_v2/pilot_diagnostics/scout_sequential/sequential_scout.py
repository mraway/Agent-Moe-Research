"""Post-hoc pilot diagnostics on frozen B2 v1 route window scores (model trained on B1).

Everything here is a post-hoc diagnostic on development data to inform proposal design.
It is NOT a result and NOT independent confirmation of anything.
"""
from __future__ import annotations
import json, math, sys
from collections import defaultdict
import numpy as np

SRC = "/home/wzh/Agent-Moe-Research/artifacts/agent_v2/agent_v2_5_b2/sequential_evaluation.json"
OUT_DIR = "/tmp/claude-1000/-home-wzh-Agent-Moe-Research--claude-worktrees-algorithm-research-proposals-427363/7e87c1f8-78bb-4b9f-9189-12780e6e8833/scratchpad/scouts/sequential"

data = json.load(open(SRC))
traces = data["trace_results"]
assert len(traces) == 240

class T:  # trace holder
    pass

TR = []
for t in traces:
    o = T()
    o.id = t["trace_id"]; o.pg = t["pair_group_id"]; o.arm = t["arm"]; o.wf = t["workflow"]
    o.ch = t["channel"]; o.dom = t["domain"]; o.pos = bool(t["primary_positive"]); o.b = t["boundary"]
    o.n = t["decode_token_count"]
    o.ends = np.array(t["models"]["route"]["window_ends"], dtype=int)
    o.s = np.array(t["models"]["route"]["window_scores"], dtype=float)
    assert len(o.ends) == len(o.s) and len(o.s) > 0
    TR.append(o)
DRIFT = [o for o in TR if o.pos]
NEG = [o for o in TR if not o.pos]
assert len(DRIFT) == 35 and len(NEG) == 205
PRE_ELIG = [o for o in DRIFT if (o.ends < o.b).any()]
print("drift", len(DRIFT), "non-drift", len(NEG), "drift with pre-boundary windows", len(PRE_ELIG))

def auroc(pos, neg):
    pos = np.asarray(pos, float); neg = np.asarray(neg, float)
    allv = np.concatenate([pos, neg])
    order = allv.argsort(kind="mergesort")
    ranks = np.empty(len(allv)); 
    # average ranks for ties
    sv = allv[order]
    i = 0
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    rp = ranks[:len(pos)].sum()
    return float((rp - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))

# ---------------------------------------------------------------- task 1
def trace_max_full(o, vals=None, ends=None):
    v = o.s if vals is None else vals
    return float(v.max())

def post_boundary_max(o, vals=None, ends=None):
    v = o.s if vals is None else vals; e = o.ends if ends is None else ends
    m = e >= o.b
    return float(v[m].max()) if m.any() else float("nan")

neg_max = [trace_max_full(o) for o in NEG]
drift_post = [post_boundary_max(o) for o in DRIFT]
drift_full = [trace_max_full(o) for o in DRIFT]
task1 = {
    "note": "post-hoc on frozen B2 v1 route scores (model trained on B1); pilot diagnostic, not a result",
    "n_drift": len(DRIFT), "n_non_drift": len(NEG), "n_drift_with_pre_boundary_windows": len(PRE_ELIG),
    "auroc_post_boundary_max_vs_non_drift_max": auroc(drift_post, neg_max),
    "auroc_full_trace_max_vs_non_drift_max": auroc(drift_full, neg_max),
    "non_drift_max_quantiles": {q: float(np.quantile(neg_max, q)) for q in (0.5, 0.9, 0.95, 0.99, 1.0)},
    "drift_post_boundary_max_quantiles": {q: float(np.quantile(drift_post, q)) for q in (0.1, 0.25, 0.5, 0.75, 0.9)},
    "drift_full_max_quantiles": {q: float(np.quantile(drift_full, q)) for q in (0.1, 0.25, 0.5, 0.75, 0.9)},
    "n_drift_full_max_gt_post_max": int(sum(f > p for f, p in zip(drift_full, drift_post))),
}
print(json.dumps(task1, indent=1))

# ---------------------------------------------------------------- reference stats (leave own scenario out)
def ref_stats(o, by_workflow):
    pool = [p for p in NEG if p.pg != o.pg and (not by_workflow or p.wf == o.wf)]
    allv = np.concatenate([p.s for p in pool])
    return float(allv.mean()), float(allv.std(ddof=1)), len(pool)

REF = {}
for o in TR:
    REF[o.id] = {"global": ref_stats(o, False), "wf": ref_stats(o, True)}

# ---------------------------------------------------------------- statistics
def stat_persist(o, k):
    if len(o.s) < k:
        return np.empty(0), np.empty(0, int)
    v = np.lib.stride_tricks.sliding_window_view(o.s, k).min(axis=1)
    return v, o.ends[k - 1:]

def stat_ewma(o, lam):
    v = np.empty_like(o.s); m = o.s[0]
    for i, x in enumerate(o.s):
        m = lam * x + (1 - lam) * m if i else x
        v[i] = m
    return v, o.ends

def stat_cusum(o, kappa, ref):
    mu, sd, _ = REF[o.id][ref]
    z = (o.s - mu) / sd
    v = np.empty_like(z); c = 0.0
    for i, zi in enumerate(z):
        c = max(0.0, c + zi - kappa); v[i] = c
    return v, o.ends

def stat_wfmean(o):
    mu, sd, _ = REF[o.id]["wf"]
    return o.s - mu, o.ends

STATS = {
    "raw_max_p1": lambda o: stat_persist(o, 1),
    "persist_2": lambda o: stat_persist(o, 2),
    "persist_3": lambda o: stat_persist(o, 3),
    "ewma_0.1": lambda o: stat_ewma(o, 0.1),
    "ewma_0.2": lambda o: stat_ewma(o, 0.2),
    "ewma_0.3": lambda o: stat_ewma(o, 0.3),
}
for k in (0.25, 0.5, 1.0):
    STATS[f"cusum_global_k{k}"] = (lambda kk: (lambda o: stat_cusum(o, kk, "global")))(k)
    STATS[f"cusum_wf_k{k}"] = (lambda kk: (lambda o: stat_cusum(o, kk, "wf")))(k)
STATS["wf_mean_sub_max"] = stat_wfmean

def order_stat_threshold(maxima, alpha):
    n = len(maxima); k = math.ceil((n + 1) * (1 - alpha))
    k = min(k, n)
    return float(np.sort(maxima)[k - 1]), k

def alarm_row(o, v, e, thr):
    states = v >= thr
    prev = np.concatenate([[False], states[:-1]])
    onset = states & ~prev
    if o.pos:
        pre = states & (e < o.b); post = states & (e >= o.b)
        first_post = int(e[post][0]) if post.any() else None
        first_pre = int(e[pre][0]) if pre.any() else None
        return dict(positive=True, boundary=o.b, neg_positions=int((e < o.b).sum()), first_pre=first_pre,
                    pre_lead=None if first_pre is None else o.b - first_pre, domain=o.dom, workflow=o.wf,
                    pre_onsets=int((onset & (e < o.b)).sum()), pre_alarm=bool(pre.any()),
                    first_post=first_post, latency=None if first_post is None else first_post - o.b)
    return dict(positive=False, neg_positions=int(len(e)), onsets=int(onset.sum()), false_alarm=bool(states.any()))

def evaluate(stat_fn, alpha):
    vals = {o.id: stat_fn(o) for o in TR}
    neg_maxima = [float(vals[o.id][0].max()) for o in NEG if len(vals[o.id][0])]
    thr, k = order_stat_threshold(neg_maxima, alpha)
    rows = {o.id: alarm_row(o, vals[o.id][0], vals[o.id][1], thr) for o in TR if len(vals[o.id][0])}
    negs = [rows[o.id] for o in NEG if o.id in rows]
    poss = [rows[o.id] for o in DRIFT if o.id in rows]
    pre_elig = [r for r in poss if r["neg_positions"] > 0]
    def recall(limit):
        return sum(1 for r in poss if not r["pre_alarm"] and r["first_post"] is not None
                   and (limit is None or r["first_post"] <= r["boundary"] + limit)) / len(DRIFT)
    lat = [r["latency"] for r in poss if not r["pre_alarm"] and r["latency"] is not None]
    def any_post(limit):
        return sum(1 for r in poss if r["first_post"] is not None and (limit is None or r["first_post"] <= r["boundary"] + limit)) / len(DRIFT)
    leads = sorted(r["pre_lead"] for r in poss if r["pre_lead"] is not None)
    neg_pos = sum(r["neg_positions"] for r in rows.values())
    neg_onsets = sum(r["onsets"] for r in negs) + sum(r["pre_onsets"] for r in poss)
    drift_post = [post_boundary_max(o, *vals[o.id]) for o in DRIFT]
    drift_full = [float(vals[o.id][0].max()) for o in DRIFT if len(vals[o.id][0])]
    return dict(
        threshold=thr, order_stat_k=k, n_neg_calib=len(neg_maxima),
        realized_far=sum(r["false_alarm"] for r in negs) / len(negs), far_count=int(sum(r["false_alarm"] for r in negs)),
        drift_pre_boundary_far=sum(r["pre_alarm"] for r in pre_elig) / len(pre_elig), pre_far_count=int(sum(r["pre_alarm"] for r in pre_elig)), pre_elig=len(pre_elig),
        clean_recall_8=recall(8), clean_recall_16=recall(16), clean_recall_32=recall(32), clean_recall_final=recall(None),
        clean_detect_counts={"+8": round(recall(8) * 35), "+16": round(recall(16) * 35), "+32": round(recall(32) * 35), "final": round(recall(None) * 35)},
        median_latency_clean=float(np.median(lat)) if lat else None, n_clean_detections=len(lat),
        any_post_recall_16=any_post(16), any_post_recall_final=any_post(None),
        pre_boundary_alarm_lead_tokens=leads, pre_boundary_lead_median=float(np.median(leads)) if leads else None,
        onsets_per_1000_neg_positions=1000 * neg_onsets / neg_pos, neg_positions=int(neg_pos), neg_onsets=int(neg_onsets),
        auroc_post_boundary_max=auroc([x for x in drift_post if not np.isnan(x)], neg_maxima), n_drift_with_stat=len([x for x in drift_post if not np.isnan(x)]),
        auroc_full_max=auroc(drift_full, neg_maxima),
        rows=rows,
    )

task2 = {}
for name, fn in STATS.items():
    task2[name] = {}
    for alpha in (0.05, 0.10):
        r = evaluate(fn, alpha)
        rows = r.pop("rows")
        task2[name][f"far{int(alpha*100)}"] = r
        task2[name][f"far{int(alpha*100)}"]["_rows"] = rows

def fmt(x, d=3):
    return "  n/a" if x is None else f"{x:.{d}f}"
hdr = f"{'statistic':20s} {'nomFAR':>6s} {'thr':>7s} {'FAR':>6s} {'preFAR':>7s} {'R+8':>5s} {'R+16':>5s} {'R+32':>5s} {'Rfin':>5s} {'medLat':>6s} {'ons/1k':>7s} {'AUCpost':>7s} {'AUCfull':>7s} {'anyP16':>6s} {'anyPfin':>7s} {'preLead':>7s}"
lines = [hdr]
for name in STATS:
    for a in ("far5", "far10"):
        r = task2[name][a]
        lines.append(f"{name:20s} {a:>6s} {r['threshold']:7.3f} {r['realized_far']:6.3f} {r['drift_pre_boundary_far']:7.3f} {r['clean_recall_8']:5.2f} {r['clean_recall_16']:5.2f} {r['clean_recall_32']:5.2f} {r['clean_recall_final']:5.2f} {fmt(r['median_latency_clean'],1):>6s} {r['onsets_per_1000_neg_positions']:7.1f} {r['auroc_post_boundary_max']:7.3f} {r['auroc_full_max']:7.3f} {r['any_post_recall_16']:6.2f} {r['any_post_recall_final']:7.2f} {fmt(r['pre_boundary_lead_median'],0):>7s}")
table2 = "\n".join(lines)
print(table2)

# ---------------------------------------------------------------- task 3 calibration size bootstrap (raw max)
rng = np.random.default_rng(20260904)
scen = defaultdict(list)
for o in NEG:
    scen[o.pg].append(o)
scen_ids = sorted(scen)
neg_max_by_id = {o.id: float(o.s.max()) for o in NEG}
task3 = {}
for alpha in (0.10, 0.05):
    task3[f"alpha{alpha}"] = {}
    for n in (20, 50, 100, 200):
        fars, recs, thrs, ntest = [], [], [], []
        for _ in range(500):
            perm = rng.permutation(len(scen_ids))
            calib, used = [], set()
            for i in perm:
                if len(calib) >= n: break
                used.add(scen_ids[i]); calib.extend(scen[scen_ids[i]])
            calib = calib[:n]
            test = [o for o in NEG if o.pg not in used]
            thr, _ = order_stat_threshold([neg_max_by_id[o.id] for o in calib], alpha)
            far = np.mean([neg_max_by_id[o.id] >= thr for o in test]) if test else float("nan")
            det = 0
            for o in DRIFT:
                st = o.s >= thr
                if (st & (o.ends < o.b)).any(): continue
                post = st & (o.ends >= o.b) & (o.ends <= o.b + 16)
                det += post.any()
            fars.append(far); recs.append(det / 35); thrs.append(thr); ntest.append(len(test))
        fars = np.array(fars); recs = np.array(recs); thrs = np.array(thrs)
        task3[f"alpha{alpha}"][f"n{n}"] = dict(
            far_median=float(np.nanmedian(fars)), far_p5=float(np.nanquantile(fars, 0.05)), far_p95=float(np.nanquantile(fars, 0.95)),
            far_mean=float(np.nanmean(fars)),
            recall16_median=float(np.median(recs)), recall16_p5=float(np.quantile(recs, 0.05)), recall16_p95=float(np.quantile(recs, 0.95)),
            thr_median=float(np.median(thrs)), thr_p5=float(np.quantile(thrs, 0.05)), thr_p95=float(np.quantile(thrs, 0.95)),
            test_non_drift_median=float(np.median(ntest)), test_non_drift_min=int(min(ntest)),
            prob_far_over_2x_nominal=float(np.nanmean(fars > 2 * alpha)),
        )
lines = [f"{'alpha':>5s} {'n':>4s} {'thr med[5-95]':>22s} {'FAR med[5-95]':>22s} {'R+16 med[5-95]':>22s} {'ntest':>5s} {'P(FAR>2a)':>9s}"]
for a in task3:
    for n, r in task3[a].items():
        lines.append(f"{a[5:]:>5s} {n[1:]:>4s} {r['thr_median']:6.3f}[{r['thr_p5']:.3f},{r['thr_p95']:.3f}] {r['far_median']:6.3f}[{r['far_p5']:.3f},{r['far_p95']:.3f}] {r['recall16_median']:6.3f}[{r['recall16_p5']:.3f},{r['recall16_p95']:.3f}] {r['test_non_drift_median']:5.0f} {r['prob_far_over_2x_nominal']:9.2f}")
table3 = "\n".join(lines); print(table3)

# ---------------------------------------------------------------- task 4 own-baseline negative control
def own_baseline_stats(kind):
    out = {}
    def base(o):
        return float(np.median(o.s[:8])) if kind == "median8" else float(o.s[0])
    negv = [float((o.s - base(o)).max()) for o in NEG]
    posv = {o.id: post_boundary_max(o, o.s - base(o), o.ends) for o in DRIFT}
    lo = [o for o in DRIFT if o.b < 23]; hi = [o for o in DRIFT if o.b >= 23]
    out["auroc_all"] = auroc(list(posv.values()), negv)
    out["auroc_boundary_lt23"] = auroc([posv[o.id] for o in lo], negv)
    out["auroc_boundary_ge23"] = auroc([posv[o.id] for o in hi], negv)
    out["n_lt23"] = len(lo); out["n_ge23"] = len(hi)
    out["mean_baseline_non_drift"] = float(np.mean([base(o) for o in NEG]))
    out["mean_baseline_drift_lt23"] = float(np.mean([base(o) for o in lo]))
    out["mean_baseline_drift_ge23"] = float(np.mean([base(o) for o in hi]))
    out["mean_stat_non_drift"] = float(np.mean(negv))
    out["mean_stat_drift_lt23"] = float(np.mean([posv[o.id] for o in lo]))
    out["mean_stat_drift_ge23"] = float(np.mean([posv[o.id] for o in hi]))
    out["mean_raw_postmax_drift_lt23"] = float(np.mean([post_boundary_max(o) for o in lo]))
    out["mean_raw_postmax_drift_ge23"] = float(np.mean([post_boundary_max(o) for o in hi]))
    out["mean_raw_max_non_drift"] = float(np.mean(neg_max))
    out["raw_auroc_lt23"] = auroc([post_boundary_max(o) for o in lo], neg_max)
    out["raw_auroc_ge23"] = auroc([post_boundary_max(o) for o in hi], neg_max)
    out["sd_baseline_non_drift"] = float(np.std([base(o) for o in NEG], ddof=1))
    out["sd_rawmax_non_drift"] = float(np.std(neg_max, ddof=1))
    out["sd_stat_non_drift"] = float(np.std(negv, ddof=1))
    out["corr_baseline_rawmax_non_drift"] = float(np.corrcoef([base(o) for o in NEG], neg_max)[0, 1])
    return out
task4 = {"median_first8": own_baseline_stats("median8"), "first_window_only": own_baseline_stats("first")}
task4["raw_max_auroc_all"] = task1["auroc_post_boundary_max_vs_non_drift_max"]
print(json.dumps(task4, indent=1))

# ---------------------------------------------------------------- task 5 per-domain / per-channel
cus_names = [n for n in STATS if n.startswith("cusum")]
best_cusum = max(cus_names, key=lambda n: (task2[n]["far10"]["clean_recall_16"], task2[n]["far10"]["clean_recall_final"], -task2[n]["far10"]["drift_pre_boundary_far"]))
def strat(name, key):
    rows = task2[name]["far10"]["_rows"]
    groups = defaultdict(list)
    for o in DRIFT:
        groups[getattr(o, key)].append(rows[o.id])
    out = {}
    for g, rs in sorted(groups.items()):
        c16 = sum(1 for r in rs if not r["pre_alarm"] and r["first_post"] is not None and r["first_post"] <= r["boundary"] + 16)
        cf = sum(1 for r in rs if not r["pre_alarm"] and r["first_post"] is not None)
        pre = sum(1 for r in rs if r["pre_alarm"])
        out[g] = dict(n_drift=len(rs), clean_16=c16, clean_final=cf, pre_alarm=pre)
    # non-drift FAR by group
    fa = defaultdict(lambda: [0, 0])
    for o in NEG:
        fa[getattr(o, key)][0] += rows[o.id]["false_alarm"]; fa[getattr(o, key)][1] += 1
    for g in out:
        out[g]["non_drift_far"] = f"{fa[g][0]}/{fa[g][1]}" if fa[g][1] else "0/0"
    return out
task5 = {"best_cusum_variant": best_cusum, "by_domain": {n: strat(n, "dom") for n in ("raw_max_p1", best_cusum)},
         "by_channel": {n: strat(n, "ch") for n in ("raw_max_p1", best_cusum)}}
# extra: raw_max at far5 domain
task5["by_domain_far5_raw_max"] = None
lines = [f"{'domain':18s} {'n':>3s} | raw_max +16/fin/pre | {best_cusum} +16/fin/pre | nondrift FA raw / cusum"]
for d in task5["by_domain"]["raw_max_p1"]:
    a = task5["by_domain"]["raw_max_p1"][d]; b = task5["by_domain"][best_cusum][d]
    lines.append(f"{d:18s} {a['n_drift']:3d} | {a['clean_16']:2d}/{a['clean_final']:2d}/{a['pre_alarm']:2d}         | {b['clean_16']:2d}/{b['clean_final']:2d}/{b['pre_alarm']:2d}  | {a['non_drift_far']} / {b['non_drift_far']}")
lines.append("channel")
for d in task5["by_channel"]["raw_max_p1"]:
    a = task5["by_channel"]["raw_max_p1"][d]; b = task5["by_channel"][best_cusum][d]
    lines.append(f"{d:18s} {a['n_drift']:3d} | {a['clean_16']:2d}/{a['clean_final']:2d}/{a['pre_alarm']:2d}         | {b['clean_16']:2d}/{b['clean_final']:2d}/{b['pre_alarm']:2d}  | {a['non_drift_far']} / {b['non_drift_far']}")
table5 = "\n".join(lines); print("best cusum:", best_cusum); print(table5)
task5["zero_domains_plus16"] = {n: [d for d, r in task5["by_domain"][n].items() if r["clean_16"] == 0] for n in ("raw_max_p1", best_cusum)}
task5["zero_domains_final"] = {n: [d for d, r in task5["by_domain"][n].items() if r["clean_final"] == 0] for n in ("raw_max_p1", best_cusum)}

# ---------------------------------------------------------------- task 6 routine score scale
def quant(vals):
    return {q: float(np.quantile(vals, q)) for q in (0.1, 0.5, 0.9, 0.95, 1.0)}
def r2_oneway(vals, groups):
    vals = np.asarray(vals); groups = np.asarray(groups)
    gm = vals.mean(); sst = ((vals - gm) ** 2).sum(); ssb = 0
    for g in np.unique(groups):
        m = groups == g; ssb += m.sum() * (vals[m].mean() - gm) ** 2
    return float(ssb / sst)
def r2_additive(vals, g1, g2):
    vals = np.asarray(vals)
    cols = [np.ones(len(vals))]
    for gs in (g1, g2):
        u = sorted(set(gs))
        for lv in u[1:]:
            cols.append(np.array([x == lv for x in gs], float))
    X = np.stack(cols, 1)
    beta, *_ = np.linalg.lstsq(X, vals, rcond=None)
    res = vals - X @ beta
    return float(1 - (res ** 2).sum() / ((vals - vals.mean()) ** 2).sum())
wf_groups = defaultdict(list); arm_groups = defaultdict(list)
for o in NEG:
    wf_groups[o.wf].append(float(o.s.max())); arm_groups[o.arm].append(float(o.s.max()))
win_vals = np.concatenate([o.s for o in NEG]); win_wf = sum([[o.wf] * len(o.s) for o in NEG], []); win_arm = sum([[o.arm] * len(o.s) for o in NEG], [])
task6 = {
    "non_drift_max_by_workflow": {k: dict(n=len(v), **{f"q{q}": x for q, x in quant(v).items()}) for k, v in sorted(wf_groups.items())},
    "non_drift_max_by_arm": {k: dict(n=len(v), **{f"q{q}": x for q, x in quant(v).items()}) for k, v in sorted(arm_groups.items())},
    "r2_max_workflow": r2_oneway(neg_max, [o.wf for o in NEG]), "r2_max_arm": r2_oneway(neg_max, [o.arm for o in NEG]),
    "r2_max_workflow_plus_arm": r2_additive(np.array(neg_max), [o.wf for o in NEG], [o.arm for o in NEG]),
    "r2_window_workflow": r2_oneway(win_vals, win_wf), "r2_window_arm": r2_oneway(win_vals, win_arm),
    "r2_window_workflow_plus_arm": r2_additive(win_vals, win_wf, win_arm),
    "r2_window_trace_id": r2_oneway(win_vals, sum([[o.id] * len(o.s) for o in NEG], [])),
    "n_windows_non_drift": int(len(win_vals)),
    "window_score_sd_non_drift": float(win_vals.std(ddof=1)), "window_score_mean_non_drift": float(win_vals.mean()),
}
# which workflows contain the top non-drift maxima
top = sorted(NEG, key=lambda o: -o.s.max())[:10]
task6["top10_non_drift_max_traces"] = [dict(id=o.id, wf=o.wf, arm=o.arm, dom=o.dom, max=float(o.s.max()), argmax_end=int(o.ends[o.s.argmax()]), n=o.n) for o in top]
print(json.dumps({k: v for k, v in task6.items() if k != "top10_non_drift_max_traces"}, indent=1))
lines = [f"{'workflow':26s} {'n':>3s} {'q50':>7s} {'q90':>7s} {'q95':>7s} {'max':>7s}"]
for k, v in task6["non_drift_max_by_workflow"].items():
    lines.append(f"{k:26s} {v['n']:3d} {v['q0.5']:7.3f} {v['q0.9']:7.3f} {v['q0.95']:7.3f} {v['q1.0']:7.3f}")
for k, v in task6["non_drift_max_by_arm"].items():
    lines.append(f"{'arm:'+k:26s} {v['n']:3d} {v['q0.5']:7.3f} {v['q0.9']:7.3f} {v['q0.95']:7.3f} {v['q1.0']:7.3f}")
table6 = "\n".join(lines); print(table6)
for t in task6["top10_non_drift_max_traces"]: print(t)

# strip rows for JSON
for name in task2:
    for a in task2[name]:
        task2[name][a].pop("_rows", None)
out = {"disclaimer": "POST-HOC PILOT DIAGNOSTIC on frozen B2 v1 route window scores (16-token causal windows; ridge trained on B1). B1/B2 are development data; nothing here is a result or independent confirmation.",
       "source": SRC, "task1_trace_level": task1, "task2_sequential_statistics": task2, "task2_table": table2,
       "task3_calibration_size_bootstrap_raw_max": task3, "task3_table": table3,
       "task4_own_baseline_negative_control": task4, "task5_per_domain_channel": task5, "task5_table": table5,
       "task6_routine_score_scale": task6, "task6_table": table6}
json.dump(out, open(f"{OUT_DIR}/sequential_scout_results.json", "w"), indent=1, default=str)
open(f"{OUT_DIR}/tables.txt", "w").write("\n\n".join([table2, table3, table5, table6]))
print("written")
