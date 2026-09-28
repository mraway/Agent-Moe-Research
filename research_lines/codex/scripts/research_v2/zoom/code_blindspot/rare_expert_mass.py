"""LENS part 4: interpretable version of the variance story.

The whitening divides each (layer, expert) coordinate by its routine sd.  A
coordinate's routine sd is essentially a monotone function of how often routine
uses that expert, so "deviation in high-variance coordinates" == "re-weighting
experts routine already uses" and "deviation in low-variance coordinates" ==
"turning on experts routine (almost) never uses".  This script measures the
second directly: the share of a window's 11*8 = 88 top-8 slots that land on
experts whose routine mean selection rate is below a threshold.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from whitening_common import (  # noqa: E402
    CACHE, LAYERS, PROG_IDS, WINDOW_SPAN, label_by_short, load_all, short_id, windows_of,
)
from research_v2 import io as rio  # noqa: E402

THRESHOLDS = (0.001, 0.005, 0.02)


def main() -> None:
    batches = load_all()
    lab = label_by_short()
    routine = {"b1": [], "b2": []}
    drift = []
    for bkey, traces in batches.items():
        for tr in traces:
            cls = rio.arm_class(tr)
            ends, win = windows_of(tr)
            if not ends.numel():
                continue
            if cls in ("clean", "benign"):
                routine[bkey].append(win)
            elif cls == "drift":
                sid = short_id(tr.trace_id)
                row = lab[sid]
                T = int(tr.token_count)
                m = (ends >= int(row["product_onset"])) & (ends < min(int(row["product_onset"]) + WINDOW_SPAN, T))
                drift.append({"short": sid, "batch": bkey, "domain": row["domain"],
                              "win": win[m], "is_code": sid in PROG_IDS})

    R_all = torch.cat(routine["b1"] + routine["b2"])
    out = {}
    for fb in ("b1", "b2"):
        mu = torch.cat(routine[fb]).mean(0)     # routine mean selection rate per (layer, expert)
        entry = {}
        for th in THRESHOLDS:
            rare = mu < th
            nlay = len(LAYERS)
            def mass(M):
                # win entries are per-layer selection rates in [0,1]; each layer sums to 8.
                return (M[:, rare].sum(1) / (8.0 * nlay))
            doms = defaultdict(list)
            for d in drift:
                doms[d["domain"]].append(d["win"])
            entry[str(th)] = {
                "n_rare_coords": int(rare.sum()),
                "routine_mean_share": float(mass(R_all).mean()),
                "routine_median_share": float(mass(R_all).median()),
                "routine_q99_share": float(torch.quantile(mass(R_all).double(), 0.99)),
                "by_domain": {dom: {"mean_share": float(mass(torch.cat(v)).mean()),
                                    "median_share": float(mass(torch.cat(v)).median())}
                              for dom, v in doms.items()},
                "code_per_trace": {d["short"]: {"mean_share": float(mass(d["win"]).mean()),
                                                "median_share": float(mass(d["win"]).median())}
                                   for d in drift if d["is_code"]},
            }
        out[fb] = entry
    (CACHE / "rare_expert_mass.json").write_text(json.dumps(out, indent=2))
    for fb in ("b1", "b2"):
        for th in THRESHOLDS:
            e = out[fb][str(th)]
            print(f"fit={fb} th={th} rare_coords={e['n_rare_coords']}/704 "
                  f"routine mean={e['routine_mean_share']:.4f} med={e['routine_median_share']:.4f} q99={e['routine_q99_share']:.4f}")
            for dom, v in sorted(e["by_domain"].items(), key=lambda x: -x[1]["mean_share"]):
                print(f"    {dom:18s} mean={v['mean_share']:.4f} med={v['median_share']:.4f}")
            print("    code per trace:", ", ".join(f"{k}={v['mean_share']:.4f}" for k, v in e["code_per_trace"].items()))
    print("wrote", CACHE / "rare_expert_mass.json")


if __name__ == "__main__":
    main()
