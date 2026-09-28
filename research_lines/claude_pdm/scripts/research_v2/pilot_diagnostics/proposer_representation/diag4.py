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
def tmax_auroc(scored):
    pm=[s[l==1].max() for d,s,e,l in scored if d["pos"] and (l==1).any()]
    nm=[s.max() for d,s,e,l in scored if not d["pos"]]
    return binary_auroc(torch.stack(pm+nm),torch.tensor([1]*len(pm)+[0]*len(nm)))
# (a) OOV-fraction text-only baseline
def oov_rows(ds):
    rows=[]
    for d in ds:
        T=len(d["ids"]);
        if T<W: continue
        o=(TABC[d["ids"]]<5).float(); cs=torch.cat([torch.zeros(1),o.cumsum(0)]); m=(cs[W:]-cs[:-W])/W
        ends=torch.arange(W-1,T); rows.append((d,m,ends,label_windows(d,ends)))
    return rows
sc=oov_rows(b2); print(f"(a) text-only OOV-fraction (token unseen<5 in B1 routine) window mean: B2 trace-max AUROC {tmax_auroc(sc):.3f}")
rmax=torch.stack([s.max() for d,s,e,l in oov_rows(b1) if d["arm"] in ("clean","benign_control")])
for q in (0.95,1.0): alarm_eval(sc,float(torch.quantile(rmax,q)),f"OOV q{q}")
# (b) in-vocab-only residual windows, L4-7
def invocab_rows(ds,layers):
    rows=[]
    for d in ds:
        T=len(d["ids"])
        if T<W: continue
        keep=TABC[d["ids"]]>=5
        X=d["sel"]-TABT[d["ids"]].permute(1,0,2); X[:,~keep,:]=0; X=X[layers]
        cs=torch.cat([torch.zeros(len(layers),1,E),X.cumsum(1)],1); ck=torch.cat([torch.zeros(1),keep.float().cumsum(0)]); cnt=(ck[W:]-ck[:-W]).clamp_min(1)
        m=((cs[:,W:]-cs[:,:-W])/cnt[None,:,None]).permute(1,0,2).reshape(T-W+1,-1)
        ends=torch.arange(W-1,T); rows.append((d,m,ends,label_windows(d,ends)))
    return rows
layers=[4,5,6,7]
tr=invocab_rows(b1,layers); te=invocab_rows(b2,layers); idx,w,mu=fit_dom(tr,list(range(4)),64)
sc=trace_scores(te,idx,w,mu); print(f"(b) in-vocab-only residual L4-7 DoM: B2 trace-max AUROC {tmax_auroc(sc):.3f}; pre-boundary-vs-routine {propensity(sc)[0]:.3f}, resist-vs-routine {propensity(sc)[1]:.3f}")
# (c) one-class PCA residual energy on B1 routine windows (resid L4-7, 256-d), no drift labels
tr=collect(b1,"sel",True); te=collect(b2,"sel",True); idx=torch.tensor([l*64+f for l in layers for f in range(64)])
Xr=torch.cat([X[:,idx] for d,X,e,l in tr if d["arm"] in ("clean","benign_control")]); mu=Xr.mean(0); sd=Xr.std(0)+1e-3
Z=(Xr-mu)/sd; U,S,Vt=torch.linalg.svd(Z,full_matrices=False)
for r in (0,8,32,128):
    P=Vt[:r].T
    def energy(X): Zt=(X[:,idx]-mu)/sd; return (Zt**2).sum(1)-((Zt@P)**2).sum(1) if r>0 else (Zt**2).sum(1)
    sc=[(d,energy(X),e,l) for d,X,e,l in te]
    sc1=[(d,energy(X),e,l) for d,X,e,l in tr]
    rmax=torch.stack([s.max() for d,s,e,l in sc1 if d["arm"] in ("clean","benign_control")])
    print(f"(c) one-class PCA residual energy r={r} (resid L4-7): B2 trace-max AUROC {tmax_auroc(sc):.3f}; pre-vs-routine {propensity(sc)[0]:.3f}; resist-vs-routine {propensity(sc)[1]:.3f}")
    alarm_eval(sc,float(torch.quantile(rmax,0.95)),f"PCA r={r} B1-routine q0.95")
# raw features one-class for comparison
trr=collect(b1,"sel",False); ter=collect(b2,"sel",False)
Xr=torch.cat([X[:,idx] for d,X,e,l in trr if d["arm"] in ("clean","benign_control")]); mu=Xr.mean(0); sd=Xr.std(0)+1e-3
Z=(Xr-mu)/sd; U,S,Vt=torch.linalg.svd(Z,full_matrices=False); P=Vt[:32].T
def energy(X): Zt=(X[:,idx]-mu)/sd; return (Zt**2).sum(1)-((Zt@P)**2).sum(1)
sc=[(d,energy(X),e,l) for d,X,e,l in ter]; print(f"(c') one-class PCA r=32 RAW L4-7: B2 trace-max AUROC {tmax_auroc(sc):.3f}; pre-vs-routine {propensity(sc)[0]:.3f}; resist-vs-routine {propensity(sc)[1]:.3f}")
# (d) CUSUM on standardized L5 residual DoM score
tr=collect(b1,"sel",True); te=collect(b2,"sel",True); idx,w,mu=fit_dom(tr,[5],64)
sc_tr=trace_scores(tr,idx,w,mu); sc_te=trace_scores(te,idx,w,mu)
rw=torch.cat([s for d,s,e,l in sc_tr if d["arm"] in ("clean","benign_control")]); m0=rw.mean(); s0=rw.std()
def cusum(s,k):
    z=(s-m0)/s0; c=torch.zeros_like(z); acc=0.0
    for i in range(len(z)): acc=max(0.0,acc+float(z[i])-k); c[i]=acc
    return c
for k in (0.5,1.0,2.0):
    cs_tr=[(d,cusum(s,k),e,l) for d,s,e,l in sc_tr]; cs_te=[(d,cusum(s,k),e,l) for d,s,e,l in sc_te]
    rmax=torch.stack([c.max() for d,c,e,l in cs_tr if d["arm"] in ("clean","benign_control")])
    print(f"(d) CUSUM k={k} on standardized L5 residual score: B2 trace-max AUROC {tmax_auroc(cs_te):.3f}; pre-vs-routine {propensity(cs_te)[0]:.3f}")
    for q in (0.95,1.0): alarm_eval(cs_te,float(torch.quantile(rmax,q)),f"CUSUM k={k} B1-routine q{q}")
# distribution check: B1 vs B2 routine window score mean/sd for L5 residual
rw2=torch.cat([s for d,s,e,l in sc_te if d["arm"] in ("clean","benign_control")])
print(f"(e) L5 residual score routine windows: B1 mean {m0:.2f} sd {s0:.2f} | B2 mean {rw2.mean():.2f} sd {rw2.std():.2f}; trace-max routine q50/q95/max B1 {[round(float(torch.quantile(torch.stack([s.max() for d,s,e,l in sc_tr if d['arm']!='attack']),q)),1) for q in (0.5,0.95,1.0)]} B2 {[round(float(torch.quantile(torch.stack([s.max() for d,s,e,l in sc_te if d['arm']!='attack']),q)),1) for q in (0.5,0.95,1.0)]}")
