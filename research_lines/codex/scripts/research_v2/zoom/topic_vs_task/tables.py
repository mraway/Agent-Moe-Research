"""Tables for the topic-mention vs task-execution zoom audit."""
from __future__ import annotations
import json, statistics
from pathlib import Path
import numpy as np

OUT = Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/research_v2/zoom/topic_vs_task")
D = json.load(open(OUT / "records.json"))
R = D["records"]
RULES = ["R0_baseline", "R1_blocks2", "R2_layerfrac", "R3_latemid", "R4_perlayer_max", "R5_sqrt", "R6_lf_and_blocks2"]
ARM = {"clean": "clean", "benign_control": "benign", "attack": "attack"}


def metrics(rows, rule):
    neg = [r for r in rows if not r["positive"]]
    pos = [r for r in rows if r["positive"]]
    out = {}
    def far(sub):
        if not sub: return None, 0, 0
        n = sum(1 for r in sub if r["rule_first_end"][rule][0] is not None)
        return n / len(sub), n, len(sub)
    out["FARall"], out["FARall_n"], out["FARall_d"] = far(neg)
    for a in ("clean", "benign_control", "attack"):
        v, n, d = far([r for r in neg if r["arm"] == a])
        out[f"FAR_{ARM[a]}"], out[f"FAR_{ARM[a]}_n"], out[f"FAR_{ARM[a]}_d"] = v, n, d
    pre = 0; pre_elig = 0; hits = {4: 0, 8: 0, 16: 0}; final = 0; lat = []
    for r in pos:
        fa, post = r["rule_first_end"][rule]
        onset = r["evidence_onset"]
        if fa is not None and fa < onset:
            pre += 1
        if r["n_windows"]:
            pre_elig += 1  # every drift trace here has windows before onset? checked separately
        if fa is not None and fa < onset:
            continue
        if post is None:
            continue
        final += 1
        lt = max(0, post - onset)
        lat.append(lt)
        for h in hits:
            if lt <= h: hits[h] += 1
    n = len(pos)
    out["n_drift"] = n
    out["pre_onset"] = pre / n if n else None
    for h in (4, 8, 16):
        out[f"R{h}"] = hits[h] / n if n else None
        out[f"R{h}_n"] = hits[h]
    out["Rfinal"] = final / n if n else None
    out["lat"] = float(statistics.median(lat)) if lat else None
    return out


def fmt(v, p=3):
    return "-" if v is None else (f"{v:.{p}f}" if isinstance(v, float) else str(v))


print("## Rule comparison (mode D, alpha=0.10, persist2, cross-fitted)\n")
print("| cand | dir | rule | FARall | clean | benign | resist | preOnset | +8 | +16 | final | lat |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|")
lines = []
for ck in ("A", "B"):
    for cn in ("b1_to_b2", "b2_to_b1"):
        rows = [r for r in R if r["cand"] == ck and r["case"] == cn]
        for rule in RULES:
            if rule == "R5_sqrt" and ck != "A":
                continue
            m = metrics(rows, rule)
            print(f"| {ck} | {cn} | {rule} | {fmt(m['FARall'])} ({m['FARall_n']}/{m['FARall_d']}) | "
                  f"{fmt(m['FAR_clean'])} ({m['FAR_clean_n']}/{m['FAR_clean_d']}) | "
                  f"{fmt(m['FAR_benign'])} ({m['FAR_benign_n']}/{m['FAR_benign_d']}) | "
                  f"{fmt(m['FAR_attack'])} ({m['FAR_attack_n']}/{m['FAR_attack_d']}) | "
                  f"{fmt(m['pre_onset'])} | {fmt(m['R8'])} ({m['R8_n']}/{m['n_drift']}) | "
                  f"{fmt(m['R16'])} ({m['R16_n']}/{m['n_drift']}) | {fmt(m['Rfinal'])} | {fmt(m['lat'],1)} |")
