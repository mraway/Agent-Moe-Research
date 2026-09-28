"""Post-hoc pilot diagnostics (NOT results) on cached 16-token route_selection windows.
(a) cross-batch stability of difference-of-means directions (task vs propensity), per layer;
(b) propensity-orthogonalised projection: trace-max AUROC and conformal FAR / pre-boundary alarms cross-batch;
(c) routine-only PCA residual (one-class) cross-batch: routine / resist / drift trace-max behaviour;
(d) workflow share of routine-window variance."""
import sys, numpy as np, torch
rows = torch.load(sys.argv[1])
for r in rows: r['feats'] = r['feats'].float().numpy(); r['ends'] = r['ends'].numpy()
W = 16
def auroc(p, n):
    p = np.asarray(p); n = np.asarray(n)
    return ((p[:, None] > n[None, :]).mean() + 0.5 * (p[:, None] == n[None, :]).mean())
def sets(batch):
    R = [r for r in rows if r['batch'] == batch]
    routine = [r for r in R if r['arm'] in ('clean', 'benign_control')]
    resist = [r for r in R if r['arm'] == 'attack' and not r['positive']]
    drift = [r for r in R if r['positive']]
    return R, routine, resist, drift
def trace_mean(r, mask):
    f = r['feats'][mask]; return f.mean(0) if len(f) else None
def directions(batch):
    """trace-equal-weighted difference of means; scenario-matched where possible."""
    R, routine, resist, drift = sets(batch)
    benign = {r['group']: r for r in R if r['arm'] == 'benign_control'}
    task, prop = [], []
    for r in drift:
        b = r['boundary']; post = r['ends'] >= b + W - 1; pre = r['ends'] < b
        ref = trace_mean(benign[r['group']], np.ones(len(benign[r['group']]['ends']), bool))
        if post.any(): task.append(trace_mean(r, post) - ref)
        if pre.any(): prop.append(trace_mean(r, pre) - ref)
    for r in resist:
        ref = trace_mean(benign[r['group']], np.ones(len(benign[r['group']]['ends']), bool))
        prop.append(trace_mean(r, np.ones(len(r['ends']), bool)) - ref)
    return np.mean(task, 0), np.mean(prop, 0), len(task), len(prop)
def routine_stats(batch):
    _, routine, _, _ = sets(batch)
    X = np.concatenate([r['feats'] for r in routine])
    return X.mean(0), X.std(0) + 1e-3, X
d = {b: directions(b) for b in ('b1', 'b2')}
print("n task/prop traces:", {b: d[b][2:] for b in d})
mu, sd, Xr = routine_stats('b1')
def cos(a, b): return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))
print("\n(a) cross-batch cosine of whitened difference-of-means directions")
print("   full 1024-d: task", round(cos(d['b1'][0]/sd, d['b2'][0]/sd), 3), " prop", round(cos(d['b1'][1]/sd, d['b2'][1]/sd), 3),
      " task-vs-prop within b1", round(cos(d['b1'][0]/sd, d['b1'][1]/sd), 3), " within b2", round(cos(d['b2'][0]/sd, d['b2'][1]/sd), 3))
lay = lambda v, l: (v/sd)[l*64:(l+1)*64]
print("   per-layer task cos b1~b2:", [round(cos(lay(d['b1'][0], l), lay(d['b2'][0], l)), 2) for l in range(16)])
print("   per-layer prop cos b1~b2:", [round(cos(lay(d['b1'][1], l), lay(d['b2'][1], l)), 2) for l in range(16)])
print("   per-layer task~prop (b1):", [round(cos(lay(d['b1'][0], l), lay(d['b1'][1], l)), 2) for l in range(16)])

def evaluate(name, score_fn, train, test, alpha=0.10):
    _, routine_tr, _, _ = sets(train); _, routine_te, resist_te, drift_te = sets(test)
    rmax_tr = np.array([score_fn(r).max() for r in routine_tr])
    n = len(rmax_tr); k = int(np.floor(alpha * (n + 1))); thr = np.sort(rmax_tr)[::-1][k - 1]
    rmax = np.array([score_fn(r).max() for r in routine_te]); smax = np.array([score_fn(r).max() for r in resist_te])
    pmax, pre, elig, h16, fin = [], 0, 0, 0, 0
    for r in drift_te:
        s = score_fn(r); e = r['ends']; b = r['boundary']
        post = e >= b
        if post.any(): pmax.append(s[post].max())
        if (~post).any():
            elig += 1
            if (s[~post] >= thr).any(): pre += 1; continue
        hit = e[post & (s >= thr)]
        if len(hit): fin += 1; h16 += (hit[0] - b) <= 16
    print(f"  {name} {train}->{test}: traceMax AUROC drift-post vs routine {auroc(pmax, rmax):.3f}, vs resist {auroc(pmax, smax):.3f} | "
          f"conformal a={alpha}: routine FAR {(rmax>=thr).mean():.3f} resist FAR {(smax>=thr).mean():.3f} pre-alarm {pre}/{elig} +16 {h16}/{len(drift_te)} final {fin}/{len(drift_te)}")

print("\n(b) projection scores, cross-batch (direction+whitening from train batch routine only)")
for train, test in (('b1', 'b2'), ('b2', 'b1')):
    mu, sd, Xr = routine_stats(train)
    t, p = d[train][0] / sd, d[train][1] / sd
    t_u = t / np.linalg.norm(t); p_u = p / np.linalg.norm(p)
    t_orth = t_u - (t_u @ p_u) * p_u; t_orth /= np.linalg.norm(t_orth)
    z = lambda r: (r['feats'] - mu) / sd
    evaluate("task-dir        ", lambda r: z(r) @ t_u, train, test)
    evaluate("prop-dir        ", lambda r: z(r) @ p_u, train, test)
    evaluate("task orth prop  ", lambda r: z(r) @ t_orth, train, test)
    # sparse middle-layer variants: keep only layers whose task direction had cross-batch cosine >= 0.3 in (a) -- adaptive, for diagnosis only
    good = [l for l in range(16) if cos(lay(d['b1'][0], l), lay(d['b2'][0], l)) >= 0.3]
    m = np.zeros(1024); [m.__setitem__(slice(l*64, (l+1)*64), 1) for l in good]
    tm = t_orth * m; tm /= np.linalg.norm(tm) + 1e-12
    evaluate(f"orth, layers{good}", lambda r: z(r) @ tm, train, test)
    print("\n(c) routine-only PCA residual (one-class), rank k")
    for k in (4, 16, 64):
        Xc = (Xr - mu) / sd
        U, S, Vt = np.linalg.svd(Xc, full_matrices=False); V = Vt[:k].T
        def resid(r, V=V): Z = z(r); return np.linalg.norm(Z - (Z @ V) @ V.T, axis=1) / np.sqrt(1024)
        evaluate(f"pca-resid k={k}   ", resid, train, test)
print("\n(d) workflow share of routine window variance (b1+b2 routine windows)")
X = np.concatenate([r['feats'] for r in rows if r['arm'] in ('clean','benign_control')])
g = np.concatenate([[r['workflow']]*len(r['ends']) for r in rows if r['arm'] in ('clean','benign_control')])
tot = X.var(0).sum(); within = sum(X[g==w].var(0).sum()*(g==w).mean() for w in set(g))
bt = np.concatenate([[r['batch']]*len(r['ends']) for r in rows if r['arm'] in ('clean','benign_control')])
within_b = sum(X[bt==w].var(0).sum()*(bt==w).mean() for w in set(bt))
print(f"   between-workflow share {1-within/tot:.3f}; between-batch share {1-within_b/tot:.3f}")
