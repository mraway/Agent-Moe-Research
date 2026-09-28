"""Position / truncation control: does decode position drive the pair-unseen and
novel-combination rates?  Routine held-out rates by position bucket, plus a
position-matched routine baseline for each code region."""
from __future__ import annotations
import json, statistics as st, sys
from pathlib import Path
import torch
ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
sys.path.insert(0, str(ROOT/"src"))
from research_v2 import io as rio  # noqa: E402
LABELS = ROOT/"docs/research_v2/labels/product_onset_v1_adjudicated.jsonl"
E=64; REGION=48
PROG=["b1-f2-012-order_status-python-function--attack","b1-f2-014-knowledge_qa-python-function--attack",
      "b1-f3-016-return_and_knowledge-javascript-utility--attack","b2-f2-011-knowledge_qa-sql-query--attack",
      "b2-f2-012-order_and_knowledge-sql-query--attack","b2-f2-014-support_case_status-sql-query--attack",
      "b2-f2-015-warranty_status-sql-query--attack","b2-f3-020-order_status-rust-function--attack"]
BUCKETS=[(0,48),(48,96),(96,144),(144,192)]

labels={json.loads(l)["trace_id"]:json.loads(l) for l in LABELS.read_text().splitlines() if l.strip()}
b=rio.load_core(); traces=[t for k in ("b1","b2") for t in b[k]]
routine=[t for t in traces if rio.arm_class(t) in ("clean","benign")]
drift=[t for t in traces if t.positive]
groups=sorted({t.pair_group_id for t in routine}); fold_of={g:i%2 for i,g in enumerate(groups)}
folds={f:[t for t in routine if fold_of[t.pair_group_id]==f] for f in (0,1)}
agg={i:{"tot":0,"uns":0,"nc":0,"ff":0} for i in range(len(BUCKETS))}
code_stats={t.trace_id:{"uns":0,"tot":0,"nc":0,"domain":t.domain} for t in drift}
for f in (0,1):
    fit,hold=folds[f],folds[1-f]
    pair=torch.zeros((15,E,E),dtype=torch.float64); marg=torch.zeros((16,E),dtype=torch.float64); n=0
    for t in fit:
        top1=t.top_k_ids.long()[:,:,0]; n+=top1.shape[1]
        for l in range(16): marg[l]+=torch.bincount(top1[l],minlength=E)
        for l in range(15): pair[l]+=torch.bincount(top1[l]*E+top1[l+1],minlength=E*E).reshape(E,E)
    unseen=pair==0; common=(marg/n)>=1.0/E
    for t in hold:
        top1=t.top_k_ids.long()[:,:,0]; T=top1.shape[1]
        for l in range(15):
            a,bb=top1[l],top1[l+1]
            u=unseen[l][a,bb]; c=common[l][a]&common[l+1][bb]
            for i,(lo,hi) in enumerate(BUCKETS):
                sl=slice(lo,min(hi,T))
                if sl.start>=T: continue
                agg[i]["tot"]+=len(range(sl.start,sl.stop)); agg[i]["uns"]+=int(u[sl].sum())
                agg[i]["nc"]+=int((u[sl]&c[sl]).sum()); agg[i]["ff"]+=int(c[sl].sum())
    for t in drift:
        lab=labels[t.trace_id]; lo=max(0,int(lab["product_onset"])); hi=min(lo+REGION,t.token_count)
        top1=t.top_k_ids.long()[:,:,0]
        for l in range(15):
            a,bb=top1[l,lo:hi],top1[l+1,lo:hi]
            u=unseen[l][a,bb]; c=common[l][a]&common[l+1][bb]
            code_stats[t.trace_id]["uns"]+=int(u.sum()); code_stats[t.trace_id]["nc"]+=int((u&c).sum()); code_stats[t.trace_id]["tot"]+=int(a.numel())
print("routine held-out rates by decode-position bucket (both folds pooled):")
print("| bucket | pair-instances | pair_unseen | novel_comb | ff_frac |")
print("|---"*5+"|")
for i,(lo,hi) in enumerate(BUCKETS):
    a=agg[i]
    if a["tot"]==0: continue
    print(f"| {lo}-{hi-1} | {a['tot']} | {a['uns']/a['tot']:.4f} | {a['nc']/a['tot']:.5f} | {a['ff']/a['tot']:.3f} |")
tot=sum(a["tot"] for a in agg.values()); uns=sum(a["uns"] for a in agg.values()); nc=sum(a["nc"] for a in agg.values())
print(f"| pooled | {tot} | {uns/tot:.4f} | {nc/tot:.5f} | |")
print("\ncode regions against the POSITION-MATCHED routine baseline (bucket of the region midpoint):")
print("| trace | onset | midpoint bucket | pair_unseen | vs pooled | vs position-matched | novel_comb vs pooled | vs position-matched |")
print("|---"*8+"|")
pm={"code":{"pu":[],"nc":[]},"other":{"pu":[],"nc":[]}}
pl={"code":{"pu":[],"nc":[]},"other":{"pu":[],"nc":[]}}
for t in drift:
    lab=labels[t.trace_id]; lo=max(0,int(lab["product_onset"])); mid=lo+REGION//2
    bi=min(range(len(BUCKETS)), key=lambda i: abs((BUCKETS[i][0]+BUCKETS[i][1])/2-mid))
    a=agg[bi]; cs=code_stats[t.trace_id]
    grp="code" if t.trace_id in PROG else "other"
    pm[grp]["pu"].append((cs['uns']/cs['tot'])/(a['uns']/a['tot'])); pm[grp]["nc"].append((cs['nc']/cs['tot'])/(a['nc']/a['tot']))
    pl[grp]["pu"].append((cs['uns']/cs['tot'])/(uns/tot)); pl[grp]["nc"].append((cs['nc']/cs['tot'])/(nc/tot))
    if t.trace_id not in PROG: continue
    print(f"| {t.trace_id.split('--')[0][:34]} | {lo} | {BUCKETS[bi][0]}-{BUCKETS[bi][1]-1} | {cs['uns']/cs['tot']:.4f} | {(cs['uns']/cs['tot'])/(uns/tot):.2f}x | {(cs['uns']/cs['tot'])/(a['uns']/a['tot']):.2f}x | {(cs['nc']/cs['tot'])/(nc/tot):.2f}x | {(cs['nc']/cs['tot'])/(a['nc']/a['tot']):.2f}x |")

print("\n| group | n | pair_unseen vs pooled | vs position-matched | novel_comb vs pooled | vs position-matched |")
print("|---"*6+"|")
for g in ("code","other"):
    print(f"| {g} | {len(pm[g]['pu'])} | {st.median(pl[g]['pu']):.2f}x | {st.median(pm[g]['pu']):.2f}x | {st.median(pl[g]['nc']):.2f}x | {st.median(pm[g]['nc']):.2f}x |")
print("code/other gap on novel_comb: pooled %.2fx -> position-matched %.2fx" % (st.median(pl['code']['nc'])/st.median(pl['other']['nc']), st.median(pm['code']['nc'])/st.median(pm['other']['nc'])))
