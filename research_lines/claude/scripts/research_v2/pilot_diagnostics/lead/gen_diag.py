"""Lead's replacement for the failed generalization scout. POST-HOC PILOT DIAGNOSTICS on development data.
Uses the residual scout's decode cache (top-8 indicators [16,T,64]) for B1 (brief=absent) and B2."""
import json, sys, math
from pathlib import Path
import numpy as np, torch
torch.set_num_threads(16)
SP = Path(__file__).resolve().parents[1]
CACHE = SP / "scouts/residual/cache"
OUT = Path(__file__).resolve().parent
W = 16
MID = list(range(5, 12))

def load(batch):
    rows = torch.load(CACHE / f"{batch}_decode.pt", weights_only=False)
    if batch == "b1":
        rows = [r for r in rows if r["brief"] == "absent"]
    out = []
    for r in rows:
        ind = r["ind"]  # [16,T,64]
        T = ind.shape[1]
        if T < W:
            wins = torch.empty(0, 1024); ends = torch.empty(0, dtype=torch.long)
        else:
            x = ind.permute(1, 0, 2).reshape(T, 1024)
            cs = torch.cat([torch.zeros(1, 1024), x.cumsum(0)])
            wins = (cs[W:] - cs[:-W]) / W
            ends = torch.arange(W - 1, T)
        out.append(dict(tid=r["trace_id"], pg=r["pair_group_id"], arm=r["arm"], domain=r["domain"],
                        positive=r["positive"], boundary=r["boundary"], wins=wins, ends=ends, batch=batch,
                        workflow=r["workflow"]))
    return out

def roles(t):
    """window masks by role"""
    e = t["ends"]
    if t["positive"]:
        b = t["boundary"]
        return dict(post=e >= b + W - 1, pre=e < b, post_any=e >= b)
    return dict(all=torch.ones_like(e, dtype=torch.bool))

