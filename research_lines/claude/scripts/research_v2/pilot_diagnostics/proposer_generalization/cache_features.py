"""Post-hoc pilot diagnostic only: cache 16-token route_selection windows for B1+B2."""
import sys, time, torch
from pathlib import Path
sys.path.insert(0, "/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363/src")
sys.path.insert(0, "/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363/scripts")
from analyze_agent_v2_b1_routing import _load_observations as load_b1
from score_agent_v2_b2_frozen import _load_observations as load_b2
from phase_a.sequential import window_features
OUT = Path(sys.argv[1])
rows = []
for name, loader, d in (("b1", load_b1, "agent_v2_5_b1"), ("b2", load_b2, "agent_v2_5_b2")):
    t0 = time.time()
    obs, valid = loader(Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2") / d)
    print(name, "loaded", len(obs), "in", round(time.time() - t0), "s", flush=True)
    for o in obs:
        ends, feats = window_features(o.sequence, "route_selection", 16)
        rows.append(dict(batch=name, trace_id=o.trace_id, group=o.pair_group_id, fold=o.fold, arm=o.arm,
                         brief=o.brief, workflow=o.workflow, channel=o.channel, domain=o.domain,
                         positive=bool(o.positive), boundary=o.boundary, n=len(o.sequence.token_ids),
                         ends=ends.clone(), feats=feats.half().clone()))
torch.save(rows, OUT)
print("saved", len(rows), "traces")
