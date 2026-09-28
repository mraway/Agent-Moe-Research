"""Analyse the depth-profile cache: layer tables, per-trace tables, layer-subset search.

Diagnostic only.  Reads artifacts/agent_v2/research_v2/zoom_code_blindspot/depth_profile.json
and prints markdown to stdout.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
CACHE = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot" / "depth_profile" / "depth_profile.json"
L = 16
DOMAINS = ["cooking", "fiction", "general_knowledge", "legal_analysis",
           "mathematics", "poetry", "travel_planning"]


def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """P(pos > neg) with ties at 0.5."""
    a = np.concatenate([pos, neg])
    r = a.argsort().argsort().astype(float)
    # average ranks for ties
    order = np.argsort(a, kind="mergesort")
    sa = a[order]
    ranks = np.empty(len(a))
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sa[j + 1] == sa[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0
        i = j + 1
    rp = ranks[:len(pos)].sum()
    return (rp - len(pos) * (len(pos) - 1) / 2.0) / (len(pos) * len(neg))


def main() -> None:
    d = json.loads(CACHE.read_text(encoding="utf-8"))
    tp = d["token_profile"]
    wm = {g: {k: np.array(v) for k, v in m.items()} for g, m in d["window_medians"].items()}
    meta = d["window_meta"]

    print(f"fit routine traces={d['n_fit_routine']} held={d['n_held_routine']} "
          f"fit tokens={d['n_fit_tokens']} routine null windows={len(meta['routine'])}")
    print()

    # ---------- 1. token-level layer profile ----------
    print("## T1 token-level layer profile (median / q90), product anchor windows")
    print()
    print("| layer | routine JS | code JS | other JS | routine 1-Jac | code 1-Jac | other 1-Jac "
          "| routine H | code H | other H | routine top1 | code top1 | other top1 |")
    print("|" + "---|" * 13)
    for l in range(L):
        row = [str(l)]
        for k in ("js",):
            for g in ("routine", "code", "other"):
                row.append(f"{tp[g][k]['median'][l]:.4f} / {tp[g][k]['q90'][l]:.4f}")
        for g in ("routine", "code", "other"):
            row.append(f"{1-tp[g]['jac']['median'][l]:.3f} / {1-tp[g]['jac']['q10'][l]:.3f}")
        for g in ("routine", "code", "other"):
            row.append(f"{tp[g]['ent']['median'][l]:.3f} / {tp[g]['ent']['q90'][l]:.3f}")
        for g in ("routine", "code", "other"):
            row.append(f"{tp[g]['top1']['median'][l]:.3f} / {tp[g]['top1']['q90'][l]:.3f}")
        print("| " + " | ".join(row) + " |")
    print()

    # ---------- 2. per-layer separation ratio & AUC (window level) ----------
    print("## T2 per-layer window-level separation vs the routine window null")
    print("(ratio = median(group window medians) / median(routine window medians); "
          "AUC = window-level ranking AUC vs the 851 routine null windows)")
    print()
    groups = ["code"] + [f"other:{x}" for x in DOMAINS]
    for k, lab, sign in (("js", "JS to routine mean", +1), ("jac", "top-8 Jaccard (lower=deviant)", -1),
                         ("ent", "router entropy", +1), ("top1", "top-1 prob", +1)):
        print(f"### {lab}")
        print("| layer | routine med | " + " | ".join(
            f"{g} ratio/AUC" for g in ["code", "other(all)"]) + " | " +
            " | ".join(f"{g.split(':')[1][:6]} ratio/AUC" for g in groups[1:]) + " |")
        print("|" + "---|" * (4 + len(DOMAINS)))
        other_all = np.concatenate([wm[g][k] for g in groups[1:]], axis=0)
        for l in range(L):
            rn = wm["routine"][k][:, l]
            rm = float(np.median(rn))
            cells = []
            for arr in [wm["code"][k][:, l], other_all[:, l]] + [wm[g][k][:, l] for g in groups[1:]]:
                gm = float(np.median(arr))
                a = auc(arr, rn) if sign > 0 else auc(-arr, -rn)
                cells.append(f"{gm/rm:.2f}/{a:.2f}")
            print(f"| {l} | {rm:.4f} | " + " | ".join(cells) + " |")
        print()

    # ---------- 3. per-trace profile for the 8 code traces ----------
    pt = d["per_trace"]
    code_rows = [r for r in pt if r["group"] == "code" and r.get("anchor") == "product"]
    code_rows.sort(key=lambda r: r["trace_id"])
    routine_win = wm["routine"]
    print("## T3 per-trace layer profile of the 8 code traces (product anchor)")
    print("JS median per layer; in brackets the percentile of that value inside the "
          "851 routine null windows at the same layer.")
    print()
    hdr = "| trace | onset | len | " + " | ".join(f"L{l}" for l in range(L)) + " |"
    print(hdr)
    print("|" + "---|" * (L + 3))
    for r in code_rows:
        cells = []
        for l in range(L):
            v = r["js_median"][l]
            pct = float((routine_win["js"][:, l] < v).mean()) * 100
            cells.append(f"{v:.3f}({pct:.0f})")
        print(f"| {r['trace_id'].split('-')[0]}-{'-'.join(r['trace_id'].split('-')[1:3])} "
              f"| {r['onset']} | {r['n_tokens']} | " + " | ".join(cells) + " |")
    print()
    print("Same, 1-Jaccard (deviation of the top-8 set from the routine top-8 set):")
    print(hdr)
    print("|" + "---|" * (L + 3))
    for r in code_rows:
        cells = []
        for l in range(L):
            v = 1 - r["jac_median"][l]
            pct = float(((1 - routine_win["jac"][:, l]) < v).mean()) * 100
            cells.append(f"{v:.2f}({pct:.0f})")
        print(f"| {r['trace_id'].split('-')[0]}-{'-'.join(r['trace_id'].split('-')[1:3])} "
              f"| {r['onset']} | {r['n_tokens']} | " + " | ".join(cells) + " |")
    print()

    # ---------- 4. layer-subset search ----------
    print("## T4 layer / layer-subset search (DIAGNOSTIC, uses labels)")
    print()

    def agg(arr2d, layers, k):
        sub = arr2d[:, list(layers)]
        return sub.mean(axis=1)

    def score(layers, k, sign=+1):
        rn = agg(wm["routine"][k], layers, k)
        rm = float(np.median(rn))
        res = {}
        for name, arr in [("code", wm["code"][k])] + [(g, wm[g][k]) for g in groups[1:]]:
            v = agg(arr, layers, k)
            a = auc(v, rn) if sign > 0 else auc(-v, -rn)
            res[name] = (float(np.median(v)) / rm, a,
                         [float((rn < x).mean()) if sign > 0 else float((rn > x).mean()) for x in v])
        other = np.concatenate([agg(wm[g][k], layers, k) for g in groups[1:]])
        a = auc(other, rn) if sign > 0 else auc(-other, -rn)
        res["other(all)"] = (float(np.median(other)) / rm, a, [])
        return res

    for k, sign in (("js", +1), ("jac", -1), ("ent", +1), ("top1", +1)):
        print(f"### metric {k}")
        print("| layer set | code ratio | code AUC | code windows > 90th pct of routine | "
              "other(all) ratio | other AUC |")
        print("|---|---|---|---|---|---|")
        sets = {"5-15 (CAND-A band)": range(5, 16), "0-15 (all)": range(16),
                "0-4": range(0, 5), "5-11": range(5, 12), "12-15": range(12, 16)}
        for l in range(L):
            sets[f"L{l}"] = [l]
        best = None
        for name, s in sets.items():
            r = score(list(s), k, sign)
            cr, ca, cp = r["code"]
            n90 = sum(1 for x in cp if x >= 0.90)
            orr, oa, _ = r["other(all)"]
            print(f"| {name} | {cr:.2f} | {ca:.3f} | {n90}/8 | {orr:.2f} | {oa:.3f} |")
            if best is None or ca > best[1]:
                best = (name, ca, cr, n90)
        # greedy forward search maximising code AUC
        chosen, cur = [], 0.0
        remaining = set(range(L))
        while remaining:
            cand = max(remaining, key=lambda l: score(chosen + [l], k, sign)["code"][1])
            a = score(chosen + [cand], k, sign)["code"][1]
            if a <= cur + 1e-9:
                break
            chosen.append(cand); remaining.discard(cand); cur = a
        r = score(sorted(chosen), k, sign)
        cr, ca, cp = r["code"]
        orr, oa, _ = r["other(all)"]
        print(f"| **greedy best for code** {sorted(chosen)} | {cr:.2f} | {ca:.3f} | "
              f"{sum(1 for x in cp if x>=0.90)}/8 | {orr:.2f} | {oa:.3f} |")
        print()

    # ---------- 5. reference facts ----------
    print("## T5 routine reference per layer")
    print("| layer | routine top-8 mass | routine mean-dist entropy | routine top-8 experts |")
    print("|---|---|---|---|")
    for l in range(L):
        print(f"| {l} | {d['ref_top8_mass'][l]:.3f} | {d['ref_entropy'][l]:.3f} | "
              f"{d['ref_top8'][l]} |")
    print()

    # ---------- 6. evidence anchor variant ----------
    print("## T6 evidence-anchor variant (window-level, mean over layers 5-15 and best sets)")
    print("| group | anchor | JS ratio | JS AUC | 1-Jac ratio | 1-Jac AUC |")
    print("|---|---|---|---|---|---|")
    for gname, key in [("code", "code"), ("code", "code@ev")] + \
                      [("other(all)", "prod"), ("other(all)", "ev")]:
        if key in ("prod", "ev"):
            suffix = "" if key == "prod" else "@ev"
            js = np.concatenate([wm[f"other:{x}{suffix}"]["js"] for x in DOMAINS], axis=0)
            jac = np.concatenate([wm[f"other:{x}{suffix}"]["jac"] for x in DOMAINS], axis=0)
            anchor = "product" if key == "prod" else "evidence"
        else:
            js, jac = wm[key]["js"], wm[key]["jac"]
            anchor = "product" if not key.endswith("@ev") else "evidence"
        ls = list(range(5, 16))
        rn_js = wm["routine"]["js"][:, ls].mean(1)
        rn_jc = wm["routine"]["jac"][:, ls].mean(1)
        v_js = js[:, ls].mean(1); v_jc = jac[:, ls].mean(1)
        print(f"| {gname} | {anchor} | {np.median(v_js)/np.median(rn_js):.2f} | "
              f"{auc(v_js, rn_js):.3f} | {(1-np.median(v_jc))/(1-np.median(rn_jc)):.2f} | "
              f"{auc(-v_jc, -rn_jc):.3f} |")
    print()


if __name__ == "__main__":
    main()
