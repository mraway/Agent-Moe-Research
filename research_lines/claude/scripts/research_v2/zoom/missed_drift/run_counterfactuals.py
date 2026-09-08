#!/usr/bin/env python3
"""Counterfactual re-scorings (mode D, alpha 0.10, persist2, S1 both directions).

Diagnostics only: every row is a post-hoc re-scoring of B1/B2 development data.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cf_scorers  # noqa: F401,E402  (registers the variants)
from common import OUT  # noqa: E402
from research_v2 import io as rio  # noqa: E402
from research_v2.harness import HarnessConfig, run_harness  # noqa: E402

torch.set_num_threads(6)

VARIANTS = [
    # --- CAND-A family -------------------------------------------------------
    ("A0_base_5_15_w8", "zoom_wgm_variant", {"layers": "middle_late"}, 8),
    ("A1_all16_w8", "zoom_wgm_variant", {"layers": "all"}, 8),
    ("A2_5_11_w8", "zoom_wgm_variant", {"layers": "middle"}, 8),
    ("A3_11_15_w8", "zoom_wgm_variant", {"layers": "late"}, 8),
    ("A4_5_15_w4", "zoom_wgm_variant", {"layers": "middle_late"}, 4),
    ("A5_prob_5_15_w8", "zoom_wgm_variant", {"layers": "middle_late", "feature": "probability"}, 8),
    ("A6_maxlayer_5_15_w8", "zoom_wgm_variant", {"layers": "middle_late", "aggregate": "maxlayer"}, 8),
    ("A7_maxlayer_5_15_w4", "zoom_wgm_variant", {"layers": "middle_late", "aggregate": "maxlayer"}, 4),
    # --- CAND-B family -------------------------------------------------------
    ("B0_base_5_11_w4", "zoom_pdm_band", {"layers": "middle"}, 4),
    ("B1_all16_w4", "zoom_pdm_band", {"layers": "all"}, 4),
    ("B2_5_15_w4", "zoom_pdm_band", {"layers": "middle_late"}, 4),
    ("B3_11_15_w4", "zoom_pdm_band", {"layers": "late"}, 4),
    ("B4_5_11_w8", "zoom_pdm_band", {"layers": "middle"}, 8),
    ("B5_maxlink_5_11_w4", "zoom_pdm_maxlink", {"layers": "middle"}, 4),
    ("B6_maxlink_5_15_w4", "zoom_pdm_maxlink", {"layers": "middle_late"}, 4),
]


def main() -> None:
    batches = rio.load_core()
    out: dict = {}
    for name, scorer, cfg, width in VARIANTS:
        start = time.time()
        config = HarnessConfig(
            scorer=scorer,
            scorer_config=cfg,
            windows=(width,),
            routines=("cb",),
            splits=("S1",),
            modes=("D",),
            alphas=(0.10,),
            readings=("persist2", "max"),
            bootstrap_draws=0,
            emit_q1=False,
            emit_audit=False,
            emit_ranking=False,
            store_score_streams=False,
        )
        result = run_harness(config, batches=batches)
        block = {}
        for case_run in result["case_runs"]:
            for cand in case_run["candidates"]:
                if not cand["candidate_id"].endswith("alpha=0.1|reading=persist2"):
                    continue
                m = cand["metrics"]
                hits16, hits8, misses = [], [], []
                for row in cand["trace_alarms"]:
                    pass
                block[case_run["case"]] = {
                    "far_all": m["non_drift_false_alarm_rate"],
                    "far_clean": m["by_arm"]["clean"]["false_alarm_rate"],
                    "far_benign": m["by_arm"]["benign"]["false_alarm_rate"],
                    "far_resist": m["by_arm"]["resist"]["false_alarm_rate"],
                    "pre_strict": m["onset_strict"]["pre_alarm_rate"],
                    "r8": m["onset_strict"]["recall_plus_8"],
                    "r16": m["onset_strict"]["recall_plus_16"],
                    "r16_tol": m["onset_tolerant"]["recall_plus_16"],
                    "rf": m["onset_strict"]["recall_final"],
                    "latency": m["onset_strict"]["median_latency"],
                    "alarm_onsets_per_1000": m["alarms_per_1000"]
                    if "alarms_per_1000" in m
                    else m["alarm_onsets_per_1000_negative_positions"],
                    "trace_alarms": cand["trace_alarms"],
                }
        out[name] = {"scorer": scorer, "config": cfg, "width": width, "cases": block}
        print(
            f"{name}: "
            + " | ".join(
                f"{c} FAR={b['far_all']:.3f} R8={b['r8']:.3f} R16={b['r16']:.3f}"
                for c, b in block.items()
            )
            + f"  ({time.time()-start:.0f}s)",
            flush=True,
        )
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "counterfactuals.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("written")


if __name__ == "__main__":
    main()
