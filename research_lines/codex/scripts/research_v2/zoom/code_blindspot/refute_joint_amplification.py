"""Is the 'joint amplification' A anything more than 'the marginal is weak'?

(i) regress log(joint ratio) on log(marginal ratio) over the 51 NON-programming drifts
    only, then predict the 8 code traces and look at the residuals;
(ii) exact multiplicative decomposition of the headline 'novel combination of familiar
    experts' gap into a marginal factor (how often both endpoints are familiar) and a
    joint factor (unseen rate per familiar-familiar opportunity).
"""
from __future__ import annotations
import json, math, statistics as st
from pathlib import Path

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
R = json.loads((ROOT/"artifacts/agent_v2/research_v2/zoom_code_blindspot/refute_joint_recompute.json").read_text())
PROG = ["b1-f2-012-order_status-python-function--attack","b1-f2-014-knowledge_qa-python-function--attack",
        "b1-f3-016-return_and_knowledge-javascript-utility--attack","b2-f2-011-knowledge_qa-sql-query--attack",
        "b2-f2-012-order_and_knowledge-sql-query--attack","b2-f2-014-support_case_status-sql-query--attack",
        "b2-f2-015-warranty_status-sql-query--attack","b2-f3-020-order_status-rust-function--attack"]

def load(split="pairgroup", anchor="product_onset"):
    b=R["splits"][split]; folds=list(b)
    routine={k: st.mean([st.mean([v[k] for v in b[f]["routine"].values() if v.get(k) is not None]) for f in folds])
             for k in ("pair_unseen_rate","novel_comb_rate","novel_comb_cond","ff_frac")}
    routine["wgm_g1_median"]=st.mean([st.median([v["wgm_g1_median"] for v in b[f]["routine"].values() if v.get("wgm_g1_median")]) for f in folds])
    d={}
    for f in folds:
        for key,v in b[f]["drift"].items():
            if not key.endswith("|"+anchor): continue
            d.setdefault(key,[]).append(v)
    out={}
    for key,vs in d.items():
        out[key]={k: st.mean([v[k] for v in vs]) for k in ("pair_unseen_rate","novel_comb_rate","novel_comb_cond","ff_frac","wgm_g1_median")}
        out[key]["domain"]=vs[0]["domain"]
    return routine,out

for split in ("pairgroup","batch"):
    routine,d=load(split)
    code=[f"{t}|product_onset" for t in PROG if f"{t}|product_onset" in d]
    other=[k for k in d if k not in code]
    print(f"\n===== split={split} =====")
    lm=lambda k: math.log(d[k]["wgm_g1_median"]/routine["wgm_g1_median"])
    lj=lambda k: math.log(d[k]["pair_unseen_rate"]/routine["pair_unseen_rate"])
    xs=[lm(k) for k in other]; ys=[lj(k) for k in other]
    mx,my=st.mean(xs),st.mean(ys)
    b=sum((x-mx)*(y-my) for x,y in zip(xs,ys))/sum((x-mx)**2 for x in xs); a=my-b*mx
    sr=math.sqrt(sum((y-(a+b*x))**2 for x,y in zip(xs,ys))/(len(xs)-2))
    print(f"fit on 51 non-programming drifts: log joint = {a:.3f} + {b:.3f} * log marginal ; residual sd {sr:.3f}")
    print("| code trace | marginal ratio | joint ratio | predicted joint | residual (sd) | A observed | A predicted |")
    print("|---"*7+"|")
    resid=[]
    for k in code:
        x,y=lm(k),lj(k); p=a+b*x; r=(y-p)/sr; resid.append(r)
        print(f"| {k.split('--')[0][:34]} | {math.exp(x):.2f} | {math.exp(y):.2f} | {math.exp(p):.2f} | {r:+.2f} | {math.exp(y-x):.2f} | {math.exp(p-x):.2f} |")
    print(f"code residual median {st.median(resid):+.2f} sd-units; residuals > 0: {sum(1 for r in resid if r>0)}/8; |resid|>1: {sum(1 for r in resid if abs(r)>1)}/8")
    # decomposition of the headline novel-comb gap
    def m(keys,k): return st.median([d[x][k] for x in keys])
    cf,ff = m(code,"ff_frac"), m(other,"ff_frac")
    cc,oc = m(code,"novel_comb_cond"), m(other,"novel_comb_cond")
    cn,on = m(code,"novel_comb_rate"), m(other,"novel_comb_rate")
    print(f"\nnovel_comb_rate  code {cn:.5f} ({cn/routine['novel_comb_rate']:.2f}x)  other {on:.5f} ({on/routine['novel_comb_rate']:.2f}x)  gap {cn/on:.2f}x")
    print(f"  = ff_frac        code {cf:.3f} ({cf/routine['ff_frac']:.2f}x)  other {ff:.3f} ({ff/routine['ff_frac']:.2f}x)  factor {cf/ff:.2f}x   [MARGINAL: staying inside routine's expert support]")
    print(f"  x cond rate      code {cc:.5f} ({cc/routine['novel_comb_cond']:.2f}x)  other {oc:.5f} ({oc/routine['novel_comb_cond']:.2f}x)  factor {cc/oc:.2f}x   [JOINT: re-wiring per opportunity]")
    lg=math.log(cn/on)
    print(f"  log-share of the code-vs-other gap: marginal factor {math.log(cf/ff)/lg*100:.0f}%, joint factor {math.log(cc/oc)/lg*100:.0f}%")
    # domain ranking on the conditional
    doms={}
    for k in other: doms.setdefault(d[k]["domain"],[]).append(k)
    ranking=sorted([("programming",m(code,"novel_comb_cond"))]+[(dm,m(ks,"novel_comb_cond")) for dm,ks in doms.items()], key=lambda p:-p[1])
    print("  domain ranking on the CONDITIONAL re-wiring rate: " + ", ".join(f"{n}={v/routine['novel_comb_cond']:.2f}x" for n,v in ranking))
    ranking2=sorted([("programming",m(code,"novel_comb_rate"))]+[(dm,m(ks,"novel_comb_rate")) for dm,ks in doms.items()], key=lambda p:-p[1])
    print("  domain ranking on the UNCONDITIONAL rate:          " + ", ".join(f"{n}={v/routine['novel_comb_rate']:.2f}x" for n,v in ranking2))
    cv=[d[k]["novel_comb_cond"] for k in code]
    for name,thr in (("other-drift median",m(other,"novel_comb_cond")),("cooking median",m(doms.get("cooking",[]),"novel_comb_cond") if "cooking" in doms else None)):
        if thr: print(f"  code traces above {name} ({thr:.4f}): {sum(1 for v in cv if v>thr)}/8")
