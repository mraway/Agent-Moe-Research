"""LENS part 3: per-trace table for the 8 programming drifts with a matched
other-domain drift, plus variance-floor sensitivity and routine-variance tail stats.

Matching rule (fixed before looking at the numbers): for each programming drift,
the matched control is the non-programming drift in the SAME batch maximising
4*[same workflow] + 2*[same channel], ties broken by |product_onset difference|
then trace_id.  Post-hoc diagnosis only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from whitening_common import (  # noqa: E402
    CACHE, D, LAYERS, PROG_IDS, VAR_FLOOR, WINDOW_SPAN,
    base_scorer, label_by_short, load_all, short_id, windows_of,
)
from research_v2 import io as rio  # noqa: E402


def auc(pos, neg):
    n1, n2 = pos.numel(), neg.numel()
    negs, _ = torch.sort(neg.double())
    lo = torch.searchsorted(negs, pos.double(), right=False).double()
    hi = torch.searchsorted(negs, pos.double(), right=True).double()
    return float(((lo + hi) / 2).sum() / (n1 * n2))


def main() -> None:
    batches = load_all()
    lab = label_by_short()
    routine = {"b1": [], "b2": []}
    drift = []
    for bkey, traces in batches.items():
        for tr in traces:
            cls = rio.arm_class(tr)
            ends, win = windows_of(tr)
            if not ends.numel():
                continue
            if cls in ("clean", "benign"):
                routine[bkey].append(win)
            elif cls == "drift":
                sid = short_id(tr.trace_id)
                row = lab[sid]
                T = int(tr.token_count)
                sel = {}
                for an, on in (("product", row["product_onset"]), ("evidence", row["evidence_onset"])):
                    sel[an] = (ends >= int(on)) & (ends < min(int(on) + WINDOW_SPAN, T))
                drift.append({"short": sid, "batch": bkey, "domain": row["domain"],
                              "workflow": tr.workflow, "channel": tr.channel,
                              "product_onset": int(row["product_onset"]),
                              "evidence_onset": int(row["evidence_onset"]),
                              "T": T, "product_class": row["product_class"],
                              "win": win, "sel": sel, "is_code": sid in PROG_IDS})

    # matched controls
    matches = {}
    for c in (d for d in drift if d["is_code"]):
        cands = [d for d in drift if not d["is_code"] and d["batch"] == c["batch"]]
        best = max(cands, key=lambda d: (
            4 * (d["workflow"] == c["workflow"]) + 2 * (d["channel"] == c["channel"]),
            -abs(d["product_onset"] - c["product_onset"]),
            [-ord(ch) for ch in d["short"]],
        ))
        matches[c["short"]] = best["short"]

    R_all = torch.cat(routine["b1"] + routine["b2"])
    by_short = {d["short"]: d for d in drift}

    def make_fit(mats, floor):
        M = torch.cat(mats)
        mu, sd = M.mean(0), M.std(0) + floor
        return {"mu": mu, "sd": sd, "centre": ((M - mu) / sd).mean(0), "var": M.var(0)}

    def wd(win, f):
        c = (win - f["mu"]) / f["sd"] - f["centre"]
        return (c ** 2).sum(1)

    def rd(win, f):
        c = win - (f["mu"] + f["sd"] * f["centre"])
        return (c ** 2).sum(1)

    out = {"matches": matches, "per_trace": {}, "floor_sensitivity": {},
           "variance_tail": {}, "rank_note": {}}

    for fb in ("b1", "b2"):
        f = make_fit(routine[fb], VAR_FLOOR)
        comp = base_scorer("g2").fit(
            [t for t in batches[fb] if rio.arm_class(t) in ("clean", "benign")]).components
        r_raw_med = float(rd(R_all, f).median())
        r_wht = wd(R_all, f)
        r_wht_med = float(r_wht.median())
        rows = []
        for c in (d for d in drift if d["is_code"]):
            def stats(d, anchor="product"):
                w = d["win"][d["sel"][anchor]]
                raw, wht = rd(w, f), wd(w, f)
                cen = (w - f["mu"]) / f["sd"] - f["centre"]
                sub = ((cen @ comp) ** 2).sum(1)
                return {
                    "n_windows": int(w.shape[0]),
                    "raw_median": float(raw.median()),
                    "raw_ratio": float(raw.median()) / r_raw_med,
                    "whitened_median": float(wht.median()),
                    "whitened_ratio": float(wht.median()) / r_wht_med,
                    "gain": (float(wht.median()) / r_wht_med) / (float(raw.median()) / r_raw_med),
                    "sub_frac_median": float((sub / wht.clamp_min(1e-12)).median()),
                    "auc_vs_routine": auc(wht, r_wht),
                }
            m = by_short[matches[c["short"]]]
            rows.append({
                "trace": c["short"], "batch": c["batch"], "cross_batch_fit": fb != c["batch"],
                "product_onset": c["product_onset"], "evidence_onset": c["evidence_onset"],
                "T": c["T"], "product_class": c["product_class"],
                "workflow": c["workflow"], "channel": c["channel"],
                "product": stats(c, "product"), "evidence": stats(c, "evidence"),
                "match": {"trace": m["short"], "domain": m["domain"], "workflow": m["workflow"],
                          "channel": m["channel"], "product_onset": m["product_onset"],
                          **{k: v for k, v in stats(m, "product").items()}},
            })
        out["per_trace"][fb] = {"routine_raw_median": r_raw_med,
                                "routine_whitened_median": r_wht_med, "rows": rows}

        # variance-floor sensitivity (pooled)
        code = torch.cat([d["win"][d["sel"]["product"]] for d in drift if d["is_code"]])
        other = torch.cat([d["win"][d["sel"]["product"]] for d in drift if not d["is_code"]])
        fl = {}
        for floor in (0.0, 1e-4, 1e-3, 1e-2, 3e-2):
            ff = make_fit(routine[fb], floor)
            rr = wd(R_all, ff)
            rm = float(rr.median())
            fl[str(floor)] = {
                "routine_median": rm,
                "code_ratio": float(wd(code, ff).median()) / rm,
                "other_ratio": float(wd(other, ff).median()) / rm,
                "code_auc": auc(wd(code, ff), rr),
                "other_auc": auc(wd(other, ff), rr),
                "min_sd": float(ff["sd"].min()),
            }
        out["floor_sensitivity"][fb] = fl

        var = f["var"]
        out["variance_tail"][fb] = {
            "n_coords": D,
            "var_min": float(var.min()), "var_q01": float(torch.quantile(var.double(), 0.01)),
            "var_q10": float(torch.quantile(var.double(), 0.10)),
            "var_median": float(var.median()), "var_max": float(var.max()),
            "n_var_below_1e-4": int((var < 1e-4).sum()),
            "n_var_below_1e-3": int((var < 1e-3).sum()),
            "n_sd_within_10pct_of_floor": int((var.sqrt() < 10 * VAR_FLOOR).sum()),
            "floor_share_of_sd_median": float((VAR_FLOOR / f["sd"]).median()),
            "floor_share_of_sd_max": float((VAR_FLOOR / f["sd"]).max()),
        }
    (CACHE / "whitening_pertrace.json").write_text(json.dumps(out, indent=2))
    print("wrote", CACHE / "whitening_pertrace.json")


if __name__ == "__main__":
    main()
