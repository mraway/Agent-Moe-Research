"""Step 0: cache decode tensors per trace and stream prefill per-token-id accumulators.

Post-hoc pilot diagnostic support code (B1/B2 are development data)."""
import json, sys, time
from pathlib import Path
import torch
torch.set_num_threads(1)
from safetensors.torch import load_file

S = Path(__file__).resolve().parent
CACHE = S / "cache"
CACHE.mkdir(exist_ok=True)
sys.path.insert(0, "/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363/src")
sys.path.insert(0, "/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363/scripts")
from analyze_agent_v2_b1_routing import _load_observations as load_b1
from score_agent_v2_b2_frozen import _load_observations as load_b2

RUNS = {
    "b1": Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/agent_v2_5_b1"),
    "b2": Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/agent_v2_5_b2"),
}
V = 50304  # OLMoE vocab padded size; checked against max token id below


def cache_decode(batch, loader):
    out = CACHE / f"{batch}_decode.pt"
    if out.exists():
        print(batch, "decode cache exists"); return
    t0 = time.time()
    obs, valid = loader(RUNS[batch])
    print(batch, "loaded", len(obs), "valid", valid, f"{time.time()-t0:.0f}s", flush=True)
    rows = []
    for o in obs:
        seq = o.sequence
        L, T, K = seq.top_k_ids.shape
        ind = torch.zeros(L, T, 64, dtype=torch.float32)
        ind.scatter_(2, seq.top_k_ids.long(), 1.0)
        rows.append({
            "trace_id": o.trace_id, "pair_group_id": o.pair_group_id, "fold": o.fold,
            "arm": o.arm, "brief": o.brief, "workflow": o.workflow, "channel": o.channel,
            "domain": o.domain, "positive": o.positive, "boundary": o.boundary,
            "trace_dir": str(o.trace_dir),
            "token_ids": torch.tensor(seq.token_ids, dtype=torch.long),
            "token_texts": list(seq.token_texts),
            "p": seq.probabilities.float().clone(),   # [16,T,64]
            "ind": ind,                                # [16,T,64] top-8 indicator
        })
    torch.save(rows, out)
    print(batch, "saved decode cache", f"{time.time()-t0:.0f}s", flush=True)


def cache_prefill(batch):
    out = CACHE / f"{batch}_prefill_tables.pt"
    if out.exists():
        print(batch, "prefill cache exists"); return
    t0 = time.time()
    sum_p = torch.zeros(V, 16, 64, dtype=torch.float32)
    sum_ind = torch.zeros(V, 16, 64, dtype=torch.float32)
    count = torch.zeros(V, dtype=torch.float64)
    n_traces = 0; n_tokens = 0
    for trace_path in sorted(RUNS[batch].glob("*/*/trace.json")):
        tdir = trace_path.parent
        rows = [json.loads(l) for l in (tdir / "manifest.jsonl").read_text().splitlines() if l]
        pre = [r for r in rows if r["phase"] == "prefill"]
        assert len(pre) == 1, (tdir, len(pre))
        r = pre[0]
        ten = load_file(tdir / r["tensor_file"])
        p = torch.softmax(ten["router_logits"].float(), dim=-1)  # [16,T,64]
        tk = ten["top_k_ids"].long()
        ind = torch.zeros_like(p); ind.scatter_(2, tk, 1.0)
        ids = torch.tensor(r["token_ids"], dtype=torch.long)
        assert ids.max() < V
        sum_p.index_add_(0, ids, p.permute(1, 0, 2).contiguous())
        sum_ind.index_add_(0, ids, ind.permute(1, 0, 2).contiguous())
        count.index_add_(0, ids, torch.ones(len(ids), dtype=torch.float64))
        n_traces += 1; n_tokens += len(ids)
    torch.save({"sum_p": sum_p, "sum_ind": sum_ind, "count": count,
                "n_traces": n_traces, "n_tokens": n_tokens}, out)
    print(batch, "prefill traces", n_traces, "tokens", n_tokens, f"{time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    batch = sys.argv[1]
    cache_decode(batch, load_b1 if batch == "b1" else load_b2)
    cache_prefill(batch)
    print("done", batch)
