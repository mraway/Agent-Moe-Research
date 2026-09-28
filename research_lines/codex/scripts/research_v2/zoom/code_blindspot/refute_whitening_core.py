"""REFUTER (independent recomputation) for the lens
'what the whitening does to the code direction' (CAND-A WGM g1).

Written from the definitions only; imports nothing from the colleague's scripts.
Post-hoc diagnosis: window sets are selected with adjudicated labels. Not a detector.
"""
from __future__ import annotations

import json
from pathlib import Path

import torch

from research_v2 import io as rio
from research_v2.features import selection_rate_windows

REPO = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
OUT = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot"
LAYERS = tuple(range(5, 16))
W = 8
FLOOR = 1e-3
SPAN = 48
PROG = ("b1-f2-012", "b1-f2-014", "b1-f3-016",
        "b2-f2-011", "b2-f2-012", "b2-f2-014", "b2-f2-015", "b2-f3-020")


def short(tid: str) -> str:
    return "-".join(tid.split("-")[:3])


def load_labels():
    rows = [json.loads(l) for l in (REPO / "docs/research_v2/labels/product_onset_v1_adjudicated.jsonl").read_text().splitlines() if l.strip()]
    return {short(r["trace_id"]): r for r in rows}


def auc(pos: torch.Tensor, neg: torch.Tensor) -> float:
    """P(pos > neg) with 0.5 for ties, computed by rank counting."""
    p = pos.double().sort().values
    n = neg.double().sort().values
    lo = torch.searchsorted(n, p, right=False).double()
    hi = torch.searchsorted(n, p, right=True).double()
    return float(((lo + hi) / 2).sum() / (p.numel() * n.numel()))


def build():
    batches = rio.load_core()
    labels = load_labels()
    routine = {"b1": [], "b2": []}
    drifts = []
    for bkey, traces in batches.items():
        for tr in traces:
            ends, win = selection_rate_windows(tr.top_k_ids, W, LAYERS)
            if not ends.numel():
                continue
            cls = rio.arm_class(tr)
            if cls in ("clean", "benign"):
                routine[bkey].append({"tid": tr.trace_id, "batch": bkey, "win": win,
                                      "ends": ends, "token_ids": tr.token_ids})
            elif cls == "drift":
                sid = short(tr.trace_id)
                row = labels[sid]
                T = int(tr.token_count)
                po, eo = int(row["product_onset"]), int(row["evidence_onset"])
                sel_p = (ends >= po) & (ends < min(po + SPAN, T))
                sel_e = (ends >= eo) & (ends < min(eo + SPAN, T))
                sel_in = (ends >= po + W - 1) & (ends < min(po + SPAN, T))
                drifts.append({"sid": sid, "tid": tr.trace_id, "batch": bkey,
                               "domain": row["domain"], "pclass": row["product_class"],
                               "workflow": tr.workflow, "channel": tr.channel,
                               "po": po, "eo": eo, "T": T, "win": win, "ends": ends,
                               "sel_p": sel_p, "sel_e": sel_e, "sel_in": sel_in,
                               "is_code": sid in PROG})
    return routine, drifts


def fit(blocks):
    m = torch.cat(blocks)
    mu = m.mean(0)
    sd = m.std(0) + FLOOR
    return mu, sd


def verify(routine, drifts):
    """Refit b1-routine whitening, compare with frozen b1_to_b2 score_streams."""
    mu, sd = fit([r["win"] for r in routine["b1"]])
    frozen = json.load(open(REPO / "artifacts/agent_v2/research_v2/wgm/c2_g1_middle_late/result.json"))
    case = [c for c in frozen["case_runs"] if c["case"] == "b1_to_b2"][0]
    ss = case["score_streams"]
    batches = rio.load_core()
    worst = 0.0
    n = 0
    for tr in batches["b2"]:
        if tr.trace_id not in ss:
            continue
        ends, win = selection_rate_windows(tr.top_k_ids, W, LAYERS)
        z = (win - mu) / sd
        s = (z ** 2).sum(1)
        ref = torch.tensor(ss[tr.trace_id]["scores"], dtype=torch.float64)
        assert list(ends.tolist()) == ss[tr.trace_id]["ends"]
        worst = max(worst, float((s.double() - ref).abs().max()))
        n += 1
    return {"traces": n, "max_abs_diff_vs_frozen_rounded": worst}


def dist(win, mu, sd, alpha=1.0):
    d = (win - mu)
    if alpha == 0.0:
        return (d ** 2).sum(1)
    return ((d / sd ** alpha) ** 2).sum(1)


def tail_stats(vals, ref_sorted, q90, q99):
    return {"median": float(vals.median()),
            "gt_q90": float((vals > q90).double().mean()),
            "gt_q99": float((vals > q99).double().mean()),
            "pctl_of_median": float((ref_sorted < vals.median()).double().mean())}


