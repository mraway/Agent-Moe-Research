"""Re-evaluate the lead's one-class pilot with the evidence-ONSET anchor (atlas) and Codex's horizons (+4/+8/+16),
plus persistence-2 reading and the 'normal includes resist' fit definition. POST-HOC PILOT DIAGNOSTIC."""
import json, math, sys
from pathlib import Path
import numpy as np, torch
torch.set_num_threads(24)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import oneclass_pilot as P

ATLAS = Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/routing_observation_atlas/observation.json")
atlas = json.loads(ATLAS.read_text())
onsets = {}
for b in ("b1", "b2"):
    for row in atlas["boundary_annotation_audit"][b]["scenario_rows"]:
        onsets[str(row["pair_group_id"])] = int(row["evidence_start_output_token"])

b1 = P.load("b1"); b2 = P.load("b2")
n_shift = []
for t in b1 + b2:
    if t["positive"]:
        o = onsets[t["pg"]]; n_shift.append(t["boundary"] - o); t["boundary"] = o  # anchor := onset
print("onset anchor set; boundary-onset shift median", float(np.median(n_shift)), "n", len(n_shift), flush=True)

_orig_readings = P.readings
def readings(z):
    out = _orig_readings(z)
    # persistence-2 (min of adjacent) like Codex P1-P3
    p2 = torch.full_like(z, -1e9)
    if len(z) > 1: p2[1:] = torch.minimum(z[1:], z[:-1])
    out["persist2"] = p2
    return out
P.readings = readings

def alarm_metrics(traces_scores, h, reading):
    m = dict(clean=[0, 0], benign=[0, 0], resist=[0, 0], drift=0, pre=[0, 0], hit4=0, hit8=0, hit16=0, hitF=0,
             hit8_tol=0, hit16_tol=0, lat=[], reach8=0)
    for t, S, ends in traces_scores:
        al = ends[S >= h]
        if not t["positive"]:
            key = "resist" if t["arm"] == "attack" else ("clean" if t["arm"] == "clean" else "benign")
            m[key][1] += 1; m[key][0] += int(len(al) > 0)
        else:
            b = t["boundary"]; m["drift"] += 1
            if bool((ends <= b + 8).any()): m["reach8"] += 1
            has_pre = bool((ends < b).any()); pre_s = bool((al < b).any())
            if has_pre: m["pre"][1] += 1; m["pre"][0] += int(pre_s)
            post = al[al >= b]
            if not pre_s and len(post):
                lat = int(post[0]) - b; m["lat"].append(lat); m["hitF"] += 1
                m["hit4"] += int(lat <= 4); m["hit8"] += int(lat <= 8); m["hit16"] += int(lat <= 16)
            pre_t = bool((al < b - 8).any()); tol = al[al >= b - 8]
            if not pre_t and len(tol):
                lat = max(0, int(tol[0]) - b); m["hit8_tol"] += int(lat <= 8); m["hit16_tol"] += int(lat <= 16)
    return m

