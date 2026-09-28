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
