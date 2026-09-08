import json,sys
from collections import Counter
sys.path.insert(0,"/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363/scripts/research_v3")
from audit_recompute_lib import *
CORE,_,_=core_anchors()
for t,run in (("b2","final_b2_both"),("b1","final_b1_both")):
    tr=load_cell(run)[("D","target")]
    an={k:CORE.get(b["trace_id"]) for k,b in tr.items()}
    drift=[k for k,b in tr.items() if b["class"]=="drift" and an.get(k) is not None]
    res=[k for k,b in tr.items() if b["class"]=="resist" and an.get(k) is not None]
    for name,rule in (("TRM-3",trm3_rule),("B-M",m_rule)):
        hd=sum(anchor_hit(alarm_ends(tr[k],rule(tr[k])),an[k])["hit8"] for k in drift)
        hr=sum(anchor_hit(alarm_ends(tr[k],rule(tr[k])),an[k])["hit8"] for k in res)
        print(f"  {t}/D {name}: drift-only +8 = {hd}/{len(drift)} = {hd/len(drift):.4f}; anchored-resist +8 = {hr}/{len(res)}")
# code/SQL domain
print()
print("# code/sql domain +8 recall (report 9.3 claim: 0/34 across six cells)")
H=h384_labels(); META=load_meta()
for t,run in (("b2","final_b2_both"),("b1","final_b1_both"),("h384","final_h384_both")):
    for col in ("D","C1"):
        tr=load_cell(run)[(col,"target")]
        if t=="h384":
            an={k:((H.get(b['trace_id']) or {}).get('engagement_onset') if (H.get(b['trace_id']) or {}).get('engagement_class') in ('cross_domain_execution','bounded_engagement_resisted') else None) for k,b in tr.items()}
        else:
            an={k:CORE.get(b["trace_id"]) for k,b in tr.items()}
        pos=[k for k,b in tr.items() if an.get(k) is not None and (b["class"] in ("drift","resist") or (t=="h384" and (H.get(b['trace_id']) or {}).get('engagement_class') in ('cross_domain_execution','bounded_engagement_resisted')))]
        dom=Counter(META[tr[k]["batch"]][tr[k]["trace_id"]]["target_domain"] for k in pos)
        code=[k for k in pos if META[tr[k]["batch"]][tr[k]["trace_id"]]["target_domain"] in ("code","sql","programming","software")]
        hits=sum(anchor_hit(alarm_ends(tr[k],trm3_rule(tr[k])),an[k])["hit8"] for k in code)
        print(f"  {t}/{col}: target_domain values={sorted(dom)} ; code/sql positives={len(code)} TRM-3 +8 hits={hits}")
