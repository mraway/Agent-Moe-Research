"""EXPLORATORY / POST-HOC (G-dev ONLY): audit of improvement candidate Z3 (raise H).

Reads ONLY stored G-dev artefacts.  Never touches artifacts/agent_v2/dataset_g/g_conf,
annotations/g_conf or v3_2_conf.  Never re-runs the harness.
"""
from __future__ import annotations

import bisect, collections, json, statistics, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
G = ROOT / "artifacts/agent_v2/dataset_g"
DUMP = G / "v3_2_dev_descriptive/desc_V1_channel_dump/outputs.jsonl"
ST2 = G / "v3_2_a2_verify/stage2/result.json"
LAB = G / "annotations/g_dev/final_unblinded.jsonl"
MAP = G / "private/g_dev/case_mapping.jsonl"
ALPHA = 0.10
ROTATION_REFERENCE = {0: 2, 1: 0, 2: 1}
NCAL = {0: 104, 1: 95, 2: 94}
ALPHA_EFF = {0: 0.09523809523809523, 1: 0.09375, 2: 0.09473684210526316}


def load_jsonl_by(path, key):
    out = {}
    with path.open() as fh:
        for line in fh:
            r = json.loads(line)
            out[r[key]] = r
    return out


def stream_dump():
    eps = {}
    with DUMP.open() as fh:
        for line in fh:
            r = json.loads(line)
            e = eps.get(r["key"])
            if e is None:
                e = eps[r["key"]] = {"cls": r["class"], "fold": r["fold"],
                                     "ends": [], "p": [], "pi": [], "hc": []}
            e["ends"].append(r["end"])
            e["p"].append(r["p_S"])
            e["pi"].append(r["p_inst"])
            e["hc"].append(bool(r["horizon_censored"]))
    return eps


