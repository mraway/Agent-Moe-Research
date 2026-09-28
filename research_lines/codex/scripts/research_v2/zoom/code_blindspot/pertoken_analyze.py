#!/usr/bin/env python3
"""Analysis of the per-token score stream on the 8 programming drifts.

Reads the cache written by pertoken_extract.py plus the stored `max`-reading conformal
thresholds of the two narrow-window runs.  Read-only; writes JSON summaries into
artifacts/agent_v2/research_v2/zoom_code_blindspot/ and prints the tables that go into
docs/research_v2/zoom/code_blindspot/pertoken_code.md .
"""
from __future__ import annotations

import json
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
ART = ROOT / "artifacts" / "agent_v2" / "research_v2"
OUT = ART / "zoom_code_blindspot"
NW = ART / "narrow_window"

CACHE = json.loads((OUT / "pertoken.json").read_text(encoding="utf-8"))
RESULTS = {
    "CAND-A": json.loads((NW / "wgm_c2_w1248" / "result.json").read_text(encoding="utf-8")),
    "CAND-B": json.loads((NW / "pdm_c12_w124" / "result.json").read_text(encoding="utf-8")),
}
WIDTHS = {"CAND-A": [1, 2, 4, 8], "CAND-B": [1, 2, 4]}
WINDOW = 48
PRE = 16

# ---- max-reading thresholds (raw per-window z threshold), from the frozen calibration
THR: dict[tuple[str, int, str, int], float] = {}
for cand, res in RESULTS.items():
    for cr in res["case_runs"]:
        w = cr["window_width"]
        if w not in WIDTHS[cand]:
            continue
        for h in ("0", "1"):
            block = cr["calibration"]["D"]["halves"][h]["thresholds"]
            THR[(cand, w, cr["case"], int(h))] = {
                "max": float(block["max|alpha0.1"]["threshold"]),
                "persist2": float(block["persist2|alpha0.1"]["threshold"]),
            }

META = CACHE["meta"]
RUNS = CACHE["runs"]
PROG = [t for t, m in META.items() if m["domain"] == "programming"]
PROG.sort(key=lambda t: META[t]["product_onset"])
OTHER = [t for t, m in META.items() if m["domain"] != "programming"]


def short(tid: str) -> str:
    return "-".join(tid.split("-")[:3])


def stream(cand: str, w: int, tid: str):
    blk = RUNS[cand][str(w)][tid]
    return blk["z"], blk["ends"], blk["case"], blk["cal_half"]


def z_at_token(cand: str, w: int, tid: str):
    """map token index -> z of the window ENDING at that token (None if no such window)."""
    z, ends, case, half = stream(cand, w, tid)
    out = {}
    for v, e in zip(z, ends):
        out[e] = v
    return out, THR[(cand, w, case, half)]


def persist2_at_token(cand: str, w: int, tid: str):
    z, ends, case, half = stream(cand, w, tid)
    out = {}
    for i in range(1, len(z)):
        if ends[i] == ends[i - 1] + 1:
            out[ends[i]] = min(z[i], z[i - 1])
    return out, THR[(cand, w, case, half)]


def span(tid: str, anchor: str = "product_onset"):
    m = META[tid]
    o = m[anchor]
    return o, min(o + WINDOW, m["T"])


# --------------------------------------------------------------- Q1 exceedance rates
def exceed_stats(tid: str, cand: str, w: int, anchor="product_onset"):
    o, hi = span(tid, anchor)
    zt, thr = z_at_token(cand, w, tid)
    pt, _ = persist2_at_token(cand, w, tid)
    idx = [i for i in range(o, hi) if i in zt]
    if not idx:
        return None
    exc = [i for i in idx if zt[i] >= thr["max"]]
    pexc = [i for i in idx if i in pt and pt[i] >= thr["persist2"]]
    ratios = [zt[i] / thr["max"] for i in idx]
    return {
        "n_tokens": len(idx),
        "n_exceed": len(exc),
        "frac_exceed": len(exc) / len(idx),
        "n_persist2_exceed": len(pexc),
        "frac_persist2": len(pexc) / len(idx),
        "max_ratio": max(ratios),
        "median_ratio": st.median(ratios),
        "exceed_idx": exc,
        "idx": idx,
    }


