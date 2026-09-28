"""POST-HOC PILOT DIAGNOSTIC 2 (B2 only, development data, not a result).
Question: is the window routing signal explained by token identity?
 (a) same-token residual: sel_t - ref[token_id_t], ref from routine (clean+benign) decode tokens of training folds
 (b) layer bands: early 0-4 / middle 5-11 / late 12-15 (route_selection only)
 (c) embedding->route map with held-out-R2-selected lambda, then residual detector
Same grouped 5-fold protocol / transition-band anchors / trace-max metrics as pilot 1.
"""
import sys, json, time
from pathlib import Path
import torch
from safetensors import safe_open
sys.path.insert(0, "/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363/src")
from phase_a.sequential import evenly_spaced_indices
from phase_a.classifier import fit_ridge_classifier, binary_auroc
torch.set_num_threads(16)
RUN = Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/agent_v2_5_b2")
EMB = "/home/wzh/Agent-Moe-Research/artifacts/hf_cache/models--allenai--OLMoE-1B-7B-0125-Instruct/snapshots/b89a7c4bc24fb9e55ce2543c9458ce0ca5c4650e/model-00001-of-00003.safetensors"
W = 16; t0 = time.time()
cache = Path("b2_decode_cache.pt")
if cache.exists():
    traces = torch.load(cache)
else:
    traces = []
    for p in sorted(RUN.glob("*/*/trace.json")):
        d = p.parent; tr = json.loads(p.read_text())
        gens = [e for e in tr["events"] if e["kind"] == "model_generation"]; step = int(gens[-1]["agent_step"])
        ids, tk = [], []
        for line in (d / "manifest.jsonl").read_text().splitlines():
            r = json.loads(line)
            if r["phase"] != "decode": continue
            mask = torch.tensor([s == step for s in r["agent_steps"]])
            if not mask.any(): continue
            with safe_open(str(d / r["tensor_file"]), "pt") as f:
                tk.append(f.get_tensor("top_k_ids").long()[:, mask, :])
            ids += [i for i, m in zip(r["token_ids"], mask.tolist()) if m]
        tk = torch.cat(tk, 1)
        sel = torch.zeros(16, tk.shape[1], 64); sel.scatter_add_(2, tk, torch.ones_like(tk, dtype=torch.float32))
        v = tr["outcome"].get("goal_plan_deviation_start_output_token")
        traces.append(dict(id=tr["trace_id"], fold=str(tr["preregistered_fold"]), arm=tr["perturbation"]["arm"],
                           pos=bool(tr["outcome"]["primary_positive"]), b=None if v is None else int(v["output_token_index"]),
                           ids=torch.tensor(ids), sel=sel.permute(1, 0, 2).contiguous()))  # [T,16,64]
    torch.save(traces, cache)
print("loaded", len(traces), f"{time.time()-t0:.0f}s", flush=True)
with safe_open(EMB, "pt") as f: emb = f.get_tensor("model.embed_tokens.weight").float()

def wmean(x):  # x [T, D] -> [T-W+1, D]
    if x.shape[0] < W: return x.new_empty(0, x.shape[1])
    pre = torch.cat((x.new_zeros(1, x.shape[1]), x.cumsum(0)), 0); return (pre[W:] - pre[:-W]) / W
for r in traces:
    r["ends"] = torch.arange(W - 1, len(r["ids"])) if len(r["ids"]) >= W else torch.empty(0, dtype=torch.long)
    r["E"] = emb[r["ids"]]
def anchors(r):
    ends = r["ends"]
    if not ends.numel(): return []
    first, last = int(ends[0]), int(ends[-1]); out = []
    if r["pos"]:
        b = r["b"]; out += [(e, -1.0) for e in evenly_spaced_indices(first, min(last, b - 1), 2)]
        out += [(e, 1.0) for e in evenly_spaced_indices(max(first, b + W - 1), last, 4)]
    else: out += [(e, -1.0) for e in evenly_spaced_indices(first, last, 2)]
    return [(e - (W - 1), lab) for e, lab in out]  # window index
