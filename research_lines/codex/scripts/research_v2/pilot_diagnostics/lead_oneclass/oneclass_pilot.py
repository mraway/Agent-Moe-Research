"""Lead's one-class pilot: three routine-manifold definitions, deployment-side conformal calibration,
three readings. POST-HOC PILOT DIAGNOSTICS on development data (B1 brief=absent, B2)."""
import json, math, sys
from pathlib import Path
import numpy as np, torch
torch.set_num_threads(24)
SP = Path(__file__).resolve().parents[1]
CACHE = SP / "scouts/residual/cache"
OUT = Path(__file__).resolve().parent
W = 16; V = 50304
MID = [l * 64 + e for l in range(5, 12) for e in range(64)]
ALL = list(range(1024))

def load(batch):
    rows = torch.load(CACHE / f"{batch}_decode.pt", weights_only=False)
    if batch == "b1": rows = [r for r in rows if r["brief"] == "absent"]
    out = []
    for r in rows:
        ind = r["ind"]; T = ind.shape[1]
        out.append(dict(tid=r["trace_id"], pg=r["pair_group_id"], arm=r["arm"], domain=r["domain"], positive=r["positive"],
                        boundary=r["boundary"], T=T, ind=ind.permute(1, 0, 2).reshape(T, 1024).contiguous(),
                        top1=r["p"].argmax(-1).T.contiguous(),  # [T,16]
                        ids=r["token_ids"].clone(), batch=batch))
    return out

def routine(traces): return [t for t in traces if t["arm"] in ("clean", "benign_control")]

def win_mean(x, w=W):
    T = x.shape[0]
    if T < w: return x.new_empty((0, x.shape[1])), torch.empty(0, dtype=torch.long)
    cs = torch.cat([torch.zeros(1, x.shape[1]), x.cumsum(0)])
    return (cs[w:] - cs[:-w]) / w, torch.arange(w - 1, T)

# ---------- manifold 1: window geometry (Gaussian / PCA residual energy / kNN) ----------
def fit_geometry(train, dims, r_list=(0, 8), knn=True):
    Xs = [win_mean(t["ind"][:, dims])[0] for t in routine(train)]
    X = torch.cat([x for x in Xs if len(x)])
    mu = X.mean(0); sd = X.std(0) + 1e-3
    Z = (X - mu) / sd; zm = Z.mean(0); Zc = Z - zm
    U, S, Vt = torch.linalg.svd(Zc, full_matrices=False)
    st = dict(mu=mu, sd=sd, zm=zm, V=Vt, r_list=r_list)
    if knn:
        P32 = Vt[:32]
        R32 = Zc @ P32.T
        g = torch.Generator().manual_seed(0)
        idx = torch.randperm(len(R32), generator=g)[:6000]
        st["knn_ref"] = R32[idx]; st["P32"] = P32
    return st

def score_geometry(st, t, dims):
    x, ends = win_mean(t["ind"][:, dims])
    if not len(x): return {}, ends
    z = (x - st["mu"]) / st["sd"] - st["zm"]
    out = {}
    e_full = (z ** 2).sum(1)
    for r in st["r_list"]:
        if r == 0: out[f"gauss_r0"] = e_full
        else:
            P = st["V"][:r]; out[f"gauss_r{r}"] = e_full - ((z @ P.T) ** 2).sum(1)
    if "knn_ref" in st:
        q = z @ st["P32"].T
        d = torch.cdist(q, st["knn_ref"])
        out["knn10"] = d.topk(10, dim=1, largest=False).values.mean(1)
    return out, ends

# ---------- manifold 2: token-conditioned expectation ----------
def fit_tokcond(train, dims, m=5.0):
    s = torch.zeros(V, len(dims)); c = torch.zeros(V)
    tot = torch.zeros(len(dims)); n = 0
    for t in routine(train):
        x = t["ind"][:, dims]; s.index_add_(0, t["ids"], x); c.index_add_(0, t["ids"], torch.ones(len(t["ids"])))
        tot += x.sum(0); n += len(x)
    mu = tot / n
    E = (s + m * mu) / (c[:, None] + m)
    # residual scale from routine residual windows
    R = []
    for t in routine(train):
        r = t["ind"][:, dims] - E[t["ids"]]
        w, _ = win_mean(r)
        if len(w): R.append(w)
    R = torch.cat(R); rsd = R.std(0) + 1e-3; rmu = R.mean(0)
    return dict(E=E, count=c, mu=mu, rsd=rsd, rmu=rmu)

