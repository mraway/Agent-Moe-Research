"""REFUTATION lens: independent recomputation of the CAND-A geometry numbers in
docs/research_v2/zoom/code_blindspot/token_identity.md section 6, plus the
counterfactuals the claim needs (raw / unwhitened space, sd-decile decomposition
of the z shift, per-trace ranking of all 59 drift traces, null distribution of
the "effective routine sd along a direction").

Written from the definitions only (features.selection_rate_windows + wgm.WGMScorer
whitening rule sd = std(0) + variance_floor); nothing imported from the colleague's
scripts.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import torch

from research_v2 import io as rio
from research_v2.features import selection_rate_windows

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
LABELS = REPO / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"

W = 8
LAYERS = tuple(range(5, 16))
FLOOR = 1e-3
SPAN = 48


def labels() -> dict[str, dict]:
    rows = {}
    for line in LABELS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["trace_id"]] = r
    return rows


def main() -> None:
    lab = labels()
    batches = rio.load_core()
    traces = [t for b in ("b1", "b2") for t in batches[b]]

    routine = sorted([t for t in traces if rio.arm_class(t) in ("clean", "benign")],
                     key=lambda t: t.trace_id)
    fit = routine[0::2]
    held = routine[1::2]
    drift = [t for t in traces if rio.arm_class(t) == "drift"]

    def wins(t):
        ends, w = selection_rate_windows(t.top_k_ids, W, LAYERS)
        return ends.numpy(), w.numpy().astype(np.float64)

    fit_w = np.concatenate([wins(t)[1] for t in fit])
    held_w = np.concatenate([wins(t)[1] for t in held])
    all_routine_w = np.concatenate([wins(t)[1] for t in routine])

    mu = fit_w.mean(0)
    sd = fit_w.std(0, ddof=1) + FLOOR          # wgm rule
    D = mu.shape[0]

    def g1(x):
        z = (x - mu) / sd
        return (z ** 2).sum(1)

    fit_g1, held_g1 = g1(fit_w), g1(held_w)

    # region windows: fully inside [onset, onset+48)
    def region(t, anchor="product"):
        row = lab[t.trace_id]
        o = int(row["product_onset"] if anchor == "product" else row["evidence_onset"])
        T = int(t.token_ids.shape[0])
        hi = min(o + SPAN, T)
        ends, w = wins(t)
        keep = (ends >= o + W - 1) & (ends <= hi - 1)
        return w[keep]

    def shift_stats(x):
        d = x.mean(0) - mu
        z = d / sd
        return {
            "n_windows": int(x.shape[0]),
            "raw_shift": float(np.linalg.norm(d)),
            "z_shift": float(np.linalg.norm(z)),
            "eff_sd": float(np.linalg.norm(d) / np.linalg.norm(z)),
            "g1_median": float(np.median(g1(x))),
        }, d, z

    # ---- sd deciles of the 704 dims ------------------------------------------------
    order = np.argsort(sd)
    dec = np.empty(D, dtype=int)
    for i in range(10):
        dec[order[i * D // 10:(i + 1) * D // 10]] = i     # 0 = lowest sd, 9 = highest

    def decile_share(v):
        sq = v ** 2
        tot = sq.sum()
        return [float(sq[dec == i].sum() / tot) for i in range(10)]

    dom_of = {t.trace_id: (lab[t.trace_id]["domain"]) for t in drift}
    groups = {}
    for t in drift:
        groups.setdefault(dom_of[t.trace_id], []).append(t)

    res = {
        "dims": D,
        "sd_quantiles": {q: float(np.quantile(sd, float(q))) for q in ("0.1", "0.25", "0.5", "0.75", "0.9", "0.99")},
        "sd_min": float(sd.min()), "sd_max": float(sd.max()),
        "routine_fit_g1": {"median": float(np.median(fit_g1)), "q90": float(np.quantile(fit_g1, .9)),
                            "q99": float(np.quantile(fit_g1, .99)), "n": int(fit_g1.size)},
        "routine_held_g1": {"median": float(np.median(held_g1)), "n": int(held_g1.size)},
        "regions": {}, "per_trace": {}, "held_shift": {},
    }
    s, dh, zh = shift_stats(held_w)
    s["raw_decile_share"] = decile_share(dh)
    s["z_decile_share"] = decile_share(zh)
    res["held_shift"] = s

    shifts = {}
    for name, ts in [("CODE", groups["programming"])] + sorted(
            [(k, v) for k, v in groups.items() if k != "programming"]):
        x = np.concatenate([region(t) for t in ts])
        s, d, z = shift_stats(x)
        s["raw_decile_share"] = decile_share(d)
        s["z_decile_share"] = decile_share(z)
        s["top_decile_raw_share"] = s["raw_decile_share"][9]
        s["bottom_decile_z_share"] = s["z_decile_share"][0]
        s["bottom2_decile_z_share"] = sum(s["z_decile_share"][:2])
        # z shift restricted to dims OUTSIDE the top variance decile
        s["z_shift_excl_topdecile"] = float(np.linalg.norm(z[dec != 9]))
        s["z_shift_topdecile_only"] = float(np.linalg.norm(z[dec == 9]))
        s["n_traces"] = len(ts)
        res["regions"][name] = s
        shifts[name] = d

    # ---- per-trace, all 59 drift traces -------------------------------------------
    for t in drift:
        x = region(t)
        s, d, z = shift_stats(x)
        s["domain"] = dom_of[t.trace_id]
        s["batch"] = t.batch
        s["onset"] = int(lab[t.trace_id]["product_onset"])
        s["product_class"] = lab[t.trace_id]["product_class"]
        s["T"] = int(t.token_ids.shape[0])
        s["top_decile_raw_share"] = decile_share(d)[9]
        s["bottom2_decile_z_share"] = sum(decile_share(z)[:2])
        s["z_shift_excl_topdecile"] = float(np.linalg.norm(z[dec != 9]))
        # evidence-anchor variant
        xe = region(t, "evidence")
        se, de, ze = shift_stats(xe)
        s["eff_sd_evidence"] = se["eff_sd"]
        s["z_shift_evidence"] = se["z_shift"]
        res["per_trace"][t.trace_id] = s

    # ---- null: effective sd of random directions and of routine's own fluctuation ---
    rng = np.random.default_rng(0)
    randdir = rng.normal(size=(2000, D))
    randdir /= np.linalg.norm(randdir, axis=1, keepdims=True)
    eff_rand = np.linalg.norm(randdir, axis=1) / np.linalg.norm(randdir / sd, axis=1)
    # direction distribution matched to a *shift-like* sparse direction is covered by
    # the per-window fluctuation null below.
    fl = held_w - mu
    eff_win = np.linalg.norm(fl, axis=1) / np.linalg.norm(fl / sd, axis=1)
    res["null_eff_sd"] = {
        "random_unit_direction": {"median": float(np.median(eff_rand)), "q10": float(np.quantile(eff_rand, .1)),
                                   "q90": float(np.quantile(eff_rand, .9))},
        "routine_held_single_window_deviation": {"median": float(np.median(eff_win)),
                                                  "q10": float(np.quantile(eff_win, .1)),
                                                  "q90": float(np.quantile(eff_win, .9))},
        "sd_median": float(np.median(sd)),
    }

    # ---- counterfactual: RAW (unwhitened) space -------------------------------------
    raw_routine = np.linalg.norm(all_routine_w - mu, axis=1)
    raw_fit = np.linalg.norm(fit_w - mu, axis=1)
    cf = {"routine_all_raw": {"median": float(np.median(raw_routine)),
                              "q90": float(np.quantile(raw_routine, .9)),
                              "q99": float(np.quantile(raw_routine, .99))},
          "routine_fit_raw": {"median": float(np.median(raw_fit)), "q90": float(np.quantile(raw_fit, .9))},
          "per_region": {}, "per_trace": {}}
    for name, ts in [("CODE", groups["programming"])] + sorted(
            [(k, v) for k, v in groups.items() if k != "programming"]):
        x = np.concatenate([region(t) for t in ts])
        r = np.linalg.norm(x - mu, axis=1)
        cf["per_region"][name] = {"raw_median": float(np.median(r)),
                                  "pct_of_routine": float((raw_fit < np.median(r)).mean()),
                                  "z_median": float(np.median(g1(x)))}
    for t in drift:
        x = region(t)
        r = np.linalg.norm(x - mu, axis=1)
        cf["per_trace"][t.trace_id] = {"raw_median": float(np.median(r)),
                                       "raw_pctile_in_routine_fit": float((raw_fit < np.median(r)).mean()),
                                       "g1_pctile_in_routine_fit": float((fit_g1 < np.median(g1(x))).mean()),
                                       "domain": dom_of[t.trace_id]}
    res["raw_counterfactual"] = cf

    # ---- cosines between domain shifts, and with routine's own symbol-window axis ----
    cos = {}
    keys = list(shifts)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            cos[f"{a}|{b}"] = float(shifts[a] @ shifts[b] /
                                    (np.linalg.norm(shifts[a]) * np.linalg.norm(shifts[b])))
    res["shift_cosines"] = cos

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "refute_tokenid_geometry.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("dims", "sd_quantiles", "routine_fit_g1", "routine_held_g1",
                                          "held_shift", "null_eff_sd")}, indent=1)[:2000])
    for k, v in res["regions"].items():
        print(k, {kk: round(v[kk], 4) for kk in ("raw_shift", "z_shift", "eff_sd", "g1_median",
                                                  "top_decile_raw_share", "bottom2_decile_z_share",
                                                  "z_shift_excl_topdecile", "z_shift_topdecile_only")})


if __name__ == "__main__":
    main()
