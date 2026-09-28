"""Step 10: how correlated are the clean/benign siblings of one scenario in the calibration pool?

A scenario contributes two calibration traces; if their maxima are strongly correlated the
effective calibration size is closer to the scenario count than to the trace count."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import CANDIDATES, OUT, load_case_streams, rio, target_batch_of  # noqa
from fastcal import TraceStream, fit_bucket, trace_max  # noqa
from research_v2.harness import scenario_halves  # noqa
from research_v2.io import arm_class  # noqa


def spearman(a, b):
    ra = np.argsort(np.argsort(a)); rb = np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1])


def main():
    batches = rio.load_core()
    out = {}
    for key, cfg in CANDIDATES.items():
        out[key] = {}
        for case in ("b1_to_b2", "b2_to_b1"):
            payload, run, streams = load_case_streams(cfg["result"], case, cfg["window_width"])
            traces = batches[target_batch_of(case)]
            halves = scenario_halves(traces)
            rows = {}
            for h in (0, 1):
                cal = [t for t in traces if halves[t.pair_group_id] == h and not t.positive
                       and arm_class(t) in ("clean", "benign")
                       and streams[t.trace_id][1].numel()]
                S = [TraceStream(t.trace_id, streams[t.trace_id][1].numpy().astype(np.int64),
                                 streams[t.trace_id][0].numpy().astype(np.float64), False,
                                 arm_class(t), None, t.pair_group_id, t.token_count) for t in cal]
                mu, sd, cap = fit_bucket(S)
                mx = {s.trace_id: trace_max(s, mu, sd, cap) for s in S}
                pairs = {}
                for t in cal:
                    pairs.setdefault(t.pair_group_id, {})[arm_class(t)] = mx[t.trace_id]
                full = [(v["clean"], v["benign"]) for v in pairs.values()
                        if "clean" in v and "benign" in v]
                a = np.array([x for x, _ in full]); b = np.array([y for _, y in full])
                rows[h] = {"n_traces": len(S), "n_scenarios": len(pairs), "n_full_pairs": len(full),
                           "pearson": float(np.corrcoef(a, b)[0, 1]),
                           "spearman": spearman(a, b),
                           "scenario_share_of_variance": float(
                               np.var((a + b) / 2, ddof=1) / np.var(np.concatenate([a, b]), ddof=1))}
            out[key][case] = rows
            for h in (0, 1):
                r = rows[h]
                print(f"{key} {case} half{h}: n={r['n_traces']} scenarios={r['n_scenarios']} "
                      f"pearson(clean,benign)={r['pearson']:.3f} spearman={r['spearman']:.3f} "
                      f"scenario-mean var share={r['scenario_share_of_variance']:.3f}")
    (OUT / "step7_sibling.json").write_text(json.dumps(out, indent=1, default=float),
                                            encoding="utf-8")


if __name__ == "__main__":
    main()
