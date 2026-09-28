"""Stage 5: censoring boundary, reachability, PROVISIONAL discipline, label interlocks."""
import json, sys
from collections import Counter, defaultdict
sys.path.insert(0,"/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363/scripts/research_v3")
from audit_recompute_lib import *
RUNS={"b2":"final_b2_both","b1":"final_b1_both","h384":"final_h384_both","c1_heldout":"final_c1_heldout_C1"}
META=load_meta(); CORE,PROD,TOPIC=core_anchors(); H384=h384_labels()
RESULT={t:json.load(open(ART/r/"result.json")) for t,r in RUNS.items()}
SPONT=set(RESULT["h384"]["spontaneous_drift_group"]["keys"])
CACHE={t:load_cell(r) for t,r in RUNS.items()}
def anchors_for(t,tr):
    o={}
    for k,b in tr.items():
        if t=="h384":
            l=H384.get(b["trace_id"]); o[k]=l["engagement_onset"] if l and l["engagement_class"] in ("cross_domain_execution","bounded_engagement_resisted") else None
        else: o[k]=CORE.get(b["trace_id"])
    return o

print("# G. label-file interlocks")
print("  product_onset rows:",len(PROD),"non-null product_onset:",sum(1 for r in PROD if r.get('product_onset') is not None),
      "unique trace_ids:",len({r['trace_id'] for r in PROD}))
print("  topic_entry rows:",len(TOPIC),"non-null topic_entry_onset:",sum(1 for r in TOPIC if r.get('topic_entry_onset') is not None),
      "unique:",len({r['trace_id'] for r in TOPIC}),"overlap with product:",len({r['trace_id'] for r in PROD}&{r['trace_id'] for r in TOPIC}))
print("  h384 engagement classes:",dict(Counter(v['engagement_class'] for v in H384.values())))
print("  h384 frozen-span vs index-class disagreements:",[t for t,v in H384.items() if v['frozen_class'] and v['frozen_class']!=v['engagement_class']])
print("  h384 execution_onset == frozen span end (executions):",
      sum(1 for v in H384.values() if v['engagement_class']=='cross_domain_execution' and v['span'] and v['execution_onset']==v['span'][1]),"/40",
      " median(exec-engage) =", statistics.median([v['execution_onset']-v['span'][0] for v in H384.values() if v['engagement_class']=='cross_domain_execution' and v['span']]))
print()

print("# H. horizon censoring boundary (h384/C1)")
tr=CACHE["h384"][("C1","target")]
lasts=set(); firstcens=set()
for k,b in tr.items():
    if any(b["hc"]):
        i=b["hc"].index(True); lasts.add(b["ends"][i-1]); firstcens.add(b["ends"][i])
print("  last in-horizon end over the 62 censored traces:",sorted(lasts),"  first censored end:",sorted(firstcens))
print("  K_cal = {S:185, M:185, J:189}; S/M 185th endpoint end = 7+184 =",7+184,"; J 189th = 3+188 =",3+188)
print("  in-horizon endpoint counts of censored traces:",sorted({sum(1 for x in b['hc'] if not x) for k,b in tr.items() if any(b['hc'])}))
print("  uncensored cells (all other target/column combos):",
      {f"{t}/{c}": sum(1 for b in v.values() if any(b['hc'])) for t in RUNS for (c,s),v in CACHE[t].items() if s=='target'})
print()

print("# I. PROVISIONAL is never an alarm")
for (t,c) in (("b2","D"),("h384","C1")):
    tra=CACHE[t][(c,"target")]
    norm=[k for k,b in tra.items() if b["class"] in ("clean","benign") and k not in SPONT]
    conf=sum(1 for k in norm if alarm_ends(tra[k],trm3_rule(tra[k])))
    prov=sum(1 for k in norm if any((not tra[k]["hc"][i]) and tra[k]["pf"][i]<=0.25 for i in range(len(tra[k]["ends"]))))
    print(f"  {t}/{c}: normals={len(norm)} FAR with CONFIRMED-only = {conf/len(norm):.4f}; if PROVISIONAL counted it would be {prov/len(norm):.4f}"
          f"  -> result.json FAR pooled = {RESULT[t]['columns'][c]['variants']['trm3']['sets']['target']['far']['pooled']:.4f}")
print()

print("# J. reachability of the +8 horizon for the positives")
for t,c in (("b2","D"),("b1","D"),("h384","D"),("h384","C1")):
    tra=CACHE[t][(c,"target")]; an=anchors_for(t,tra)
    pos=[k for k,b in tra.items() if an.get(k) is not None and (b["class"] in ("drift","resist") or (t=="h384" and (H384.get(b['trace_id']) or {}).get('engagement_class') in ("cross_domain_execution","bounded_engagement_resisted")))]
    unreach8=[]; unreach_anchor=[]
    for k in pos:
        b=tra[k]; inh=[b["ends"][i] for i in range(len(b["ends"])) if not b["hc"][i]]
        last=inh[-1] if inh else None
        if last is None or last < an[k]+8: unreach8.append((b["trace_id"],an[k],last))
        if last is None or last < an[k]: unreach_anchor.append((b["trace_id"],an[k],last))
    print(f"  {t}/{c}: positives={len(pos)}  cannot reach anchor+8 within the scored horizon: {len(unreach8)}  cannot even reach the anchor: {len(unreach_anchor)}")
    for x in unreach8[:8]: print("      ",x)
