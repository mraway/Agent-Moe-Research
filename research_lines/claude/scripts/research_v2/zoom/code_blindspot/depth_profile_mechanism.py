"""Mechanism side of the depth-profile lens: where the code windows sit relative to the
routine centroid, in which direction they move, and what a trace-level null does to the
one layer that looks good at window level.  Diagnostic only, uses labels.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
CACHE = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot" / "depth_profile" / "depth_profile.json"
L = 16
DOMAINS = ["cooking", "fiction", "general_knowledge", "legal_analysis",
           "mathematics", "poetry", "travel_planning"]


def auc(pos, neg):
    a = np.concatenate([pos, neg]); order = np.argsort(a, kind="mergesort"); sa = a[order]
    ranks = np.empty(len(a)); i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sa[j + 1] == sa[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0; i = j + 1
    return (ranks[:len(pos)].sum() - len(pos) * (len(pos) - 1) / 2.0) / (len(pos) * len(neg))


def main():
    d = json.loads(CACHE.read_text(encoding="utf-8"))
    ref = np.array(d["ref_p"])                       # [16,64]
    gm = {g: np.array(v) for g, v in d["group_mean_probs"].items()}
    wm = {g: {k: np.array(v) for k, v in m.items()} for g, m in d["window_medians"].items()}
    meta = d["window_meta"]
    tp = d["token_profile"]

    # ---------- M1 mean-shift magnitude and direction ----------
    print("## M1 per-layer mean-shift of the router distribution away from the routine centroid")
    print("TV = 0.5*L1(mean_group - routine mean).  cos = cosine similarity of the shift vector")
    print("with the code shift vector at the same layer (routine_held column: cos with code too).")
    print()
    names = ["code_all", "other_all", "routine_held"] + [f"other:{x}" for x in DOMAINS]
    print("| layer | " + " | ".join(f"TV {n}" for n in names[:3]) + " | " +
          " | ".join(f"TV {n.split(':')[-1][:6]}" for n in names[3:]) + " |")
    print("|" + "---|" * (len(names) + 1))
    for l in range(L):
        cells = [f"{0.5*np.abs(gm[n][l]-ref[l]).sum():.4f}" for n in names]
        print(f"| {l} | " + " | ".join(cells) + " |")
    print()
    print("| layer | cos(code, other_all) | cos(code, routine_held) | " +
          " | ".join(f"cos(code,{x[:6]})" for x in DOMAINS) +
          " | mean pairwise cos among other domains |")
    print("|" + "---|" * (4 + len(DOMAINS)))
    for l in range(L):
        c = gm["code_all"][l] - ref[l]
        def cs(v):
            u = v - ref[l]
            return float(c @ u / (np.linalg.norm(c) * np.linalg.norm(u) + 1e-12))
        pair = []
        for i, x in enumerate(DOMAINS):
            for y in DOMAINS[i + 1:]:
                u = gm[f"other:{x}"][l] - ref[l]; v = gm[f"other:{y}"][l] - ref[l]
                pair.append(u @ v / (np.linalg.norm(u) * np.linalg.norm(v) + 1e-12))
        print(f"| {l} | {cs(gm['other_all'][l]):.3f} | {cs(gm['routine_held'][l]):.3f} | " +
              " | ".join(f"{cs(gm[f'other:{x}'][l]):.3f}" for x in DOMAINS) +
              f" | {np.mean(pair):.3f} |")
    print()

    # ---------- M2 routine top-8 probability mass ----------
    print("## M2 probability mass on the routine top-8 experts (token level, median / q10)")
    print("Higher = the token routes to the *routine-canonical* experts.")
    print()
    print("| layer | routine | code | other(all) | " + " | ".join(x[:6] for x in DOMAINS) + " |")
    print("|" + "---|" * (4 + len(DOMAINS)))
    for l in range(L):
        row = []
        for g in ["routine", "code", "other"] + [f"other:{x}" for x in DOMAINS]:
            row.append(f"{tp[g]['rmass']['median'][l]:.3f}/{tp[g]['rmass']['q10'][l]:.3f}")
        print(f"| {l} | " + " | ".join(row) + " |")
    print()

    # ---------- M3 trace-level null: max over windows ----------
    print("## M3 trace-level null (what the window-level numbers cost)")
    print("For each held-out routine trace and each resist trace, take the MAX over its "
          "48-token window medians (stride 8); compare with each code trace's single "
          "product-anchor window median.  'routine traces above' = fraction of the 120 "
          "held-out routine traces whose max window beats that code trace.")
    print()
    per_trace_max = defaultdict(dict)
    for grp in ("routine", "resist"):
        by_trace = defaultdict(list)
        for i, m in enumerate(meta[grp]):
            by_trace[m["trace_id"]].append(i)
        for tid, idx in by_trace.items():
            per_trace_max[grp][tid] = {k: wm[grp][k][idx].max(axis=0) for k in ("js",)}
    subsets = {"L5": [5], "L7": [7], "L5+L7": [5, 7], "5-15": list(range(5, 16)),
               "12-15": [12, 13, 14, 15]}
    pt = [r for r in d["per_trace"] if r.get("anchor") == "product"]
    code_rows = sorted([r for r in pt if r["group"] == "code"], key=lambda r: r["trace_id"])
    other_rows = sorted([r for r in pt if r["group"] != "code"], key=lambda r: r["trace_id"])
    print("| trace | domain | " + " | ".join(
        f"{s}: JS / routine traces above / resist traces above" for s in subsets) + " |")
    print("|" + "---|" * (2 + len(subsets)))
    for r in code_rows + other_rows[:0]:
        cells = []
        for s, ls in subsets.items():
            v = float(np.mean([r["js_median"][l] for l in ls]))
            fr = np.mean([1.0 if np.mean([per_trace_max["routine"][t]["js"][l] for l in ls]) > v
                          else 0.0 for t in per_trace_max["routine"]])
            fs = np.mean([1.0 if np.mean([per_trace_max["resist"][t]["js"][l] for l in ls]) > v
                          else 0.0 for t in per_trace_max["resist"]])
            cells.append(f"{v:.3f} / {fr:.2f} / {fs:.2f}")
        print(f"| {'-'.join(r['trace_id'].split('-')[:3])} | code | " + " | ".join(cells) + " |")
    # same for the other-drift traces, summarised per domain
    print()
    print("| domain | n | " + " | ".join(f"{s}: median JS / median routine-traces-above" for s in subsets) + " |")
    print("|" + "---|" * (2 + len(subsets)))
    for dom in ["programming"] + DOMAINS:
        rows = code_rows if dom == "programming" else [r for r in other_rows if r["domain"] == dom]
        cells = []
        for s, ls in subsets.items():
            vs = [float(np.mean([r["js_median"][l] for l in ls])) for r in rows]
            frs = [np.mean([1.0 if np.mean([per_trace_max["routine"][t]["js"][l] for l in ls]) > v
                            else 0.0 for t in per_trace_max["routine"]]) for v in vs]
            cells.append(f"{np.median(vs):.3f} / {np.median(frs):.2f}")
        print(f"| {dom} | {len(rows)} | " + " | ".join(cells) + " |")
    print()

    # ---------- M4 the separation test with a resist null too ----------
    print("## M4 window-level separation of code, with routine and resist nulls (JS)")
    print("| layer set | code ratio vs routine | code AUC vs routine | code AUC vs resist | "
          "other(all) ratio | other AUC vs routine | other AUC vs resist |")
    print("|---|---|---|---|---|---|---|")
    other_all = np.concatenate([wm[f"other:{x}"]["js"] for x in DOMAINS], axis=0)
    sets = {"0-4": list(range(5)), "5-15 (CAND-A)": list(range(5, 16)), "5-7": [5, 6, 7],
            "8-11": [8, 9, 10, 11], "12-15": [12, 13, 14, 15], "L5": [5], "L7": [7],
            "L5+L7": [5, 7], "0-15": list(range(16))}
    for name, ls in sets.items():
        rn = wm["routine"]["js"][:, ls].mean(1)
        rs = wm["resist"]["js"][:, ls].mean(1)
        c = wm["code"]["js"][:, ls].mean(1)
        o = other_all[:, ls].mean(1)
        print(f"| {name} | {np.median(c)/np.median(rn):.2f} | {auc(c, rn):.3f} | "
              f"{auc(c, rs):.3f} | {np.median(o)/np.median(rn):.2f} | {auc(o, rn):.3f} | "
              f"{auc(o, rs):.3f} |")
    print()

    # ---------- M5 how far the code windows are from matching other drifts ----------
    print("## M5 'same separation as other drifts' test")
    print("Target = the value layers 5-15 give the 51 other-domain drift windows "
          "(ratio 1.44 / AUC 0.940 vs the routine window null).  Best over all 16 single "
          "layers, all 120 contiguous bands and a greedy subset search, for code:")
    best = {}
    cand = {}
    for a in range(L):
        for b in range(a, L):
            cand[f"{a}-{b}"] = list(range(a, b + 1))
    rn_all = wm["routine"]["js"]
    for name, ls in cand.items():
        rn = rn_all[:, ls].mean(1); c = wm["code"]["js"][:, ls].mean(1)
        o = other_all[:, ls].mean(1)
        best[name] = (float(np.median(c) / np.median(rn)), auc(c, rn),
                      float(np.median(o) / np.median(rn)), auc(o, rn))
    top_ratio = sorted(best.items(), key=lambda kv: -kv[1][0])[:5]
    top_auc = sorted(best.items(), key=lambda kv: -kv[1][1])[:5]
    print()
    print("| band | code ratio | code AUC | other ratio | other AUC |")
    print("|---|---|---|---|---|")
    for n, v in top_ratio:
        print(f"| best-ratio {n} | {v[0]:.2f} | {v[1]:.3f} | {v[2]:.2f} | {v[3]:.3f} |")
    for n, v in top_auc:
        print(f"| best-AUC {n} | {v[0]:.2f} | {v[1]:.3f} | {v[2]:.2f} | {v[3]:.3f} |")
    print()


if __name__ == "__main__":
    main()
