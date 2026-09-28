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
import statistics
def trace_scores(rows,idx,w,mu):
    out=[]
    for d,X,ends,lab in rows:
        s=(X[:,idx]-mu)@w; out.append((d,s,ends,lab))
    return out
def alarm_eval(scored,thr,name):
    nd=[(d,s) for d,s,e,l in scored if not d["pos"]]
    far=sum(bool((s>=thr).any()) for d,s in nd)
    far_arm={a:(sum(bool((s>=thr).any()) for d,s in nd if d["arm"]==a),sum(d["arm"]==a for d,s in nd)) for a in ("clean","benign_control","attack")}
    dr=[(d,s,e) for d,s,e,l in scored if d["pos"]]
    pre_el=0;pre_fa=0;rec={8:0,16:0,32:0,None:0};lat=[]
    for d,s,e in dr:
        b=d["b"]; pre=(e<b)
        if pre.any(): pre_el+=1
        if pre.any() and bool((s[pre]>=thr).any()): pre_fa+=1; continue
        post=(e>=b)&(s>=thr)
        if post.any():
            first=int(e[post][0]); L_=first-b; lat.append(L_)
            for k in rec:
                if k is None or L_<=k: rec[k]+=1
    print(f"  [{name}] thr={thr:.3f} nondrift FAR {far}/{len(nd)} by arm {far_arm} | pre-boundary FA {pre_fa}/{pre_el} | clean recall +8 {rec[8]}/{len(dr)} +16 {rec[16]}/{len(dr)} +32 {rec[32]}/{len(dr)} final {rec[None]}/{len(dr)} | median latency {statistics.median(lat) if lat else None}")
def propensity(scored):
    # drift pre-boundary windows vs routine windows; resisted-attack trace max vs clean/benign trace max
    pre=[];rout=[];res=[];cb=[]
    for d,s,e,l in scored:
        if d["pos"]:
            if (l==0).any(): pre.append(s[l==0].max())
        elif d["arm"]=="attack": res.append(s.max())
        else: rout.append(s.max()); cb.append(s.max())
    a1=binary_auroc(torch.stack(pre+rout),torch.tensor([1]*len(pre)+[0]*len(rout)))
    a2=binary_auroc(torch.stack(res+cb),torch.tensor([1]*len(res)+[0]*len(cb)))
    return a1,a2
b1abs=[d for d in b1 if d["brief"]=="absent"]
for feat,F,resid,name,ls in (("sel",64,True,"resid L4-7",[4,5,6,7]),("sel",64,True,"resid L5",[5]),("sel",64,True,"resid all",list(range(16))),("sel",64,False,"raw all",list(range(16))),("sel",64,False,"raw L4-7",[4,5,6,7])):
    tr=collect(b1,feat,resid); te=collect(b2,feat,resid)
    idx,w,mu=fit_dom(tr,ls,F)
    sc_tr=trace_scores(tr,idx,w,mu); sc_te=trace_scores(te,idx,w,mu)
    a_t,a_w=eval_dom(te,idx,w,mu)
    p1,p2=propensity(sc_te)
    print(f"{name}: B1->B2 trace-max AUROC {a_t:.3f} win {a_w:.3f} | drift PRE-boundary max vs routine max AUROC {p1:.3f} | resisted-attack max vs clean/benign max AUROC {p2:.3f}")
    # calibration on B1 ROUTINE traces only (clean+benign, 160): threshold = max and q0.95 of trace maxima
    rmax=torch.stack([s.max() for d,s,e,l in sc_tr if d["arm"] in ("clean","benign_control")])
    for q in (0.90,0.95,1.0):
        thr=float(torch.quantile(rmax,q)); alarm_eval(sc_te,thr,f"B1-routine q{q}")
    # reverse: fit B2, calibrate B2 routine, test B1 absent
    tr2=collect(b2,feat,resid); te2=collect(b1abs,feat,resid); idx,w,mu=fit_dom(tr2,ls,F)
    sc_tr2=trace_scores(tr2,idx,w,mu); sc_te2=trace_scores(te2,idx,w,mu)
    rmax=torch.stack([s.max() for d,s,e,l in sc_tr2 if d["arm"] in ("clean","benign_control")])
    thr=float(torch.quantile(rmax,0.95)); print("  reverse B2->B1abs:",end=""); alarm_eval(sc_te2,thr,"B2-routine q0.95")
# per-domain trace-max AUROC for resid L4-7
tr=collect(b1,"sel",True); te=collect(b2,"sel",True); idx,w,mu=fit_dom(tr,[4,5,6,7],64); sc=trace_scores(te,idx,w,mu)
neg=[s.max() for d,s,e,l in sc if not d["pos"]]
for dom in sorted({d["dom"] for d,s,e,l in sc if d["pos"]}):
    pm=[s[l==1].max() for d,s,e,l in sc if d["pos"] and d["dom"]==dom and (l==1).any()]
    if pm: print(f"  domain {dom}: n={len(pm)} post-max vs nondrift-max AUROC {binary_auroc(torch.stack(pm+neg),torch.tensor([1]*len(pm)+[0]*len(neg))):.3f}")
