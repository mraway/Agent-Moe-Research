"""A03-R: one fixed spectrum revision, normal-first, bounded, restore-only evaluation."""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import time
from unittest.mock import patch
import numpy as np
import torch
from research_v2 import trm3_g
from research_v4 import codex_g_alg_a01_preflight as pf, codex_g_alg_a01_io as ai
from research_v4 import codex_g_alg_a01_math as am, codex_g_alg_a01_eval as ae
from research_v4 import codex_g_alg_a03_math as oldmath, codex_g_alg_a03_run_v1_1 as parent
from research_v4 import codex_g_alg_a03c0_run as c0
from research_v4 import codex_g_alg_a03r_math as math, codex_g_alg_a03r_eval as ev

ROOT, OUT = pf.ROOT, pf.BASE/"alg_a03r_spectral_floor_v1"
N_OUT = pf.BASE/"alg_a03n_normal_structure_v1"
N_SOURCE_SHA = "ba27693986d045d8360c1611170ad6f352ae7809ccb2654f17831ecdd0638704"
N_REPORT = "docs/research_v4/codex_g_alg_a03n_report.md"
N_REPORT_SHA = "8b9cb6e25842803f3a4053ea505391ebf1261525bb1dc214c6869d4f5fb11ae4"
EXTRA = ("docs/research_v4/codex_g_alg_a03r_spec_v1.md", N_REPORT,
         "scripts/research_v4/codex_g_alg_a03r_math.py", "scripts/research_v4/codex_g_alg_a03r_run.py",
         "scripts/research_v4/codex_g_alg_a03r_eval.py", "scripts/research_v4/codex_g_alg_a03r_audit.py",
         "tests/test_research_v4_codex_g_alg_a03r.py")
TAGS = ("analysis", "commentary", "final")
RESERVE_SECONDS = 60.


class Guard(parent.Guard):
    def check_path(self, path):
        p = super().check_path(path)
        if self.normal and pf.BASE in p.parents:
            rel = p.relative_to(pf.BASE)
            if "score" in rel.parts or (OUT in p.parents and "audit" in rel.parts) or rel.parts[0] in (
                    "alg_a03d_timing_tail_v1", "alg_a02d_normal_tail_v1"):
                self.blocked_attempts += 1
                raise PermissionError("A03-R normal stage refuses mixed-arm/posthoc inputs")
        return p


def parent_sources(checked):
    state = checked.json(N_OUT/"source_manifest.json", N_SOURCE_SHA)
    for p, h in state["source_sha256"].items(): checked.read(ROOT/p, h)
    c0.parent_sources(checked); checked.read(ROOT/N_REPORT, N_REPORT_SHA)
    return state["source_sha256"]


def freeze():
    guard = Guard(True); guard.install(); checked = pf.CheckedInputs(guard)
    sources = {**parent_sources(checked), **pf.source_snapshot(), **{p: pf.digest((ROOT/p).read_bytes()) for p in EXTRA}}
    OUT.mkdir(exist_ok=False)
    ai.write_json(OUT/"source_manifest.json", {"source_sha256": sources, "parent_source_sha256": N_SOURCE_SHA,
                  "parent_threshold_sha256": c0.PARENT_THRESHOLD_SHA, "floor_ratio": math.FLOOR,
                  "created_utc": datetime.now(timezone.utc).isoformat(), "cpu_threads": 1,
                  "budget_seconds": 600, "memory_limit_gib": 2, "other_work_reserved_seconds": RESERVE_SECONDS,
                  "role": "normal-first adaptive G-dev spectral-floor ablation, not confirmation"})
    print(json.dumps({"source_manifest_sha256": pf.digest((OUT/"source_manifest.json").read_bytes())}), flush=True)


def verify_sources(sha, checked):
    state = checked.json(OUT/"source_manifest.json", sha)
    for p, h in state["source_sha256"].items(): checked.read(ROOT/p, h)
    parent_sources(checked)
    if not set(pf.source_snapshot()).issubset(state["source_sha256"]): raise ValueError("unfrozen imported dependency")


def prohibit(*args, **kwargs): raise AssertionError("A03-R evaluation is restore-only; no fitting or parameter selection")


