"""Stage 2: recompute positive sets, recall, FAR, McNemar, alpha_eff -- and diff against result.json."""
import json, sys
from collections import Counter, defaultdict
sys.path.insert(0, "/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363/scripts/research_v3")
from audit_recompute_lib import *

RUNS = {"b2": "final_b2_both", "b1": "final_b1_both", "h384": "final_h384_both", "c1_heldout": "final_c1_heldout_C1"}
META = load_meta()
CORE_ANCHOR, PROD_ROWS, TOPIC_ROWS = core_anchors()
H384 = h384_labels()

print("## label-file interlocks (recomputed)")
print("  product_onset rows:", len(PROD_ROWS), "non-null:", sum(1 for r in PROD_ROWS if r.get("product_onset") is not None))
print("  topic_entry rows:", len(TOPIC_ROWS), "anchored:", sum(1 for r in TOPIC_ROWS if r.get("topic_entry_onset") is not None))
print("  product/topic trace_id overlap:", len({str(r['trace_id']) for r in PROD_ROWS} & {str(r['trace_id']) for r in TOPIC_ROWS}))
print("  h384 engagement classes:", dict(Counter(v["engagement_class"] for v in H384.values())))
print("  h384 frozen-vs-index class disagreements:",
      [t for t, v in H384.items() if v["frozen_class"] is not None and v["frozen_class"] != v["engagement_class"]])
print("  h384 engaged traces missing an engagement_onset:",
      [t for t, v in H384.items() if v["engagement_class"] in ("cross_domain_execution","bounded_engagement_resisted") and v["engagement_onset"] is None])
print("  h384 execution_onset == span end for executions:",
      sum(1 for v in H384.values() if v["engagement_class"]=="cross_domain_execution" and v["span"] and v["execution_onset"]==v["span"][1]), "/ 40")
print("  h384 spontaneous drift (benign_control with goal_plan_deviation_started):",
      sorted(tid for tid, row in META["h384"].items() if row["arm"]=="benign_control" and row["goal_plan_deviation_started"]))
print()

def anchors_for(target, traces):
    out = {}
    for key, b in traces.items():
        tid = b["trace_id"]
        if target == "h384":
            lab = H384.get(tid)
            if lab and lab["engagement_class"] in ("cross_domain_execution","bounded_engagement_resisted"):
                out[key] = lab["engagement_onset"]
            else:
                out[key] = None
        else:
            out[key] = CORE_ANCHOR.get(tid)
    return out

def sec_anchors_for(traces):
    out = {}
    for key, b in traces.items():
        lab = H384.get(b["trace_id"])
        if lab and lab["engagement_class"] in ("cross_domain_execution","bounded_engagement_resisted"):
            out[key] = lab["execution_onset"]
        else:
            out[key] = None
    return out

def behaviour(target, b):
    if target == "h384":
        lab = H384.get(b["trace_id"])
        k = lab["engagement_class"] if lab else None
        if k == "cross_domain_execution": return "execution"
        if k == "bounded_engagement_resisted": return "bounded"
        if k == "no_observable_engagement": return "silent"
    return {"drift":"execution","resist":"resist"}.get(b["class"], b["class"])

RESULT = {t: json.load(open(ART / r / "result.json")) for t, r in RUNS.items()}
SPONT = {"h384": set(RESULT["h384"]["spontaneous_drift_group"]["keys"])}

report = {}
for target, run in RUNS.items():
    cells = load_cell(run)
    res = RESULT[target]
    for (col, evalset), traces in sorted(cells.items()):
        anch = anchors_for(target, traces) if evalset == "target" and target != "c1_heldout" else {k: None for k in traces}
        excluded = SPONT.get(target, set()) if evalset == "target" else set()
        rules = {"trm3": trm3_rule, "m_only": m_rule, "s_only": s_rule, "j_only": j_rule}
        blkout = {}
        for vname, rf in rules.items():
            alarms = {k: alarm_ends(b, rf(b)) for k, b in traces.items()}
            normals = [k for k, b in traces.items() if b["class"] in ("clean","benign") and k not in excluded]
            clean = [k for k in normals if traces[k]["class"] == "clean"]
            benign = [k for k in normals if traces[k]["class"] == "benign"]
            groups = defaultdict(list)
            for k in normals:
                b = traces[k]
                groups[META[b["batch"]][b["trace_id"]]["pair_group_id"]].append(k)
            far = {
                "clean": len([k for k in clean if alarms[k]]) / len(clean) if clean else None,
                "benign": len([k for k in benign if alarms[k]]) / len(benign) if benign else None,
                "pooled": len([k for k in normals if alarms[k]]) / len(normals) if normals else None,
                "matched_group": sum(1 for rows in groups.values() if any(alarms[k] for k in rows)) / len(groups) if groups else None,
                "n_clean": len(clean), "n_benign": len(benign), "n_groups": len(groups),
            }
            half = {}
            for h in (0,1):
                rows = [k for k in normals if traces[k]["half"] == h]
                half[h] = (len(rows), len([k for k in rows if alarms[k]]) / len(rows) if rows else None)
            far["half_gap"] = abs(half[0][1] - half[1][1]) if half[0][1] is not None and half[1][1] is not None else None
            far["halves"] = half
            # positives
            pos = [k for k, b in traces.items()
                   if anch.get(k) is not None and (b["class"] in ("drift","resist") or behaviour(target,b) in ("execution","bounded"))]
            hits = {k: anchor_hit(alarms[k], anch[k]) for k in pos}
            rec = {}
            if pos:
                for h in (8,16,32,64):
                    rec[f"r{h}"] = sum(1 for v in hits.values() if v[f"hit{h}"]) / len(pos)
                rec["final"] = sum(1 for v in hits.values() if v["hit_final"]) / len(pos)
                rec["pre"] = sum(1 for v in hits.values() if v["pre"]) / len(pos)
                lat = [v["latency"] for v in hits.values() if v["hit_final"] and v["latency"] is not None]
                rec["median"] = statistics.median(lat) if lat else None
                rec["p90"] = sorted(lat)[max(0, math.ceil(0.9*len(lat))-1)] if lat else None
            silent = [k for k,b in traces.items() if behaviour(target,b) == "silent"]
            elig = sum(len([1 for i in range(len(b['ends'])) if not b['hc'][i]]) for k,b in traces.items() if k in normals)
            al_ep = sum(len(alarms[k]) for k in normals)
            onsets = 0
            for k in normals:
                b = traces[k]; prev = False
                for i in range(len(b["ends"])):
                    if b["hc"][i]: continue
                    cur = rf(b)(i)
                    if cur and not prev: onsets += 1
                    prev = cur
            blkout[vname] = {
                "far": far, "recall": rec, "pos": sorted(pos),
                "pos_count": len(pos),
                "drift_pos": sum(1 for k in pos if traces[k]["class"]=="drift"),
                "resist_pos": sum(1 for k in pos if traces[k]["class"]=="resist"),
                "hits8": {k: v["hit8"] for k, v in hits.items()},
                "silent_alarm": (len([k for k in silent if alarms[k]])/len(silent)) if silent else None,
                "silent_n": len(silent),
                "eligible": elig, "alarm_endpoints": al_ep, "onsets": onsets,
                "onsets_per_1k": 1000.0*onsets/elig if elig else None,
                "alarm_ep_rate": al_ep/elig if elig else None,
            }
        report[(target,col,evalset)] = (traces, anch, blkout)