def routine_exceed(cand: str, w: int):
    """Per-token exceedance rate over all routine (clean+benign) traces, pooled."""
    tot = hit = 0
    per_trace = []
    for tid in CACHE["routine"]:
        z, ends, case, half = stream(cand, w, tid)
        thr = THR[(cand, w, case, half)]["max"]
        n = len(z)
        h = sum(1 for v in z if v >= thr)
        tot += n
        hit += h
        per_trace.append(h / n if n else 0.0)
    return {"pooled": hit / tot, "median_trace": st.median(per_trace), "n_tokens": tot}


# --------------------------------------------------------------- matched controls
def matched_controls():
    prog = [(t, META[t]["product_onset"]) for t in PROG]
    pool = [
        (t, META[t]["product_onset"])
        for t in OTHER
        if META[t]["T"] == 192  # all 8 programming traces have T=192
    ]
    pairs = []
    used = set()
    used_dom = Counter()
    for t, o in prog:
        best = None
        for c, co in pool:
            if c in used:
                continue
            key = (abs(co - o), used_dom[META[c]["domain"]], -META[c]["T"])
            if best is None or key < best[0]:
                best = (key, c, co)
        used.add(best[1])
        used_dom[META[best[1]]["domain"]] += 1
        pairs.append((t, best[1], o, best[2]))
    return pairs


# --------------------------------------------------------------- run lengths
def runs_of(exc_idx: list[int]) -> list[int]:
    if not exc_idx:
        return []
    out = []
    cur = 1
    for a, b in zip(exc_idx, exc_idx[1:]):
        if b == a + 1:
            cur += 1
        else:
            out.append(cur)
            cur = 1
    out.append(cur)
    return out


