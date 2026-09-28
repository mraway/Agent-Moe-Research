"""REFUTER part 2: is the code 'raw deviation' a token-class (routine JSON-mode) artefact?

Alternative hypothesis H_alt: the routine window cloud is bimodal (tool-call/structured
windows vs prose windows); the highest-routine-variance coordinates are the ones carrying
that internal mode split; code windows sit at the structured mode.  Then code's 'large raw
deviation' is not off-domain evidence at all, and whitening removes a ROUTINE-INTERNAL axis
rather than a code-specific direction.

Post-hoc diagnosis only; no detector, no calibration, no improvement claim.
"""
from __future__ import annotations

import json
import string
from pathlib import Path

import torch

from research_v2 import io as rio
from research_v2.features import selection_rate_windows

REPO = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
OUT = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot"
LAYERS = tuple(range(5, 16))
W, FLOOR, SPAN = 8, 1e-3, 48
PROG = ("b1-f2-012", "b1-f2-014", "b1-f3-016",
        "b2-f2-011", "b2-f2-012", "b2-f2-014", "b2-f2-015", "b2-f3-020")
PUNCT = set(string.punctuation) | {"“", "”", "‘", "’", "—", "–", "…"}


def short(t):
    return "-".join(t.split("-")[:3])


def auc(pos, neg):
    p = pos.double().sort().values
    n = neg.double().sort().values
    lo = torch.searchsorted(n, p, right=False).double()
    hi = torch.searchsorted(n, p, right=True).double()
    return float(((lo + hi) / 2).sum() / (p.numel() * n.numel()))


def prose_mask(token_ids, ends):
    """Colleague's rule, re-implemented: no '{', '\"', ':' in the 8 tokens and <30% alpha-free digit/punct."""
    texts = rio.decode_token_texts(token_ids.tolist())
    jf, df = [], []
    for tok in texts:
        jf.append(any(c in tok for c in ("{", '"', ":")))
        core = tok.strip()
        if not core:
            df.append(False)
            continue
        df.append((not any(c.isalpha() for c in core)) and any(c.isdigit() or c in PUNCT for c in core))
    m = torch.zeros(ends.numel(), dtype=torch.bool)
    for i, e in enumerate(ends.tolist()):
        lo = e - W + 1
        if any(jf[lo:e + 1]):
            continue
        if sum(df[lo:e + 1]) / float(W) >= 0.30:
            continue
        m[i] = True
    return m


