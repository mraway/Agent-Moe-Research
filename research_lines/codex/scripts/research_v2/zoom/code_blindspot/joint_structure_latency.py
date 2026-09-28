"""When inside the code product does the cross-layer novelty actually arrive?

For each of the 8 programming drifts: the offset (relative to product_onset) at which
the causal w=4 mean of the standardized D1 depth-chain surprisal (layers 5-11) first
reaches the held-out routine per-trace-max q90, compared with the offset of the first
literal code token (a descriptive text marker, not a detector) and with the frozen
CAND-B alarm offset read out of the frozen result.  Diagnostic only.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import torch

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from research_v2 import io as rio  # noqa: E402
from joint_structure_lens import (  # noqa: E402
    PDM_W,
    RoutineReference,
    load_labels,
    window_mean,
)

OUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
FROZEN = ROOT / "artifacts" / "agent_v2" / "research_v2" / "pdm_d1_middle_s1" / "result.json"
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
CODEY = re.compile(r"```|SELECT|FROM|WHERE|def |fn |function |=>|\{|\}|\(\)|;|::|->|    ")


def frozen_alarms() -> dict:
    d = json.loads(FROZEN.read_text(encoding="utf-8"))
    out = {}
    for cr in d["case_runs"]:
        for c in cr["candidates"]:
            if c["mode"] == "D" and abs(c["alpha"] - 0.10) < 1e-9 and c["reading"] == "persist2":
                for row in c["trace_alarms"]:
                    out[row[0]] = row[2]
    return out


def main() -> None:
    labels = load_labels()
    batches = rio.load_core()
    traces = [t for k in ("b1", "b2") for t in batches[k]]
    routine = [t for t in traces if rio.arm_class(t) in ("clean", "benign")]
    by_id = {t.trace_id: t for t in traces}
    groups = sorted({t.pair_group_id for t in routine})
    fold_of = {g: i % 2 for i, g in enumerate(groups)}
    folds = {f: [t for t in routine if fold_of[t.pair_group_id] == f] for f in (0, 1)}
    fa = frozen_alarms()

    rows = []
    for f in (0, 1):
        ref = RoutineReference(folds[f])
        maxima = []
        for t in folds[1 - f]:
            raw = ref.depth_surprisal(t.top_k_ids, "pdm", 0.5)
            z = (raw - ref.surp_mean[("pdm", 0.5)]) / ref.surp_sd[("pdm", 0.5)]
            w = window_mean(z, PDM_W)
            if w.numel():
                maxima.append(float(w.max()))
        thr = float(torch.quantile(torch.tensor(maxima), 0.90))
        for tid in PROG:
            t = by_id[tid]
            lab = labels[tid]
            po = lab["product_onset"]
            raw = ref.depth_surprisal(t.top_k_ids, "pdm", 0.5)
            z = (raw - ref.surp_mean[("pdm", 0.5)]) / ref.surp_sd[("pdm", 0.5)]
            w = window_mean(z, PDM_W)
            ends = torch.arange(PDM_W - 1, t.token_count, dtype=torch.long)
            hit = None
            for i in range(w.numel()):
                if int(ends[i]) >= po and float(w[i]) >= thr:
                    hit = int(ends[i]) - po
                    break
            rows.append({"fold": f, "trace_id": tid, "threshold": thr, "novelty_offset": hit})

    texts = {}
    for tid in PROG:
        t = by_id[tid]
        lab = labels[tid]
        po = lab["product_onset"]
        toks = rio.decode_token_texts(t.token_ids.tolist())
        first_code = None
        for i in range(po, min(po + 96, t.token_count)):
            if CODEY.search(toks[i]):
                first_code = i - po
                break
        texts[tid] = {
            "first_code_token_offset": first_code,
            "product_onset": po,
            "region_text": "".join(toks[po : min(po + 48, t.token_count)]),
        }

    res = {"rows": rows, "text": texts, "frozen_alarm_end": {t: fa.get(t) for t in PROG}}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "latency.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")

    print("\n## O. Where inside the code product does the chain novelty arrive?\n")
    print("| trace | product_onset | first literal code token (offset) | chain-novelty offset f0 / f1 | frozen CAND-B alarm offset |")
    print("|---|---|---|---|---|")
    for tid in PROG:
        r = [x for x in rows if x["trace_id"] == tid]
        po = texts[tid]["product_onset"]
        al = fa.get(tid)
        off = None if al is None else al - po
        print(f"| {tid.split('--')[0]} | {po} | {texts[tid]['first_code_token_offset']} | "
              f"{r[0]['novelty_offset']} / {r[1]['novelty_offset']} | {off} |")
    print("\nroutine trace-max q90 thresholds (fold 0 / fold 1): "
          f"{rows[0]['threshold']:.3f} / {rows[-1]['threshold']:.3f}")
    print("\n### product-region text (first 48 tokens)\n")
    for tid in PROG:
        print(f"- **{tid.split('--')[0]}**: `{texts[tid]['region_text'][:260]}`")


if __name__ == "__main__":
    main()
