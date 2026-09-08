"""Fourth pass: where does routine's variance on the code-shift dimensions come from?

For the (layer, expert) dimensions that carry the code windows' selection-rate shift,
split the routine w=8 window pool by whether the window contains any ``code_symbol``
token (the JSON tool-call punctuation class) and report the per-dimension mean / sd in
each half.  Diagnostic only: the *alarm-level* version of this conditioning was already
run (FCM F1 / F8a) and recovered 0/8 programming drifts.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from research_v2 import io as rio
from research_v2.features import selection_rate_windows

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
BAND = tuple(range(5, 16))
W8 = 8
WINDOW = 48


def main() -> None:
    meta = json.loads((OUT / "token_cache_meta.json").read_text(encoding="utf-8"))
    cache = np.load(OUT / "token_cache.npz")
    classes = meta["classes"]
    ci_sym = classes.index("code_symbol")
    ci_ws = classes.index("whitespace")
    tmeta = {t["trace_id"]: t for t in meta["traces"]}
    tclass_all = cache["tclass"]
    trace_ix_all = cache["trace_ix"]

    batches = rio.load_core()
    traces = {t.trace_id: t for b in batches.values() for t in b}
    routine_ids = sorted([tid for tid, t in tmeta.items() if t["arm_class"] in ("clean", "benign")])
    fit_ids = routine_ids[0::2]
    prog_ids = sorted([tid for tid, t in tmeta.items() if t["arm_class"] == "drift" and t["label_domain"] == "programming"])
    other_by_dom: dict[str, list[str]] = {}
    for tid, t in tmeta.items():
        if t["arm_class"] == "drift" and t["label_domain"] not in (None, "programming"):
            other_by_dom.setdefault(t["label_domain"], []).append(tid)

    mats, flags = [], []
    for tid in fit_ids:
        ends, w = selection_rate_windows(traces[tid].top_k_ids, W8, list(BAND))
        if not len(w):
            continue
        cls = tclass_all[trace_ix_all == tmeta[tid]["trace_ix"]]
        e = ends.numpy()
        has_sym = np.array([bool(((cls[i - W8 + 1 : i + 1] == ci_sym) | (cls[i - W8 + 1 : i + 1] == ci_ws)).any()) for i in e])
        mats.append(w.numpy())
        flags.append(has_sym)
    fit_mat = np.concatenate(mats)
    has_sym = np.concatenate(flags)

    mu_all, sd_all = fit_mat.mean(0), fit_mat.std(0) + 1e-3
    prose = fit_mat[~has_sym]
    jsonish = fit_mat[has_sym]
    mu_p, sd_p = prose.mean(0), prose.std(0) + 1e-3

    def region_mat(ids):
        out = []
        for tid in ids:
            t = tmeta[tid]
            o = t["product_onset"]
            if o is None:
                continue
            ends, w = selection_rate_windows(traces[tid].top_k_ids, W8, list(BAND))
            e = ends.numpy()
            keep = (e >= o + W8 - 1) & (e < min(o + WINDOW, t["token_count"]))
            if keep.any():
                out.append(w.numpy()[keep])
        return np.concatenate(out) if out else np.zeros((0, fit_mat.shape[1]))

    code_mat = region_mat(prog_ids)
    d_code = code_mat.mean(0) - mu_all
    order = np.argsort(-np.abs(d_code))[:20]

    report = {
        "routine_fit_windows": int(len(fit_mat)),
        "frac_windows_with_code_symbol_or_whitespace": float(has_sym.mean()),
        "routine_sd_summary": {
            "all_windows": {"median": float(np.median(sd_all)), "q90": float(np.quantile(sd_all, 0.9))},
            "prose_only_windows": {"median": float(np.median(sd_p)), "q90": float(np.quantile(sd_p, 0.9))},
        },
        "code_top_dims_variance_attribution": [
            {
                "layer": int(BAND[int(i) // 64]),
                "expert": int(int(i) % 64),
                "d_raw_code": float(d_code[i]),
                "routine_mean_all": float(mu_all[i]),
                "routine_sd_all": float(sd_all[i]),
                "routine_mean_prose": float(mu_p[i]),
                "routine_sd_prose": float(sd_p[i]),
                "routine_mean_symbolic": float(jsonish[:, i].mean()),
                "sd_ratio_prose_over_all": float(sd_p[i] / sd_all[i]),
            }
            for i in order
        ],
    }
    # summary: shift norms under the all-window vs prose-only reference
    def z_norm(mat, mu, sd):
        if not len(mat):
            return None
        centre = ((fit_mat - mu) / sd).mean(0)
        return float(np.linalg.norm(((mat - mu) / sd).mean(0) - centre))

    rows = {"code": code_mat}
    for d, ids in sorted(other_by_dom.items()):
        rows["od:" + d] = region_mat(ids)
    report["shift_norm_under_two_references"] = {
        k: {
            "raw_shift_norm": float(np.linalg.norm(v.mean(0) - mu_all)) if len(v) else None,
            "z_all_windows": z_norm(v, mu_all, sd_all),
            "z_prose_only_sd": z_norm(v, mu_all, sd_p),
        }
        for k, v in rows.items()
    }
    report["note"] = (
        "Diagnostic only.  The alarm-level version of a prose-conditioned reference was "
        "already run as FCM F1/F8a and recovered 0/8 programming drifts; conditioning also "
        "turns routine's own JSON windows into outliers."
    )
    (OUT / "token_identity_variance.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["shift_norm_under_two_references"], indent=1))
    print(json.dumps(report["routine_sd_summary"], indent=1))
    print("frac windows with symbol/ws:", report["frac_windows_with_code_symbol_or_whitespace"])
    for r in report["code_top_dims_variance_attribution"][:12]:
        print(f"L{r['layer']} E{r['expert']} d={r['d_raw_code']:+.3f} mu_all={r['routine_mean_all']:.3f} sd_all={r['routine_sd_all']:.3f} mu_prose={r['routine_mean_prose']:.3f} sd_prose={r['routine_sd_prose']:.3f} mu_sym={r['routine_mean_symbolic']:.3f} ratio={r['sd_ratio_prose_over_all']:.2f}")




def variance_alignment() -> None:
    """Addendum: what share of each region's squared raw shift sits on the routine's
    high-variance (layer, expert) dimensions?"""
    meta = json.loads((OUT / "token_cache_meta.json").read_text(encoding="utf-8"))
    tmeta = {t["trace_id"]: t for t in meta["traces"]}
    batches = rio.load_core()
    traces = {t.trace_id: t for b in batches.values() for t in b}
    routine_ids = sorted([tid for tid, t in tmeta.items() if t["arm_class"] in ("clean", "benign")])
    fit_ids = routine_ids[0::2]
    fit_mat = np.concatenate(
        [selection_rate_windows(traces[tid].top_k_ids, W8, list(BAND))[1].numpy() for tid in fit_ids]
    )
    mu, sd = fit_mat.mean(0), fit_mat.std(0) + 1e-3
    hi = sd >= np.quantile(sd, 0.9)
    med = sd >= np.median(sd)

    def region_mat(ids):
        out = []
        for tid in ids:
            t = tmeta[tid]
            o = t["product_onset"]
            if o is None:
                continue
            ends, w = selection_rate_windows(traces[tid].top_k_ids, W8, list(BAND))
            e = ends.numpy()
            keep = (e >= o + W8 - 1) & (e < min(o + WINDOW, t["token_count"]))
            if keep.any():
                out.append(w.numpy()[keep])
        return np.concatenate(out) if out else np.zeros((0, fit_mat.shape[1]))

    groups = {"code": [tid for tid, t in tmeta.items() if t["arm_class"] == "drift" and t["label_domain"] == "programming"]}
    for tid, t in tmeta.items():
        if t["arm_class"] == "drift" and t["label_domain"] not in (None, "programming"):
            groups.setdefault("od:" + t["label_domain"], []).append(tid)
    out = {}
    for name, ids in sorted(groups.items()):
        m = region_mat(ids)
        if not len(m):
            continue
        d = m.mean(0) - mu
        tot = float((d ** 2).sum())
        out[name] = {
            "share_on_top_decile_variance_dims": float((d[hi] ** 2).sum() / tot),
            "share_on_above_median_variance_dims": float((d[med] ** 2).sum() / tot),
            "sd_weighted_by_d2": float((sd * d ** 2).sum() / tot),
        }
    path = OUT / "token_identity_variance.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["variance_alignment"] = {"routine_sd_median": float(np.median(sd)), "routine_sd_q90": float(np.quantile(sd, 0.9)), "regions": out}
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    for k, v in out.items():
        print(k, {a: round(b, 4) for a, b in v.items()})


if __name__ == "__main__":
    main()
    variance_alignment()
