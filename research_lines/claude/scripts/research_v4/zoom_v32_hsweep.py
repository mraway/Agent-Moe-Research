"""EXPLORATORY / POST-HOC (G-dev ONLY).  Lens (b), second variant: SYMMETRIC horizon sweep.

Keeps everything frozen except the look budget H: for each H' the calibration reference is
re-taken as the max over the first H' looks of each reference path AND the target is scored
over its first H' looks.  Because both sides are truncated at the same look budget the
exchangeability argument of the conformal p-value is untouched, so the anytime guarantee
survives; only the support (survivors_at_H) changes.  H' = None means "no horizon at all"
(full-path maxima on both sides), which is also symmetric.

Contrast with zoom_v32_beyond_h.py, which keeps the H = 352 reference and lets ONLY the
target run past H -- that one is asymmetric and breaks the guarantee.
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
HS = [352, 400, 450, 500, 550, 600, None]

man = json.loads((A2 / "stage1/threshold_manifest.json").read_text())
res = json.loads((A2 / "stage2/result.json").read_text())
S = res["cells"]["S"]
pe = S["metrics"]["positives_anchored"]["per_episode"]
view = trm3_g.VIEWS["V1"]

episodes = list(io_g.load_g(DEV, labels=LABELS, variants=None, scenarios=None,
                            tag_scope="message", cache_dir=CACHE, manifest={}))
fa = res["calibration_design"]["fold_assignment"]
by_fold: dict[int, list] = {0: [], 1: [], 2: []}
for e in episodes:
    by_fold[int(fa[str(e.pair_group_id)])].append(e)

Z: dict[str, dict] = {}          # key -> {fold, variant, filter_pass, ends, z}
REF: dict[int, list] = {}        # fold -> [z arrays of its reference paths]
check = {}
for f in (0, 1, 2):
    cell = man["folds"][str(f)]["cells"]["S"]
    stat = trm3_g.build_statistic("S", {})
    stat.load_state(cell["statistics"]["S"])
    cal = trm3_g.calibration_from_state(cell["calibrations"]["S"])
    ref_keys = set(cell["calibrations"]["S"]["trace_ids"])
    ref_eps = [e for e in episodes if trm3.trace_key(e) in ref_keys]
    assert len(ref_eps) == len(ref_keys), (f, len(ref_eps), len(ref_keys))
    for pool, tag in ((by_fold[f], "eval"), (ref_eps, "ref")):
        streams = trm3_g.episode_streams({"S": stat}, pool, view)["S"]
        for ep, st in zip(pool, streams):
            z = np.asarray(cal.standardiser.standardize(st), dtype=np.float64)
            k = trm3.trace_key(ep)
            if tag == "eval":
                Z[k] = {"fold": f, "variant": ep.variant, "filter_pass": ep.filter_pass,
                        "ends": np.asarray(st.ends, dtype=np.int64), "z": z}
            else:
                REF.setdefault(f, []).append(z)
    m = np.sort([float(z[:352].max()) for z in REF[f] if z.size])
    check[f] = {"n": int(m.size), "min": float(m.min()), "median": float(np.median(m)),
                "max": float(m.max()),
                "manifest": {kk: cell["reference_path_maxima"][kk]
                             for kk in ("count", "min", "median", "max")}}

NORMALS = ("clean", "benign_control", "benign_lexical")
out = {"reference_reproduction_at_352": check, "sweep": {}}

for Hp in HS:
    thr = {}
    surv = {}
    for f in (0, 1, 2):
        cut = (lambda z: z) if Hp is None else (lambda z: z[:Hp])
        m = np.sort(np.array([float(cut(z).max()) for z in REF[f] if z.size]))
        thr[f] = m
        surv[f] = int(sum(1 for z in REF[f] if Hp is None or z.size >= Hp))
    def pvalue(f, r):
        m = thr[f]
        n = m.size
        ge = n - int(np.searchsorted(m, r, side="left"))
        return (1.0 + ge) / (n + 1.0)
    first = {}
    for k, b in Z.items():
        z = b["z"] if Hp is None else b["z"][:Hp]
        ends = b["ends"] if Hp is None else b["ends"][:Hp]
        if not z.size:
            first[k] = None
            continue
        rm = np.maximum.accumulate(z)
        p = np.array([pvalue(b["fold"], float(v)) for v in rm])
        idx = np.nonzero(p <= ALPHA + 1e-12)[0]
        first[k] = int(ends[idx[0]]) if idx.size else None
    norm = {k: v for k, v in Z.items() if v["variant"] in NORMALS}
    filt = {k: v for k, v in norm.items() if v["filter_pass"] is True}
    hits = 0
    beyond_hits = 0
    beyond_n = 0
    reach = 0
    for k, blk in pe.items():
        ends = Z[k]["ends"] if Hp is None else Z[k]["ends"][:Hp]
        if not len(ends):
            continue
        last = int(ends[-1])
        up = min(blk["x"] + 16, last)
        lo = blk["e_view"]
        reachable = bool(((ends >= lo) & (ends <= up)).any())
        beyond = last < blk["x"]
        if beyond:
            beyond_n += 1
        if not reachable:
            continue
        reach += 1
        ae = first[k]
        ok = ae is not None and lo <= ae <= up
        hits += int(ok)
        if beyond:
            beyond_hits += int(ok)
    out["sweep"][str(Hp)] = {
        "H": Hp,
        "survivors_at_H_per_fold": surv,
        "alpha_eff_per_fold": {f: float(np.floor((thr[f].size + 1) * ALPHA) / (thr[f].size + 1))
                               for f in (0, 1, 2)},
        "threshold_z_per_fold": {f: float(np.sort(thr[f])[max(0, thr[f].size - int(np.floor((thr[f].size + 1) * ALPHA)))]) for f in (0, 1, 2)},
        "far_all": {"alarms": sum(1 for k in norm if first[k] is not None), "n": len(norm)},
        "far_filtered": {"alarms": sum(1 for k in filt if first[k] is not None), "n": len(filt)},
        "hits": hits, "reachable": reach, "positives": len(pe),
        "x_beyond_h_count": beyond_n, "x_beyond_h_hits": beyond_hits,
    }
    b = out["sweep"][str(Hp)]
    b["far_all_rate"] = b["far_all"]["alarms"] / b["far_all"]["n"]
    b["far_filtered_rate"] = b["far_filtered"]["alarms"] / b["far_filtered"]["n"]
    b["recall"] = hits / reach if reach else None

(SP / "zoom_hsweep.json").write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