def context(checked, normal):
    old, meta, prior, log = c0.load_parent(checked, normal)
    if normal: pm, grid, episodes, inventory = pf.normal_inputs(checked)
    else: pm, grid, episodes, inventory, _ = ai.score_inputs(checked)
    if set(pm) != set(meta): raise ValueError("routing/metadata cohort mismatch")
    for k in meta:
        for field in ("variant", "filter_pass", "scenario", "fold", "token_count"):
            if pm[k][field] != meta[k][field]: raise ValueError("routing metadata mismatch")
        for field in ("ends", "tags", "ordinals"): np.testing.assert_array_equal(grid[k][field], prior[k][field])
    return old, meta, prior, episodes, inventory, log


def preflight(checked, folder, budget):
    old, meta, grid, episodes, inventory, _ = context(checked, True)
    models, probes = {}, []
    normal_looks = normal_calls = 0
    for f in range(3):
        roles = c0.role_keys(meta, old["folds"][str(f)], f)
        keys = sorted(set(sum(roles.values(), [])))
        normal_looks += sum(len(grid[k]["ends"]) for k in keys)
        normal_calls += sum(len(set(grid[k]["tags"])) for k in keys)
        models[str(f)] = {}
        directory = folder/f"fold{f}"; directory.mkdir()
        for tag in TAGS:
            budget.check(); then = time.monotonic()
            info = old["folds"][str(f)]["models"][tag]
            original = oldmath.Model.restore(ai.npz_bytes(checked.read(parent.OUT/info["path"], info["sha256"])))
            model, diag = math.derive(original.mu[1], original.cov[1])
            path = directory/f"model_{tag}.npz"; sha = ai.save_npz(path, **model.state())
            model = math.Model.restore(ai.npz_bytes(checked.read(path, sha)))
            models[str(f)][tag] = {"path": str(path.relative_to(OUT)), "sha256": sha,
                                   "source_model": info, "diagnostics": diag, "seconds": time.monotonic()-then}
            key = next(k for k in roles["fit"] if tag in grid[k]["tags"])
            ends = grid[key]["ends"][grid[key]["tags"] == tag][:256]
            then = time.monotonic(); ps = pf.routes(key, episodes, checked, inventory)
            roots = am.features(ps[1], ends)[1]; q = math.score(roots, model)
            probes.append({"fold": f, "tag": tag, "key": key, "ends": ends.tolist(), "looks": len(ends),
                           "seconds": time.monotonic()-then})
            ai.save_npz(directory/f"probe_{tag}.npz", roots=roots, scores=q)
            del ps, roots, model, original
        print(json.dumps({"preflight_models_fold": f, "rss_gib": pf.rss()}), flush=True)
    seconds = sum(r["seconds"] for r in probes); looks = sum(r["looks"] for r in probes)
    by_look = seconds/looks*(normal_looks+190284)
    by_call = seconds/len(probes)*(normal_calls+784*3)
    projected = budget.previous+time.monotonic()-budget.start+2*max(by_look, by_call)+180
    result = {"models": models, "probes": probes, "normal_role_looks": normal_looks, "normal_calls": normal_calls,
              "known_eval_looks": 190284, "eval_calls_upper_bound": 784*3, "projected_by_look_seconds": by_look,
              "projected_by_call_seconds": by_call, "projected_total_seconds": projected,
              "cost_gate_pass": projected <= 600 and pf.rss() <= 2, "normal_episodes": 408, "filtered_episodes": 293,
              "role": "normal-only frozen transform construction and cost check; no attack score"}
    ai.write_json(folder/"result.json", result)
    if not result["cost_gate_pass"]: raise RuntimeError("A03-R cost preflight failed; no automatic budget relaxation")


def export(path, data, keys, name, col, fold, role, fit_keys, models):
    with path.open("x") as f:
        for k in keys:
            s = data[k]
            row = {"schema": "dataset-g-look-scores-1.0.0", "statistic": name, "batch": "g_dev", "key": k,
                   "trace_id": k.split("|", 1)[-1], "view": "V1", "tag_scope": "message", "window_width": 8,
                   "ends": s["ends"].tolist(), "scores": s["raw"][:, col].tolist(),
                   "tags": list(map(str, s["tags"])), "ordinals": s["ordinals"].tolist(),
                   "fit_pool_sha256": ai.fit_hash(fit_keys), "config": {"outer_fold": fold, "role": role,
                   "representation": "W", "structure": name.removeprefix("W_").removesuffix("_floor"),
                   "layers": list(range(24)), "window_summary": "sqrt(mean8/24)", "dimension": 768,
                   "rho": .5, "eta": .01, "floor_ratio": math.FLOOR, "floor_unit": "trace(C)/dimension",
                   "geometry_condition": ["channel"], "standardise": True,
                   "calibration": "shared_channel_position_standardisation_then_C1_full_path_max",
                   "observation": "M7_complete_episode", "mean_cov_refit": False,
                   "geometry_source_threshold_sha256": c0.PARENT_THRESHOLD_SHA,
                   "model_sha256": {t: m["sha256"] for t, m in models.items()}}}
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+"\n")


