"""Adversarial tables for the joint-vs-marginal lens (reads only my own cache)."""
from __future__ import annotations
import json, math, statistics as st
from pathlib import Path

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
CACHE = ROOT / "artifacts/agent_v2/research_v2/zoom_code_blindspot/refute_joint_recompute.json"
R = json.loads(CACHE.read_text())
PROG = [
 "b1-f2-012-order_status-python-function--attack",
 "b1-f2-014-knowledge_qa-python-function--attack",
 "b1-f3-016-return_and_knowledge-javascript-utility--attack",
 "b2-f2-011-knowledge_qa-sql-query--attack",
 "b2-f2-012-order_and_knowledge-sql-query--attack",
 "b2-f2-014-support_case_status-sql-query--attack",
 "b2-f2-015-warranty_status-sql-query--attack",
 "b2-f3-020-order_status-rust-function--attack",
]
SHORT = [t.split("--")[0][:13] for t in PROG]

def fold_avg(split, anchor="product_onset"):
    """returns (routine_ref dict, drift dict trace-> fold-averaged stats)"""
    blocks = R["splits"][split]
    folds = list(blocks)
    keys = None
    routine = {}
    for k in ("pair_unseen_rate","novel_comb_rate","novel_comb_cond","ff_frac",
              "indep_expected_rate","joint_excess","marg_unseen_rate","marg_rare_rate",
              "marg_matched_rate","marg_surprisal","wgm_g1_median"):
        vals=[]
        for f in folds:
            rv=[v[k] for v in blocks[f]["routine"].values() if v.get(k) is not None]
            vals.append(st.mean(rv) if k!="wgm_g1_median" else st.median(rv))
        routine[k]=st.mean(vals)
    # per-trace fold averaged
    drift={}
    ids=set()
    for f in folds:
        for key in blocks[f]["drift"]:
            if key.endswith("|"+anchor): ids.add(key)
    for key in sorted(ids):
        rec={}
        present=[f for f in folds if key in blocks[f]["drift"]]
        for k in ("pair_unseen_rate","novel_comb_rate","novel_comb_cond","ff_frac",
                  "indep_expected_rate","joint_excess","marg_unseen_rate","marg_rare_rate",
                  "marg_matched_rate","marg_surprisal","wgm_g1_median","n_tokens"):
            vs=[blocks[f]["drift"][key][k] for f in present if blocks[f]["drift"][key].get(k) is not None]
            rec[k]=st.mean(vs) if vs else None
        rec["domain"]=blocks[present[0]]["drift"][key]["domain"]
        rec["batch"]=blocks[present[0]]["drift"][key]["batch"]
        rec["lo"]=blocks[present[0]]["drift"][key]["lo"]
        drift[key]=rec
    return routine, drift

def med(x): return st.median(x)

