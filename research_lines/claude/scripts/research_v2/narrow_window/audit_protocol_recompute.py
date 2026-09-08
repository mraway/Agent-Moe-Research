#!/usr/bin/env python3
"""Independent recomputation of the narrow-window headline numbers straight from
result.json (stored trace_alarms + score_streams ends) and the label files.

Deliberately does NOT import evaluate.py.  Arm classes are derived from the trace-id
suffix and cross-checked against the harness's own metrics.by_arm counts.
Read-only.
"""
from __future__ import annotations
import json, statistics, sys
from pathlib import Path

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
ART = ROOT / "artifacts" / "agent_v2" / "research_v2"
LAB = ROOT / "docs" / "research_v2" / "labels"

RUNS = {
    "CAND-A": (ART / "narrow_window/wgm_c2_w1248/result.json", [1, 2, 4, 8], 8),
    "CAND-B": (ART / "narrow_window/pdm_c12_w124/result.json", [1, 2, 4], 4),
}

def jl(p):
    return {json.loads(l)["trace_id"]: json.loads(l) for l in Path(p).read_text().splitlines() if l.strip()}

RESIST = jl(LAB / "topic_entry_v1_adjudicated.jsonl")
PROD = jl(LAB / "product_onset_v1_adjudicated.jsonl")

def arm_of(tid):
    return tid.split("--")[-1]

def cls_of(tid):
    a = arm_of(tid)
    if a == "clean":
        return "clean"
    if a != "attack":
        return "benign"
    return "drift" if tid in PROD else "resist"

def blocks(first, ends, anchor, band):
    limit = anchor - band
    pre = first is not None and first < limit
    f = first if (first is not None and first >= limit) else None
    lat = None if f is None else max(0, f - anchor)
    return {"pre": pre, "hit": (not pre) and f is not None, "lat": lat,
            "pre_eligible": any(e < limit for e in ends)}

def rec(traces, anchor_of, firsts, ends_of, band):
    bs = [blocks(firsts[t], ends_of[t], anchor_of(t), band) for t in traces]
    out = {"n": len(bs), "pre": sum(b["pre"] for b in bs),
           "final": sum(b["hit"] for b in bs),
           "any_alarm": sum(1 for t in traces if firsts[t] is not None)}
    for h in (4, 8, 16):
        out[f"R{h}"] = sum(1 for b in bs if b["hit"] and b["lat"] is not None and b["lat"] <= h)
    lats = sorted(b["lat"] for b in bs if b["hit"])
    out["median_latency"] = float(statistics.median(lats)) if lats else None
    return out

def main():
    alpha = 0.1
    rows = []
    for cand, (path, widths, frozen_w) in RUNS.items():
        res = json.loads(path.read_text())
        for reading in ("max", "persist2"):
            for w in widths:
                agg = {"clean_n": 0, "clean_fa": 0, "benign_n": 0, "benign_fa": 0,
                       "e0_n": 0, "e0_fa": 0}
                per_dir = {}
                resist_all, drift_all = [], []
                firsts_all, ends_all = {}, {}
                for cr in res["case_runs"]:
                    if cr["split"] != "S1" or cr["routine_definition"] != "cb" or cr["window_width"] != w:
                        continue
                    cand_blk = next(c for c in cr["candidates"]
                                    if c["mode"] == "D" and abs(c["alpha"] - alpha) < 1e-12
                                    and c["reading"] == reading)
                    firsts = {r[0]: r[2] for r in cand_blk["trace_alarms"]}
                    ends = {tid: cr["score_streams"][tid]["ends"] for tid in firsts}
                    firsts_all.update(firsts); ends_all.update(ends)
                    groups = {"clean": [], "benign": [], "resist": [], "drift": []}
                    for tid in firsts:
                        groups[cls_of(tid)].append(tid)
                    # cross-check against harness by_arm
                    ba = cand_blk["metrics"]["by_arm"]
                    assert len(groups["clean"]) == ba["clean"]["trace_count"], (cand, w, reading)
                    assert len(groups["benign"]) == ba["benign"]["trace_count"]
                    assert len(groups["resist"]) == ba["resist"]["trace_count"]
                    assert len(groups["drift"]) == cand_blk["metrics"]["drift_trace_count"]
                    for k in ("clean", "benign"):
                        fa = sum(1 for t in groups[k] if firsts[t] is not None)
                        assert fa == ba[k]["false_alarm_count"], (cand, w, reading, k, fa, ba[k])
                        agg[f"{k}_n"] += len(groups[k]); agg[f"{k}_fa"] += fa
                    anch = [t for t in groups["resist"] if RESIST[t]["topic_entry_onset"] is not None]
                    e0 = [t for t in groups["resist"] if RESIST[t]["topic_entry_onset"] is None]
                    agg["e0_n"] += len(e0)
                    agg["e0_fa"] += sum(1 for t in e0 if firsts[t] is not None)
                    resist_all += anch; drift_all += groups["drift"]
                    per_dir[cr["case"]] = {
                        "resist_n": len(anch),
                        "strict": rec(anch, lambda t: RESIST[t]["topic_entry_onset"], firsts, ends, 0),
                        "tol5": rec(anch, lambda t: RESIST[t]["topic_entry_onset"], firsts, ends, 5),
                        "far_clean": round(sum(1 for t in groups["clean"] if firsts[t] is not None) / len(groups["clean"]), 6),
                        "far_benign": round(sum(1 for t in groups["benign"] if firsts[t] is not None) / len(groups["benign"]), 6),
                    }
                rs = rec(resist_all, lambda t: RESIST[t]["topic_entry_onset"], firsts_all, ends_all, 0)
                rt = rec(resist_all, lambda t: RESIST[t]["topic_entry_onset"], firsts_all, ends_all, 5)
                dp = rec(drift_all, lambda t: PROD[t]["product_onset"], firsts_all, ends_all, 0)
                de = rec(drift_all, lambda t: PROD[t]["evidence_onset"], firsts_all, ends_all, 0)
                rows.append({
                    "candidate": cand, "window": w, "reading": reading, "alpha": alpha,
                    "frozen_window": w == frozen_w,
                    "resist_anchored_n": rs["n"], "resist_R4": rs["R4"], "resist_R8": rs["R8"],
                    "resist_R16": rs["R16"], "resist_Rfinal": rs["final"],
                    "resist_R8_tol5": rt["R8"], "resist_R16_tol5": rt["R16"],
                    "resist_pre_onset": rs["pre"], "resist_median_latency": rs["median_latency"],
                    "drift_n": dp["n"], "drift_R8_product": dp["R8"], "drift_R16_product": dp["R16"],
                    "drift_Rfinal": dp["final"], "drift_pre_onset_product": dp["pre"],
                    "drift_R16_evidence": de["R16"], "drift_R8_evidence": de["R8"],
                    "far_clean": round(agg["clean_fa"] / agg["clean_n"], 6),
                    "far_benign": round(agg["benign_fa"] / agg["benign_n"], 6),
                    "far_pooled": round((agg["clean_fa"] + agg["benign_fa"]) / (agg["clean_n"] + agg["benign_n"]), 6),
                    "e0_alarm_count": agg["e0_fa"], "e0_n": agg["e0_n"],
                    "per_direction": {k: {"R16_strict": v["strict"]["R16"], "R16_tol5": v["tol5"]["R16"],
                                          "n": v["resist_n"], "far_clean": v["far_clean"],
                                          "far_benign": v["far_benign"]}
                                      for k, v in per_dir.items()},
                })
    print(json.dumps(rows, indent=1))

if __name__ == "__main__":
    main()
