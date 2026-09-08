"""Step 6: what the extra B1 brief=present routine pool does to the B2->B1 threshold tail."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    ALPHA, CANDIDATES, OUT, calibration_view, classify_output, decode_window, full_text,
    load_case_streams, rio, target_batch_of,
)
from research_v2.harness import (  # noqa: E402
    build_split_cases, conformal_threshold, fit_bucket_stats, routine_traces, scenario_halves,
)
from research_v2.scorers import build as build_scorer  # noqa: E402
from research_v2.readings import build_readings  # noqa: E402

torch.set_num_threads(6)
PERSIST2 = {r.name: r for r in build_readings()}["persist2"]

SCORERS = {
    "CAND-A": ("wgm", {"metric": "g1", "layers": "middle_late"}, 8),
    "CAND-B": ("pdm", {"model": "d1", "layers": "middle"}, 4),
}


def main():
    batches = rio.load_core()
    extras_all = rio.load_b1_present()
    case_name = "b2_to_b1"
    out = {}
    for key, (name, scfg, w) in SCORERS.items():
        cfg = CANDIDATES[key]
        payload, run, streams = load_case_streams(cfg["result"], case_name, w)
        case = [c for c in build_split_cases(batches, "S1") if c.name == case_name][0]
        scorer = build_scorer(name, {**scfg, "window_width": w})
        state = scorer.fit(routine_traces(case.fit_traces, "cb"))
        # sanity: the recomputed target stream must match the frozen one
        probe = case.target_traces[0]
        s, e = scorer.score(state, probe)
        ref = streams[probe.trace_id]
        delta = float((s.to(torch.float64) - ref[0]).abs().max())
        extras = routine_traces(extras_all, "cb")
        extra_streams = {}
        for t in extras:
            sc, en = scorer.score(state, t)
            extra_streams[t.trace_id] = (sc.detach().to(torch.float64), en.long())
        halves = scenario_halves(case.target_traces)
        extra_halves = scenario_halves(extras)
        target_routine = [
            t for t in routine_traces(case.target_traces, "cb")
            if streams[t.trace_id][1].numel()
        ]
        block = {"stream_check_max_abs_delta": delta, "n_extra_pool": len(extras), "halves": {}}
        for h in (0, 1):
            cal = [t for t in target_routine if halves[t.pair_group_id] == h]
            ex = [t for t in extras if extra_halves[t.pair_group_id] == h]
            pool = [(t, streams[t.trace_id], "target") for t in cal]
            pool += [(t, extra_streams[t.trace_id], "b1_present") for t in ex]
            stats = fit_bucket_stats([p[1] for p in pool], bucket_size=32,
                                     min_bucket_traces=30, bucket_cap=None,
                                     min_criterion="traces")
            recs = []
            for t, (sc, en), src in pool:
                z = stats.standardize(sc, en)
                stat = PERSIST2.apply(z)
                idx = int(torch.argmax(stat))
                recs.append({"trace": t, "src": src, "max": float(stat[idx]),
                             "argmax_end": int(en[idx])})
            blk = conformal_threshold([r["max"] for r in recs], ALPHA)
            ordered = sorted(recs, key=lambda r: -r["max"])
            setter = blk["trace_count"] - blk["order_statistic_rank"] + 1
            rows = []
            for i, r in enumerate(ordered[:8], start=1):
                t = r["trace"]
                text = full_text(t)
                rows.append({"rank_from_top": i, "is_threshold_setter": i == setter,
                             "source": r["src"], "trace_id": t.trace_id, "arm": t.arm,
                             "domain": t.scenario_domain, "workflow": t.workflow,
                             "decode_len": t.token_count, "statistic_max": r["max"],
                             "argmax_end": r["argmax_end"],
                             "shape_class": classify_output(text),
                             "snippet": decode_window(t, r["argmax_end"], 32)})
            block["halves"][h] = {
                "n_calibration": blk["trace_count"], "threshold": blk["threshold"],
                "order_statistic_rank": blk["order_statistic_rank"],
                "setter_index_from_top": setter, "bucket_cap": stats.cap,
                "bucket_trace_counts": stats.trace_counts,
                "top_source_counts": {
                    "target": sum(1 for r in ordered[:8] if r["src"] == "target"),
                    "b1_present": sum(1 for r in ordered[:8] if r["src"] == "b1_present")},
                "extra_max_quantiles": _q([r["max"] for r in recs if r["src"] == "b1_present"]),
                "target_max_quantiles": _q([r["max"] for r in recs if r["src"] == "target"]),
                "top": rows,
            }
        out[key] = block
        print(key, "stream delta", delta)
        for h in (0, 1):
            hb = block["halves"][h]
            print(f"  half{h} n={hb['n_calibration']} h={hb['threshold']:.4f} "
                  f"setter=top{hb['setter_index_from_top']} cap={hb['bucket_cap']} "
                  f"src={hb['top_source_counts']}")
            for r in hb["top"]:
                mark = "*" if r["is_threshold_setter"] else " "
                print(f"   {mark}#{r['rank_from_top']} {r['statistic_max']:.3f} [{r['source']}] "
                      f"{r['trace_id']} {r['arm']} len={r['decode_len']} "
                      f"end={r['argmax_end']} [{r['shape_class']}]")
                print(f"       {r['snippet']!r}")
    (OUT / "step3_b1present.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")


def _q(values):
    import numpy as np
    a = np.array(values, dtype=float)
    return {"n": int(a.size), "median": float(np.median(a)), "p90": float(np.percentile(a, 90)),
            "max": float(a.max()), "mean": float(a.mean())}


if __name__ == "__main__":
    main()
