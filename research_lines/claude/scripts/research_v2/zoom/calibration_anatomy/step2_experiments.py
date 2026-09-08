"""Steps 2-5: exclusion sensitivity, half spread, calibration-size study, margin distribution."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import ALPHA, CANDIDATES, OUT, load_case_streams, rio, target_batch_of  # noqa: E402
from fastcal import TraceStream, conformal, evaluate, fit_bucket, trace_max  # noqa: E402
from research_v2.harness import scenario_halves  # noqa: E402
from research_v2.io import arm_class  # noqa: E402

RNG = np.random.default_rng(20260905)
DRAWS = 200
SIZES = (20, 40, 60, 80)


def build(case, streams, traces):
    out = []
    for t in traces:
        sc, en = streams[t.trace_id]
        en = en.numpy()
        if en.size == 0:
            continue
        out.append(TraceStream(
            trace_id=t.trace_id, ends=en.astype(np.int64), scores=sc.numpy().astype(np.float64),
            positive=t.positive, arm_class=arm_class(t), onset=t.evidence_onset,
            pair_group=t.pair_group_id, token_count=t.token_count,
        ))
    return out


def pooled(a, b, key):
    """Pool two half-results by counts (both halves evaluate disjoint trace sets)."""
    na, nb = a["n_neg"], b["n_neg"]
    da, db = a["n_drift"], b["n_drift"]
    if key == "far":
        return (a["far"] * na + b["far"] * nb) / (na + nb)
    return (a[key] * da + b[key] * db) / (da + db)


def main():
    batches = rio.load_core()
    report = {}
    for key, cfg in CANDIDATES.items():
        report[key] = {"label": cfg["label"], "directions": {}}
        for case in ("b1_to_b2", "b2_to_b1"):
            payload, run, streams = load_case_streams(cfg["result"], case, cfg["window_width"])
            traces = batches[target_batch_of(case)]
            halves = scenario_halves(traces)
            all_streams = build(case, streams, traces)
            by_id = {s.trace_id: s for s in all_streams}
            tid_half = {t.trace_id: halves[t.pair_group_id] for t in traces}
            cal = {h: [s for s in all_streams if tid_half[s.trace_id] == h
                       and not s.positive and s.arm_class in ("clean", "benign")] for h in (0, 1)}
            ev = {h: [s for s in all_streams if tid_half[s.trace_id] != h] for h in (0, 1)}

            block = {"halves": {}, "exclusion": {}, "subsample": {}, "margins": {}}

            base = {}
            for h in (0, 1):
                mu, sd, cap = fit_bucket(cal[h])
                maxima = [trace_max(s, mu, sd, cap) for s in cal[h]]
                thr, rank = conformal(maxima, ALPHA)
                res = evaluate(ev[h], mu, sd, cap, thr)
                base[h] = {"mu": mu, "sd": sd, "cap": cap, "maxima": maxima,
                           "thr": thr, "rank": rank, "res": res}
                block["halves"][h] = {
                    "n_cal": len(cal[h]), "threshold": thr, "rank": rank, "bucket_cap": cap,
                    "n_eval": len(ev[h]), "far": res["far"], "by_arm": res["by_arm"],
                    "arm_counts": res["arm_counts"], "n_drift": res["n_drift"],
                    "recall_8": res["recall_8"], "recall_16": res["recall_16"],
                    "recall_final": res["recall_final"], "median_latency": res["median_latency"],
                    "pre_alarm_rate_all_drift": res["pre_alarm_rate_all_drift"],
                }
            block["pooled_base"] = {
                "far": pooled(base[0]["res"], base[1]["res"], "far"),
                "recall_8": pooled(base[0]["res"], base[1]["res"], "recall_8"),
                "recall_16": pooled(base[0]["res"], base[1]["res"], "recall_16"),
                "threshold_spread_abs": abs(base[0]["thr"] - base[1]["thr"]),
                "threshold_spread_rel": abs(base[0]["thr"] - base[1]["thr"])
                / (0.5 * (base[0]["thr"] + base[1]["thr"])),
            }
            # cross application of the other half's threshold to the same evaluated set
            block["cross_threshold"] = {}
            for h in (0, 1):
                other = base[1 - h]["thr"]
                res = evaluate(ev[h], base[h]["mu"], base[h]["sd"], base[h]["cap"], other)
                block["cross_threshold"][h] = {
                    "threshold_used": other, "far": res["far"],
                    "recall_8": res["recall_8"], "recall_16": res["recall_16"]}

            # ---- exclusion of the top-k calibration traces ------------------------
            for k in range(0, 6):
                per_half = {}
                for h in (0, 1):
                    m = sorted(base[h]["maxima"])
                    kept = m[: len(m) - k] if k else m
                    thr, rank = conformal(kept, ALPHA)
                    res = evaluate(ev[h], base[h]["mu"], base[h]["sd"], base[h]["cap"], thr)
                    per_half[h] = {"threshold": thr, "rank": rank, "n": len(kept),
                                   "far": res["far"], "recall_8": res["recall_8"],
                                   "recall_16": res["recall_16"], "res": res}
                block["exclusion"][k] = {
                    "half0_threshold": per_half[0]["threshold"],
                    "half1_threshold": per_half[1]["threshold"],
                    "far": pooled(per_half[0]["res"], per_half[1]["res"], "far"),
                    "recall_8": pooled(per_half[0]["res"], per_half[1]["res"], "recall_8"),
                    "recall_16": pooled(per_half[0]["res"], per_half[1]["res"], "recall_16"),
                }

            # ---- calibration-size study (resample by scenario) ---------------------
            for size in SIZES:
                per_half = {}
                for h in (0, 1):
                    groups = sorted({s.pair_group for s in cal[h]})
                    by_group = {g: [s for s in cal[h] if s.pair_group == g] for g in groups}
                    per_group = len(cal[h]) / len(groups)
                    n_groups = int(round(size / per_group))
                    if n_groups < 1 or n_groups > len(groups):
                        continue
                    rows = []
                    for _ in range(DRAWS):
                        pick = RNG.choice(len(groups), size=n_groups, replace=False)
                        sub = [s for i in pick for s in by_group[groups[i]]]
                        mu, sd, cap = fit_bucket(sub)
                        thr, rank = conformal([trace_max(s, mu, sd, cap) for s in sub], ALPHA)
                        res = evaluate(ev[h], mu, sd, cap, thr)
                        rows.append({"threshold": thr, "cap": cap, "n": len(sub),
                                     "far": res["far"], "recall_8": res["recall_8"],
                                     "recall_16": res["recall_16"],
                                     "benign": res["by_arm"]["benign"],
                                     "resist": res["by_arm"]["resist"]})
                    per_half[h] = rows
                if per_half:
                    block["subsample"][size] = {
                        str(h): summarize(rows) for h, rows in per_half.items()
                    }
                    # pooled over the two halves draw-by-draw (independent draws)
                    if len(per_half) == 2:
                        n0 = base[0]["res"]["n_neg"]; n1 = base[1]["res"]["n_neg"]
                        d0 = base[0]["res"]["n_drift"]; d1 = base[1]["res"]["n_drift"]
                        pf = [(a["far"] * n0 + b["far"] * n1) / (n0 + n1)
                              for a, b in zip(per_half[0], per_half[1])]
                        p8 = [(a["recall_8"] * d0 + b["recall_8"] * d1) / (d0 + d1)
                              for a, b in zip(per_half[0], per_half[1])]
                        p16 = [(a["recall_16"] * d0 + b["recall_16"] * d1) / (d0 + d1)
                               for a, b in zip(per_half[0], per_half[1])]
                        block["subsample"][size]["pooled"] = {
                            "far": q(pf), "recall_8": q(p8), "recall_16": q(p16),
                            "far_within_0.03_of_base": float(np.mean(
                                np.abs(np.array(pf) - block["pooled_base"]["far"]) <= 0.03)),
                            "far_le_0.15": float(np.mean(np.array(pf) <= 0.15)),
                        }

            # ---- margins ----------------------------------------------------------
            marg = []
            for h in (0, 1):
                for m in base[h]["res"]["margins"]:
                    m = dict(m)
                    m["cal_half"] = h
                    m["threshold"] = base[h]["thr"]
                    s = by_id[m["trace_id"]]
                    m["token_count"] = s.token_count
                    m["onset"] = s.onset
                    marg.append(m)
            block["margins"]["rows"] = marg
            hitm = [m["margin"] for m in marg if m["hit"]]
            missm = [m["margin"] for m in marg if not m["hit"] and not m["pre_alarm"]]
            prem = [m["margin"] for m in marg if m["pre_alarm"]]
            block["margins"]["summary"] = {
                "n_drift": len(marg), "n_hit": len(hitm), "n_miss_clean": len(missm),
                "n_pre_alarm": len(prem),
                "hit_margin": q(hitm), "miss_margin": q(missm), "pre_alarm_margin": q(prem),
                "miss_within_0.5": int(sum(1 for x in missm if x > -0.5)),
                "miss_within_1.0": int(sum(1 for x in missm if x > -1.0)),
                "hit_within_0.5": int(sum(1 for x in hitm if x < 0.5)),
                "hit_within_1.0": int(sum(1 for x in hitm if x < 1.0)),
            }
            # threshold-shift sweep: what a uniform multiplicative/additive shift buys
            sweep = []
            for delta in (-1.5, -1.0, -0.5, -0.25, 0.0, 0.25, 0.5, 1.0):
                res = {h: evaluate(ev[h], base[h]["mu"], base[h]["sd"], base[h]["cap"],
                                   base[h]["thr"] + delta) for h in (0, 1)}
                sweep.append({"delta": delta,
                              "far": pooled(res[0], res[1], "far"),
                              "recall_8": pooled(res[0], res[1], "recall_8"),
                              "recall_16": pooled(res[0], res[1], "recall_16")})
            block["margins"]["threshold_sweep"] = sweep
            report[key]["directions"][case] = block
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "step2_experiments.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print("written", OUT / "step2_experiments.json")


def q(values):
    a = np.array([v for v in values if v is not None], dtype=float)
    if not a.size:
        return None
    return {"n": int(a.size), "mean": float(a.mean()), "sd": float(a.std(ddof=1)) if a.size > 1 else 0.0,
            "p10": float(np.percentile(a, 10)), "p25": float(np.percentile(a, 25)),
            "median": float(np.median(a)), "p75": float(np.percentile(a, 75)),
            "p90": float(np.percentile(a, 90)), "min": float(a.min()), "max": float(a.max())}


def summarize(rows):
    return {
        "n_traces": q([r["n"] for r in rows]),
        "bucket_cap": q([r["cap"] for r in rows]),
        "threshold": q([r["threshold"] for r in rows]),
        "far": q([r["far"] for r in rows]),
        "recall_8": q([r["recall_8"] for r in rows]),
        "recall_16": q([r["recall_16"] for r in rows]),
        "benign_far": q([r["benign"] for r in rows]),
        "resist_far": q([r["resist"] for r in rows]),
    }


if __name__ == "__main__":
    main()
