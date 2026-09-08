"""Independent recomputation of the TRM-3 final cells from outputs.jsonl + label files.

READ-ONLY.  Deliberately does NOT import research_v2.trm3: every rate, hit, gate and
p-value below is re-implemented from the frozen output contract
(docs/research_v3/trm3_output_schema.md) and the frozen label files, so that a bug in
``trm3.evaluate`` cannot reproduce itself in the audit.
"""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
ART = ROOT / "artifacts/agent_v2/research_v3/trm3"
LABELS = ROOT / "docs/research_v2/labels"
ALPHA = 0.10
WEIGHTS = {"S": 0.2, "M": 0.4, "J": 0.4}


def read_jsonl(path):
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


# ---------------------------------------------------------------- labels
def sample_index(name):
    return {str(r["trace_id"]): r for r in read_jsonl(ART.parent.parent / name / "sample_index.jsonl")}


def load_meta():
    """batch -> trace_id -> index row."""
    return {
        "b1": sample_index("agent_v2_5_b1"),
        "b2": sample_index("agent_v2_5_b2"),
        "h384": sample_index("agent_v2_5_b2_horizon384"),
        "c1": sample_index("normal_calibration_c1"),
    }


def core_anchors():
    """trace_id -> anchor (product_onset wins over topic_entry, as the runner does)."""
    out = {}
    prod = list(read_jsonl(LABELS / "product_onset_v1_adjudicated.jsonl"))
    topic = list(read_jsonl(LABELS / "topic_entry_v1_adjudicated.jsonl"))
    for row in prod:
        v = row.get("product_onset", row.get("evidence_onset"))
        out[str(row["trace_id"])] = None if v is None else int(v)
    for row in topic:
        v = row.get("topic_entry_onset")
        out.setdefault(str(row["trace_id"]), None if v is None else int(v))
    return out, prod, topic


def h384_labels():
    """trace_id -> (engagement_class, engagement_onset from the frozen per-trace json,
    execution_onset from the sample index)."""
    adj = {str(r["trace_id"]): r for r in
           read_jsonl(ROOT / "data/agent_v2/agent_v2_5_b2_horizon384_engagement_adjudications.jsonl")}
    idx = sample_index("agent_v2_5_b2_horizon384")
    run = ROOT / "artifacts/agent_v2/agent_v2_5_b2_horizon384"
    out = {}
    for tid, row in adj.items():
        klass = str(row["engagement_class"])
        rel = idx[tid]["relative_path"]
        frozen = run / rel / "engagement_adjudication.json"
        span = None
        fklass = None
        if frozen.exists():
            payload = json.loads(frozen.read_text(encoding="utf-8"))
            span = payload.get("engagement_evidence_output_token_span")
            fklass = payload.get("engagement_class")
        boundary = idx[tid].get("goal_plan_deviation_start_output_token")
        exec_onset = None if boundary is None else int(boundary["output_token_index"])
        out[tid] = {
            "engagement_class": klass,
            "frozen_class": fklass,
            "engagement_onset": None if span is None else int(span[0]),
            "span": span,
            "execution_onset": exec_onset,
            "goal_plan_deviation_started": bool(idx[tid]["goal_plan_deviation_started"]),
        }
    return out


# ---------------------------------------------------------------- outputs
def load_cell(run_name):
    """(column, set) -> key -> per-trace endpoint block, straight from outputs.jsonl."""
    cells = defaultdict(dict)
    for row in read_jsonl(ART / run_name / "outputs.jsonl"):
        col = row["calibration_column"]
        _, evalset, half = row["calibration"].split("|")
        blk = cells[(col, evalset)].setdefault(row["key"], {
            "key": row["key"], "batch": row["batch"], "trace_id": row["trace_id"],
            "arm": row["arm"], "class": row["class"], "half": int(half.replace("half", "")),
            "ends": [], "pS": [], "pM": [], "pJ": [], "pf": [], "state": [], "hc": [],
            "k": [],
        })
        blk["ends"].append(int(row["end"]))
        blk["k"].append(int(row["k"]))
        blk["pS"].append(row["p_S"])
        blk["pM"].append(row["p_M"])
        blk["pJ"].append(row["p_J"])
        blk["pf"].append(float(row["p_fused"]))
        blk["state"].append(row["state"])
        blk["hc"].append(bool(row["horizon_censored"]))
    return cells


def alarm_ends(blk, rule):
    """rule(index) -> bool.  In-horizon endpoints only (schema 1.1 / summarize_trace)."""
    return [e for i, e in enumerate(blk["ends"]) if not blk["hc"][i] and rule(i)]


def trm3_rule(blk, alpha=ALPHA):
    return lambda i: blk["pf"][i] <= alpha


def m_rule(blk, alpha=ALPHA):
    return lambda i: blk["pM"][i] is not None and blk["pM"][i] <= alpha


def s_rule(blk, alpha=ALPHA):
    return lambda i: blk["pS"][i] is not None and blk["pS"][i] <= alpha


def j_rule(blk, alpha=ALPHA):
    return lambda i: blk["pJ"][i] is not None and blk["pJ"][i] <= alpha


def anchor_hit(ends, anchor, band=0):
    limit = anchor - band
    pre = [e for e in ends if e < limit]
    elig = [e for e in ends if e >= limit]
    first = elig[0] if elig else None
    latency = None if first is None else max(0, first - anchor)
    hit = (not pre) and first is not None
    return {"pre": bool(pre), "first": first, "latency": latency, "hit_final": hit,
            "hit8": bool(hit and latency is not None and latency <= 8),
            "hit16": bool(hit and latency is not None and latency <= 16),
            "hit32": bool(hit and latency is not None and latency <= 32),
            "hit64": bool(hit and latency is not None and latency <= 64)}


def mcnemar(hits_a, hits_b):
    keys = sorted(set(hits_a) & set(hits_b))
    pairs = [(bool(hits_a[k]), bool(hits_b[k])) for k in keys]
    only_a = sum(1 for a, b in pairs if a and not b)
    only_b = sum(1 for a, b in pairs if b and not a)
    n = only_a + only_b
    if n == 0:
        p = 1.0
    else:
        smaller = min(only_a, only_b)
        p = min(1.0, 2.0 * sum(math.comb(n, i) for i in range(smaller + 1)) / (2.0 ** n))
    return {"pair_count": len(pairs), "both": sum(1 for a, b in pairs if a and b),
            "neither": sum(1 for a, b in pairs if not a and not b),
            "only_a": only_a, "only_b": only_b, "discordant": n,
            "net": only_a - only_b, "p": p,
            "unpaired": sorted((set(hits_a) | set(hits_b)) - set(keys))}


def attainable(n, alpha):
    if n <= 0:
        return 0, 0.0
    rank = max(0, min(int(math.floor((n + 1) * alpha + 1e-12)), n + 1))
    return rank, rank / (n + 1.0)


def infer_n_reference(cells, col, evalset, channel):
    """n_cal recovered from the p-value lattice itself: p = j/(n+1)."""
    per_half = defaultdict(set)
    for blk in cells[(col, evalset)].values():
        vals = blk[{"S": "pS", "M": "pM", "J": "pJ"}[channel]]
        for v in vals:
            if v is not None:
                per_half[blk["half"]].add(round(float(v), 12))
    out = {}
    for half, vals in per_half.items():
        smallest = min(vals)
        out[half] = int(round(1.0 / smallest)) - 1
    return out
