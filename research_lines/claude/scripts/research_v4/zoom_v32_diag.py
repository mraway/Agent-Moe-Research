"""Diagnostics behind the v3.2 fusion views (EXPLORATORY, G-dev only).

Onset-look anatomy, S/M/P discordance on the X-anchored positives, the attainable
conformal level grid per fold, and a two-dimensional alpha sweep of the OR arm.
All post hoc from the stored per-look p of the FROZEN cells.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

import numpy as np

from zoom_v32_fusion import (  # type: ignore
    COMPACT,
    HORIZON,
    NORMAL_VARIANTS,
    Looks,
    alarms_for,
    and_same_look,
    debounce,
    far_block,
    hit_block,
    load_meta,
    mcnemar_exact,
    or_fuse,
    single,
)

OUT = "artifacts/agent_v2/dataset_g/v3_2_zoom_fusion/fusion_diag.json"


def main() -> int:
    looks = Looks(COMPACT)
    bundle = load_meta()
    meta, per_episode, res = bundle["meta"], bundle["per_episode"], bundle["result"]
    payload: dict = {"note": "EXPLORATORY, G-dev only"}

    # ---- 0. is the registered p a running maximum (monotone non-increasing)? ----
    viol = 0
    total = 0
    for key, grid in looks.grid.items():
        for stat, g in grid.items():
            total += 1
            if np.any(np.diff(g["p"]) > 1e-12):
                viol += 1
    payload["p_monotone"] = {"episode_cells": total, "non_monotone": viol}

    # ---- 1. attainable conformal levels per fold ----
    manifest = json.load(
        open(
            "artifacts/agent_v2/dataset_g/v3_2_a2_verify/stage1/threshold_manifest.json",
            encoding="utf-8",
        )
    )
    grid_rows = {}
    for fold in ("0", "1", "2"):
        cell = manifest["folds"][fold]["cells"]["S"]
        n_cal = int(cell["n_cal"])
        row = {"n_cal": n_cal, "alpha_eff_registered": cell["alpha_eff"]}
        for alpha in (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.10, 0.15, 0.20):
            rank = int(np.floor(alpha * (n_cal + 1)))
            row[f"alpha={alpha}"] = {
                "rank": rank,
                "alpha_eff": rank / (n_cal + 1),
                "attainable": rank >= 1,
            }
        grid_rows[fold] = row
    payload["attainable_levels"] = grid_rows

    normals_all = [k for k, m in meta.items() if m["variant"] in NORMAL_VARIANTS]
    normals_filtered = [k for k in normals_all if meta[k]["filter_pass"] is not False]
    reachable = [k for k, b in per_episode.items() if b["reachable_plus_16"]]

    # ---- 2. onset anatomy: where do false alarms and hits start, in LOOK index ----
    def onset_look(key: str, ends: np.ndarray) -> int | None:
        if not ends.size:
            return None
        g = looks.grid[key]["S"]
        pos = int(np.searchsorted(g["end"], int(ends[0])))
        return int(g["look"][min(pos, len(g["look"]) - 1)])

    anat = {}
    for stat in ("S", "M", "P"):
        alarms = alarms_for(looks, single(stat, 0.10))
        fa = [onset_look(k, alarms[k]) for k in normals_filtered if alarms[k].size]
        fa_all = [onset_look(k, alarms[k]) for k in normals_all if alarms[k].size]
        hits = []
        for k in reachable:
            b = per_episode[k]
            e = alarms[k]
            if e.size and int(b["e_view"]) <= int(e[0]) <= int(b["x"]) + HORIZON:
                hits.append(onset_look(k, e))
        anat[stat] = {
            "false_alarm_onset_look_filtered": summary(fa),
            "false_alarm_onset_look_all": summary(fa_all),
            "hit_onset_look": summary(hits),
        }
    payload["onset_anatomy"] = anat

    # ---- 3. discordance on the X-anchored positives ----
    hits_by = {}
    for name, rule in (
        ("S", single("S", 0.10)),
        ("M", single("M", 0.10)),
        ("P", single("P", 0.10)),
        ("OR(S.05,M.05)", or_fuse([("S", 0.05), ("M", 0.05)])),
        ("OR(S.03,M.07)", or_fuse([("S", 0.03), ("M", 0.07)])),
        ("OR(S.05,P.05)", or_fuse([("S", 0.05), ("P", 0.05)])),
        ("AND(S.10,M.10)", and_same_look([("S", 0.10), ("M", 0.10)])),
        ("S-debounce2", debounce("S", 0.10, 2)),
    ):
        alarms = alarms_for(looks, rule)
        hb = hit_block(alarms, per_episode, meta)
        hits_by[name] = set(hb["hit_keys"])

    def strata(keys) -> dict:
        out: dict[str, Counter] = defaultdict(Counter)
        for k in keys:
            b = per_episode[k]
            out["family"][b["attack_family_id"] or "none"] += 1
            out["domain_group"][b["domain_group"]] += 1
            out["injection_channel"][b["injection_channel"]] += 1
            out["wording_tier"][b["wording_tier"]] += 1
            out["trajectory_class"][b["trajectory_class"]] += 1
            out["x_beyond_h"][str(bool(b["x_beyond_h"]))] += 1
        return {k: dict(v) for k, v in out.items()}

    only_m = sorted(hits_by["M"] - hits_by["S"])
    only_s = sorted(hits_by["S"] - hits_by["M"])
    missed_both = sorted(set(reachable) - hits_by["S"] - hits_by["M"])
    payload["discordance_S_vs_M"] = {
        "mcnemar": mcnemar_exact(
            [k in hits_by["S"] for k in reachable], [k in hits_by["M"] for k in reachable]
        ),
        "only_M_keys": only_m,
        "only_S_keys": only_s,
        "only_M_strata": strata(only_m),
        "only_S_strata": strata(only_s),
        "missed_by_both": len(missed_both),
        "missed_by_both_strata": strata(missed_both),
        "union_S_or_M_at_0.10": len(hits_by["S"] | hits_by["M"]),
    }
    payload["discordance_S_vs_P"] = {
        "only_P_keys": sorted(hits_by["P"] - hits_by["S"]),
        "only_P_strata": strata(sorted(hits_by["P"] - hits_by["S"])),
        "union_S_or_P_at_0.10": len(hits_by["S"] | hits_by["P"]),
    }
    payload["fused_vs_S_hitsets"] = {
        name: {
            "n": len(keys),
            "gained_over_S": sorted(keys - hits_by["S"]),
            "lost_vs_S": sorted(hits_by["S"] - keys),
        }
        for name, keys in hits_by.items()
    }

    # ---- 4. two-dimensional alpha sweep of the OR arm ----
    sweep = []
    grid = (0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10)
    for a_s in grid:
        for a_m in grid:
            if abs(a_s + a_m - 0.10) > 1e-9:
                continue
            for partner in ("M", "P"):
                alarms = alarms_for(looks, or_fuse([("S", a_s), (partner, a_m)]))
                hb = hit_block(alarms, per_episode, meta)
                sweep.append(
                    {
                        "partner": partner,
                        "alpha_S": a_s,
                        "alpha_partner": a_m,
                        "hits": hb["hits"],
                        "recall": hb["recall"],
                        "far_filtered": far_block(alarms, meta, normals_filtered)["far"],
                        "far_all": far_block(alarms, meta, normals_all)["far"],
                    }
                )
    payload["or_sweep_budget_0.10"] = sweep

    json.dump(payload, open(OUT, "w", encoding="utf-8"), indent=1, default=str)
    print(json.dumps({k: v for k, v in payload.items() if k in ("p_monotone",)}))
    print("onset anatomy:", json.dumps(payload["onset_anatomy"], indent=1))
    print("discordance:", json.dumps(
        {k: v for k, v in payload["discordance_S_vs_M"].items() if not k.endswith("_keys")},
        indent=1))
    print("only_P strata:", json.dumps(payload["discordance_S_vs_P"]["only_P_strata"]))
    print("union S|P:", payload["discordance_S_vs_P"]["union_S_or_P_at_0.10"])
    print("sweep:")
    for row in sweep:
        print(f"  OR(S@{row['alpha_S']:.2f},{row['partner']}@{row['alpha_partner']:.2f}) "
              f"hits={row['hits']:3d} recall={row['recall']:.3f} "
              f"far_filt={row['far_filtered']:.5f} far_all={row['far_all']:.5f}")
    print("wrote", OUT)
    return 0


def summary(vals) -> dict:
    arr = np.asarray([v for v in vals if v is not None], dtype=float)
    if not arr.size:
        return {"n": 0}
    return {
        "n": int(arr.size),
        "min": float(arr.min()),
        "p25": float(np.percentile(arr, 25)),
        "median": float(np.median(arr)),
        "p75": float(np.percentile(arr, 75)),
        "max": float(arr.max()),
        "le_64": int((arr <= 64).sum()),
        "gt_64": int((arr > 64).sum()),
    }


if __name__ == "__main__":
    raise SystemExit(main())
