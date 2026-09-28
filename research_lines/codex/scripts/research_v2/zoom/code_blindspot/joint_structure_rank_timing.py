"""Rank decomposition and timing split for the joint-structure lens (diagnostic only).

(a) rank-r depth-pair novelty: for each rank r = 1..8 build the routine table of
    (e_l^{(r)}, e_{l+1}^{(r)}) pairs and measure the unseen rate of the code /
    other-drift / held-out routine tokens.  Answers "is code's chain novelty in
    top-2..8 rather than top-1?".
(b) timing split: the same w=4 max statistics restricted to product_onset..+16
    (CAND-B's timeliness window) vs the full 48-token region.
"""

from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

import torch

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from research_v2 import io as rio  # noqa: E402
from joint_structure_lens import (  # noqa: E402
    EXPERTS,
    PDM_W,
    SMOOTHINGS,
    RoutineReference,
    load_labels,
    region_slice,
    window_mean,
)

OUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
PROG = [
    "b1-f2-012-order_status-python-function--attack",
    "b1-f2-014-knowledge_qa-python-function--attack",
    "b1-f3-016-return_and_knowledge-javascript-utility--attack",
    "b2-f2-011-knowledge_qa-sql-query--attack",
    "b2-f2-012-order_and_knowledge-sql-query--attack",
    "b2-f2-014-support_case_status-sql-query--attack",
    "b2-f2-015-warranty_status-sql-query--attack",
    "b2-f3-020-order_status-rust-function--attack",
]


def rank_tables(traces):
    """[8, 15, 64, 64] counts of (rank-r expert at l, rank-r expert at l+1)."""
    tab = torch.zeros((8, 15, EXPERTS, EXPERTS), dtype=torch.float64)
    for t in traces:
        ids = t.top_k_ids.long()
        for r in range(8):
            sel = ids[:, :, r]
            for l in range(15):
                flat = sel[l] * EXPERTS + sel[l + 1]
                tab[r, l] += torch.bincount(flat, minlength=EXPERTS * EXPERTS).reshape(
                    EXPERTS, EXPERTS
                )
    return tab


def unseen_rate(tab, ids, lo, hi):
    """[8] per-rank unseen rate over layer pairs and tokens in [lo, hi)."""
    out = torch.zeros(8, dtype=torch.float64)
    for r in range(8):
        sel = ids[:, lo:hi, r]
        vals = []
        for l in range(15):
            vals.append((tab[r, l][sel[l], sel[l + 1]] == 0).double())
        out[r] = torch.cat(vals).mean()
    return out


def main() -> None:
    labels = load_labels()
    batches = rio.load_core()
    traces = [t for k in ("b1", "b2") for t in batches[k]]
    routine = [t for t in traces if rio.arm_class(t) in ("clean", "benign")]
    drift = [t for t in traces if t.positive]
    groups = sorted({t.pair_group_id for t in routine})
    fold_of = {g: i % 2 for i, g in enumerate(groups)}
    folds = {f: [t for t in routine if fold_of[t.pair_group_id] == f] for f in (0, 1)}

    res = {"folds": {}}
    for f in (0, 1):
        tab = rank_tables(folds[f])
        ref = RoutineReference(folds[f])
        block = {"drift": {}}
        rr = []
        for t in folds[1 - f]:
            ids = t.top_k_ids.long()
            rr.append(unseen_rate(tab, ids, 0, ids.shape[1]))
        block["routine_rank_unseen"] = [float(v) for v in torch.stack(rr).mean(0)]
        for t in drift:
            lab = labels[t.trace_id]
            lo, hi = region_slice(t, lab["product_onset"])
            ids = t.top_k_ids.long()
            e = {"domain": t.domain,
                 "rank_unseen": [float(v) for v in unseen_rate(tab, ids, lo, hi)]}
            # timing split
            raw = ref.depth_surprisal(t.top_k_ids, "pdm", 0.5)
            z = (raw - ref.surp_mean[("pdm", 0.5)]) / ref.surp_sd[("pdm", 0.5)]
            w = window_mean(z, PDM_W)
            ends = torch.arange(PDM_W - 1, ids.shape[1], dtype=torch.long)
            for name, cap in (("p16", 16), ("p48", 48)):
                m = (ends >= lo) & (ends < min(lo + cap, ids.shape[1]))
                e[f"w4max_z_{name}"] = float(w[m].max()) if int(m.sum()) else None
            ul = ref.unseen_links(t.top_k_ids, "pdm")
            wl = window_mean(ul, PDM_W)
            for name, cap in (("p16", 16), ("p48", 48)):
                m = (ends >= lo) & (ends < min(lo + cap, ids.shape[1]))
                e[f"w4max_links_{name}"] = float(wl[m].max()) if int(m.sum()) else None
            block["drift"][t.trace_id] = e
        res["folds"][str(f)] = block
        print(f"fold {f} done", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "rank_timing.json").write_text(json.dumps(res, indent=1), encoding="utf-8")

    F = res["folds"]

    def fa(v):
        return sum(v) / len(v)

    others = [t for t in F["0"]["drift"] if F["0"]["drift"][t]["domain"] != "programming"]
    print("\n## M. Per-rank depth-pair unseen rate (routine reference, product region)\n")
    print("| rank | routine | " + " | ".join(t.split("--")[0][:12] for t in PROG) +
          " | code median | code/routine | other median | other/routine |")
    print("|---" * (len(PROG) + 6) + "|")
    for r in range(8):
        rv = fa([F[f]["routine_rank_unseen"][r] for f in ("0", "1")])
        cv = [fa([F[f]["drift"][t]["rank_unseen"][r] for f in ("0", "1")]) for t in PROG]
        ov = [fa([F[f]["drift"][t]["rank_unseen"][r] for f in ("0", "1")]) for t in others]
        print(f"| {r+1} | {rv:.4f} | " + " | ".join(f"{v:.3f}" for v in cv) +
              f" | {st.median(cv):.3f} | {st.median(cv)/rv:.2f} | {st.median(ov):.3f} | {st.median(ov)/rv:.2f} |")

    print("\n## N. Timing split: w=4 max inside onset..+16 vs onset..+48\n")
    print("| statistic | " + " | ".join(t.split("--")[0][:12] for t in PROG) + " | code median | other median |")
    print("|---" * (len(PROG) + 3) + "|")
    for key in ("w4max_z_p16", "w4max_z_p48", "w4max_links_p16", "w4max_links_p48"):
        cv = [fa([F[f]["drift"][t][key] for f in ("0", "1")]) for t in PROG]
        ov = [fa([F[f]["drift"][t][key] for f in ("0", "1")]) for t in others]
        print(f"| {key} | " + " | ".join(f"{v:.2f}" for v in cv) +
              f" | {st.median(cv):.2f} | {st.median(ov):.2f} |")


if __name__ == "__main__":
    main()