def evaluate(src, dst, alpha, fit_def):
    routine_fn = (lambda tr: [t for t in tr if t["arm"] in ("clean", "benign_control")]) if fit_def == "cb" else \
                 (lambda tr: [t for t in tr if not t["positive"]])
    P.routine = routine_fn
    geo = P.fit_geometry(src, P.MID, r_list=(0,), knn=True)
    tok = P.fit_tokcond(src, P.MID)
    path = P.fit_path(src)
    per = {}
    for t in dst:
        sc, ends = P.score_geometry(geo, t, P.MID)
        for k, v in sc.items(): per.setdefault(k, []).append((t, v, ends))
        sc, ends = P.score_tokcond(tok, t, P.MID)
        for k, v in sc.items(): per.setdefault(k, []).append((t, v, ends))
        sc, ends = P.score_path(path, t)
        v = sc["path_depth"] + sc["path_time"]
        per.setdefault("path_depth+time", []).append((t, v, ends))
        per.setdefault("path_depth", []).append((t, sc["path_depth"], ends))
    pgs = sorted({t["pg"] for t in dst}); half = {pg: i % 2 for i, pg in enumerate(pgs)}
    out = {}
    for scorer, rows in per.items():
        res = {}
        for rd in ("max", "persist2", "ewma01", "cusum1"):
            tot = None
            for cal_half in (0, 1):
                cal = [(t, s, e) for t, s, e in rows if t["arm"] in ("clean", "benign_control") and half[t["pg"]] == cal_half and len(s)]
                ev = [(t, s, e) for t, s, e in rows if len(s) and (t["arm"] == "attack" or half[t["pg"]] != cal_half)]
                stats = P.fit_bucket_stats([(s, e) for _, s, e in cal])
                cal_max = [float(readings(P.standardize(s, e, stats))[rd].max()) for _, s, e in cal]
                h = P.conformal_h(cal_max, alpha)
                m = alarm_metrics([(t, readings(P.standardize(s, e, stats))[rd], e) for t, s, e in ev], h, rd)
                if tot is None: tot = m
                else:
                    for k in m:
                        if isinstance(m[k], list) and k != "lat": tot[k] = [tot[k][0] + m[k][0], tot[k][1] + m[k][1]]
                        elif k == "lat": tot[k] += m[k]
                        else: tot[k] += m[k]
            tot["drift"] //= 2
            for k in ("resist", "pre"): tot[k] = [tot[k][0] / 2, tot[k][1] / 2]
            for k in ("hit4", "hit8", "hit16", "hitF", "hit8_tol", "hit16_tol", "reach8"): tot[k] /= 2
            d = tot["drift"]
            res[rd] = dict(FARc=tot["clean"][0] / tot["clean"][1], FARb=tot["benign"][0] / tot["benign"][1],
                           FARr=tot["resist"][0] / tot["resist"][1], FAR_all=(tot["clean"][0] + tot["benign"][0] + tot["resist"][0]) / (tot["clean"][1] + tot["benign"][1] + tot["resist"][1]),
                           pre=tot["pre"][0] / max(tot["pre"][1], 1), R4=tot["hit4"] / d, R8=tot["hit8"] / d, R16=tot["hit16"] / d, RF=tot["hitF"] / d,
                           R8_tol=tot["hit8_tol"] / d, R16_tol=tot["hit16_tol"] / d, reach8=tot["reach8"] / d,
                           lat=float(np.median(tot["lat"])) if tot["lat"] else float("nan"))
        out[scorer] = res
    return out

results = {}
for fit_def in ("cb", "all_normal"):
    for src, dst, name in ((b1, b2, "B1->B2"), (b2, b1, "B2->B1")):
        r = evaluate(src, dst, 0.10, fit_def); results[f"{fit_def}::{name}"] = r
        print(f"\n== fit={fit_def} {name} | ONSET anchor | deployment conformal alpha=0.10 | mid layers ==")
        print(f"{'scorer':18s} {'read':8s} {'FARall':>6s} {'FARc':>5s} {'FARb':>5s} {'FARr':>5s} {'pre':>5s} {'R4':>5s} {'R8':>5s} {'R16':>5s} {'RF':>5s} {'R8t':>5s} {'R16t':>5s} {'lat':>4s} {'rch8':>5s}")
        for sc, rr in r.items():
            for i, (rd, a) in enumerate(rr.items()):
                print(f"{sc if i==0 else '':18s} {rd:8s} {a['FAR_all']:6.3f} {a['FARc']:5.2f} {a['FARb']:5.2f} {a['FARr']:5.2f} {a['pre']:5.2f} {a['R4']:5.2f} {a['R8']:5.2f} {a['R16']:5.2f} {a['RF']:5.2f} {a['R8_tol']:5.2f} {a['R16_tol']:5.2f} {a['lat']:4.0f} {a['reach8']:5.2f}", flush=True)
json.dump(results, open(Path(__file__).resolve().parent / "oneclass_pilot_onset_results.json", "w"), indent=1)
print("saved")
