"""POST-HOC PILOT DIAGNOSTIC (B2 only, development data, not a result).
Same causal 16-token windows, same grouped 5-fold (preregistered folds) ridge protocol for:
  route_selection (1024-d), static input-embedding mean (2048-d), token hash (2048-d),
  and route residualized on embedding (route - E @ W, W fitted on training-fold windows).
Metrics: trace-max AUROC (drift post-boundary max vs non-drift full max), rise at +15,
pre-vs-post ordering within drift traces.
"""
import sys, json, time
from pathlib import Path
import torch
from safetensors import safe_open
sys.path.insert(0, "/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363/src")
sys.path.insert(0, "/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363/scripts")

from safetensors import safe_open as _so
from phase_a.routing_analysis import RoutingSequence
class _Obs:
    def __init__(self, d):
        self.trace_dir = d
        tr = json.loads((d / "trace.json").read_text())
        self.trace = tr
        self.fold = str(tr["preregistered_fold"]); self.arm = tr["perturbation"]["arm"]
        self.positive = bool(tr["outcome"]["primary_positive"])
        v = tr["outcome"].get("goal_plan_deviation_start_output_token")
        self.boundary = None if v is None else int(v["output_token_index"])
        self.trace_id = tr["trace_id"]
        gens = [e for e in tr["events"] if e["kind"] == "model_generation"]
        step = int(gens[-1]["agent_step"])
        ids, tk = [], []
        for line in (d / "manifest.jsonl").read_text().splitlines():
            r = json.loads(line)
            if r["phase"] != "decode": continue
            mask = torch.tensor([s == step for s in r["agent_steps"]])
            if not mask.any(): continue
            with _so(str(d / r["tensor_file"]), "pt") as f:
                t = f.get_tensor("top_k_ids").long()[:, mask, :]
            tk.append(t); ids += [i for i, m in zip(r["token_ids"], mask.tolist()) if m]
        tk = torch.cat(tk, 1)
        assert tuple(ids) == tuple(gens[-1]["output_token_ids"])
        L, T, K = tk.shape
        self.sequence = RoutingSequence(tuple(ids), tuple(str(i) for i in ids), torch.zeros(L, T, 64), tk)
def _load_observations(run):
    return [_Obs(p.parent) for p in sorted(run.glob("*/*/trace.json"))], 240

from phase_a.sequential import window_features, evenly_spaced_indices
from phase_a.classifier import fit_ridge_classifier, binary_auroc

torch.set_num_threads(16)
RUN = Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/agent_v2_5_b2")
EMB = "/home/wzh/Agent-Moe-Research/artifacts/hf_cache/models--allenai--OLMoE-1B-7B-0125-Instruct/snapshots/b89a7c4bc24fb9e55ce2543c9458ce0ca5c4650e/model-00001-of-00003.safetensors"
W = 16
t0 = time.time()
with safe_open(EMB, "pt") as f:
    emb = f.get_tensor("model.embed_tokens.weight").float()
print("embedding", tuple(emb.shape), f"{time.time()-t0:.0f}s", flush=True)
obs, valid = _load_observations(RUN)
print("loaded", len(obs), f"{time.time()-t0:.0f}s", flush=True)

def emb_windows(seq):
    ids = torch.tensor(seq.token_ids)
    e = emb[ids]  # [T, 2048]
    if len(ids) < W:
        return torch.empty(0, e.shape[1])
    prefix = torch.cat((torch.zeros(1, e.shape[1]), e.cumsum(0)), 0)
    return (prefix[W:] - prefix[:-W]) / W

rows = []
for o in obs:
    ends, route = window_features(o.sequence, "route_selection", W)
    _, th = window_features(o.sequence, "token_hash", W)
    em = emb_windows(o.sequence)
    rows.append(dict(o=o, ends=ends, feats={"route": route, "emb": em, "hash": th}))
print("features", f"{time.time()-t0:.0f}s", flush=True)

def anchors(row):
    """(ends, label) with transition band: positives only fully post-boundary (end>=b+W-1)."""
    o, ends = row["o"], row["ends"]
    if not ends.numel():
        return []
    first, last = int(ends[0]), int(ends[-1])
    out = []
    if o.positive:
        b = o.boundary
        for e in evenly_spaced_indices(first, min(last, b - 1), 2):
            out.append((e, -1.0))
        for e in evenly_spaced_indices(max(first, b + W - 1), last, 4):
            out.append((e, 1.0))
    else:
        for e in evenly_spaced_indices(first, last, 2):
            out.append((e, -1.0))
    return out

def idx(row, e):
    return int((row["ends"] == e).nonzero().reshape(-1)[0])

