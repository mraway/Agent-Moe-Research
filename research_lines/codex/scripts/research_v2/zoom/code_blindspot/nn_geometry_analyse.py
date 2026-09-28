"""Distance tables for the NN-geometry lens (reads the caches built by nn_geometry_build.py)."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import torch

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"


def q(t: torch.Tensor, ps=(0.05, 0.25, 0.5, 0.75, 0.95)):
    if t.numel() == 0:
        return [float("nan")] * len(ps)
    return [round(float(torch.quantile(t.float(), p)), 4) for p in ps]


def load(band: str, batch: str):
    return torch.load(OUT / f"nn_{band}_{batch}.pt", weights_only=False)


def main(band: str = "middle_late", anchor: str = "product") -> dict:
    report: dict = {"band": band, "anchor": anchor, "batches": {}}
    for batch in ("b1", "b2"):
        d = load(band, batch)
        meta = d["meta"]
        keep = [i for i, m in enumerate(meta) if m["anchor"] == anchor]
        out: dict = {}
        for space in ("raw", "whitened"):
            td = d[f"target_d_{space}"]
            rd = d[f"routine_d_{space}"]
            tn = d[f"target_norm_{space}"]
            rows_by_trace = defaultdict(list)
            for i in keep:
                rows_by_trace[meta[i]["trace_id"]].append(i)
            per_trace = {}
            for tid, idxs in rows_by_trace.items():
                ii = torch.tensor(idxs)
                per_trace[tid] = {
                    "domain": meta[idxs[0]]["domain"],
                    "product_class": meta[idxs[0]]["product_class"],
                    "onset": meta[idxs[0]]["onset"],
                    "n_windows": len(idxs),
                    "d1_med": round(float(td[ii, 0].median()), 4),
                    "d1_min": round(float(td[ii, 0].min()), 4),
                    "d1_max": round(float(td[ii, 0].max()), 4),
                    "d10_med": round(float(td[ii].mean(1).median()), 4),
                    "norm_med": round(float(tn[ii].median()), 4),
                }
            # group aggregates
            groups = defaultdict(list)
            for i in keep:
                dom = meta[i]["domain"]
                groups["code" if dom == "programming" else "other"].append(i)
                groups[f"dom:{dom}"].append(i)
            agg = {}
            for gname, idxs in groups.items():
                ii = torch.tensor(idxs)
                agg[gname] = {
                    "n_windows": len(idxs),
                    "n_traces": len({meta[i]["trace_id"] for i in idxs}),
                    "d1_q": q(td[ii, 0]),
                    "d10_q": q(td[ii].mean(1)),
                    "norm_q": q(tn[ii]),
                }
            agg["routine_holdout"] = {
                "n_windows": int(rd.shape[0]),
                "n_traces": len(d["routine_trace_ids"]),
                "d1_q": q(rd[:, 0]),
                "d10_q": q(rd.mean(1)),
                "norm_q": q(d[f"routine_norm_{space}"]),
            }
            out[space] = {"per_trace": per_trace, "aggregate": agg}
        report["batches"][batch] = {"scalars": d["scalars"], **out}
    return report


if __name__ == "__main__":
    import sys
    band = sys.argv[1] if len(sys.argv) > 1 else "middle_late"
    anchor = sys.argv[2] if len(sys.argv) > 2 else "product"
    rep = main(band, anchor)
    path = OUT / f"distances_{band}_{anchor}.json"
    path.write_text(json.dumps(rep, indent=2))
    for batch, blk in rep["batches"].items():
        for space in ("raw", "whitened"):
            print(f"\n=== {band} {anchor} {batch} {space} ===")
            a = blk[space]["aggregate"]
            print(f"{'group':22s} {'n_win':>6s} {'d1 q05/q25/med/q75/q95':>40s}   {'d10 med':>8s} {'norm med':>9s}")
            for g in ["routine_holdout", "code", "other"] + sorted(k for k in a if k.startswith("dom:")):
                r = a[g]
                print(f"{g:22s} {r['n_windows']:6d} {str(r['d1_q']):>40s}   {r['d10_q'][2]:8.3f} {r['norm_q'][2]:9.3f}")
            print("-- per code trace --")
            for tid, r in blk[space]["per_trace"].items():
                if r["domain"] == "programming":
                    print(f"  {tid[:52]:52s} {r['product_class']:3s} on={r['onset']:3d} n={r['n_windows']:2d} d1_med={r['d1_med']:8.3f} d1_min={r['d1_min']:8.3f} d10_med={r['d10_med']:8.3f} norm={r['norm_med']:8.3f}")
