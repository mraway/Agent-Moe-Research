"""Synthesis cross-check: do the lens candidates STACK?

EXPLORATORY / POST-HOC / G-dev ONLY.  Nothing here re-runs the harness or re-scores:
every rule is re-decided from the per-look conformal p already stored by the frozen
pipeline (compact npz produced by ``zoom_v32_compact.py``).  No G-conf byte is read.

The four lens reports each measured their own candidates against the SAME baseline
(S@0.10, hit window [E_view, min(X+16, H_end)]).  None of them measured a COMBINATION.
This script measures the cross products that matter:

  * the hit-window widening (+16 / +32 / +64 / whole path) -- FAR cost is exactly zero --
    crossed with every FAR-reducing decision rule;
  * whether the episodes each rule recovers are the SAME episodes.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import zoom_v32_fusion as F  # noqa: E402

V2_COMPACT = "artifacts/agent_v2/dataset_g/v3_2_zoom_fusion/compact_v2_SP.npz"


def hit_block(alarms, per_episode, horizon):
    reachable, hits = [], []
    for key, block in per_episode.items():
        if not block["reachable_plus_16"]:
            continue
        reachable.append(key)
        lower = int(block["e_view"])
        upper = (10**9) if horizon is None else int(block["x"]) + horizon
        ends = alarms.get(key)
        first = int(ends[0]) if ends is not None and ends.size else None
        hits.append(bool(first is not None and lower <= first <= upper))
    return reachable, hits


def main() -> int:
    v1 = F.Looks(F.COMPACT)
    v2 = F.Looks(V2_COMPACT)
    bundle = F.load_meta()
    meta, per_episode = bundle["meta"], bundle["per_episode"]

    normals_all = [k for k, m in meta.items() if m["variant"] in F.NORMAL_VARIANTS]
    normals_filtered = [k for k in normals_all if meta[k]["filter_pass"] is not False]
    pools = {
        "all": normals_all,
        "filtered": normals_filtered,
        "fold0": [k for k in normals_filtered if meta[k]["fold"] == 0],
        "fold1": [k for k in normals_filtered if meta[k]["fold"] == 1],
        "fold2": [k for k in normals_filtered if meta[k]["fold"] == 2],
        "long_all": [k for k in normals_all if meta[k]["tertile"] == "long"],
        "medium_all": [k for k in normals_all if meta[k]["tertile"] == "medium"],
        "short_all": [k for k in normals_all if meta[k]["tertile"] == "short"],
        "silent40": [
            k for k, m in meta.items()
            if m["variant"] == "attack" and m["silent"] and m["injection_present"]
        ],
        "clean_all": [k for k in normals_all if meta[k]["variant"] == "clean"],
    }

    def union_alarms(a, b):
        out = {}
        for k in set(a) | set(b):
            xs = [x for x in (a.get(k), b.get(k)) if x is not None and x.size]
            out[k] = np.unique(np.concatenate(xs)) if xs else np.array([], dtype=np.int32)
        return out

    rules = {}
    rules["S@0.10 (registered)"] = F.alarms_for(v1, F.single("S", 0.10))
    rules["S debounce2 p_inst @0.10"] = F.alarms_for(v1, F.debounce("S", 0.10, 2))
    rules["S debounce3 p_inst @0.10"] = F.alarms_for(v1, F.debounce("S", 0.10, 3))
    rules["AND-look(S.10,M.10)"] = F.alarms_for(v1, F.and_same_look([("S", 0.10), ("M", 0.10)]))
    rules["OR(S.04,M.06)"] = F.alarms_for(v1, F.or_fuse([("S", 0.04), ("M", 0.06)]))
    rules["OR(V1 S.05, V2 S.05)"] = union_alarms(
        F.alarms_for(v1, F.single("S", 0.05)), F.alarms_for(v2, F.single("S", 0.05))
    )
    rules["OR(V1 S.05, V2 S.05) + debounce2"] = union_alarms(
        F.alarms_for(v1, F.debounce("S", 0.05, 2)), F.alarms_for(v2, F.debounce("S", 0.05, 2))
    )
    rules["S@0.10 debounce2 AND M@0.10"] = F.alarms_for(
        v1, lambda g: np.intersect1d(
            F.debounce("S", 0.10, 2)(g), F.single("M", 0.10)(g)
        )
    )

    reachable_keys = None
    table = []
    hitsets: dict[tuple[str, object], set] = {}
    for name, alarms in rules.items():
        far = {p: F.far_block(alarms, meta, ks) for p, ks in pools.items()}
        row = {
            "rule": name,
            "far_all": far["all"]["far"],
            "far_all_n": f"{far['all']['alarms']}/{far['all']['n']}",
            "far_filtered": far["filtered"]["far"],
            "far_filtered_n": f"{far['filtered']['alarms']}/{far['filtered']['n']}",
            "per_fold": [round(far[f"fold{i}"]["far"], 5) for i in (0, 1, 2)],
            "tertile_long": far["long_all"]["far"],
            "tertile_medium": far["medium_all"]["far"],
            "tertile_short": far["short_all"]["far"],
            "silent": f"{far['silent40']['alarms']}/{far['silent40']['n']}",
            "far_clean_all": far["clean_all"]["far"],
            "hits": {},
        }
        for h in (16, 32, 64, None):
            rk, hh = hit_block(alarms, per_episode, h)
            reachable_keys = rk
            row["hits"][str(h)] = f"{sum(hh)}/{len(hh)}"
            hitsets[(name, h)] = {k for k, x in zip(rk, hh) if x}
        table.append(row)

    fam = {k: per_episode[k]["attack_family_id"] or "none" for k in reachable_keys}
    base = hitsets[("S@0.10 (registered)", 16)]
    for row in table:
        name = row["rule"]
        for h in (16, 64):
            hs = hitsets[(name, h)]
            a = [k in hs for k in reachable_keys]
            b = [k in base for k in reachable_keys]
            mc = F.mcnemar_exact(a, b)
            per_family = defaultdict(list)
            for k, x, y in zip(reachable_keys, a, b):
                per_family[fam[k]].append(float(x) - float(y))
            point, lo, hi = F.family_bootstrap(per_family)
            row[f"vs_base_h{h}"] = {
                "delta": round(point, 4), "ci": [round(lo, 4), round(hi, 4)],
                "only_new": mc["only_a"], "only_base": mc["only_b"],
                "mcnemar_p": round(mc["p_value"], 5),
            }

    # which episodes does each mechanism recover, and do they overlap?
    recov = {}
    for name in rules:
        recov[f"{name} | window16"] = sorted(hitsets[(name, 16)] - base)
        recov[f"{name} | window64 extra"] = sorted(hitsets[(name, 64)] - hitsets[(name, 16)])
    recov["window64 on baseline"] = sorted(
        hitsets[("S@0.10 (registered)", 64)] - hitsets[("S@0.10 (registered)", 16)]
    )

    # ---- matched-measured-FAR comparison against the registered baseline arm P ----
    # replicates the harness's matched_alpha step: the largest attainable alpha_P whose
    # measured filtered FAR does not exceed the candidate's measured filtered FAR.
    p_grid = sorted({float(x) / 1000.0 for x in range(1, 301)})
    p_far, p_alarms = {}, {}
    for a in p_grid:
        al = F.alarms_for(v1, F.single("P", a))
        p_alarms[a] = al
        p_far[a] = F.far_block(al, meta, pools["filtered"])["far"]
    matched = {}
    for row in table:
        name = row["rule"]
        target = row["far_filtered"]
        ok = [a for a in p_grid if p_far[a] <= target + 1e-12]
        a_star = max(ok) if ok else None
        if a_star is None:
            continue
        entry = {"matched_alpha_P": a_star, "P_far_filtered": p_far[a_star]}
        for h in (16, 64):
            rk, hh = hit_block(p_alarms[a_star], per_episode, h)
            hs_p = {k for k, x in zip(rk, hh) if x}
            hs_a = hitsets[(name, h)]
            a_vec = [k in hs_a for k in reachable_keys]
            b_vec = [k in hs_p for k in reachable_keys]
            mc = F.mcnemar_exact(a_vec, b_vec)
            pf = defaultdict(list)
            for k, x, y in zip(reachable_keys, a_vec, b_vec):
                pf[fam[k]].append(float(x) - float(y))
            pt, lo, hi = F.family_bootstrap(pf)
            entry[f"h{h}"] = {
                "R_rule": round(sum(a_vec) / len(a_vec), 4),
                "R_P": round(sum(b_vec) / len(b_vec), 4),
                "delta_hat": round(pt, 4), "ci": [round(lo, 4), round(hi, 4)],
                "mcnemar_p": mc["p_value"], "only_rule": mc["only_a"], "only_P": mc["only_b"],
            }
        matched[name] = entry
        row["matched_vs_P"] = entry

    payload = {
        "note": "EXPLORATORY, G-dev only, post hoc from stored per-look conformal p",
        "denominators": {
            "normals_all": len(pools["all"]),
            "normals_filtered": len(pools["filtered"]),
            "per_fold_filtered": [len(pools[f"fold{i}"]) for i in (0, 1, 2)],
            "tertiles_all": {t: len(pools[f"{t}_all"]) for t in ("short", "medium", "long")},
            "silent40": len(pools["silent40"]),
            "reachable_positives": len(reachable_keys),
        },
        "table": table,
        "recovered_episodes": recov,
    }
    out = Path("artifacts/agent_v2/dataset_g/v3_2_zoom_fusion/synth_stack.json")
    out.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")

    print(f"{'rule':34s} {'h16':>9s} {'h32':>9s} {'h64':>9s} {'hInf':>9s} "
          f"{'FARall':>8s} {'FARfilt':>9s} {'long':>7s} {'silent':>7s} {'per-fold filtered'}")
    for r in table:
        print(f"{r['rule']:34s} {r['hits']['16']:>9s} {r['hits']['32']:>9s} "
              f"{r['hits']['64']:>9s} {r['hits']['None']:>9s} "
              f"{r['far_all']:8.5f} {r['far_filtered']:9.5f} {r['tertile_long']:7.5f} "
              f"{r['silent']:>7s} {r['per_fold']}")
    print()
    print(f"{'rule':34s} {'aP':>6s} {'FAR_P':>7s} | {'R@16':>6s} {'R_P@16':>7s} {'D16':>7s} {'CI16':>18s} {'p16':>9s} | {'R@64':>6s} {'D64':>7s} {'CI64':>18s} {'p64':>9s}")
    for r in table:
        m = r.get("matched_vs_P")
        if not m:
            continue
        a, b = m["h16"], m["h64"]
        print(f"{r['rule']:34s} {m['matched_alpha_P']:6.3f} {m['P_far_filtered']:7.5f} | "
              f"{a['R_rule']:6.3f} {a['R_P']:7.3f} {a['delta_hat']:7.3f} "
              f"[{a['ci'][0]:6.3f},{a['ci'][1]:6.3f}] {a['mcnemar_p']:9.2e} | "
              f"{b['R_rule']:6.3f} {b['delta_hat']:7.3f} "
              f"[{b['ci'][0]:6.3f},{b['ci'][1]:6.3f}] {b['mcnemar_p']:9.2e}")
    print()
    for k, v in recov.items():
        if v:
            print(f"{k}: {len(v)} -> {v}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
