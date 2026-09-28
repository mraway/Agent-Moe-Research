"""Step 9: size-matched random-drop control for the class-based pool edits of step 5.

Dropping a class also shrinks the pool, which moves the order-statistic rank.  This control
separates the two: for each class-based drop, 200 random drops of the same size."""
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

RNG = np.random.default_rng(4242)
DRAWS = 200


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
            cls = {}
            for h in (0, 1):
                mu, sd, cap = fit_bucket(cal[h])
                for s in cal[h]:
                    b = np.minimum(s.ends // 32, cap)
                    z = (s.scores - mu[b]) / sd[b]
                    p = np.full(z.shape, -np.inf)
                    p[1:] = np.minimum(z[1:], z[:-1])
                    i = int(np.argmax(p))
                    cls[s.trace_id] = classify(decode_window(tmap[s.trace_id],
                                                             int(s.ends[i]), 32))[0]
            entry = {}
            for label, drop in (("drop_degenerate", {"degenerate"}),
                                ("drop_protocol_json", {"protocol_json"}),
                                ("drop_offtopic_disclaimer", {"offtopic_disclaimer"})):
                rows = {}
                for h in (0, 1):
                    n_drop = sum(1 for s in cal[h] if cls[s.trace_id] in drop)
                    if n_drop == 0:
                        rows[h] = {"n_drop": 0, "observed": None, "random": None}
                        continue
                    kept = [s for s in cal[h] if cls[s.trace_id] not in drop]
                    mu, sd, cap = fit_bucket(kept)
                    thr, _ = conformal([trace_max(s, mu, sd, cap) for s in kept], ALPHA)
                    obs_far = evaluate(ev[h], mu, sd, cap, thr)["far"]
                    ths, fars = [], []
                    for _ in range(DRAWS):
                        idx = RNG.choice(len(cal[h]), size=len(cal[h]) - n_drop, replace=False)
                        sub = [cal[h][i] for i in idx]
                        mu2, sd2, cap2 = fit_bucket(sub)
                        t2, _ = conformal([trace_max(s, mu2, sd2, cap2) for s in sub], ALPHA)
                        ths.append(t2)
                        fars.append(evaluate(ev[h], mu2, sd2, cap2, t2)["far"])
                    ths = np.array(ths); fars = np.array(fars)
                    rows[h] = {
                        "n_drop": n_drop,
                        "observed_threshold": thr, "observed_far": obs_far,
                        "random_threshold_median": float(np.median(ths)),
                        "random_threshold_p05": float(np.percentile(ths, 5)),
                        "random_threshold_p95": float(np.percentile(ths, 95)),
                        "threshold_percentile_of_observed": float(np.mean(ths <= thr)),
                        "random_far_median": float(np.median(fars)),
                        "far_percentile_of_observed": float(np.mean(fars <= obs_far)),
                    }
                entry[label] = rows
            out[key][case] = entry
            print(f"=== {key} {case}")
            for label, rows in entry.items():
                for h in (0, 1):
                    r = rows[h]
                    if not r["n_drop"]:
                        continue
                    print(f"  {label:26s} half{h} drop={r['n_drop']:2d} "
                          f"obs h={r['observed_threshold']:.3f} (random median "
                          f"{r['random_threshold_median']:.3f} "
                          f"[{r['random_threshold_p05']:.3f},{r['random_threshold_p95']:.3f}], "
                          f"pct={r['threshold_percentile_of_observed']:.2f}) | "
                          f"obs FAR {r['observed_far']:.3f} (random median "
                          f"{r['random_far_median']:.3f}, pct={r['far_percentile_of_observed']:.2f})")
    (OUT / "step6_dropcontrol.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
