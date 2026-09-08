"""The routine cloud's tool-JSON <-> prose axis, and where code sits on it.

u = unit vector from the routine prose centroid to the routine tool-call-JSON centroid
in CAND-A's whitened space.  Reports the projection distribution of routine prose,
routine tool JSON, code windows and other-drift windows, plus the share of routine
variance that this single axis carries.
"""

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
WIDTH, SPAN = 8, 48
torch.set_num_threads(32)


def qs(t, ps=(0.05, 0.25, 0.5, 0.75, 0.95)):
    return [round(float(torch.quantile(t.float(), p)), 3) for p in ps]


def main(band="middle_late", anchor="product"):
    labels = {json.loads(l)["trace_id"]: json.loads(l) for l in LABELS.read_text().splitlines() if l.strip()}
    scorer = WGMScorer(window_width=WIDTH, layers=band, metric="g1")
    batches = rio.load_core()
    report = {}
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
        c_tool, c_prose = Rz[is_tool].mean(0), Rz[is_prose].mean(0)
        sep = (c_tool - c_prose).norm()
        u = (c_tool - c_prose) / sep
        centre = state.centre
        proj_r = (Rz - centre) @ u
        total_var = float(((Rz - centre) ** 2).sum(1).mean())
        axis_var = float((proj_r ** 2).mean())
        rows = {
            "lobe_separation": round(float(sep), 3),
            "axis_share_of_routine_variance": round(axis_var / total_var, 4),
            "routine_all": qs(proj_r), "routine_prose": qs(proj_r[is_prose]), "routine_tool_json": qs(proj_r[is_tool]),
            "per_trace": {},
        }
        by_dom = defaultdict(list)
        for t in drift:
            lab = labels[t.trace_id]
            onset = int(lab[f"{anchor}_onset"])
            ends, win = scorer._windows(t)
            stop = min(onset + SPAN, t.token_count)
            sel = torch.nonzero((ends - (WIDTH - 1) >= onset) & (ends <= stop - 1)).flatten()
            if not sel.numel():
                continue
            Qz = (win[sel].float() - state.mu) / state.sd
            p = (Qz - centre) @ u
            own = [own_label(rio.decode_text(t.token_ids[int(ends[j]) - 7 : int(ends[j]) + 1])) for j in sel]
            cb = torch.tensor([o == "code_body" for o in own])
            rows["per_trace"][t.trace_id] = {
                "domain": lab["domain"], "product_class": lab["product_class"],
                "proj_q": qs(p),
                "proj_med_codebody": round(float(p[cb].median()), 3) if bool(cb.any()) else None,
                "frac_beyond_tool_q50": round(float((p > torch.quantile(proj_r[is_tool], 0.5)).float().mean()), 3),
            }
            by_dom[lab["domain"]].append(float(p.median()))
        rows["domain_median_proj"] = {d: round(float(torch.tensor(v).median()), 3) for d, v in by_dom.items()}
        report[batch] = rows
        print(f"=== {batch}: lobe separation {rows['lobe_separation']}, axis carries "
              f"{rows['axis_share_of_routine_variance']:.3f} of routine variance")
        print(f"  routine all       {rows['routine_all']}")
        print(f"  routine prose     {rows['routine_prose']}")
        print(f"  routine tool_json {rows['routine_tool_json']}")
        for tid, r in rows["per_trace"].items():
            if r["domain"] == "programming" or r["product_class"].startswith("P3"):
                print(f"  {tid[:52]:52s} {r['product_class']:3s} proj {r['proj_q']} cb_med={r['proj_med_codebody']} "
                      f"frac>tool_q50={r['frac_beyond_tool_q50']}")
        print("  domain median proj:", rows["domain_median_proj"])
    (OUT / f"axis_{band}_{anchor}.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    import sys
    main(*(sys.argv[1:] or []))