def score_tokcond(st, t, dims):
    r = t["ind"][:, dims] - st["E"][t["ids"]]
    w, ends = win_mean(r)
    if not len(w): return {}, ends
    z = (w - st["rmu"]) / st["rsd"]
    oov = (st["count"][t["ids"]] < 5).float()
    oovw, _ = win_mean(oov[:, None])
    return {"tokcond_energy": (z ** 2).sum(1), "oov_fraction": oovw[:, 0]}, ends

# ---------- manifold 3: path dynamics (depth chain + time chain over top-1 experts) ----------
def fit_path(train, alpha=0.5):
    depth = torch.full((15, 64, 64), alpha); first = torch.full((64,), alpha)
    time_ = torch.full((16, 64, 64), alpha)
    for t in routine(train):
        e = t["top1"]  # [T,16]
        first.index_add_(0, e[:, 0], torch.ones(len(e)))
        for l in range(15):
            depth[l].index_put_((e[:, l], e[:, l + 1]), torch.ones(len(e)), accumulate=True)
        for l in range(16):
            time_[l].index_put_((e[:-1, l], e[1:, l]), torch.ones(len(e) - 1), accumulate=True)
    logd = (depth / depth.sum(-1, keepdim=True)).log(); logf = (first / first.sum()).log()
    logt = (time_ / time_.sum(-1, keepdim=True)).log()
    st = dict(logd=logd, logf=logf, logt=logt)
    # per-token standardization from routine
    vals = {"path_depth": [], "path_time": []}
    for t in routine(train):
        s = score_path_tokens(st, t)
        for k in vals: vals[k].append(s[k])
    st["norm"] = {k: (torch.cat(v).mean(), torch.cat(v).std() + 1e-3) for k, v in vals.items()}
    return st

def score_path_tokens(st, t):
    e = t["top1"]; T = len(e)
    nll_d = -st["logf"][e[:, 0]].clone()
    for l in range(15): nll_d = nll_d - st["logd"][l][e[:, l], e[:, l + 1]]
    nll_t = torch.zeros(T)
    for l in range(16):
        nll_t[1:] = nll_t[1:] - st["logt"][l][e[:-1, l], e[1:, l]]
    nll_t[0] = nll_t[1:].mean() if T > 1 else 0.0
    return {"path_depth": nll_d, "path_time": nll_t}

def score_path(st, t):
    s = score_path_tokens(st, t); out = {}; ends = None
    for k, v in s.items():
        m, sd = st["norm"][k]
        w, ends = win_mean(((v - m) / sd)[:, None])
        if len(w): out[k] = w[:, 0]
    return out, (ends if ends is not None else torch.empty(0, dtype=torch.long))