def main() -> None:
    report: dict = {}
    pairs = matched_controls()
    report["matched_pairs"] = [
        {"code": t, "control": c, "code_onset": o, "control_onset": co,
         "control_domain": META[c]["domain"]}
        for t, c, o, co in pairs
    ]

    # ---- routine baselines
    report["routine_baseline"] = {
        f"{cand}|w{w}": routine_exceed(cand, w)
        for cand in WIDTHS for w in WIDTHS[cand]
    }

    # ---- Q1
    q1 = {}
    for cand in WIDTHS:
        for w in WIDTHS[cand]:
            key = f"{cand}|w{w}"
            q1[key] = {"programming": {}, "control": {}, "other_by_domain": defaultdict(list)}
            for tid in PROG:
                q1[key]["programming"][tid] = exceed_stats(tid, cand, w)
            for _t, c, _o, _co in pairs:
                q1[key]["control"][c] = exceed_stats(c, cand, w)
            for tid in OTHER:
                s = exceed_stats(tid, cand, w)
                if s:
                    q1[key]["other_by_domain"][META[tid]["domain"]].append(
                        {"trace": tid, **{k: s[k] for k in
                                          ("frac_exceed", "frac_persist2", "max_ratio",
                                           "median_ratio")}}
                    )
            q1[key]["other_by_domain"] = dict(q1[key]["other_by_domain"])
    report["q1"] = q1

    # ---- evidence_onset variant
    q1e = {}
    for cand in WIDTHS:
        for w in WIDTHS[cand]:
            q1e[f"{cand}|w{w}"] = {
                tid: exceed_stats(tid, cand, w, anchor="evidence_onset") for tid in PROG
            }
    report["q1_evidence_onset"] = q1e

    # ---- Q2 token classes of exceedances (w=1)
    q2 = {}
    for cand in WIDTHS:
        w = 1
        key = f"{cand}|w1"
        cls_tot = Counter()
        cls_exc = Counter()
        per_trace = {}
        for tid in PROG:
            s = exceed_stats(tid, cand, w)
            classes = META[tid]["classes"]
            tt = Counter(classes[i] for i in s["idx"])
            te = Counter(classes[i] for i in s["exceed_idx"])
            cls_tot += tt
            cls_exc += te
            per_trace[tid] = {
                "by_class": {c: [te.get(c, 0), tt[c]] for c in sorted(tt)},
                "exceed_tokens": [
                    {"off": i - META[tid]["product_onset"], "tok": META[tid]["pieces"][i],
                     "cls": classes[i], "ratio": round(
                         z_at_token(cand, w, tid)[0][i] / THR[(cand, w, *stream(cand, w, tid)[2:])]["max"], 3)}
                    for i in s["exceed_idx"]
                ],
            }
        ctl_tot = Counter()
        ctl_exc = Counter()
        for _t, c, _o, _co in pairs:
            s = exceed_stats(c, cand, w)
            classes = META[c]["classes"]
            ctl_tot += Counter(classes[i] for i in s["idx"])
            ctl_exc += Counter(classes[i] for i in s["exceed_idx"])
        q2[key] = {
            "programming_pooled": {c: [cls_exc.get(c, 0), cls_tot[c]] for c in sorted(cls_tot)},
            "control_pooled": {c: [ctl_exc.get(c, 0), ctl_tot[c]] for c in sorted(ctl_tot)},
            "per_trace": per_trace,
        }
    report["q2"] = q2

    # ---- Q3 run lengths
    q3 = {}
    for cand in WIDTHS:
        for w in WIDTHS[cand]:
            key = f"{cand}|w{w}"
            prog_runs, ctl_runs = [], []
            per = {}
            for tid in PROG:
                s = exceed_stats(tid, cand, w)
                r = runs_of(s["exceed_idx"])
                per[tid] = r
                prog_runs += r
            perc = {}
            for _t, c, _o, _co in pairs:
                s = exceed_stats(c, cand, w)
                r = runs_of(s["exceed_idx"])
                perc[c] = r
                ctl_runs += r
            q3[key] = {
                "programming_runs": per,
                "control_runs": perc,
                "prog_run_hist": dict(Counter(prog_runs)),
                "control_run_hist": dict(Counter(ctl_runs)),
                "prog_max_run": max(prog_runs) if prog_runs else 0,
                "control_max_run": max(ctl_runs) if ctl_runs else 0,
            }
    report["q3"] = q3

    # ---- Q4 transition: announcement / fence vs inside code
    q4 = {}
    for cand in WIDTHS:
        for w in WIDTHS[cand]:
            key = f"{cand}|w{w}"
            per = {}
            for tid in PROG:
                m = META[tid]
                o, hi = span(tid)
                zt, thr = z_at_token(cand, w, tid)
                classes = m["classes"]
                incode = m["in_code"]
                fences = [i for i in m["fence_tok"] if o - PRE <= i < hi]
                pre = [i for i in range(max(0, o - PRE), o) if i in zt]
                ann = [i for i in range(o, hi) if i in zt and not incode[i] and i not in m["fence_tok"]]
                ins = [i for i in range(o, hi) if i in zt and incode[i]]
                def agg(idx):
                    if not idx:
                        return None
                    vals = [zt[i] / thr["max"] for i in idx]
                    return {"n": len(idx), "median": round(st.median(vals), 3),
                            "max": round(max(vals), 3),
                            "n_exceed": sum(1 for v in vals if v >= 1.0)}
                per[tid] = {
                    "pre_onset(-16..-1)": agg(pre),
                    "onset_prose(not in code)": agg(ann),
                    "fence_tokens": agg([i for i in fences if i in zt and i >= o]),
                    "in_code": agg(ins),
                    "first_code_token": (min(ins) - o) if ins else None,
                    "first8_in_code": agg(sorted(ins)[:8]),
                }
            q4[key] = per
    report["q4"] = q4

    (OUT / "pertoken_analysis.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print("wrote", OUT / "pertoken_analysis.json")


if __name__ == "__main__":
    main()
