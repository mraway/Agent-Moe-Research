"""Post-hoc pilot diagnostic (NOT a result): token-identity vs context share of routing per layer on B2."""
import sys, json, time
from pathlib import Path
import torch
t0=time.time()
sys.path.insert(0,"/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363/src")
sys.path.insert(0,"/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363/scripts")
from score_agent_v2_b2_frozen import _load_observations
from phase_a.classifier import binary_auroc
run=Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/agent_v2_5_b2")
obs,_=_load_observations(run)
print("loaded",len(obs),time.time()-t0)
OUT=Path(__file__).parent
# cache compact tensors for reuse
def sel(o):
    L,T,E=o.sequence.probabilities.shape
    s=torch.zeros(L,T,E); s.scatter_add_(2,o.sequence.top_k_ids,torch.ones_like(o.sequence.top_k_ids,dtype=torch.float32)); return s
data=[]
for o in obs:
    data.append(dict(tid=o.trace_id,fold=o.fold,arm=o.arm,dom=o.domain,wf=o.workflow,pos=o.positive,b=o.boundary,
        ids=torch.tensor(o.sequence.token_ids),sel=sel(o),prob=o.sequence.probabilities.float()))
torch.save(data,OUT/"b2_compact.pt")
print("saved",time.time()-t0)
