"""A02 bounded normal-first full detector ablation; never runs a generation model."""
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
from research_v4 import codex_g_alg_a01_audit as old_audit
from research_v4 import codex_g_alg_a02_math as math, codex_g_alg_a02_eval as ev

ROOT, OUT = ai.ROOT, ai.BASE/"alg_a02_pooled_reference_v1"
OLD_SOURCE_SHA = "1415877e8860f4fe807bce4d31ba47affcd92c0c8210cfa17076a41838a5de55"
OLD_THRESHOLD_SHA = "f521b01d33a315cde32f9446e378b8470f6bc240e6d4c7d59dc8f125300a5f7b"
OLD_LOGS = {"calibrate": "b13601ded124728d9cdfa9f24b6a1852537d93ee89289400f67719f61f46b2e7",
            "score": "7eb3738b013fc8bf6d33db2f11aca29d32972e3cf8d76c7d91dd17bcc83e84ee"}
M11_AUDIT = ai.BASE/"mechanism_m11_full_routing_v1/audit/checks.json"
M11_AUDIT_SHA = "e30deb3e8acd762b74ba0304d23c8face4f5d3c5ef95ea44dd2cb3ce4c4570e6"
EXTRA = ("docs/research_v4/codex_g_alg_a02_spec_v1.md",
         "docs/research_v4/codex_g_mech_m11_report.md",
         "scripts/research_v4/codex_g_alg_a02_math.py", "scripts/research_v4/codex_g_alg_a02_eval.py",
         "scripts/research_v4/codex_g_alg_a02_run.py", "scripts/research_v4/codex_g_alg_a02_audit.py",
         "tests/test_research_v4_codex_g_alg_a02_full.py")


class Guard(ai.Guard):
    def check_path(self, path):
        p = super().check_path(path)
        if self.normal and any(root == p or root in p.parents for root in
                               (ai.OUT/"score", ai.OUT/"posthoc_risk_v1", OUT/"score", OUT/"audit")):
            self.blocked_attempts += 1
            raise PermissionError("A02 normal stage refuses mixed-arm evaluated artifacts")
        return p


def snapshot():
    return {**pf.source_snapshot(), **{p: pf.digest((ROOT/p).read_bytes()) for p in EXTRA}}


def freeze():
    guard = Guard(True); guard.install()
    body = M11_AUDIT.read_bytes()
    if pf.digest(body) != M11_AUDIT_SHA: raise ValueError("M11 audit snapshot changed")
    OUT.mkdir(exist_ok=False)
    ai.write_json(OUT/"source_manifest.json", {"source_sha256": snapshot(), "created_utc": datetime.now(timezone.utc).isoformat(),
                  "head_provenance": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  "m11_audit_sha256": M11_AUDIT_SHA, "cpu_threads": 1, "budget_seconds": 600, "memory_limit_gib": 2,
                  "evidence_role": "adaptive G-dev full-episode development; not confirmation"})
    print(json.dumps({"source_manifest_sha256": pf.digest((OUT/"source_manifest.json").read_bytes())}), flush=True)


def verify_sources(sha):
    body = (OUT/"source_manifest.json").read_bytes()
    if pf.digest(body) != sha: raise ValueError("source manifest changed")
    sources = json.loads(body)["source_sha256"]
    for p, h in sources.items():
        if pf.digest((ROOT/p).read_bytes()) != h: raise ValueError(f"frozen source changed: {p}")
    if not set(pf.source_snapshot()).issubset(sources): raise ValueError("unfrozen imported dependency")
    return sources


def previous_seconds(stages):
    total = 0.
    for s in stages:
        r = json.loads((OUT/s/"run_manifest.json").read_text())
        if r["status"] != "completed": raise ValueError("previous stage incomplete")
        total += r["elapsed_seconds"]
    return total


class Budget:
    def __init__(self, previous=0): self.start = time.monotonic(); self.previous = previous
    def check(self):
        elapsed = self.previous+time.monotonic()-self.start
        if elapsed > 600 or pf.rss() > 2:
            raise RuntimeError(f"resource limit: cumulative seconds={elapsed:.2f}, peakGiB={pf.rss():.3f}")


def hashes(directory):
    return {str(p.relative_to(directory)): pf.digest(p.read_bytes()) for p in sorted(directory.rglob("*"))
            if p.is_file() and p.name != "run_manifest.json"}


