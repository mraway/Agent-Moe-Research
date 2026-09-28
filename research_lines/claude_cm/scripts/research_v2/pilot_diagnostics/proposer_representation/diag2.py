"""POST-HOC PILOT DIAGNOSTIC (not a result). Where does the drift signal live: token identity vs context, per layer."""
import torch, json, math, sys
from pathlib import Path
torch.set_num_threads(4)
def binary_auroc(scores,labels):
    scores=scores.double().reshape(-1); labels=labels.bool().reshape(-1)
    order=scores.argsort(); s=scores[order]
    # average ranks with ties
    ranks=torch.empty_like(s); n=s.numel()
    uniq,inv,cnt=torch.unique(s,return_inverse=True,return_counts=True)
    csum=cnt.cumsum(0); avg=(csum.double()-(cnt.double()-1)/2.0)
    r=avg[inv]; ranks=torch.empty(n,dtype=torch.double); ranks[order]=r
    npos=labels.sum(); nneg=n-npos
    return float((ranks[labels].sum()-npos*(npos+1)/2)/(npos*nneg))
D=Path(__file__).parent
b1=torch.load(D/"b1_compact.pt"); b2=torch.load(D/"b2_compact.pt")
L,E,K=16,64,8
def sel_of(d):
    tk=d["topk"].long(); s=torch.zeros(L,tk.shape[1],E); s.scatter_add_(2,tk,torch.ones_like(tk,dtype=torch.float32)); return s
def stats_of(d):
    lg=d["logits"].float(); p=torch.softmax(lg,-1)
    ent=-(p*p.clamp_min(1e-12).log()).sum(-1)              # [L,T]
    top2=lg.topk(2,dim=-1).values; margin=top2[...,0]-top2[...,1]
    w=d["topw"].float(); top1w=w[...,0]
    eff=1.0/(p*p).sum(-1)
    return torch.stack([ent,margin,top1w,eff],-1)          # [L,T,4]
for d in b1+b2:
    d["sel"]=sel_of(d); d["st"]=stats_of(d)
routine=lambda ds:[d for d in ds if d["arm"] in ("clean","benign_control")]
drift=lambda ds:[d for d in ds if d["pos"]]
nondrift=lambda ds:[d for d in ds if not d["pos"]]

# ---------- D1: token-identity table from B1 routine (all 160 routine traces incl brief=present) ----------
def build_table(ds,min_count=1):
    sums={}; cnt={}
    for d in ds:
        for t,i in enumerate(d["ids"].tolist()):
            sums[i]=sums.get(i,0)+d["sel"][:,t,:]; cnt[i]=cnt.get(i,0)+1
    return {i:(sums[i]/cnt[i],cnt[i]) for i in sums}
tab=build_table(routine(b1))
VOCAB=max(max(d["ids"].max().item() for d in b1+b2)+1,1)
TABT=torch.zeros(VOCAB,L,E); TABC=torch.zeros(VOCAB)
for i,(m,c) in tab.items(): TABT[i]=m; TABC[i]=c
glob=torch.stack([d["sel"].mean(1) for d in routine(b1)]).mean(0)  # [L,E]
def r2_per_layer(ds,tab,min_count):
    ss_res=torch.zeros(L); ss_tot=torch.zeros(L); cov=0; tot=0
    for d in ds:
        ids=d["ids"]; ok=TABC[ids]>=min_count; tot+=len(ids); cov+=int(ok.sum())
        S=d["sel"][:,ok,:]; ref=TABT[ids[ok]].permute(1,0,2)
        ss_res+=((S-ref)**2).sum((1,2)); ss_tot+=((S-glob[:,None,:])**2).sum((1,2))
    return (1-ss_res/ss_tot), cov/tot
print("== D1 token-identity R2 of top-8 selection (table from B1 routine, eval on B2 routine), per layer ==")
for mc in (5,20):
    r2,cov=r2_per_layer(routine(b2),tab,mc)
    print(f" min_count>={mc} coverage={cov:.2f} R2 per layer:",[round(x,2) for x in r2.tolist()])
r2d,covd=r2_per_layer(drift(b2),tab,5); print(f" B2 drift traces (all tokens) coverage={covd:.2f} R2:",[round(x,2) for x in r2d.tolist()])

# ---------- window features ----------
W=16
def windows(d,feat,resid):
    """feat: 'sel' or 'st'; resid: subtract token-id table (fallback global). Returns [nwin, L*F], ends"""
    X=d[feat].clone()  # [L,T,F]
    if resid:
        ids=d["ids"]; ok=TABC[ids]>=5
        ref=torch.where(ok[:,None,None],TABT[ids],glob[None]).permute(1,0,2)
        X=X-ref
    T=X.shape[1]
    if T<W: return None,None
    cs=torch.cat([torch.zeros(L,1,X.shape[2]),X.cumsum(1)],1)
    mean=(cs[:,W:]-cs[:,:-W])/W
    return mean.permute(1,0,2).reshape(T-W+1,-1), torch.arange(W-1,T)
def label_windows(d,ends):
    if not d["pos"]: return torch.zeros(len(ends),dtype=torch.long)  # 0 = negative
    b=d["b"]; lab=torch.full((len(ends),),-1,dtype=torch.long)     # -1 = ignore (straddling)
    lab[ends< b]=0; lab[ends>=b+W-1]=1
    return lab
