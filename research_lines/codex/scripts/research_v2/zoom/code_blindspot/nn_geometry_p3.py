"""Detail view: the three P3 tool-call deliverable drifts vs the 8 programming drifts.

Prints, per window, the window's own decoded text and its 3 nearest routine windows
(whitened space, same-batch routine = clean + benign_control).
"""

from __future__ import annotations

import json
from pathlib import Path

import torch

from research_v2 import io as rio
from research_v2.scorers.wgm import WGMScorer
from nn_geometry_neighbours import form_label, own_label

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
LABELS = REPO / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"
WIDTH, SPAN = 8, 48
torch.set_num_threads(16)

TARGETS = {
    "b1-f1-058-knowledge_qa-packing-guide--attack",
    "b2-f0-052-subscription_and_knowledge-transit-route--attack",
    "b2-f2-040-warranty_and_knowledge-grocery-plan--attack",
}


def main(band="middle_late", anchor="product", stride=6):
    stride = int(stride)
    labels = {json.loads(l)["trace_id"]: json.loads(l) for l in LABELS.read_text().splitlines() if l.strip()}
    scorer = WGMScorer(window_width=WIDTH, layers=band, metric="g1")
    batches = rio.load_core()
    lines = []
    for batch, traces in batches.items():
        tgt = [t for t in traces if t.trace_id in TARGETS]
        if not tgt:
            continue
        routine = [t for t in traces if rio.arm_class(t) in ("clean", "benign")]
        state = scorer.fit(routine)
        R, rti, rend = [], [], []
        for ti, t in enumerate(routine):
            ends, win = scorer._windows(t)
            R.append(win); rti.append(torch.full((ends.numel(),), ti)); rend.append(ends)
        R = torch.cat(R).float(); rti = torch.cat(rti).long(); rend = torch.cat(rend)
        Rz = (R - state.mu) / state.sd
        r_text = [rio.decode_text(routine[int(rti[g])].token_ids[int(rend[g]) - 7 : int(rend[g]) + 1])
                  for g in range(rti.numel())]
        for t in tgt:
            lab = labels[t.trace_id]
            onset = int(lab[f"{anchor}_onset"])
            ends, win = scorer._windows(t)
            stop = min(onset + SPAN, t.token_count)
            sel = torch.nonzero((ends - (WIDTH - 1) >= onset) & (ends <= stop - 1)).flatten()
            Qz = (win[sel].float() - state.mu) / state.sd
            d = torch.cdist(Qz, Rz)
            vals, idx = torch.topk(d, 3, dim=1, largest=False)
            lines.append(f"### {t.trace_id}  class={lab['product_class']} {anchor}_onset={onset} wf={t.workflow}")
            for a in range(0, sel.numel(), stride):
                own = rio.decode_text(t.token_ids[int(ends[sel[a]]) - 7 : int(ends[sel[a]]) + 1])
                lines.append(f"  end={int(ends[sel[a]]):3d} own[{own_label(own)}]={own!r}")
                for j in range(3):
                    lines.append(f"      d={float(vals[a,j]):6.2f} [{form_label(r_text[int(idx[a,j])])}] {r_text[int(idx[a,j])]!r}")
    text = "\n".join(lines)
    (OUT / f"p3_detail_{band}_{anchor}.txt").write_text(text)
    print(text)


if __name__ == "__main__":
    import sys
    main(*(sys.argv[1:] or []))
