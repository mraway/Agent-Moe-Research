"""Full frozen-vs-new comparison at the reference windows: every candidate block,
every stored metric, every threshold, plus score_streams equality."""
import json
from pathlib import Path
ROOT=Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
A=ROOT/"artifacts/agent_v2/research_v2"
PAIRS={"CAND-A":(A/"wgm/c2_g1_middle_late/result.json",A/"narrow_window/wgm_c2_w1248/result.json",8),
       "CAND-B":(A/"pdm_d1_middle_s1/result.json",A/"narrow_window/pdm_c12_w124/result.json",4)}
def diff(a,b,path=""):
    out=[]
    if type(a)!=type(b) and not (isinstance(a,(int,float)) and isinstance(b,(int,float))):
        return [(path,type(a).__name__,type(b).__name__)]
    if isinstance(a,dict):
        for k in set(a)|set(b):
            if k not in a: out.append((path+"/"+str(k),"<missing frozen>",""))
            elif k not in b: out.append((path+"/"+str(k),"","<missing new>"))
            else: out+=diff(a[k],b[k],path+"/"+str(k))
    elif isinstance(a,list):
        if len(a)!=len(b): return [(path,f"len {len(a)}",f"len {len(b)}")]
        for i,(x,y) in enumerate(zip(a,b)): out+=diff(x,y,f"{path}[{i}]")
    elif isinstance(a,float) or isinstance(b,float):
        if a!=b and not (a!=a and b!=b): out.append((path,a,b))
    else:
        if a!=b: out.append((path,a,b))
    return out
for cand,(fp,np_,w) in PAIRS.items():
    F=json.loads(fp.read_text()); N=json.loads(np_.read_text())
    fcr={c["case"]:c for c in F["case_runs"] if c["window_width"]==w and c["routine_definition"]=="cb"}
    ncr={c["case"]:c for c in N["case_runs"] if c["window_width"]==w and c["routine_definition"]=="cb"}
    for case in fcr:
        f,n=fcr[case],ncr[case]
        ds=diff(f["score_streams"],n["score_streams"],"score_streams")
        dc=diff(f["calibration"],n["calibration"],"calibration")
        fcand={c["candidate_id"]:c for c in f["candidates"]}
        ncand={c["candidate_id"]:c for c in n["candidates"]}
        dcand=[]
        assert set(fcand)==set(ncand)
        for k in fcand:
            a={x:y for x,y in fcand[k].items() if x!="bootstrap"}
            b={x:y for x,y in ncand[k].items() if x!="bootstrap"}
            dcand+=diff(a,b,k)
        dq=diff(f.get("q1_panel"),n.get("q1_panel"),"q1_panel")
        dr=diff(f.get("ranking"),n.get("ranking"),"ranking")
        da=diff(f.get("pre_onset_audit"),n.get("pre_onset_audit"),"pre_onset_audit")
        print(f"{cand} {case} w={w}: candidates={len(fcand)} "
              f"score_stream_diffs={len(ds)} calibration_diffs={len(dc)} "
              f"candidate_metric_diffs={len(dcand)} q1_diffs={len(dq)} ranking_diffs={len(dr)} audit_diffs={len(da)}")
        for d in (ds+dc+dcand+dq+dr+da)[:8]: print("   ",d)
    # sanity: rest of the config-independent header
    print(f"   frozen commit {F.get('code_commit')} new commit {N.get('code_commit')} datasets_equal={F.get('datasets')==N.get('datasets')}")
