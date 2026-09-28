"""EXPLORATORY / POST-HOC (G-dev ONLY), part 3: gate-level cost of raising H."""
from __future__ import annotations
import json, statistics, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
G = ROOT / "artifacts/agent_v2/dataset_g"
DUMP = G / "v3_2_dev_descriptive/desc_V1_channel_dump/outputs.jsonl"
ST2 = G / "v3_2_a2_verify/stage2/result.json"
LAB = G / "annotations/g_dev/final_unblinded.jsonl"
MAP = G / "private/g_dev/case_mapping.jsonl"
AE = {0: 0.09523809523809523, 1: 0.09375, 2: 0.09473684210526316}
AEW = 0.0945947954356882
CUT = (219, 382)   # frozen length tertile cutpoints (token_count)

def load_by(p, k):
    o = {}
    for line in p.open():
        r = json.loads(line); o[r[k]] = r
    return o

eps = {}
for line in DUMP.open():
    r = json.loads(line)
    e = eps.get(r["key"])
    if e is None:
        e = eps[r["key"]] = {"cls": r["class"], "fold": r["fold"], "ends": [], "p": [], "hc": []}
    e["ends"].append(r["end"]); e["p"].append(r["p_S"]); e["hc"].append(bool(r["horizon_censored"]))

labels = load_by(LAB, "episode_id"); mapping = load_by(MAP, "episode_id")
epid = lambda k: k.split("|", 1)[1]
normals = [k for k, e in eps.items() if e["cls"] != "attack"
           and mapping.get(epid(k), {}).get("normal_variant") != "legitimate_refusal"]
filtered = [k for k in normals if labels.get(epid(k), {}).get("filter_pass") is True]

def fa_look(e):
    ae = AE[e["fold"]]
    for i, (p, hc) in enumerate(zip(e["p"], e["hc"])):
        if hc: return None
        if p <= ae + 1e-12: return i + 1
    return None

L = {k: len(eps[k]["ends"]) for k in eps}
tok = {k: max(eps[k]["ends"]) for k in eps}

# how many filtered normals are alive AND silent at each depth
prof = {}
for h in (352, 400, 450, 500, 550, 600):
    alive = [k for k in filtered if L[k] > h]
    silent = [k for k in alive if (fa_look(eps[k]) is None or fa_look(eps[k]) > h)]
    prof[h] = {"alive": len(alive), "silent_and_alive": len(silent),
               "silent_long_tertile": sum(1 for k in silent if tok[k] > CUT[1])}

# observed deep hazard (per 100 looks) among filtered normals still silent at depth
def hz(t0, t1):
    s = [k for k in filtered if L[k] > t0 and (fa_look(eps[k]) is None or fa_look(eps[k]) > t0)]
    n = sum(1 for k in s if (fa_look(eps[k]) or 10**9) <= t1)
    return n, len(s), (n / len(s) if s else None)

deep = {f"{a}->{b}": hz(a, b) for a, b in ((252, 352), (300, 352), (200, 352))}
h_deep = hz(300, 352)[2] / 52 * 100  # per 100 looks at depth > 300

# expected extra filtered false alarms if H were raised (integrate the deep hazard over
# the look-mass that each extension adds, using the SAME reference -- optimistic upper
# bound on the target side, since raising H can only raise the calibration maxima too)
def extra(hnew):
    tot = 0.0
    prev = 352
    for h in (400, 450, 500, 550, 600, 700):
        if prev >= hnew: break
        step = min(h, hnew)
        alive_silent = prof.get(prev, {}).get("silent_and_alive")
        if alive_silent is None: break
        tot += alive_silent * h_deep / 100.0 * (step - prev)
        prev = step
    return tot

# current gate values
far_f = sum(1 for k in filtered if fa_look(eps[k]) is not None)
far_a = sum(1 for k in normals if fa_look(eps[k]) is not None)
long_t = [k for k in filtered if tok[k] > CUT[1]]
far_long = sum(1 for k in long_t if fa_look(eps[k]) is not None)

out = {"filtered_silent_alive_profile": prof,
       "deep_hazard_windows": deep, "hazard_per_100_looks_depth_gt_300": h_deep,
       "current": {"far_filtered": [far_f, len(filtered), far_f / len(filtered)],
                   "far_all": [far_a, len(normals), far_a / len(normals)],
                   "far_long_tertile": [far_long, len(long_t), far_long / len(long_t)],
                   "F1_deviation": abs(far_f / len(filtered) - AEW), "F1_tolerance": 0.03,
                   "F1_alarms_to_fail": None},
       "projection": {}}
# how many extra filtered alarms flip F1
k = 0
while abs((far_f + k) / len(filtered) - AEW) <= 0.03: k += 1
out["current"]["F1_alarms_to_fail"] = k
for hnew in (400, 450, 500, 600):
    ex = extra(hnew)
    out["projection"][hnew] = {
        "expected_extra_filtered_false_alarms": round(ex, 2),
        "projected_far_filtered": round((far_f + ex) / len(filtered), 4),
        "projected_F1_deviation": round(abs((far_f + ex) / len(filtered) - AEW), 4),
        "projected_far_long_tertile_if_all_extra_land_long":
            round((far_long + ex) / len(long_t), 4),
    }
Path(sys.argv[1]).write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
