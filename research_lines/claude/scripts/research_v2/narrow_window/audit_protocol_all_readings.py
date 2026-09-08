import json, statistics
from pathlib import Path
ROOT=Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
ART=ROOT/"artifacts/agent_v2/research_v2"; LAB=ROOT/"docs/research_v2/labels"
def jl(p): return {json.loads(l)["trace_id"]:json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()}
RES=jl(LAB/"topic_entry_v1_adjudicated.jsonl"); PROD=jl(LAB/"product_onset_v1_adjudicated.jsonl")
def cls(t):
    a=t.split("--")[-1]
    if a=="clean": return "clean"
    if a!="attack": return "benign"
    return "drift" if t in PROD else "resist"
RUNS={"CAND-A":(ART/"narrow_window/wgm_c2_w1248/result.json",[1,2,4,8]),
      "CAND-B":(ART/"narrow_window/pdm_c12_w124/result.json",[1,2,4])}
print(f"{'cand':7}{'w':>3} {'reading':10}{'FARpool':>9}{'FAclean':>8}{'FAben':>7}{'R16':>5}{'Rfin':>5}{'pre':>4}{'driftR16':>9}{'E0':>4}{'FA@firstend':>12}{'FA@end<8':>9}")
for cand,(p,ws) in RUNS.items():
    res=json.loads(p.read_text())
    for w in ws:
        agg={}
        for cr in res["case_runs"]:
            if cr["window_width"]!=w: continue
            firstend={t:(b["ends"][0] if b["ends"] else None) for t,b in cr["score_streams"].items()}
            ends={t:b["ends"] for t,b in cr["score_streams"].items()}
            for c in cr["candidates"]:
                if c["mode"]!="D" or abs(c["alpha"]-0.1)>1e-12: continue
                k=c["reading"]; d=agg.setdefault(k,dict(n=0,fa=0,fc=0,nc=0,fb=0,nb=0,r16=0,rf=0,pre=0,dr16=0,e0=0,ffe=0,f8=0))
                for r in c["trace_alarms"]:
                    t,f=r[0],r[2]; kk=cls(t)
                    if kk in("clean","benign"):
                        d["n"]+=1; d["fa"]+=f is not None
                        if kk=="clean": d["nc"]+=1; d["fc"]+= f is not None
                        else: d["nb"]+=1; d["fb"]+= f is not None
                        if f is not None:
                            d["ffe"]+= (f==firstend[t]); d["f8"]+= (f<8)
                    elif kk=="resist":
                        a=RES[t]["topic_entry_onset"]
                        if a is None: d["e0"]+= f is not None
                        else:
                            if f is None: pass
                            elif f<a: d["pre"]+=1
                            else:
                                d["rf"]+=1; d["r16"]+= (f-a<=16)
                    else:
                        a=PROD[t]["product_onset"]
                        if f is not None and f>=a and f-a<=16: d["dr16"]+=1
        for k in sorted(agg):
            d=agg[k]
            print(f"{cand:7}{w:>3} {k:10}{d['fa']/d['n']:>9.4f}{d['fc']/d['nc']:>8.4f}{d['fb']/d['nb']:>7.4f}{d['r16']:>5}{d['rf']:>5}{d['pre']:>4}{d['dr16']:>9}{d['e0']:>4}{d['ffe']:>12}{d['f8']:>9}")
