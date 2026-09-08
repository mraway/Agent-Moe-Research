"""Length control: recompute the routine baselines on 48-token routine windows
(matched to the drift region length) instead of whole routine traces."""
from __future__ import annotations
import json, statistics as st, sys
from pathlib import Path
import torch
ROOT=Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
sys.path.insert(0,str(ROOT/"src"))
from research_v2 import io as rio  # noqa: E402
E=64; REGION=48
b=rio.load_core(); traces=[t for k in ("b1","b2") for t in b[k]]
routine=[t for t in traces if rio.arm_class(t) in ("clean","benign")]
groups=sorted({t.pair_group_id for t in routine}); fold_of={g:i%2 for i,g in enumerate(groups)}
folds={f:[t for t in routine if fold_of[t.pair_group_id]==f] for f in (0,1)}
res={}
for f in (0,1):
    fit,hold=folds[f],folds[1-f]
    pair=torch.zeros((15,E,E),dtype=torch.float64); marg=torch.zeros((16,E),dtype=torch.float64); n=0
    for t in fit:
        top1=t.top_k_ids.long()[:,:,0]; n+=top1.shape[1]
        for l in range(16): marg[l]+=torch.bincount(top1[l],minlength=E)
        for l in range(15): pair[l]+=torch.bincount(top1[l]*E+top1[l+1],minlength=E*E).reshape(E,E)
    unseen=pair==0; common=(marg/n)>=1.0/E
    pu=[];nc=[];cond=[];ff=[];exc=[]
    for t in hold:
        top1=t.top_k_ids.long()[:,:,0]; T=top1.shape[1]
        for s in range(0,T-REGION+1,REGION):
            lo,hi=s,s+REGION
            tot=u=c_n=uc=0; expd=0.0
            rm=torch.zeros((16,E),dtype=torch.float64)
            for l in range(16): rm[l]=torch.bincount(top1[l,lo:hi],minlength=E).double()/REGION
            for l in range(15):
                a,bb=top1[l,lo:hi],top1[l+1,lo:hi]
                uu=unseen[l][a,bb]; cc=common[l][a]&common[l+1][bb]
                u+=int(uu.sum()); c_n+=int(cc.sum()); uc+=int((uu&cc).sum()); tot+=REGION
                expd+=float(rm[l]@unseen[l].double()@rm[l+1])*REGION
            pu.append(u/tot); nc.append(uc/tot); ff.append(c_n/tot)
            cond.append(uc/max(1,c_n)); exc.append((u/tot)/max(1e-12,expd/tot))
    res[f]={"n_windows":len(pu),"pair_unseen":st.mean(pu),"novel_comb":st.mean(nc),
            "cond":st.mean(cond),"ff_frac":st.mean(ff),"joint_excess":st.mean(exc),
            "pair_unseen_med":st.median(pu),"novel_comb_med":st.median(nc),"cond_med":st.median(cond),"joint_excess_med":st.median(exc)}
    print(f"fold {f}: {res[f]}")
avg={k: st.mean([res[0][k],res[1][k]]) for k in res[0]}
print("\nfold-averaged 48-token routine-window baselines:", {k:round(v,5) for k,v in avg.items()})
print("(compare with the whole-trace routine baselines: pair_unseen 0.0211, novel_comb 0.00433, cond 0.00671, ff 0.655, joint_excess 0.0474)")
