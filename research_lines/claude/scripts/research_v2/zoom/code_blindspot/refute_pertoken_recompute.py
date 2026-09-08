#!/usr/bin/env python3
"""REFUTER: independent recomputation of the per-token score-stream lens.

Rebuilds mode-D (position-bucket z + two-half conformal thresholds) from the frozen
narrow-window result.json files with its OWN numpy implementation (no import of the
colleague's scripts, no import of research_v2.harness for the statistics), then
recomputes the numbers that carry the claim.

Read-only.  Writes one cache under
artifacts/agent_v2/research_v2/zoom_code_blindspot/refute_pertoken_cache.json
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
sys.path.insert(0, str(ROOT / "src"))
from research_v2 import io as rio  # noqa: E402

ART = ROOT / "artifacts" / "agent_v2" / "research_v2"
NW = ART / "narrow_window"
OUT = ART / "zoom_code_blindspot"
LABELS = ROOT / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"

RUNS = {
    "CAND-A": (NW / "wgm_c2_w1248" / "result.json", [1, 2, 4, 8]),
    "CAND-B": (NW / "pdm_c12_w124" / "result.json", [1, 2, 4]),
}
ALPHA = 0.10
BUCKET = 32
MIN_BUCKET_TRACES = 30
VAR_FLOOR = 1e-6


# ---------------------------------------------------------------- my own mode-D
def fit_buckets(streams):
    """streams: list of (scores[np], ends[np]).  Returns (mu[], sd[], cap)."""
    streams = [(s, e) for s, e in streams if len(e)]
    raw_max = max(int((e // BUCKET).max()) for _, e in streams)

    def trace_count(minimum):
        return sum(1 for _, e in streams if bool(((e // BUCKET) >= minimum).any()))

    cap = raw_max
    while cap > 0 and trace_count(cap) < MIN_BUCKET_TRACES:
        cap -= 1
    mus, sds = [], []
    for b in range(cap + 1):
        vals = np.concatenate(
            [s[np.minimum(e // BUCKET, cap) == b] for s, e in streams]
        )
        contributing = sum(
            1 for s, e in streams if bool((np.minimum(e // BUCKET, cap) == b).any())
        )
        if b > 0 and (contributing < MIN_BUCKET_TRACES or vals.size < 2):
            mus.append(mus[-1])
            sds.append(sds[-1])
        else:
            mus.append(float(vals.mean()))
            sds.append(float(vals.std(ddof=1)) + VAR_FLOOR if vals.size > 1 else 1.0)
    return np.array(mus), np.array(sds), cap


def standardize(scores, ends, mu, sd, cap):
    idx = np.minimum(ends // BUCKET, cap)
    return (scores - mu[idx]) / sd[idx]


def conformal(maxima, alpha=ALPHA):
    ordered = sorted(float(v) for v in maxima)
    rank = min(math.ceil((len(ordered) + 1) * (1.0 - alpha)), len(ordered))
    return ordered[rank - 1]


def persist2(z, ends):
    """min of two adjacent (contiguous-end) windows; returns (stat, end)."""
    out_z, out_e = [], []
    for i in range(1, len(z)):
        if ends[i] == ends[i - 1] + 1:
            out_z.append(min(z[i], z[i - 1]))
            out_e.append(ends[i])
    return np.array(out_z), np.array(out_e, dtype=int)


def main():
    batches = rio.load_core()
    traces = {t.trace_id: t for v in batches.values() for t in v}
    meta = {
        t.trace_id: {
            "batch": b,
            "arm": rio.arm_class(t),
            "positive": bool(t.positive),
            "pair_group_id": t.pair_group_id,
            "domain": t.domain,
            "T": int(t.token_ids.numel()),
        }
        for b, v in batches.items()
        for t in v
    }
    labels = {}
    for line in LABELS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            labels[r["trace_id"]] = r

    # S1 cases: name -> target batch
    cases = {"b1_to_b2": "b2", "b2_to_b1": "b1"}
    payload = {"thresholds": {}, "z": {}, "meta": meta, "labels": labels}
    thr_check = []

    for cand, (path, widths) in RUNS.items():
        res = json.loads(path.read_text(encoding="utf-8"))
        by_key = {(cr["case"], cr["window_width"]): cr for cr in res["case_runs"]}
        for w in widths:
            for case, tbatch in cases.items():
                cr = by_key[(case, w)]
                streams = {
                    tid: (
                        np.asarray(blk["scores"], dtype=np.float64),
                        np.asarray(blk["ends"], dtype=np.int64),
                    )
                    for tid, blk in cr["score_streams"].items()
                }
                target = [t.trace_id for t in batches[tbatch]]
                assert set(target) == set(streams), (cand, w, case)
                groups = sorted({meta[t]["pair_group_id"] for t in target})
                half = {g: i % 2 for i, g in enumerate(groups)}
                routine = [
                    t
                    for t in target
                    if (not meta[t]["positive"])
                    and traces[t].arm in rio.ROUTINE_CB_ARMS
                ]
                for cal_half in (0, 1):
                    cal = [t for t in routine if half[meta[t]["pair_group_id"]] == cal_half]
                    mu, sd, cap = fit_buckets([streams[t] for t in cal])
                    maxima = [
                        float(standardize(*streams[t], mu, sd, cap).max()) for t in cal
                    ]
                    thr_max = conformal(maxima)
                    p2max = []
                    for t in cal:
                        z = standardize(*streams[t], mu, sd, cap)
                        pz, _ = persist2(z, streams[t][1])
                        p2max.append(float(pz.max()))
                    thr_p2 = conformal(p2max)
                    stored = cr["calibration"]["D"]["halves"][str(cal_half)]["thresholds"]
                    thr_check.append(
                        {
                            "cand": cand, "w": w, "case": case, "half": cal_half,
                            "max_mine": thr_max,
                            "max_stored": float(stored["max|alpha0.1"]["threshold"]),
                            "p2_mine": thr_p2,
                            "p2_stored": float(stored["persist2|alpha0.1"]["threshold"]),
                        }
                    )
                    key = f"{cand}|{w}|{case}|{cal_half}"
                    payload["thresholds"][key] = {"max": thr_max, "persist2": thr_p2}
                    for t in target:
                        if half[meta[t]["pair_group_id"]] == cal_half:
                            continue  # calibration half: not evaluated with this threshold
                        z = standardize(*streams[t], mu, sd, cap)
                        payload["z"].setdefault(f"{cand}|{w}", {})[t] = {
                            "z": [round(float(v), 6) for v in z],
                            "ends": [int(v) for v in streams[t][1]],
                            "thr_max": thr_max,
                            "thr_p2": thr_p2,
                            "case": case,
                            "cal_half": cal_half,
                        }
    payload["thr_check"] = thr_check
    dev = max(abs(r["max_mine"] - r["max_stored"]) for r in thr_check)
    dev2 = max(abs(r["p2_mine"] - r["p2_stored"]) for r in thr_check)
    print(f"threshold reproduction: max dev {dev:.3e} (max reading), {dev2:.3e} (persist2), n={len(thr_check)}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "refute_pertoken_cache.json").write_text(json.dumps(payload))
    print("wrote", OUT / "refute_pertoken_cache.json")


if __name__ == "__main__":
    main()