def old_baseline(checked, normal, meta, m7_prior):
    source = checked.json(ai.OUT/"source_manifest.json", OLD_SOURCE_SHA)
    for p, h in source["source_sha256"].items(): checked.read(ROOT/p, h)
    state = checked.json(ai.OUT/"calibrate/threshold_manifest.json", OLD_THRESHOLD_SHA)
    stage = "calibrate" if normal else "score"
    log = checked.json(ai.OUT/stage/"run_manifest.json", OLD_LOGS[stage])
    z = ai.npz_bytes(checked.read(ai.OUT/stage/"look_streams.npz", log["output_sha256"]["look_streams.npz"]))
    if list(z["columns"]) != list(ae.CELLS): raise ValueError("old columns changed")
    data = ai.unpack(z)
    if set(data) != set(meta): raise ValueError("old cohort changed")
    result = {}
    for key, s in data.items():
        for field in ("ends", "tags", "ordinals"):
            np.testing.assert_array_equal(s[field], m7_prior[key][field])
        for field in ("raw", "p"):
            np.testing.assert_array_equal(s[field][:, :3], m7_prior[key][field])
        result[key] = {**{f: s[f] for f in ("ends", "tags", "ordinals")},
                       **{f: s[f][:, :5] for f in ("raw", "z", "p")}}
    return result, state


def nearest_audit(ps, end, bank, scenario, score, rows, distances):
    """Independent float64 coordinate enumeration, including all donor scenarios."""
    errors = []
    for rep in range(2):
        q = np.sqrt(np.asarray(ps[rep][end-7:end+1], np.float64).mean(0)/24).ravel()
        ds = np.empty(len(bank.ends))
        for lo in range(0, len(ds), 128):
            roots = bank.mean_roots[rep][lo:lo+128].astype(np.float64)
            ds[lo:lo+128] = .5*((roots-q)**2).sum(1)
        mins = [ds[bank.scenario_index == i].min() for i, s in enumerate(bank.scenarios) if s != scenario]
        errors.append(abs(np.mean(sorted(mins)[:3])-score[rep]))
        errors.extend(abs(ds[rows[rep]]-distances[rep]))
    if max(errors) > 2e-6: raise AssertionError("independent nearest audit failed")
    return float(max(errors))


def preflight(checked, budget):
    meta, grid, episodes, inventory = pf.normal_inputs(checked)
    support = math.support_audit(meta, grid)
    if support["unsupported_queries"]: raise ValueError("unsupported normal bank")
    results = []
    for fold, block in support["banks"].items():
        outer = int(fold); budget.check()
        ps = {k: pf.routes(k, episodes, checked, inventory) for k in block["fit_keys"]}
        for tag, info in block["conditions"].items():
            start = time.monotonic()
            bank = math.build_bank([(meta[k]["scenario"], k, grid[k]["ends"][grid[k]["tags"] == tag], ps[k]) for k in info["fit_keys"]])
            build = time.monotonic()-start
            keys = sorted(k for k, r in meta.items() if r["fold"] == outer and r["filter_pass"] is True and tag in grid[k]["tags"])
            key = keys[0]; ends = grid[key]["ends"][grid[key]["tags"] == tag][:64]
            qp = pf.routes(key, episodes, checked, inventory)
            start = time.monotonic(); raw, rows, ds = math.neighbour_scores(qp, ends, bank, meta[key]["scenario"])
            seconds = time.monotonic()-start
            error = nearest_audit(qp, int(ends[0]), bank, meta[key]["scenario"], raw[0], rows[0], ds[0])
            qkeys = [k for k, r in meta.items() if r["fold"] == outer or r["filter_pass"] is True]
            looks = sum(int(np.count_nonzero(grid[k]["tags"] == tag)) for k in qkeys)
            results.append({"fold": outer, "tag": tag, "query_key": key, "query_looks": len(ends),
                            "normal_full_search_looks": looks, "bank_windows": len(bank.ends), "bank_scenarios": len(bank.scenarios),
                            "build_seconds": build, "search_seconds": seconds, "seconds_per_look": seconds/len(ends),
                            "bank_tensor_bytes": sum(a.nbytes for a in bank.state().values()), "independent_error": error})
            print(json.dumps({"preflight": results[-1], "rss_gib": pf.rss()}), flush=True)
            del bank, qp; budget.check()
        del ps
    projected = 1.5*(sum(r["normal_full_search_looks"]*r["seconds_per_look"] for r in results)
                     +190284*max(r["seconds_per_look"] for r in results))+120+time.monotonic()-budget.start
    return {"support": support, "costs": results, "projected_total_seconds": projected,
            "cost_gate_pass": projected <= 600, "all_eval_look_count_provenance": "previously audited M7 full 190284; no attack artifact opened"}