folds = sorted({r["fold"] for r in traces})
BANDS = {"early0-4": range(0, 5), "mid5-11": range(5, 12), "late12-15": range(12, 16)}
variants = ["route", "sametok_resid", "emb_resid", "emb"] + [f"route_{k}" for k in BANDS] + [f"sametok_{k}" for k in BANDS]
scores = {k: {} for k in variants}
r2_log = {}
for held in folds:
    train = [r for r in traces if r["fold"] != held]; test = [r for r in traces if r["fold"] == held]
    # (a) same-token reference from routine traces (clean + benign) in training folds
    ref_sum, ref_cnt = {}, {}
    for r in train:
        if r["arm"] == "attack": continue
        for tid, s in zip(r["ids"].tolist(), r["sel"]):
            ref_sum[tid] = ref_sum.get(tid, 0) + s; ref_cnt[tid] = ref_cnt.get(tid, 0) + 1
    gmean = sum(ref_sum.values()) / sum(ref_cnt.values())
    def sametok(r):
        out = torch.empty_like(r["sel"])
        for i, tid in enumerate(r["ids"].tolist()):
            out[i] = r["sel"][i] - (ref_sum[tid] / ref_cnt[tid] if ref_cnt.get(tid, 0) >= 3 else gmean)
        return out
    cover = [sum(ref_cnt.get(t, 0) >= 3 for t in r["ids"].tolist()) / len(r["ids"]) for r in test]
    # (c) emb->route map with lambda picked by held-out R2 on a routine split of training folds
    trn = [r for r in train if r["fold"] != folds[(folds.index(held) + 1) % 5]]
    val = [r for r in train if r["fold"] == folds[(folds.index(held) + 1) % 5]]
    def flat(rs, key): return torch.cat([r[key].reshape(len(r["ids"]), -1)[::2] for r in rs]).double()
    Etr, Rtr, Eva, Rva = flat(trn, "E"), flat(trn, "sel"), flat(val, "E"), flat(val, "sel")
    Em, Rm = Etr.mean(0), Rtr.mean(0); G = (Etr - Em).T @ (Etr - Em); C = (Etr - Em).T @ (Rtr - Rm)
    best = None
    for lam in (1e-2, 1e-1, 1.0, 10.0, 100.0):
        Wm = torch.linalg.solve(G + lam * torch.eye(2048, dtype=torch.float64), C)
        res = (Rva - Rm) - (Eva - Em) @ Wm; r2 = float(1 - res.pow(2).sum() / (Rva - Rva.mean(0)).pow(2).sum())
        if best is None or r2 > best[1]: best = (lam, r2, Wm)
    lam, r2, Wm = best; r2_log[held] = dict(lam=lam, heldout_R2=round(r2, 3), sametok_coverage=round(sum(cover) / len(cover), 3))
    def embres(r): return ((r["sel"].reshape(len(r["ids"]), -1).double() - Rm) - (r["E"].double() - Em) @ Wm).float()
    feats = {}
    for r in train + test:
        st = sametok(r)
        f = {"route": wmean(r["sel"].reshape(len(r["ids"]), -1)), "sametok_resid": wmean(st.reshape(len(r["ids"]), -1)),
             "emb_resid": wmean(embres(r)), "emb": wmean(r["E"])}
        for k, band in BANDS.items():
            f[f"route_{k}"] = wmean(r["sel"][:, list(band), :].reshape(len(r["ids"]), -1))
            f[f"sametok_{k}"] = wmean(st[:, list(band), :].reshape(len(r["ids"]), -1))
        feats[r["id"]] = f
    for k in variants:
        X, y = [], []
        for r in train:
            for i, lab in anchors(r): X.append(feats[r["id"]][k][i]); y.append(lab)
        m = fit_ridge_classifier(torch.stack(X), torch.tensor(y))
        for r in test:
            scores[k][r["id"]] = m.score(feats[r["id"]][k]).reshape(-1) if r["ends"].numel() else torch.empty(0)
    print("fold", held, r2_log[held], f"{time.time()-t0:.0f}s", flush=True)

def summarize(k):
    pm, nm, prem, rise15 = [], [], [], []
    for r in traces:
        s = scores[k][r["id"]]; ends = r["ends"]
        if not s.numel(): continue
        if r["pos"]:
            b = r["b"]; post, pre = s[ends >= b], s[ends < b]
            if post.numel(): pm.append(float(post.max()))
            if pre.numel(): prem.append(float(pre.max()))
            if (ends == b - 1).any() and (ends == b + W - 1).any(): rise15.append(float(s[ends == b + W - 1][0] - s[ends == b - 1][0]))
        else: nm.append(float(s.max()))
    pm, nm, prem = map(torch.tensor, (pm, nm, prem))
    au = lambda a, n: round(binary_auroc(torch.cat((a, n)), torch.cat((torch.ones(len(a)), torch.zeros(len(n)))).bool()), 3)
    rec = {}
    for name, thr in (("far0", float(nm.max())), ("far5", float(torch.quantile(nm, 0.95)))):
        h16 = hF = pa = 0
        for r in traces:
            s = scores[k][r["id"]]; ends = r["ends"]
            if not r["pos"] or not s.numel(): continue
            b = r["b"]
            if (s[ends < b] >= thr).any(): pa += 1; continue
            post = ends[(ends >= b) & (s >= thr)]
            if post.numel(): hF += 1; h16 += int(int(post[0]) <= b + 16)
        rec[name] = f"pre{pa}/h16 {h16}/final {hF} of 35"
    return dict(trace_max_auroc=au(pm, nm), pre_boundary_vs_nondrift_auroc=au(prem, nm),
                rise_b15_median=round(float(torch.tensor(rise15).median()), 3), rise_pos=f"{int((torch.tensor(rise15)>0).sum())}/{len(rise15)}", **rec)
out = {"r2": r2_log, **{k: summarize(k) for k in variants}}
print(json.dumps(out, indent=1)); Path("pilot2.json").write_text(json.dumps(out, indent=1)); print("done", f"{time.time()-t0:.0f}s")
