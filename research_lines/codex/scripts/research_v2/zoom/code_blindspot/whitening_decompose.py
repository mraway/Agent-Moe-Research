"""LENS: what the whitening does to the code direction.

Post-hoc diagnosis on B1/B2 development data.  Refits CAND-A's whitening on
routine (clean + benign_control) per batch and decomposes every CODE window and
every OTHER-DRIFT window into

    raw squared deviation from the routine centre  (selection-rate units)
    whitened squared distance                      (= the CAND-A g1 statistic)
    retained-PCA-subspace energy / residual energy (rank from the frozen auto rule)

plus the top-|deviation| (layer, expert) coordinates and their routine variance,
and the prose-only / structured-only whitening counterfactuals.

Writes artifacts/agent_v2/research_v2/zoom_code_blindspot/whitening_lens.json
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict

import torch

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from whitening_common import (  # noqa: E402
    CACHE,
    D,
    LAYERS,
    PROG_IDS,
    VAR_FLOOR,
    WIDTH,
    WINDOW_SPAN,
    base_scorer,
    label_by_short,
    load_all,
    prose_window_mask,
    short_id,
    windows_of,
)
from research_v2 import io as rio  # noqa: E402


def med(x: torch.Tensor) -> float:
    return float(x.median()) if x.numel() else float("nan")


def q(x: torch.Tensor, p: float) -> float:
    return float(torch.quantile(x.double(), p)) if x.numel() else float("nan")


def main() -> None:
    batches = load_all()
    lab = label_by_short()

    # ------------------------------------------------------------------ windows
    routine: dict[str, list] = {"b1": [], "b2": []}
    drift: list[dict] = []
    for bkey, traces in batches.items():
        for tr in traces:
            cls = rio.arm_class(tr)
            ends, win = windows_of(tr)
            if not ends.numel():
                continue
            if cls in ("clean", "benign"):
                routine[bkey].append(
                    {"trace_id": tr.trace_id, "ends": ends, "win": win,
                     "prose": prose_window_mask(tr, ends)}
                )
            elif cls == "drift":
                sid = short_id(tr.trace_id)
                row = lab.get(sid)
                if row is None:
                    raise SystemExit(f"no label for {tr.trace_id}")
                T = int(tr.token_count)
                sel = {}
                for anchor_name, onset in (("product", row["product_onset"]),
                                           ("evidence", row["evidence_onset"])):
                    if onset is None:
                        sel[anchor_name] = torch.zeros(ends.numel(), dtype=torch.bool)
                        continue
                    hi = min(int(onset) + WINDOW_SPAN, T)
                    sel[anchor_name] = (ends >= int(onset)) & (ends < hi)
                drift.append({
                    "trace_id": tr.trace_id, "short": sid, "batch": bkey,
                    "domain": row["domain"], "product_onset": row["product_onset"],
                    "evidence_onset": row["evidence_onset"], "T": T,
                    "product_class": row["product_class"],
                    "ends": ends, "win": win, "sel": sel,
                    "is_code": sid in PROG_IDS,
                })

    assert sum(d["is_code"] for d in drift) == 8, sum(d["is_code"] for d in drift)
    print("routine traces", {k: len(v) for k, v in routine.items()},
          "drift traces", len(drift), flush=True)

    # --------------------------------------------------------------- whitenings
    def fit_stats(mats: list[torch.Tensor]) -> dict:
        M = torch.cat(mats)
        mu = M.mean(0)
        sd = M.std(0) + VAR_FLOOR
        Z = (M - mu) / sd
        centre = Z.mean(0)
        var = M.var(0)  # true routine variance per coordinate (no floor)
        return {"mu": mu, "sd": sd, "centre": centre, "var": var,
                "n_windows": int(M.shape[0])}

    fits: dict[tuple[str, str], dict] = {}
    for bkey in ("b1", "b2"):
        allmats = [r["win"] for r in routine[bkey]]
        prose = [r["win"][r["prose"]] for r in routine[bkey] if r["prose"].any()]
        struct = [r["win"][~r["prose"]] for r in routine[bkey] if (~r["prose"]).any()]
        fits[(bkey, "all")] = fit_stats(allmats)
        fits[(bkey, "prose")] = fit_stats(prose)
        fits[(bkey, "structured")] = fit_stats(struct)
        n_all = fits[(bkey, "all")]["n_windows"]
        n_pr = fits[(bkey, "prose")]["n_windows"]
        print(f"{bkey}: routine windows {n_all}, prose {n_pr} ({n_pr/n_all:.3f}), "
              f"structured {fits[(bkey,'structured')]['n_windows']}", flush=True)

    # PCA subspace from the frozen auto-rank rule, on the "all" fit only
    pca: dict[str, dict] = {}
    for bkey in ("b1", "b2"):
        sc = base_scorer("g2")
        rtraces = [tr for tr in batches[bkey] if rio.arm_class(tr) in ("clean", "benign")]
        st = sc.fit(rtraces)
        f = fits[(bkey, "all")]
        # sanity: harness fit must equal our recomputation
        assert torch.allclose(st.mu, f["mu"], atol=1e-5), "mu mismatch"
        assert torch.allclose(st.sd, f["sd"], atol=1e-5), "sd mismatch"
        assert torch.allclose(st.centre, f["centre"], atol=1e-4), "centre mismatch"
        pca[bkey] = {"rank": int(st.rank), "components": st.components,
                     "selection": st.rank_selection}
        print(f"{bkey}: auto rank = {st.rank} elbow_found={st.rank_selection.get('elbow_found')}",
              flush=True)

    # ------------------------------------------------------------- decomposition
    def decompose(win: torch.Tensor, f: dict, comp: torch.Tensor | None) -> dict:
        centred = (win - f["mu"]) / f["sd"] - f["centre"]          # whitened deviation
        rawdev = centred * f["sd"]                                  # original units
        wsq = (centred ** 2).sum(1)
        rsq = (rawdev ** 2).sum(1)
        out = {"whitened_sq": wsq, "raw_sq": rsq, "ratio": wsq / rsq.clamp_min(1e-12)}
        if comp is not None:
            proj = centred @ comp
            sub = (proj ** 2).sum(1)
            out["sub"] = sub
            out["res"] = (wsq - sub).clamp_min(0.0)
        return out

    report: dict = {
        "config": {"layers": list(LAYERS), "width": WIDTH, "variance_floor": VAR_FLOOR,
                   "centre": "global", "transform": "identity", "window_span": WINDOW_SPAN,
                   "D": D},
        "fits": {}, "routine": {}, "per_trace": {}, "by_domain": {},
        "coordinates": {}, "counterfactual": {},
    }
    for bkey in ("b1", "b2"):
        report["fits"][bkey] = {
            "rank": pca[bkey]["rank"],
            "rank_selection": {k: v for k, v in pca[bkey]["selection"].items()
                               if k != "reconstruction_error"},
            "reconstruction_error": pca[bkey]["selection"].get("reconstruction_error"),
            "routine_windows": {sub: fits[(bkey, sub)]["n_windows"]
                                for sub in ("all", "prose", "structured")},
        }

    # routine reference distributions under the "all" fit
    for fit_b in ("b1", "b2"):
        f = fits[(fit_b, "all")]
        comp = pca[fit_b]["components"]
        for tgt in ("b1", "b2"):
            M = torch.cat([r["win"] for r in routine[tgt]])
            dec = decompose(M, f, comp)
            report["routine"][f"fit={fit_b}|routine={tgt}"] = {
                "n": int(M.shape[0]),
                "whitened_sq_median": med(dec["whitened_sq"]),
                "whitened_sq_q90": q(dec["whitened_sq"], 0.9),
                "raw_sq_median": med(dec["raw_sq"]),
                "ratio_median": med(dec["ratio"]),
                "sub_frac_median": med(dec["sub"] / dec["whitened_sq"].clamp_min(1e-12)),
                "sub_median": med(dec["sub"]),
                "res_median": med(dec["res"]),
            }
            del M, dec

    # per-drift-trace decomposition, both anchors, both fits
    for anchor in ("product", "evidence"):
        for fit_b in ("b1", "b2"):
            f = fits[(fit_b, "all")]
            comp = pca[fit_b]["components"]
            rows = []
            for d in drift:
                m = d["sel"][anchor]
                if not bool(m.any()):
                    rows.append({"trace": d["short"], "domain": d["domain"],
                                 "batch": d["batch"], "n_windows": 0})
                    continue
                dec = decompose(d["win"][m], f, comp)
                rows.append({
                    "trace": d["short"], "domain": d["domain"], "batch": d["batch"],
                    "is_code": d["is_code"], "n_windows": int(m.sum()),
                    "onset": d["product_onset"] if anchor == "product" else d["evidence_onset"],
                    "T": d["T"], "product_class": d["product_class"],
                    "whitened_sq_median": med(dec["whitened_sq"]),
                    "whitened_sq_max": float(dec["whitened_sq"].max()),
                    "raw_sq_median": med(dec["raw_sq"]),
                    "ratio_median": med(dec["ratio"]),
                    "sub_median": med(dec["sub"]),
                    "res_median": med(dec["res"]),
                    "sub_frac_median": med(dec["sub"] / dec["whitened_sq"].clamp_min(1e-12)),
                })
            report["per_trace"][f"anchor={anchor}|fit={fit_b}"] = rows

            # domain aggregates over pooled windows
            pooled: dict[str, list[torch.Tensor]] = defaultdict(list)
            for d in drift:
                m = d["sel"][anchor]
                if bool(m.any()):
                    pooled[d["domain"]].append(d["win"][m])
            agg = {}
            for dom, mats in pooled.items():
                M = torch.cat(mats)
                dec = decompose(M, f, comp)
                agg[dom] = {
                    "n_windows": int(M.shape[0]), "n_traces": len(mats),
                    "whitened_sq_median": med(dec["whitened_sq"]),
                    "raw_sq_median": med(dec["raw_sq"]),
                    "ratio_median": med(dec["ratio"]),
                    "sub_frac_median": med(dec["sub"] / dec["whitened_sq"].clamp_min(1e-12)),
                }
            report["by_domain"][f"anchor={anchor}|fit={fit_b}"] = agg

    # ------------------------------------------------ top coordinates on code
    for fit_b in ("b1", "b2"):
        f = fits[(fit_b, "all")]
        code = torch.cat([d["win"][d["sel"]["product"]] for d in drift
                          if d["is_code"] and bool(d["sel"]["product"].any())])
        other = torch.cat([d["win"][d["sel"]["product"]] for d in drift
                           if not d["is_code"] and bool(d["sel"]["product"].any())])
        rout = torch.cat([r["win"] for r in routine["b1"]] + [r["win"] for r in routine["b2"]])
        centre_raw = f["mu"] + f["sd"] * f["centre"]

        def coord_table(M: torch.Tensor) -> dict:
            rawdev = M - centre_raw
            z = rawdev / f["sd"]
            return {"mean_abs_raw": rawdev.abs().mean(0), "mean_abs_z": z.abs().mean(0),
                    "mean_raw": rawdev.mean(0), "mean_z": z.mean(0),
                    "mean_z2": (z ** 2).mean(0), "mean_raw2": (rawdev ** 2).mean(0)}

        ct_code = coord_table(code)
        ct_other = coord_table(other)
        ct_rout = coord_table(rout)
        var = f["var"]
        med_var = float(var.median())
        entries = {}
        for key in ("mean_abs_raw", "mean_abs_z", "mean_raw2", "mean_z2"):
            idx = torch.argsort(ct_code[key], descending=True)[:10]
            entries[key] = [{
                "layer": int(LAYERS[int(i) // 64]), "expert": int(i) % 64,
                "code_mean_abs_raw": float(ct_code["mean_abs_raw"][i]),
                "code_mean_raw": float(ct_code["mean_raw"][i]),
                "code_mean_abs_z": float(ct_code["mean_abs_z"][i]),
                "code_mean_z": float(ct_code["mean_z"][i]),
                "code_mean_z2": float(ct_code["mean_z2"][i]),
                "other_mean_abs_z": float(ct_other["mean_abs_z"][i]),
                "other_mean_z2": float(ct_other["mean_z2"][i]),
                "routine_mean_abs_z": float(ct_rout["mean_abs_z"][i]),
                "routine_var": float(var[i]), "routine_sd_used": float(f["sd"][i]),
                "var_rank_pct": float((var < var[i]).float().mean()),
                "var_over_median": float(var[i] / med_var),
            } for i in idx.tolist()]
        # same for other-drift, top by mean_abs_raw
        idx_o = torch.argsort(ct_other["mean_abs_raw"], descending=True)[:10]
        entries["other_top_mean_abs_raw"] = [{
            "layer": int(LAYERS[int(i) // 64]), "expert": int(i) % 64,
            "other_mean_abs_raw": float(ct_other["mean_abs_raw"][i]),
            "other_mean_abs_z": float(ct_other["mean_abs_z"][i]),
            "routine_var": float(var[i]), "var_over_median": float(var[i] / med_var),
            "var_rank_pct": float((var < var[i]).float().mean()),
        } for i in idx_o.tolist()]

        # variance-weighted concentration: share of raw energy in high-variance coords
        order = torch.argsort(var, descending=True)
        def energy_share(ct, frac):
            k = max(1, int(round(frac * D)))
            hi = order[:k]
            tot = ct["mean_raw2"].sum()
            return float(ct["mean_raw2"][hi].sum() / tot.clamp_min(1e-12))
        def z_energy_share(ct, frac):
            k = max(1, int(round(frac * D)))
            hi = order[:k]
            tot = ct["mean_z2"].sum()
            return float(ct["mean_z2"][hi].sum() / tot.clamp_min(1e-12))
        report["coordinates"][fit_b] = {
            "routine_var_median": med_var,
            "routine_var_q10": float(torch.quantile(var.double(), 0.1)),
            "routine_var_q90": float(torch.quantile(var.double(), 0.9)),
            "top10": entries,
            "raw_energy_share_top10pct_var": {
                "code": energy_share(ct_code, 0.10), "other": energy_share(ct_other, 0.10)},
            "raw_energy_share_top25pct_var": {
                "code": energy_share(ct_code, 0.25), "other": energy_share(ct_other, 0.25)},
            "z_energy_share_top10pct_var": {
                "code": z_energy_share(ct_code, 0.10), "other": z_energy_share(ct_other, 0.10)},
            "z_energy_share_top25pct_var": {
                "code": z_energy_share(ct_code, 0.25), "other": z_energy_share(ct_other, 0.25)},
            "corr_meanraw2_var": {
                "code": float(torch.corrcoef(torch.stack([ct_code["mean_raw2"], var]))[0, 1]),
                "other": float(torch.corrcoef(torch.stack([ct_other["mean_raw2"], var]))[0, 1]),
            },
            "n_code_windows": int(code.shape[0]), "n_other_windows": int(other.shape[0]),
            "n_routine_windows": int(rout.shape[0]),
        }
        del code, other, rout

    # ------------------------------------------------------- counterfactual fits
    for fit_b in ("b1", "b2"):
        for sub in ("all", "prose", "structured"):
            f = fits[(fit_b, sub)]
            entry = {"n_fit_windows": f["n_windows"]}
            # routine reference = all routine windows of BOTH batches (post-hoc diagnosis)
            R = torch.cat([r["win"] for r in routine["b1"]] + [r["win"] for r in routine["b2"]])
            dr = decompose(R, f, None)
            rmed = med(dr["whitened_sq"])
            entry["routine_median"] = rmed
            entry["routine_q90"] = q(dr["whitened_sq"], 0.9)
            del R, dr
            # also routine reference restricted to the target batch (cross-batch view)
            tgt = "b2" if fit_b == "b1" else "b1"
            Rt = torch.cat([r["win"] for r in routine[tgt]])
            drt = decompose(Rt, f, None)
            entry["routine_median_target_batch"] = med(drt["whitened_sq"])
            del Rt, drt
            pooled: dict[str, list[torch.Tensor]] = defaultdict(list)
            for d in drift:
                m = d["sel"]["product"]
                if bool(m.any()):
                    pooled[d["domain"]].append(d["win"][m])
            doms = {}
            for dom, mats in pooled.items():
                M = torch.cat(mats)
                dd = decompose(M, f, None)
                doms[dom] = {"median": med(dd["whitened_sq"]),
                             "q90": q(dd["whitened_sq"], 0.9),
                             "ratio_to_routine_median": med(dd["whitened_sq"]) / rmed}
            entry["by_domain"] = doms
            per = []
            for d in drift:
                if not d["is_code"]:
                    continue
                m = d["sel"]["product"]
                dd = decompose(d["win"][m], f, None)
                per.append({"trace": d["short"], "batch": d["batch"],
                            "median": med(dd["whitened_sq"]),
                            "ratio_to_routine_median": med(dd["whitened_sq"]) / rmed})
            entry["code_per_trace"] = per
            report["counterfactual"][f"fit={fit_b}|subset={sub}"] = entry

    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / "whitening_lens.json"
    out.write_text(json.dumps(report, indent=2))
    print("wrote", out, flush=True)


if __name__ == "__main__":
    main()
