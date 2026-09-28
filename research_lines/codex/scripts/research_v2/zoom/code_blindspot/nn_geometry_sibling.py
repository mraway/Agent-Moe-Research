"""Robustness: does the code window's own scenario sibling (the clean / benign arm of the
same pair_group, which IS in the routine pool) explain its small NN distance?"""

from __future__ import annotations

import json
from pathlib import Path

import torch

from research_v2 import io as rio
from research_v2.scorers.wgm import WGMScorer

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
LABELS = REPO / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"
torch.set_num_threads(32)


def main(band="middle_late", anchor="product"):
    labels = {json.loads(l)["trace_id"]: json.loads(l) for l in LABELS.read_text().splitlines() if l.strip()}
    scorer = WGMScorer(window_width=8, layers=band, metric="g1")
    out = {}
    for batch, traces in rio.load_core().items():
        routine = [t for t in traces if rio.arm_class(t) in ("clean", "benign")]
        drift = [t for t in traces if rio.arm_class(t) == "drift"]
        state = scorer.fit(routine)
        R, rpg, rwf = [], [], []
        for t in routine:
            ends, win = scorer._windows(t)
            R.append(win)
            rpg += [t.pair_group_id] * ends.numel()
            rwf += [t.workflow] * ends.numel()
        Rz = (torch.cat(R).float() - state.mu) / state.sd
        rpg = list(rpg); rwf = list(rwf)
        rows = {}
        for t in drift:
            lab = labels[t.trace_id]
            onset = int(lab[f"{anchor}_onset"])
            ends, win = scorer._windows(t)
            stop = min(onset + 48, t.token_count)
            sel = torch.nonzero((ends - 7 >= onset) & (ends <= stop - 1)).flatten()
            if not sel.numel():
                continue
            Qz = (win[sel].float() - state.mu) / state.sd
            d = torch.cdist(Qz, Rz)
            same_pg = torch.tensor([p == t.pair_group_id for p in rpg])
            same_wf = torch.tensor([w == t.workflow for w in rwf])
            rows[t.trace_id] = {
                "domain": lab["domain"], "product_class": lab["product_class"],
                "d1_all": round(float(d.min(1).values.median()), 3),
                "d1_excl_pair_group": round(float(d.masked_fill(same_pg[None, :], float("inf")).min(1).values.median()), 3),
                "d1_excl_workflow": round(float(d.masked_fill(same_wf[None, :], float("inf")).min(1).values.median()), 3),
                "share_nn_from_own_pair_group": round(float(same_pg[d.argmin(1)].float().mean()), 3),
            }
        out[batch] = rows
        print(f"=== {batch}")
        for tid, r in rows.items():
            if r["domain"] == "programming" or r["product_class"].startswith("P3"):
                print(f"  {tid[:50]:50s} {r['product_class']:3s} all={r['d1_all']:6.2f} "
                      f"exPG={r['d1_excl_pair_group']:6.2f} exWF={r['d1_excl_workflow']:6.2f} "
                      f"nn_from_sibling={r['share_nn_from_own_pair_group']:.3f}")
    (OUT / f"sibling_{band}_{anchor}.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    import sys
    main(*(sys.argv[1:] or []))
