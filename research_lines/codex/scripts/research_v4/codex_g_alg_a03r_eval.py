"""A03-R fixed 16-cell evaluation; frozen C0 metric kernels copied, not edited upstream."""
from __future__ import annotations
from collections import defaultdict
import numpy as np
from research_v2 import trm3_g
from research_v4 import codex_g_alg_a01_eval as old
from research_v4 import codex_g_alg_a01_gate_report as gates
from research_v4 import codex_g_alg_a01_preflight as pf, codex_g_m7_math as m7

BASELINES = ("S", "CW", "TU", "U_mean", "W_mean", "U_pool", "W_pool",
             "U_diag", "W_diag", "U_block", "W_block", "U_full", "W_full")
NEW = ("W_diag_floor", "W_block_floor", "W_full_floor")
CELLS = (*BASELINES, *NEW)
LEVELS = (.1, .05, .01, .001)


def freeze_points(meta, p, counts):
    if any(r["variant"] not in pf.NORMALS for r in meta.values()):
        raise ValueError("workpoints are normal-only")
    grids = {"filtered": {}, "all": {}}
    for den in grids:
        keys = sorted(k for k, r in meta.items() if den == "all" or r["filter_pass"] is True)
        if not keys: raise ValueError("empty normal denominator")
        for j, name in enumerate(CELLS):
            alphas = sorted({0.} | {k/(n+1) for n in counts[name] for k in range(1, n+2)})
            minima = np.asarray([p[k][:, j].min() if len(p[k]) else 1. for k in keys])
            nonempty = np.asarray([bool(len(p[k])) for k in keys])
            grids[den][name] = [{"alpha": a, "measured_far": float(np.count_nonzero((minima <= a)&nonempty)/len(keys))}
                                for a in alphas]
    budget = {str(a): {n: m7.pick(grids["filtered"][n], a) for n in CELLS} for a in LEVELS}
    points = {"nominal": {str(a): {n: {"alpha": a} for n in CELLS} for a in LEVELS},
              "budget": budget, "matched": {}, "matched_all": {}}
    for a in LEVELS:
        s = budget[str(a)]["S"]
        all_target = next(r["measured_far"] for r in grids["all"]["S"] if r["alpha"] == s["alpha"])
        for mode, den, target in (("matched", "filtered", s["measured_far"]), ("matched_all", "all", all_target)):
            points[mode][str(a)] = {n: {**m7.pick(grids[den][n], target), "target_far": target,
                                      "far_gap": m7.pick(grids[den][n], target)["measured_far"]-target,
                                      "matching_denominator": den} for n in CELLS}
    return {"points": points, "grids": grids,
            "selection": "same-batch normal-only attainable grid; conditional development workpoints"}


def evaluate(meta, streams, points, counts):
    readings, ledger, comparisons = {}, {}, {}
    for mode, levels in points.items():
        readings[mode] = {}
        for level, wp in levels.items():
            block, alarms_by_cell = {}, {}
            for j, name in enumerate(CELLS):
                alarms = {k: m7.first(s["ends"], s["p"][:, j], wp[name]["alpha"]) for k, s in streams.items()}
                row = m7.summarize(meta, alarms)
                row["control_alarms"].update(old.add_normal_groups(meta, alarms))
                row["gates"] = gates.correct(meta, row, name, wp[name]["alpha"], counts[name], False)
                block[name] = {"workpoint": wp[name], **row}; alarms_by_cell[name] = alarms
            readings[mode][level] = block; ledger[f"{mode}/{level}"] = alarms_by_cell
            if mode in ("matched", "matched_all") and level in ("0.05", "0.01"):
                pairs = [("W_full_floor", "W_full"), ("W_block_floor", "W_block"), ("W_diag_floor", "W_diag"),
                         ("W_full_floor", "S"), ("W_full_floor", "W_mean"), ("W_full_floor", "W_block_floor")]
                comparisons[f"{mode}/{level}"] = {f"{a}_minus_{b}": old.paired(meta, block[a]["hits"], block[b]["hits"])
                                                 for a, b in pairs}
    normal_ci = {}
    for mode in ("matched", "matched_all"):
        normal_ci[mode] = {}
        for level in ("0.05", "0.01"):
            if level not in points[mode]: continue
            normal_ci[mode][level] = {}
            for den in ("all", "filtered"):
                keys = [k for k, r in meta.items() if r["variant"] in pf.NORMALS and (den == "all" or r["filter_pass"] is True)]
                clusters = {k: meta[k]["scenario"] for k in keys}; columns = {}
                alarms = ledger[f"{mode}/{level}"]
                for name in CELLS:
                    hits = {k: alarms[name][k] is not None for k in keys}; groups = defaultdict(list)
                    for k, hit in hits.items(): groups[clusters[k]].append(hit)
                    r = trm3_g.cluster_bootstrap_rate(groups, replicates=2000)
                    row = {"rate": {k: r[k] for k in ("n", "point_estimate", "ci", "family_count")},
                           "minus_S": trm3_g.cluster_bootstrap_paired(hits, {k: alarms["S"][k] is not None for k in keys},
                                                                     clusters, replicates=2000)}
                    if name in NEW:
                        base = name.removesuffix("_floor")
                        row["minus_unfloored"] = trm3_g.cluster_bootstrap_paired(
                            hits, {k: alarms[base][k] is not None for k in keys}, clusters, replicates=2000)
                    columns[name] = row
                normal_ci[mode][level][den] = columns
    primary = comparisons["matched_all/0.01"]["W_full_floor_minus_W_full"]
    gain = primary["gain_gate_eligible"] and primary["family"]["ci"][0] > 0 and primary["family"]["mcnemar"]["p_value"] < .05
    early = {}
    positives = sorted(k for k, r in meta.items() if r["variant"] == "attack" and r["x"] is not None)
    for level in ("0.05", "0.01"):
        early[level] = {}
        for name in CELLS:
            early[level][name] = {}
            for delta in (-64, -32, -16, -8, -1, 0, 8, 16):
                alarms = ledger[f"matched_all/{level}"][name]
                count = sum(alarms[k] is not None and meta[k]["e"] <= alarms[k] <= meta[k]["x"]+delta for k in positives)
                absent = [k for k in positives if not np.any((streams[k]["ends"] >= meta[k]["e"]) &
                                                             (streams[k]["ends"] <= meta[k]["x"]+delta))]
                early[level][name][str(delta)] = {"count": count, "n": len(positives), "no_eligible_look": absent}
    return {"readings": readings, "comparisons": comparisons, "normal_scenario_ci": normal_ci,
            "early_deadlines_matched_all_descriptive": early,
            "primary_gain_gate": {"pass": bool(gain), "comparison": "matched_all/0.01/W_full_floor_minus_W_full",
                                  "role": "adaptive G-dev spectral-floor ablation; not a detector upgrade or confirmation"},
            "audit_status": "pending"}, ledger