def empty(grid):
    n = len(grid["ends"])
    return {**{f: np.asarray(grid[f]) for f in ("ends", "tags", "ordinals")},
            "raw": np.full((n, 2), np.nan), "donor_rows": np.full((n, 2, 3), -1, np.int64),
            "donor_distances": np.full((n, 2, 3), np.nan)}


def raw_fold(outer, normal, meta, grid, episodes, inventory, checked, directory, budget, frozen=None):
    fit = sorted(k for k, r in meta.items() if r["fold"] == (outer+1)%3 and r["variant"] in pf.NORMALS and r["filter_pass"] is True)
    keys = sorted(k for k, r in meta.items() if r["fold"] == outer or (normal and r["filter_pass"] is True))
    if normal and any(meta[k]["variant"] not in pf.NORMALS for k in keys): raise ValueError("non-normal calibration")
    if not normal and fit != frozen["fit_keys"]: raise ValueError("fit keys changed")
    data = {k: empty(grid[k]) for k in keys}
    conditions = sorted({str(t) for k in keys for t in grid[k]["tags"]})
    ps = {k: pf.routes(k, episodes, checked, inventory) for k in fit} if normal else {}
    banks, costs = {}, []
    for tag in conditions:
        budget.check(); start = time.monotonic()
        if normal:
            donors = [k for k in fit if tag in grid[k]["tags"]]
            bank = math.build_bank([(meta[k]["scenario"], k, grid[k]["ends"][grid[k]["tags"] == tag], ps[k]) for k in donors])
            path = directory/f"bank_{tag}.npz"; sha = ai.save_npz(path, **bank.state())
            info = {"path": str(path.relative_to(OUT)), "sha256": sha, "fit_keys": donors,
                    "windows": len(bank.ends), "scenario_count": len(bank.scenarios),
                    "tensor_bytes": sum(a.nbytes for a in bank.state().values()), "compressed_bytes": path.stat().st_size}
        else:
            info = frozen["banks"][tag]
            bank = math.Bank.restore(ai.npz_bytes(checked.read(OUT/info["path"], info["sha256"])))
        banks[tag] = info
        costs.append({"tag": tag, "build_or_restore_seconds": time.monotonic()-start})
        qkeys = [k for k in keys if tag in grid[k]["tags"]]
        condition_start = time.monotonic()
        for i, key in enumerate(qkeys):
            budget.check(); ix = np.flatnonzero(grid[key]["tags"] == tag)
            qp = ps[key] if key in ps else pf.routes(key, episodes, checked, inventory)
            start = time.monotonic(); raw, rows, ds = math.neighbour_scores(qp, grid[key]["ends"][ix], bank, meta[key]["scenario"])
            costs.append({"tag": tag, "key": key, "looks": len(ix), "search_seconds": time.monotonic()-start})
            data[key]["raw"][ix] = raw; data[key]["donor_rows"][ix] = rows; data[key]["donor_distances"][ix] = ds
            del qp, raw, rows, ds
            if (i+1)%100 == 0: print(json.dumps({"fold": outer, "normal": normal, "tag": tag, "queries_done": i+1, "total": len(qkeys)}), flush=True)
        print(json.dumps({"fold": outer, "normal": normal, "tag_complete": tag, "seconds": time.monotonic()-condition_start, "rss_gib": pf.rss()}), flush=True)
        del bank
    for s in data.values():
        if not np.isfinite(s["raw"]).all() or np.any(s["donor_rows"] < 0): raise ValueError("unscored endpoints")
    return data, {"fit_keys": fit, "banks": banks, "costs": costs}


def prohibit(*args, **kwargs): raise AssertionError("score stage is restore-only")


