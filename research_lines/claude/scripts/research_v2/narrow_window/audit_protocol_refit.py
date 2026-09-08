"""Refit-vs-stored fragility diagnostic + w=1 reading semantics check + bucket-0 dilution."""
import json, sys
from pathlib import Path
import torch
ROOT=Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
sys.path[:0]=[str(ROOT/"src"),str(ROOT/"scripts"),str(ROOT/"scripts/research_v2/zoom/missed_drift")]
from common import cases_for, load_result, streams_from_result
from research_v2 import io as rio
from research_v2.harness import COMPARISONS, fit_bucket_stats, routine_traces, scenario_halves, conformal_threshold
from research_v2.readings import build_readings
torch.set_num_threads(8)
LAB=ROOT/"docs/research_v2/labels"
def jl(p): return {json.loads(l)["trace_id"]:json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()}
RES=jl(LAB/"topic_entry_v1_adjudicated.jsonl"); PROD=jl(LAB/"product_onset_v1_adjudicated.jsonl")
batches=rio.load_core(); cases=cases_for(batches)
readings={r.name:r for r in build_readings()}
RUNS={"CAND-A":ROOT/"artifacts/agent_v2/research_v2/narrow_window/wgm_c2_w1248/result.json",
      "CAND-B":ROOT/"artifacts/agent_v2/research_v2/narrow_window/pdm_c12_w124/result.json"}
for cand,p in RUNS.items():
    res=load_result(p)
    for cr in res["case_runs"]:
        w=cr["window_width"]; case=cases[cr["case"]]
        streams=streams_from_result(cr)
        halves=scenario_halves(case.target_traces)
        tr=[t for t in routine_traces(case.target_traces,"cb") if streams[t.trace_id][1].numel()]
        for h in (0,1):
            cal=[t for t in tr if halves[t.pair_group_id]==h]
            cs=[streams[t.trace_id] for t in cal]
            st=fit_bucket_stats(cs,bucket_size=32,min_bucket_traces=30,bucket_cap=None,min_criterion="traces")
            zc=[st.standardize(s,e) for s,e in cs]
            stored=cr["calibration"]["D"]["halves"][str(h)]["thresholds"]
            for rn in ("max","persist2"):
                R=readings[rn]
                mx=[float(R.apply(z).max()) for z in zc]
                for a in (0.05,0.1):
                    thr_refit=float(conformal_threshold(mx,a)["threshold"])
                    thr_st=float(stored[f"{rn}|alpha{a}"]["threshold"])
                    if abs(thr_refit-thr_st)>1e-9:
                        print(f"THRDIFF {cand} {cr['case']} w={w} h={h} {rn} a={a}: stored={thr_st!r} refit={thr_refit!r} diff={thr_st-thr_refit:.3e}")
            # per trace decisions
            for rn in ("max","persist2"):
                R=readings[rn]
                for a in (0.1,):
                    thr_st=float(stored[f"{rn}|alpha{a}"]["threshold"])
                    cnd=next(x for x in cr["candidates"] if x["mode"]=="D" and x["reading"]==rn and abs(x["alpha"]-a)<1e-12)
                    st_first={r[0]:r[2] for r in cnd["trace_alarms"]}
                    for t in case.target_traces:
                        if halves[t.pair_group_id]==h: continue
                        s,e=streams[t.trace_id]
                        if not e.numel(): continue
                        stat=R.apply(st.standardize(s,e))
                        al=e[COMPARISONS["ge"](stat,thr_st)]
                        f=int(al[0]) if al.numel() else None
                        if f!=st_first[t.trace_id]:
                            kind = "ANCHORED_RESIST" if (t.trace_id in RES and RES[t.trace_id]["topic_entry_onset"] is not None) else ("DRIFT" if t.trace_id in PROD else ("E0" if t.trace_id in RES else t.trace_id.split("--")[-1]))
                            marg=float((stat-thr_st).abs().min())
                            print(f"ALARMDIFF {cand} {cr['case']} w={w} h={h} {rn} a={a} {t.trace_id} [{kind}] stored={st_first[t.trace_id]} refit={f} min|stat-thr|={marg:.3e}")
        del streams
print("done")
