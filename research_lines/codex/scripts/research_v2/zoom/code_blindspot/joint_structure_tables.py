"""Aggregate the joint-structure lens cache into the report tables (diagnostic only)."""

from __future__ import annotations

import json
import statistics as st
from pathlib import Path

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
CACHE = ROOT / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot" / "joint_structure.json"

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
SHORT = {t: t.split("--")[0] for t in PROG}

# routine reference key for each measure, and how the drift value is read
MEASURES = [
    # (label, drift key, routine key, routine stat)
    ("(1) CAND-A g1 median", "m_g1_median", "m_g1_median", None),
    ("(1) CAND-A g1 max", "m_g1_max", "m_g1_median", None),
    ("(2a) top1 pair rare rate", "p_top1_rare", "r_top1_rare_mean", None),
    ("(2a) top1 pair UNSEEN rate", "p_top1_unseen", "r_top1_unseen_mean", None),
    ("(2b) top8 cross-pair rare rate", "p_cross_rare", "r_cross_rare_mean", None),
    ("(2c) overlap-pattern rare rate", "p_overlap_rare", "r_overlap_rare_mean", None),
    ("(3) unseen links 5-11 /token", "c_pdm_unseen_links", "r_pdm_unseen_links_mean", None),
    ("(3) unseen links 0-15 /token", "c_all_unseen_links", "r_all_unseen_links_mean", None),
    ("(3) unseen chain 5-11 frac", "c_pdm_unseen_chain", "r_pdm_unseen_chain_mean", None),
    ("(3) unseen chain 0-15 frac", "c_all_unseen_chain", "r_all_unseen_chain_mean", None),
    ("(3) D1 z 5-11 s0.5 mean", "c_pdm_s0.5_z_mean", "r_pdm_s0.5_z_mean", None),
    ("(3) D1 z 5-11 s0.5 w4max", "c_pdm_s0.5_z_w4max", "r_pdm_s0.5_zw4_q90", None),
    ("(3) D1 z 0-15 s0.5 mean", "c_all_s0.5_z_mean", "r_all_s0.5_z_mean", None),
]


