"""Read-only recompute of the TRM-3 P1 legs, FARs and recalls (LENS = statistics).

Rebuilds every headline number from (a) the frozen per-trace summaries in result.json
and (b) the per-endpoint p streams in outputs.jsonl, independently of the runner's own
aggregation, and reports exact intervals / tests.  Writes nothing outside stdout.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_statistics_lib as L  # noqa: E402

ROOT = L.ROOT
LABELS = ROOT / "docs/research_v2/labels"
RECALL_HORIZONS = (8, 16, 32, 64)
TOLERANT_BAND = 5


def read_jsonl(path):
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


# ------------------------------------------------------------------ anchors
def core_anchor_maps():
    """product_onset / evidence_onset / topic_entry_onset keyed by trace_id."""
    product, evidence, topic = {}, {}, {}
    for row in read_jsonl(LABELS / "product_onset_v1_adjudicated.jsonl"):
        tid = str(row["trace_id"])
        product[tid] = row.get("product_onset")
        evidence[tid] = row.get("evidence_onset")
    for row in read_jsonl(LABELS / "topic_entry_v1_adjudicated.jsonl"):
        topic[str(row["trace_id"])] = row.get("topic_entry_onset")
    return product, evidence, topic


def core_anchors(summaries, which="product"):
    """Replicates run_trm3.core_anchors; ``which`` swaps product_onset -> evidence_onset."""
    product, evidence, topic = core_anchor_maps()
    primary = product if which == "product" else evidence
    out = {}
    for s in summaries:
        tid = s["trace_id"]
        if tid in primary:
            v = primary[tid]
            out[s["key"]] = None if v is None else int(v)
        elif tid in topic:
            v = topic[tid]
            out[s["key"]] = None if v is None else int(v)
    return out


_H384_CACHE: tuple[dict, dict] | None = None


def h384_anchor_maps():
    """h384 primary (``engagement_onset``) / secondary (``execution_onset``) anchors.

    Read straight off the frozen loader, exactly as ``run_trm3.h384_anchors`` does; nothing
    is written back.
    """
    global _H384_CACHE
    if _H384_CACHE is None:
        from research_v2 import io as _io  # noqa: PLC0415

        primary, secondary = {}, {}
        for trace in _io.load_h384():
            labels = getattr(trace, "labels", {}) or {}
            key = f"{trace.batch}|{trace.trace_id}"
            if labels.get("engagement_class") in ("cross_domain_execution", "bounded_engagement_resisted"):
                e, x = labels.get("engagement_onset"), labels.get("execution_onset")
                primary[key] = None if e is None else int(e)
                secondary[key] = None if x is None else int(x)
            else:
                primary[key] = None
                secondary[key] = None
        _H384_CACHE = (primary, secondary)
    return _H384_CACHE


# ------------------------------------------------------------------ hit logic
def anchor_hit(alarm_ends, last_end, anchor, band=0):
    """Replicates trm3.anchor_hits."""
    limit = anchor - band
    pre = [e for e in alarm_ends if e < limit]
    eligible = [e for e in alarm_ends if e >= limit]
    first = eligible[0] if eligible else None
    latency = None if first is None else max(0, first - anchor)
    hit = (not pre) and first is not None
    block = {"pre": bool(pre), "first": first, "latency": latency, "hit_final": hit}
    for h in RECALL_HORIZONS:
        block[f"hit_plus_{h}"] = bool(hit and latency is not None and latency <= h)
    return block


def recall_block(blocks):
    n = len(blocks)
    out = {"positive_count": n}
    for h in RECALL_HORIZONS:
        k = sum(1 for b in blocks.values() if b[f"hit_plus_{h}"])
        out[f"r{h}"] = (k, n)
    out["final"] = (sum(1 for b in blocks.values() if b["hit_final"]), n)
    out["pre"] = (sum(1 for b in blocks.values() if b["pre"]), n)
    lat = sorted(b["latency"] for b in blocks.values() if b["hit_final"])
    out["latencies"] = lat
    return out


def positives_of(summaries, anchors):
    return [
        s for s in summaries
        if anchors.get(s["key"]) is not None
        and (s["arm_class"] in ("drift", "resist") or s["behaviour_class"] in ("execution", "bounded"))
    ]


def far_block(summaries, positives_keys=(), excluded=()):
    normals = [s for s in summaries if s["arm_class"] in ("clean", "benign") and s["key"] not in set(excluded)]
    clean = [s for s in normals if s["arm_class"] == "clean"]
    benign = [s for s in normals if s["arm_class"] == "benign"]
    groups = defaultdict(list)
    for s in normals:
        groups[s["pair_group_id"]].append(s)
    halves = {}
    for h in (0, 1):
        rows = [s for s in normals if s["calibration_half"] == h]
        halves[h] = (sum(1 for r in rows if r["alarm"]), len(rows))
    return {
        "clean": (sum(1 for s in clean if s["alarm"]), len(clean)),
        "benign": (sum(1 for s in benign if s["alarm"]), len(benign)),
        "pooled": (sum(1 for s in normals if s["alarm"]), len(normals)),
        "matched_group": (sum(1 for r in groups.values() if any(x["alarm"] for x in r)), len(groups)),
        "halves": halves,
    }


# ------------------------------------------------------------------ p streams
def load_streams(cell, column):
    """key -> list of (end, p_S, p_M, p_J, p_fused) over IN-HORIZON endpoints."""
    streams = defaultdict(list)
    for row in read_jsonl(L.CELLS / cell / "outputs.jsonl"):
        if row.get("calibration_column") != column:
            continue
        if row.get("horizon_censored"):
            continue
        streams[row["key"]].append(
            (row["end"], row["p_S"], row["p_M"], row["p_J"], row["p_fused"])
        )
    for k in streams:
        streams[k].sort()
    return streams


def alarms_from_stream(stream, channel_index, alpha):
    return [e for e, *ps in stream if ps[channel_index] <= alpha + 1e-12]


def alarms_trunc_equal(mine, frozen):
    """Frozen ``alarm_ends`` are serialized with a 64-entry cap (trm3.py:1478)."""
    return mine[:64] == frozen[:64] and (len(frozen) < 64 or len(mine) >= 64)
