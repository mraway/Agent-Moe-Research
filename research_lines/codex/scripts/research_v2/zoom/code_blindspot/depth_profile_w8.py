"""Robustness of the depth shape at the deployable window width (w=8, CAND-A's width).

Same fit/held routine split and the same diagonal whitening as the w=48 tables, but the
window is 8 tokens.  Drift traces are summarised by the MAX over the w=8 windows whose
end falls in [onset, onset+48) (a post-hoc, label-anchored read); the routine and resist
nulls take the MAX over all of a trace's w=8 windows.  Diagnostic only.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch

from research_v2 import io as rio

REPO = Path(__file__).resolve().parents[4]
CACHE = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot/depth_profile"
LABELS = REPO / "docs/research_v2/labels/product_onset_v1_adjudicated.jsonl"
W = 8
FLOOR = 1e-3
DOMAINS = ["cooking", "fiction", "general_knowledge", "legal_analysis",
           "mathematics", "poetry", "travel_planning"]
PROG = {"b1-f2-012", "b1-f2-014", "b1-f3-016", "b2-f2-011",
        "b2-f2-012", "b2-f2-014", "b2-f2-015", "b2-f3-020"}


def auc(pos, neg):
    a = np.concatenate([pos, neg]); order = np.argsort(a, kind="mergesort"); sa = a[order]
    ranks = np.empty(len(a)); i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sa[j + 1] == sa[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0; i = j + 1
    return (ranks[:len(pos)].sum() - len(pos) * (len(pos) - 1) / 2.0) / (len(pos) * len(neg))


def windows(x: torch.Tensor) -> np.ndarray:
    """x [16,T,64] -> window means [T-W+1, 16, 64] (window ends at index W-1 ... T-1)."""
    c = torch.cat((torch.zeros(16, 1, 64), x.cumsum(1)), dim=1)
    m = (c[:, W:, :] - c[:, :-W, :]) / float(W)
    return m.permute(1, 0, 2).numpy().astype(np.float32)


def main():
    d = json.loads((CACHE / "depth_profile.json").read_text())
    fit_ids, held_ids = set(d["fit_ids"]), set(d["held_ids"])
    labels = {}
    for line in LABELS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            labels[r["trace_id"]] = r
    batches = rio.load_core()
    traces = [t for b in ("b1", "b2") for t in batches[b]]

    fitP, fitS = [], []
    heldP, heldS, held_owner = [], [], []
    resP, resS, res_owner = [], [], []
    driftP, driftS, drift_meta = [], [], []
    for t in traces:
        pr = t.probabilities().float()
        oh = torch.zeros(16, t.token_count, 64)
        oh.scatter_(2, t.top_k_ids, 1.0)
        if t.token_count < W:
            continue
        wp, ws = windows(pr), windows(oh)
        if t.trace_id in fit_ids:
            fitP.append(wp[::2]); fitS.append(ws[::2])
        elif t.trace_id in held_ids:
            heldP.append(wp[::2]); heldS.append(ws[::2])
            held_owner += [t.trace_id] * len(wp[::2])
        elif rio.arm_class(t) == "resist":
            resP.append(wp[::2]); resS.append(ws[::2])
            res_owner += [t.trace_id] * len(wp[::2])
        elif rio.arm_class(t) == "drift":
            onset = int(labels[t.trace_id]["product_onset"])
            lo = max(0, onset - (W - 1))
            ends = np.arange(W - 1, t.token_count)
            sel = (ends >= onset) & (ends < min(onset + 48, t.token_count))
            if not sel.any():
                sel = np.zeros_like(sel); sel[0] = True
            driftP.append(wp[sel]); driftS.append(ws[sel])
            short = "-".join(t.trace_id.split("-")[:3])
            drift_meta.append({"trace_id": t.trace_id, "n": int(sel.sum()),
                               "group": "code" if short in PROG else "other",
                               "domain": labels[t.trace_id]["domain"]})
        t._probabilities = None

    def stack(xs):
        return np.concatenate(xs, axis=0)
    FP, FS = stack(fitP), stack(fitS)
    HP, HS = stack(heldP), stack(heldS)
    RP, RS = stack(resP), stack(resS)
    print(f"w={W}: fit windows {FP.shape[0]}, held-out routine {HP.shape[0]}, resist {RP.shape[0]}, "
          f"drift traces {len(drift_meta)}")
    print()

    def build(F, ls):
        X = np.concatenate([F[:, l, :] for l in ls], axis=1).astype(np.float64)
        return X.mean(0), X.std(0) + FLOOR

    def sc(A, ls, mu, sd):
        X = np.concatenate([A[:, l, :] for l in ls], axis=1).astype(np.float64)
        return np.linalg.norm((X - mu) / sd, axis=1)

    sets = {**{f"L{l}": [l] for l in range(16)},
            "0-4": list(range(5)), "5-11": list(range(5, 12)), "12-15": [12, 13, 14, 15],
            "5-15 (CAND-A band)": list(range(5, 16)), "0-15": list(range(16))}
    print("## w=8 window-level separation (max over the onset..+48 windows per drift trace, "
          "max over all windows per routine / resist trace)")
    print("| layer set | feat | code median ratio | code AUC vs routine traces | code clean "
          "(<=5% routine traces above) | other median ratio | other AUC | other clean |")
    print("|" + "---|" * 8)
    for name, ls in sets.items():
        for tag, (F, H, R, D) in (("P", (FP, HP, RP, driftP)), ("S", (FS, HS, RS, driftS))):
            mu, sd = build(F, ls)
            hs, rs = sc(H, ls, mu, sd), sc(R, ls, mu, sd)
            hmax = defaultdict(float)
            for v, o in zip(hs, held_owner):
                hmax[o] = max(hmax[o], v)
            hmax = np.array(list(hmax.values()))
            vals = np.array([sc(x, ls, mu, sd).max() for x in D])
            code = np.array([v for v, m in zip(vals, drift_meta) if m["group"] == "code"])
            oth = np.array([v for v, m in zip(vals, drift_meta) if m["group"] != "code"])
            med = np.median(hmax)
            print(f"| {name} | {tag} | {np.median(code)/med:.2f} | {auc(code, hmax):.3f} | "
                  f"{sum(1 for v in code if (hmax > v).mean() <= 0.05)}/8 | "
                  f"{np.median(oth)/med:.2f} | {auc(oth, hmax):.3f} | "
                  f"{sum(1 for v in oth if (hmax > v).mean() <= 0.05)}/51 |")
    print()
    print("## per-code-trace, w=8, feature P and S (score / fraction of routine traces above)")
    for name, ls in (("12-15", [12, 13, 14, 15]), ("5-15 (CAND-A band)", list(range(5, 16))),
                     ("L13", [13])):
        print(f"### layers {name}")
        print("| trace | P score / routine above | S score / routine above |")
        print("|---|---|---|")
        cols = {}
        for tag, (F, H, D) in (("P", (FP, HP, driftP)), ("S", (FS, HS, driftS))):
            mu, sd = build(F, ls)
            hs = sc(H, ls, mu, sd)
            hmax = defaultdict(float)
            for v, o in zip(hs, held_owner):
                hmax[o] = max(hmax[o], v)
            hmax = np.array(list(hmax.values()))
            cols[tag] = ([sc(x, ls, mu, sd).max() for x in D], hmax)
        for i, m in enumerate(drift_meta):
            if m["group"] != "code":
                continue
            out = []
            for tag in ("P", "S"):
                vals, hmax = cols[tag]
                out.append(f"{vals[i]:.2f} / {(hmax > vals[i]).mean():.2f}")
            print(f"| {'-'.join(m['trace_id'].split('-')[:3])} | " + " | ".join(out) + " |")
        print()


if __name__ == "__main__":
    main()
