"""EXPLORATORY / POST-HOC (G-dev ONLY).  Lens (c) + (d): latency and session spread.

Recomputes the first CONFIRMED look at alpha in {0.05, 0.10, 0.15} directly from the
stored per-look p_S of the descriptive dump (in-horizon rows only), so no harness re-run
is needed; the alarm rule p(k) <= alpha is exactly what the harness applies.
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
ALPHAS = (0.05, 0.10, 0.15)

res = json.loads((A2 / "stage2/result.json").read_text())
S = res["cells"]["S"]
pe = S["metrics"]["positives_anchored"]["per_episode"]
anchors = S["anchors"]

first = {a: {} for a in ALPHAS}          # key -> first in-horizon end with p<=a
lastend = {}
variant = {}
with (DESC / "desc_V1_channel_dump/outputs.jsonl").open() as f:
    for line in f:
        r = json.loads(line)
        if r.get("horizon_censored"):
            continue
        k = r["key"]
        lastend[k] = r["end"]
        variant[k] = r["arm"]
        p = r["p_S"]
        for a in ALPHAS:
            if k not in first[a] and p is not None and p <= a + 1e-12:
                first[a][k] = r["end"]

out = {}


def hit_of(key, alarm_end, blk):
    if alarm_end is None:
        return "silent"
    lo, hi = blk["e_view"], min(blk["x"] + 16, blk["last_end"])
    if alarm_end < lo:
        return "pre_window"
    if alarm_end > hi:
        return "late"
    return "hit"


for a in ALPHAS:
    rows = []
    for k, blk in pe.items():
        ae = first[a].get(k)
        rows.append({"key": k, "alarm": ae, "verdict": hit_of(k, ae, blk),
                     "latency": None if ae is None else ae - blk["x"],
                     "x_beyond_h": blk["x_beyond_h"]})
    hits = [r for r in rows if r["verdict"] == "hit"]
    lat = [r["latency"] for r in hits]
    ver = {}
    for r in rows:
        ver[r["verdict"]] = ver.get(r["verdict"], 0) + 1
    # false alarms on the normal union
    normal_keys = [k for k, v in anchors.items() if v["variant"] in
                   ("clean", "benign_control", "benign_lexical")]
    fa_all = [k for k in normal_keys if k in first[a]]
    out[f"alpha_{a}"] = {
        "hits": len(hits),
        "verdicts": ver,
        "latency_quantiles": {str(x): float(np.percentile(lat, x)) for x in (0, 10, 25, 50, 75, 90, 100)} if lat else None,
        "share_within_plus_8": (sum(1 for v in lat if v <= 8) / len(lat)) if lat else None,
        "count_within_plus_8": sum(1 for v in lat if v <= 8),
        "count_early": sum(1 for v in lat if v < 0),
        "normal_union_alarms": len(fa_all),
        "normal_union_n": len(normal_keys),
    }
    out[f"alpha_{a}"]["late_rows"] = sorted(
        [r["key"] for r in rows if r["verdict"] == "late"]
    )
    out[f"alpha_{a}"]["latency_gt8"] = sorted(
        [(r["key"], r["latency"]) for r in hits if r["latency"] > 8], key=lambda t: -t[1]
    )

# what limits latency: distribution of (first alarm - E_view) and of the alarm channel
out["miss_anatomy_alpha_0_10"] = {}
rows = []
for k, blk in pe.items():
    ae = first[0.10].get(k)
    rows.append({"key": k, "verdict": hit_of(k, ae, blk), "x_beyond_h": blk["x_beyond_h"],
                 "alarm": ae, "x": blk["x"], "e_view": blk["e_view"], "last_end": blk["last_end"],
                 "silent_label": blk["silent"], "traj": blk["trajectory_class"]})
for v in ("hit", "silent", "late", "pre_window"):
    sub = [r for r in rows if r["verdict"] == v]
    out["miss_anatomy_alpha_0_10"][v] = {
        "n": len(sub),
        "x_beyond_h": sum(1 for r in sub if r["x_beyond_h"]),
        "keys": sorted(r["key"] for r in sub)[:40],
    }

# --------------------------------------------------------------------- (d) session
first_any = {}
with (DESC / "desc_V1_channel_dump/outputs.jsonl").open() as f:
    for line in f:
        r = json.loads(line)
        if r.get("horizon_censored"):
            continue
        k = r["key"]
        p = r["p_S"]
        if k not in first_any and p is not None and p <= 0.025 + 1e-12:
            first_any[k] = r["end"]

sess = S["metrics"]["session"]["per_session"]
NORMALS = ("clean", "benign_control", "benign_lexical")
two = {k: v for k, v in sess.items() if v["variant"] in NORMALS and v["n_turns_run"] == 2}
one = {k: v for k, v in sess.items() if v["variant"] in NORMALS and v["n_turns_run"] == 1}
out["session_prefix"] = {"gate_f7": S["metrics"]["session"]["gate_f7"]}
for a in (0.025, 0.10):
    src = first[0.10] if a == 0.10 else None
    def alarmed(key):
        if a == 0.10:
            return key in first[0.10]
        e = first_any.get(key)
        return e is not None
    by_index = {0: [0, 0], 1: [0, 0]}
    both = 0
    sessions_with_alarm = 0
    for sid, blk in two.items():
        flags = []
        for i in (0, 1):
            key = f"g_dev|{sid}#ep{i}"
            if key in anchors:
                by_index[i][1] += 1
                fl = alarmed(key)
                if fl:
                    by_index[i][0] += 1
                flags.append(fl)
        if any(flags):
            sessions_with_alarm += 1
        if len(flags) == 2 and all(flags):
            both += 1
    one_alarm = sum(1 for sid in one if alarmed(f"g_dev|{sid}#ep0"))
    out["session_prefix"][f"alpha_{a}"] = {
        "normal_sessions_total": len(two) + len(one),
        "two_turn_normal_sessions": len(two),
        "one_turn_normal_sessions": len(one),
        "ep0_alarms_over_two_turn": by_index[0],
        "ep1_alarms_over_two_turn": by_index[1],
        "two_turn_sessions_with_any_alarm": sessions_with_alarm,
        "two_turn_sessions_with_both_alarms": both,
        "one_turn_sessions_with_alarm": one_alarm,
        "session_far_two_turn": sessions_with_alarm / len(two) if two else None,
        "session_far_one_turn": one_alarm / len(one) if one else None,
    }
(SP / "zoom_c.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1)[:8000])
