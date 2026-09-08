"""Remaining report tables: per-trace entropy / top-1 / routine-top-8 mass profiles,
the evidence-anchor variant, and the resist-arm comparison.  Diagnostic only."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
CACHE = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot/depth_profile"
L = 16
DOMAINS = ["cooking", "fiction", "general_knowledge", "legal_analysis",
           "mathematics", "poetry", "travel_planning"]
FLOOR = 1e-3


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
    d = json.loads((CACHE / "depth_profile.json").read_text())
    wm = {g: {k: np.array(v) for k, v in m.items()} for g, m in d["window_medians"].items()}
    pt = [r for r in d["per_trace"] if r.get("anchor") == "product"]
    code = sorted([r for r in pt if r["group"] == "code"], key=lambda r: r["trace_id"])

    for key, name, tag in (("rmass", "probability mass on the routine top-8 experts", "R"),
                           ("ent", "router entropy (bits)", "H"),
                           ("top1", "top-1 router probability", "P1")):
        print(f"## per-trace {name}: value (percentile in the 851 held-out routine windows)")
        print("| trace | " + " | ".join(f"L{l}" for l in range(L)) + " |")
        print("|" + "---|" * (L + 1))
        for r in code:
            cells = []
            for l in range(L):
                v = r[f"{key}_median"][l]
                cells.append(f"{v:.2f}({(wm['routine'][key][:, l] < v).mean()*100:.0f})")
            print(f"| {'-'.join(r['trace_id'].split('-')[:3])} | " + " | ".join(cells) + " |")
        print(f"| routine median | " + " | ".join(
            f"{np.median(wm['routine'][key][:, l]):.2f}" for l in range(L)) + " |")
        other = np.concatenate([wm[f"other:{x}"][key] for x in DOMAINS], axis=0)
        print(f"| other-drift median | " + " | ".join(
            f"{np.median(other[:, l]):.2f}" for l in range(L)) + " |")
        print()

    # anchor variant
    zp = np.load(CACHE / "window_probs.npz")

    def whit(layers, group):
        F = np.concatenate([zp["routine_fit"][:, l, :] for l in layers], axis=1).astype(np.float64)
        mu, sd = F.mean(0), F.std(0) + FLOOR
        X = np.concatenate([zp[group][:, l, :] for l in layers], axis=1).astype(np.float64)
        return np.linalg.norm((X - mu) / sd, axis=1)

    print("## anchor variant: product_onset vs evidence_onset (48-token window, feature P)")
    print("| group | layers | product ratio / AUC | evidence ratio / AUC |")
    print("|---|---|---|---|")
    for layers, lname in ((list(range(5, 16)), "5-15"), ([12, 13, 14, 15], "12-15")):
        rn = whit(layers, "routine")
        for gname, gp, ge in (("code", "code", "code@ev"),
                              ("other(all)", None, None)):
            if gname == "code":
                vp, ve = whit(layers, gp), whit(layers, ge)
            else:
                vp = np.concatenate([whit(layers, f"other:{x}") for x in DOMAINS])
                ve = np.concatenate([whit(layers, f"other:{x}@ev") for x in DOMAINS])
            print(f"| {gname} | {lname} | {np.median(vp)/np.median(rn):.2f} / {auc(vp, rn):.3f} | "
                  f"{np.median(ve)/np.median(rn):.2f} / {auc(ve, rn):.3f} |")
    print()
    print("| trace | 12-15 product | 12-15 evidence | 5-15 product | 5-15 evidence |")
    print("|---|---|---|---|---|")
    meta = d["window_meta"]
    for lay, tag in (([12, 13, 14, 15], "a"),):
        pass
    p1215, e1215 = whit([12, 13, 14, 15], "code"), whit([12, 13, 14, 15], "code@ev")
    p515, e515 = whit(list(range(5, 16)), "code"), whit(list(range(5, 16)), "code@ev")
    for i, m in enumerate(meta["code"]):
        j = [k for k, mm in enumerate(meta["code@ev"]) if mm["trace_id"] == m["trace_id"]][0]
        print(f"| {'-'.join(m['trace_id'].split('-')[:3])} | {p1215[i]:.2f} | {e1215[j]:.2f} | "
              f"{p515[i]:.2f} | {e515[j]:.2f} |")
    print()


if __name__ == "__main__":
    main()