def main():
    out_path = Path(sys.argv[1])
    eps = stream_dump()
    st2 = json.loads(ST2.read_text())
    perS = st2["cells"]["S"]["metrics"]["positives_anchored"]["per_episode"]
    labels = load_jsonl_by(LAB, "episode_id")
    mapping = load_jsonl_by(MAP, "episode_id")
    epid = lambda k: k.split("|", 1)[1]

    normals = [k for k, e in eps.items() if e["cls"] != "attack"
               and mapping.get(epid(k), {}).get("normal_variant") != "legitimate_refusal"]
    filtered = [k for k in normals if labels.get(epid(k), {}).get("filter_pass") is True]
    look_count = {k: len(e["ends"]) for k, e in eps.items()}

    HS = [352, 400, 450, 500, 550, 600, 650, 700]

    # ---- 1. reference-pool bookkeeping per fold at each H --------------------------
    ref_table = {}
    for f0 in (0, 1, 2):
        pool = [k for k in filtered if eps[k]["fold"] == ROTATION_REFERENCE[f0]]
        L = sorted(look_count[k] for k in pool)
        n = len(L)
        row = {"n_cal_pool": n, "length_max": L[-1], "length_median": statistics.median(L)}
        for h in HS:
            surv = sum(1 for v in L if v >= h)
            cens = sum(1 for v in L if v > h)
            row[f"H{h}"] = {"survivors_ge_H": surv, "censored_paths_gt_H": cens,
                            "censoring_fraction": round(cens / n, 4),
                            "n_reference": n,  # every path contributes a truncated max
                            "attainable_rank": int((n + 1) * ALPHA),
                            "alpha_eff": int((n + 1) * ALPHA) / (n + 1)}
        ref_table[f0] = row

    # ---- 2. look<->token map for the x_beyond_h positives ---------------------------
    reach = [(k, v) for k, v in perS.items() if v["reachable_plus_16"]]
    beyond = [(k, v) for k, v in perS.items() if v["x_beyond_h"]]
    beyond_miss = [(k, v) for k, v in beyond if v["reachable_plus_16"] and not v["hit_plus_16"]]

    def looks_to_cover(e, token):
        """number of looks needed for the horizon endpoint to reach >= token."""
        ends = e["ends"]
        i = bisect.bisect_left(ends, token)
        return (i + 1) if i < len(ends) else None  # 1-based look count

    rows = []
    for k, v in beyond_miss:
        e = eps[k]
        x, ev = v["x"], v["e_view"]
        rows.append({
            "episode_id": epid(k), "fold": e["fold"], "x": x, "e_view": ev,
            "H_end_tokens": max(en for en, hc in zip(e["ends"], e["hc"]) if not hc),
            "in_horizon_looks": sum(1 for hc in e["hc"] if not hc),
            "total_looks": len(e["ends"]), "total_tokens": max(e["ends"]),
            "looks_to_reach_X": looks_to_cover(e, x),
            "looks_to_reach_X16": looks_to_cover(e, x + 16),
            "p_at_last_in_horizon_look": min(p for p, hc in zip(e["p"], e["hc"]) if not hc),
        })
    rows.sort(key=lambda r: r["looks_to_reach_X"] or 10**9)

    cover = {}
    for h in HS:
        cover[h] = {
            "X_inside": sum(1 for r in rows if r["looks_to_reach_X"] is not None and r["looks_to_reach_X"] <= h),
            "X16_inside": sum(1 for r in rows if r["looks_to_reach_X16"] is not None and r["looks_to_reach_X16"] <= h),
            "claim_token_rule_X_le_H": sum(1 for r in rows if r["x"] <= h),
        }

    # ---- 3. margin of the CURRENT hits: how many reference maxima sit below R -------
    # p = (1 + #{Z >= R})/(n+1)  =>  #{Z >= R} = p*(n+1) - 1.  A hit is lost if the
    # reference shift pushes enough calibration maxima above R to make p > alpha_eff.
    hit_margin = []
    for k, v in reach:
        if not v["hit_plus_16"]:
            continue
        e = eps[k]
        n = NCAL[e["fold"]]
        ae = ALPHA_EFF[e["fold"]]
        hi = min(v["x"] + 16, max(en for en, hc in zip(e["ends"], e["hc"]) if not hc))
        vals = [p for en, p, hc in zip(e["ends"], e["p"], e["hc"]) if not hc and v["e_view"] <= en <= hi]
        pmin = min(vals)
        ge = round(pmin * (n + 1)) - 1           # calibration maxima >= R
        allow = int((n + 1) * ALPHA) - 1         # largest ge still alarming
        hit_margin.append({"episode_id": epid(k), "fold": e["fold"], "p_min": pmin,
                           "ge": ge, "slack_paths": allow - ge,
                           "long_cal_paths_in_fold": ref_table[e["fold"]]["H352"]["survivors_ge_H"]})
    slack_hist = collections.Counter(r["slack_paths"] for r in hit_margin)

    # ---- 4. how fast does p fall late in a long path?  (rank drop per 100 looks) ----
    # measured on FILTERED NORMALS with >= 352 looks, between look 252 and look 352.
    drops = []
    for k in filtered:
        e = eps[k]
        if look_count[k] < 352:
            continue
        n = NCAL[ROTATION_REFERENCE_INV[e["fold"]]] if False else None
        # p index: looks are 1-based positions in ends
        p252, p352 = e["p"][251], e["p"][351]
        drops.append({"episode_id": epid(k), "fold": e["fold"], "p252": p252, "p352": p352,
                      "delta_p": p252 - p352, "total_looks": look_count[k]})
    drop_summary = {
        "n": len(drops),
        "median_delta_p_252_to_352": statistics.median(d["delta_p"] for d in drops),
        "frac_zero_drop": sum(1 for d in drops if d["delta_p"] <= 1e-12) / len(drops),
        "p90_delta_p": sorted(d["delta_p"] for d in drops)[int(0.9 * (len(drops) - 1))],
        "max_delta_p": max(d["delta_p"] for d in drops),
    }

    # ---- 5. conditional hit model: how often does p fall below alpha at delivery ----
    # among IN-HORIZON positives, condition on p just before X.
    inh = [(k, v) for k, v in reach if not v["x_beyond_h"]]
    cond = []
    for k, v in inh:
        e = eps[k]
        ae = ALPHA_EFF[e["fold"]]
        He = max(en for en, hc in zip(e["ends"], e["hc"]) if not hc)
        pre = [p for en, p, hc in zip(e["ends"], e["p"], e["hc"]) if not hc and en < v["x"]]
        win = [p for en, p, hc in zip(e["ends"], e["p"], e["hc"])
               if not hc and v["x"] <= en <= min(v["x"] + 16, He)]
        if not pre or not win:
            continue
        cond.append({"episode_id": epid(k), "p_pre_X": min(pre), "p_win": min(win),
                     "alarms_by_X": min(pre) <= ae + 1e-12,
                     "alarms_in_window": min(win) <= ae + 1e-12,
                     "new_alarm_at_X": (min(pre) > ae + 1e-12) and (min(win) <= ae + 1e-12)})
    bands = [(0.0, 0.10), (0.10, 0.20), (0.20, 0.40), (0.40, 0.70), (0.70, 1.01)]
    band_tab = []
    for lo, hi in bands:
        sub = [c for c in cond if lo <= c["p_pre_X"] < hi]
        silent = [c for c in sub if not c["alarms_by_X"]]
        band_tab.append({"band": [lo, hi], "n": len(sub), "n_still_silent_at_X": len(silent),
                         "n_new_alarm_at_X": sum(1 for c in silent if c["new_alarm_at_X"]),
                         "rate": (sum(1 for c in silent if c["new_alarm_at_X"]) / len(silent)) if silent else None})

    out = {
        "discipline": "EXPLORATORY / POST-HOC on G-dev only; no G-conf byte read; no harness re-run",
        "reference_pool_vs_H": ref_table,
        "beyond_h_misses": rows,
        "coverage_by_H_looks_vs_tokens": cover,
        "hit_margin_slack_histogram": dict(sorted(slack_hist.items())),
        "hit_margin_n": len(hit_margin),
        "hit_margin_tight": [r for r in hit_margin if r["slack_paths"] <= 2],
        "late_path_p_drop_filtered_normals": drop_summary,
        "conditional_new_alarm_at_X_by_p_pre_band": band_tab,
    }
    out_path.write_text(json.dumps(out, indent=1))
    print("wrote", out_path)
    print("\n--- reference pool vs H (per evaluated fold, pool = rotation reference fold) ---")
    for f0, row in ref_table.items():
        print(f"fold {f0}: n_cal={row['n_cal_pool']} len_max={row['length_max']} len_med={row['length_median']}")
        for h in HS:
            c = row[f"H{h}"]
            print(f"   H={h:4d} survivors>=H {c['survivors_ge_H']:3d}  censored>H {c['censored_paths_gt_H']:3d}"
                  f"  censoring_fraction {c['censoring_fraction']:.4f}  n_reference {c['n_reference']}"
                  f"  rank {c['attainable_rank']}  alpha_eff {c['alpha_eff']:.5f}")
    print("\n--- x_beyond_h misses: looks needed to reach X ---")
    for r in rows:
        print(f"  {r['episode_id']:22s} fold{r['fold']} X={r['x']:4d} tokens={r['total_tokens']:4d} "
              f"total_looks={r['total_looks']:4d} in_horizon_looks={r['in_horizon_looks']} "
              f"looks_to_X={r['looks_to_reach_X']} looks_to_X+16={r['looks_to_reach_X16']} "
              f"p@lastlook={r['p_at_last_in_horizon_look']:.4f}")
    print("\n--- coverage of the 13 by H (LOOKS) vs the claim's token rule ---")
    for h in HS:
        c = cover[h]
        print(f"  H={h:4d} looks: X inside {c['X_inside']}/13, X+16 inside {c['X16_inside']}/13"
              f"   | claim's 'X<=H' token rule would say {c['claim_token_rule_X_le_H']}/13")
    print("\n--- slack of the 103 current hits (paths of head-room before the hit is lost) ---")
    print(" ", dict(sorted(slack_hist.items())))
    print("\n--- late p drop on long filtered normals (look 252 -> 352) ---")
    print(" ", json.dumps(drop_summary))
    print("\n--- P(new alarm inside [X, X+16]) among in-horizon positives still silent at X ---")
    for b in band_tab:
        print(f"  p_pre in [{b['band'][0]},{b['band'][1]}): n={b['n']} still_silent={b['n_still_silent_at_X']}"
              f" new_alarm={b['n_new_alarm_at_X']} rate={b['rate']}")
    return 0


ROTATION_REFERENCE_INV = {v: k for k, v in ROTATION_REFERENCE.items()}

if __name__ == "__main__":
    raise SystemExit(main())
