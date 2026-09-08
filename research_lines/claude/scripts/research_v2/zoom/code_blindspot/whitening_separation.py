"""LENS part 2: threshold-free separation + variance-decile energy profile.

For every whitening fit (batch x routine subset) reports, per domain and per
code trace, the median whitened distance, the routine percentile of that median,
the pooled AUC P(drift window > routine window) and the tail fractions above the
routine q90/q99.  Also profiles where the deviation energy lives as a function of
the routine variance of each (layer, expert) coordinate.

Post-hoc diagnosis; nothing here is calibrated or presented as a detector.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from whitening_common import (  # noqa: E402
    CACHE, D, LAYERS, PROG_IDS, VAR_FLOOR, WIDTH, WINDOW_SPAN,
    label_by_short, load_all, prose_window_mask, short_id, windows_of,
)
from research_v2 import io as rio  # noqa: E402


def auc(pos: torch.Tensor, neg: torch.Tensor) -> float:
    """P(pos > neg) + 0.5 P(=), rank based."""
    x = torch.cat([pos, neg]).double()
    order = torch.argsort(x)
    ranks = torch.empty_like(x)
    ranks[order] = torch.arange(1, x.numel() + 1, dtype=torch.float64)
    # average ranks for ties
    xs = x[order]
    i = 0
    while i < xs.numel():
        j = i
        while j + 1 < xs.numel() and xs[j + 1] == xs[i]:
            j += 1
        if j > i:
            avg = (i + j + 2) / 2.0
            ranks[order[i:j + 1]] = avg
        i = j + 1
    n1, n2 = pos.numel(), neg.numel()
    return float((ranks[:n1].sum() - n1 * (n1 + 1) / 2.0) / (n1 * n2))


def main() -> None:
    batches = load_all()
    lab = label_by_short()
    routine, drift = {"b1": [], "b2": []}, []
    for bkey, traces in batches.items():
        for tr in traces:
            cls = rio.arm_class(tr)
            ends, win = windows_of(tr)
            if not ends.numel():
                continue
            if cls in ("clean", "benign"):
                routine[bkey].append({"win": win, "prose": prose_window_mask(tr, ends)})
            elif cls == "drift":
                sid = short_id(tr.trace_id)
                row = lab[sid]
                T = int(tr.token_count)
                sel = {}
                for an, on in (("product", row["product_onset"]), ("evidence", row["evidence_onset"])):
                    sel[an] = ((ends >= int(on)) & (ends < min(int(on) + WINDOW_SPAN, T))
                               if on is not None else torch.zeros(ends.numel(), dtype=torch.bool))
                drift.append({"short": sid, "batch": bkey, "domain": row["domain"],
                              "win": win, "sel": sel, "is_code": sid in PROG_IDS})

    R_all = torch.cat([r["win"] for r in routine["b1"]] + [r["win"] for r in routine["b2"]])

    def fit_stats(mats):
        M = torch.cat(mats)
        mu, sd = M.mean(0), M.std(0) + VAR_FLOOR
        centre = ((M - mu) / sd).mean(0)
        return {"mu": mu, "sd": sd, "centre": centre, "var": M.var(0), "n": int(M.shape[0])}

    def wdist(win, f):
        c = (win - f["mu"]) / f["sd"] - f["centre"]
        return (c ** 2).sum(1)

    def rawdist(win, f):
        c = win - (f["mu"] + f["sd"] * f["centre"])
        return (c ** 2).sum(1)

    out = {"separation": {}, "variance_profile": {}, "whitening_gain": {}}

    for fb in ("b1", "b2"):
        subsets = {
            "all": [r["win"] for r in routine[fb]],
            "prose": [r["win"][r["prose"]] for r in routine[fb] if r["prose"].any()],
            "structured": [r["win"][~r["prose"]] for r in routine[fb] if (~r["prose"]).any()],
        }
        for sub, mats in subsets.items():
            f = fit_stats(mats)
            rd = wdist(R_all, f)
            rmed = float(rd.median())
            rq90 = float(torch.quantile(rd.double(), 0.90))
            rq99 = float(torch.quantile(rd.double(), 0.99))
            rs, _ = torch.sort(rd)
            def pctl(v):  # routine percentile of value v
                return float(torch.searchsorted(rs, torch.tensor(v)).item()) / rs.numel()
            entry = {"n_fit_windows": f["n"], "routine_median": rmed,
                     "routine_q90": rq90, "routine_q99": rq99, "domains": {}, "code_traces": {}}
            pooled = defaultdict(list)
            for d in drift:
                m = d["sel"]["product"]
                if bool(m.any()):
                    pooled[d["domain"]].append(d["win"][m])
            for dom, ms in pooled.items():
                dd = wdist(torch.cat(ms), f)
                entry["domains"][dom] = {
                    "n": int(dd.numel()), "median": float(dd.median()),
                    "ratio_median": float(dd.median()) / rmed,
                    "median_routine_pctl": pctl(float(dd.median())),
                    "auc": auc(dd, rd),
                    "frac_above_routine_q90": float((dd > rq90).float().mean()),
                    "frac_above_routine_q99": float((dd > rq99).float().mean()),
                }
            for d in drift:
                if not d["is_code"]:
                    continue
                dd = wdist(d["win"][d["sel"]["product"]], f)
                de = wdist(d["win"][d["sel"]["evidence"]], f) if bool(d["sel"]["evidence"].any()) else None
                entry["code_traces"][d["short"]] = {
                    "batch": d["batch"], "n": int(dd.numel()),
                    "median": float(dd.median()), "max": float(dd.max()),
                    "ratio_median": float(dd.median()) / rmed,
                    "median_routine_pctl": pctl(float(dd.median())),
                    "auc": auc(dd, rd),
                    "frac_above_routine_q90": float((dd > rq90).float().mean()),
                    "evidence_median": None if de is None else float(de.median()),
                    "evidence_ratio_median": None if de is None else float(de.median()) / rmed,
                    "evidence_auc": None if de is None else auc(de, rd),
                }
            out["separation"][f"fit={fb}|subset={sub}"] = entry

        # ---- whitening gain and variance-decile profile (subset=all only) ----
        f = fit_stats(subsets["all"])
        r_raw, r_wht = rawdist(R_all, f), wdist(R_all, f)
        gains = {}
        pooled = defaultdict(list)
        for d in drift:
            m = d["sel"]["product"]
            if bool(m.any()):
                pooled[d["domain"]].append(d["win"][m])
        for dom, ms in pooled.items():
            M = torch.cat(ms)
            rr = float(rawdist(M, f).median()) / float(r_raw.median())
            wr = float(wdist(M, f).median()) / float(r_wht.median())
            gains[dom] = {"raw_ratio": rr, "whitened_ratio": wr, "gain": wr / rr,
                          "raw_median": float(rawdist(M, f).median()),
                          "whitened_median": float(wdist(M, f).median())}
        gains["_routine"] = {"raw_median": float(r_raw.median()),
                             "whitened_median": float(r_wht.median())}
        out["whitening_gain"][fb] = gains

        var = f["var"]
        order = torch.argsort(var)          # ascending
        deciles = [order[i * D // 10:(i + 1) * D // 10] for i in range(10)]
        centre_raw = f["mu"] + f["sd"] * f["centre"]
        prof = {}
        sets = {"code": torch.cat([d["win"][d["sel"]["product"]] for d in drift
                                   if d["is_code"] and bool(d["sel"]["product"].any())]),
                "other": torch.cat([d["win"][d["sel"]["product"]] for d in drift
                                    if not d["is_code"] and bool(d["sel"]["product"].any())]),
                "routine": R_all}
        for name, M in sets.items():
            raw = M - centre_raw
            z = raw / f["sd"]
            raw2, z2 = (raw ** 2).mean(0), (z ** 2).mean(0)
            prof[name] = {
                "raw_energy_by_var_decile": [float(raw2[d].sum()) for d in deciles],
                "z_energy_by_var_decile": [float(z2[d].sum()) for d in deciles],
                "raw_total": float(raw2.sum()), "z_total": float(z2.sum()),
            }
        prof["decile_var_median"] = [float(var[d].median()) for d in deciles]
        out["variance_profile"][fb] = prof

    (CACHE / "whitening_separation.json").write_text(json.dumps(out, indent=2))
    print("wrote", CACHE / "whitening_separation.json")


if __name__ == "__main__":
    main()
