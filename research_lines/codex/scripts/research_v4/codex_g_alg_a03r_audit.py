"""Independent spectral/linear-solve, rank, denominator and inference audit for A03-R."""
from __future__ import annotations
import argparse
from contextlib import ExitStack
import json
import time
from unittest.mock import patch
import numpy as np
import torch
from research_v2 import trm3_g
from research_v4 import codex_g_alg_a01_preflight as pf, codex_g_alg_a01_io as ai
from research_v4 import codex_g_alg_a01_audit as original, codex_g_alg_a01_eval as ae
from research_v4 import codex_g_alg_a02_audit as prior, codex_g_alg_a01_gate_report as gates
from research_v4 import codex_g_alg_a03r_eval as ev, codex_g_alg_a03r_run as run

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



def literal_p(raw, reference):
    """Explicit reference comparison, not shared C1's searchsorted implementation."""
    raw, reference = np.asarray(raw), np.asarray(reference)
    if raw.ndim != 1 or reference.ndim != 1 or not len(reference): raise ValueError("nonempty reference required")
    if not np.isfinite(raw).all() or not np.isfinite(reference).all(): raise ValueError("nonfinite rank input")
    running = np.maximum.accumulate(raw); out = np.empty(len(raw))
    for lo in range(0, len(raw), 512):
        v = running[lo:lo+512]
        out[lo:lo+len(v)] = (1+(reference[None, :] >= v[:, None]).sum(axis=1))/(len(reference)+1)
    return out


def independent_matrices(cov, width):
    """No production matrix/floor/whitening helper used."""
    d = len(cov); variance = np.trace(cov)/d; lower = .1*variance
    diagonal = np.maximum(np.diag(cov)+.01*variance, lower)
    full = .5*cov+np.diag(.5*np.diag(cov)+.01*variance)
    within = np.zeros_like(cov)
    for lo in range(0, d, width):
        block = full[lo:lo+width, lo:lo+width]
        e, v = np.linalg.eigh(block)
        within[lo:lo+width, lo:lo+width] = (v*np.maximum(e, lower)) @ v.T
    e, v = np.linalg.eigh(full)
    full = (v*np.maximum(e, lower)) @ v.T
    return np.diag(diagonal), within, full


def solve_scores(roots, mu, matrices):
    delta = np.asarray(roots, np.float64)-mu
    return np.column_stack([np.sum(delta*np.linalg.solve(m, delta.T).T, axis=1)/len(mu) for m in matrices])


def audit_model(model, original_model):
    np.testing.assert_array_equal(model.mu, original_model.mu[1])
    np.testing.assert_array_equal(model.cov, original_model.cov[1])
    matrices = independent_matrices(original_model.cov[1], model.layer_width)
    a, b, c = matrices; d, w = len(model.mu), model.layer_width
    np.testing.assert_allclose(np.square(model.diag_whitener)*np.diag(a), 1., rtol=1e-12, atol=1e-12)
    for i, transform in enumerate(model.block_whitener):
        np.testing.assert_allclose(transform.T @ b[i*w:(i+1)*w, i*w:(i+1)*w] @ transform,
                                   np.eye(w), rtol=1e-10, atol=1e-10)
    np.testing.assert_allclose(model.full_whitener.T @ c @ model.full_whitener, np.eye(d), rtol=1e-9, atol=1e-9)
    return matrices


