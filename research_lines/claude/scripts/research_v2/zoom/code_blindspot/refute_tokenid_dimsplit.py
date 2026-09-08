"""REFUTATION follow-up: the claim says code's shift lies on routine's HIGHEST-variance
(JSON symbol) directions and that per-dimension whitening therefore spends the code
signal as noise budget.  Direct counterfactual: split the 704 (layer, expert) dims by
routine sd and recompute the CAND-A statistic on each subset.

If the blindness were caused by the high-variance JSON axis, deleting that axis should
help code more than it helps the other domains.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np

from research_v2 import io as rio
from research_v2.features import selection_rate_windows

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
LABELS = REPO / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"
W, LAYERS, FLOOR, SPAN = 8, tuple(range(5, 16)), 1e-3, 48


def main() -> None:
    lab = {json.loads(l)["trace_id"]: json.loads(l)
           for l in LABELS.read_text(encoding="utf-8").splitlines() if l.strip()}
    batches = rio.load_core()
    traces = [t for b in ("b1", "b2") for t in batches[b]]
    routine = sorted([t for t in traces if rio.arm_class(t) in ("clean", "benign")], key=lambda t: t.trace_id)
    fit, held = routine[0::2], routine[1::2]
    drift = [t for t in traces if rio.arm_class(t) == "drift"]

    def wins(t):
        e, w = selection_rate_windows(t.top_k_ids, W, LAYERS)
        return e.numpy(), w.numpy().astype(np.float64)

    fit_w = np.concatenate([wins(t)[1] for t in fit])
    mu = fit_w.mean(0); sd = fit_w.std(0, ddof=1) + FLOOR
    D = mu.shape[0]
    order = np.argsort(sd)
    dec = np.empty(D, int)
    for i in range(10):
        dec[order[i * D // 10:(i + 1) * D // 10]] = i

    def region(t):
        o = int(lab[t.trace_id]["product_onset"]); T = int(t.token_ids.shape[0])
        hi = min(o + SPAN, T); e, w = wins(t)
        return w[(e >= o + W - 1) & (e <= hi - 1)]

    subsets = {
        "all_704": np.ones(D, bool),
        "top_variance_decile_only_70": dec == 9,
        "drop_top_variance_decile_634": dec != 9,
        "drop_top_2_variance_deciles_563": dec < 8,
        "bottom_2_variance_deciles_only_140": dec < 2,
    }
    doms = {}
    for t in drift:
        doms.setdefault(lab[t.trace_id]["domain"], []).append(t)
    reg = {k: np.concatenate([region(t) for t in v]) for k, v in doms.items()}

    res = {"subsets": {}, "per_code_trace": {}}
    for name, m in subsets.items():
        zf = ((fit_w[:, m] - mu[m]) / sd[m] ** 1) ** 2
        g_fit = zf.sum(1)
        entry = {"n_dims": int(m.sum()), "routine_fit_median": float(np.median(g_fit)),
                 "routine_fit_q90": float(np.quantile(g_fit, .9)), "domains": {}}
        for d, x in sorted(reg.items()):
            g = (((x[:, m] - mu[m]) / sd[m]) ** 2).sum(1)
            entry["domains"][d] = {
                "g_median": float(np.median(g)),
                "ratio_to_routine_median": float(np.median(g) / np.median(g_fit)),
                "pctile_in_routine_fit": float((g_fit < np.median(g)).mean()),
                "frac_windows_above_routine_q90": float((g > np.quantile(g_fit, .9)).mean()),
            }
        res["subsets"][name] = entry
        for t in doms["programming"]:
            g = (((region(t)[:, m] - mu[m]) / sd[m]) ** 2).sum(1)
            res["per_code_trace"].setdefault(t.trace_id, {})[name] = {
                "g_median": float(np.median(g)),
                "pctile_in_routine_fit": float((g_fit < np.median(g)).mean())}

    # which dims actually carry each domain's whitened signal?
    dimtab = {}
    for d, x in sorted(reg.items()):
        dz = (x.mean(0) - mu) / sd
        idx = np.argsort(-np.abs(dz))[:15]
        dimtab[d] = [{"layer": int(LAYERS[i // 64]), "expert": int(i % 64), "z": float(dz[i]),
                      "raw_d": float(x.mean(0)[i] - mu[i]), "routine_sd": float(sd[i]),
                      "routine_mean": float(mu[i]), "sd_decile": int(dec[i])} for i in idx]
        z2 = dz ** 2
        dimtab[d + "__top15_z_share"] = float(z2[idx].sum() / z2.sum())
    res["top_z_dims"] = dimtab

    # what are the low-variance dims?  (rarely used experts)
    res["dim_profile_by_decile"] = [
        {"decile": i, "n": int((dec == i).sum()), "sd_median": float(np.median(sd[dec == i])),
         "routine_mean_median": float(np.median(mu[dec == i]))} for i in range(10)]

    (OUT / "refute_tokenid_dimsplit.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    for name, e in res["subsets"].items():
        print(f"\n== {name} (D={e['n_dims']}) routine median {e['routine_fit_median']:.1f} q90 {e['routine_fit_q90']:.1f}")
        for d, v in sorted(e["domains"].items(), key=lambda kv: -kv[1]["ratio_to_routine_median"]):
            print(f"   {d:20s} ratio={v['ratio_to_routine_median']:7.3f} pctile={v['pctile_in_routine_fit']:.3f} above_q90={v['frac_windows_above_routine_q90']:.3f}")
    print("\ndecile profile:", res["dim_profile_by_decile"])
    print("\ntop-15 |z| dims, CODE:")
    for r_ in res["top_z_dims"]["programming"]:
        print("   ", r_)
    print("top15 z share code", res["top_z_dims"]["programming__top15_z_share"],
          "gk", res["top_z_dims"]["general_knowledge__top15_z_share"])
    print("\ntop-15 |z| dims, general_knowledge:")
    for r_ in res["top_z_dims"]["general_knowledge"][:8]:
        print("   ", r_)


if __name__ == "__main__":
    main()