def export(path, streams, name, col, fold, role, fit, meta, banks):
    with path.open("x") as f:
        for k, s in sorted(streams.items()):
            row = {"schema": "dataset-g-look-scores-1.0.0", "statistic": name, "batch": "g_dev", "key": k,
                   "trace_id": k.split("|", 1)[1], "view": "V1", "tag_scope": "message", "window_width": 8,
                   "ends": s["ends"].tolist(), "scores": s["raw"][:, col].tolist(),
                   "tags": list(map(str, s["tags"])), "ordinals": s["ordinals"].tolist(),
                   "config": {"outer_fold": fold, "role": role, "condition": ["channel"], "k_distinct_scenarios": 3,
                              "representation": name, "layers": list(range(24)), "distance": "squared_Hellinger", "window_summary": "mean8",
                              "self_exclusion": "entire_scenario", "query_block": 64, "bank_block": 2048,
                              "observation": "M7_complete_episode", "condition_banks_sha256": {t: b["sha256"] for t, b in banks.items()}},
                   "fit_pool_sha256": ai.fit_hash([q for q in fit if meta[q]["scenario"] != meta[k]["scenario"]])}
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+"\n")


def experiment(normal, checked, directory, budget, source_sha, frozen=None):
    if normal:
        meta, grid, episodes, inventory = pf.normal_inputs(checked)
        prior3, _, _ = ai.baseline(checked, True)
    else:
        meta, prior3, episodes, inventory, _ = ai.score_inputs(checked); grid = prior3
    prior, old = old_baseline(checked, normal, meta, prior3)
    counts = {n: old["reference_counts"][n] for n in ev.BASELINES}
    counts.update({n: [] for n in math.CELLS})
    combined, states, folds = {}, {}, {}
    with ExitStack() as scope:
        if not normal:
            for owner, name in ((math, "build_bank"), (am, "build_bank"), (ae, "calibrate"), (trm3_g, "calibrate_g")):
                scope.enter_context(patch.object(owner, name, prohibit))
        for outer in range(3):
            folder = directory/f"fold{outer}"; folder.mkdir()
            data, record = raw_fold(outer, normal, meta, grid, episodes, inventory, checked, folder, budget,
                                    None if normal else frozen["folds"][str(outer)])
            fit = record["fit_keys"]
            cal_keys = sorted(k for k, r in meta.items() if r["fold"] == (outer+2)%3 and r["filter_pass"] is True and r["variant"] in pf.NORMALS)
            state = {}
            for j, name in enumerate(math.CELLS):
                streams = {k: ae.stream(k, s, s["raw"][:, j]) for k, s in data.items()}
                calibration = (ae.calibrate(name, [streams[k] for k in fit], [streams[k] for k in cal_keys]) if normal else
                               trm3_g.calibration_from_state(frozen["calibrations"][str(outer)][name]))
                counts[name].append(calibration.n_reference)
                if calibration.n_reference != counts["S"][outer] or calibration.horizon["censored_endpoints"] != 0:
                    raise ValueError("calibration coverage changed")
                state[name] = calibration.state_dict()
                for k, s in streams.items():
                    start = time.monotonic(); z, p = ae.score(name, s, calibration)
                    record["costs"].append({"key": k, "statistic": name, "looks": len(z), "standardize_and_score_seconds": time.monotonic()-start})
                    data[k].setdefault("z", np.empty_like(data[k]["raw"]))[:, j] = z
                    data[k].setdefault("p", np.empty_like(data[k]["raw"]))[:, j] = p
            states[str(outer)] = state; record["cal_keys"] = cal_keys
            record["streams_sha256"] = ai.save_streams(folder/"look_streams.npz", data, math.CELLS)
            ai.write_json(folder/"record.json", record); folds[str(outer)] = record
            roles = [("eval", [k for k in data if meta[k]["fold"] == outer])]
            if normal: roles += [("fit", fit), ("cal", cal_keys)]
            for role, keys in roles:
                for j, name in enumerate(math.CELLS):
                    export(folder/f"{name}_{role}.jsonl", {k: data[k] for k in keys}, name, j, outer, role, fit, meta, record["banks"])
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
        for n in ev.BASELINES:
            if points["grids"]["filtered"][n] != old["grid"][n]: raise ValueError("old normal grid not replayed")
        manifest = {"source_manifest_sha256": source_sha, "calibrations": states, "folds": folds,
                    "workpoints": points["points"], "grids": points["grids"], "reference_counts": counts,
                    "normal_input_sha256": checked.hashes, "normal_output_sha256": hashes(directory),
                    "fold_table": old["fold_table"], "length_cutpoints": [219, 382], "evidence_role": "normal-first G-dev adaptive development"}
        ai.write_json(directory/"threshold_manifest.json", manifest)
        return pf.digest((directory/"threshold_manifest.json").read_bytes())
    normals = ai.unpack(ai.npz_bytes(checked.read(OUT/"calibrate/look_streams.npz", frozen["normal_output_sha256"]["look_streams.npz"])))
    for k, s in normals.items():
        for f in ("raw", "z", "p", "ends", "tags", "ordinals"): np.testing.assert_array_equal(s[f], combined[k][f])
    if counts != frozen["reference_counts"]: raise ValueError("changed calibration counts")
    result, ledger = ev.evaluate(meta, combined, frozen["workpoints"], counts)
    result.update(normal_replay_exact=True, old_five_cells_replayed_exact=True)
    ai.write_json(directory/"result.json", result); ai.write_json(directory/"alarm_ledger.json", ledger)
    return None


