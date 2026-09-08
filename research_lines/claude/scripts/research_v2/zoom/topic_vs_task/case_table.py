from __future__ import annotations
import json
from pathlib import Path
import numpy as np, torch
torch.set_num_threads(6)
import research_v2.io as rio
OUT = Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/research_v2/zoom/topic_vs_task")
D = json.load(open(OUT / "records.json")); Z = np.load(OUT / "zstreams.npz")
FA = {(r["cand"], r["case"], r["trace_id"]): r for r in json.load(open(OUT / "false_alarm_text.json"))}
W = {"A": 8, "B": 4}
batches = rio.load_core(); by_id = {t.trace_id: t for b in batches.values() for t in b}
SEL = [
 ("A","b2_to_b1","b1-f2-036-knowledge_qa-meal-plan--attack"),
 ("A","b2_to_b1","b1-f4-048-order_and_knowledge-dialogue-scene--attack"),
 ("A","b2_to_b1","b1-f4-048-order_and_knowledge-dialogue-scene--benign_control"),
 ("A","b2_to_b1","b1-f0-030-subscription_status-math-proof--benign_control"),
 ("A","b2_to_b1","b1-f4-048-order_and_knowledge-dialogue-scene--clean"),
 ("A","b1_to_b2","b2-f3-044-knowledge_qa-character-monologue--attack"),
 ("A","b1_to_b2","b2-f2-038-subscription_status-grocery-plan--attack"),
 ("A","b1_to_b2","b2-f0-001-order_and_knowledge-free-verse--benign_control"),
 ("A","b1_to_b2","b2-f3-042-order_status-character-monologue--attack"),
 ("A","b1_to_b2","b2-f4-048-warranty_status-fable--attack"),
 ("A","b2_to_b1","b1-f1-058-knowledge_qa-packing-guide--attack"),
 ("A","b2_to_b1","b1-f2-014-knowledge_qa-python-function--attack"),
 ("B","b1_to_b2","b2-f1-010-return_status-limerick--attack"),
 ("B","b2_to_b1","b1-f2-062-warranty_status-fictional-legal-memo--attack"),
]
print("| cand | dir | trace_id | arm | domain / channel / workflow | len | onset | thr | peak z @end | run win/blocks | layerFrac | late/mid | first alarm | snippet |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for ck, cn, tid in SEL:
    r = next(x for x in D["records"] if x["cand"]==ck and x["case"]==cn and x["trace_id"]==tid)
    key=f"{cn}::{ck}::{tid}"; st=Z[key+"::stat"]; ends=Z[key+"::ends"]
    t=by_id[tid]; ids=t.token_ids.tolist(); w=W[ck]
    fin=np.where(np.isfinite(st),st,-1e18)
    if r["positive"]:
        sel=ends>=r["evidence_onset"]; fin2=np.where(sel,fin,-1e18)
    else:
        fin2=fin
    i=int(np.argmax(fin2)); pe=int(ends[i]); s=max(0,pe-w+1)
    snip=rio.decode_text(ids[s:pe+1]).replace("|","/").replace("\n","\\n")[:60]
    print(f"| {ck} | {cn} | {tid} | {r['arm']} | {r['domain']} / {r['channel']} / {r['workflow']} | {r['token_count']} | "
          f"{r['evidence_onset'] if r['evidence_onset'] is not None else '-'} | {r['threshold']:.2f} | {fin2[i]:.2f} @{pe} | "
          f"{r['max_run_windows']}/{r['max_run_blocks']} | {r['peak_layer_frac']:.2f} | {r['peak_late_mid']:.2f} | "
          f"{r['first_alarm_end']} | `{snip}` |")
