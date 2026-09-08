import json, sys
from collections import Counter, defaultdict
sys.path.insert(0,"/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363/scripts/research_v3")
from audit_recompute_lib import *
RUNS={"b2":"final_b2_both","b1":"final_b1_both","h384":"final_h384_both","c1_heldout":"final_c1_heldout_C1"}
META=load_meta(); CORE,_,_=core_anchors(); H384=h384_labels()
RESULT={t:json.load(open(ART/r/"result.json")) for t,r in RUNS.items()}
CACHE={t:load_cell(r) for t,r in RUNS.items()}

print("# K. C1 fold discipline")
for t in ("b2","b1","h384","c1_heldout"):
    s = ("C1","c1_heldout") if ("C1","c1_heldout") in CACHE[t] else ("C1","target")
    if t=="c1_heldout": s=("C1","target")
    tr=CACHE[t][s]
    folds=Counter(META["c1"][b["trace_id"]]["preregistered_fold"] for b in tr.values() if b["batch"]=="c1")
    roles=Counter(META["c1"][b["trace_id"]].get("fold_role") for b in tr.values() if b["batch"]=="c1")
    print(f"  {t} {s}: c1 traces={sum(folds.values())} folds={dict(folds)} roles={dict(roles)}")
allfolds=Counter(r["preregistered_fold"] for r in META["c1"].values())
print("  c1 index folds overall:",dict(allfolds),"total",sum(allfolds.values()))
print()

print("# L. h384/C1 positives whose anchor lies past the calibration horizon (end 191)")
tr=CACHE["h384"][("C1","target")]
an={k:( (H384.get(b['trace_id']) or {}).get('engagement_onset') if (H384.get(b['trace_id']) or {}).get('engagement_class') in ('cross_domain_execution','bounded_engagement_resisted') else None) for k,b in tr.items()}
pos=[k for k,v in an.items() if v is not None]
bad=[k for k in pos if max(tr[k]["ends"][i] for i in range(len(tr[k]["ends"])) if not tr[k]["hc"][i]) < an[k]]
ht={k:anchor_hit(alarm_ends(tr[k],trm3_rule(tr[k])),an[k]) for k in pos}
hm={k:anchor_hit(alarm_ends(tr[k],m_rule(tr[k])),an[k]) for k in pos}
hmM={k:anchor_hit(alarm_ends(tr[k],m_rule(tr[k],7/91.0)),an[k]) for k in pos}
print(f"  positives={len(pos)}  structurally unreachable={len(bad)}")
for k in bad:
    print(f"    {tr[k]['trace_id']}: anchor={an[k]} last scored end=191  trm3_hit8={ht[k]['hit8']} bm_hit8={hm[k]['hit8']} bm_matched_hit8={hmM[k]['hit8']}  (concordant 'neither')")
r8t=sum(v['hit8'] for v in ht.values()); r8m=sum(v['hit8'] for v in hm.values())
print(f"  recall+8 as reported: TRM-3 {r8t}/45={r8t/45:.4f}  B-M(nominal) {r8m}/45={r8m/45:.4f}")
keep=[k for k in pos if k not in bad]
print(f"  recall+8 over reachable positives only: TRM-3 {sum(ht[k]['hit8'] for k in keep)}/{len(keep)}={sum(ht[k]['hit8'] for k in keep)/len(keep):.4f}"
      f"  B-M {sum(hm[k]['hit8'] for k in keep)}/{len(keep)}={sum(hm[k]['hit8'] for k in keep)/len(keep):.4f}")
mc=mcnemar({k:ht[k]['hit8'] for k in keep},{k:hmM[k]['hit8'] for k in keep})
print(f"  McNemar (matched alpha) restricted to reachable positives: pair={mc['pair_count']} both={mc['both']} neither={mc['neither']} only_trm3={mc['only_a']} only_BM={mc['only_b']} net={mc['net']:+d} p={mc['p']:.6g}  -> still FAIL")
print()

print("# M. F9 spot-check: clean-trace CONFIRMED endpoints by attribution channel")
for t,c in (("b2","D"),("b1","D")):
    tra=CACHE[t][(c,"target")]
    cnt=Counter()
    for k,b in tra.items():
        if b["class"]!="clean": continue
        for i in range(len(b["ends"])):
            if b["hc"][i] or b["pf"][i]>0.10: continue
            vals={ch:b[key][i] for ch,key in (("S","pS"),("M","pM"),("J","pJ")) if b[key][i] is not None}
            cnt[min(("S","M","J"),key=lambda ch: vals[ch]/WEIGHTS[ch])]+=1
    print(f"  {t}/{c} clean CONFIRMED endpoints by attribution: {dict(cnt)}   (report F9 claims b2/D J=184 M=3; b1/D J=58 M=25)")
print()

print("# N. F10 spot-check: b2/D b2-f3-042-order_status-character-monologue--attack")
tra=CACHE["b2"][("D","target")]
k=[kk for kk,b in tra.items() if b["trace_id"].startswith("b2-f3-042-order_status-character-monologue")][0]
b=tra[k]
print("  anchor =",CORE.get(b["trace_id"]))
for name,rule in (("A-S",s_rule),("B-M",m_rule),("TRM-3",trm3_rule)):
    ae=alarm_ends(b,rule(b))
    print(f"    {name}: first alarm end = {ae[0] if ae else None}")