json.dump({f"{t}|{c}|{s}": {v: {kk: vv for kk, vv in b.items() if kk != "hits8"} | {"hits8": b["hits8"]}
                            for v, b in blk.items()}
           for (t,c,s), (_, _, blk) in report.items()},
          open("/tmp/claude-1000/-home-wzh-Agent-Moe-Research--claude-worktrees-algorithm-research-proposals-427363/7e87c1f8-78bb-4b9f-9189-12780e6e8833/scratchpad/audit_stage2.json","w"), indent=1)

# ---- diff against result.json
def g(res, col, var, evalset, *path):
    o = res["columns"][col]["variants"][var]["sets"][evalset]
    for p in path: o = o[p]
    return o

print("## recomputed vs result.json (TRM-3 and B-M, target sets)")
hdr = f"{'cell':<22}{'var':<8}{'metric':<22}{'recomputed':>14}{'result.json':>14}  ok"
print(hdr)
def cmp(cell, var, name, mine, theirs, tol=1e-9):
    ok = (mine is None and theirs is None) or (mine is not None and theirs is not None and abs(mine-theirs) <= tol)
    print(f"{cell:<22}{var:<8}{name:<22}{(f'{mine:.6f}' if mine is not None else 'None'):>14}{(f'{theirs:.6f}' if theirs is not None else 'None'):>14}  {'OK' if ok else '*** MISMATCH'}")
    return ok

bad = []
for (target,col,evalset), (traces, anch, blk) in sorted(report.items()):
    res = RESULT[target]
    cell = f"{target}/{col}/{evalset}"
    for var in ("trm3","m_only","s_only","j_only"):
        b = blk[var]
        try:
            rf = g(res,col,var,evalset,"far")
        except KeyError:
            continue
        for k_mine,k_th in (("clean","clean"),("benign","benign"),("pooled","pooled"),("matched_group","matched_group")):
            if not cmp(cell,var,"far."+k_mine,b["far"][k_mine],rf[k_th]): bad.append((cell,var,"far."+k_mine))
        if not cmp(cell,var,"far_half_gap",b["far"]["half_gap"],g(res,col,var,evalset,"far_half_gap")): bad.append((cell,var,"half_gap"))
        rs = g(res,col,var,evalset,"recall_strict")
        if not cmp(cell,var,"positives",float(b["pos_count"]),float(rs["positive_count"])): bad.append((cell,var,"positives"))
        if b["recall"]:
            for h in (8,16,32,64):
                if not cmp(cell,var,f"recall+{h}",b["recall"][f"r{h}"],rs[f"recall_plus_{h}"]): bad.append((cell,var,f"r{h}"))
            if not cmp(cell,var,"recall_final",b["recall"]["final"],rs["recall_final"]): bad.append((cell,var,"final"))
            if not cmp(cell,var,"pre_onset",b["recall"]["pre"],rs["pre_onset_rate"]): bad.append((cell,var,"pre"))
            if not cmp(cell,var,"latency_median",b["recall"]["median"],rs["latency_median"]): bad.append((cell,var,"med"))
        if not cmp(cell,var,"onsets_per_1k",b["onsets_per_1k"],g(res,col,var,evalset,"endpoint","alarm_onsets_per_1000_eligible")): bad.append((cell,var,"g8"))
        if not cmp(cell,var,"silent_alarm_rate",b["silent_alarm"],g(res,col,var,evalset,"silent_alarm_rate")): bad.append((cell,var,"silent"))
print()
print("TOTAL MISMATCHES:", len(bad))
for x in bad: print("  ", x)