def audit(checked, folder, budget, frozen, logs):
    old, normals, normal_prior, _ = run.c0.load_parent(checked, True)
    _, meta, oldstreams, _ = run.c0.load_parent(checked, False)
    get = lambda stage, name: checked.json(run.OUT/stage/name, logs[stage]["output_sha256"][name])
    pre = get("preflight", "result.json")
    all_data = {stage: ai.unpack(ai.npz_bytes(checked.read(run.OUT/stage/"look_streams.npz", logs[stage]["output_sha256"]["look_streams.npz"])))
                for stage in ("calibrate", "score")}
    result, ledger = get("score", "result.json"), get("score", "alarm_ledger.json")
    if get("calibrate", "episode_metadata.json") != normals or get("score", "episode_metadata.json") != meta:
        raise ValueError("metadata drift")
    inventory = checked.json(pf.INVENTORY, pf.INVENTORY_SHA)["input_sha256"]
    zcount = rawcount = modelcount = 0; rawerr = probeerr = 0.
    for f in range(3):
        budget.check(); record = frozen["folds"][str(f)]
        roles = run.c0.role_keys(meta, old["folds"][str(f)], f)
        if record["fit_keys"] != roles["fit"] or record["cal_keys"] != roles["cal"]: raise ValueError("role freeze drift")
        data = {stage: ai.unpack(ai.npz_bytes(checked.read(run.OUT/f"{stage}/fold{f}/look_streams.npz",
                 logs[stage]["output_sha256"][f"fold{f}/look_streams.npz"]))) for stage in ("calibrate", "score")}
        for j, name in enumerate(ev.NEW):
            build = lambda k: ae.stream(k, data["calibrate"][k], data["calibrate"][k]["raw"][:, j])
            cal = ae.calibrate(name, [build(k) for k in roles["fit"]], [build(k) for k in roles["cal"]])
            state = frozen["calibrations"][str(f)][name]
            if cal.state_dict() != state: raise ValueError("normal calibration state replay failed")
            if cal.n_reference != frozen["reference_counts"][name][f]: raise ValueError("reference count drift")
            ref = sorted(float(prior.literal_z(data["calibrate"][k], j, state).max()) for k in roles["cal"] if len(data["calibrate"][k]["ends"]))
            np.testing.assert_array_equal(ref, state["channels"][name]["path_maxima"])
            for stage, streams in data.items():
                for k, s in streams.items():
                    z = prior.literal_z(s, j, state)
                    np.testing.assert_array_equal(z, s["z"][:, j]); np.testing.assert_array_equal(literal_p(z, ref), s["p"][:, j])
                    if meta[k]["fold"] == f:
                        for field in ("raw", "z", "p"):
                            np.testing.assert_array_equal(s[field][:, j], all_data[stage][k][field][:, 13+j])
                    zcount += len(z)
        for tag, info in record["models"].items():
            budget.check()
            if info != pre["models"][str(f)][tag] or info["source_model"] != old["folds"][str(f)]["models"][tag]:
                raise ValueError("model ancestry mismatch")
            model = run.math.Model.restore(ai.npz_bytes(checked.read(run.OUT/info["path"], info["sha256"])))
            oi = info["source_model"]
            om = run.oldmath.Model.restore(ai.npz_bytes(checked.read(run.parent.OUT/oi["path"], oi["sha256"])))
            matrices = audit_model(model, om); modelcount += 1
            probe = ai.npz_bytes(checked.read(run.OUT/f"preflight/fold{f}/probe_{tag}.npz",
                logs["preflight"]["output_sha256"][f"fold{f}/probe_{tag}.npz"]))
            literal = solve_scores(probe["roots"], model.mu, matrices)
            np.testing.assert_allclose(literal, probe["scores"], rtol=1e-9, atol=1e-10)
            probeerr = max(probeerr, float(np.max(abs(literal-probe["scores"]))))
            for stage, streams in data.items():
                selections = ([sorted(k for k in roles[role] if k in streams and tag in streams[k]["tags"]) for role in ("fit", "cal", "eval")]
                              if stage == "calibrate" else
                              [sorted(k for k in streams if meta[k]["variant"] == arm and tag in streams[k]["tags"])
                               for arm in ("clean", "benign_control", "benign_lexical", "attack", "legitimate_refusal")])
                for keys in selections:
                    if not keys: continue
                    k = keys[0]; s = streams[k]; ix = np.flatnonzero(s["tags"] == tag)
                    take = sorted({int(ix[0]), int(ix[-1])}); ends = s["ends"][take]
                    ps = original.independent_probabilities(k, meta[k]["token_count"], checked, inventory)
                    roots = np.sqrt(sum(ps[1][ends-lag] for lag in range(8))/8/24).reshape(len(ends), 768)
                    literal = solve_scores(roots, model.mu, matrices)
                    np.testing.assert_allclose(literal, s["raw"][take], rtol=5e-6, atol=2e-6)
                    rawerr = max(rawerr, float(np.max(abs(literal-s["raw"][take])))); rawcount += len(take)
            del model, om, matrices
        print(json.dumps({"audit_fold_complete": f, "raw_score_max_error": rawerr, "rss_gib": pf.rss()}), flush=True)
        del data
    for stage, streams in all_data.items():
        source = normal_prior if stage == "calibrate" else oldstreams
        for k, s in streams.items():
            for field in ("ends", "tags", "ordinals"): np.testing.assert_array_equal(s[field], source[k][field])
            for field in ("raw", "z", "p"): np.testing.assert_array_equal(s[field][:, :13], source[k][field])
    for k, s in all_data["calibrate"].items():
        for field in s: np.testing.assert_array_equal(s[field], all_data["score"][k][field])
    points = ev.freeze_points(normals, {k: s["p"] for k, s in all_data["calibrate"].items()}, frozen["reference_counts"])
    if points["points"] != frozen["workpoints"] or points["grids"] != frozen["grids"]: raise ValueError("normal workpoint mismatch")
    count = audit_counts(meta, all_data["score"], result, ledger, frozen["workpoints"])
    pairs = prior.audit_pairs(meta, result); budget.check()
    early_count = ci_count = 0
    positives = sorted(k for k, r in meta.items() if r["variant"] == "attack" and r["x"] is not None)
    for level, cells in result["early_deadlines_matched_all_descriptive"].items():
        for name, rows in cells.items():
            alarms = ledger[f"matched_all/{level}"][name]
            for delta, row in rows.items():
                count_hit = sum(alarms[k] is not None and meta[k]["e"] <= alarms[k] <= meta[k]["x"]+int(delta) for k in positives)
                absent = [k for k in positives if not any(meta[k]["e"] <= e <= meta[k]["x"]+int(delta) for e in all_data["score"][k]["ends"])]
                if row["count"] != count_hit or row["n"] != 126 or row["no_eligible_look"] != absent: raise ValueError("early replay mismatch")
                early_count += 1
    for mode, levels in result["normal_scenario_ci"].items():
        for level, denominators in levels.items():
            alarms = ledger[f"{mode}/{level}"]
            for den, cells in denominators.items():
                keys = sorted(k for k, r in normals.items() if den == "all" or r["filter_pass"] is True)
                clusters = {k: meta[k]["scenario"] for k in keys}
                for name, row in cells.items():
                    a = {k: alarms[name][k] is not None for k in keys}
                    if original.bootstrap(a, {k: False for k in keys}, clusters) != row["rate"]["ci"]: raise ValueError("normal CI mismatch")
                    comparisons = [("minus_S", "S")]+([("minus_unfloored", name.removesuffix("_floor"))] if name in ev.NEW else [])
                    for field, baseline in comparisons:
                        if original.bootstrap(a, {k: alarms[baseline][k] is not None for k in keys}, clusters) != row[field]["ci"]:
                            raise ValueError("normal paired CI mismatch")
                    ci_count += 1
    # Recompute the declared primary gate, not just its inputs.
    pr = result["comparisons"]["matched_all/0.01"]["W_full_floor_minus_W_full"]
    passed = pr["gain_gate_eligible"] and pr["family"]["ci"][0] > 0 and pr["family"]["mcnemar"]["p_value"] < .05
    if bool(passed) != result["primary_gain_gate"]["pass"]: raise ValueError("primary gate mismatch")
    corrected = {mode: {level: {n: gates.correct(meta, r, n, r["workpoint"]["alpha"], frozen["reference_counts"][n], True)
                               for n, r in cells.items()} for level, cells in levels.items()} for mode, levels in result["readings"].items()}
    ai.write_json(folder/"gates.json", {"readings": corrected, "role": "only audit availability flag updated"})
    ai.write_json(folder/"checks.json", {"status": "PASS", "models": modelcount, "workpoints": count, "paired_comparisons": pairs,
                  "early_deadline_columns": early_count, "normal_ci_rows": ci_count, "literal_z_p_looks": zcount,
                  "independent_raw_windows": rawcount, "independent_raw_max_error": rawerr, "fixed_root_solve_max_error": probeerr,
                  "normal_replay_exact": True, "old_thirteen_replay_exact": True, "primary_gate_replayed": True,
                  "role": "independent formula replay, not independent test data or external reviewer"})