def run(stage, source_sha, threshold_sha=None):
    start = time.monotonic(); normal = stage != "score"
    guard = Guard(normal); guard.install(); verify_sources(source_sha)
    checked = pf.CheckedInputs(guard)
    previous = previous_seconds([] if stage == "preflight" else ["preflight"] if normal else ["preflight", "calibrate"])
    budget = Budget(previous); frozen = None
    if stage != "preflight":
        log = json.loads((OUT/"preflight/run_manifest.json").read_text())
        pre = checked.json(OUT/"preflight/result.json", log["output_sha256"]["result.json"])
        if not pre["cost_gate_pass"]: raise RuntimeError("preflight projected cost exceeds approved limit")
    if not normal:
        if not threshold_sha: raise ValueError("threshold SHA required")
        frozen = checked.json(OUT/"calibrate/threshold_manifest.json", threshold_sha)
        log = json.loads((OUT/"calibrate/run_manifest.json").read_text())
        if frozen["source_manifest_sha256"] != source_sha or log["threshold_manifest_sha256"] != threshold_sha:
            raise ValueError("normal freeze mismatch")
    directory = OUT/stage; directory.mkdir(exist_ok=False)
    ai.write_json(directory/"started.json", {"source_manifest_sha256": source_sha, "threshold_manifest_sha256": threshold_sha,
                  "utc": datetime.now(timezone.utc).isoformat()})
    try:
        if stage == "preflight":
            result = preflight(checked, budget); ai.write_json(directory/"result.json", result)
            print(json.dumps({"cost_gate_pass": result["cost_gate_pass"], "projected_total_seconds": result["projected_total_seconds"]}), flush=True)
        else:
            new_sha = experiment(normal, checked, directory, budget, source_sha, frozen)
            if normal: threshold_sha = new_sha; print(json.dumps({"threshold_manifest_sha256": threshold_sha}), flush=True)
        verify_sources(source_sha); checked.verify_again(); budget.check()
        ai.write_json(directory/"run_manifest.json", {"status": "completed", "source_manifest_sha256": source_sha,
                      "threshold_manifest_sha256": threshold_sha, "input_sha256": checked.hashes, "output_sha256": hashes(directory),
                      "elapsed_seconds": time.monotonic()-start, "previous_stage_seconds": previous, "peak_rss_gib": pf.rss(),
                      "access_guard": guard.summary()})
        print(json.dumps({"stage": stage, "status": "completed", "seconds": time.monotonic()-start, "cumulative_seconds": previous+time.monotonic()-start,
                          "rss_gib": pf.rss()}), flush=True)
    except Exception as exc:
        ai.write_json(directory/"failure.json", {"status": "failed", "type": type(exc).__name__, "message": str(exc),
                      "elapsed_seconds": time.monotonic()-start, "previous_seconds": previous, "access_guard": guard.summary(), "input_sha256": checked.hashes})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("freeze", "preflight", "calibrate", "score"), required=True)
    parser.add_argument("--source-sha256"); parser.add_argument("--threshold-sha256")
    args = parser.parse_args(); torch.set_num_threads(1)
    if args.stage == "freeze": freeze()
    elif not args.source_sha256: parser.error("source SHA required")
    else: run(args.stage, args.source_sha256, args.threshold_sha256)
