#!/usr/bin/env python
"""EXPLORATORY / DEVELOPMENT: tabulate the registered descriptive ablations."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path("artifacts/agent_v2/dataset_g/v3_2_dev_descriptive")
A2 = Path("artifacts/agent_v2/dataset_g/v3_2_a2_verify/stage2/result.json")

RUNS = [
    ("PRIMARY (a2_verify stage2, two-stage)", A2, ("S", "P")),
    ("A-V1 baseline (single-stage replica)", ROOT / "selftest_V1" / "result.json", ("S", "P")),
    ("A-V2 (view V2)", ROOT / "abl_V2" / "result.json", ("S", "P")),
    ("A-V3 (view V3)", ROOT / "abl_V3" / "result.json", ("S", "P")),
    ("A-raw (--no-standardise)", ROOT / "abl_A_raw" / "result.json", ("S", "P")),
    ("A-no-bucket (bucket=1e5)", ROOT / "abl_A_nobucket" / "result.json", ("S", "P")),
    ("A-w4 (w=4)", ROOT / "abl_A_w4" / "result.json", ("S", "P")),
    ("B-B (depth chain, w=4)", ROOT / "abl_B" / "result.json", ("B", "P")),
]

hdr = ("run", "cell", "hit", "hit_n", "FAR_all", "FAR_filt", "silent", "silent_n",
       "worst_tertile", "onsets/1k", "x_beyond", "H", "alpha_eff_w")
print(" | ".join(hdr))
for name, path, cells in RUNS:
    if not path.exists():
        print(f"{name} | MISSING {path}")
        continue
    with path.open() as fh:
        d = json.load(fh)
    for c in cells:
        if c not in d["cells"]:
            continue
        m = d["cells"][c]["metrics"]
        xw = m["positives_anchored"]["recall"]["x_window"]
        sa = m["classes"]["silent_attack"]
        wt = m["far"]["worst_length_tertile"]
        print(" | ".join(str(v) for v in (
            name, c, f"{xw['recall']:.4f}", f"{xw['hit_count']}/{xw['reachable_count']}",
            f"{m['far']['all']['far']:.4f}", f"{m['far']['filtered']['far']:.4f}",
            f"{sa['far']:.4f}", f"{sa['alarm_count']}/{sa['episode_count']}",
            f"{wt[0]}:{wt[1]:.4f}",
            f"{m['endpoint']['alarm_onsets_per_1000_eligible']:.4f}",
            m["positives_anchored"]["reachability"]["x_beyond_h"],
            d["cells"][c]["fold_summary"]["H"][0],
            f"{d['cells'][c]['fold_summary']['alpha_eff_weighted']:.4f}",
        )))
    ca = d.get("comparison_anchored")
    if ca:
        b = ca["bootstrap"]
        print(f"    -> delta={b['point_estimate']:.4f} ci={[round(x, 4) for x in b['ci']]} "
              f"p={b['mcnemar']['p_value']:.4g} pairs={b['pair_count']} "
              f"matched_alpha={ca['matched_alpha_secondary']['alpha']:.6f} "
              f"far_primary={ca['measured_far_primary']:.5f} "
              f"positives={d['cells'][cells[0]]['metrics']['positives_anchored']['count']} "
              f"view={d['view']['name']}")
    print(f"    -> cost: " + ", ".join(
        f"{c}: total {d['cells'][c]['cost']['total_seconds']:.2f}s, "
        f"{d['cells'][c]['cost']['seconds_per_1000_endpoints']:.5f}s/1k, "
        f"scored {d['cells'][c]['cost']['scored_endpoints']}"
        for c in cells if c in d["cells"]))
