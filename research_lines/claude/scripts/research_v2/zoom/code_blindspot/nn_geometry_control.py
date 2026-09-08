"""Control: what does a ROUTINE window's own k=10 neighbourhood look like (held out by
trace)?  Needed to tell 'code lands next to tool-call JSON' from 'tool-call JSON is a
hub that everything is nearest to'."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import torch

from research_v2 import io as rio
from research_v2.scorers.wgm import WGMScorer
from nn_geometry_neighbours import form_label

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
torch.set_num_threads(32)


def main(band="middle_late"):
    scorer = WGMScorer(window_width=8, layers=band, metric="g1")
    batches = rio.load_core()
    report = {}
    for batch, traces in batches.items():
        routine = [t for t in traces if rio.arm_class(t) in ("clean", "benign")]
        state = scorer.fit(routine)
        R, rti, rend = [], [], []
        for ti, t in enumerate(routine):
            ends, win = scorer._windows(t)
            R.append(win); rti.append(torch.full((ends.numel(),), ti)); rend.append(ends)
        R = torch.cat(R).float(); rti = torch.cat(rti).long(); rend = torch.cat(rend)
        Rz = (R - state.mu) / state.sd
        forms = [form_label(rio.decode_text(routine[int(rti[g])].token_ids[int(rend[g]) - 7 : int(rend[g]) + 1]))
                 for g in range(rti.numel())]
        mix = defaultdict(Counter)
        for s in range(0, Rz.shape[0], 512):
            d = torch.cdist(Rz[s : s + 512], Rz)
            d = d.masked_fill(rti[s : s + 512, None] == rti[None, :], float("inf"))
            idx = torch.topk(d, 10, dim=1, largest=False).indices
            for a in range(idx.shape[0]):
                own = forms[s + a]
                for j in idx[a]:
                    mix[own][forms[int(j)]] += 1
                    mix["_all"][forms[int(j)]] += 1
        base = Counter(forms)
        n = len(forms)
        def pct(c):
            tot = sum(c.values())
            return {k: round(v / tot, 4) for k, v in c.most_common()} | {"_n": tot}
        report[batch] = {"base_rate": {k: round(v / n, 4) for k, v in base.most_common()} | {"_n": n},
                         "neighbour_mix_by_own_form": {k: pct(v) for k, v in mix.items()}}
        print(f"=== {batch}")
        print(f"  base           {report[batch]['base_rate']}")
        for k in ["_all", "prose", "json_tool_call", "json_field", "kb_recitation", "list_markdown", "sign_off", "ids_dates_numbers"]:
            if k in mix:
                print(f"  own={k:18s} -> {pct(mix[k])}")
    (OUT / f"control_routine_neighbourhood_{band}.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    import sys
    main(*(sys.argv[1:] or []))