def fold_avg(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def main() -> None:
    data = json.loads(CACHE.read_text(encoding="utf-8"))
    folds = data["folds"]
    anchor = "product"

    # per-trace fold-averaged values
    def dv(tid, key, anchor=anchor):
        return fold_avg([folds[f]["drift"][tid][anchor].get(key) for f in ("0", "1")])

    def rv(key):
        return fold_avg([folds[f]["routine_holdout"].get(key) for f in ("0", "1")])

    tids = list(folds["0"]["drift"].keys())
    dom = {t: folds["0"]["drift"][t]["domain"] for t in tids}
    others = [t for t in tids if dom[t] != "programming"]

    lines = []
    lines.append("## A. Routine held-out reference (fold-averaged)\n")
    lines.append("| measure | routine mean | routine median | routine q90 | routine q99 |")
    lines.append("|---|---|---|---|---|")
    for lab, dkey, rkey, _ in MEASURES:
        base = rkey[:-5] if rkey.endswith("_mean") else None
        row = [f"{rv(rkey):.4g}"]
        for suf in ("_median", "_q90", "_q99"):
            k = (base + suf) if base else None
            v = rv(k) if k else None
            row.append(f"{v:.4g}" if v is not None else "-")
        lines.append(f"| {lab} | " + " | ".join(row) + " |")

    lines.append("\n## B. Per-trace ratio to routine (8 programming drifts, product anchor)\n")
    header = "| measure | " + " | ".join(SHORT[t].replace("b1-", "b1·").replace("b2-", "b2·") for t in PROG) + " | code median |"
    lines.append(header)
    lines.append("|---" * (len(PROG) + 2) + "|")
    ratios = {}
    for lab, dkey, rkey, _ in MEASURES:
        base = rv(rkey)
        cells = []
        vals = []
        for t in PROG:
            v = dv(t, dkey)
            if v is None or base in (None, 0):
                cells.append("-")
                continue
            r = v / base
            vals.append(r)
            cells.append(f"{r:.2f}")
        ratios[lab] = vals
        lines.append(f"| {lab} | " + " | ".join(cells) + f" | {st.median(vals):.2f} |")

    lines.append("\n## C. Other-domain drift ratios (per domain median of per-trace ratio)\n")
    doms = sorted({dom[t] for t in others})
    lines.append("| measure | " + " | ".join(f"{d} (n={sum(1 for t in others if dom[t]==d)})" for d in doms) + " | all other median | code median |")
    lines.append("|---" * (len(doms) + 3) + "|")
    for lab, dkey, rkey, _ in MEASURES:
        base = rv(rkey)
        cells = []
        allv = []
        for d in doms:
            vs = [dv(t, dkey) / base for t in others if dom[t] == d and dv(t, dkey) is not None]
            allv += vs
            cells.append(f"{st.median(vs):.2f}" if vs else "-")
        lines.append(f"| {lab} | " + " | ".join(cells) + f" | {st.median(allv):.2f} | {st.median(ratios[lab]):.2f} |")

    # raw per-trace values for the key chain measures
    lines.append("\n## D. Raw per-trace values (product anchor, fold-averaged)\n")
    raw_keys = [
        ("g1 median", "m_g1_median"),
        ("top1 rare", "p_top1_rare"),
        ("cross rare", "p_cross_rare"),
        ("unseen links 5-11", "c_pdm_unseen_links"),
        ("unseen links 0-15", "c_all_unseen_links"),
        ("tok any unseen link 5-11", "c_pdm_tok_any_unseen_link"),
        ("tok any unseen link 0-15", "c_all_tok_any_unseen_link"),
        ("w4 max novel frac 5-11", "c_pdm_w4_max_novel_frac"),
        ("unseen chain 5-11", "c_pdm_unseen_chain"),
        ("unseen chain 0-15", "c_all_unseen_chain"),
        ("D1 z 5-11 s0.5 mean", "c_pdm_s0.5_z_mean"),
        ("D1 z 5-11 s0.5 w4max", "c_pdm_s0.5_z_w4max"),
        ("D1 z 0-15 s0.5 w4max", "c_all_s0.5_z_w4max"),
        ("D1 z 5-11 s0.05 w4max", "c_pdm_s0.05_z_w4max"),
        ("D1 z 5-11 s0.005 w4max", "c_pdm_s0.005_z_w4max"),
        ("D1 raw nats 5-11 s0.5", "c_pdm_s0.5_raw_mean"),
    ]
    lines.append("| measure | " + " | ".join(SHORT[t][:12] for t in PROG) + " | routine |")
    lines.append("|---" * (len(PROG) + 2) + "|")
    rmap = {
        "m_g1_median": "m_g1_median", "p_top1_rare": "r_top1_rare_mean",
        "p_cross_rare": "r_cross_rare_mean", "c_pdm_unseen_links": "r_pdm_unseen_links_mean",
        "c_all_unseen_links": "r_all_unseen_links_mean",
        "c_pdm_tok_any_unseen_link": "r_pdm_any_unseen_link_mean",
        "c_all_tok_any_unseen_link": "r_all_any_unseen_link_mean",
        "c_pdm_w4_max_novel_frac": "r_pdm_w4_novel_frac_q99",
        "c_pdm_unseen_chain": "r_pdm_unseen_chain_mean",
        "c_all_unseen_chain": "r_all_unseen_chain_mean",
        "c_pdm_s0.5_z_mean": "r_pdm_s0.5_z_mean",
        "c_pdm_s0.5_z_w4max": "r_pdm_s0.5_zw4_q99",
        "c_all_s0.5_z_w4max": "r_all_s0.5_zw4_q99",
        "c_pdm_s0.05_z_w4max": "r_pdm_s0.05_zw4_q99",
        "c_pdm_s0.005_z_w4max": "r_pdm_s0.005_zw4_q99",
        "c_pdm_s0.5_raw_mean": "r_pdm_s0.5_raw_mean",
    }
    for lab, key in raw_keys:
        cells = [f"{dv(t,key):.3g}" if dv(t, key) is not None else "-" for t in PROG]
        r = rv(rmap[key])
        lines.append(f"| {lab} | " + " | ".join(cells) + f" | {r:.3g} |")

    # layer-pair breakdown
    lines.append("\n## E. Per-layer-pair top-1 rare rate, code vs routine vs other drift\n")
    lines.append("| pair (l,l+1) | routine | code median | code/routine | other-drift median | other/routine |")
    lines.append("|---|---|---|---|---|---|")
    for l in range(15):
        rvv = fold_avg([folds[f]["routine_holdout"]["r_top1_rare_bylayer_mean"][l] for f in ("0", "1")])
        cvals = [fold_avg([folds[f]["drift"][t][anchor]["p_top1_rare_bylayer"][l] for f in ("0", "1")]) for t in PROG]
        ovals = [fold_avg([folds[f]["drift"][t][anchor]["p_top1_rare_bylayer"][l] for f in ("0", "1")]) for t in others]
        cm, om = st.median(cvals), st.median(ovals)
        lines.append(f"| {l}-{l+1} | {rvv:.4f} | {cm:.4f} | {cm/rvv:.2f} | {om:.4f} | {om/rvv:.2f} |")

    lines.append("\n## E2. Per-layer-pair top-8 cross-pair rare rate\n")
    lines.append("| pair (l,l+1) | routine | code median | code/routine | other-drift median | other/routine |")
    lines.append("|---|---|---|---|---|---|")
    for l in range(15):
        rvv = fold_avg([folds[f]["routine_holdout"]["r_cross_rare_bylayer_mean"][l] for f in ("0", "1")])
        cvals = [fold_avg([folds[f]["drift"][t][anchor]["p_cross_rare_bylayer"][l] for f in ("0", "1")]) for t in PROG]
        ovals = [fold_avg([folds[f]["drift"][t][anchor]["p_cross_rare_bylayer"][l] for f in ("0", "1")]) for t in others]
        cm, om = st.median(cvals), st.median(ovals)
        lines.append(f"| {l}-{l+1} | {rvv:.4f} | {cm:.4f} | {cm/rvv:.2f} | {om:.4f} | {om/rvv:.2f} |")

    # per-layer marginal
    lines.append("\n## F. Per-layer marginal (CAND-A g1 layer contribution) code/routine ratio\n")
    lines.append("| layer | routine median | code median | ratio | other-drift median | ratio |")
    lines.append("|---|---|---|---|---|---|")
    for i, layer in enumerate(range(5, 16)):
        rvv = fold_avg([folds[f]["routine_holdout"]["m_g1_layer_median"][i] for f in ("0", "1")])
        cvals = [fold_avg([folds[f]["drift"][t][anchor]["m_g1_layer_median"][i] for f in ("0", "1")]) for t in PROG]
        ovals = [fold_avg([folds[f]["drift"][t][anchor]["m_g1_layer_median"][i] for f in ("0", "1")]) for t in others]
        cm, om = st.median(cvals), st.median(ovals)
        lines.append(f"| {layer} | {rvv:.2f} | {cm:.2f} | {cm/rvv:.2f} | {om:.2f} | {om/rvv:.2f} |")

    # evidence-anchor sensitivity
    lines.append("\n## G. Anchor sensitivity (code median ratio, product vs evidence)\n")
    lines.append("| measure | product | evidence |")
    lines.append("|---|---|---|")
    for lab, dkey, rkey, _ in MEASURES:
        base = rv(rkey)
        p = st.median([dv(t, dkey) / base for t in PROG if dv(t, dkey) is not None])
        e = st.median([dv(t, dkey, "evidence") / base for t in PROG if dv(t, dkey, "evidence") is not None])
        lines.append(f"| {lab} | {p:.2f} | {e:.2f} |")

    out = "\n".join(lines)
    print(out)




def amplification() -> None:
    """Section: per-trace joint amplification = (joint ratio) / (marginal ratio)."""
    data = json.loads(CACHE.read_text(encoding="utf-8"))
    folds = data["folds"]

    def dv(tid, key):
        return fold_avg([folds[f]["drift"][tid]["product"].get(key) for f in ("0", "1")])

    def rv(key):
        return fold_avg([folds[f]["routine_holdout"].get(key) for f in ("0", "1")])

    tids = list(folds["0"]["drift"].keys())
    dom = {t: folds["0"]["drift"][t]["domain"] for t in tids}
    m_base, j_base = rv("m_g1_median"), rv("r_top1_rare_mean")
    print("\n## Q. Joint amplification A = (2a top-1 pair ratio) / (1 CAND-A g1 ratio)\n")
    print("| trace | domain | marginal ratio | joint ratio | A |")
    print("|---|---|---|---|---|")
    amps = {}
    for t in tids:
        mr = dv(t, "m_g1_median") / m_base
        jr = dv(t, "p_top1_rare") / j_base
        amps[t] = jr / mr
        if dom[t] == "programming":
            print(f"| {t.split('--')[0]} | programming | {mr:.2f} | {jr:.2f} | {jr/mr:.2f} |")
    code = [amps[t] for t in tids if dom[t] == "programming"]
    other = [amps[t] for t in tids if dom[t] != "programming"]
    print(f"| **code median (n=8)** | | | | **{st.median(code):.2f}** |")
    print(f"| **other-drift median (n=51)** | | | | **{st.median(other):.2f}** |")
    print(f"| other-drift range | | | | {min(other):.2f} - {max(other):.2f} |")
    print(f"| code range | | | | {min(code):.2f} - {max(code):.2f} |")
    n_above = sum(1 for t in tids if dom[t] != "programming" and amps[t] >= min(code))
    print(f"\nother-drift traces with A >= min(code A = {min(code):.2f}): {n_above}/51")

    print("\n### A by domain\n")
    print("| domain | n | A median | A range |")
    print("|---|---|---|---|")
    byd = {}
    for t in tids:
        byd.setdefault(dom[t], []).append(amps[t])
    for d, v in sorted(byd.items(), key=lambda kv: -st.median(kv[1])):
        print(f"| {d} | {len(v)} | {st.median(v):.2f} | {min(v):.2f} - {max(v):.2f} |")


def matched_pairs() -> None:
    """Section: each code trace vs the same-batch other-domain drift with the closest
    marginal (CAND-A g1) ratio -- the hardest honest control for the A statistic."""
    data = json.loads(CACHE.read_text(encoding="utf-8"))
    folds = data["folds"]

    def dv(tid, key):
        return fold_avg([folds[f]["drift"][tid]["product"].get(key) for f in ("0", "1")])

    def rv(key):
        return fold_avg([folds[f]["routine_holdout"].get(key) for f in ("0", "1")])

    m_base, j_base = rv("m_g1_median"), rv("r_top1_rare_mean")
    info = {}
    for tid, blk in folds["0"]["drift"].items():
        mr = dv(tid, "m_g1_median") / m_base
        jr = dv(tid, "p_top1_rare") / j_base
        info[tid] = {"domain": blk["domain"], "batch": blk["batch"],
                     "po": blk["product_onset"], "mr": mr, "jr": jr, "A": jr / mr}
    prog = sorted(t for t in info if info[t]["domain"] == "programming")
    others = [t for t in info if info[t]["domain"] != "programming"]
    print("\n## S. Matched control: same batch, closest marginal ratio\n")
    print("| code trace | product_onset | marg | joint | A | matched other-domain drift | domain | marg | joint | A |")
    print("|---" * 10 + "|")
    ma = []
    for t in prog:
        pool = [o for o in others if info[o]["batch"] == info[t]["batch"]] or others
        m = min(pool, key=lambda o: abs(info[o]["mr"] - info[t]["mr"]))
        ma.append(info[m]["A"])
        print(f"| {t.split('--')[0]} | {info[t]['po']} | {info[t]['mr']:.2f} | {info[t]['jr']:.2f} | "
              f"{info[t]['A']:.2f} | {m.split('--')[0]} | {info[m]['domain']} | {info[m]['mr']:.2f} | "
              f"{info[m]['jr']:.2f} | {info[m]['A']:.2f} |")
    print(f"\ncode A median {st.median([info[t]['A'] for t in prog]):.2f} vs matched A median {st.median(ma):.2f}")


if __name__ == "__main__":
    main()
    amplification()
    matched_pairs()