def main():
    batches = rio.load_core()
    labels = {short(json.loads(l)["trace_id"]): json.loads(l)
              for l in (REPO / "docs/research_v2/labels/product_onset_v1_adjudicated.jsonl").read_text().splitlines() if l.strip()}
    routine = {"b1": [], "b2": []}
    rout_prose, rout_struct = {"b1": [], "b2": []}, {"b1": [], "b2": []}
    dom_w, dom_prose = {}, {}
    code_traces = {}
    for bk, traces in batches.items():
        for tr in traces:
            ends, win = selection_rate_windows(tr.top_k_ids, W, LAYERS)
            if not ends.numel():
                continue
            cls = rio.arm_class(tr)
            pm = prose_mask(tr.token_ids, ends)
            if cls in ("clean", "benign"):
                routine[bk].append(win)
                rout_prose[bk].append(win[pm])
                rout_struct[bk].append(win[~pm])
            elif cls == "drift":
                sid = short(tr.trace_id)
                row = labels[sid]
                po, T = int(row["product_onset"]), int(tr.token_count)
                sel = (ends >= po) & (ends < min(po + SPAN, T))
                dm = row["domain"]
                dom_w.setdefault(dm, []).append(win[sel])
                dom_prose.setdefault(dm, []).append(pm[sel])
                if sid in PROG:
                    code_traces[sid] = (win[sel], pm[sel])

    cat = lambda xs: torch.cat([x for x in xs if x.shape[0]])
    R_all = cat([w for b in ("b1", "b2") for w in routine[b]])
    R_prose = cat([w for b in ("b1", "b2") for w in rout_prose[b]])
    R_struct = cat([w for b in ("b1", "b2") for w in rout_struct[b]])
    dom = {k: cat(v) for k, v in dom_w.items()}
    domp = {k: torch.cat(v) for k, v in dom_prose.items()}
    out = {"counts": {"routine_all": int(R_all.shape[0]), "routine_prose": int(R_prose.shape[0]),
                      "routine_structured": int(R_struct.shape[0]),
                      "prose_fraction_routine": float(R_prose.shape[0] / R_all.shape[0])},
           "prose_fraction_by_domain": {k: float(v.double().mean()) for k, v in domp.items()},
           "prose_fraction_by_code_trace": {k: float(v[1].double().mean()) for k, v in code_traces.items()}}

    res = {}
    for fb in ("b1", "b2"):
        mu = torch.cat(routine[fb]).mean(0)
        sd = torch.cat(routine[fb]).std(0) + FLOOR
        raw = lambda x: ((x - mu) ** 2).sum(1)
        wht = lambda x: (((x - mu) / sd) ** 2).sum(1)
        r_all_raw, r_all_wht = raw(R_all), wht(R_all)
        r_pr_raw, r_pr_wht = raw(R_prose), wht(R_prose)
        r_st_raw, r_st_wht = raw(R_struct), wht(R_struct)
        e = {"routine_all": {"raw_med": float(r_all_raw.median()), "wht_med": float(r_all_wht.median())},
             "routine_prose": {"raw_med": float(r_pr_raw.median()), "wht_med": float(r_pr_wht.median()),
                               "raw_ratio_vs_all": float(r_pr_raw.median() / r_all_raw.median()),
                               "wht_ratio_vs_all": float(r_pr_wht.median() / r_all_wht.median())},
             "routine_structured": {"raw_med": float(r_st_raw.median()), "wht_med": float(r_st_wht.median()),
                                    "raw_ratio_vs_all": float(r_st_raw.median() / r_all_raw.median()),
                                    "wht_ratio_vs_all": float(r_st_wht.median() / r_all_wht.median()),
                                    "raw_auc_vs_prose": auc(r_st_raw, r_pr_raw),
                                    "wht_auc_vs_prose": auc(r_st_wht, r_pr_wht),
                                    "raw_auc_vs_all": auc(r_st_raw, r_all_raw),
                                    "wht_auc_vs_all": auc(r_st_wht, r_all_wht)}}
        # domains scored against three reference pools
        e["domains"] = {}
        for dmn, wv in dom.items():
            dr, dw = raw(wv), wht(wv)
            e["domains"][dmn] = {
                "raw_ratio_all": float(dr.median() / r_all_raw.median()),
                "raw_ratio_struct": float(dr.median() / r_st_raw.median()),
                "wht_ratio_struct": float(dw.median() / r_st_wht.median()),
                "raw_auc_all": auc(dr, r_all_raw), "raw_auc_struct": auc(dr, r_st_raw),
                "wht_auc_all": auc(dw, r_all_wht), "wht_auc_struct": auc(dw, r_st_wht),
                "raw_gt_struct_q90": float((dr > torch.quantile(r_st_raw.double(), .9)).double().mean()),
                "wht_gt_struct_q90": float((dw > torch.quantile(r_st_wht.double(), .9)).double().mean()),
            }
        for sid, (wv, _) in code_traces.items():
            dr, dw = raw(wv), wht(wv)
            e["domains"]["TRACE:" + sid] = {
                "raw_ratio_all": float(dr.median() / r_all_raw.median()),
                "raw_ratio_struct": float(dr.median() / r_st_raw.median()),
                "wht_ratio_struct": float(dw.median() / r_st_wht.median()),
                "raw_auc_all": auc(dr, r_all_raw), "raw_auc_struct": auc(dr, r_st_raw),
                "wht_auc_all": auc(dw, r_all_wht), "wht_auc_struct": auc(dw, r_st_wht),
                "raw_gt_struct_q90": float((dr > torch.quantile(r_st_raw.double(), .9)).double().mean()),
                "wht_gt_struct_q90": float((dw > torch.quantile(r_st_wht.double(), .9)).double().mean()),
            }
        # variance decomposition of the FIT routine into between-mode / within-mode
        fp = cat(rout_prose[fb]); fs = cat(rout_struct[fb]); fa = torch.cat(routine[fb])
        n1, n2, N = fp.shape[0], fs.shape[0], fa.shape[0]
        m1, m2, m = fp.mean(0), fs.mean(0), fa.mean(0)
        var_tot = fa.var(0)
        var_bet = (n1 * (m1 - m) ** 2 + n2 * (m2 - m) ** 2) / N
        frac_bet = var_bet / var_tot
        code_all = torch.cat([v[0] for v in code_traces.values()])
        other_all = cat([wv for k, wv in dom.items() if k != "programming"])
        dev_code = (code_all - mu).abs().mean(0)
        dev_other = (other_all - mu).abs().mean(0)
        order = var_tot.argsort()
        top10_code = dev_code.argsort(descending=True)[:10]
        e["variance_decomposition"] = {
            "between_mode_fraction_overall": float(var_bet.sum() / var_tot.sum()),
            "between_fraction_median_all": float(frac_bet.median()),
            "between_fraction_median_decile9": float(frac_bet[order[630:]].median()),
            "between_fraction_median_decile0": float(frac_bet[order[:70]].median()),
            "top10_code_coords": [[int(i) // 64 + 5, int(i) % 64, float(dev_code[i]),
                                   float(var_tot[i] / var_tot.median()), float(frac_bet[i])]
                                  for i in top10_code.tolist()],
            "corr_devcode2_varbetween": float(torch.corrcoef(torch.stack([dev_code ** 2, var_bet]))[0, 1]),
            "corr_devother2_varbetween": float(torch.corrcoef(torch.stack([dev_other ** 2, var_bet]))[0, 1]),
        }
        # direction alignment: is code's mean deviation the routine structured-mode axis?
        axis = (m2 - m)  # structured routine mode direction (raw units)
        def cos(a, b):
            return float((a @ b) / (a.norm() * b.norm()))
        e["direction_alignment"] = {
            "cos(code_mean_dev, routine_structured_axis)": cos(code_all.mean(0) - mu, axis),
            "cos(other_mean_dev, routine_structured_axis)": cos(other_all.mean(0) - mu, axis),
            **{f"cos({dmn})": cos(wv.mean(0) - mu, axis) for dmn, wv in dom.items()},
            **{f"cos(TRACE:{sid})": cos(wv.mean(0) - mu, axis) for sid, (wv, _) in code_traces.items()},
            "cos_whitened(code, axis)": cos((code_all.mean(0) - mu) / sd, axis / sd),
            "cos_whitened(other, axis)": cos((other_all.mean(0) - mu) / sd, axis / sd),
        }
        # held-out routine reference (fit b1 -> reference = b2 routine only, and vice versa)
        ob = "b2" if fb == "b1" else "b1"
        R_out = torch.cat(routine[ob]); R_in = torch.cat(routine[fb])
        e["insample_vs_heldout"] = {
            "raw_med_in": float(raw(R_in).median()), "raw_med_out": float(raw(R_out).median()),
            "wht_med_in": float(wht(R_in).median()), "wht_med_out": float(wht(R_out).median()),
            "code_gain_ref_in": float((wht(code_all).median() / wht(R_in).median()) /
                                      (raw(code_all).median() / raw(R_in).median())),
            "code_gain_ref_out": float((wht(code_all).median() / wht(R_out).median()) /
                                       (raw(code_all).median() / raw(R_out).median())),
            "code_auc_wht_ref_out": auc(wht(code_all), wht(R_out)),
            "code_auc_raw_ref_out": auc(raw(code_all), raw(R_out)),
        }
        res[fb] = e
    out["fits"] = res
    (OUT / "refute_whitening_confound.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out["counts"], indent=1))
    print(json.dumps(out["prose_fraction_by_domain"], indent=1))
    print(json.dumps(out["prose_fraction_by_code_trace"], indent=1))


if __name__ == "__main__":
    main()
