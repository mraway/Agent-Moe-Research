#!/usr/bin/env python3
"""Per-token tables (onset-16 .. onset+48) in threshold units for the 8 programming
drifts and their 8 matched other-domain drifts, plus whole-trace profiles.

Read-only.  Prints markdown; nothing is written outside the cache directory.
"""
from __future__ import annotations

import json
import statistics as st
from collections import Counter
from pathlib import Path

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
OUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
NW = ROOT / "artifacts" / "agent_v2" / "research_v2" / "narrow_window"

CACHE = json.loads((OUT / "pertoken.json").read_text(encoding="utf-8"))
ANA = json.loads((OUT / "pertoken_analysis.json").read_text(encoding="utf-8"))
META = CACHE["meta"]
RUNS = CACHE["runs"]
WIDTHS = {"CAND-A": [1, 2, 4, 8], "CAND-B": [1, 2, 4]}

THR = {}
for cand, sub in (("CAND-A", "wgm_c2_w1248"), ("CAND-B", "pdm_c12_w124")):
    res = json.loads((NW / sub / "result.json").read_text(encoding="utf-8"))
    for cr in res["case_runs"]:
        w = cr["window_width"]
        if w not in WIDTHS[cand]:
            continue
        for h in ("0", "1"):
            b = cr["calibration"]["D"]["halves"][h]["thresholds"]
            THR[(cand, w, cr["case"], int(h))] = float(b["max|alpha0.1"]["threshold"])


def sh(t):
    return "-".join(t.split("-")[:3])


def ratio_map(cand, w, tid):
    blk = RUNS[cand][str(w)][tid]
    thr = THR[(cand, w, blk["case"], blk["cal_half"])]
    return {e: v / thr for v, e in zip(blk["z"], blk["ends"])}, thr


CLS_MARK = {
    "keyword": "KW", "identifier": "ID", "symbol": "SY", "newline": "NL",
    "prose": "pr", "fence": "FN", "number": "NU", "space": "sp", "other": "ot",
}


def token_table(tid, lo_off=-16, hi_off=48):
    m = META[tid]
    o = m["product_onset"]
    lines = []
    hdr = "| off | tok | cls | A1 | A2 | A4 | A8 | B1 | B2 | B4 |"
    lines.append(hdr)
    lines.append("|" + "---|" * 10)
    maps = {}
    for cand in WIDTHS:
        for w in WIDTHS[cand]:
            maps[(cand, w)] = ratio_map(cand, w, tid)[0]
    for i in range(max(0, o + lo_off), min(m["T"], o + hi_off)):
        tok = m["pieces"][i].replace("\n", "\\n").replace("|", "\\|")
        cls = CLS_MARK.get(m["classes"][i], m["classes"][i])
        cells = []
        for cand in ("CAND-A", "CAND-B"):
            for w in WIDTHS[cand]:
                v = maps[(cand, w)].get(i)
                cells.append("." if v is None else f"{v:.2f}" + ("*" if v >= 1.0 else ""))
        lines.append(f"| {i - o:+d} | `{tok}` | {cls} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def whole_trace(tid):
    m = META[tid]
    rows = {}
    for cand in WIDTHS:
        for w in WIDTHS[cand]:
            rm, thr = ratio_map(cand, w, tid)
            allv = list(rm.values())
            code_idx = [i for i in rm if m["in_code"][i]]
            pre_idx = [i for i in rm if i < m["product_onset"]]
            post_prose = [
                i for i in rm
                if i >= m["product_onset"] and not m["in_code"][i] and i not in m["fence_tok"]
            ]
            exc = sorted(i for i, v in rm.items() if v >= 1.0)
            rows[f"{cand}|w{w}"] = {
                "trace_max_ratio": round(max(allv), 3),
                "argmax_off": max(rm, key=lambda i: rm[i]) - m["product_onset"],
                "argmax_cls": m["classes"][max(rm, key=lambda i: rm[i])],
                "n_exceed_trace": len(exc),
                "exceed_offsets": [i - m["product_onset"] for i in exc],
                "code_tokens": len(code_idx),
                "code_median_ratio": round(st.median([rm[i] for i in code_idx]), 3) if code_idx else None,
                "code_max_ratio": round(max([rm[i] for i in code_idx]), 3) if code_idx else None,
                "code_exceed": sum(1 for i in code_idx if rm[i] >= 1.0),
                "pre_onset_median": round(st.median([rm[i] for i in pre_idx]), 3) if pre_idx else None,
                "post_onset_prose_median": round(st.median([rm[i] for i in post_prose]), 3) if post_prose else None,
            }
    return rows


def main():
    pairs = ANA["matched_pairs"]
    doc = []
    profiles = {}
    for p in pairs:
        for tid in (p["code"], p["control"]):
            profiles[tid] = whole_trace(tid)
    (OUT / "pertoken_whole_trace.json").write_text(json.dumps(profiles, indent=1), encoding="utf-8")

    for p in pairs:
        t = p["code"]
        doc.append(f"\n#### {sh(t)}  (`{t}`)  product_onset={p['code_onset']} "
                   f"evidence_onset={META[t]['evidence_onset']} class={META[t]['product_class']}\n")
        doc.append(token_table(t))
    (OUT / "pertoken_code_tables.md").write_text("\n".join(doc), encoding="utf-8")

    doc2 = []
    for p in pairs:
        c = p["control"]
        doc2.append(f"\n#### {sh(c)} ({p['control_domain']}) product_onset={p['control_onset']}\n")
        doc2.append(token_table(c))
    (OUT / "pertoken_control_tables.md").write_text("\n".join(doc2), encoding="utf-8")
    print("wrote", OUT / "pertoken_code_tables.md", OUT / "pertoken_control_tables.md", OUT / "pertoken_whole_trace.json")

    # whole-trace summary print
    print("\n== whole-trace profile, CAND-A w=1 / w=8, CAND-B w=1 / w=4")
    for p in pairs:
        for tag, tid in (("CODE", p["code"]), ("ctrl", p["control"])):
            pr = profiles[tid]
            print(f"{tag} {sh(tid):14s} " + "  ".join(
                f"{k}: max={pr[k]['trace_max_ratio']:6.2f}@{pr[k]['argmax_off']:+4d}({pr[k]['argmax_cls'][:2]}) "
                f"nexc={pr[k]['n_exceed_trace']:3d} codeMed={pr[k]['code_median_ratio']} codeExc={pr[k]['code_exceed']}"
                for k in ("CAND-A|w1", "CAND-B|w1")))


if __name__ == "__main__":
    main()
