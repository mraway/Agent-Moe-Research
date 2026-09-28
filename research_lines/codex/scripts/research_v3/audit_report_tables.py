"""Stage 6: transcription check -- every number in report tables 1.1-1.4 vs result.json."""
import json, re, sys
from pathlib import Path
ROOT=Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
ART=ROOT/"artifacts/agent_v2/research_v3/trm3"
RUNS={"b2":"final_b2_both","b1":"final_b1_both","h384":"final_h384_both","c1_heldout":"final_c1_heldout_C1"}
R={t:json.load(open(ART/r/"result.json")) for t,r in RUNS.items()}
VAR={"TRM-3":"trm3","B-M (m_only)":"m_only","A-S (s_only)":"s_only","A-J (j_only)":"j_only","A-SM":"sm","A-MJ":"mj","A-SJ":"sj","B-U (degenerate)":"unseen_only","B-S":"surprisal_marginal","B-NT":"no_temporal","B-NT2":"no_temporal2"}
TGT={"b2 (B1->B2)":"b2","b1 (B2->B1)":"b1","h384 replay":"h384","c1 fold 4":"c1_heldout"}
txt=(ROOT/"docs/research_v3/trm3_report.md").read_text(encoding="utf-8").splitlines()

def sec(name):
    i=[n for n,l in enumerate(txt) if l.startswith(name)][0]
    j=next((n for n in range(i+1,len(txt)) if txt[n].startswith("### ") or txt[n].startswith("## ")), len(txt))
    return txt[i:j]

def cells(line): return [c.strip() for c in line.strip().strip("|").split("|")]
def num(s):
    s=s.replace("**","").strip()
    if s in ("-","—",""): return None
    try: return float(s)
    except ValueError: return None

bad=[]; checked=0
# ---- 1.1 FAR table
for line in sec("### 1.1"):
    c=cells(line)
    if len(c)!=13 or c[0] not in TGT: continue
    t=TGT[c[0]]; col=c[1]; var=VAR[c[2].replace("**","")]; st=c[3]
    if t=="c1_heldout": st="target"
    m=R[t]["columns"][col]["variants"][var]["sets"][st]
    exp={"traces":m["far"]["clean_count"]+m["far"]["benign_count"],
         "alpha_eff":R[t]["columns"][col]["variants"][var]["alpha_budget"]["alpha_eff"],
         "FAR clean":m["far"]["clean"],"FAR benign":m["far"]["benign"],"FAR pooled":m["far"]["pooled"],
         "matched-group":m["far"]["matched_group"],"half gap":m["far_half_gap"],
         "alarm ep/1k":1000*m["endpoint"]["alarm_endpoint_rate"],"onsets/1k":m["endpoint"]["alarm_onsets_per_1000_eligible"]}
    got=dict(zip(["traces","alpha_eff","FAR clean","FAR benign","FAR pooled","matched-group","half gap","alarm ep/1k","onsets/1k"],[num(x) for x in c[4:]]))
    for k,v in exp.items():
        g=got[k]; checked+=1
        dec=len((f"{g}".split(".")+[""])[1]) if g is not None else 3
        tol=0.5*10**(-dec) if g is not None else 0
        if g is None or abs(g-v)>tol+1e-12: bad.append(("1.1",t,col,var,st,k,g,v))
# ---- 1.2/1.3/1.4 recall tables
def recall_rows(section, block):
    global checked
    for line in sec(section):
        c=cells(line)
        if len(c)!=12 or c[0] not in TGT: continue
        t=TGT[c[0]]; col=c[1]; var=VAR[c[2].replace("**","")]
        m=R[t]["columns"][col]["variants"][var]["sets"]["target"][block]
        if m is None:
            continue
        exp=[m["positive_count"],m["recall_plus_8"],m["recall_plus_16"],m["recall_plus_32"],m["recall_plus_64"],
             m["recall_final"],m["pre_onset_rate"],m["latency_median"],m["latency_p90"]]
        got=[num(x) for x in c[3:]]
        for i,(g,v) in enumerate(zip(got,exp)):
            checked+=1
            if v is None and g is None: continue
            if g is None or v is None: bad.append((section,t,col,var,block,i,g,v)); continue
            dec=len((f"{g}".split(".")+[""])[1])
            if abs(g-v)>0.5*10**(-dec)+1e-12: bad.append((section,t,col,var,block,i,g,v))
recall_rows("### 1.2","recall_strict")
recall_rows("### 1.3","recall_tolerant")
for line in sec("### 1.4"):
    c=cells(line)
    if len(c)!=12 or c[0] not in TGT: continue
    t=TGT[c[0]]; col=c[1]; var=VAR[c[2].replace("**","")]
    sets=R[t]["columns"][col]["variants"][var]["sets"]["target"]
    for block in ("recall_strict_secondary","recall_tolerant_secondary"):
        m=sets[block]
        if m is None: continue
        exp=[m["positive_count"],m["recall_plus_8"],m["recall_plus_16"],m["recall_plus_32"],m["recall_plus_64"],m["recall_final"],m["pre_onset_rate"],m["latency_median"],m["latency_p90"]]
        got=[num(x) for x in c[3:]]
        ok=all((g is None and v is None) or (g is not None and v is not None and abs(g-v)<=0.5*10**(-len((f"{g}".split('.')+[''])[1]))+1e-12) for g,v in zip(got,exp))
        if ok: break
    else:
        bad.append(("1.4",t,col,var,"secondary",None,got,exp))
    checked+=9
print(f"checked {checked} table numbers; mismatches: {len(bad)}")
for x in bad: print("  ",x)
