"""Does lowering CAND-B's smoothing rescue the code traces inside onset..+16?

Same w=4 max of the standardized D1 depth-chain surprisal (layers 5-11), restricted to
product_onset..+16, at smoothing 0.5 / 0.05 / 0.005, each compared with the held-out
routine per-trace-max q90 at the SAME smoothing.  Diagnostic only.
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
    PDM_W,
    SMOOTHINGS,
    RoutineReference,
    load_labels,
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


def main() -> None:
    labels = load_labels()
    batches = rio.load_core()
    traces = [t for k in ("b1", "b2") for t in batches[k]]
    routine = [t for t in traces if rio.arm_class(t) in ("clean", "benign")]
    drift = [t for t in traces if t.positive]
    groups = sorted({t.pair_group_id for t in routine})
    fold_of = {g: i % 2 for i, g in enumerate(groups)}
    folds = {f: [t for t in routine if fold_of[t.pair_group_id] == f] for f in (0, 1)}

    acc: dict = {s: {"thr": [], "code": {t: [] for t in PROG}, "other": {}} for s in SMOOTHINGS}
    for f in (0, 1):
        ref = RoutineReference(folds[f])
        for s in SMOOTHINGS:
            maxima = []
            for t in folds[1 - f]:
                raw = ref.depth_surprisal(t.top_k_ids, "pdm", s)
                z = (raw - ref.surp_mean[("pdm", s)]) / ref.surp_sd[("pdm", s)]
                w = window_mean(z, PDM_W)
                if w.numel():
                    maxima.append(float(w.max()))
            acc[s]["thr"].append(float(torch.quantile(torch.tensor(maxima), 0.90)))
            for t in drift:
                lab = labels[t.trace_id]
                po = lab["product_onset"]
                raw = ref.depth_surprisal(t.top_k_ids, "pdm", s)
                z = (raw - ref.surp_mean[("pdm", s)]) / ref.surp_sd[("pdm", s)]
                w = window_mean(z, PDM_W)
                ends = torch.arange(PDM_W - 1, t.token_count, dtype=torch.long)
                m = (ends >= po) & (ends < min(po + 16, t.token_count))
                v = float(w[m].max()) if int(m.sum()) else None
                if t.trace_id in acc[s]["code"]:
                    acc[s]["code"][t.trace_id].append(v)
                elif t.domain != "programming":
                    acc[s]["other"].setdefault(t.trace_id, []).append(v)

    print("\n## P. Inside onset..+16 only: w=4 max standardized D1 z by smoothing\n")
    print("| smoothing | " + " | ".join(t.split("--")[0][:12] for t in PROG) +
          " | routine trace-max q90 | code above | other-drift above |")
    print("|---" * (len(PROG) + 4) + "|")
    rows = {}
    for s in SMOOTHINGS:
        thr = st.mean(acc[s]["thr"])
        cv = [st.mean([x for x in acc[s]["code"][t] if x is not None]) for t in PROG]
        ov = [st.mean([x for x in v if x is not None]) for v in acc[s]["other"].values()]
        rows[s] = (thr, cv, ov)
        print(f"| {s} | " + " | ".join(f"{v:.2f}" for v in cv) + f" | {thr:.2f} | "
              f"{sum(v >= thr for v in cv)}/8 | {sum(v >= thr for v in ov)}/{len(ov)} |")
    (OUT / "smoothing16.json").write_text(
        json.dumps({str(s): {"threshold": rows[s][0], "code": rows[s][1], "other": rows[s][2]}
                    for s in SMOOTHINGS}, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
