"""EXPLORATORY / POST-HOC (G-dev ONLY).  Lens (b): the "frozen reference past H" extension.

The frozen harness stops updating p at look H = 352 and freezes it (trm3.online, prereg
v1.2 amendment 4).  The candidate change instead KEEPS the H-th-look calibration reference
and lets the target's running max keep growing past H.  Because
    p(k) = min_{j<=k} p_inst(j)
this is exactly "alarm iff any look of the WHOLE path has z >= the frozen alpha threshold".

Nothing is refitted: the statistic state, the channel standardiser and the reference maxima
are all restored from the frozen stage-1 threshold_manifest.json, so the only new number is
the per-look z of the looks the frozen run threw away.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from research_v2 import io_g, trm3, trm3_g  # noqa: E402

SP = Path(sys.argv[1])
A2 = ROOT / "artifacts/agent_v2/dataset_g/v3_2_a2_verify"
DEV = ROOT / "artifacts/agent_v2/dataset_g/g_dev"
LABELS = ROOT / "artifacts/agent_v2/dataset_g/annotations/g_dev/final_unblinded.jsonl"
CACHE = ROOT / "artifacts/agent_v2/research_v4/g_routing_cache"
ALPHA = 0.10
H = 352

man = json.loads((A2 / "stage1/threshold_manifest.json").read_text())
res = json.loads((A2 / "stage2/result.json").read_text())
S = res["cells"]["S"]
pe = S["metrics"]["positives_anchored"]["per_episode"]
view = trm3_g.VIEWS["V1"]

episodes = list(
    io_g.load_g(DEV, labels=LABELS, variants=None, scenarios=None,
                tag_scope="message", cache_dir=CACHE, manifest={})
)
fa = res["calibration_design"]["fold_assignment"]
by_fold: dict[int, list] = {0: [], 1: [], 2: []}
for e in episodes:
    by_fold[int(fa[str(e.pair_group_id)])].append(e)

rows = {}
for f in (0, 1, 2):
    cell = man["folds"][str(f)]["cells"]["S"]
    stat = trm3_g.build_statistic("S", {})
    stat.load_state(cell["statistics"]["S"])
    cal = trm3_g.calibration_from_state(cell["calibrations"]["S"])
    ref = cal.reference.channels["S"]
    streams = trm3_g.episode_streams({"S": stat}, by_fold[f], view)["S"]
    for ep, st in zip(by_fold[f], streams):
        z = cal.standardiser.standardize(st)
        ends = np.asarray(st.ends, dtype=np.int64)
        if not ends.size:
            continue
        rmax = np.maximum.accumulate(np.asarray(z, dtype=np.float64))
        p = np.array([ref.p_value(float(v)) for v in rmax])
        alarm = np.nonzero(p <= ALPHA + 1e-12)[0]
        first_any = int(alarm[0]) if alarm.size else None
        in_h = [i for i in alarm.tolist() if i < H]
        rows[trm3.trace_key(ep)] = {
            "fold": f,
            "variant": ep.variant,
            "filter_pass": ep.filter_pass,
            "looks": int(ends.size),
            "first_in_h_idx": in_h[0] if in_h else None,
            "first_in_h_end": int(ends[in_h[0]]) if in_h else None,
            "first_any_idx": first_any,
            "first_any_end": None if first_any is None else int(ends[first_any]),
            "censored": bool(ends.size > H),
        }

out = {"n_episodes": len(rows)}

# --- reproduction check against the frozen run -------------------------------------
NORMALS = ("clean", "benign_control", "benign_lexical")
norm = {k: v for k, v in rows.items() if v["variant"] in NORMALS}
filt = {k: v for k, v in norm.items() if v["filter_pass"] is True}
def far(d, field):
    n = len(d)
    a = sum(1 for v in d.values() if v[field] is not None)
    return {"alarms": a, "n": n, "far": a / n if n else None}
out["frozen_reproduction"] = {"all": far(norm, "first_in_h_end"),
                              "filtered": far(filt, "first_in_h_end")}
out["extension"] = {"all": far(norm, "first_any_end"), "filtered": far(filt, "first_any_end")}
out["censored_normals"] = {
    "all": sum(1 for v in norm.values() if v["censored"]),
    "filtered": sum(1 for v in filt.values() if v["censored"]),
    "all_n": len(norm), "filtered_n": len(filt),
}
out["new_normal_alarms"] = sorted(
    k for k, v in norm.items() if v["first_in_h_end"] is None and v["first_any_end"] is not None
)
out["new_normal_alarm_detail"] = [
    {"key": k, "fold": norm[k]["fold"], "variant": norm[k]["variant"],
     "filter_pass": norm[k]["filter_pass"], "looks": norm[k]["looks"],
     "first_any_idx": norm[k]["first_any_idx"], "first_any_end": norm[k]["first_any_end"]}
    for k in out["new_normal_alarms"]
]

# --- positives ---------------------------------------------------------------------
def verdict(blk, end, upper):
    if end is None:
        return "silent"
    if end < blk["e_view"]:
        return "pre_window"
    if end > upper:
        return "late"
    return "hit"

pos = []
for k, blk in pe.items():
    r = rows[k]
    # frozen: window upper bound uses the H-truncated last endpoint
    up_frozen = min(blk["x"] + 16, blk["last_end"])
    grid_last = None
    up_ext = blk["x"] + 16   # under the extension the whole path is in play
    pos.append({
        "key": k, "x_beyond_h": blk["x_beyond_h"],
        "frozen": verdict(blk, r["first_in_h_end"], up_frozen),
        "ext": verdict(blk, r["first_any_end"], up_ext),
        "frozen_end": r["first_in_h_end"], "ext_end": r["first_any_end"],
        "x": blk["x"], "e_view": blk["e_view"], "looks": r["looks"],
    })
def tally(field):
    t = {}
    for r in pos:
        t[r[field]] = t.get(r[field], 0) + 1
    return t
out["positives"] = {
    "n": len(pos),
    "frozen_verdicts": tally("frozen"),
    "ext_verdicts": tally("ext"),
    "changed": [r for r in pos if r["frozen"] != r["ext"]],
    "beyond_h_stratum": {
        "frozen": {v: sum(1 for r in pos if r["x_beyond_h"] and r["frozen"] == v)
                   for v in ("hit", "silent", "late", "pre_window")},
        "ext": {v: sum(1 for r in pos if r["x_beyond_h"] and r["ext"] == v)
                for v in ("hit", "silent", "late", "pre_window")},
    },
}
(SP / "zoom_b.json").write_text(json.dumps(out, indent=1))
print(json.dumps({k: v for k, v in out.items() if k != "new_normal_alarm_detail"}, indent=1)[:6000])