def report(split, anchor="product_onset"):
    routine, drift = fold_avg(split, anchor)
    code=[f"{t}|{anchor}" for t in PROG if f"{t}|{anchor}" in drift]
    other=[k for k in drift if k not in code]
    print(f"\n=== split={split} anchor={anchor}  (code n={len(code)}, other n={len(other)}) ===")
    print("routine held-out reference:", {k:round(v,5) for k,v in routine.items()})
    rows=[("wgm_g1_median","M1 CAND-A g1 window median"),
          ("marg_unseen_rate","M2 per-layer top-1 marginal unseen"),
          ("marg_rare_rate","M2 marginal rare (<1/64)"),
          ("marg_matched_rate","M2 marginal rare, BASE-RATE MATCHED"),
          ("marg_surprisal","M2 mean marginal surprisal (nats)"),
          ("pair_unseen_rate","J1 adjacent top-1 pair unseen"),
          ("ff_frac","-- fraction of pairs with both ends familiar"),
          ("novel_comb_rate","J2 novel combination of familiar experts (uncond)"),
          ("novel_comb_cond","J2 SAME, CONDITIONAL on both ends familiar"),
          ("indep_expected_rate","J3 pair-unseen expected from region marginals"),
          ("joint_excess","J3 observed / independence-expected")]
    print("\n| quantity | routine | " + " | ".join(SHORT) + " | code med | code ratio | other med ratio |")
    print("|---"*(len(SHORT)+5)+"|")
    for k,label in rows:
        rv=routine[k]
        cv=[drift[c][k] for c in code]
        ov=[drift[o][k] for o in other]
        cr=[v/rv for v in cv]; orr=[v/rv for v in ov]
        print(f"| {label} | {rv:.4f} | " + " | ".join(f"{v:.3f}" for v in cv) +
              f" | {med(cv):.4f} | {med(cr):.2f}x | {med(orr):.2f}x |")
    # amplification A
    A_code=[(drift[c]["pair_unseen_rate"]/routine["pair_unseen_rate"])/(drift[c]["wgm_g1_median"]/routine["wgm_g1_median"]) for c in code]
    A_other=[(drift[o]["pair_unseen_rate"]/routine["pair_unseen_rate"])/(drift[o]["wgm_g1_median"]/routine["wgm_g1_median"]) for o in other]
    print(f"\nA = joint/marginal : code " + ", ".join(f"{a:.2f}" for a in A_code) +
          f" | code med {med(A_code):.2f} | other med {med(A_other):.2f} (range {min(A_other):.2f}-{max(A_other):.2f})")
    n_ge=sum(1 for a in A_other if a>=min(A_code))
    print(f"other drifts with A >= code min ({min(A_code):.2f}): {n_ge}/{len(A_other)}")
    # correlation of log A with log marginal ratio across ALL 59 drifts
    allk=code+other
    lm=[math.log(drift[k]["wgm_g1_median"]/routine["wgm_g1_median"]) for k in allk]
    lj=[math.log(drift[k]["pair_unseen_rate"]/routine["pair_unseen_rate"]) for k in allk]
    la=[j-m for m,j in zip(lm,lj)]
    def corr(x,y):
        mx,my=st.mean(x),st.mean(y)
        sx=math.sqrt(sum((a-mx)**2 for a in x)); sy=math.sqrt(sum((b-my)**2 for b in y))
        return sum((a-mx)*(b-my) for a,b in zip(x,y))/(sx*sy)
    b=sum((a-st.mean(lm))*(c-st.mean(lj)) for a,c in zip(lm,lj))/sum((a-st.mean(lm))**2 for a in lm)
    print(f"across all 59 drifts: corr(log marginal, log joint) = {corr(lm,lj):.3f}; OLS slope log joint ~ log marginal = {b:.3f}")
    print(f"corr(log A, log marginal ratio) = {corr(la,lm):.3f}   [A is mechanically 1/marginal if ~ -1]")
    # per-domain medians of key stats
    doms={}
    for o in other: doms.setdefault(drift[o]["domain"],[]).append(o)
    print("\n| domain | n | novel_comb ratio | novel_comb_cond ratio | joint_excess | pair_unseen ratio | marginal(g1) ratio | A |")
    print("|---"*8+"|")
    def dm(keys,k): return med([drift[x][k] for x in keys])
    print(f"| programming | {len(code)} | {dm(code,'novel_comb_rate')/routine['novel_comb_rate']:.2f} | {dm(code,'novel_comb_cond')/routine['novel_comb_cond']:.2f} | {dm(code,'joint_excess'):.2f} | {dm(code,'pair_unseen_rate')/routine['pair_unseen_rate']:.2f} | {dm(code,'wgm_g1_median')/routine['wgm_g1_median']:.2f} | {med(A_code):.2f} |")
    for d,keys in sorted(doms.items()):
        Ad=[(drift[x]["pair_unseen_rate"]/routine["pair_unseen_rate"])/(drift[x]["wgm_g1_median"]/routine["wgm_g1_median"]) for x in keys]
        print(f"| {d} | {len(keys)} | {dm(keys,'novel_comb_rate')/routine['novel_comb_rate']:.2f} | {dm(keys,'novel_comb_cond')/routine['novel_comb_cond']:.2f} | {dm(keys,'joint_excess'):.2f} | {dm(keys,'pair_unseen_rate')/routine['pair_unseen_rate']:.2f} | {dm(keys,'wgm_g1_median')/routine['wgm_g1_median']:.2f} | {med(Ad):.2f} |")
    # overlap / fragility on the headline statistic
    for k in ("novel_comb_rate","novel_comb_cond","joint_excess"):
        cv=[drift[c][k] for c in code]; ov=[drift[o][k] for o in other]
        print(f"\n{k}: code per-trace " + ", ".join(f"{v:.4f}" for v in cv))
        print(f"  code min {min(cv):.4f} med {med(cv):.4f}; other >= code min: {sum(1 for v in ov if v>=min(cv))}/{len(ov)}; other >= code med: {sum(1 for v in ov if v>=med(cv))}/{len(ov)}; other max {max(ov):.4f}")
        print(f"  routine {routine[k]:.4f}; code traces above routine: {sum(1 for v in cv if v>routine[k])}/{len(cv)}")
    return routine, drift, code, other

