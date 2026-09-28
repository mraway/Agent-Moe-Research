import json, sys, statistics
from pathlib import Path
import torch
ROOT=Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
sys.path[:0]=[str(ROOT/"src"),str(ROOT/"scripts"),str(ROOT/"scripts/research_v2/zoom/missed_drift")]
from common import cases_for, load_result, streams_from_result
from research_v2 import io as rio
from research_v2.harness import fit_bucket_stats, routine_traces, scenario_halves
batches=rio.load_core(); cases=cases_for(batches)
RUNS={"CAND-A":(ROOT/"artifacts/agent_v2/research_v2/narrow_window/wgm_c2_w1248/result.json",[1,2,4,8]),
      "CAND-B":(ROOT/"artifacts/agent_v2/research_v2/narrow_window/pdm_c12_w124/result.json",[1,2,4])}
print("bucket-0 statistics of the calibration pool, and the effect of the extra early ends that only exist at narrow w")
for cand,(p,ws) in RUNS.items():
    res=load_result(p)
    for cr in res["case_runs"]:
        w=cr["window_width"]; case=cases[cr["case"]]
        streams=streams_from_result(cr); halves=scenario_halves(case.target_traces)
        tr=[t for t in routine_traces(case.target_traces,"cb") if streams[t.trace_id][1].numel()]
        for h in (0,1):
            cal=[streams[t.trace_id] for t in tr if halves[t.pair_group_id]==h]
            st=fit_bucket_stats(cal,bucket_size=32,min_bucket_traces=30,bucket_cap=None,min_criterion="traces")
            vals=torch.cat([s[(e//32)==0] for s,e in cal]); ends=torch.cat([e[(e//32)==0] for s,e in cal])
            late=vals[ends>=7]; early=vals[ends<7]
            print(f"{cand} {cr['case']:9} w={w} h={h} bucket0: n={vals.numel()} mu={float(vals.mean()):.4f} sd={float(vals.std()):.4f} "
                  f"| ends>=7 n={late.numel()} mu={float(late.mean()):.4f} sd={float(late.std()):.4f} "
                  f"| ends<7 n={early.numel()} " + (f"mu={float(early.mean()):.4f} sd={float(early.std()):.4f}" if early.numel()>1 else "-") +
                  f" | mu_all-mu_late={float(vals.mean()-late.mean()):+.4f} sd_ratio={float(vals.std()/late.std()):.4f}")
        del streams
