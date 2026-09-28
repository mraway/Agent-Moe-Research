import json, collections, statistics
from pathlib import Path
ROOT=Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
ART=ROOT/"artifacts/agent_v2/research_v2"
LAB=ROOT/"docs/research_v2/labels"
def jl(p): return {json.loads(l)["trace_id"]:json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()}
RES=jl(LAB/"topic_entry_v1_adjudicated.jsonl"); PROD=jl(LAB/"product_onset_v1_adjudicated.jsonl")
RUNS={"CAND-A":(ART/"narrow_window/wgm_c2_w1248/result.json",[1,2,4,8]),
      "CAND-B":(ART/"narrow_window/pdm_c12_w124/result.json",[1,2,4])}
def cls(t):
    a=t.split("--")[-1]
    if a=="clean": return "clean"
    if a!="attack": return "benign"
    return "drift" if t in PROD else "resist"
def pg(t): return t.rsplit("--",1)[0]

print("### A. per-window trace counts / endpoint structure")
for cand,(p,ws) in RUNS.items():
    res=json.loads(p.read_text())
    for cr in res["case_runs"]:
        w=cr["window_width"]; ss=cr["score_streams"]
        n=len(ss); lens={t:len(b["ends"]) for t,b in ss.items()}
        firsts_end=collections.Counter(b["ends"][0] for b in ss.values() if b["ends"])
        empty=[t for t,b in ss.items() if not b["ends"]]
        cand0=next(c for c in cr["candidates"] if c["mode"]=="D" and c["reading"]=="max" and abs(c["alpha"]-0.1)<1e-12)
        rows=len(cand0["trace_alarms"])
        print(f"{cand} {cr['case']:9} w={w}: streams={n} rows={rows} empty_streams={len(empty)} "
              f"first_end_values={dict(firsts_end)} total_ends={sum(lens.values())} "
              f"min_len={min(lens.values())} max_len={max(lens.values())}")
print()
print("### B. calibration-half membership reconstructed from trace ids (scenario_halves rule)")
for cand,(p,ws) in RUNS.items():
    res=json.loads(p.read_text())
    for reading in ("max","persist2"):
        for w in ws:
            diffs=[]
            for cr in res["case_runs"]:
                if cr["window_width"]!=w: continue
                groups=sorted({pg(t) for t in cr["score_streams"]})
                half={g:i%2 for i,g in enumerate(groups)}
                c=next(x for x in cr["candidates"] if x["mode"]=="D" and x["reading"]==reading and abs(x["alpha"]-0.1)<1e-12)
                firsts={r[0]:r[2] for r in c["trace_alarms"]}
                by={0:[],1:[]}
                for t in firsts:
                    if cls(t) in ("clean","benign"):
                        by[1-half[pg(t)]].append(t)   # cal_half whose threshold evaluated it
                fars=[sum(1 for t in by[h] if firsts[t] is not None)/len(by[h]) for h in (0,1)]
                diffs.append(abs(fars[0]-fars[1]))
                if reading=="max" and w in (1,ws[-1]):
                    print(f"   {cand} {cr['case']:9} w={w} {reading}: n_half0={len(by[0])} n_half1={len(by[1])} "
                          f"far0={fars[0]:.4f} far1={fars[1]:.4f} |diff|={diffs[-1]:.4f}")
            print(f"{cand} w={w} {reading}: far_half_max_abs_diff = {max(diffs):.6f}  per_case={[round(d,6) for d in diffs]}")
print()
print("### C. reachability of +4/+8/+16 per window (resist anchored / drift)")
for cand,(p,ws) in RUNS.items():
    res=json.loads(p.read_text())
    for w in ws:
        counts={}
        for cr in res["case_runs"]:
            if cr["window_width"]!=w: continue
            for t,b in cr["score_streams"].items():
                e=b["ends"]
                if not e: continue
                if t in RES and RES[t]["topic_entry_onset"] is not None: kind="resist"; a=RES[t]["topic_entry_onset"]
                elif t in PROD: kind="drift"; a=PROD[t]["product_onset"]
                else: continue
                for h in (4,8,16):
                    ok=any(a<=x<=a+h for x in e)
                    counts[(kind,h)]=counts.get((kind,h),0)+int(ok)
        print(f"{cand} w={w}: " + " ".join(f"{k[0]}+{k[1]}={v}" for k,v in sorted(counts.items())))
print()
print("### D. false-alarm endpoint positions (clean+benign, mode D alpha .1)")
for cand,(p,ws) in RUNS.items():
    res=json.loads(p.read_text())
    for reading in ("max","persist2"):
        for w in ws:
            fe=[]
            for cr in res["case_runs"]:
                if cr["window_width"]!=w: continue
                c=next(x for x in cr["candidates"] if x["mode"]=="D" and x["reading"]==reading and abs(x["alpha"]-0.1)<1e-12)
                for r in c["trace_alarms"]:
                    if cls(r[0]) in ("clean","benign") and r[2] is not None: fe.append(r[2])
            fe.sort()
            print(f"{cand} w={w} {reading}: n_FA={len(fe)} first_alarm_ends={fe} n_below_8={sum(1 for x in fe if x<8)} n_below_32={sum(1 for x in fe if x<32)}")
