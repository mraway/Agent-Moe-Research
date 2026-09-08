"""Fragility test for the 'code is closer to routine than every other drift'
ordering: resample the routine reference (traces) and refit the whitening.
Diagnostic, post-hoc; no detector, no threshold."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import torch

from research_v2 import io as rio
from research_v2.features import selection_rate_windows

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
LABELS = REPO / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"
W, SPAN, DEV = 8, 48, "cuda" if torch.cuda.is_available() else "cpu"
LAYERS = tuple(range(5, 16))
B = 40
FRAC = 0.5


def main(anchor="product", whitening="refit"):
    labels = {json.loads(l)["trace_id"]: json.loads(l) for l in LABELS.read_text().splitlines() if l.strip()}
    batches = rio.load_core()
    out = {}
    for batch, traces in batches.items():
        routine = [t for t in traces if rio.arm_class(t) in ("clean", "benign")]
        drift = [t for t in traces if rio.arm_class(t) == "drift"]
        rblocks = []
        for t in routine:
            ends, win = selection_rate_windows(t.top_k_ids, W, LAYERS)
            rblocks.append(win.float())
        qmap = {}
        for t in drift:
            lab = labels[t.trace_id]
            ends, win = selection_rate_windows(t.top_k_ids, W, LAYERS)
            onset = int(lab["product_onset"] if anchor == "product" else lab["evidence_onset"])
            stop = min(onset + SPAN, t.token_count)
            starts = ends - (W - 1)
            m = (starts >= onset) & (ends <= stop - 1)
            if not bool(m.any()):
                continue
            qmap[t.trace_id] = (win[m].float(), lab["domain"], lab["product_class"])
        Rall = torch.cat(rblocks)
        mu_full, sd_full = Rall.mean(0), Rall.std(0) + 1e-3
        g = torch.Generator().manual_seed(20260905)
        stats = defaultdict(list)
        wins = 0
        gaps = []
        for b in range(B):
            perm = torch.randperm(len(routine), generator=g)[: int(FRAC * len(routine))]
            R = torch.cat([rblocks[int(i)] for i in perm])
            if whitening == "refit":
                mu, sd = R.mean(0), R.std(0) + 1e-3
            else:
                mu, sd = mu_full, sd_full
            Rz = ((R - mu) / sd).to(DEV)
            med = {}
            for tid, (q, dom, pc) in qmap.items():
                Qz = ((q - mu) / sd).to(DEV)
                d = torch.cdist(Qz, Rz).min(1).values
                med[tid] = float(d.median())
                stats[tid].append(med[tid])
            code = [v for k, v in med.items() if qmap[k][1] == "programming"]
            other = [v for k, v in med.items() if qmap[k][1] != "programming" and not qmap[k][2].startswith("P3")]
            other_all = [v for k, v in med.items() if qmap[k][1] != "programming"]
            gaps.append(min(other) - max(code))
            wins += int(max(code) < min(other))
        out[batch] = {
            "n_resamples": B, "whitening": whitening, "routine_traces_per_resample": int(FRAC * len(routine)),
            "ordering_holds": wins, "gap_q": [round(float(torch.quantile(torch.tensor(gaps), p)), 3) for p in (0.0, 0.05, 0.5, 0.95, 1.0)],
            "per_trace": {k: {"med": round(float(torch.tensor(v).median()), 2),
                              "min": round(min(v), 2), "max": round(max(v), 2),
                              "domain": qmap[k][1], "pclass": qmap[k][2]} for k, v in stats.items()},
        }
        print(f"{batch} [{whitening}]: ordering(code strictly closest) holds in {wins}/{B} resamples; gap quantiles(min/5/50/95/max)={out[batch]['gap_q']}")
        for k, v in sorted(out[batch]["per_trace"].items(), key=lambda kv: kv[1]["med"])[:12]:
            print(f"   {k[:44]:44s} {v['domain']:16s} {v['pclass']:3s} med={v['med']:7.2f} [{v['min']:7.2f},{v['max']:7.2f}]")
    (OUT / f"refute_boot_{anchor}_{whitening}.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    import sys
    main(*(sys.argv[1:] or []))
