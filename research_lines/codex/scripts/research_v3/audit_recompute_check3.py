"""Stage 3: P1 McNemar (nominal + matched alpha), alpha_eff, calibration-half discipline, gates."""
import json, sys
from collections import Counter, defaultdict
sys.path.insert(0, "/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363/scripts/research_v3")
from audit_recompute_lib import *

RUNS = {"b2": "final_b2_both", "b1": "final_b1_both", "h384": "final_h384_both", "c1_heldout": "final_c1_heldout_C1"}
META = load_meta()
CORE_ANCHOR, PROD, TOPIC = core_anchors()
H384 = h384_labels()
RESULT = {t: json.load(open(ART / r / "result.json")) for t, r in RUNS.items()}
SPONT = set(RESULT["h384"]["spontaneous_drift_group"]["keys"])

def anchors_for(target, traces):
    out = {}
    for key, b in traces.items():
        if target == "h384":
            lab = H384.get(b["trace_id"])
            out[key] = lab["engagement_onset"] if lab and lab["engagement_class"] in ("cross_domain_execution","bounded_engagement_resisted") else None
        else:
            out[key] = CORE_ANCHOR.get(b["trace_id"])
    return out

def sec_anchors_for(traces):
    out = {}
    for key, b in traces.items():
        lab = H384.get(b["trace_id"])
        out[key] = lab["execution_onset"] if lab and lab["engagement_class"] in ("cross_domain_execution","bounded_engagement_resisted") else None
    return out

print("# A. calibration-half discipline (mode D: reference half = the OTHER scenario half)")
for target in ("b2","b1","h384"):
    cells = load_cell(RUNS[target])
    for col in ("D","C1"):
        traces = cells.get((col,"target"))
        if not traces: continue
        gh = defaultdict(set)
        for k,b in traces.items():
            gh[META[b["batch"]][b["trace_id"]]["pair_group_id"]].add(b["half"])
        multi = [g for g,v in gh.items() if len(v)>1]
        per_half = Counter(next(iter(v)) for v in gh.values())
        routine_per_half = Counter()
        for k,b in traces.items():
            if b["class"] in ("clean","benign") and k not in SPONT:
                routine_per_half[b["half"]] += 1
        predicted = {h: routine_per_half[1-h] for h in (0,1)}
        recovered = infer_n_reference(cells, col, "target", "M")
        print(f"  {target}/{col}: groups={len(gh)} split-across-halves={len(multi)} groups/half={dict(per_half)}")
        print(f"      routine traces per output-half={dict(routine_per_half)}  ->  predicted n_ref(half h)=|routine in half 1-h|={predicted}   recovered from p lattice={dict(recovered)}   MATCH={all(predicted[h]==recovered.get(h) for h in (0,1)) if col=='D' else 'n/a (C1 pool is external)'}")
        # p == 1/(n+1) attained by routine traces -> proof of self-exclusion
        for h in (0,1):
            n = recovered.get(h)
            if not n: continue
            lo = 1.0/(n+1)
            hitmin = [k for k,b in traces.items() if b["half"]==h and b["class"] in ("clean","benign")
                      and any(v is not None and abs(v-lo)<1e-12 for v in b["pM"])]
            print(f"      half{h}: routine traces attaining p_M = 1/(n+1) = {lo:.6f}: {len(hitmin)}  (impossible if the trace were inside its own reference)")
print()

print("# B. spontaneous-drift exclusion (h384, prereg v1.1 amendment 4)")
cells = load_cell(RUNS["h384"])
for col in ("D","C1"):
    traces = cells[(col,"target")]
    sp = {k: traces[k] for k in SPONT}
    print(f"  {col}: 4 keys present in outputs={len(sp)} halves={[b['half'] for b in sp.values()]} classes={[b['class'] for b in sp.values()]}")
    nb = len([1 for k,b in traces.items() if b['class']=='benign' and k not in SPONT])
    nb_all = len([1 for k,b in traces.items() if b['class']=='benign'])
    print(f"      benign FAR denominator without them = {nb} (with them {nb_all}); result.json benign_count = {RESULT['h384']['columns'][col]['variants']['trm3']['sets']['target']['far']['benign_count']}")
    alarms = {k: alarm_ends(b, trm3_rule(b)) for k,b in sp.items()}
    print(f"      their own any-alarm count (descriptive group) = {sum(1 for v in alarms.values() if v)} / 4 ; result.json = {RESULT['h384']['columns'][col]['variants']['trm3']['sets']['target']['spontaneous_drift']['any_alarm_count']}")
    # are they positives?
    anch = anchors_for("h384", traces)
    print(f"      anchors of the 4 = {[anch[k] for k in SPONT]} (must all be None -> never positives)")
print()