def raw_fold(f, normal, meta, grid, episodes, inventory, checked, budget, models, old):
    roles = c0.role_keys(meta, old["folds"][str(f)], f)
    keys = sorted(set(sum(roles.values(), []))) if normal else roles["eval"]
    data = {k: {**{field: np.asarray(grid[k][field]) for field in ("ends", "tags", "ordinals")},
                "raw": np.full((len(grid[k]["ends"]), 3), np.nan)} for k in keys}
    costs = []
    for tag in TAGS:
        budget.check(); then = time.monotonic(); info = models[tag]
        if info["source_model"] != old["folds"][str(f)]["models"][tag]: raise ValueError("wrong parent model")
        model = math.Model.restore(ai.npz_bytes(checked.read(OUT/info["path"], info["sha256"])))
        qkeys = [k for k in keys if tag in grid[k]["tags"]]
        for k in qkeys:
            budget.check(); ix = np.flatnonzero(grid[k]["tags"] == tag)
            ps = pf.routes(k, episodes, checked, inventory)
            roots = am.features(ps[1], grid[k]["ends"][ix])[1]
            data[k]["raw"][ix] = math.score(roots, model)
            del ps, roots
        costs.append({"tag": tag, "queries": len(qkeys), "seconds": time.monotonic()-then})
        print(json.dumps({"fold": f, "normal": normal, "channel_complete": tag, **costs[-1], "rss_gib": pf.rss()}), flush=True)
        del model
    for s in data.values():
        if not np.isfinite(s["raw"]).all(): raise ValueError("missing new look score")
    return data, roles, costs


def floor_tails(meta, data):
    out = {}
    for name in ("W_diag", "W_block", "W_full", *ev.NEW):
        col = ev.CELLS.index(name); out[name] = {}
        for alpha in (1/105, 1/96, 1/95):
            rows = []
            for k, s in sorted(data.items()):
                ix = np.flatnonzero(s["p"][:, col] <= alpha)
                if len(ix):
                    i = int(ix[0]); rows.append({"key": k, "fold": meta[k]["fold"], "filter_pass": meta[k]["filter_pass"],
                      "end": int(s["ends"][i]), "tag": str(s["tags"][i]), "raw": float(s["raw"][i, col]),
                      "z": float(s["z"][i, col]), "p": float(s["p"][i, col])})
            out[name][str(alpha)] = {"all": len(rows), "filtered": sum(r["filter_pass"] is True for r in rows), "events": rows}
    return out


