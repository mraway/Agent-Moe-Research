#!/usr/bin/env python3
"""Print the markdown tables that go into docs/research_v2/zoom/code_blindspot/pertoken_code.md."""
from __future__ import annotations

import json
import statistics as st
from pathlib import Path

OUT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363"
           "/artifacts/agent_v2/research_v2/zoom_code_blindspot")
A = json.loads((OUT / "pertoken_analysis.json").read_text())
S = json.loads((OUT / "pertoken_sustain.json").read_text())
W = json.loads((OUT / "pertoken_whole_trace.json").read_text())
C = json.loads((OUT / "pertoken.json").read_text())
M = C["meta"]


def sh(t):
    return "-".join(t.split("-")[:3])


pairs = A["matched_pairs"]

print("### T1 matched pairs\n")
print("| # | code trace | product_onset | matched other-domain drift | domain | onset | |Δonset| |")
print("|---|---|---|---|---|---|---|")
for i, p in enumerate(pairs, 1):
    print(f"| {i} | {sh(p['code'])} | {p['code_onset']} | {sh(p['control'])} | "
          f"{p['control_domain']} | {p['control_onset']} | {abs(p['code_onset']-p['control_onset'])} |")

for key in ("CAND-A|w1", "CAND-A|w8", "CAND-B|w1", "CAND-B|w4"):
    print(f"\n### T2 {key} — onset..onset+48\n")
    print("| code trace | ons | n | #>thr | frac | maxR | medR | pct-routine | run>=1 || control | #>thr | frac | maxR | medR | pct-routine |")
    print("|" + "---|" * 15)
    for p in pairs:
        t, c = p["code"], p["control"]
        s = A["q1"][key]["programming"][t]
        cs = A["q1"][key]["control"][c]
        su, cu = S[key]["code"][t], S[key]["control"][c]
        print(f"| {sh(t)} | {p['code_onset']} | {s['n_tokens']} | {s['n_exceed']} | "
              f"{s['frac_exceed']:.3f} | {s['max_ratio']:.2f} | {s['median_ratio']:+.3f} | "
              f"{su['routine_percentile_of_median']:.1f} | {su['longest_run_ge_1.0']} || "
              f"{sh(c)} | {cs['n_exceed']} | {cs['frac_exceed']:.3f} | {cs['max_ratio']:.2f} | "
              f"{cs['median_ratio']:+.3f} | {cu['routine_percentile_of_median']:.1f} |")
    pe = sum(x["n_exceed"] for x in A["q1"][key]["programming"].values())
    pn = sum(x["n_tokens"] for x in A["q1"][key]["programming"].values())
    ce = sum(x["n_exceed"] for x in A["q1"][key]["control"].values())
    cn = sum(x["n_tokens"] for x in A["q1"][key]["control"].values())
    rb = A["routine_baseline"][key]["pooled"]
    print(f"| **pooled** |  | {pn} | {pe} | **{pe/pn:.4f}** |  |  |  |  || **pooled** | {ce} | "
          f"**{ce/cn:.4f}** |  |  | routine {rb:.4f} |")

print("\n### T3 non-programming drifts by domain (median over traces), onset..+48\n")
for key in ("CAND-A|w1", "CAND-A|w8", "CAND-B|w1", "CAND-B|w4"):
    q = A["q1"][key]["other_by_domain"]
    print(f"\n**{key}**\n")
    print("| domain | n | median frac>thr | median medR | median maxR | maxR range |")
    print("|---|---|---|---|---|---|")
    for dom, rows in sorted(q.items()):
        fr = [r["frac_exceed"] for r in rows]
        mr = [r["median_ratio"] for r in rows]
        xr = [r["max_ratio"] for r in rows]
        print(f"| {dom} | {len(rows)} | {st.median(fr):.3f} | {st.median(mr):+.3f} | "
              f"{st.median(xr):.3f} | {min(xr):.2f}–{max(xr):.2f} |")
    prog = list(A["q1"][key]["programming"].values())
    allo = [r for rows in q.values() for r in rows]
    print(f"| **all non-programming** | {len(allo)} | {st.median([r['frac_exceed'] for r in allo]):.3f} | "
          f"{st.median([r['median_ratio'] for r in allo]):+.3f} | "
          f"{st.median([r['max_ratio'] for r in allo]):.3f} | |")
    print(f"| **programming** | 8 | {st.median([r['frac_exceed'] for r in prog]):.3f} | "
          f"{st.median([r['median_ratio'] for r in prog]):+.3f} | "
          f"{st.median([r['max_ratio'] for r in prog]):.3f} | "
          f"{min(r['max_ratio'] for r in prog):.2f}–{max(r['max_ratio'] for r in prog):.2f} |")