folds = sorted({r["o"].fold for r in rows})
scores = {k: {} for k in ("route", "emb", "hash", "route_resid", "route_plus_emb")}
for held in folds:
    train = [r for r in rows if r["o"].fold != held]
    test = [r for r in rows if r["o"].fold == held]
    X = {k: [] for k in ("route", "emb", "hash")}; y = []
    for r in train:
        for e, lab in anchors(r):
            i = idx(r, e)
            for k in X: X[k].append(r["feats"][k][i])
            y.append(lab)
    y = torch.tensor(y)
    X = {k: torch.stack(v) for k, v in X.items()}
    # residualization map W: route ~ emb, fitted on subsampled training windows (every 4th window)
    Etr = torch.cat([r["feats"]["emb"][::4] for r in train]).double()
    Rtr = torch.cat([r["feats"]["route"][::4] for r in train]).double()
    Em, Rm = Etr.mean(0), Rtr.mean(0)
    Ec, Rc = Etr - Em, Rtr - Rm
    lam = 1.0 * Ec.shape[0]
    Wmap = torch.linalg.solve(Ec.T @ Ec + lam * torch.eye(Ec.shape[1], dtype=torch.float64), Ec.T @ Rc)
    def resid(E, R):
        return (R.double() - Rm) - (E.double() - Em) @ Wmap
    models = {
        "route": fit_ridge_classifier(X["route"], y),
        "emb": fit_ridge_classifier(X["emb"], y),
        "hash": fit_ridge_classifier(X["hash"], y),
        "route_resid": fit_ridge_classifier(resid(X["emb"], X["route"]).float(), y),
        "route_plus_emb": fit_ridge_classifier(torch.cat((X["route"], X["emb"]), 1), y),
    }
    r2 = 1 - resid(Etr.float(), Rtr.float()).pow(2).sum() / Rc.pow(2).sum()
    print(f"fold {held}: anchors={len(y)} pos={(y>0).sum().item()} train-R2(route|emb)={r2:.3f}", flush=True)
    for r in test:
        F = r["feats"]
        if not r["ends"].numel():
            for k in scores: scores[k][r["o"].trace_id] = torch.empty(0)
            continue
        scores["route"][r["o"].trace_id] = models["route"].score(F["route"]).reshape(-1)
        scores["emb"][r["o"].trace_id] = models["emb"].score(F["emb"]).reshape(-1)
        scores["hash"][r["o"].trace_id] = models["hash"].score(F["hash"]).reshape(-1)
        scores["route_resid"][r["o"].trace_id] = models["route_resid"].score(resid(F["emb"], F["route"]).float()).reshape(-1)
        scores["route_plus_emb"][r["o"].trace_id] = models["route_plus_emb"].score(torch.cat((F["route"], F["emb"]), 1)).reshape(-1)

def summarize(k):
    pos_max, neg_max, rises, order, rise15, pre_max_list = [], [], [], [], [], []
    for r in rows:
        o, ends = r["o"], r["ends"]
        s = scores[k][o.trace_id]
        if not s.numel(): continue
        if o.positive:
            b = o.boundary
            post = s[ends >= b]; pre = s[ends < b]
            if post.numel(): pos_max.append(float(post.max()))
            if pre.numel() and post.numel():
                rises.append(float(post.max() - pre.max())); order.append(float(post.max() > pre.max()))
            if (ends == b - 1).any() and (ends == b + W - 1).any():
                rise15.append(float(s[ends == b + W - 1][0] - s[ends == b - 1][0]))
        else:
            neg_max.append(float(s.max()))
    pm, nm = torch.tensor(pos_max), torch.tensor(neg_max)
    auroc = binary_auroc(torch.cat((pm, nm)), torch.cat((torch.ones(len(pm)), torch.zeros(len(nm)))).bool())
    # also: drift pre-boundary max vs non-drift max (does the score fire before the behaviour?)
    pre_list = []
    for r in rows:
        o, ends = r["o"], r["ends"]; s = scores[k][o.trace_id]
        if o.positive and s.numel() and (ends < o.boundary).any(): pre_list.append(float(s[ends < o.boundary].max()))
    pre_auroc = binary_auroc(torch.cat((torch.tensor(pre_list), nm)), torch.cat((torch.ones(len(pre_list)), torch.zeros(len(nm)))).bool())
    # recall at FAR-matched threshold: threshold = max of non-drift (0 FAR) and q0.95 of non-drift maxima
    thr0 = float(nm.max()); thr95 = float(torch.quantile(nm, 0.95))
    rec = {}
    for name, thr in (("far0", thr0), ("far5", thr95)):
        hit16 = hitF = pre_alarm = 0
        for r in rows:
            o, ends = r["o"], r["ends"]; s = scores[k][o.trace_id]
            if not o.positive or not s.numel(): continue
            b = o.boundary
            if (s[ends < b] >= thr).any(): pre_alarm += 1; continue
            post = ends[(ends >= b) & (s >= thr)]
            if post.numel():
                hitF += 1
                if int(post[0]) <= b + 16: hit16 += 1
        rec[name] = dict(thr=round(thr, 3), pre_alarm=pre_alarm, hit16=hit16, hitF=hitF, n=35)
    return dict(trace_max_auroc=round(auroc, 3), pre_boundary_max_vs_nondrift_auroc=round(pre_auroc, 3),
                n_pos=len(pm), n_neg=len(nm), post_gt_pre=f"{int(sum(order))}/{len(order)}",
                median_rise=round(float(torch.tensor(rises).median()), 3),
                rise_b15_minus_bm1_median=round(float(torch.tensor(rise15).median()), 3),
                rise_b15_positive=f"{int((torch.tensor(rise15)>0).sum())}/{len(rise15)}", recall=rec)

out = {k: summarize(k) for k in scores}
# paired: per drift trace, which of route/emb has larger rise
print(json.dumps(out, indent=1))
Path(__file__).with_suffix(".json").write_text(json.dumps(out, indent=1))
print("done", f"{time.time()-t0:.0f}s")
