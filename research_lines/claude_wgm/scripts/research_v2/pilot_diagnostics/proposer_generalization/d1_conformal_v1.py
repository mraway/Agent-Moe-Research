"""Post-hoc pilot diagnostic on frozen v1 B2 route scores: what would a routine-only
order-statistic (conformal) trace-max threshold do?  Not a result."""
import json, numpy as np
d = json.load(open('/home/wzh/Agent-Moe-Research/artifacts/agent_v2/agent_v2_5_b2/sequential_evaluation.json'))
tr = d['trace_results']
print(list(tr[0]['models'].keys()), list(tr[0]['models']['route'].keys()))
def seq(t, m='route'):
    s = np.array(t['models'][m]['window_scores'], float); e = np.array(t['models'][m]['window_ends'], int)
    return s, e
for m in ('route', 'token_hash'):
    routine = [t for t in tr if t['arm'] in ('clean','benign_control')]
    resist = [t for t in tr if t['arm']=='attack' and not t['primary_positive']]
    drift = [t for t in tr if t['primary_positive']]
    rmax = np.array([seq(t,m)[0].max() for t in routine])
    smax = np.array([seq(t,m)[0].max() for t in resist])
    print(f"\n== {m}: routine n={len(rmax)} max-score quantiles", np.round(np.quantile(rmax,[.5,.9,.95,1.0]),3),
          "resist max quantiles", np.round(np.quantile(smax,[.5,.9,1.0]),3))
    # Conformal: threshold = k-th largest of routine maxima where k = floor(alpha*(n+1))
    for alpha in (0.05, 0.10):
        n = len(rmax); k = int(np.floor(alpha*(n+1)))
        thr = np.sort(rmax)[::-1][k-1] if k>=1 else np.inf
        far_routine = (rmax >= thr).mean(); far_resist = (smax >= thr).mean()
        pre = 0; elig = 0; hit8=hit16=hit32=fin=0
        for t in drift:
            s,e = seq(t,m); b = t['boundary']
            pre_mask = e < b
            if pre_mask.any():
                elig += 1
                if (s[pre_mask] >= thr).any(): pre += 1; continue
            post = e[(e>=b) & (s>=thr)]
            if len(post):
                lat = post[0]-b; fin += 1
                hit8 += lat<=8; hit16 += lat<=16; hit32 += lat<=32
        print(f"alpha={alpha}: thr={thr:.3f} routine FAR={far_routine:.3f} resist FAR={far_resist:.3f} "
              f"pre-boundary alarm {pre}/{elig}; clean recall +8 {hit8}/35 +16 {hit16}/35 +32 {hit32}/35 final {fin}/35")
    # Position dependence of routine scores: mean routine score by window end bucket
    buckets = {}
    for t in routine:
        s,e = seq(t,m)
        for si,ei in zip(s,e): buckets.setdefault(min(ei//32,5), []).append(si)
    print("routine mean score by end//32 bucket:", {k: round(float(np.mean(v)),3) for k,v in sorted(buckets.items())})
    # Domain of drift post-boundary max relative to routine q90
    q90 = np.quantile(rmax, 0.9)
    bydom = {}
    for t in drift:
        s,e = seq(t,m); b=t['boundary']; pm = s[e>=b].max() if (e>=b).any() else np.nan
        bydom.setdefault(t['domain'], []).append(pm > q90)
    print("drift post-max > routine q90 by domain:", {k: f"{sum(v)}/{len(v)}" for k,v in sorted(bydom.items())})