print("\n### T4 whole-trace (all 192 tokens) exceedance counts\n")
print("| trace | A|w1 max@off(cls) | A|w1 #exc | A|w8 #exc | B|w1 max@off | B|w1 #exc | B|w4 #exc |")
print("|---|---|---|---|---|---|---|")
for p in pairs:
    for tag, tid in (("CODE ", p["code"]), ("ctrl ", p["control"])):
        w = W[tid]
        print(f"| {tag}{sh(tid)} | {w['CAND-A|w1']['trace_max_ratio']:.2f}@{w['CAND-A|w1']['argmax_off']:+d}"
              f"({w['CAND-A|w1']['argmax_cls'][:2]}) | {w['CAND-A|w1']['n_exceed_trace']} | "
              f"{w['CAND-A|w8']['n_exceed_trace']} | {w['CAND-B|w1']['trace_max_ratio']:.2f}@"
              f"{w['CAND-B|w1']['argmax_off']:+d} | {w['CAND-B|w1']['n_exceed_trace']} | "
              f"{w['CAND-B|w4']['n_exceed_trace']} |")

print("\n### T5 Q4 transition: pre-onset / announcement prose / fence / in-code (threshold units, median)\n")
for key in ("CAND-A|w1", "CAND-A|w8", "CAND-B|w1", "CAND-B|w4"):
    print(f"\n**{key}**\n")
    print("| trace | pre(-16..-1) med | announce-prose med (n) | fence tok | in-code med (n) | in-code max | first in-code off |")
    print("|---|---|---|---|---|---|---|")
    for p in pairs:
        t = p["code"]
        v = A["q4"][key][t]
        def med(x):
            return "n/a" if x is None else "%+.3f" % x["median"]

        def medn(x):
            return "n/a" if x is None else "%+.3f (%d)" % (x["median"], x["n"])

        def mx(x):
            return "n/a" if x is None else "%+.2f" % x["max"]

        print("| %s | %s | %s | %s | %s | %s | %s |" % (
            sh(t), med(v["pre_onset(-16..-1)"]), medn(v["onset_prose(not in code)"]),
            mx(v["fence_tokens"]), medn(v["in_code"]), mx(v["in_code"]),
            v["first_code_token"]))

print("\n### T6 run-length histogram of exceedance runs in onset..+48\n")
print("| stream | programming runs (len:count) | max | 8 controls runs | max |")
print("|---|---|---|---|---|")
for key in ("CAND-A|w1", "CAND-A|w2", "CAND-A|w4", "CAND-A|w8", "CAND-B|w1", "CAND-B|w2", "CAND-B|w4"):
    q = A["q3"][key]
    print(f"| {key} | {q['prog_run_hist'] or '{}'} | {q['prog_max_run']} | "
          f"{q['control_run_hist']} | {q['control_max_run']} |")

print("\n### T7 which tokens exceed at w=1 (all of them, programming)\n")
for cand in ("CAND-A", "CAND-B"):
    print(f"\n**{cand}|w1**\n")
    print("| trace | off | token | class | ratio |")
    print("|---|---|---|---|---|")
    any_row = False
    for t, v in A["q2"][f"{cand}|w1"]["per_trace"].items():
        for e in v["exceed_tokens"]:
            any_row = True
            print(f"| {sh(t)} | {e['off']:+d} | `{e['tok']}` | {e['cls']} | {e['ratio']} |")
    if not any_row:
        print("| — | | | | |")
    print(f"\npooled class counts (exceed/total) programming: {A['q2'][f'{cand}|w1']['programming_pooled']}")
    print(f"\npooled class counts (exceed/total) 8 controls : {A['q2'][f'{cand}|w1']['control_pooled']}")

print("\n### T8 evidence_onset variant (onset := evidence_onset)\n")
print("| stream | " + " | ".join(sh(t) for t in A["q1_evidence_onset"]["CAND-A|w1"]) + " |")
print("|" + "---|" * 9)
for key in ("CAND-A|w1", "CAND-A|w8", "CAND-B|w1", "CAND-B|w4"):
    row = A["q1_evidence_onset"][key]
    print(f"| {key} #>thr (maxR) | " + " | ".join(
        f"{v['n_exceed']} ({v['max_ratio']:.2f})" for v in row.values()) + " |")
