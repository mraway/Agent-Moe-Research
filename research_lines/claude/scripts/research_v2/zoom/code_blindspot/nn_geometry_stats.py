"""Where exactly does code sit?  Percentiles, strict held-out variants, sub-population
distances and the P3 tool-call deliverables.  (diagnostic, post-hoc)"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import torch

from research_v2 import io as rio
from research_v2.scorers.wgm import WGMScorer
from nn_geometry_neighbours import form_label, own_label  # same ladder, single source

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
LABELS = REPO / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"
WIDTH, SPAN, K = 8, 48, 10
torch.set_num_threads(32)


def knn_masked(q, r, k, mask_cols=None, qgroup=None, rgroup=None, block=256):
    outs = []
    for s in range(0, q.shape[0], block):
        d = torch.cdist(q[s : s + block], r)
        if qgroup is not None:
            d = d.masked_fill(qgroup[s : s + block, None] == rgroup[None, :], float("inf"))
        if mask_cols is not None:
            d = d[:, mask_cols] if mask_cols.dtype == torch.long else d.masked_fill(~mask_cols[None, :], float("inf"))
        outs.append(torch.topk(d, min(k, d.shape[1]), dim=1, largest=False).values)
    return torch.cat(outs)


def qs(t, ps=(0.05, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99)):
    return {f"q{int(p*100):02d}": round(float(torch.quantile(t.float(), p)), 4) for p in ps}


def main(band="middle_late", anchor="product"):
    labels = {json.loads(l)["trace_id"]: json.loads(l) for l in LABELS.read_text().splitlines() if l.strip()}
    scorer = WGMScorer(window_width=WIDTH, layers=band, metric="g1")
    batches = rio.load_core()
    report = {"band": band, "anchor": anchor, "batches": {}}

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
        centre = state.centre
        r_pair = torch.tensor([hash(routine[int(i)].pair_group_id) for i in rti])
        r_wf = torch.tensor([hash(routine[int(i)].workflow) for i in rti])
        r_text = [rio.decode_text(routine[int(rti[g])].token_ids[int(rend[g]) - 7 : int(rend[g]) + 1])
                  for g in range(rti.numel())]
        r_form = [form_label(t) for t in r_text]
        is_json = torch.tensor([f in ("json_tool_call", "json_field") for f in r_form])
        is_prose = torch.tensor([f not in ("json_tool_call", "json_field") for f in r_form])
        is_tool = torch.tensor([f == "json_tool_call" for f in r_form])

        # -- routine held-out at three exclusion strengths ----------------------
        held = {}
        for name, grp in (("trace", rti), ("pair_group", r_pair), ("workflow", r_wf)):
            held[name] = knn_masked(Rz, Rz, K, qgroup=grp, rgroup=grp)
        r_centre_d = (Rz - centre).norm(dim=1)
        # routine sub-population cross distances
        sub = {
            "tool_json_to_tool_json": knn_masked(Rz[is_tool], Rz[is_tool], K, qgroup=rti[is_tool], rgroup=rti[is_tool])[:, 0],
            "prose_to_tool_json": knn_masked(Rz[is_prose], Rz, K, mask_cols=is_tool, qgroup=rti[is_prose], rgroup=rti)[:, 0],
            "prose_to_prose": knn_masked(Rz[is_prose], Rz, K, mask_cols=is_prose, qgroup=rti[is_prose], rgroup=rti)[:, 0],
            "tool_json_to_prose": knn_masked(Rz[is_tool], Rz, K, mask_cols=is_prose, qgroup=rti[is_tool], rgroup=rti)[:, 0],
        }

        # -- target windows -----------------------------------------------------
        rows = []
        for t in drift:
            lab = labels[t.trace_id]
            onset = int(lab[f"{anchor}_onset"])
            ends, win = scorer._windows(t)
            if not ends.numel():
                continue
            stop = min(onset + SPAN, t.token_count)
            sel = torch.nonzero((ends - (WIDTH - 1) >= onset) & (ends <= stop - 1)).flatten()
            if not sel.numel():
                continue
            Q = win[sel].float()
            Qz = (Q - state.mu) / state.sd
            d_all = knn_masked(Qz, Rz, K)
            d_tool = knn_masked(Qz, Rz, K, mask_cols=is_tool)[:, 0]
            d_prose = knn_masked(Qz, Rz, K, mask_cols=is_prose)[:, 0]
            d_centre = (Qz - centre).norm(dim=1)
            own = [own_label(rio.decode_text(t.token_ids[int(ends[j]) - 7 : int(ends[j]) + 1])) for j in sel]
            cb = torch.tensor([o == "code_body" for o in own])
            rows.append({
                "trace_id": t.trace_id, "domain": lab["domain"], "product_class": lab["product_class"],
                "workflow": t.workflow, "onset": onset, "n_windows": int(sel.numel()),
                "code_body_windows": int(cb.sum()),
                "d1_med": round(float(d_all[:, 0].median()), 3),
                "d10_med": round(float(d_all.mean(1).median()), 3),
                "d1_med_codebody": round(float(d_all[cb, 0].median()), 3) if bool(cb.any()) else None,
                "d1_med_prose": round(float(d_all[~cb, 0].median()), 3) if bool((~cb).any()) else None,
                "d_tool_med": round(float(d_tool.median()), 3),
                "d_prose_med": round(float(d_prose.median()), 3),
                "d_centre_med": round(float(d_centre.median()), 3),
                "pct_in_routine_trace_holdout": round(float((held["trace"][:, 0] < d_all[:, 0].median()).float().mean()), 4),
                "pct_in_routine_wf_holdout": round(float((held["workflow"][:, 0] < d_all[:, 0].median()).float().mean()), 4),
                "frac_win_below_routine_q95": round(float((d_all[:, 0] < torch.quantile(held["trace"][:, 0], 0.95)).float().mean()), 4),
                "frac_win_below_routine_wf_q95": round(float((d_all[:, 0] < torch.quantile(held["workflow"][:, 0], 0.95)).float().mean()), 4),
                "frac_win_below_routine_q50": round(float((d_all[:, 0] < torch.quantile(held["trace"][:, 0], 0.50)).float().mean()), 4),
                "frac_win_inside_tool_lobe_q95": round(float((d_tool < torch.quantile(sub["tool_json_to_tool_json"], 0.95)).float().mean()), 4),
                "d1_q": qs(d_all[:, 0]),
            })

        report["batches"][batch] = {
            "n_routine_windows": int(Rz.shape[0]), "n_routine_traces": len(routine),
            "routine_form_counts": {f: int(sum(1 for x in r_form if x == f)) for f in set(r_form)},
            "routine_holdout_d1": {name: qs(h[:, 0]) for name, h in held.items()},
            "routine_holdout_d10": {name: qs(h.mean(1)) for name, h in held.items()},
            "routine_centre_d": qs(r_centre_d),
            "routine_subpop_d1": {k: qs(v) for k, v in sub.items()},
            "per_trace": rows,
        }
        print(f"--- {batch}: routine d1 holdout(trace/pair/wf) med "
              f"{float(held['trace'][:,0].median()):.2f}/{float(held['pair_group'][:,0].median()):.2f}/"
              f"{float(held['workflow'][:,0].median()):.2f}; centre-d med {float(r_centre_d.median()):.2f}", flush=True)
        for k, v in sub.items():
            print(f"    subpop {k:24s} d1 med {float(v.median()):.2f}")
        for r in rows:
            if r["domain"] == "programming" or r["product_class"].startswith("P3"):
                print(f"    {r['trace_id'][:50]:50s} {r['product_class']:3s} d1={r['d1_med']:6.2f} "
                      f"cb={r['d1_med_codebody']} pr={r['d1_med_prose']} tool={r['d_tool_med']:6.2f} "
                      f"prose={r['d_prose_med']:6.2f} centre={r['d_centre_med']:6.2f} "
                      f"pct_trace={r['pct_in_routine_trace_holdout']:.3f} pct_wf={r['pct_in_routine_wf_holdout']:.3f}")

    (OUT / f"stats_{band}_{anchor}.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    import sys
    main(*(sys.argv[1:] or []))
