"""Decompose CAND-A's statistic ||z-centre||^2 into the routine bimodality axis u and
its orthogonal complement, for routine (by form), code and other-drift windows."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import torch

from research_v2 import io as rio
from research_v2.scorers.wgm import WGMScorer
from nn_geometry_neighbours import form_label, own_label

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
LABELS = REPO / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"
torch.set_num_threads(32)


def qs(t, ps=(0.05, 0.5, 0.95)):
    return [round(float(torch.quantile(t.float(), p)), 2) for p in ps]


def main(band="middle_late", anchor="product"):
    labels = {json.loads(l)["trace_id"]: json.loads(l) for l in LABELS.read_text().splitlines() if l.strip()}
    scorer = WGMScorer(window_width=8, layers=band, metric="g1")
    batches = rio.load_core()
    rep = {}
    for batch, traces in batches.items():
        routine = [t for t in traces if rio.arm_class(t) in ("clean", "benign")]
        drift = [t for t in traces if rio.arm_class(t) == "drift"]
        state = scorer.fit(routine)
        R, rti, rend = [], [], []
        for ti, t in enumerate(routine):
            ends, win = scorer._windows(t)
            R.append(win); rti.append(torch.full((ends.numel(),), ti)); rend.append(ends)
        R = torch.cat(R).float(); rti = torch.cat(rti).long(); rend = torch.cat(rend)
        Rz = (R - state.mu) / state.sd
        forms = [form_label(rio.decode_text(routine[int(rti[g])].token_ids[int(rend[g]) - 7 : int(rend[g]) + 1]))
                 for g in range(rti.numel())]
        is_tool = torch.tensor([f == "json_tool_call" for f in forms])
        is_prose = torch.tensor([f == "prose" for f in forms])
        c = state.centre
        u = (Rz[is_tool].mean(0) - Rz[is_prose].mean(0))
        u = u / u.norm()
        def dec(Z):
            d = Z - c
            par = d @ u
            tot = (d ** 2).sum(1)
            return tot, par ** 2, (tot - par ** 2).clamp_min(0)
        out = {}
        tot, par, orth = dec(Rz)
        for name, m in (("routine_all", torch.ones_like(is_tool)), ("routine_tool_json", is_tool), ("routine_prose", is_prose)):
            out[name] = {"n": int(m.sum()), "g1_total": qs(tot[m]), "along_u": qs(par[m]), "orthogonal": qs(orth[m]),
                         "dist": qs(tot[m].sqrt()), "orth_dist": qs(orth[m].sqrt())}
        per = {}
        by_dom = defaultdict(lambda: [[], []])
        for t in drift:
            lab = labels[t.trace_id]
            onset = int(lab[f"{anchor}_onset"])
            ends, win = scorer._windows(t)
            stop = min(onset + 48, t.token_count)
            sel = torch.nonzero((ends - 7 >= onset) & (ends <= stop - 1)).flatten()
            if not sel.numel():
                continue
            Z = (win[sel].float() - state.mu) / state.sd
            tt, pp, oo = dec(Z)
            per[t.trace_id] = {"domain": lab["domain"], "product_class": lab["product_class"],
                               "g1_total": qs(tt), "along_u": qs(pp), "orthogonal": qs(oo),
                               "dist": qs(tt.sqrt()), "orth_dist": qs(oo.sqrt())}
            by_dom[lab["domain"]][0].append(float(tt.median()))
            by_dom[lab["domain"]][1].append(float(oo.sqrt().median()))
        out["per_trace"] = per
        out["domain_median"] = {d: [round(float(torch.tensor(a).median()), 2), round(float(torch.tensor(b).median()), 2)]
                                for d, (a, b) in by_dom.items()}
        rep[batch] = out
        print(f"=== {batch}   [q05, q50, q95]")
        for k in ("routine_all", "routine_tool_json", "routine_prose"):
            r = out[k]
            print(f"  {k:18s} n={r['n']:6d} g1={r['g1_total']} dist={r['dist']} orth_dist={r['orth_dist']}")
        for tid, r in per.items():
            if r["domain"] == "programming" or r["product_class"].startswith("P3"):
                print(f"  {tid[:50]:50s} {r['product_class']:3s} g1={r['g1_total']} dist={r['dist']} orth_dist={r['orth_dist']}")
        print("  domain median [g1, orth_dist]:", out["domain_median"])
    (OUT / f"decomp_{band}_{anchor}.json").write_text(json.dumps(rep, indent=2))


if __name__ == "__main__":
    import sys
    main(*(sys.argv[1:] or []))
