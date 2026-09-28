"""A03 fixed 13-cell evaluation. Adapted from immutable A02; shared metric kernels unchanged."""
from __future__ import annotations
from collections import defaultdict
import numpy as np
from research_v2 import trm3_g
from research_v4 import codex_g_alg_a01_eval as old
from research_v4 import codex_g_alg_a01_gate_report as gates
from research_v4 import codex_g_alg_a01_preflight as pf, codex_g_m7_math as m7

BASELINES = ("S", "CW", "TU", "U_mean", "W_mean", "U_pool", "W_pool")
NEW = ("U_diag", "W_diag", "U_block", "W_block", "U_full", "W_full")
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
            if mode in ("matched", "matched_all"):
                pairs = [("W_full", "W_diag")]
                if level == "0.05":
                    pairs += [("W_full", "W_block"), ("W_block", "W_diag"), ("U_full", "U_diag"),
                              ("U_full", "U_block"), ("U_block", "U_diag"), ("W_full", "U_full"),
                              ("W_block", "U_block"), ("W_diag", "U_diag"), ("W_full", "S"),
                              ("W_full", "W_mean"), ("W_full", "W_pool"), ("W_full", "CW"),
                              ("W_full", "TU"), ("U_full", "S")]
                comparisons[f"{mode}/{level}"] = {f"{a}_minus_{b}": old.paired(meta, block[a]["hits"], block[b]["hits"])
                                                     for a, b in pairs}
    normal_ci = {}
    for mode in ("matched", "matched_all"):
        normal_ci[mode] = {}
        for den in ("all", "filtered"):
            keys = [k for k, r in meta.items() if r["variant"] in pf.NORMALS and (den == "all" or r["filter_pass"] is True)]
            clusters = {k: meta[k]["scenario"] for k in keys}; columns = {}
            alarms = ledger[f"{mode}/0.05"]
            for name in CELLS:
                hits = {k: alarms[name][k] is not None for k in keys}; groups = defaultdict(list)
                for k, hit in hits.items(): groups[clusters[k]].append(hit)
                r = trm3_g.cluster_bootstrap_rate(groups, replicates=2000)
                columns[name] = {"rate": {k: r[k] for k in ("n", "point_estimate", "ci", "family_count")},
                                 "minus_S": trm3_g.cluster_bootstrap_paired(hits, {k: alarms["S"][k] is not None for k in keys},
                                                                           clusters, replicates=2000)}
            normal_ci[mode][den] = columns
    primary = comparisons["matched_all/0.05"]["W_full_minus_W_diag"]
    gain = primary["gain_gate_eligible"] and primary["family"]["ci"][0] > 0 and primary["family"]["mcnemar"]["p_value"] < .05
    return {"readings": readings, "comparisons": comparisons, "normal_scenario_ci_at_5pct": normal_ci,
            "primary_gain_gate": {"pass": bool(gain), "comparison": "matched_all/0.05/W_full_minus_W_diag",
                                  "role": "adaptive G-dev development, not confirmation; not a detector upgrade by itself"},
            "audit_status": "pending: risk-gate audit flags are not yet PASS"}, ledger