def collect(ds,feat,resid):
    rows=[]
    for d in ds:
        X,ends=windows(d,feat,resid)
        if X is None: continue
        rows.append((d,X,ends,label_windows(d,ends)))
    return rows
def fit_dom(rows,layers,F):
    idx=torch.tensor([l*F+f for l in layers for f in range(F)])
    pos=[];neg=[]
    for d,X,ends,lab in rows:
        if (lab==1).any(): pos.append(X[lab==1][:,idx].mean(0))
        if (lab==0).any() and not d["pos"]: neg.append(X[lab==0][:,idx].mean(0))
    pos=torch.stack(pos); neg=torch.stack(neg)
    var=0.5*(pos.var(0)+neg.var(0))+1e-6
    w=(pos.mean(0)-neg.mean(0))/var
    return idx,w,(pos.mean(0)+neg.mean(0))/2
def eval_dom(rows,idx,w,mu):
    tmax=[];tlab=[]; wsc=[];wlab=[]
    for d,X,ends,lab in rows:
        s=(X[:,idx]-mu)@w
        if d["pos"]:
            if (lab==1).any(): tmax.append(s[lab==1].max()); tlab.append(1)
        else:
            tmax.append(s.max()); tlab.append(0)
        keep=lab>=0; wsc.append(s[keep]); wlab.append(lab[keep])
    tmax=torch.stack(tmax); tlab=torch.tensor(tlab)
    wsc=torch.cat(wsc); wlab=torch.cat(wlab)
    return binary_auroc(tmax,tlab.bool()), binary_auroc(wsc,wlab.bool())
bands={"early0-4":range(0,5),"mid5-11":range(5,12),"late12-15":range(12,16),"all":range(16)}
print("\n== D2/D4 cross-batch diag-LDA (difference-of-means) direction: fit on B1 (brief-absent+present), test on B2; AUROC trace-max / window ==")
for feat,F in (("sel",64),("st",4)):
    for resid in ((False,True) if feat=="sel" else (False,)):
        tr=collect(b1,feat,resid); te=collect(b2,feat,resid)
        line=[]
        for name,ls in bands.items():
            idx,w,mu=fit_dom(tr,list(ls),F); a_t,a_w=eval_dom(te,idx,w,mu)
            line.append(f"{name}: trace {a_t:.3f} win {a_w:.3f}")
        print(f" feat={feat} resid={resid}: "+" | ".join(line))
    # per-layer single-layer curves
    for resid in ((False,True) if feat=="sel" else (False,)):
        tr=collect(b1,feat,resid); te=collect(b2,feat,resid)
        per=[]
        for l in range(16):
            idx,w,mu=fit_dom(tr,[l],F); a_t,a_w=eval_dom(te,idx,w,mu); per.append(round(a_t,2))
        print(f"   per-layer trace-max AUROC feat={feat} resid={resid}:",per)
print("\n== reverse direction: fit on B2, test on B1 brief-absent ==")
b1abs=[d for d in b1 if d["brief"]=="absent"]
for feat,F in (("sel",64),("st",4)):
    for resid in ((False,True) if feat=="sel" else (False,)):
        tr=collect(b2,feat,resid); te=collect(b1abs,feat,resid)
        line=[]
        for name,ls in bands.items():
            idx,w,mu=fit_dom(tr,list(ls),F); a_t,a_w=eval_dom(te,idx,w,mu)
            line.append(f"{name}: trace {a_t:.3f} win {a_w:.3f}")
        print(f" feat={feat} resid={resid}: "+" | ".join(line))

# ---------- D3: same-token control: restrict to 40 most frequent routine tokens, residual only ----------
print("\n== D3 same-token control: windows built ONLY from the 40 most frequent routine token ids (function words/punct), residualized ==")
freq=sorted(tab.items(),key=lambda kv:-kv[1][1])[:40]; common={i for i,_ in freq}
def windows_common(d):
    X=d["sel"].clone(); T=X.shape[1]
    keep=torch.tensor([i in common for i in d["ids"].tolist()])
    X=X-TABT[d["ids"]].permute(1,0,2)
    X[:,~keep,:]=0
    if T<W: return None,None
    cs=torch.cat([torch.zeros(L,1,E),X.cumsum(1)],1); ck=torch.cat([torch.zeros(1),keep.float().cumsum(0)])
    cnt=(ck[W:]-ck[:-W]).clamp_min(1)
    mean=(cs[:,W:]-cs[:,:-W])/cnt[None,:,None]
    return mean.permute(1,0,2).reshape(T-W+1,-1), torch.arange(W-1,T)
def collect_common(ds):
    rows=[]
    for d in ds:
        X,ends=windows_common(d)
        if X is None: continue
        rows.append((d,X,ends,label_windows(d,ends)))
    return rows
tr=collect_common(b1); te=collect_common(b2)
for name,ls in bands.items():
    idx,w,mu=fit_dom(tr,list(ls),64); a_t,a_w=eval_dom(te,idx,w,mu); print(f" {name}: trace {a_t:.3f} win {a_w:.3f}")
print(" common token texts sample:", [t for t in freq[:15]])