def main():
    routine, drifts = build()
    out = {"reproduction": verify(routine, drifts)}
    print("repro", out["reproduction"])

    routine_all = torch.cat([r["win"] for b in ("b1", "b2") for r in routine[b]])
    print("routine windows", routine_all.shape, "b1", sum(r["win"].shape[0] for r in routine["b1"]),
          "b2", sum(r["win"].shape[0] for r in routine["b2"]))
    code_w = torch.cat([d["win"][d["sel_p"]] for d in drifts if d["is_code"]])
    other_w = torch.cat([d["win"][d["sel_p"]] for d in drifts if not d["is_code"]])
    print("code windows", code_w.shape[0], "other", other_w.shape[0])

    domains = sorted({d["domain"] for d in drifts})
    dom_w = {dm: torch.cat([d["win"][d["sel_p"]] for d in drifts if d["domain"] == dm]) for dm in domains}

    out["counts"] = {"routine": int(routine_all.shape[0]), "code": int(code_w.shape[0]),
                     "other": int(other_w.shape[0]),
                     "per_domain": {dm: int(v.shape[0]) for dm, v in dom_w.items()}}

    # --- A. domain table: raw AND whitened separation (the key new column) ---
    out["domain_table"] = {}
    for fitb in ("b1", "b2"):
        mu, sd = fit([r["win"] for r in routine[fitb]])
        rows = {}
        stats = {}
        for alpha in (0.0, 0.25, 0.5, 0.75, 1.0):
            rr = dist(routine_all, mu, sd, alpha)
            rs = rr.sort().values
            q90 = torch.quantile(rr.double(), 0.90).float()
            q99 = torch.quantile(rr.double(), 0.99).float()
            stats[alpha] = {"routine_median": float(rr.median()), "routine_q90": float(q90),
                            "routine_q99": float(q99),
                            "routine_q90_over_median": float(q90 / rr.median())}
            for dm, wv in list(dom_w.items()) + [("ALL_OTHER", other_w)]:
                dv = dist(wv, mu, sd, alpha)
                key = f"{dm}|a{alpha}"
                rows[key] = {"median": float(dv.median()),
                             "ratio": float(dv.median() / rr.median()),
                             "auc": auc(dv, rr),
                             "gt_q90": float((dv > q90).double().mean()),
                             "gt_q99": float((dv > q99).double().mean()),
                             "pctl_of_median": float((rs < dv.median()).double().mean())}
        out["domain_table"][fitb] = {"routine": {str(k): v for k, v in stats.items()}, "domains": rows}

    # --- B. per-trace gain for ALL 59 drifts (cross-batch fit) ---
    pertrace = {}
    for fitb in ("b1", "b2"):
        mu, sd = fit([r["win"] for r in routine[fitb]])
        rr_raw = dist(routine_all, mu, sd, 0.0)
        rr_wht = dist(routine_all, mu, sd, 1.0)
        med_raw, med_wht = float(rr_raw.median()), float(rr_wht.median())
        rows = {}
        for d in drifts:
            for anchor, key in (("sel_p", "product"), ("sel_e", "evidence"), ("sel_in", "inside")):
                wv = d["win"][d[anchor]]
                if wv.shape[0] == 0:
                    continue
                raw = dist(wv, mu, sd, 0.0)
                wht = dist(wv, mu, sd, 1.0)
                rr = float(raw.median()) / med_raw
                wr = float(wht.median()) / med_wht
                rows.setdefault(d["sid"], {})[key] = {
                    "n": int(wv.shape[0]), "raw_ratio": rr, "wht_ratio": wr,
                    "gain": wr / rr, "auc_raw": auc(raw, rr_raw), "auc_wht": auc(wht, rr_wht)}
            rows[d["sid"]].update({"domain": d["domain"], "batch": d["batch"],
                                   "pclass": d["pclass"], "po": d["po"], "eo": d["eo"],
                                   "T": d["T"], "is_code": d["is_code"],
                                   "workflow": d["workflow"], "channel": d["channel"]})
        pertrace[fitb] = rows
    out["pertrace"] = pertrace

    # --- C. variance deciles + rare-expert mass ---
    dec = {}
    for fitb in ("b1", "b2"):
        mu, sd = fit([r["win"] for r in routine[fitb]])
        var = (sd - FLOOR) ** 2
        order = var.argsort()
        groups = [order[i * 70:(i + 1) * 70] for i in range(10)]
        rows = []
        for i, g in enumerate(groups):
            e = {"decile": i, "n": int(g.numel()), "var_median": float(var[g].median())}
            for name, wv in (("code", code_w), ("other", other_w), ("routine", routine_all)):
                dfull = (wv - mu)
                raw_e = (dfull ** 2)
                z_e = (dfull / sd) ** 2
                e[f"raw_share_{name}"] = float(raw_e[:, g].sum() / raw_e.sum())
                e[f"wht_share_{name}"] = float(z_e[:, g].sum() / z_e.sum())
            rows.append(e)
        totals = {}
        for name, wv in (("code", code_w), ("other", other_w), ("routine", routine_all)):
            dfull = (wv - mu)
            totals[name] = {"raw_mean_energy": float((dfull ** 2).sum(1).mean()),
                            "wht_mean_energy": float(((dfull / sd) ** 2).sum(1).mean())}
        # rare experts by routine MEAN selection rate
        rare = {}
        for tau in (0.02, 0.005):
            mask = mu < tau
            rare[str(tau)] = {"n_coords": int(mask.sum())}
            for name, wv in [("routine", routine_all), ("code", code_w)] + list(dom_w.items()):
                share = wv[:, mask].sum(1) / wv.sum(1)
                rare[str(tau)][name] = {"mean": float(share.mean()), "median": float(share.median())}
            for d in drifts:
                if d["is_code"]:
                    wv = d["win"][d["sel_p"]]
                    rare[str(tau)][d["sid"]] = {"mean": float((wv[:, mask].sum(1) / wv.sum(1)).mean())}
        dec[fitb] = {"deciles": rows, "totals": totals, "rare": rare,
                     "var_median": float(var.median()), "var_min": float(var.min()),
                     "n_var_below_1e-4": int((var < 1e-4).sum())}
    out["deciles"] = dec

    (OUT / "refute_whitening_core.json").write_text(json.dumps(out, indent=1))
    print("written", OUT / "refute_whitening_core.json")


if __name__ == "__main__":
    main()