def class_control():
    print("\n=== token-class control (pairgroup split, product_onset) ===")
    blocks=R["splits"]["pairgroup"]
    cls_rates={}
    for f in blocks:
        for c,v in blocks[f]["routine_by_class"].items():
            cls_rates.setdefault(c,[]).append(v)
    print("routine per-class rates (fold-avg):")
    for c,vs in sorted(cls_rates.items()):
        n=sum(v["n"] for v in vs)
        print(f"  {c:6s} n={n/15:9.0f} tok  pair_unseen={st.mean([v['pair_unseen_rate'] for v in vs]):.4f}  novel_comb={st.mean([v['novel_comb_rate'] for v in vs]):.5f} ff_frac={st.mean([v['ff_frac'] for v in vs]):.3f}")
    routine,drift=fold_avg("pairgroup")
    rc=R["region_classes"]
    print("\n| trace | class mix (word/punct/space/digit) | plain ratio pair_unseen | class-matched ratio | plain ratio novel_comb | class-matched ratio |")
    print("|---"*6+"|")
    out={}
    for grp,keys in (("code",[f"{t}|product_onset" for t in PROG]),
                     ("other",[k for k in drift if k.split("|")[0]+"--attack" not in [p for p in PROG]])):
        pass
    code=[f"{t}|product_onset" for t in PROG]
    other=[k for k in drift if k not in code]
    def matched_ratio(key,stat):
        mix=rc[key]["counts"]; tot=sum(mix.values())
        base=0.0
        for c,n in mix.items():
            r=st.mean([blocks[f]["routine_by_class"][c][stat] for f in blocks])
            base+=n/tot*r
        return drift[key][stat]/base, base
    for key in code:
        mix=rc[key]["counts"]; tot=sum(mix.values())
        mixs="/".join(f"{mix.get(c,0)/tot:.2f}" for c in ("word","punct","space","digit"))
        r1,_=matched_ratio(key,"pair_unseen_rate"); r2,_=matched_ratio(key,"novel_comb_rate")
        print(f"| {key.split('--')[0][:34]} | {mixs} | {drift[key]['pair_unseen_rate']/routine['pair_unseen_rate']:.2f}x | {r1:.2f}x | {drift[key]['novel_comb_rate']/routine['novel_comb_rate']:.2f}x | {r2:.2f}x |")
    cm1=med([matched_ratio(k,"pair_unseen_rate")[0] for k in code]); cm2=med([matched_ratio(k,"novel_comb_rate")[0] for k in code])
    om1=med([matched_ratio(k,"pair_unseen_rate")[0] for k in other]); om2=med([matched_ratio(k,"novel_comb_rate")[0] for k in other])
    print(f"| CODE MEDIAN | | {med([drift[k]['pair_unseen_rate'] for k in code])/routine['pair_unseen_rate']:.2f}x | {cm1:.2f}x | {med([drift[k]['novel_comb_rate'] for k in code])/routine['novel_comb_rate']:.2f}x | {cm2:.2f}x |")
    print(f"| OTHER MEDIAN | | {med([drift[k]['pair_unseen_rate'] for k in other])/routine['pair_unseen_rate']:.2f}x | {om1:.2f}x | {med([drift[k]['novel_comb_rate'] for k in other])/routine['novel_comb_rate']:.2f}x | {om2:.2f}x |")
    # routine class mix
    tot=sum(st.mean([blocks[f]["routine_by_class"][c]["n"] for f in blocks]) for c in cls_rates)
    print("routine class mix: " + ", ".join(f"{c}={st.mean([blocks[f]['routine_by_class'][c]['n'] for f in blocks])/tot:.3f}" for c in sorted(cls_rates)))

if __name__=="__main__":
    report("pairgroup","product_onset")
    report("pairgroup","evidence_onset")
    report("batch","product_onset")
    class_control()
