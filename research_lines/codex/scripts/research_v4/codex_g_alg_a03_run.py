"""A03 bounded normal-first covariance experiment; never loads a generation model."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import subprocess
import time
from unittest.mock import patch
import numpy as np
import torch
from research_v2 import trm3_g
from research_v4 import codex_g_alg_a01_math as am, codex_g_alg_a01_preflight as pf
from research_v4 import codex_g_alg_a01_io as ai, codex_g_alg_a01_eval as ae
from research_v4 import codex_g_alg_a02_run as previous, codex_g_alg_a02_audit as prior_audit
from research_v4 import codex_g_alg_a03_math as math, codex_g_alg_a03_eval as ev
from research_v4 import codex_g_alg_a03_preflight as pre

ROOT, OUT = pre.ROOT, pre.OUT
PRE_SOURCE_SHA = "d40f23b43e73f07ea9dc7d71abeeca05447d33e70cb3b2e9c4114b7702df6cc8"
PRE_RESULT_SHA = "253746c4b99419403d7dfb9cf241de90fc4f9f4a6193688d5f8292f41690e496"
PRE_LOG_SHA = "560e5fa31022456fb8e8b2ceda86bdf0779a78d09e3d3c10fa03b90892e9a981"
A02_SCORE_LOG_SHA = "caf21ca8f11bb89b13c62db43585530e82efcef34b81ddd0aed49b7d48480c80"
EXTRA = ("docs/research_v4/codex_g_alg_a03_execution_v1.md", "scripts/research_v4/codex_g_alg_a03_run.py",
         "scripts/research_v4/codex_g_alg_a03_eval.py", "scripts/research_v4/codex_g_alg_a03_audit.py",
         "tests/test_research_v4_codex_g_alg_a03_full.py")
Guard, Budget, hashes = pre.Guard, previous.Budget, previous.hashes


def literal_preflight_sources():
    body = (OUT/"preflight_source_manifest.json").read_bytes()
    if pf.digest(body) != PRE_SOURCE_SHA: raise ValueError("parent source manifest changed")
    sources = json.loads(body)["source_sha256"]
    for p, sha in sources.items():
        if pf.digest((ROOT/p).read_bytes()) != sha: raise ValueError(f"parent source changed: {p}")
    return sources


def snapshot():
    return {**literal_preflight_sources(), **pf.source_snapshot(),
            **{p: pf.digest((ROOT/p).read_bytes()) for p in EXTRA}}


def freeze():
    guard = Guard(True); guard.install(); checked = pf.CheckedInputs(guard)
    log = checked.json(OUT/"preflight/run_manifest.json", PRE_LOG_SHA)
    result = checked.json(OUT/"preflight/result.json", PRE_RESULT_SHA)
    if log["source_manifest_sha256"] != PRE_SOURCE_SHA or not result["cost_gate_pass"]:
        raise ValueError("preflight has not passed")
    ai.write_json(OUT/"execution_source_manifest.json", {"source_sha256": snapshot(), "preflight_source_sha256": PRE_SOURCE_SHA,
                  "preflight_result_sha256": PRE_RESULT_SHA, "preflight_log_sha256": PRE_LOG_SHA,
                  "created_utc": datetime.now(timezone.utc).isoformat(),
                  "head_provenance": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  "cpu_threads": 1, "budget_seconds": 600, "memory_limit_gib": 2,
                  "evidence_role": "adaptive G-dev full-episode development, not confirmation"})
    print(json.dumps({"source_manifest_sha256": pf.digest((OUT/"execution_source_manifest.json").read_bytes())}), flush=True)


def verify_sources(sha):
    body = (OUT/"execution_source_manifest.json").read_bytes()
    if pf.digest(body) != sha: raise ValueError("execution source manifest changed")
    sources = json.loads(body)["source_sha256"]
    for p, expected in sources.items():
        if pf.digest((ROOT/p).read_bytes()) != expected: raise ValueError(f"frozen source changed: {p}")
    if not set(pf.source_snapshot()).issubset(sources): raise ValueError("unfrozen imported dependency")
    literal_preflight_sources()
    return sources


def previous_seconds(stages):
    total = 0.
    for stage in stages:
        body = (OUT/stage/"run_manifest.json").read_bytes()
        if stage == "preflight" and pf.digest(body) != PRE_LOG_SHA: raise ValueError("preflight log changed")
        log = json.loads(body)
        if log["status"] != "completed": raise ValueError("earlier stage incomplete")
        total += log["elapsed_seconds"]
    return total


def old_baseline(checked, normal, meta, grid):
    state, cal_log = pre.previous_normal(checked)
    stage = "calibrate" if normal else "score"
    log = cal_log if normal else checked.json(previous.OUT/"score/run_manifest.json", A02_SCORE_LOG_SHA)
    data = ai.npz_bytes(checked.read(previous.OUT/stage/"look_streams.npz", log["output_sha256"]["look_streams.npz"]))
    if list(data["columns"]) != list(ev.BASELINES): raise ValueError("old columns changed")
    streams = ai.unpack(data)
    if set(streams) != set(meta): raise ValueError("old cohort changed")
    for k, s in streams.items():
        for f in ("ends", "tags", "ordinals"): np.testing.assert_array_equal(s[f], grid[k][f])
    return streams, state


def raw_fold(outer, normal, meta, grid, episodes, inventory, checked, directory, budget, old, frozen=None):
    fit = sorted(k for k, r in meta.items() if r["fold"] == (outer+1)%3 and r["variant"] in pf.NORMALS and r["filter_pass"] is True)
    keys = sorted(k for k, r in meta.items() if r["fold"] == outer or (normal and r["filter_pass"] is True))
    if normal and any(meta[k]["variant"] not in pf.NORMALS for k in keys): raise ValueError("non-normal calibration")
    if not normal and fit != frozen["fit_keys"]: raise ValueError("fit pool changed")
    fit_scenarios = {meta[k]["scenario"] for k in fit}
    if any(meta[k]["fold"] != (outer+1)%3 and meta[k]["scenario"] in fit_scenarios for k in keys):
        raise ValueError("cal/eval scenario leakage")
    data = {k: {**{f: np.asarray(grid[k][f]) for f in ("ends", "tags", "ordinals")},
                "raw": np.full((len(grid[k]["ends"]), 6), np.nan)} for k in keys}
    conditions = sorted({str(t) for k in keys for t in grid[k]["tags"]})
    models, costs = {}, []
    if not normal and set(conditions) != set(frozen["models"]): raise ValueError("unsupported/changed eval channels")
    for tag in conditions:
        budget.check(); start = time.monotonic()
        if normal:
            bank, donor_info = pre.load_bank(outer, tag, old, meta, grid, checked)
            weights = math.balanced_weights(bank.keys, bank.scenario_index)
            model = math.fit(bank.mean_roots, weights)
            path = directory/f"model_{tag}.npz"; sha = ai.save_npz(path, **model.state())
            info = {"path": str(path.relative_to(OUT)), "sha256": sha, "fit_keys": donor_info["fit_keys"],
                    "fit_scenarios": list(bank.scenarios), "fit_looks": len(bank.ends), "source_bank_sha256": donor_info["sha256"],
                    "diagnostics": math.diagnostics(model), "compressed_bytes": path.stat().st_size,
                    "tensor_bytes": sum(a.nbytes for a in model.state().values()), "fit_scoring": "resubstitution"}
            del bank, weights
        else:
            info = frozen["models"][tag]
            model = math.Model.restore(ai.npz_bytes(checked.read(OUT/info["path"], info["sha256"])))
        models[tag] = info; costs.append({"tag": tag, "build_or_restore_seconds": time.monotonic()-start})
        start = time.monotonic(); qkeys = [k for k in keys if tag in grid[k]["tags"]]
        for key in qkeys:
            budget.check(); ix = np.flatnonzero(grid[key]["tags"] == tag)
            ps = pf.routes(key, episodes, checked, inventory)
            then = time.monotonic(); roots = [am.features(p, grid[key]["ends"][ix])[1] for p in ps]
            data[key]["raw"][ix] = math.score(roots, model)
            costs.append({"tag": tag, "key": key, "looks": len(ix), "score_seconds": time.monotonic()-then})
            del ps, roots
        print(json.dumps({"fold": outer, "normal": normal, "channel_complete": tag,
                          "queries": len(qkeys), "seconds": time.monotonic()-start, "rss_gib": pf.rss()}), flush=True)
        del model
    for s in data.values():
        if not np.isfinite(s["raw"]).all(): raise ValueError("missing/nonfinite look scores")
    return data, {"fit_keys": fit, "models": models, "costs": costs}


def prohibit(*args, **kwargs): raise AssertionError("A03 score stage is restore-only")


def export(path, streams, name, col, fold, role, fit, models):
    with path.open("x") as f:
        for k, s in sorted(streams.items()):
            row = {"schema": "dataset-g-look-scores-1.0.0", "statistic": name, "batch": "g_dev", "key": k,
                   "trace_id": k.split("|", 1)[1], "view": "V1", "tag_scope": "message", "window_width": 8,
                   "ends": s["ends"].tolist(), "scores": s["raw"][:, col].tolist(),
                   "tags": list(map(str, s["tags"])), "ordinals": s["ordinals"].tolist(),
                   "config": {"outer_fold": fold, "role": role, "condition": ["channel"], "representation": name.split("_")[0],
                              "structure": name.split("_")[1], "layers": list(range(24)), "dimension": 768,
                              "window_summary": "sqrt(mean8/24)", "score": "mahalanobis_squared/d", "rho": .5, "eta": .01,
                              "fit_weighting": "scenario/episode/look_equal", "fit_scoring": "resubstitution",
                              "cal_eval_isolation": "entire_scenario", "observation": "M7_complete_episode", "query_block": 256,
                              "model_sha256": {t: m["sha256"] for t, m in models.items()}},
                   "fit_pool_sha256": ai.fit_hash(fit)}
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+"\n")


def experiment(normal, checked, directory, budget, source_sha, frozen=None):
    if normal: meta, grid, episodes, inventory = pf.normal_inputs(checked)
    else: meta, grid, episodes, inventory, _ = ai.score_inputs(checked)
    prior, old = old_baseline(checked, normal, meta, grid)
    counts = {n: old["reference_counts"][n] for n in ev.BASELINES}
    counts.update({n: [] for n in math.CELLS})
    combined, states, folds = {}, {}, {}
    with ExitStack() as scope:
        if not normal:
            for owner, name in ((math, "fit"), (math, "moments"), (ae, "calibrate"), (trm3_g, "calibrate_g")):
                scope.enter_context(patch.object(owner, name, prohibit))
        for outer in range(3):
            folder = directory/f"fold{outer}"; folder.mkdir()
            data, record = raw_fold(outer, normal, meta, grid, episodes, inventory, checked, folder, budget, old,
                                    None if normal else frozen["folds"][str(outer)])
            fit = record["fit_keys"]
            cal_keys = sorted(k for k, r in meta.items() if r["fold"] == (outer+2)%3 and r["filter_pass"] is True and r["variant"] in pf.NORMALS)
            state, quantiles = {}, {}
            for j, name in enumerate(math.CELLS):
                streams = {k: ae.stream(k, s, s["raw"][:, j]) for k, s in data.items()}
                calibration = (ae.calibrate(name, [streams[k] for k in fit], [streams[k] for k in cal_keys]) if normal else
                               trm3_g.calibration_from_state(frozen["calibrations"][str(outer)][name]))
                counts[name].append(calibration.n_reference)
                if calibration.n_reference != counts["S"][outer] or calibration.horizon["censored_endpoints"] != 0:
                    raise ValueError("calibration coverage changed")
                state[name] = calibration.state_dict()
                for k, s in streams.items():
                    z, p = ae.score(name, s, calibration)
                    data[k].setdefault("z", np.empty_like(data[k]["raw"]))[:, j] = z
                    data[k].setdefault("p", np.empty_like(data[k]["raw"]))[:, j] = p
                if normal:
                    quantiles[name] = {role: np.quantile(np.concatenate([data[k]["raw"][:, j] for k in keys]), [.5, .9, .99]).tolist()
                                       for role, keys in (("fit_resubstitution", fit), ("cal_held_out", cal_keys))}
            states[str(outer)] = state; record.update(cal_keys=cal_keys, raw_quantiles_descriptive=quantiles)
            record["streams_sha256"] = ai.save_streams(folder/"look_streams.npz", data, math.CELLS)
            ai.write_json(folder/"record.json", record); folds[str(outer)] = record
            roles = [("eval", [k for k in data if meta[k]["fold"] == outer])]
            if normal: roles += [("fit", fit), ("cal", cal_keys)]
            for role, keys in roles:
                for j, name in enumerate(math.CELLS):
                    export(folder/f"{name}_{role}.jsonl", {k: data[k] for k in keys}, name, j, outer, role, fit, record["models"])
            for k, s in data.items():
                if meta[k]["fold"] != outer: continue
                if k in combined: raise ValueError("duplicate eval")
                combined[k] = {**{f: s[f] for f in ("ends", "tags", "ordinals")},
                               **{f: np.column_stack((prior[k][f], s[f])) for f in ("raw", "z", "p")}}
            del data; budget.check()
    if set(combined) != set(meta): raise ValueError("missing eval episodes")
    ai.write_json(directory/"episode_metadata.json", meta)
    ai.save_streams(directory/"look_streams.npz", combined, ev.CELLS)
    if normal:
        points = ev.freeze_points(meta, {k: s["p"] for k, s in combined.items()}, counts)
        for den in ("all", "filtered"):
            for n in ev.BASELINES:
                if points["grids"][den][n] != old["grids"][den][n]: raise ValueError("old normal grid not replayed")
        manifest = {"source_manifest_sha256": source_sha, "preflight_source_manifest_sha256": PRE_SOURCE_SHA,
                    "calibrations": states, "folds": folds, "workpoints": points["points"], "grids": points["grids"],
                    "reference_counts": counts, "normal_input_sha256": checked.hashes, "normal_output_sha256": hashes(directory),
                    "fold_table": old["fold_table"], "length_cutpoints": [219, 382],
                    "primary": "matched_all/0.05/W_full_minus_W_diag", "evidence_role": "normal-first G-dev adaptive development"}
        ai.write_json(directory/"threshold_manifest.json", manifest)
        return pf.digest((directory/"threshold_manifest.json").read_bytes())
    normals = ai.unpack(ai.npz_bytes(checked.read(OUT/"calibrate/look_streams.npz", frozen["normal_output_sha256"]["look_streams.npz"])))
    for k, s in normals.items():
        for f in ("raw", "z", "p", "ends", "tags", "ordinals"): np.testing.assert_array_equal(s[f], combined[k][f])
    if counts != frozen["reference_counts"]: raise ValueError("changed calibration counts")
    result, ledger = ev.evaluate(meta, combined, frozen["workpoints"], counts)
    result.update(normal_replay_exact=True, old_seven_cells_replayed_exact=True)
    ai.write_json(directory/"result.json", result); ai.write_json(directory/"alarm_ledger.json", ledger)
    return None


def run(stage, source_sha, threshold_sha=None):
    start = time.monotonic(); normal = stage == "calibrate"
    guard = Guard(normal); guard.install(); verify_sources(source_sha); checked = pf.CheckedInputs(guard)
    previous_time = previous_seconds(["preflight"] if normal else ["preflight", "calibrate"])
    budget = Budget(previous_time); frozen = None
    pre_result = checked.json(OUT/"preflight/result.json", PRE_RESULT_SHA)
    if not pre_result["cost_gate_pass"]: raise ValueError("cost gate has not passed")
    if not normal:
        if not threshold_sha: raise ValueError("threshold SHA required before attack access")
        frozen = checked.json(OUT/"calibrate/threshold_manifest.json", threshold_sha)
        if frozen["source_manifest_sha256"] != source_sha: raise ValueError("normal freeze mismatch")
        for p, sha in frozen["normal_input_sha256"].items(): checked.read(ROOT/p, sha)
        for p, sha in frozen["normal_output_sha256"].items(): checked.read(OUT/"calibrate"/p, sha)
    directory = OUT/stage; directory.mkdir(exist_ok=False)
    ai.write_json(directory/"started.json", {"source_manifest_sha256": source_sha, "threshold_manifest_sha256": threshold_sha,
                  "utc": datetime.now(timezone.utc).isoformat()})
    try:
        new_sha = experiment(normal, checked, directory, budget, source_sha, frozen)
        if normal: threshold_sha = new_sha; print(json.dumps({"threshold_manifest_sha256": threshold_sha}), flush=True)
        verify_sources(source_sha); checked.verify_again(); budget.check()
        ai.write_json(directory/"run_manifest.json", {"status": "completed", "source_manifest_sha256": source_sha,
                      "threshold_manifest_sha256": threshold_sha, "input_sha256": checked.hashes, "output_sha256": hashes(directory),
                      "elapsed_seconds": time.monotonic()-start, "previous_stage_seconds": previous_time, "peak_rss_gib": pf.rss(),
                      "access_guard": guard.summary()})
        print(json.dumps({"stage": stage, "status": "completed", "seconds": time.monotonic()-start,
                          "cumulative_seconds": previous_time+time.monotonic()-start, "rss_gib": pf.rss()}), flush=True)
    except Exception as exc:
        ai.write_json(directory/"failure.json", {"status": "failed", "type": type(exc).__name__, "message": str(exc),
                      "elapsed_seconds": time.monotonic()-start, "previous_seconds": previous_time,
                      "access_guard": guard.summary(), "input_sha256": checked.hashes})
        raise


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stage", choices=("freeze", "calibrate", "score"), required=True)
    p.add_argument("--source-sha256"); p.add_argument("--threshold-sha256")
    args = p.parse_args(); torch.set_num_threads(1)
    if args.stage == "freeze": freeze()
    elif not args.source_sha256: p.error("source SHA required")
    else: run(args.stage, args.source_sha256, args.threshold_sha256)