def experiment(normal, checked, folder, budget, source_sha, pre, frozen=None):
    old, meta, prior, episodes, inventory, parent_log = context(checked, normal)
    if not pre["cost_gate_pass"]: raise ValueError("preflight not passed")
    combined, states, records = {}, {}, {}
    counts = {**old["reference_counts"], **{n: [] for n in ev.NEW}}
    for f in range(3):
        directory = folder/f"fold{f}"; directory.mkdir()
        models = pre["models"][str(f)] if normal else frozen["folds"][str(f)]["models"]
        if models != pre["models"][str(f)]: raise ValueError("changed frozen transforms")
        data, roles, costs = raw_fold(f, normal, meta, prior, episodes, inventory, checked, budget, models, old)
        states[str(f)] = {}; ratios = {}
        if normal:
            rec = old["folds"][str(f)]
            old_data = ai.unpack(ai.npz_bytes(checked.read(parent.OUT/f"calibrate/fold{f}/look_streams.npz", rec["streams_sha256"])))
            old_columns = oldmath.CELLS
        else: old_data, old_columns = prior, ev.BASELINES
        for j, name in enumerate(ev.NEW):
            oldcol = old_columns.index(name.removesuffix("_floor"))
            for k, s in data.items():
                for field in ("ends", "tags", "ordinals"): np.testing.assert_array_equal(s[field], old_data[k][field])
                if np.any(s["raw"][:, j] > old_data[k]["raw"][:, oldcol]+1e-9): raise ValueError("floor increased raw penalty")
            ss = {k: ae.stream(k, s, s["raw"][:, j]) for k, s in data.items()}
            cal = (ae.calibrate(name, [ss[k] for k in roles["fit"]], [ss[k] for k in roles["cal"]]) if normal
                   else trm3_g.calibration_from_state(frozen["calibrations"][str(f)][name]))
            if cal.n_reference != old["reference_counts"]["S"][f] or cal.horizon["censored_endpoints"]:
                raise ValueError("reference count/horizon changed")
            counts[name].append(cal.n_reference); states[str(f)][name] = cal.state_dict()
            for k, s in ss.items():
                z, p = ae.score(name, s, cal)
                data[k].setdefault("z", np.empty_like(data[k]["raw"]))[:, j] = z
                data[k].setdefault("p", np.empty_like(data[k]["raw"]))[:, j] = p
            ratios[name] = {}
            for role in (("fit", "cal", "eval") if normal else ("eval",)):
                export(directory/f"{name}_{role}.jsonl", data, roles[role], name, j, f, role, roles["fit"], models)
                if normal:
                    a = np.concatenate([data[k]["raw"][:, j] for k in roles[role]])
                    b = np.concatenate([old_data[k]["raw"][:, oldcol] for k in roles[role]])
                    ratio = np.divide(a, b, out=np.ones_like(a), where=b > 0)
                    ratios[name][role] = {"n_looks": len(a), "raw_ratio_quantiles_look_weighted_descriptive": np.quantile(ratio, [0, .1, .5, .9, 1]).tolist()}
        record = {"fit_keys": roles["fit"], "cal_keys": roles["cal"], "eval_keys": roles["eval"],
                  "models": models, "costs": costs, "normal_raw_ratio": ratios}
        record["streams_sha256"] = ai.save_streams(directory/"look_streams.npz", data, ev.NEW)
        ai.write_json(directory/"record.json", record); records[str(f)] = record
        combined.update(c0.combine({k: prior[k] for k in roles["eval"]}, {k: data[k] for k in roles["eval"]}))
        del old_data, data; budget.check()
    if set(combined) != set(meta): raise ValueError("missing evaluated episode")
    ai.write_json(folder/"episode_metadata.json", meta); ai.save_streams(folder/"look_streams.npz", combined, ev.CELLS)
    if normal:
        points = ev.freeze_points(meta, {k: s["p"] for k, s in combined.items()}, counts)
        for mode, levels in old["workpoints"].items():
            for level, cells in levels.items():
                for n in ev.BASELINES:
                    if points["points"][mode][level][n] != cells[n]: raise ValueError("old workpoint changed")
        for den in points["grids"]:
            for n in ev.BASELINES:
                if points["grids"][den][n] != old["grids"][den][n]: raise ValueError("old normal grid changed")
        manifest = {"source_manifest_sha256": source_sha, "parent_threshold_sha256": c0.PARENT_THRESHOLD_SHA,
                    "calibrations": states, "folds": records, "reference_counts": counts,
                    "workpoints": points["points"], "grids": points["grids"], "fold_table": old["fold_table"],
                    "length_cutpoints": old["length_cutpoints"], "normal_input_sha256": checked.hashes,
                    "normal_floor_tail_diagnostics": floor_tails(meta, combined), "normal_output_sha256": parent.hashes(folder),
                    "primary": "matched_all/0.01/W_full_floor_minus_W_full", "floor_ratio": math.FLOOR,
                    "role": "normal-first fixed spectrum ablation; adaptive full-episode G-dev development"}
        ai.write_json(folder/"threshold_manifest.json", manifest)
        return pf.digest((folder/"threshold_manifest.json").read_bytes())
    normals = ai.unpack(ai.npz_bytes(checked.read(OUT/"calibrate/look_streams.npz", frozen["normal_output_sha256"]["look_streams.npz"])))
    for k, s in normals.items():
        for field in ("ends", "tags", "ordinals", "raw", "z", "p"): np.testing.assert_array_equal(s[field], combined[k][field])
    if counts != frozen["reference_counts"]: raise ValueError("new reference count changed")
    result, ledger = ev.evaluate(meta, combined, frozen["workpoints"], counts)
    old_result = checked.json(parent.OUT/"score/result.json", parent_log["output_sha256"]["result.json"])
    old_ledger = checked.json(parent.OUT/"score/alarm_ledger.json", parent_log["output_sha256"]["alarm_ledger.json"])
    for mode, levels in old_result["readings"].items():
        for level, cells in levels.items():
            for n in ev.BASELINES:
                if result["readings"][mode][level][n] != cells[n] or ledger[f"{mode}/{level}"][n] != old_ledger[f"{mode}/{level}"][n]:
                    raise ValueError("old reading/first-alarm replay failed")
    result.update(normal_replay_exact=True, old_thirteen_raw_z_p_and_readings_exact=True)
    ai.write_json(folder/"result.json", result); ai.write_json(folder/"alarm_ledger.json", ledger)
    return None