def auroc(pos, neg):
    pos = np.asarray(pos, float); neg = np.asarray(neg, float)
    if len(pos) == 0 or len(neg) == 0: return float("nan")
    return float(((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean()))

def benign_of(traces):
    m = {}
    for t in traces:
        if t["arm"] == "benign_control": m[t["pg"]] = t
    return m

def fit_directions(train, dims):
    routine = [t for t in train if t["arm"] in ("clean", "benign_control") and len(t["ends"])]
    R = torch.cat([t["wins"][:, dims] for t in routine])
    mu = R.mean(0); sd = R.std(0) + 1e-3
    ben = benign_of(train)
    dt_m, dt_u, dp = [], [], []
    for t in train:
        if not len(t["ends"]): continue
        rl = roles(t)
        if t["positive"] and rl["post"].any():
            post = t["wins"][rl["post"]][:, dims].mean(0)
            dt_u.append(post - mu)
            if t["pg"] in ben and len(ben[t["pg"]]["ends"]):
                dt_m.append(post - ben[t["pg"]]["wins"][:, dims].mean(0))
            if rl["pre"].any() and t["pg"] in ben and len(ben[t["pg"]]["ends"]):
                dp.append(t["wins"][rl["pre"]][:, dims].mean(0) - ben[t["pg"]]["wins"][:, dims].mean(0))
        elif t["arm"] == "attack" and not t["positive"] and t["pg"] in ben and len(ben[t["pg"]]["ends"]):
            dp.append(t["wins"][:, dims].mean(0) - ben[t["pg"]]["wins"][:, dims].mean(0))
    d_task_m = torch.stack(dt_m).mean(0) / sd
    d_task_u = torch.stack(dt_u).mean(0) / sd
    d_prop = torch.stack(dp).mean(0) / sd
    p = d_prop / d_prop.norm()
    u_raw = d_task_m / d_task_m.norm()
    u_orth = d_task_m - (d_task_m @ p) * p; u_orth = u_orth / u_orth.norm()
    return dict(mu=mu, sd=sd, d_task_m=d_task_m, d_task_u=d_task_u, d_prop=d_prop, u_raw=u_raw, u_orth=u_orth,
                n_task=len(dt_m), n_prop=len(dp), R=R)

def score_traces(test, dims, mu, sd, u):
    res = dict(drift_post=[], drift_pre=[], routine=[], resist=[], drift_full=[], benign=[], clean=[])
    for t in test:
        if not len(t["ends"]): 
            continue
        z = ((t["wins"][:, dims] - mu) / sd) @ u
        rl = roles(t)
        if t["positive"]:
            if rl["post_any"].any(): res["drift_post"].append(float(z[rl["post_any"]].max()))
            if rl["pre"].any(): res["drift_pre"].append(float(z[rl["pre"]].max()))
            res["drift_full"].append(float(z.max()))
        else:
            m = float(z.max())
            if t["arm"] == "attack": res["resist"].append(m)
            else:
                res["routine"].append(m); res[t["arm"] if t["arm"]=="clean" else "benign"].append(m)
    return res

def summarize(res):
    return dict(post_vs_routine=auroc(res["drift_post"], res["routine"]),
                post_vs_resist=auroc(res["drift_post"], res["resist"]),
                pre_vs_routine=auroc(res["drift_pre"], res["routine"]),
                resist_vs_routine=auroc(res["resist"], res["routine"]),
                benign_vs_clean=auroc(res["benign"], res["clean"]),
                full_vs_nondrift=auroc(res["drift_full"], res["routine"] + res["resist"]),
                n=dict(post=len(res["drift_post"]), pre=len(res["drift_pre"]), routine=len(res["routine"]), resist=len(res["resist"])))

def oneclass_fit(R, k):
    # R already whitened [n,d]
    Rc = R - R.mean(0)
    if k == 0: return None
    U, S, V = torch.linalg.svd(Rc, full_matrices=False)
    return V[:k]  # [k,d]

def oneclass_score(test, dims, mu, sd, P, Rmean):
    res = dict(drift_post=[], drift_pre=[], routine=[], resist=[], drift_full=[], benign=[], clean=[])
    for t in test:
        if not len(t["ends"]): continue
        z = (t["wins"][:, dims] - mu) / sd - Rmean
        e = (z ** 2).sum(1)
        if P is not None:
            proj = z @ P.T
            e = e - (proj ** 2).sum(1)
        rl = roles(t)
        if t["positive"]:
            if rl["post_any"].any(): res["drift_post"].append(float(e[rl["post_any"]].max()))
            if rl["pre"].any(): res["drift_pre"].append(float(e[rl["pre"]].max()))
            res["drift_full"].append(float(e.max()))
        else:
            m = float(e.max())
            if t["arm"] == "attack": res["resist"].append(m)
            else:
                res["routine"].append(m); res[t["arm"] if t["arm"]=="clean" else "benign"].append(m)
    return res

def cos(a, b): return float(a @ b / (a.norm() * b.norm()))

b1 = load("b1"); b2 = load("b2")
print("loaded", len(b1), len(b2), flush=True)
out = {}
ALL = list(range(1024)); MIDD = [l * 64 + e for l in MID for e in range(64)]
for name, dims in (("all", ALL), ("mid5-11", MIDD)):
    f1 = fit_directions(b1, dims); f2 = fit_directions(b2, dims)
    stab = dict(cos_task_matched=cos(f1["d_task_m"], f2["d_task_m"]), cos_task_unmatched=cos(f1["d_task_u"], f2["d_task_u"]),
                cos_prop=cos(f1["d_prop"], f2["d_prop"]), cos_task_prop_b1=cos(f1["d_task_m"], f1["d_prop"]),
                cos_task_prop_b2=cos(f2["d_task_m"], f2["d_prop"]), n_task=(f1["n_task"], f2["n_task"]), n_prop=(f1["n_prop"], f2["n_prop"]))
    # null cosine: split B1 drift in halves? skip; give random baseline ~0
    print(name, "stability", json.dumps(stab), flush=True)
    xb = {}
    for src, dst, fs, test in (("b1->b2", "b2", f1, b2), ("b2->b1", "b1", f2, b1)):
        row = {}
        for un in ("u_raw", "u_orth"):
            row[un] = summarize(score_traces(test, dims, fs["mu"], fs["sd"], fs[un]))
        # unmatched task direction
        uu = fs["d_task_u"] / fs["d_task_u"].norm()
        row["u_unmatched"] = summarize(score_traces(test, dims, fs["mu"], fs["sd"], uu))
        # sign split
        neg = fs["u_raw"].clamp(max=0); pos = fs["u_raw"].clamp(min=0)
        row["u_negative_only"] = summarize(score_traces(test, dims, fs["mu"], fs["sd"], neg / neg.norm()))
        row["u_positive_only"] = summarize(score_traces(test, dims, fs["mu"], fs["sd"], pos / pos.norm()))
        # prop direction alone
        row["u_prop_alone"] = summarize(score_traces(test, dims, fs["mu"], fs["sd"], fs["d_prop"] / fs["d_prop"].norm()))
        # one-class
        Rw = (fs["R"] - fs["mu"]) / fs["sd"]; Rmean = Rw.mean(0)
        for k in (0, 8, 32):
            P = oneclass_fit(Rw, k)
            row[f"oneclass_k{k}"] = summarize(oneclass_score(test, dims, fs["mu"], fs["sd"], P, Rmean))
        xb[src] = row
        for kk, v in row.items():
            print(f"  {name} {src} {kk}: post_vs_routine={v['post_vs_routine']:.3f} post_vs_resist={v['post_vs_resist']:.3f} pre_vs_routine={v['pre_vs_routine']:.3f} resist_vs_routine={v['resist_vs_routine']:.3f} benign_vs_clean={v['benign_vs_clean']:.3f} full_vs_nondrift={v['full_vs_nondrift']:.3f}", flush=True)
    out[name] = dict(stability=stab, cross_batch=xb)

# LODO pooled
pooled = b1 + b2
domains = sorted({t["domain"] for t in pooled if t["arm"] == "attack"})
pg_domain = {t["pg"]: t["domain"] for t in pooled if t["arm"] == "attack"}
lodo = {}
for name, dims in (("all", ALL), ("mid5-11", MIDD)):
    lodo[name] = {}
    for d in domains:
        train = [t for t in pooled if pg_domain.get(t["pg"]) != d]
        test = [t for t in pooled if pg_domain.get(t["pg"]) == d]
        fs = fit_directions(train, dims)
        row = {}
        for un in ("u_raw", "u_orth"):
            row[un] = summarize(score_traces(test, dims, fs["mu"], fs["sd"], fs[un]))
        Rw = (fs["R"] - fs["mu"]) / fs["sd"]; Rmean = Rw.mean(0)
        for k in (0, 8, 32):
            row[f"oneclass_k{k}"] = summarize(oneclass_score(test, dims, fs["mu"], fs["sd"], oneclass_fit(Rw, k), Rmean))
        lodo[name][d] = row
        print(f"LODO {name} {d} n_drift={row['u_raw']['n']['post']}: " + " ".join(f"{k}={v['post_vs_routine']:.3f}/{v['post_vs_resist']:.3f}" for k, v in row.items()), flush=True)
out["lodo"] = lodo
json.dump(out, open(OUT / "gen_diag_results.json", "w"), indent=1)
print("saved")
