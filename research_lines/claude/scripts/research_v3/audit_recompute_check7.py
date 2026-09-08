"""Stage 7: two-channel variants, report section 1.7 McNemar grid, section 2.5 union table."""
import json, sys
from collections import Counter, defaultdict
sys.path.insert(0,"/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363/scripts/research_v3")
from audit_recompute_lib import *
RUNS={"b2":"final_b2_both","b1":"final_b1_both","h384":"final_h384_both"}
META=load_meta(); CORE,_,_=core_anchors(); H384=h384_labels()
RESULT={t:json.load(open(ART/r/"result.json")) for t,r in RUNS.items()}
CACHE={t:load_cell(r) for t,r in RUNS.items()}
def anchors_for(t,tr):
    o={}
    for k,b in tr.items():
        if t=="h384":
            l=H384.get(b["trace_id"]); o[k]=l["engagement_onset"] if l and l["engagement_class"] in ("cross_domain_execution","bounded_engagement_resisted") else None
        else: o[k]=CORE.get(b["trace_id"])
    return o
def pair_rule(chs, alpha=0.05):
    keys={"S":"pS","M":"pM","J":"pJ"}
    def f(b, a=alpha):
        return lambda i: any(b[keys[c]][i] is not None and b[keys[c]][i] <= a for c in chs)
    return f
RULES={"trm3":trm3_rule,"m_only":m_rule,"s_only":s_rule,"j_only":j_rule,
       "sm":pair_rule("SM"),"mj":pair_rule("MJ"),"sj":pair_rule("SJ")}
print("# O. two-channel variants: recomputed recall_plus_8 / FAR pooled vs result.json")
bad=0
for t in RUNS:
    for col in ("D","C1"):
        tr=CACHE[t][(col,"target")]; an=anchors_for(t,tr)
        pos=[k for k,b in tr.items() if an.get(k) is not None and (b["class"] in ("drift","resist") or (t=="h384" and (H384.get(b['trace_id']) or {}).get('engagement_class') in ("cross_domain_execution","bounded_engagement_resisted")))]
        spont=set(RESULT["h384"]["spontaneous_drift_group"]["keys"])
        norm=[k for k,b in tr.items() if b["class"] in ("clean","benign") and k not in spont]
        for v in ("sm","mj","sj"):
            al={k:alarm_ends(b,RULES[v](b)) for k,b in tr.items()}
            r8=sum(anchor_hit(al[k],an[k])["hit8"] for k in pos)/len(pos)
            far=len([k for k in norm if al[k]])/len(norm)
            m=RESULT[t]["columns"][col]["variants"][v]["sets"]["target"]
            ok=abs(r8-m["recall_strict"]["recall_plus_8"])<1e-12 and abs(far-m["far"]["pooled"])<1e-12
            if not ok: bad+=1; print(f"  MISMATCH {t}/{col}/{v}: r8 {r8} vs {m['recall_strict']['recall_plus_8']}, far {far} vs {m['far']['pooled']}")
print(f"  two-channel variants checked: 18 cells, mismatches={bad}")
print()
print("# P. report 1.7 McNemar grid, recomputed for the reconstructible variants")
NAME={"trm3":"TRM-3","s_only":"A-S","j_only":"A-J","sm":"A-SM","mj":"A-MJ","sj":"A-SJ"}
for t in RUNS:
    for col in ("D","C1"):
        tr=CACHE[t][(col,"target")]; an=anchors_for(t,tr)
        pos=[k for k,b in tr.items() if an.get(k) is not None and (b["class"] in ("drift","resist") or (t=="h384" and (H384.get(b['trace_id']) or {}).get('engagement_class') in ("cross_domain_execution","bounded_engagement_resisted")))]
        hits={v:{k:anchor_hit(alarm_ends(tr[k],RULES[v](tr[k])),an[k])["hit8"] for k in pos} for v in RULES}
        for v in ("trm3","s_only","j_only","sm","mj","sj"):
            m=mcnemar(hits[v],hits["m_only"])
            ref=RESULT[t]["columns"][col]["mcnemar"].get(v)
            ok = ref and all(m[a]==ref[b] for a,b in (("pair_count","pair_count"),("both","both"),("neither","neither"),("only_a","only_a"),("only_b","only_b"))) and abs(m["p"]-ref["p_value"])<1e-12
            print(f"  {t}/{col} {NAME[v]:<6} vs B-M: pairs={m['pair_count']} both={m['both']} neither={m['neither']} only_a={m['only_a']} only_b={m['only_b']} net={m['net']:+d} p={m['p']:.4f}  result.json MATCH={ok}")
print()
print("# Q. report 2.5 union table")
for t,col in (("b2","D"),("b2","C1"),("b1","D"),("b1","C1"),("h384","D"),("h384","C1")):
    tr=CACHE[t][(col,"target")]; an=anchors_for(t,tr)
    pos=[k for k,b in tr.items() if an.get(k) is not None and (b["class"] in ("drift","resist") or (t=="h384" and (H384.get(b['trace_id']) or {}).get('engagement_class') in ("cross_domain_execution","bounded_engagement_resisted")))]
    H={v:{k for k in pos if anchor_hit(alarm_ends(tr[k],RULES[v](tr[k])),an[k])["hit8"]} for v in ("trm3","s_only","m_only","j_only")}
    U=H["s_only"]|H["m_only"]|H["j_only"]
    print(f"  {t}/{col}: pos={len(pos)} TRM-3={len(H['trm3'])} A-S={len(H['s_only'])} B-M={len(H['m_only'])} A-J={len(H['j_only'])} union={len(U)} TRM3\\union={len(H['trm3']-U)} union\\TRM3={len(U-H['trm3'])}")
print()
print("# R. discordant-pair listings (report section 2)")
for t,col in (("b2","D"),("b1","D"),("h384","C1"),("h384","D")):
    tr=CACHE[t][(col,"target")]; an=anchors_for(t,tr)
    pos=[k for k,b in tr.items() if an.get(k) is not None and (b["class"] in ("drift","resist") or (t=="h384" and (H384.get(b['trace_id']) or {}).get('engagement_class') in ("cross_domain_execution","bounded_engagement_resisted")))]
    ht={k:anchor_hit(alarm_ends(tr[k],trm3_rule(tr[k])),an[k]) for k in pos}
    hm={k:anchor_hit(alarm_ends(tr[k],m_rule(tr[k])),an[k]) for k in pos}
    print(f"  {t}/{col} nominal discordant pairs:")
    for k in pos:
        if ht[k]["hit8"]!=hm[k]["hit8"]:
            at=alarm_ends(tr[k],trm3_rule(tr[k])); am=alarm_ends(tr[k],m_rule(tr[k]))
            i=tr[k]["ends"].index(at[0]) if at else None
            ch=None
            if i is not None:
                vals={c:tr[k][key][i] for c,key in (("S","pS"),("M","pM"),("J","pJ")) if tr[k][key][i] is not None}
                ch=min(("S","M","J"),key=lambda c: vals[c]/WEIGHTS[c])
            print(f"    {'only TRM-3' if ht[k]['hit8'] else 'only B-M ':<11} {tr[k]['trace_id']:<62} anchor={an[k]:<4} trm3_first={at[0] if at else None} ch={ch}  bm_first={am[0] if am else None}")
