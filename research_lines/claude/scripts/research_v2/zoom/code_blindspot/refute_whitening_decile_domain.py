"""REFUTER part 3: does the 'lowest-variance decile / rare expert' account actually rank the
domains by separability, or is the pooled 78.6% driven by a few extreme domains?

Also: per-trace correlation of whitened AUC with (gain, rare-expert mass, raw AUC).
Post-hoc diagnosis; not a detector.
"""
from __future__ import annotations

import json
from pathlib import Path

import torch

from research_v2 import io as rio
from research_v2.features import selection_rate_windows

REPO = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
OUT = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot"
LAYERS, W, FLOOR, SPAN = tuple(range(5, 16)), 8, 1e-3, 48
PROG = ("b1-f2-012", "b1-f2-014", "b1-f3-016",
        "b2-f2-011", "b2-f2-012", "b2-f2-014", "b2-f2-015", "b2-f3-020")


def short(t):
    return "-".join(t.split("-")[:3])


def auc(pos, neg):
    p = pos.double().sort().values
    n = neg.double().sort().values
    lo = torch.searchsorted(n, p, right=False).double()
    hi = torch.searchsorted(n, p, right=True).double()
    return float(((lo + hi) / 2).sum() / (p.numel() * n.numel()))


def main():
    batches = rio.load_core()
    labels = {short(json.loads(l)["trace_id"]): json.loads(l)
              for l in (REPO / "docs/research_v2/labels/product_onset_v1_adjudicated.jsonl").read_text().splitlines() if l.strip()}
    routine = {"b1": [], "b2": []}
    traces = []
    for bk, trs in batches.items():
        for tr in trs:
            ends, win = selection_rate_windows(tr.top_k_ids, W, LAYERS)
            if not ends.numel():
                continue
            cls = rio.arm_class(tr)
            if cls in ("clean", "benign"):
                routine[bk].append(win)
            elif cls == "drift":
                sid = short(tr.trace_id)
                row = labels[sid]
                po, T = int(row["product_onset"]), int(tr.token_count)
                sel = (ends >= po) & (ends < min(po + SPAN, T))
                traces.append({"sid": sid, "domain": row["domain"], "batch": bk,
                               "is_code": sid in PROG, "win": win[sel]})
    R = torch.cat([w for b in ("b1", "b2") for w in routine[b]])
    out = {}
    for fb in ("b1", "b2"):
        M = torch.cat(routine[fb])
        mu, sd = M.mean(0), M.std(0) + FLOOR
        var = M.var(0)
        order = var.argsort()
        d0 = order[:70]
        rw = (((R - mu) / sd) ** 2)
        r_tot = float(rw.sum(1).mean())
        r_d0 = float(rw[:, d0].sum(1).mean())
        rraw = ((R - mu) ** 2).sum(1)
        rwht = rw.sum(1)
        rare_mask = mu < 0.02
        rows = {}
        groups = {}
        for t in traces:
            groups.setdefault(t["domain"], []).append(t["win"])
        for name, wv in [(f"DOMAIN:{k}", torch.cat(v)) for k, v in groups.items()] + \
                        [(f"TRACE:{t['sid']}", t["win"]) for t in traces]:
            z = (((wv - mu) / sd) ** 2)
            tot = float(z.sum(1).mean())
            e0 = float(z[:, d0].sum(1).mean())
            raw = ((wv - mu) ** 2).sum(1)
            wht = z.sum(1)
            rows[name] = {
                "n": int(wv.shape[0]),
                "wht_total_mean": tot, "wht_d0_mean": e0, "wht_d0_share": e0 / tot,
                "wht_d0_over_routine_d0": e0 / r_d0,
                "wht_rest_over_routine_rest": (tot - e0) / (r_tot - r_d0),
                "auc_wht": auc(wht, rwht), "auc_raw": auc(raw, rraw),
                "gain": float((wht.median() / rwht.median()) / (raw.median() / rraw.median())),
                "rare_mass": float((wv[:, rare_mask].sum(1) / wv.sum(1)).mean()),
            }
        rows["ROUTINE"] = {"n": int(R.shape[0]), "wht_total_mean": r_tot, "wht_d0_mean": r_d0,
                           "wht_d0_share": r_d0 / r_tot, "wht_d0_over_routine_d0": 1.0,
                           "wht_rest_over_routine_rest": 1.0, "auc_wht": 0.5, "auc_raw": 0.5,
                           "gain": 1.0,
                           "rare_mass": float((R[:, rare_mask].sum(1) / R.sum(1)).mean())}
        # correlations across the 59 drift traces
        tr_rows = {k: v for k, v in rows.items() if k.startswith("TRACE:")}
        import math
        def corr(a, b):
            a = torch.tensor(a); b = torch.tensor(b)
            return float(torch.corrcoef(torch.stack([a, b]))[0, 1])
        aucs = [v["auc_wht"] for v in tr_rows.values()]
        out[fb] = {"rows": rows,
                   "corr_auc_gain": corr(aucs, [v["gain"] for v in tr_rows.values()]),
                   "corr_auc_log_gain": corr(aucs, [math.log(v["gain"]) for v in tr_rows.values()]),
                   "corr_auc_raremass": corr(aucs, [v["rare_mass"] for v in tr_rows.values()]),
                   "corr_auc_log_d0ratio": corr(aucs, [math.log(v["wht_d0_over_routine_d0"]) for v in tr_rows.values()]),
                   "corr_auc_aucraw": corr(aucs, [v["auc_raw"] for v in tr_rows.values()]),
                   "routine_d0_mean": r_d0, "routine_total_mean": r_tot}
    (OUT / "refute_whitening_decile_domain.json").write_text(json.dumps(out, indent=1))
    for fb in ("b1", "b2"):
        print("=== fit", fb, {k: round(v, 3) for k, v in out[fb].items() if k.startswith("corr")})
        rows = out[fb]["rows"]
        print(f"{'set':24s} {'n':>5s} {'whtTot':>8s} {'d0share':>7s} {'d0/rtn':>7s} {'rest/rtn':>8s} {'aucWht':>6s} {'gain':>6s} {'rare':>6s}")
        for k in sorted(rows, key=lambda k: -rows[k]["auc_wht"]):
            if k.startswith("TRACE:"):
                continue
            v = rows[k]
            print(f"{k:24s} {v['n']:5d} {v['wht_total_mean']:8.0f} {v['wht_d0_share']:7.3f} {v['wht_d0_over_routine_d0']:7.1f} {v['wht_rest_over_routine_rest']:8.2f} {v['auc_wht']:6.3f} {v['gain']:6.2f} {v['rare_mass']:6.4f}")
        print("  code traces:")
        for k in [f"TRACE:{s}" for s in PROG]:
            v = rows[k]
            print(f"{k:24s} {v['n']:5d} {v['wht_total_mean']:8.0f} {v['wht_d0_share']:7.3f} {v['wht_d0_over_routine_d0']:7.1f} {v['wht_rest_over_routine_rest']:8.2f} {v['auc_wht']:6.3f} {v['gain']:6.2f} {v['rare_mass']:6.4f}")


if __name__ == "__main__":
    main()