print("# C. positive sets")
for target in ("b2","b1","h384"):
    cells = load_cell(RUNS[target])
    traces = cells[("D","target")]
    anch = anchors_for(target, traces)
    pos = [k for k,b in traces.items() if anch.get(k) is not None and (b["class"] in ("drift","resist") or (target=="h384" and H384.get(b["trace_id"],{}).get("engagement_class") in ("cross_domain_execution","bounded_engagement_resisted")))]
    print(f"  {target}: positives={len(pos)} drift={sum(1 for k in pos if traces[k]['class']=='drift')} resist={sum(1 for k in pos if traces[k]['class']=='resist')}")
    print(f"      attack-arm traces={sum(1 for b in traces.values() if b['class'] in ('drift','resist'))} with non-null anchor={sum(1 for k,b in traces.items() if b['class'] in ('drift','resist') and anch.get(k) is not None)}")
    print(f"      any clean/benign carrying a non-null anchor = {sum(1 for k,b in traces.items() if b['class'] in ('clean','benign') and anch.get(k) is not None)}")
b2c = load_cell(RUNS['b2'])[("D","target")]; b1c = load_cell(RUNS['b1'])[("D","target")]
a2 = anchors_for("b2", b2c); a1 = anchors_for("b1", b1c)
p2 = {k for k,b in b2c.items() if a2.get(k) is not None and b['class'] in ('drift','resist')}
p1 = {k for k,b in b1c.items() if a1.get(k) is not None and b['class'] in ('drift','resist')}
print(f"  B1+B2 pooled positives = {len(p1)+len(p2)} (drift {sum(1 for k in p1|p2 if (b1c|b2c)[k]['class']=='drift')} + anchored resist {sum(1 for k in p1|p2 if (b1c|b2c)[k]['class']=='resist')})")
print()

print("# D. P1 McNemar -- recomputed")
LEGS = [("b2","D",3),("b1","D",3),("h384","C1",4),("h384","D",4)]
for target,col,thr in LEGS:
    cells = load_cell(RUNS[target])
    traces = cells[(col,"target")]
    anch = anchors_for(target, traces)
    pos = [k for k,b in traces.items() if anch.get(k) is not None and (b["class"] in ("drift","resist") or (target=="h384" and H384.get(b["trace_id"],{}).get("engagement_class") in ("cross_domain_execution","bounded_engagement_resisted")))]
    nref = infer_n_reference(cells, col, "target", "M")
    # alpha_eff per half, from the recovered n
    eff = {}
    for h,n in nref.items():
        eff[h] = sum(attainable(n, WEIGHTS[c]*ALPHA)[1] for c in "SMJ")
    alpha_eff_min = min(eff.values()); alpha_eff_max = max(eff.values())
    matched_per_half = {h: attainable(nref[h], eff[h])[1] for h in nref}
    alpha_matched = min(matched_per_half.values())
    trm3_hits = {k: anchor_hit(alarm_ends(traces[k], trm3_rule(traces[k])), anch[k])["hit8"] for k in pos}
    out = {}
    for label, a in (("matched", alpha_matched), ("nominal", ALPHA)):
        bm = {k: anchor_hit(alarm_ends(traces[k], m_rule(traces[k], a)), anch[k])["hit8"] for k in pos}
        out[label] = mcnemar(trm3_hits, bm)
    res_block = RESULT[target]["columns"][col]
    ref_m = res_block.get("p1_matched_alpha", {}).get("mcnemar_matched")
    ref_n = res_block.get("mcnemar", {}).get("trm3")
    ref_alpha = res_block.get("p1_matched_alpha", {})
    print(f"  LEG {target}/{col} (threshold net>=+{thr}, p<0.05): positives={len(pos)}")
    print(f"    n_ref by half={dict(nref)}  alpha_eff by half={ {h:round(v,12) for h,v in eff.items()} }  alpha_eff_min={alpha_eff_min!r}")
    print(f"    alpha_matched recomputed={alpha_matched!r}   result.json={ref_alpha.get('alpha_matched')!r}  MATCH={abs(alpha_matched-(ref_alpha.get('alpha_matched') or -1))<1e-12}")
    for label in ("matched","nominal"):
        m = out[label]
        r = ref_m if label=="matched" else ref_n
        same = r is not None and all(m[a]==r[b] for a,b in (("pair_count","pair_count"),("both","both"),("neither","neither"),("only_a","only_a"),("only_b","only_b"),("net","net_gain_a_over_b"))) and abs(m["p"]-r["p_value"])<1e-12
        print(f"    {label:<8} pair={m['pair_count']} both={m['both']} neither={m['neither']} only_trm3={m['only_a']} only_BM={m['only_b']} net={m['net']:+d} p={m['p']:.6g}  -> {'PASS' if (m['net']>=thr and m['p']<0.05) else 'FAIL'}   result.json MATCH={same}")
        if r is not None and not same:
            print("       result.json:", {k:r[k] for k in ("pair_count","both","neither","only_a","only_b","net_gain_a_over_b","p_value")})
