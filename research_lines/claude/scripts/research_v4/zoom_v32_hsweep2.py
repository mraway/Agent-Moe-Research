"""EXPLORATORY / POST-HOC (G-dev ONLY).  Lens (b) follow-up: the H = None (no horizon)
variant on BOTH statistics of the primary cell, with the F3 length-tertile split and the
matched-measured-FAR working point, so the S-vs-P delta can be read at the candidate.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io_g, trm3, trm3_g  # noqa: E402

SP = Path(sys.argv[1])
A2 = ROOT / "artifacts/agent_v2/dataset_g/v3_2_a2_verify"
DEV = ROOT / "artifacts/agent_v2/dataset_g/g_dev"
LABELS = ROOT / "artifacts/agent_v2/dataset_g/annotations/g_dev/final_unblinded.jsonl"
CACHE = ROOT / "artifacts/agent_v2/research_v4/g_routing_cache"
ALPHA = 0.10
CUTS = (219, 382)

man = json.loads((A2 / "stage1/threshold_manifest.json").read_text())
res = json.loads((A2 / "stage2/result.json").read_text())
pe = res["cells"]["S"]["metrics"]["positives_anchored"]["per_episode"]
view = trm3_g.VIEWS["V1"]
episodes = list(io_g.load_g(DEV, labels=LABELS, variants=None, scenarios=None,
                            tag_scope="message", cache_dir=CACHE, manifest={}))
fa = res["calibration_design"]["fold_assignment"]
by_fold = {0: [], 1: [], 2: []}
for e in episodes:
    by_fold[int(fa[str(e.pair_group_id)])].append(e)
tokens = {trm3.trace_key(e): int(e.top_k_ids.shape[1]) for e in episodes}
NORMALS = ("clean", "benign_control", "benign_lexical")


def tertile(t):
    return "short" if t <= CUTS[0] else ("medium" if t <= CUTS[1] else "long")


def run(stat_name, Hp):
    Z, REF = {}, {}
    for f in (0, 1, 2):
        cell = man["folds"][str(f)]["cells"][stat_name]
        st = trm3_g.build_statistic(stat_name, {})
        st.load_state(cell["statistics"][stat_name])
        cal = trm3_g.calibration_from_state(cell["calibrations"][stat_name])
        rk = set(cell["calibrations"][stat_name]["trace_ids"])
        ref_eps = [e for e in episodes if trm3.trace_key(e) in rk]
        for pool, tag in ((by_fold[f], "eval"), (ref_eps, "ref")):
            streams = trm3_g.episode_streams({stat_name: st}, pool, view)[stat_name]
            for ep, s in zip(pool, streams):
                z = np.asarray(cal.standardiser.standardize(s), dtype=np.float64)
                if tag == "eval":
                    Z[trm3.trace_key(ep)] = {"fold": f, "variant": ep.variant,
                                             "filter_pass": ep.filter_pass,
                                             "ends": np.asarray(s.ends, dtype=np.int64), "z": z}
                else:
                    REF.setdefault(f, []).append(z)
    thr = {}
    for f in (0, 1, 2):
        cut = (lambda z: z) if Hp is None else (lambda z: z[:Hp])
        thr[f] = np.sort(np.array([float(cut(z).max()) for z in REF[f] if z.size]))
    pmin = {}
    ends_of = {}
    for k, b in Z.items():
        z = b["z"] if Hp is None else b["z"][:Hp]
        ends = b["ends"] if Hp is None else b["ends"][:Hp]
        ends_of[k] = ends
        if not z.size:
            pmin[k] = None
            continue
        rm = np.maximum.accumulate(z)
        m = thr[b["fold"]]
        n = m.size
        p = (1.0 + (n - np.searchsorted(m, rm, side="left"))) / (n + 1.0)
        pmin[k] = p
    return Z, pmin, ends_of


def readout(Z, pmin, ends_of, alpha):
    first = {}
    for k, p in pmin.items():
        if p is None:
            first[k] = None
            continue
        idx = np.nonzero(p <= alpha + 1e-12)[0]
        first[k] = int(ends_of[k][idx[0]]) if idx.size else None
    norm = {k: v for k, v in Z.items() if v["variant"] in NORMALS}
    filt = {k: v for k, v in norm.items() if v["filter_pass"] is True}
    hits, reach, hitkeys = 0, 0, set()
    for k, blk in pe.items():
        ends = ends_of[k]
        if not len(ends):
            continue
        up = min(blk["x"] + 16, int(ends[-1]))
        lo = blk["e_view"]
        if not bool(((ends >= lo) & (ends <= up)).any()):
            continue
        reach += 1
        ae = first[k]
        if ae is not None and lo <= ae <= up:
            hits += 1
            hitkeys.add(k)
    ter = {}
    for t in ("short", "medium", "long"):
        for name, pool in (("filtered", filt), ("all", norm)):
            keys = [k for k in pool if tertile(tokens[k]) == t]
            ter[f"{t}_{name}"] = {"alarms": sum(1 for k in keys if first[k] is not None),
                                  "n": len(keys)}
            ter[f"{t}_{name}"]["far"] = (ter[f"{t}_{name}"]["alarms"] / len(keys)) if keys else None
    return {
        "alpha": alpha,
        "far_all": sum(1 for k in norm if first[k] is not None), "n_all": len(norm),
        "far_filtered": sum(1 for k in filt if first[k] is not None), "n_filtered": len(filt),
        "hits": hits, "reachable": reach,
        "tertile_filtered": ter,
    }, hitkeys


out = {}
cache = {}
for stat in ("S", "P"):
    for Hp in (352, None):
        Z, pmin, ends_of = run(stat, Hp)
        cache[(stat, Hp)] = (Z, pmin, ends_of)
        r, hk = readout(Z, pmin, ends_of, ALPHA)
        r["far_all_rate"] = r["far_all"] / r["n_all"]
        r["far_filtered_rate"] = r["far_filtered"] / r["n_filtered"]
        r["recall"] = r["hits"] / r["reachable"]
        out[f"{stat}_H{Hp}"] = r

# matched working point: pick alpha for P so its measured FILTERED far <= S's
def sweep_alpha(stat, Hp, target):
    Z, pmin, ends_of = cache[(stat, Hp)]
    grid = sorted({float(v) for p in pmin.values() if p is not None for v in np.unique(p)})
    best = None
    for a in grid:
        r, hk = readout(Z, pmin, ends_of, a)
        if r["far_filtered"] / r["n_filtered"] <= target + 1e-12:
            best = (a, r, hk)
    return best

for Hp in (352, None):
    s = out[f"S_H{Hp}"]
    tgt = s["far_filtered_rate"]
    a, r, hk = sweep_alpha("P", Hp, tgt)
    out[f"matched_H{Hp}"] = {
        "S_far_filtered": tgt, "P_alpha_matched": a,
        "P_far_filtered": r["far_filtered"] / r["n_filtered"],
        "P_hits": r["hits"], "P_reachable": r["reachable"],
        "S_hits": s["hits"], "S_reachable": s["reachable"],
        "delta": s["hits"] / s["reachable"] - r["hits"] / r["reachable"],
    }
(SP / "zoom_hsweep2.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
