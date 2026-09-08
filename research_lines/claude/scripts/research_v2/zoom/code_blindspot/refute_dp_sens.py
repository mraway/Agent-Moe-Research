"""REFUTER step G: sensitivity of the w=8 '5/8 vs 1/8' count to the arbitrary choices in
depth_profile's null (routine windows subsampled with stride 2 while drift windows use
stride 1; 'clean' threshold at exactly 5% of 120 routine traces = 6 traces).
Diagnostic only.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch

from research_v2 import io as rio

REPO = Path(__file__).resolve().parents[4]
LABELS = REPO / "docs/research_v2/labels/product_onset_v1_adjudicated.jsonl"
W, FLOOR = 8, 1e-3
PROG = {"b1-f2-012", "b1-f2-014", "b1-f3-016", "b2-f2-011",
        "b2-f2-012", "b2-f2-014", "b2-f2-015", "b2-f3-020"}
LITERAL = {"b1-f2-012", "b1-f2-014", "b1-f3-016", "b2-f2-011", "b2-f3-020"}


def short(t):
    return "-".join(t.split("-")[:3])


def windows(x):
    c = torch.cat((torch.zeros(16, 1, x.shape[2]), x.cumsum(1)), dim=1)
    return ((c[:, W:, :] - c[:, :-W, :]) / float(W)).permute(1, 0, 2).numpy().astype(np.float32)


def main():
    labels = {}
    for line in LABELS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line); labels[r["trace_id"]] = r
    batches = rio.load_core()
    traces = [t for b in ("b1", "b2") for t in batches[b]]
    routine = sorted([t for t in traces if rio.arm_class(t) in ("clean", "benign")],
                     key=lambda t: t.trace_id)
    grp = defaultdict(list)
    for t in routine:
        grp[(t.batch, t.arm)].append(t)
    fit_ids, held_ids = set(), set()
    for k in sorted(grp):
        for i, t in enumerate(grp[k]):
            (fit_ids if i % 2 == 0 else held_ids).add(t.trace_id)
    P, S, ENDS, M = {}, {}, {}, {}
    for t in traces:
        if t.token_count < W:
            continue
        pr = t.probabilities().float()
        oh = torch.zeros(16, t.token_count, 64); oh.scatter_(2, t.top_k_ids, 1.0)
        P[t.trace_id] = windows(pr); S[t.trace_id] = windows(oh)
        ENDS[t.trace_id] = np.arange(W - 1, t.token_count)
        lab = labels.get(t.trace_id)
        M[t.trace_id] = {"short": short(t.trace_id), "ac": rio.arm_class(t), "T": t.token_count,
                         "onset": None if lab is None else int(lab["product_onset"]),
                         "code": short(t.trace_id) in PROG,
                         "fit": t.trace_id in fit_ids, "held": t.trace_id in held_ids}
        t._probabilities = None
    ls = list(range(5, 16))

    def build(feat, sub):
        A = np.concatenate([(P if feat == "P" else S)[t][::sub][:, ls, :].reshape(-1, len(ls) * 64)
                            for t in M if M[t]["fit"]], axis=0).astype(np.float64)
        return A.mean(0), A.std(0) + FLOOR

    print("## sensitivity of 'code clean k/8' (layers 5-15, w=8) to the null's window stride")
    print("| feat | fit stride | routine-null stride | drift stride | code clean | which |")
    print("|" + "---|" * 6)
    out = {}
    for feat in ("P", "S"):
        for fsub, nsub, dsub in ((2, 2, 1), (1, 1, 1), (2, 1, 1), (2, 2, 2)):
            mu, sd = build(feat, fsub)
            hmax = np.array([np.linalg.norm(
                ((P if feat == "P" else S)[t][::nsub][:, ls, :].reshape(-1, len(ls) * 64) - mu) / sd,
                axis=1).max() for t in M if M[t]["held"]])
            names = []
            for t in M:
                if M[t]["ac"] != "drift" or not M[t]["code"]:
                    continue
                e = ENDS[t]
                sel = (e >= M[t]["onset"]) & (e < min(M[t]["onset"] + 48, M[t]["T"]))
                if not sel.any():
                    sel = np.zeros_like(sel); sel[0] = True
                X = (P if feat == "P" else S)[t][:, ls, :].reshape(-1, len(ls) * 64)
                sc = np.linalg.norm((X - mu) / sd, axis=1)
                sc = sc[sel][::dsub] if dsub > 1 else sc[sel]
                v = sc.max()
                if (hmax > v).mean() <= 0.05:
                    names.append(M[t]["short"])
            out[f"{feat}|{fsub}{nsub}{dsub}"] = names
            print(f"| {feat} | {fsub} | {nsub} | {dsub} | {len(names)}/8 | "
                  f"{','.join(sorted(names))} |")
    print("\nLITERAL set =", sorted(LITERAL))
    print("prose-about-SQL set =", sorted(PROG - LITERAL))


if __name__ == "__main__":
    main()