def prior_log(checked, stage, sha, source_sha):
    log = checked.json(OUT/stage/"run_manifest.json", sha)
    if log["status"] != "completed" or log["source_manifest_sha256"] != source_sha: raise ValueError("earlier stage incomplete")
    for p, h in log["input_sha256"].items(): checked.read(ROOT/p, h)
    for p, h in log["output_sha256"].items(): checked.read(OUT/stage/p, h)
    return log


def run(stage, source_sha, pre_sha=None, threshold_sha=None, cal_sha=None):
    start = time.monotonic(); normal = stage != "score"; guard = Guard(normal); guard.install()
    checked = pf.CheckedInputs(guard); verify_sources(source_sha, checked)
    previous = RESERVE_SECONDS; frozen = pre = None
    if stage != "preflight":
        log = prior_log(checked, "preflight", pre_sha, source_sha); previous += log["elapsed_seconds"]
        pre = checked.json(OUT/"preflight/result.json", log["output_sha256"]["result.json"])
    if not normal:
        log = prior_log(checked, "calibrate", cal_sha, source_sha); previous += log["elapsed_seconds"]
        frozen = checked.json(OUT/"calibrate/threshold_manifest.json", threshold_sha)
        if log["threshold_manifest_sha256"] != threshold_sha or frozen["source_manifest_sha256"] != source_sha:
            raise ValueError("wrong threshold freeze")
    budget = parent.Budget(previous); budget.start = start
    folder = OUT/stage; folder.mkdir(exist_ok=False); status, error, thsha = "failed", None, threshold_sha
    try:
        torch.set_num_threads(1); torch.set_num_interop_threads(1)
        with ExitStack() as scope:
            for owner, name in ((oldmath, "fit"), (oldmath, "moments")):
                scope.enter_context(patch.object(owner, name, prohibit))
            if stage != "preflight": scope.enter_context(patch.object(math, "derive", prohibit))
            if not normal:
                for owner, name in ((math, "floored"), (ae, "calibrate"), (trm3_g, "calibrate_g"),
                                     (trm3_g, "fit_channel_standardiser"), (ev, "freeze_points")):
                    scope.enter_context(patch.object(owner, name, prohibit))
            if stage == "preflight": preflight(checked, folder, budget)
            else:
                returned = experiment(normal, checked, folder, budget, source_sha, pre, frozen)
                if normal: thsha = returned
        verify_sources(source_sha, checked); checked.verify_again(); budget.check(); status = "completed"
    except Exception as exc:
        error = repr(exc); raise
    finally:
        log = {"status": status, "error": error, "source_manifest_sha256": source_sha, "threshold_manifest_sha256": thsha,
               "preflight_log_sha256": pre_sha, "calibration_log_sha256": cal_sha,
               "input_sha256": checked.hashes, "output_sha256": parent.hashes(folder), "previous_seconds": previous,
               "elapsed_seconds": time.monotonic()-start, "peak_gib": pf.rss(), "access_guard": guard.summary()}
        ai.write_json(folder/"run_manifest.json", log)
        print(json.dumps({k: log[k] for k in ("status", "error", "elapsed_seconds", "previous_seconds", "peak_gib", "threshold_manifest_sha256")}), flush=True)
        print(json.dumps({"run_manifest_sha256": pf.digest((folder/"run_manifest.json").read_bytes())}), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("stage", choices=("freeze", "preflight", "calibrate", "score"))
    for n in ("source", "pre-log", "threshold", "cal-log"): p.add_argument(f"--{n}-sha")
    a = p.parse_args()
    if a.stage == "freeze": freeze()
    else:
        if not a.source_sha or (a.stage != "preflight" and not a.pre_log_sha) or (a.stage == "score" and (not a.threshold_sha or not a.cal_log_sha)):
            p.error("required frozen SHA missing")
        run(a.stage, a.source_sha, a.pre_log_sha, a.threshold_sha, a.cal_log_sha)
