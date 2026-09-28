"""EXPLORATORY / POST-HOC (G-dev ONLY).  Lens (a) + (c) + (d) of the horizon/latency zoom.

Reads only: the frozen G-dev two-stage run (v3_2_a2_verify), the descriptive per-look dump
(desc_V1_channel_dump/outputs.jsonl), the w4 ablation, the untruncated look grids rebuilt
by zoom_v32_endpoint_grid.py, and trace.json metadata.  Nothing here touches G-conf.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SP = Path(sys.argv[1])
A2 = ROOT / "artifacts/agent_v2/dataset_g/v3_2_a2_verify"
DESC = ROOT / "artifacts/agent_v2/dataset_g/v3_2_dev_descriptive"

grid = json.loads((SP / "grid.json").read_text())
gsess = json.loads((SP / "grid_session.json").read_text())
res = json.loads((A2 / "stage2/result.json").read_text())
man = json.loads((A2 / "stage1/threshold_manifest.json").read_text())
S = res["cells"]["S"]
pe = S["metrics"]["positives_anchored"]["per_episode"]
out = {}


def q(a, qs=(0, 25, 50, 75, 90, 100)):
    a = np.asarray(a, dtype=float)
    return {str(x): float(np.percentile(a, x)) for x in qs}


# ---------------------------------------------------------------- (a) horizon
def need(ends, token):
    """Smallest look budget H with H_end >= token; None if the path never reaches it."""
    for i, e in enumerate(ends):
        if e >= token:
            return i + 1
    return None


rows = []
for k, b in pe.items():
    g = grid[k]
    ends = g["ends"]
    rows.append(
        {
            "key": k,
            "fold": None,
            "x": b["x"],
            "e_view": b["e_view"],
            "tokens": g["token_count"],
            "looks_full": len(ends),
            "endpoint_count_at_H": b["endpoint_count"],
            "last_end_at_H": b["last_end"],
            "x_beyond_h": bool(b["x_beyond_h"]),
            "hit": bool(b["hit_plus_16"]),
            "first_alarm_end": b["first_alarm_end"],
            "latency": b["latency"],
            "need_x": need(ends, b["x"]),
            "need_x16": need(ends, b["x"] + 16),
            "trajectory_class": b["trajectory_class"],
            "injection_channel": b["injection_channel"],
            "silent": b["silent"],
            "wording_tier": b["wording_tier"],
        }
    )
fa = res["calibration_design"]["fold_assignment"]
for r in rows:
    scen = r["key"].split("|")[1].split("--")[0]
    r["fold"] = fa[scen]

# definition check: what does the harness's x_beyond_h actually compare?
chk = {
    "flag_equals_last_end_lt_x": sum(
        1 for r in rows if r["x_beyond_h"] == (r["last_end_at_H"] < r["x"])
    ),
    "flag_equals_last_end_lt_x_plus_16": sum(
        1 for r in rows if r["x_beyond_h"] == (r["last_end_at_H"] < r["x"] + 16)
    ),
    "n": len(rows),
}
out["x_beyond_h_definition_check"] = chk

beyond = [r for r in rows if r["x_beyond_h"]]
out["beyond"] = {
    "count": len(beyond),
    "tokens": q([r["tokens"] for r in beyond]),
    "looks_full": q([r["looks_full"] for r in beyond]),
    "x": q([r["x"] for r in beyond]),
    "need_x": q([r["need_x"] for r in beyond if r["need_x"]]),
    "need_x_none": sum(1 for r in beyond if r["need_x"] is None),
    "need_x16": q([r["need_x16"] for r in beyond if r["need_x16"]]),
    "need_x16_none": sum(1 for r in beyond if r["need_x16"] is None),
    "hits": sum(1 for r in beyond if r["hit"]),
    "rows": sorted(
        [
            {
                kk: r[kk]
                for kk in (
                    "key",
                    "fold",
                    "x",
                    "e_view",
                    "tokens",
                    "looks_full",
                    "last_end_at_H",
                    "need_x",
                    "need_x16",
                    "hit",
                    "silent",
                    "injection_channel",
                    "trajectory_class",
                )
            }
            for r in beyond
        ],
        key=lambda r: (r["need_x"] is None, r["need_x"] or 0),
    ),
}
within = [r for r in rows if not r["x_beyond_h"]]
out["within"] = {
    "count": len(within),
    "tokens": q([r["tokens"] for r in within]),
    "looks_full": q([r["looks_full"] for r in within]),
    "x": q([r["x"] for r in within]),
    "need_x": q([r["need_x"] for r in within if r["need_x"]]),
}

# coverage curve of need_x / need_x16 over all 126 positives
def coverage(field):
    vals = [r[field] for r in rows]
    finite = sorted(v for v in vals if v is not None)
    n = len(vals)
    curve = {}
    for H in (89, 107, 113, 200, 250, 300, 352, 400, 450, 500, 550, 600, 700, 913):
        curve[H] = sum(1 for v in finite if v <= H)
    need90 = None
    target = int(np.ceil(0.90 * n))
    if len(finite) >= target:
        need90 = finite[target - 1]
    return {
        "n_positives": n,
        "unreachable_at_any_H": n - len(finite),
        "counts_at_H": curve,
        "H_for_90pct_of_all_positives": need90,
        "H_for_90pct_of_reachable": finite[int(np.ceil(0.90 * len(finite))) - 1] if finite else None,
        "quantiles": q(finite),
    }


out["coverage_need_x"] = coverage("need_x")
out["coverage_need_x16"] = coverage("need_x16")
per_fold_cov = {}
for f in ("0", "1", "2"):
    sub = [r for r in rows if str(r["fold"]) == f]
    fin = sorted(r["need_x"] for r in sub if r["need_x"] is not None)
    n = len(sub)
    tgt = int(np.ceil(0.90 * n))
    per_fold_cov[f] = {
        "n": n,
        "unreachable_at_any_H": n - len(fin),
        "at_352": sum(1 for v in fin if v <= 352),
        "H_for_90pct": fin[tgt - 1] if len(fin) >= tgt else None,
        "max_need": fin[-1] if fin else None,
    }
out["coverage_need_x_per_fold"] = per_fold_cov

# ------------------------------------------------- survivors rule, with/without G-session
ref_keys = {f: man["folds"][f]["cells"]["S"]["calibrations"]["S"]["trace_ids"] for f in ("0", "1", "2")}
sess_norm = {k: v for k, v in gsess.items() if "--clean" in k}
sess_all = gsess


def rule_H(lengths, floor=90):
    a = sorted(lengths, reverse=True)
    return int(a[floor - 1]) if len(a) >= floor else 0


surv = {}
for f in ("0", "1", "2"):
    base = [len(grid[k]["ends"]) for k in ref_keys[f]]
    s_norm = [len(v["ends"]) for v in sess_norm.values()]
    s_all = [len(v["ends"]) for v in sess_all.values()]
    surv[f] = {
        "n_cal": len(base),
        "rule_H_base": rule_H(base),
        "survivors_at_352_base": sum(1 for v in base if v >= 352),
        "rule_H_plus_session_normals": rule_H(base + s_norm),
        "n_plus_session_normals": len(base) + len(s_norm),
        "survivors_at_352_plus_session_normals": sum(1 for v in base + s_norm if v >= 352),
        "rule_H_plus_session_all": rule_H(base + s_all),
        "n_plus_session_all": len(base) + len(s_all),
        "survivors_at_352_plus_session_all": sum(1 for v in base + s_all if v >= 352),
        "base_length_quantiles": q(base),
    }
surv["session_normal_episodes"] = len(sess_norm)
surv["session_all_episodes"] = len(sess_all)
surv["session_normal_length_quantiles"] = q([len(v["ends"]) for v in sess_norm.values()])
surv["session_all_length_quantiles"] = q([len(v["ends"]) for v in sess_all.values()])
surv["gdev_filtered_normal_length_quantiles"] = q(
    [len(grid[k]["ends"]) for f in ("0", "1", "2") for k in ref_keys[f]]
)
# how many looks would be needed for the rule to yield H >= 352 on a fold?
surv["survivors_at_352_needed"] = 90
out["survivors_rule"] = surv

# --------------------------------------------------------------- (c) latency
hits = [r for r in rows if r["hit"]]
lat = [r["latency"] for r in hits]
out["latency_alpha_0_10"] = {
    "n_hits": len(hits),
    "quantiles": q(lat, (0, 10, 25, 50, 75, 90, 100)),
    "share_within_plus_8": sum(1 for v in lat if v <= 8) / len(lat),
    "share_within_plus_16": sum(1 for v in lat if v <= 16) / len(lat),
    "share_negative_early": sum(1 for v in lat if v < 0) / len(lat),
    "count_within_plus_8": sum(1 for v in lat if v <= 8),
    "count_negative": sum(1 for v in lat if v < 0),
    "count_zero_to_8": sum(1 for v in lat if 0 <= v <= 8),
    "positives_denominator": len(rows),
}
out["_rows"] = rows
(SP / "zoom_a.json").write_text(json.dumps(out, indent=1))
print(json.dumps({k: v for k, v in out.items() if k != "_rows"}, indent=1)[:9000])
