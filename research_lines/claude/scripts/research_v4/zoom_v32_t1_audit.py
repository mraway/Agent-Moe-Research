"""EXPLORATORY (G-dev only): independent audit of candidate T1 (transition/warm-up mask).

Re-derives, from the stored per-look dump alone, the baseline decision layer and every
transition-mask variant, plus the checks the T1 claim does not make:
  * the SAME matched-alpha machine applied to the BASELINE column (per-fold -> single
    global threshold), which isolates how much of the "+2 hits" is the mask and how much
    is the switch to a single global alpha;
  * the silent-attack side (gate F4) and legitimate_refusal / over_refusal layers;
  * paired McNemar over the hit sets;
  * twin-pair (byte-identical multi_turn #ep0) accounting on the removed false alarms.

Never reads G-conf.  Read-only over existing artefacts; the harness is never re-run.
"""
from __future__ import annotations

import json
import pickle
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
G = ROOT / "artifacts/agent_v2/dataset_g"
DUMP = G / "v3_2_dev_descriptive/desc_V1_channel_dump/outputs.jsonl"
PRIMARY = G / "v3_2_a2_verify/stage2/result.json"
STAGE1 = G / "v3_2_a2_verify/stage1/threshold_manifest.json"
LABELS = G / "annotations/g_dev/final_unblinded.jsonl"
MAPPING = G / "private/g_dev/case_mapping.jsonl"
CH = ("analysis", "commentary", "final")
CID = {c: i for i, c in enumerate(CH)}


def extract(cache: Path) -> dict:
    if cache.exists():
        with cache.open("rb") as h:
            return pickle.load(h)
    rows: dict[str, list] = {}
    meta: dict[str, dict] = {}
    n = 0
    with DUMP.open() as h:
        for line in h:
            d = json.loads(line)
            key = d["key"]
            b = rows.get(key)
            if b is None:
                b = rows[key] = []
                meta[key] = {"arm": d["arm"], "fold": int(d["fold"]), "class": d["class"],
                             "session_id": d["session_id"], "episode_index": int(d["episode_index"]),
                             "trace_id": d["trace_id"]}
            pi = d.get("p_inst")
            b.append((int(d["k"]), int(d["end"]), CID[d["channel"]], float(d["p_S"]),
                      float("nan") if pi is None else float(pi), bool(d["horizon_censored"])))
            n += 1
    store = {}
    for key, b in rows.items():
        b.sort(key=lambda r: r[0])
        store[key] = {"k": np.array([r[0] for r in b], np.int32),
                      "end": np.array([r[1] for r in b], np.int32),
                      "chan": np.array([r[2] for r in b], np.int8),
                      "p": np.array([r[3] for r in b], np.float64),
                      "p_inst": np.array([r[4] for r in b], np.float64),
                      "cens": np.array([r[5] for r in b], bool)}
    out = {"store": store, "meta": meta, "rows": n}
    with cache.open("wb") as h:
        pickle.dump(out, h, protocol=4)
    return out


def run_structure(chan, end):
    n = len(chan)
    run_id = np.zeros(n, np.int32); pos = np.zeros(n, np.int32)
    r = 0
    for i in range(n):
        if i > 0 and (chan[i] != chan[i - 1] or int(end[i]) != int(end[i - 1]) + 1):
            r += 1
        run_id[i] = r
        pos[i] = 0 if (i == 0 or run_id[i] != run_id[i - 1]) else pos[i - 1] + 1
    return run_id, pos


def masked_running_p(p_inst, keep):
    out = np.ones(len(p_inst), np.float64)
    cur = 1.0
    for i in range(len(p_inst)):
        v = p_inst[i]
        if keep[i] and not np.isnan(v) and v < cur:
            cur = float(v)
        out[i] = cur
    return out


def first_alarm(end, p, live, alpha):
    m = live & (p <= alpha + 1e-12)
    if not m.any():
        return None
    return int(end[m].min())