# ---------- readings & calibration ----------
def bucket(ends): return torch.clamp(ends // 32, max=3)
def standardize(scores, ends, stats):
    b = bucket(ends); mu = stats["mu"][b]; sd = stats["sd"][b]
    return (scores - mu) / sd
def fit_bucket_stats(list_scores_ends):
    mus = []; sds = []
    for k in range(4):
        vals = torch.cat([s[bucket(e) == k] for s, e in list_scores_ends if len(s)])
        if len(vals) < 30 and k > 0: mus.append(mus[-1]); sds.append(sds[-1])
        else: mus.append(vals.mean()); sds.append(vals.std() + 1e-6)
    return dict(mu=torch.stack(mus), sd=torch.stack(sds))
def readings(z):
    out = {"max": z}
    ew = torch.zeros_like(z); acc = 0.0
    for i in range(len(z)): acc = 0.1 * float(z[i]) + 0.9 * acc if i else float(z[i]); ew[i] = acc
    out["ewma01"] = ew
    cu = torch.zeros_like(z); c = 0.0
    for i in range(len(z)): c = max(0.0, c + float(z[i]) - 1.0); cu[i] = c
    out["cusum1"] = cu
    return out

def alarm_metrics(traces_scores, h, reading):
    """traces_scores: list of (trace, S[nwin], ends). Returns counts."""
    m = dict(clean=[0, 0], benign=[0, 0], resist=[0, 0], drift=0, pre_strict=[0, 0], pre_tol=[0, 0],
             hit16=0, hit32=0, hitF=0, hit16_tol=0, hitF_tol=0, lat=[])
    for t, S, ends in traces_scores:
        al = ends[S >= h]
        if not t["positive"]:
            key = "resist" if t["arm"] == "attack" else ("clean" if t["arm"] == "clean" else "benign")
            m[key][1] += 1; m[key][0] += int(len(al) > 0)
        else:
            b = t["boundary"]; m["drift"] += 1
            has_pre = bool((ends < b).any())
            pre_s = bool((al < b).any()); pre_t = bool((al < b - 8).any())
            if has_pre: m["pre_strict"][1] += 1; m["pre_strict"][0] += int(pre_s)
            if bool((ends < b - 8).any()): m["pre_tol"][1] += 1; m["pre_tol"][0] += int(pre_t)
            post = al[al >= b]
            if not pre_s and len(post):
                lat = int(post[0]) - b; m["lat"].append(lat)
                m["hitF"] += 1; m["hit16"] += int(lat <= 16); m["hit32"] += int(lat <= 32)
            tol_al = al[al >= b - 8]
            if not pre_t and len(tol_al):
                lat = max(0, int(tol_al[0]) - b); m["hitF_tol"] += 1; m["hit16_tol"] += int(lat <= 16)
    return m

def conformal_h(maxima, alpha):
    v = sorted(maxima); n = len(v); k = math.ceil((n + 1) * (1 - alpha)); return v[min(k, n) - 1]

def evaluate_direction(src, dst, alpha=0.10):
    results = {}
    # fit manifolds on src routine
    geo = {name: fit_geometry(src, dims) for name, dims in (("mid", MID), ("all", ALL))}
    tok = {name: fit_tokcond(src, dims) for name, dims in (("mid", MID), ("all", ALL))}
    path = fit_path(src)
    # per-trace raw scores on dst
    per = {}  # scorer -> list of (trace, s, ends)
    for t in dst:
        for name, dims in (("mid", MID), ("all", ALL)):
            sc, ends = score_geometry(geo[name], t, dims)
            for k, v in sc.items(): per.setdefault(f"{k}_{name}", []).append((t, v, ends))
            sc, ends = score_tokcond(tok[name], t, dims)
            for k, v in sc.items():
                if k == "oov_fraction" and name == "all": continue
                per.setdefault(f"{k}_{name}" if k != "oov_fraction" else "oov_fraction", []).append((t, v, ends))
        sc, ends = score_path(path, t)
        for k, v in sc.items(): per.setdefault(k, []).append((t, v, ends))
    # ranking diagnostics + deployment-mode conformal alarms (split dst routine by scenario halves)
    pgs = sorted({t["pg"] for t in dst}); half = {pg: i % 2 for i, pg in enumerate(pgs)}
    for scorer, rows in per.items():
        # ranking (no calibration): trace-level maxima of raw score
        agg = dict(post=[], pre=[], routine=[], resist=[], clean=[], benign=[])
        for t, s, ends in rows:
            if not len(s): continue
            if t["positive"]:
                b = t["boundary"]
                if (ends >= b).any(): agg["post"].append(float(s[ends >= b].max()))
                if (ends < b).any(): agg["pre"].append(float(s[ends < b].max()))
            else:
                mx = float(s.max())
                if t["arm"] == "attack": agg["resist"].append(mx)
                else: agg["routine"].append(mx); agg["clean" if t["arm"] == "clean" else "benign"].append(mx)
        def au(a, b):
            a = np.array(a); b = np.array(b)
            return float(((a[:, None] > b[None, :]).mean() + 0.5 * (a[:, None] == b[None, :]).mean())) if len(a) and len(b) else float("nan")
        rank = dict(post_vs_routine=au(agg["post"], agg["routine"]), post_vs_resist=au(agg["post"], agg["resist"]),
                    pre_vs_routine=au(agg["pre"], agg["routine"]), resist_vs_routine=au(agg["resist"], agg["routine"]),
                    benign_vs_clean=au(agg["benign"], agg["clean"]))
        # alarms: for each half as calibration
        alarm = {}
        for rd in ("max", "ewma01", "cusum1"):
            tot = None
            for cal_half in (0, 1):
                cal = [(t, s, e) for t, s, e in rows if t["arm"] in ("clean", "benign_control") and half[t["pg"]] == cal_half and len(s)]
                ev = [(t, s, e) for t, s, e in rows if len(s) and (t["arm"] == "attack" or half[t["pg"]] != cal_half)]
                stats = fit_bucket_stats([(s, e) for _, s, e in cal])
                cal_max = [float(readings(standardize(s, e, stats))[rd].max()) for _, s, e in cal]
                h = conformal_h(cal_max, alpha)
                ev_s = [(t, readings(standardize(s, e, stats))[rd], e) for t, s, e in ev]
                m = alarm_metrics(ev_s, h, rd)
                if tot is None: tot = m
                else:
                    for k in m:
                        if isinstance(m[k], list) and k != "lat": tot[k] = [tot[k][0] + m[k][0], tot[k][1] + m[k][1]]
                        elif k == "lat": tot[k] = tot[k] + m[k]
                        else: tot[k] += m[k]
            # drift/resist counted twice (once per calibration half) -> average
            tot["drift"] //= 2
            for k in ("resist", "pre_strict", "pre_tol"): tot[k] = [tot[k][0] / 2, tot[k][1] / 2]
            for k in ("hit16", "hit32", "hitF", "hit16_tol", "hitF_tol"): tot[k] /= 2
            alarm[rd] = dict(FAR_clean=tot["clean"][0] / max(tot["clean"][1], 1), FAR_benign=tot["benign"][0] / max(tot["benign"][1], 1),
                             FAR_resist=tot["resist"][0] / max(tot["resist"][1], 1),
                             pre_strict=tot["pre_strict"][0] / max(tot["pre_strict"][1], 1), pre_tol=tot["pre_tol"][0] / max(tot["pre_tol"][1], 1),
                             R16=tot["hit16"] / tot["drift"], R32=tot["hit32"] / tot["drift"], RF=tot["hitF"] / tot["drift"],
                             R16_tol=tot["hit16_tol"] / tot["drift"], RF_tol=tot["hitF_tol"] / tot["drift"],
                             med_lat=float(np.median(tot["lat"])) if tot["lat"] else float("nan"), n_drift=tot["drift"])
        results[scorer] = dict(rank=rank, alarm=alarm)
    return results

if __name__ == "__main__":
    b1 = load("b1"); b2 = load("b2"); print("loaded", len(b1), len(b2), flush=True)

    allres = {}
    for src, dst, name in ((b1, b2, "B1->B2"), (b2, b1, "B2->B1")):
        res = evaluate_direction(src, dst); allres[name] = res
        print(f"\n===== {name} (fit routine on source; deployment-mode conformal alpha=0.10 on target routine halves) =====")
        print(f"{'scorer':22s} {'AUC post/rout':>13s} {'post/res':>8s} {'pre/rout':>8s} {'res/rout':>8s} {'ben/cln':>7s} | reading  FARc  FARb  FARr  preS  preT   R16  R16t   RF   RFt  lat")
        for sc, r in res.items():
            rk = r["rank"]
            first = True
            for rd, a in r["alarm"].items():
                head = f"{sc:22s} {rk['post_vs_routine']:13.3f} {rk['post_vs_resist']:8.3f} {rk['pre_vs_routine']:8.3f} {rk['resist_vs_routine']:8.3f} {rk['benign_vs_clean']:7.3f}" if first else " " * 71
                first = False
                print(f"{head} | {rd:7s} {a['FAR_clean']:5.2f} {a['FAR_benign']:5.2f} {a['FAR_resist']:5.2f} {a['pre_strict']:5.2f} {a['pre_tol']:5.2f} {a['R16']:5.2f} {a['R16_tol']:5.2f} {a['RF']:5.2f} {a['RF_tol']:5.2f} {a['med_lat']:4.0f}", flush=True)
    json.dump(allres, open(OUT / "oneclass_pilot_results.json", "w"), indent=1)
    print("saved")
