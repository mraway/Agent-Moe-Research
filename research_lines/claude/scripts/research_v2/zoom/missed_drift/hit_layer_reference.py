#!/usr/bin/env python3
"""Reference: the same per-layer ratio for the drift traces CAND-A does detect."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT, cases_for  # noqa: E402
from layer_anatomy import A_LAYERS, wgm_layer_contrib  # noqa: E402
from research_v2 import io as rio  # noqa: E402
from research_v2.harness import routine_traces  # noqa: E402
from research_v2.scorers.wgm import WGMScorer  # noqa: E402

torch.set_num_threads(6)


def main() -> None:
    misses = json.loads((OUT / "misses.json").read_text())
    batches = rio.load_core()
    cases = cases_for(batches)
    out = {}
    for case_name, target in (("b1_to_b2", "b2"), ("b2_to_b1", "b1")):
        case = cases[case_name]
        fit = routine_traces(case.fit_traces, "cb")
        scorer = WGMScorer(window_width=8, layers="middle_late", metric="g1")
        state = scorer.fit(fit)
        blocks = []
        for tr in fit:
            _, per, _ = wgm_layer_contrib(state, tr, A_LAYERS, 8)
            if per is not None:
                blocks.append(per)
        mean = torch.cat(blocks).mean(0)
        miss = {m["trace_id"] for m in misses["CAND-A"][case_name]["misses"]}
        rows = []
        for t in batches[target]:
            if not t.positive or t.trace_id in miss:
                continue
            ends, per, _ = wgm_layer_contrib(state, t, A_LAYERS, 8)
            idx = {int(e): i for i, e in enumerate(ends.tolist())}
            on = t.evidence_onset
            cand = [e for e in idx if on <= e <= on + 16]
            if not cand:
                continue
            best = max(cand, key=lambda e: float(per[idx[e]].sum()))
            ratio = per[idx[best]] / mean
            rows.append(
                {
                    "trace_id": t.trace_id,
                    "domain": t.scenario_domain,
                    "max_layer_ratio": round(float(ratio.max()), 2),
                    "min_layer_ratio": round(float(ratio.min()), 2),
                }
            )
        rows.sort(key=lambda r: r["max_layer_ratio"])
        med = rows[len(rows) // 2]["max_layer_ratio"] if rows else None
        out[case_name] = {"n": len(rows), "median_max_ratio": med, "rows": rows}
        print(case_name, "n=", len(rows), "median max ratio", med,
              "range", rows[0]["max_layer_ratio"], rows[-1]["max_layer_ratio"])
    (OUT / "hit_layer_reference.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
