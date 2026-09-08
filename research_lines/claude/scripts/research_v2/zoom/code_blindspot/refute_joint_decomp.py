"""Adversarial follow-ups: (a) exceedance-matched marginal vs joint, (b) A-regression
residuals, (c) decomposition of the headline 'novel combination' gap into a marginal
(familiar-opportunity) factor and a joint (re-wiring propensity) factor,
(d) independent timing recompute with hard unseen-link counts.

Diagnostic only; the 'routine trace-max q90' is a self-contained alpha=0.10 analogue,
never a detector threshold.
"""
from __future__ import annotations
import json, math, re, statistics as st, sys
from pathlib import Path
import torch

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
sys.path.insert(0, str(ROOT / "src"))
from research_v2 import io as rio  # noqa: E402
from research_v2.features import selection_rate_windows  # noqa: E402

OUT = ROOT / "artifacts/agent_v2/research_v2/zoom_code_blindspot"
LABELS = ROOT / "docs/research_v2/labels/product_onset_v1_adjudicated.jsonl"
CACHE = OUT / "refute_joint_recompute.json"
E, W, VAR_FLOOR, REGION = 64, 8, 1e-3, 48
WGM_LAYERS = tuple(range(5, 16))
PDM_LAYERS = tuple(range(5, 12))
PROG = ["b1-f2-012-order_status-python-function--attack","b1-f2-014-knowledge_qa-python-function--attack",
        "b1-f3-016-return_and_knowledge-javascript-utility--attack","b2-f2-011-knowledge_qa-sql-query--attack",
        "b2-f2-012-order_and_knowledge-sql-query--attack","b2-f2-014-support_case_status-sql-query--attack",
        "b2-f2-015-warranty_status-sql-query--attack","b2-f3-020-order_status-rust-function--attack"]

CODE_RE = re.compile(r"(```|SELECT|FROM |WHERE|JOIN|GROUP BY|ORDER BY|def |fn |function |=>|\(\)|\{|\}|;|==|->|import |const |let |return |SUM\(|COUNT\()")

def med(x): return st.median(x)

