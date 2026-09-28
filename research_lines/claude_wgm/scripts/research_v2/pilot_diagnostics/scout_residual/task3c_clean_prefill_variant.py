"""Variant of the prefill-augmented pool that uses ONLY clean + benign_control prefill (no attack-arm prefill,
which contains attack text). Reports coverage and retention for this pool. Post-hoc pilot diagnostic; not a result."""
import json, numpy as np, torch
from pathlib import Path
from safetensors.torch import load_file
from common import *
torch.set_num_threads(8)
RUNS = {"b1": Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/agent_v2_5_b1"),
        "b2": Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/agent_v2_5_b2")}


def clean_prefill(batch):
    out = CACHE / f"{batch}_prefill_tables_cleanbenign.pt"
    if out.exists():
        return torch.load(out, weights_only=False)
    sum_p = torch.zeros(V, 16, 64); sum_ind = torch.zeros(V, 16, 64); count = torch.zeros(V, dtype=torch.float64)
    n_traces = n_tokens = 0
    for trace_path in sorted(RUNS[batch].glob("*/*/trace.json")):
        if trace_path.parent.name not in ("clean", "benign_control"):
            continue
        tdir = trace_path.parent
        r = [json.loads(l) for l in (tdir / "manifest.jsonl").read_text().splitlines() if l and '"prefill"' in l]
        assert len(r) == 1; r = r[0]
        ten = load_file(tdir / r["tensor_file"])
        p = torch.softmax(ten["router_logits"].float(), dim=-1); ind = torch.zeros_like(p); ind.scatter_(2, ten["top_k_ids"].long(), 1.0)
        ids = torch.tensor(r["token_ids"], dtype=torch.long)
        sum_p.index_add_(0, ids, p.permute(1, 0, 2).contiguous()); sum_ind.index_add_(0, ids, ind.permute(1, 0, 2).contiguous())
        count.index_add_(0, ids, torch.ones(len(ids), dtype=torch.float64)); n_traces += 1; n_tokens += len(ids)
    d = {"sum_p": sum_p, "sum_ind": sum_ind, "count": count, "n_traces": n_traces, "n_tokens": n_tokens}
    torch.save(d, out); return d


def window_labels(d, rowsb):
    lab = np.full(len(d["pos"]), -1); pos = d["pos"].numpy(); tr = d["trace"].numpy(); grp = d["grp"].numpy()
    for i, r in enumerate(rowsb):
        m = tr == i
        if r["positive"]:
            lab[m & (pos - (WIN - 1) >= r["boundary"])] = 1
        elif grp[m][0] == 0:
            lab[m & (pos >= WIN - 1)] = 0
    return lab


data = {b: stack_batch(load_batch(b)) for b in ("b1", "b2")}
rows = {b: load_batch(b) for b in ("b1", "b2")}
pre = {b: clean_prefill(b) for b in ("b1", "b2")}
out = {"note": "post-hoc pilot diagnostic on development data; not a result",
       "pool": "R(X) decode + prefill of clean and benign_control traces of X only (160 traces per batch)",
       "coverage": {}, "rows": []}
for x, y in (("b1", "b2"), ("b2", "b1")):
    tab0 = routine_tables(data[x], "p"); mu = tab0["mu"]
    tab = augment_table(tab0, pre[x], "p")
    ids_y = data[y]["ids"][data[y]["grp"] == 1]; c = tab["count"][ids_y]
    out["coverage"][f"DRIFT_POST_{y}_vs_pool_{x}_R_plus_cleanbenign_prefill"] = {
        "prefill_tokens": int(pre[x]["n_tokens"]), "prefill_traces": int(pre[x]["n_traces"]),
        **{f"frac_count_ge_{k}": float((c >= k).float().mean()) for k in (1, 5, 20)}}
    def feats(d):
        E = shrunk_expectation(tab, d["ids"], mu=mu); return {"raw_p": d["p"], "residual": d["p"] - E, "E_token_only": E}
    fx, fy = feats(data[x]), feats(data[y]); wl = window_labels(data[y], rows[y]); g = data[y]["grp"].numpy()
    for fname in ("raw_p", "residual", "E_token_only"):
        w = diag_lda(fx[fname][data[x]["grp"] == 1], fx[fname][data[x]["grp"] == 0])
        for band in BANDS:
            s = band_scores(fy[fname], w, band); wm = causal_window_means(s, data[y]["trace"])
            out["rows"].append({"fit": x, "eval": y, "pool": "R_plus_cleanbenign_prefill", "feature": fname, "band": band,
                                "token_auroc": auroc(s[g == 1], s[g == 0]), "window16_auroc": auroc(wm[wl == 1], wm[wl == 0])})
dump("task3c_clean_prefill_variant.json", out)
print(out["coverage"])
for r in out["rows"]:
    print(f"{r['fit']}->{r['eval']} {r['feature']:13s} {r['band']:7s} tokAUC={r['token_auroc']:.3f} winAUC={r['window16_auroc']:.3f}")