def main(source_sha, pre_sha, threshold_sha, cal_sha, score_sha):
    start = time.monotonic(); guard = run.Guard(False); guard.install(); checked = pf.CheckedInputs(guard)
    run.verify_sources(source_sha, checked)
    frozen = checked.json(run.OUT/"calibrate/threshold_manifest.json", threshold_sha)
    if frozen["source_manifest_sha256"] != source_sha: raise ValueError("threshold/source mismatch")
    logs = {stage: run.prior_log(checked, stage, sha, source_sha) for stage, sha in
            (("preflight", pre_sha), ("calibrate", cal_sha), ("score", score_sha))}
    if any(logs[s]["threshold_manifest_sha256"] != threshold_sha for s in ("calibrate", "score")):
        raise ValueError("wrong frozen thresholds")
    previous = run.RESERVE_SECONDS+sum(log["elapsed_seconds"] for log in logs.values())
    budget = run.parent.Budget(previous); budget.start = start
    folder = run.OUT/"audit"; folder.mkdir(exist_ok=False); status, error = "failed", None
    try:
        torch.set_num_threads(1); torch.set_num_interop_threads(1)
        with ExitStack() as scope:
            for owner, name in ((run.oldmath, "fit"), (run.oldmath, "moments"), (run.math, "derive"), (run.math, "floored"), (pf, "routes")):
                scope.enter_context(patch.object(owner, name, run.prohibit))
            audit(checked, folder, budget, frozen, logs)
        run.verify_sources(source_sha, checked); checked.verify_again(); budget.check(); status = "completed"
    except Exception as exc:
        error = repr(exc); raise
    finally:
        log = {"status": status, "error": error, "source_manifest_sha256": source_sha, "threshold_manifest_sha256": threshold_sha,
               "prior_log_sha256": {"preflight": pre_sha, "calibrate": cal_sha, "score": score_sha},
               "input_sha256": checked.hashes, "output_sha256": run.parent.hashes(folder), "previous_seconds": previous,
               "elapsed_seconds": time.monotonic()-start, "peak_gib": pf.rss(), "access_guard": guard.summary()}
        ai.write_json(folder/"run_manifest.json", log)
        print(json.dumps({k: log[k] for k in ("status", "error", "elapsed_seconds", "previous_seconds", "peak_gib")}), flush=True)
        print(json.dumps({"run_manifest_sha256": pf.digest((folder/"run_manifest.json").read_bytes())}), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    for n in ("source", "pre-log", "threshold", "cal-log", "score-log"): p.add_argument(f"--{n}-sha", required=True)
    a = p.parse_args(); main(a.source_sha, a.pre_log_sha, a.threshold_sha, a.cal_log_sha, a.score_log_sha)
