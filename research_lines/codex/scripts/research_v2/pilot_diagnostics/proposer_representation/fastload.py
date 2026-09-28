"""Fast direct loader for final-generation decode routing (post-hoc pilot diagnostic only)."""
import sys, json, time
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import torch
from safetensors.torch import load_file

def load_one(args):
    run, rel = args
    d = Path(run)/rel
    trace = json.loads((d/"trace.json").read_text())
    gens=[e for e in trace["events"] if e["kind"]=="model_generation"]
    step=int(gens[-1]["agent_step"])
    rows=[json.loads(l) for l in (d/"manifest.jsonl").read_text().splitlines() if l.strip()]
    ids=[];logits=[];topk=[];topw=[]
    for r in rows:
        if r["phase"]!="decode": continue
        mask=[s==step for s in r["agent_steps"]]
        if not any(mask): continue
        m=torch.tensor(mask)
        t=load_file(str(d/r["tensor_file"]))
        logits.append(t["router_logits"].float()[:,m,:]); topk.append(t["top_k_ids"].long()[:,m,:]); topw.append(t["top_k_weights"].float()[:,m,:])
        ids.extend(i for i,k in zip(r["token_ids"],mask) if k)
    logits=torch.cat(logits,1); topk=torch.cat(topk,1); topw=torch.cat(topw,1)
    assert tuple(gens[-1]["output_token_ids"])==tuple(ids)
    out=trace["outcome"]; pert=trace["perturbation"]
    b=out.get("goal_plan_deviation_start_output_token")
    return dict(tid=trace["trace_id"], fold=str(trace["preregistered_fold"]), arm=pert["arm"], channel=pert["channel"],
        dom=str((pert.get("attack_goal") or {}).get("target_domain","none")), wf=trace["task_mandate"]["authorized_goal"],
        brief=trace["response_brief_condition"], pos=bool(out["primary_positive"]), b=None if b is None else int(b["output_token_index"]),
        ids=torch.tensor(ids), logits=logits.to(torch.bfloat16), topk=topk.to(torch.int16), topw=topw.to(torch.bfloat16))

if __name__=="__main__":
    run=sys.argv[1]; outp=sys.argv[2]
    t0=time.time()
    rels=[json.loads(l)["relative_path"] for l in open(Path(run)/"sample_index.jsonl") if l.strip()]
    with ProcessPoolExecutor(16) as ex:
        data=list(ex.map(load_one,[(run,r) for r in rels]))
    torch.save(data,outp); print("loaded",len(data),"in",round(time.time()-t0,1),"s")
