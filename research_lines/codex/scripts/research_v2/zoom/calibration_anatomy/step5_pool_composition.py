"""Step 8: counterfactual calibration-pool edits (drop degenerate / protocol-shaped tails)
and the length dependence of the trace maximum."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ALPHA, CANDIDATES, OUT, decode_window, load_case_streams, rio, target_batch_of  # noqa
from fastcal import TraceStream, conformal, evaluate, fit_bucket, trace_max  # noqa
from research_v2.harness import scenario_halves  # noqa
from research_v2.io import arm_class  # noqa
from step4_classify import classify  # noqa


def main():
    batches = rio.load_core()
    out = {}
    for key, cfg in CANDIDATES.items():
        out[key] = {}
        for case in ("b1_to_b2", "b2_to_b1"):
            payload, run, streams = load_case_streams(cfg["result"], case, cfg["window_width"])
            traces = batches[target_batch_of(case)]
            tmap = {t.trace_id: t for t in traces}
            halves = scenario_halves(traces)
            S = {}
            for t in traces:
                sc, en = streams[t.trace_id]
                if en.numel() == 0:
                    continue
                S[t.trace_id] = TraceStream(t.trace_id, en.numpy().astype(np.int64),
                                            sc.numpy().astype(np.float64), t.positive,
                                            arm_class(t), t.evidence_onset, t.pair_group_id,
                                            t.token_count)
            cal = {h: [S[t.trace_id] for t in traces if t.trace_id in S
                       and halves[t.pair_group_id] == h and not t.positive
                       and arm_class(t) in ("clean", "benign")] for h in (0, 1)}
            ev = {h: [S[t.trace_id] for t in traces if t.trace_id in S
                      and halves[t.pair_group_id] != h] for h in (0, 1)}
            variants = {}
            # baseline classification of every calibration trace at its own argmax window
            cls = {}
            corr = {}
            for h in (0, 1):
                mu, sd, cap = fit_bucket(cal[h])
                for s in cal[h]:
                    b = np.minimum(s.ends // 32, cap)
                    z = (s.scores - mu[b]) / sd[b]
                    p = np.full(z.shape, -np.inf)
                    p[1:] = np.minimum(z[1:], z[:-1])
                    i = int(np.argmax(p))
                    win = decode_window(tmap[s.trace_id], int(s.ends[i]), 32)
                    cls[s.trace_id] = classify(win)[0]
                maxima = np.array([trace_max(s, mu, sd, cap) for s in cal[h]])
                lens = np.array([s.token_count for s in cal[h]], dtype=float)
                r = float(np.corrcoef(np.argsort(np.argsort(maxima)),
                                      np.argsort(np.argsort(lens)))[0, 1])
                corr[h] = {"spearman_max_vs_decode_len": r,
                           "n": len(cal[h]),
                           "frac_len_192": float(np.mean(lens >= 192))}
            for label, drop in (("baseline", set()),
                                ("drop_degenerate", {"degenerate"}),
                                ("drop_protocol_json", {"protocol_json"}),
                                ("drop_degenerate_and_protocol", {"degenerate", "protocol_json"}),
                                ("drop_offtopic_disclaimer", {"offtopic_disclaimer"})):
                res_h = {}
                for h in (0, 1):
                    kept = [s for s in cal[h] if cls[s.trace_id] not in drop]
                    mu, sd, cap = fit_bucket(kept)
                    thr, rank = conformal([trace_max(s, mu, sd, cap) for s in kept], ALPHA)
                    res = evaluate(ev[h], mu, sd, cap, thr)
                    res_h[h] = {"n_kept": len(kept), "n_dropped": len(cal[h]) - len(kept),
                                "threshold": thr, "cap": cap, "res": res}
                n0 = res_h[0]["res"]["n_neg"]; n1 = res_h[1]["res"]["n_neg"]
                d0 = res_h[0]["res"]["n_drift"]; d1 = res_h[1]["res"]["n_drift"]
                variants[label] = {
                    "half0": {k: v for k, v in res_h[0].items() if k != "res"},
                    "half1": {k: v for k, v in res_h[1].items() if k != "res"},
                    "far": (res_h[0]["res"]["far"] * n0 + res_h[1]["res"]["far"] * n1) / (n0 + n1),
                    "recall_8": (res_h[0]["res"]["recall_8"] * d0
                                 + res_h[1]["res"]["recall_8"] * d1) / (d0 + d1),
                    "recall_16": (res_h[0]["res"]["recall_16"] * d0
                                  + res_h[1]["res"]["recall_16"] * d1) / (d0 + d1),
                    "benign_far": {h: res_h[h]["res"]["by_arm"]["benign"] for h in (0, 1)},
                }
            class_counts = {}
            for h in (0, 1):
                for s in cal[h]:
                    class_counts[cls[s.trace_id]] = class_counts.get(cls[s.trace_id], 0) + 1
            out[key][case] = {"class_counts_calibration_pool": class_counts,
                              "length_correlation": corr, "variants": variants}
            print(f"=== {key} {case} pool classes {class_counts}")
            for h in (0, 1):
                print(f"   half{h} spearman(max, decode_len) = "
                      f"{corr[h]['spearman_max_vs_decode_len']:.3f}  frac len=192 "
                      f"{corr[h]['frac_len_192']:.2f}")
            for label, v in variants.items():
                print(f"   {label:32s} drop {v['half0']['n_dropped']}/{v['half1']['n_dropped']} "
                      f"h=({v['half0']['threshold']:.3f},{v['half1']['threshold']:.3f}) "
                      f"FAR {v['far']:.3f} r8 {v['recall_8']:.3f} r16 {v['recall_16']:.3f}")
    (OUT / "step5_pool_composition.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
