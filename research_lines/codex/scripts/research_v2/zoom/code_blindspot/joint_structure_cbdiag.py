"""Follow-up diagnostics for the joint-structure lens: why CAND-B still misses 7/8.

Adds, on top of joint_structure_lens.py's tables:
  * trace-level decision-statistic analogue -- max over causal w=4 windows of the
    standardized D1 surprisal (and of the unseen-link count), with the held-out
    routine per-trace-max q90 as a self-contained conformal analogue;
  * effect size of the code region in routine-sd units, per smoothing;
  * rank-promotion test: for code tokens whose top-1 depth link is UNSEEN in the
    routine top-1 table, how often is that same (a, b) pair present in the routine
    top-8 cross table (i.e. routine visits the pair, only at rank >= 2);
  * inside- vs outside-band (5-11) layer-pair contribution;
  * count<=2 sensitivity for the rarity rule (the 1st-percentile rule degenerates
    to "unseen" at this reference size).

Diagnostic only; uses labels and anchors post hoc.  Nothing here is a detector.
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
    PDM_LAYERS,
    PDM_W,
    REGION,
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


def q(vals, p):
    return float(torch.quantile(torch.tensor(vals, dtype=torch.float64), p))


def main() -> None:
    labels = load_labels()
    batches = rio.load_core()
    traces = [t for k in ("b1", "b2") for t in batches[k]]
    routine = [t for t in traces if rio.arm_class(t) in ("clean", "benign")]
    drift = [t for t in traces if t.positive]
    groups = sorted({t.pair_group_id for t in routine})
    fold_of = {g: i % 2 for i, g in enumerate(groups)}
    folds = {f: [t for t in routine if fold_of[t.pair_group_id] == f] for f in (0, 1)}

    out = {"folds": {}}
    for f in (0, 1):
        ref = RoutineReference(folds[f])
        block: dict = {}
        # ---------- routine per-trace maxima (conformal analogue) ----------
        rmax = {f"z{s}": [] for s in SMOOTHINGS}
        rmax["links"] = []
        rmax["links_any"] = []
        for t in folds[1 - f]:
            ids = t.top_k_ids
            for s in SMOOTHINGS:
                raw = ref.depth_surprisal(ids, "pdm", s)
                z = (raw - ref.surp_mean[("pdm", s)]) / ref.surp_sd[("pdm", s)]
                w = window_mean(z, PDM_W)
                rmax[f"z{s}"].append(float(w.max()) if w.numel() else float("nan"))
            ul = ref.unseen_links(ids, "pdm")
            w = window_mean(ul, PDM_W)
            rmax["links"].append(float(w.max()) if w.numel() else float("nan"))
            w2 = window_mean((ul > 0).double(), PDM_W)
            rmax["links_any"].append(float(w2.max()) if w2.numel() else float("nan"))
        block["routine_trace_max"] = {
            k: {"median": q(v, 0.5), "q90": q(v, 0.90), "q95": q(v, 0.95), "max": max(v)}
            for k, v in rmax.items()
        }
        block["routine_sd_nats"] = {str(s): ref.surp_sd[("pdm", s)] for s in SMOOTHINGS}
        block["routine_mean_nats"] = {str(s): ref.surp_mean[("pdm", s)] for s in SMOOTHINGS}

        # ---------- per drift trace ----------
        per = {}
        for t in drift:
            lab = labels[t.trace_id]
            lo, hi = region_slice(t, lab["product_onset"])
            ids = t.top_k_ids
            e: dict = {"domain": t.domain}
            for s in SMOOTHINGS:
                raw = ref.depth_surprisal(ids, "pdm", s)
                z = (raw - ref.surp_mean[("pdm", s)]) / ref.surp_sd[("pdm", s)]
                w = window_mean(z, PDM_W)
                ends = torch.arange(PDM_W - 1, ids.shape[1], dtype=torch.long)
                m = (ends >= lo) & (ends < hi)
                e[f"region_w4max_z{s}"] = float(w[m].max()) if int(m.sum()) else None
                e[f"region_raw_mean_{s}"] = float(raw[lo:hi].mean())
                e[f"effect_sd_{s}"] = float(
                    (raw[lo:hi].mean() - ref.surp_mean[("pdm", s)]) / ref.surp_sd[("pdm", s)]
                )
            ul = ref.unseen_links(ids, "pdm")
            w = window_mean(ul, PDM_W)
            ends = torch.arange(PDM_W - 1, ids.shape[1], dtype=torch.long)
            m = (ends >= lo) & (ends < hi)
            e["region_w4max_links"] = float(w[m].max()) if int(m.sum()) else None
            w2 = window_mean((ul > 0).double(), PDM_W)
            e["region_w4max_links_any"] = float(w2[m].max()) if int(m.sum()) else None

            # rank-promotion test on layers 0..14 and on 5..11 pairs
            idl = ids.long()
            top1 = idl[:, :, 0]
            for name, pairs in (("all", range(15)), ("pdm", range(5, 11))):
                unseen_n = 0
                promoted = 0
                cross_seen_counts = []
                for l in pairs:
                    a = top1[l, lo:hi]
                    b = top1[l + 1, lo:hi]
                    cnt = ref.top1_pair_freq[l][a, b]
                    mask = cnt == 0
                    unseen_n += int(mask.sum())
                    cross = ref.cross_pair_freq[l][a, b]
                    promoted += int(((cnt == 0) & (cross > 0)).sum())
                    if int(mask.sum()):
                        cross_seen_counts.append(float((cross[mask] > 0).double().mean()))
                e[f"unseen_top1_{name}"] = unseen_n
                e[f"unseen_top1_but_seen_top8_{name}"] = promoted
                e[f"promotion_frac_{name}"] = promoted / unseen_n if unseen_n else None
            # count<=2 rare sensitivity (top-1 pairs, all 15 pairs)
            n_fit = ref.n_tokens
            le2 = 0
            tot = 0
            for l in range(15):
                a = top1[l, lo:hi]
                b = top1[l + 1, lo:hi]
                c = ref.top1_pair_freq[l][a, b] * n_fit
                le2 += int((c <= 2).sum())
                tot += int(a.numel())
            e["top1_le2_rate"] = le2 / tot
            per[t.trace_id] = e

        # routine baseline for promotion + le2
        pro_all, pro_pdm, le2_rates = [], [], []
        for t in folds[1 - f]:
            idl = t.top_k_ids.long()
            top1 = idl[:, :, 0]
            n_fit = ref.n_tokens
            for name, pairs, acc in (("all", range(15), pro_all), ("pdm", range(5, 11), pro_pdm)):
                un = 0
                pr = 0
                for l in pairs:
                    a, b = top1[l], top1[l + 1]
                    cnt = ref.top1_pair_freq[l][a, b]
                    cross = ref.cross_pair_freq[l][a, b]
                    un += int((cnt == 0).sum())
                    pr += int(((cnt == 0) & (cross > 0)).sum())
                if un:
                    acc.append(pr / un)
            le2 = 0
            tot = 0
            for l in range(15):
                a, b = top1[l], top1[l + 1]
                c = ref.top1_pair_freq[l][a, b] * n_fit
                le2 += int((c <= 2).sum())
                tot += int(a.numel())
            le2_rates.append(le2 / tot)
        block["routine_promotion_frac_all"] = st.mean(pro_all)
        block["routine_promotion_frac_pdm"] = st.mean(pro_pdm)
        block["routine_top1_le2_rate"] = st.mean(le2_rates)
        block["drift"] = per
        out["folds"][str(f)] = block

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "candb_diagnosis.json").write_text(json.dumps(out, indent=1), encoding="utf-8")

    # ---------------- print tables ----------------
    def fa(vals):
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    F = out["folds"]
    others = [t for t in F["0"]["drift"] if F["0"]["drift"][t]["domain"] != "programming"]
    print("\n## H. Routine per-trace max of the w=4 statistic (held-out, fold-averaged)\n")
    print("| statistic | routine median | routine q90 | routine q95 | routine max |")
    print("|---|---|---|---|---|")
    for k in ("z0.5", "z0.05", "z0.005", "links", "links_any"):
        row = [fa([F[f]["routine_trace_max"][k][s] for f in ("0", "1")]) for s in ("median", "q90", "q95", "max")]
        print(f"| {k} | " + " | ".join(f"{v:.3g}" for v in row) + " |")

    print("\n## I. Code region max vs routine trace-max q90 (per trace)\n")
    hdr = ["statistic"] + [t.split("--")[0][:12] for t in PROG] + ["routine q90", "code above q90", "other-drift above q90"]
    print("| " + " | ".join(hdr) + " |")
    print("|---" * len(hdr) + "|")
    for key, rk in (("region_w4max_z0.5", "z0.5"), ("region_w4max_z0.05", "z0.05"),
                    ("region_w4max_z0.005", "z0.005"), ("region_w4max_links", "links"),
                    ("region_w4max_links_any", "links_any")):
        thr = fa([F[f]["routine_trace_max"][rk]["q90"] for f in ("0", "1")])
        cells = []
        above = 0
        for t in PROG:
            v = fa([F[f]["drift"][t][key] for f in ("0", "1")])
            cells.append(f"{v:.2f}")
            above += int(v >= thr)
        oab = sum(int(fa([F[f]["drift"][t][key] for f in ("0", "1")]) >= thr) for t in others)
        print(f"| {key} | " + " | ".join(cells) + f" | {thr:.2f} | {above}/8 | {oab}/{len(others)} |")

    print("\n## J. Effect size of the code region in routine-sd units (D1, layers 5-11)\n")
    print("| smoothing | " + " | ".join(t.split("--")[0][:12] for t in PROG) + " | code median | other-drift median |")
    print("|---" * (len(PROG) + 3) + "|")
    for s in SMOOTHINGS:
        cells = [fa([F[f]["drift"][t][f"effect_sd_{s}"] for f in ("0", "1")]) for t in PROG]
        oc = [fa([F[f]["drift"][t][f"effect_sd_{s}"] for f in ("0", "1")]) for t in others]
        print(f"| {s} | " + " | ".join(f"{v:.2f}" for v in cells) + f" | {st.median(cells):.2f} | {st.median(oc):.2f} |")
    print("\nroutine surprisal mean / sd (nats, fold-averaged): " + ", ".join(
        f"s={s}: {fa([F[f]['routine_mean_nats'][str(s)] for f in ('0','1')]):.2f} / "
        f"{fa([F[f]['routine_sd_nats'][str(s)] for f in ('0','1')]):.2f}" for s in SMOOTHINGS))

    print("\n## K. Rank-promotion test (unseen top-1 depth links that routine visits at rank 2-8)\n")
    print("| scope | " + " | ".join(t.split("--")[0][:12] for t in PROG) + " | code median | other-drift median | routine |")
    print("|---" * (len(PROG) + 4) + "|")
    for name in ("all", "pdm"):
        cells = [fa([F[f]["drift"][t][f"promotion_frac_{name}"] for f in ("0", "1")]) for t in PROG]
        oc = [fa([F[f]["drift"][t][f"promotion_frac_{name}"] for f in ("0", "1")]) for t in others]
        rr = fa([F[f][f"routine_promotion_frac_{name}"] for f in ("0", "1")])
        print(f"| {name} | " + " | ".join(f"{v:.3f}" for v in cells) +
              f" | {st.median(cells):.3f} | {st.median(oc):.3f} | {rr:.3f} |")

    print("\n## L. count<=2 rarity sensitivity (top-1 pairs, all 15 layer pairs)\n")
    cells = [fa([F[f]["drift"][t]["top1_le2_rate"] for f in ("0", "1")]) for t in PROG]
    oc = [fa([F[f]["drift"][t]["top1_le2_rate"] for f in ("0", "1")]) for t in others]
    rr = fa([F[f]["routine_top1_le2_rate"] for f in ("0", "1")])
    print("| rule | " + " | ".join(t.split("--")[0][:12] for t in PROG) + " | code median | other median | routine |")
    print("|---" * (len(PROG) + 4) + "|")
    print("| routine count<=2 | " + " | ".join(f"{v:.3f}" for v in cells) +
          f" | {st.median(cells):.3f} | {st.median(oc):.3f} | {rr:.3f} |")
    print(f"| ratio to routine | " + " | ".join(f"{v/rr:.2f}" for v in cells) +
          f" | {st.median(cells)/rr:.2f} | {st.median(oc)/rr:.2f} | 1.00 |")


if __name__ == "__main__":
    main()
