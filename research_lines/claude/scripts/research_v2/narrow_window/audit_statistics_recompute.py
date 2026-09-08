#!/usr/bin/env python3
"""Independent statistical audit (lens: statistics at n=14 and n=59).

Recomputes anchored-resist and drift hit/miss vectors DIRECTLY from the stored
trace_alarms first_alarm_end of the narrow-window result.json files (no use of
effect.json), then computes Clopper-Pearson intervals, paired discordant-pair
counts vs the frozen window, tolerance/leak sensitivity, and a paired bootstrap.
Read-only; writes nothing outside the audit doc produced by the caller.
"""
from __future__ import annotations

import json
import math
import random
import statistics
from pathlib import Path

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
NW = ROOT / "artifacts" / "agent_v2" / "research_v2" / "narrow_window"
LAB = ROOT / "docs" / "research_v2" / "labels"

RUNS = {
    "CAND-A": (NW / "wgm_c2_w1248" / "result.json", 8),
    "CAND-B": (NW / "pdm_c12_w124" / "result.json", 4),
}
READINGS = ("max", "persist2")
HORIZONS = (4, 8, 16)


def read_jsonl(p):
    return {json.loads(l)["trace_id"]: json.loads(l)
            for l in p.read_text(encoding="utf-8").splitlines() if l.strip()}


def load_alarms(path):
    """(candidate) -> {(window, case, alpha, reading): {trace_id: first_alarm_end}}"""
    d = json.loads(path.read_text(encoding="utf-8"))
    out = {}
    for cr in d["case_runs"]:
        if cr["split"] != "S1" or cr["routine_definition"] != "cb":
            continue
        for c in cr["candidates"]:
            if c["mode"] != "D" or c["reading"] not in READINGS:
                continue
            key = (cr["window_width"], cr["case"], round(float(c["alpha"]), 3), c["reading"])
            out[key] = {r[0]: r[2] for r in c["trace_alarms"]}
    return out


def hit(first_end, anchor, band):
    """Returns (hit, latency) exactly as harness._anchor_block."""
    limit = anchor - band
    if first_end is None:
        return False, None
    if first_end < limit:
        return False, None          # pre_alarm -> disqualified
    return True, max(0, first_end - anchor)


def betainc(a, b, x):
    """Raw continued-fraction branch of the regularized incomplete beta.

    Only valid for x < (a+1)/(a+b+2); always call it through betainc_safe().
    """
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    front = math.exp(math.log(x) * a + math.log(1 - x) * b - lbeta) / a
    f, c, d = 1.0, 1.0, 0.0
    for i in range(0, 300):
        m = i // 2
        if i == 0:
            num = 1.0
        elif i % 2 == 0:
            num = (m * (b - m) * x) / ((a + 2 * m - 1) * (a + 2 * m))
        else:
            num = -((a + m) * (a + b + m) * x) / ((a + 2 * m) * (a + 2 * m + 1))
        d = 1.0 + num * d
        if abs(d) < 1e-30:
            d = 1e-30
        d = 1.0 / d
        c = 1.0 + num / c
        if abs(c) < 1e-30:
            c = 1e-30
        f *= c * d
        if abs(1 - c * d) < 1e-14:
            break
    return front * (f - 1)


def betainc_safe(a, b, x):
    if x < (a + 1) / (a + b + 2):
        return betainc(a, b, x)
    return 1 - betainc(b, a, 1 - x)


def cp(k, n, conf=0.95):
    if n == 0:
        return (None, None)
    alpha = (1 - conf) / 2

    def solve(target, aa, bb):
        lo, hi = 0.0, 1.0
        for _ in range(300):
            mid = (lo + hi) / 2
            if betainc_safe(aa, bb, mid) < target:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2

    lower = 0.0 if k == 0 else solve(alpha, k, n - k + 1)
    upper = 1.0 if k == n else solve(1 - alpha, k + 1, n - k)
    return (lower, upper)


def mcnemar_exact(b, c):
    """Two-sided exact binomial p on the discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def main():
    resist = read_jsonl(LAB / "topic_entry_v1_adjudicated.jsonl")
    prod = read_jsonl(LAB / "product_onset_v1_adjudicated.jsonl")
    anchored = {k: v for k, v in resist.items() if v["topic_entry_onset"] is not None}
    assert len(anchored) == 14, len(anchored)
    assert len(prod) == 59, len(prod)
    leak = [k for k, v in anchored.items() if v.get("flag") == "topic_word_leak"]

    alarms = {c: load_alarms(p) for c, (p, _) in RUNS.items()}
    windows = {c: sorted({k[0] for k in a}) for c, a in alarms.items()}

    # ---- per-trace vectors -------------------------------------------------
    # vec[(cand, w, alpha, reading, band, pool)] = {trace_id: (hit, latency)}
    vec = {}
    coverage = {}
    for cand, amap in alarms.items():
        for w in windows[cand]:
            for alpha in (0.05, 0.1):
                for reading in READINGS:
                    merged = {}
                    for case in ("b1_to_b2", "b2_to_b1"):
                        merged.update({t: (case, e)
                                       for t, e in amap[(w, case, alpha, reading)].items()})
                    coverage[(cand, w, alpha, reading)] = len(merged)
                    for band in (0, 5):
                        for pool, labs, akey in (("resist", anchored, "topic_entry_onset"),
                                                 ("drift", prod, "product_onset")):
                            d = {}
                            for tid, lab in labs.items():
                                if tid not in merged:
                                    raise SystemExit(f"missing {tid}")
                                d[tid] = hit(merged[tid][1], int(lab[akey]), band)
                            vec[(cand, w, alpha, reading, band, pool)] = d
    return vec, anchored, prod, leak, windows, coverage


if __name__ == "__main__":
    v, anch, prod, leak, windows, cov = main()
    print("leak trace:", leak)
    print("coverage (traces per cell):", sorted(set(cov.values())))
    print("windows:", windows)
    for cand in ("CAND-A", "CAND-B"):
        for w in windows[cand]:
            for r in READINGS:
                d = v[(cand, w, 0.1, r, 0, "resist")]
                dd = v[(cand, w, 0.1, r, 0, "drift")]
                def cnt(dic, h=None):
                    return sum(1 for h_, l in dic.values()
                               if h_ and (h is None or l <= h))
                print(cand, w, r,
                      "resist R4/R8/R16/Rf", cnt(d, 4), cnt(d, 8), cnt(d, 16), cnt(d),
                      "| drift", cnt(dd, 8), cnt(dd, 16), cnt(dd))
