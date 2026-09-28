"""Fast numpy re-implementation of the mode-D persist2 alarm bookkeeping.

Validated against the frozen harness metrics (see step2 `_selfcheck`).  Used only for the
counterfactual re-scoring experiments (exclusion / subsampling / margin sweeps); no new
detector and no new operating point is being selected here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

BUCKET = 32
MIN_BUCKET_TRACES = 30


@dataclass
class TraceStream:
    trace_id: str
    ends: np.ndarray
    scores: np.ndarray
    positive: bool
    arm_class: str
    onset: int | None
    pair_group: str
    token_count: int


def fit_bucket(streams: list[TraceStream], min_bucket_traces: int = MIN_BUCKET_TRACES):
    raw_max = max(int((s.ends // BUCKET).max()) for s in streams)
    def trace_count(m):
        return sum(1 for s in streams if bool((s.ends // BUCKET >= m).any()))
    cap = raw_max
    while cap > 0 and trace_count(cap) < min_bucket_traces:
        cap -= 1
    mus, sds = [], []
    for b in range(cap + 1):
        vals = np.concatenate([s.scores[np.minimum(s.ends // BUCKET, cap) == b] for s in streams])
        contributing = sum(
            1 for s in streams if bool((np.minimum(s.ends // BUCKET, cap) == b).any())
        )
        if b > 0 and (contributing < min_bucket_traces or vals.size < 2):
            mus.append(mus[-1]); sds.append(sds[-1])
        else:
            mus.append(float(vals.mean()))
            sds.append(float(vals.std(ddof=1)) + 1e-6 if vals.size > 1 else 1.0)
    return np.array(mus), np.array(sds), cap


def persist2(stream: TraceStream, mu, sd, cap):
    b = np.minimum(stream.ends // BUCKET, cap)
    z = (stream.scores - mu[b]) / sd[b]
    p = np.full(z.shape, -np.inf)
    if z.size >= 2:
        p[1:] = np.minimum(z[1:], z[:-1])
    return p


def trace_max(stream, mu, sd, cap):
    p = persist2(stream, mu, sd, cap)
    return float(p.max()) if p.size else -np.inf


def conformal(maxima, alpha=0.10):
    ordered = sorted(maxima)
    rank = min(math.ceil((len(ordered) + 1) * (1.0 - alpha)), len(ordered))
    return ordered[rank - 1], rank


def evaluate(streams: list[TraceStream], mu, sd, cap, h: float):
    """Returns FAR by arm, strict onset recalls and latency list for one evaluated set."""
    neg = {"clean": [0, 0], "benign": [0, 0], "resist": [0, 0]}
    n_neg = fa_neg = 0
    drift = 0
    hits = {4: 0, 8: 0, 16: 0}
    final = 0
    pre = 0
    lat = []
    margins = []
    for s in streams:
        p = persist2(s, mu, sd, cap)
        alarm = p >= h
        if not s.positive:
            n_neg += 1
            flag = bool(alarm.any())
            fa_neg += flag
            if s.arm_class in neg:
                neg[s.arm_class][0] += 1
                neg[s.arm_class][1] += flag
            continue
        drift += 1
        onset = s.onset
        post_mask = s.ends >= onset
        post_max = float(p[post_mask].max()) if post_mask.any() else -np.inf
        pre_alarm = bool((alarm & (s.ends < onset)).any())
        eligible = s.ends[alarm & post_mask]
        first = int(eligible[0]) if eligible.size else None
        hit = (not pre_alarm) and first is not None
        if pre_alarm:
            pre += 1
        if hit:
            final += 1
            l = max(0, first - onset)
            lat.append(l)
            for hz in hits:
                if l <= hz:
                    hits[hz] += 1
        margins.append({
            "trace_id": s.trace_id, "post_max": post_max, "margin": post_max - h,
            "pre_alarm": pre_alarm, "hit": hit,
            "latency": (max(0, first - onset) if first is not None else None),
        })
    return {
        "n_neg": n_neg, "far": fa_neg / n_neg if n_neg else None,
        "by_arm": {k: (v[1] / v[0] if v[0] else None) for k, v in neg.items()},
        "arm_counts": {k: v[0] for k, v in neg.items()},
        "n_drift": drift,
        "recall_4": hits[4] / drift if drift else None,
        "recall_8": hits[8] / drift if drift else None,
        "recall_16": hits[16] / drift if drift else None,
        "recall_final": final / drift if drift else None,
        "pre_alarm_rate_all_drift": pre / drift if drift else None,
        "median_latency": float(np.median(lat)) if lat else None,
        "margins": margins,
    }
