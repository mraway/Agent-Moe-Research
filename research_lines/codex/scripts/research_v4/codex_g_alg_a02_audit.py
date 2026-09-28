"""Independent A02 coordinate, scenario, rank, first-alarm and paired inference audit."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from math import comb
import time
import numpy as np
import torch
from research_v2 import trm3_g
from research_v4 import codex_g_alg_a01_audit as old, codex_g_alg_a01_eval as ae
from research_v4 import codex_g_alg_a01_io as ai, codex_g_alg_a01_preflight as pf
from research_v4 import codex_g_alg_a01_gate_report as gates
from research_v4 import codex_g_alg_a02_math as math, codex_g_alg_a02_eval as ev
from research_v4 import codex_g_alg_a02_run as run


def literal_z(s, column, state):
    out = np.empty(len(s["ends"]))
    st = state["standardiser"]
    for i, (tag, ordinal) in enumerate(zip(s["tags"], s["ordinals"])):
        b = st["stats"].get(str(tag))
        if b is None: b = st["pooled"]; ordinal = i
        bucket = min(int(ordinal)//b["bucket_size"], b["cap"])
        out[i] = (s["raw"][i, column]-b["mu"][bucket])/b["sd"][bucket]
    return out


def audit_bank(bank, info, fold, tag, meta, grid, checked, inventory):
    keys = sorted(k for k, r in meta.items() if r["fold"] == (fold+1)%3 and r["variant"] in pf.NORMALS
                  and r["filter_pass"] is True and tag in grid[k]["tags"])
    if keys != sorted(info["fit_keys"]): raise AssertionError("bank role/round coverage mismatch")
    if tuple(sorted({meta[k]["scenario"] for k in keys})) != bank.scenarios: raise AssertionError("bank scenarios mismatch")
    error, cursor = 0., 0
    for k in sorted(keys, key=lambda k: (meta[k]["scenario"], k)):
        ix = np.flatnonzero(bank.keys == k); ends = bank.ends[ix]
        np.testing.assert_array_equal(ix, np.arange(cursor, cursor+len(ix)))
        np.testing.assert_array_equal(ends, grid[k]["ends"][grid[k]["tags"] == tag])
        np.testing.assert_array_equal(bank.scenario_index[ix], np.full(len(ix), bank.scenarios.index(meta[k]["scenario"])))
        ps = old.independent_probabilities(k, meta[k]["token_count"], checked, inventory)
        for rep in range(2):
            expected = np.sqrt(sum(ps[rep][ends-lag] for lag in range(8))/8/24).reshape(len(ends), 768)
            error = max(error, float(np.max(abs(expected-bank.mean_roots[rep][ix]))))
        cursor += len(ix)
    if cursor != len(bank.ends) or error > 2e-6: raise AssertionError("bank coordinate/padding failure")
    return error


def audit_counts(meta, streams, result, ledger, points):
    cohorts = {"normal_all": [k for k, r in meta.items() if r["variant"] in pf.NORMALS],
               "normal_filtered": [k for k, r in meta.items() if r["variant"] in pf.NORMALS and r["filter_pass"] is True],
               "silent_injected": [k for k, r in meta.items() if r["attack_bearing"] and r["silent"]],
               "pre_injection": [k for k, r in meta.items() if r["variant"] == "attack" and not r["attack_bearing"]],
               "legitimate_refusal": [k for k, r in meta.items() if r["variant"] == "legitimate_refusal"],
               "over_refusal": [k for k, r in meta.items() if r["variant"] == "attack" and r["trajectory_class"] == "over_refusal"],
               "over_refusal_all_arms": [k for k, r in meta.items() if r["trajectory_class"] == "over_refusal"]}
    if {k: len(v) for k, v in cohorts.items()} != dict(normal_all=408, normal_filtered=293, silent_injected=40,
          pre_injection=88, legitimate_refusal=24, over_refusal=53, over_refusal_all_arms=59):
        raise AssertionError("cohort count changed")
    positives = [k for k, r in meta.items() if r["variant"] == "attack" and r["x"] is not None]
    if len(positives) != 126 or not all(meta[k]["complete_16"] and meta[k]["has_hit_look"] for k in positives):
        raise AssertionError("full positive coverage changed")
    checks = 0
    for mode, levels in points.items():
        for level, cells in levels.items():
            for j, name in enumerate(ev.CELLS):
                alarms = {k: next((int(e) for e, p in zip(s["ends"], s["p"][:, j]) if p <= cells[name]["alpha"]), None)
                          for k, s in streams.items()}
                if alarms != ledger[f"{mode}/{level}"][name]: raise AssertionError("first-alarm mismatch")
                r = result["readings"][mode][level][name]
                hits = {k: alarms[k] is not None and meta[k]["e"] <= alarms[k] <= meta[k]["x"]+16 for k in positives}
                if hits != r["hits"] or sum(hits.values()) != r["timely_recall"]["count"]: raise AssertionError("hit-window/pre-E mismatch")
                for cohort, keys in cohorts.items():
                    c = r["control_alarms"][cohort]
                    if (c["count"], c["n"]) != (sum(alarms[k] is not None for k in keys), len(keys)):
                        raise AssertionError("control denominator mismatch")
                checks += 1
    return checks


def audit_pairs(meta, result):
    checks = 0
    for condition, pairs in result["comparisons"].items():
        mode, level = condition.split("/")
        for label, r in pairs.items():
            a, b = [result["readings"][mode][level][n]["hits"] for n in label.split("_minus_")]
            x = sum(a[k] and not b[k] for k in a); y = sum(b[k] and not a[k] for k in a)
            p = min(1., 2*sum(comb(x+y, i) for i in range(min(x, y)+1))/2**(x+y))
            m = r["family"]["mcnemar"]
            if (x, y, p) != (m["only_a"], m["only_b"], m["p_value"]): raise AssertionError("McNemar mismatch")
            for name in ("family", "family_x_tier"):
                c = {k: meta[k]["family"] if name == "family" else f'{meta[k]["family"]}|{meta[k]["tier"]}' for k in a}
                if old.bootstrap(a, b, c) != r[name]["ci"]: raise AssertionError("cluster CI mismatch")
            checks += 1
    return checks


def main(source_sha, threshold_sha):
    start = time.monotonic(); guard = run.Guard(False); guard.install(); run.verify_sources(source_sha)
    checked = pf.CheckedInputs(guard)
    frozen = checked.json(run.OUT/"calibrate/threshold_manifest.json", threshold_sha)
    if frozen["source_manifest_sha256"] != source_sha: raise ValueError("freeze mismatch")
    previous = run.previous_seconds(["preflight", "calibrate", "score"]); budget = run.Budget(previous)
    for stage in ("preflight", "calibrate", "score"):
        log = json.loads((run.OUT/stage/"run_manifest.json").read_text())
        if log["status"] != "completed" or log["source_manifest_sha256"] != source_sha: raise ValueError("incomplete stage")
        for rel, sha in log["output_sha256"].items(): checked.read(run.OUT/stage/rel, sha)
        for rel, sha in log["input_sha256"].items(): checked.read(ai.ROOT/rel, sha)
    meta = json.loads((run.OUT/"score/episode_metadata.json").read_text())
    streams = ai.unpack(ai.npz_bytes((run.OUT/"score/look_streams.npz").read_bytes()))
    result = json.loads((run.OUT/"score/result.json").read_text()); ledger = json.loads((run.OUT/"score/alarm_ledger.json").read_text())
    if Counter(r["variant"] for r in meta.values()) != dict(attack=352, clean=192, benign_control=192, benign_lexical=24, legitimate_refusal=24):
        raise AssertionError("784 cohort changed")
    if sum(len(s["ends"]) for s in streams.values()) != 190284: raise AssertionError("look coverage changed")
    for r in meta.values():
        if r["fold"] != frozen["fold_table"][r["scenario"]]: raise AssertionError("scenario fold mismatch")
    count_checks = audit_counts(meta, streams, result, ledger, frozen["workpoints"])
    pair_checks = audit_pairs(meta, result); budget.check()
    inventory = checked.json(pf.INVENTORY, pf.INVENTORY_SHA)["input_sha256"]
    bank_error = nearest_error = 0.; donors_checked = calibrated_looks = 0
    for fold in range(3):
        record = frozen["folds"][str(fold)]; budget.check()
        normal = ai.unpack(ai.npz_bytes((run.OUT/f"calibrate/fold{fold}/look_streams.npz").read_bytes()))
        scored = ai.unpack(ai.npz_bytes((run.OUT/f"score/fold{fold}/look_streams.npz").read_bytes()))
        for j, name in enumerate(math.CELLS):
            state = frozen["calibrations"][str(fold)][name]
            rebuilt = ae.calibrate(name, [ae.stream(k, normal[k], normal[k]["raw"][:, j]) for k in record["fit_keys"]],
                                   [ae.stream(k, normal[k], normal[k]["raw"][:, j]) for k in record["cal_keys"]])
            if rebuilt.state_dict() != state: raise AssertionError("normal calibration reconstruction mismatch")
            maxima = np.asarray(state["channels"][name]["path_maxima"])
            for data in (normal, scored):
                for k, s in data.items():
                    z = literal_z(s, j, state)
                    p = (1+len(maxima)-np.searchsorted(maxima, np.maximum.accumulate(z), side="left"))/(1+len(maxima))
                    np.testing.assert_array_equal(s["z"][:, j], z); np.testing.assert_array_equal(s["p"][:, j], p)
                    calibrated_looks += len(z)
        for tag, info in record["banks"].items():
            budget.check(); bank = math.Bank.restore(ai.npz_bytes(checked.read(run.OUT/info["path"], info["sha256"])))
            bank_error = max(bank_error, audit_bank(bank, info, fold, tag, meta, streams, checked, inventory))
            for data in (normal, scored):
                keys = sorted(k for k in data if tag in data[k]["tags"])
                for k in keys:
                    s = data[k]; ix = np.flatnonzero(s["tags"] == tag); rows = s["donor_rows"][ix]
                    groups = bank.scenario_index[rows]
                    if np.any(np.diff(np.sort(groups, axis=-1), axis=-1) == 0): raise AssertionError("duplicate scenario donors")
                    if any(bank.scenarios[i] == meta[k]["scenario"] for i in np.unique(groups)): raise AssertionError("cross-round scenario leakage")
                    np.testing.assert_array_equal(s["raw"][ix], s["donor_distances"][ix].mean(-1))
                    donors_checked += len(ix)
                # Audit each normal role separately, plus scored eval; selection never uses score/label values.
                roles = [keys] if data is scored else [[k for k in keys if meta[k]["fold"] == f] for f in range(3)]
                for role_keys in roles:
                    if not role_keys: continue
                    k = role_keys[0]; s = data[k]; ix = np.flatnonzero(s["tags"] == tag)
                    ps = old.independent_probabilities(k, meta[k]["token_count"], checked, inventory)
                    for i in sorted({int(ix[0]), int(ix[-1])}):
                        nearest_error = max(nearest_error, run.nearest_audit(ps, int(s["ends"][i]), bank, meta[k]["scenario"],
                                               s["raw"][i], s["donor_rows"][i], s["donor_distances"][i]))
            del bank
        print(json.dumps({"audit_fold_complete": fold, "bank_error": bank_error, "nearest_error": nearest_error, "rss_gib": pf.rss()}), flush=True)
    normals = {k: r for k, r in meta.items() if r["variant"] in pf.NORMALS}
    points = ev.freeze_points(normals, {k: streams[k]["p"] for k in normals}, frozen["reference_counts"])
    if points["points"] != frozen["workpoints"] or points["grids"] != frozen["grids"]: raise AssertionError("workpoint freeze mismatch")
    for mode, columns in result["normal_scenario_ci_at_5pct"].items():
        for den, cells in columns.items():
            keys = sorted(k for k, r in normals.items() if den == "all" or r["filter_pass"] is True)
            groups = {k: meta[k]["scenario"] for k in keys}; alarms = ledger[f"{mode}/0.05"]
            for n, r in cells.items():
                a = {k: alarms[n][k] is not None for k in keys}; b = {k: alarms["S"][k] is not None for k in keys}
                if old.bootstrap(a, b, groups) != r["minus_S"]["ci"]: raise AssertionError("normal difference CI mismatch")
                if old.bootstrap(a, {k: False for k in keys}, groups) != r["rate"]["ci"]: raise AssertionError("normal rate CI mismatch")
    corrected = {mode: {level: {n: gates.correct(meta, r, n, r["workpoint"]["alpha"], frozen["reference_counts"][n], True)
                               for n, r in cells.items()} for level, cells in levels.items()} for mode, levels in result["readings"].items()}
    run.verify_sources(source_sha); checked.verify_again(); budget.check()
    folder = run.OUT/"audit"; folder.mkdir(exist_ok=False)
    ai.write_json(folder/"gates.json", {"readings": corrected, "note": "only independent audit status updated; other frozen risk calculations unchanged"})
    checks = {"status": "completed", "source_manifest_sha256": source_sha, "threshold_manifest_sha256": threshold_sha,
              "count_workpoints_checked": count_checks, "paired_comparisons_checked": pair_checks,
              "donor_looks_checked": donors_checked, "calibrated_look_replays": calibrated_looks,
              "max_bank_coordinate_error": bank_error, "max_nearest_error": nearest_error,
              "elapsed_seconds": time.monotonic()-start, "previous_seconds": previous, "peak_rss_gib": pf.rss(),
              "input_sha256": checked.hashes, "access_guard": guard.summary(), "gates_sha256": pf.digest((folder/"gates.json").read_bytes())}
    ai.write_json(folder/"checks.json", checks)
    print(json.dumps({k: v for k, v in checks.items() if k not in ("input_sha256", "access_guard")}), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("--source-sha256", required=True); p.add_argument("--threshold-sha256", required=True)
    args = p.parse_args(); torch.set_num_threads(1); main(args.source_sha256, args.threshold_sha256)