def main():
    labels = {json.loads(l)["trace_id"]: json.loads(l) for l in LABELS.read_text().splitlines() if l.strip()}
    batches = rio.load_core()
    traces = [t for k in ("b1","b2") for t in batches[k]]
    routine = [t for t in traces if rio.arm_class(t) in ("clean","benign")]
    drift = [t for t in traces if t.positive]
    groups = sorted({t.pair_group_id for t in routine})
    fold_of = {g: i % 2 for i, g in enumerate(groups)}
    folds = {f: [t for t in routine if fold_of[t.pair_group_id] == f] for f in (0,1)}

    res = {"folds": {}}
    for f in (0,1):
        fit, hold = folds[f], folds[1-f]
        # ---- reference
        blocks=[]
        for t in fit:
            _, w = selection_rate_windows(t.top_k_ids, W, WGM_LAYERS)
            if w.shape[0]: blocks.append(w)
        m = torch.cat(blocks); mu = m.mean(0); sd = m.std(0)+VAR_FLOOR; centre=((m-mu)/sd).mean(0)
        pair = torch.zeros((15,E,E), dtype=torch.float64); n=0
        for t in fit:
            top1=t.top_k_ids.long()[:,:,0]; n+=top1.shape[1]
            for l in range(15):
                pair[l]+=torch.bincount(top1[l]*E+top1[l+1], minlength=E*E).reshape(E,E)
        unseen = pair == 0

        def g1_of(t):
            ends,w = selection_rate_windows(t.top_k_ids, W, WGM_LAYERS)
            z=(w-mu)/sd-centre
            return ends, (z**2).sum(1)
        def links(t):  # per-token unseen link count over layers 5..11
            top1=t.top_k_ids.long()[:,:,0]
            T=top1.shape[1]
            x=torch.zeros(T, dtype=torch.float64)
            for l in PDM_LAYERS[:-1]:
                x += unseen[l][top1[l], top1[l+1]].to(torch.float64)
            return x
        def w4max(x, lo, hi):
            if x.numel()<4: return None, None
            c=torch.cat((torch.zeros(1,dtype=torch.float64), x.cumsum(0)))
            mean4=(c[4:]-c[:-4])/4.0   # window ending at index i+3
            ends=torch.arange(3, x.numel())
            keep=[i for i,e in enumerate(ends.tolist()) if lo<=e<hi]
            if not keep: return None, None
            return mean4[keep], [ends[i].item() for i in keep]

        # routine held-out distributions
        rw_g1 = torch.cat([g1_of(t)[1] for t in hold])
        rl = torch.cat([links(t) for t in hold])
        r_w4 = []
        for t in hold:
            v,_ = w4max(links(t), 0, t.token_count)
            if v is not None: r_w4.append(v)
        r_w4_all = torch.cat(r_w4)
        mu4, sd4 = float(r_w4_all.mean()), float(r_w4_all.std())
        trace_max_z = sorted((float(v.max())-mu4)/sd4 for v in r_w4)
        q90 = trace_max_z[int(0.90*(len(trace_max_z)-1))]
        # base-rate matched marginal exceedance: routine pair-unseen rate on held-out
        pu=[]
        for t in hold:
            top1=t.top_k_ids.long()[:,:,0]; tot=0; u=0
            for l in range(15):
                u+=int(unseen[l][top1[l],top1[l+1]].sum()); tot+=top1.shape[1]
            pu.append(u/tot)
        base = st.mean(pu)
        thr_g1 = float(torch.quantile(rw_g1.double(), 1-base))
        blk={"base_pair_unseen":base,"g1_threshold":thr_g1,"q90_trace_max_z":q90,
             "mu4":mu4,"sd4":sd4,"routine":{},"drift":{}}
        # routine exceedance rate (sanity: should be ~base)
        blk["routine_g1_exceed"]=float((rw_g1>thr_g1).double().mean())
        for t in drift:
            lab=labels[t.trace_id]; lo=max(0,int(lab["product_onset"])); hi=min(lo+REGION,t.token_count)
            ends,g=g1_of(t)
            keep=[i for i,e in enumerate(ends.tolist()) if (e-W+1)>=lo and e<hi]
            exc=float((g[keep]>thr_g1).double().mean()) if keep else None
            x=links(t)
            v,e4=w4max(x, lo, hi)
            z=((v-mu4)/sd4) if v is not None else None
            first_cross=None
            if z is not None:
                for zz,ee in zip(z.tolist(), e4):
                    if zz>=q90: first_cross=ee-lo; break
            v16,e16=w4max(x, lo, min(lo+16,hi))
            z16=float(((v16-mu4)/sd4).max()) if v16 is not None else None
            blk["drift"][t.trace_id]={"domain":t.domain,"g1_exceed":exc,
                "w4max_z_48":float(z.max()) if z is not None else None,
                "w4max_z_16":z16,"first_cross_offset":first_cross,
                "links_per_token":float(x[lo:hi].mean()),"lo":lo}
        res["folds"][str(f)]=blk
        print(f"fold {f}: base={base:.5f} g1_thr={thr_g1:.1f} routine_g1_exceed={blk['routine_g1_exceed']:.5f} q90={q90:.3f}", flush=True)
    (OUT/"refute_joint_decomp.json").write_text(json.dumps(res), encoding="utf-8")

    F=res["folds"]
    def fa(tid,k):
        vs=[F[f]["drift"][tid][k] for f in ("0","1") if F[f]["drift"][tid][k] is not None]
        return st.mean(vs) if vs else None
    others=[t.trace_id for t in drift if t.domain!="programming"]
    print("\n## Base-rate-matched marginal exceedance vs joint pair-unseen")
    base=st.mean([F[f]["base_pair_unseen"] for f in ("0","1")])
    ce=[fa(t,"g1_exceed") for t in PROG]; oe=[fa(t,"g1_exceed") for t in others]
    print(f"routine base rate (both statistics) = {base:.4f}")
    print("code g1-exceedance: " + ", ".join(f"{v:.3f}" for v in ce) + f" | median {med(ce):.4f} = {med(ce)/base:.2f}x")
    print(f"other-drift median g1-exceedance {med(oe):.4f} = {med(oe)/base:.2f}x")

    print("\n## Timing recompute (hard unseen-link count, layers 5-11, w=4, routine trace-max q90)")
    q90=st.mean([F[f]["q90_trace_max_z"] for f in ("0","1")])
    print(f"routine trace-max q90 (fold-avg) = {q90:.3f}")
    print("| trace | w4max z (48) | w4max z (16) | cross<=48 | cross<=16 | first cross offset f0/f1 |")
    print("|---"*6+"|")
    c48=c16=0
    for t in PROG:
        z48=fa(t,"w4max_z_48"); z16=fa(t,"w4max_z_16")
        fc=[F[f]["drift"][t]["first_cross_offset"] for f in ("0","1")]
        a=z48>=q90; b=z16>=q90; c48+=a; c16+=b
        print(f"| {t.split('--')[0][:34]} | {z48:.2f} | {z16:.2f} | {'Y' if a else 'N'} | {'Y' if b else 'N'} | {fc[0]}/{fc[1]} |")
    print(f"code crossings: {c48}/8 over 48 tokens, {c16}/8 within +16")
    o48=sum(1 for t in others if fa(t,"w4max_z_48")>=q90); o16=sum(1 for t in others if fa(t,"w4max_z_16")>=q90)
    print(f"other-drift crossings: {o48}/51 over 48, {o16}/51 within +16")

    print("\n## First literal-code token offset inside the product region (my own regex rule)")
    for t in drift:
        if t.domain!="programming": continue
        lab=labels[t.trace_id]; lo=max(0,int(lab["product_onset"])); hi=min(lo+REGION+80,t.token_count)
        txt=rio.decode_token_texts(t.token_ids[lo:hi])
        off=None
        for i,s in enumerate(txt):
            if CODE_RE.search(s): off=i; break
        print(f"  {t.trace_id.split('--')[0][:40]:42s} po={lo:4d} first-code-offset={off} product_class={lab.get('product_class')}")

if __name__=="__main__":
    main()