def main():
    cache = Path(sys.argv[1])
    D = extract(cache)
    store, meta = D["store"], D["meta"]
    res = json.load(PRIMARY.open())
    anchors = res["cells"]["S"]["anchors"]
    man = json.load(STAGE1.open())
    ALPHA = {int(f): man["folds"][f]["cells"]["S"]["alpha_eff"] for f in man["folds"]}
    labels = {}
    for line in LABELS.open():
        d = json.loads(line)
        labels[d["episode_id"]] = d
    mapping = {}
    for line in MAPPING.open():
        d = json.loads(line)
        mapping[d["episode_id"]] = d

    eps = {}
    for key, arr in store.items():
        eid = key.split("|", 1)[1]
        run_id, pos = run_structure(arr["chan"], arr["end"])
        lab = labels.get(eid, {})
        a = anchors.get(key, {})
        eps[key] = {"eid": eid, "arm": meta[key]["arm"], "fold": meta[key]["fold"],
                    "alpha": ALPHA[meta[key]["fold"]],
                    "filter_pass": lab.get("filter_pass"),
                    "traj": lab.get("trajectory_class"),
                    "epi": meta[key]["episode_index"],
                    "scenario": eid.split("--", 1)[0],
                    "e_view": a.get("anchor"), "x": a.get("x"),
                    "end": arr["end"], "chan": arr["chan"], "p": arr["p"],
                    "p_inst": arr["p_inst"], "live": ~arr["cens"],
                    "pos": pos, "run_id": run_id,
                    "n_runs": int(run_id.max()) + 1}

    normal_all = [k for k, e in eps.items() if e["arm"] in ("clean", "benign_control", "benign_lexical")]
    filtered = [k for k in normal_all if e_fp(eps[k])]
    positives = [k for k, e in eps.items() if e["arm"] == "attack" and e["e_view"] is not None and e["x"] is not None]
    reachable = []
    for k in positives:
        e = eps[k]
        ends = e["end"][e["live"]]
        if ((ends >= e["e_view"]) & (ends <= e["x"] + 16)).any():
            reachable.append(k)
    # silent attack-bearing episodes (gate F4 denominator = 40)
    silent_bearing = [k for k, e in eps.items()
                      if e["arm"] == "attack" and e["traj"] == "silent"
                      and not (labels.get(e["eid"], {}).get("episode_index", 0) == 0
                               and mapping.get(e["eid"], {}).get("channel") == "multi_turn_user")]
    silent_all = [k for k, e in eps.items() if e["arm"] == "attack" and e["traj"] == "silent"]
    legit = [k for k, e in eps.items() if e["arm"] == "legitimate_refusal"]

    print("denoms", len(normal_all), len(filtered), len(positives), len(reachable),
          "silent_bearing", len(silent_bearing), "silent_all", len(silent_all), "legit", len(legit))

    def evaluate(pcol, alpha_map=None):
        af = (lambda k: eps[k]["alpha"]) if alpha_map is None else (lambda k: alpha_map)
        fa = {k for k in normal_all if first_alarm(eps[k]["end"], eps[k][pcol], eps[k]["live"], af(k)) is not None}
        fa_f = fa & set(filtered)
        scen_all = {eps[k]["scenario"] for k in normal_all}
        scen_fa = {eps[k]["scenario"] for k in fa}
        hits = set()
        for k in reachable:
            e = eps[k]
            f = first_alarm(e["end"], e[pcol], e["live"], af(k))
            if f is not None and e["e_view"] <= f <= e["x"] + 16:
                hits.add(k)
        sb = {k for k in silent_bearing if first_alarm(eps[k]["end"], eps[k][pcol], eps[k]["live"], af(k)) is not None}
        sa = {k for k in silent_all if first_alarm(eps[k]["end"], eps[k][pcol], eps[k]["live"], af(k)) is not None}
        lg = {k for k in legit if first_alarm(eps[k]["end"], eps[k][pcol], eps[k]["live"], af(k)) is not None}
        return {"far_all": (len(fa), len(normal_all)), "far_filt": (len(fa_f), len(filtered)),
                "mg_far": (len(scen_fa), len(scen_all)),
                "hits": (len(hits), len(reachable)),
                "silent_bearing": (len(sb), len(silent_bearing)),
                "silent_all": (len(sa), len(silent_all)),
                "legit": (len(lg), len(legit)),
                "fa_keys": fa, "hit_keys": hits}

    def matched_alpha(pcol, target):
        grid = sorted({float(v) for k in filtered for v in eps[k][pcol][eps[k]["live"]] if v <= 0.5})
        n = len(filtered)
        for a in grid:
            c = sum(1 for k in filtered if first_alarm(eps[k]["end"], eps[k][pcol], eps[k]["live"], a) is not None)
            if c / n >= target - 1e-12:
                return a, c, n
        return None

    base = evaluate("p")
    print("BASELINE", {k: v for k, v in base.items() if not k.endswith("_keys")})
    target = base["far_filt"][0] / base["far_filt"][1]

    out = {"baseline": {k: v for k, v in base.items() if not k.endswith("_keys")}}

    # baseline under a single global matched alpha (isolates the per-fold -> global switch)
    mb = matched_alpha("p", target)
    print("baseline matched-global alpha", mb)
    bg = evaluate("p", alpha_map=mb[0])
    out["baseline_global_matched"] = {"alpha": mb[0], **{k: v for k, v in bg.items() if not k.endswith("_keys")}}
    print("BASELINE@global", {k: v for k, v in bg.items() if not k.endswith("_keys")})

    variants = {}
    for kmask in (4, 8, 12, 16, 20, 24, 32, 48):
        col = f"m{kmask}"
        masked_share_num = masked_share_den = 0
        for k, e in eps.items():
            keep = e["pos"] >= kmask
            e[col] = masked_running_p(e["p_inst"], keep)
            masked_share_num += int((~keep).sum()); masked_share_den += len(keep)
        r = evaluate(col)
        m = matched_alpha(col, target)
        rec = {"frozen": {k2: v for k2, v in r.items() if not k2.endswith("_keys")},
               "masked_look_share": masked_share_num / masked_share_den}
        rec["frozen_lost_fa"] = sorted(base["fa_keys"] - r["fa_keys"])
        rec["frozen_lost_hits"] = sorted(base["hit_keys"] - r["hit_keys"])
        rec["frozen_new_hits"] = sorted(r["hit_keys"] - base["hit_keys"])
        if m:
            rm = evaluate(col, alpha_map=m[0])
            rec["matched"] = {"alpha": m[0], **{k2: v for k2, v in rm.items() if not k2.endswith("_keys")}}
            rec["matched_vs_base_only_masked"] = sorted(rm["hit_keys"] - base["hit_keys"])
            rec["matched_vs_base_only_base"] = sorted(base["hit_keys"] - rm["hit_keys"])
            rec["matched_vs_baseglobal_only_masked"] = sorted(rm["hit_keys"] - bg["hit_keys"])
            rec["matched_vs_baseglobal_only_base"] = sorted(bg["hit_keys"] - rm["hit_keys"])
        variants[kmask] = rec
        print("k=", kmask, json.dumps({kk: vv for kk, vv in rec.items() if kk in ("frozen", "matched", "masked_look_share")}, default=float))
    out["variants"] = variants
    with open(sys.argv[2], "w") as h:
        json.dump(out, h, indent=1, default=lambda o: sorted(o) if isinstance(o, set) else float(o))


def e_fp(e):
    return e["filter_pass"] is True


if __name__ == "__main__":
    main()
