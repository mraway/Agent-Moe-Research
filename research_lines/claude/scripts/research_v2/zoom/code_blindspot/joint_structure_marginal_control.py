"""Control: is the code top-1 depth-pair novelty genuinely JOINT, or just a shadow of
per-layer marginal novelty?

For every (token, adjacent layer pair) instance we split the unseen top-1 pairs into
  * "novel combination of familiar experts": the pair (a, b) is unseen in routine while
    BOTH a (at layer l) and b (at layer l+1) are individually routine-common
    (routine top-1 marginal frequency >= 1/64, the uniform rate);
  * "novel expert": the pair is unseen and at least one of a, b is individually rare.
Also reports the marginal-only novelty rate (fraction of (token, layer) top-1 experts
that are individually rare / unseen), which is what a per-layer marginal detector sees.

Diagnostic only.
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
from joint_structure_lens import EXPERTS, RoutineReference, load_labels, region_slice  # noqa: E402

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
UNIFORM = 1.0 / EXPERTS


def top1_marginals(traces) -> torch.Tensor:
    """[16, 64] routine top-1 marginal frequency per layer."""
    counts = torch.zeros((16, EXPERTS), dtype=torch.float64)
    n = 0
    for t in traces:
        top1 = t.top_k_ids.long()[:, :, 0]
        n += top1.shape[1]
        for l in range(16):
            counts[l] += torch.bincount(top1[l], minlength=EXPERTS)
    return counts / n


def split_rates(ref: RoutineReference, marg: torch.Tensor, ids, lo, hi) -> dict:
    top1 = ids.long()[:, lo:hi, 0]
    joint_novel, expert_novel, tot = 0, 0, 0
    marg_rare, marg_unseen, mtot = 0, 0, 0
    for l in range(15):
        a, b = top1[l], top1[l + 1]
        unseen = ref.top1_pair_freq[l][a, b] == 0
        common = (marg[l][a] >= UNIFORM) & (marg[l + 1][b] >= UNIFORM)
        joint_novel += int((unseen & common).sum())
        expert_novel += int((unseen & ~common).sum())
        tot += int(a.numel())
    for l in range(16):
        e = top1[l]
        marg_rare += int((marg[l][e] < UNIFORM).sum())
        marg_unseen += int((marg[l][e] == 0).sum())
        mtot += int(e.numel())
    return {
        "pair_unseen_rate": (joint_novel + expert_novel) / tot,
        "joint_novel_rate": joint_novel / tot,
        "expert_novel_rate": expert_novel / tot,
        "joint_share_of_unseen": joint_novel / max(1, joint_novel + expert_novel),
        "marginal_rare_rate": marg_rare / mtot,
        "marginal_unseen_rate": marg_unseen / mtot,
    }


def main() -> None:
    labels = load_labels()
    batches = rio.load_core()
    traces = [t for k in ("b1", "b2") for t in batches[k]]
    routine = [t for t in traces if rio.arm_class(t) in ("clean", "benign")]
    drift = [t for t in traces if t.positive]
    groups = sorted({t.pair_group_id for t in routine})
    fold_of = {g: i % 2 for i, g in enumerate(groups)}
    folds = {f: [t for t in routine if fold_of[t.pair_group_id] == f] for f in (0, 1)}

    res: dict = {"folds": {}}
    for f in (0, 1):
        ref = RoutineReference(folds[f])
        marg = top1_marginals(folds[f])
        block = {"drift": {}, "routine": {}}
        rr = [split_rates(ref, marg, t.top_k_ids, 0, t.token_count) for t in folds[1 - f]]
        block["routine"] = {k: st.mean([r[k] for r in rr]) for k in rr[0]}
        for t in drift:
            lab = labels[t.trace_id]
            lo, hi = region_slice(t, lab["product_onset"])
            block["drift"][t.trace_id] = dict(
                split_rates(ref, marg, t.top_k_ids, lo, hi), domain=t.domain
            )
        res["folds"][str(f)] = block
        print(f"fold {f} done", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "marginal_control.json").write_text(json.dumps(res, indent=1), encoding="utf-8")

    F = res["folds"]

    def fa(v):
        return sum(v) / len(v)

    others = [t for t in F["0"]["drift"] if F["0"]["drift"][t]["domain"] != "programming"]
    keys = ["marginal_rare_rate", "marginal_unseen_rate", "pair_unseen_rate",
            "joint_novel_rate", "expert_novel_rate", "joint_share_of_unseen"]
    print("\n## R. Marginal control: novel combination of familiar experts vs novel expert\n")
    print("| measure | routine | " + " | ".join(t.split("--")[0][:12] for t in PROG) +
          " | code median | other median |")
    print("|---" * (len(PROG) + 4) + "|")
    for k in keys:
        rv = fa([F[f]["routine"][k] for f in ("0", "1")])
        cv = [fa([F[f]["drift"][t][k] for f in ("0", "1")]) for t in PROG]
        ov = [fa([F[f]["drift"][t][k] for f in ("0", "1")]) for t in others]
        print(f"| {k} | {rv:.4f} | " + " | ".join(f"{v:.3f}" for v in cv) +
              f" | {st.median(cv):.3f} | {st.median(ov):.3f} |")
    print("\n### ratio to routine\n")
    print("| measure | " + " | ".join(t.split("--")[0][:12] for t in PROG) +
          " | code median | other median |")
    print("|---" * (len(PROG) + 3) + "|")
    for k in keys:
        rv = fa([F[f]["routine"][k] for f in ("0", "1")])
        cv = [fa([F[f]["drift"][t][k] for f in ("0", "1")]) / rv for t in PROG]
        ov = [fa([F[f]["drift"][t][k] for f in ("0", "1")]) / rv for t in others]
        print(f"| {k} | " + " | ".join(f"{v:.2f}" for v in cv) +
              f" | {st.median(cv):.2f} | {st.median(ov):.2f} |")


if __name__ == "__main__":
    main()
