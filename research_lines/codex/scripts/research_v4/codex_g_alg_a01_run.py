"""A01 normal-first full-episode experiment. Existing caches, CPU one thread only."""
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

ROOT, OUT = ai.ROOT, ai.OUT
EXTRA_SOURCES = (
    "docs/research_v4/codex_g_alg_a01_execution_v1.md",
    "docs/research_v4/codex_g_alg_a01_preflight_fix_v1_1.md",
    "scripts/research_v4/codex_g_alg_a01_run.py", "scripts/research_v4/codex_g_alg_a01_io.py",
    "scripts/research_v4/codex_g_alg_a01_eval.py", "scripts/research_v4/codex_g_alg_a01_audit.py",
    "tests/test_research_v4_codex_g_alg_a01_full.py", "tests/test_research_v4_codex_g_alg_a01_preflight_v1_1.py",
)


def snapshot():
    return {**pf.source_snapshot(), **{p: pf.digest((ROOT/p).read_bytes()) for p in EXTRA_SOURCES}}


def source_freeze():
    OUT.mkdir(parents=True, exist_ok=False)
    ai.write_json(OUT/"source_manifest.json", {"source_sha256": snapshot(),
                 "head_provenance": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                 "created_utc": datetime.now(timezone.utc).isoformat(), "cpu_threads": 1,
                 "budget_seconds": 2400, "memory_limit_gib": 2, "evidence_role": "G-dev adaptive development; not confirmation"})
    print(json.dumps({"source_manifest_sha256": pf.digest((OUT/"source_manifest.json").read_bytes())}), flush=True)


def verify_sources(expected):
    body = (OUT/"source_manifest.json").read_bytes()
    if pf.digest(body) != expected: raise ValueError("source manifest digest mismatch")
    frozen = json.loads(body)["source_sha256"]
    for p, sha in frozen.items():
        if pf.digest((ROOT/p).read_bytes()) != sha: raise ValueError(f"frozen source changed: {p}")
    # Newly imported project modules cannot evade the explicit source scope.
    if not set(pf.source_snapshot()).issubset(frozen): raise ValueError("unfrozen project dependency")
    return frozen


class Budget:
    def __init__(self, previous=0):
        self.start = time.monotonic(); self.previous = previous

    def check(self):
        elapsed = self.previous+time.monotonic()-self.start
        if elapsed > 2400 or pf.rss() > 2:
            raise RuntimeError(f"approved resource limit reached: seconds={elapsed:.1f}, peakGiB={pf.rss():.3f}")


def hashes(directory):
    return {str(p.relative_to(directory)): pf.digest(p.read_bytes()) for p in sorted(directory.rglob("*"))
            if p.is_file() and p.name != "run_manifest.json"}


def empty_stream(grid):
    n = len(grid["ends"])
    return {**{f: np.asarray(grid[f]) for f in ("ends", "tags", "ordinals")},
            "raw": np.full((n, 4), np.nan), "donor_rows": np.full((n, 4, 3), -1, np.int64),
            "donor_distances": np.full((n, 4, 3), np.nan)}


def replay_baseline(meta, prior, old):
    states = {n: {f: trm3_g.calibration_from_state(s["calibrations"][n])
                  for f, s in old["cells"][n]["folds"].items()} for n in ("S", "CW", "TU")}
    result = {}
    for key, s in sorted(prior.items()):
        zs = []
        for j, name in enumerate(("S", "CW", "TU")):
            z, p = ae.score(name, ae.stream(key, s, s["raw"][:, j]), states[name][str(meta[key]["fold"])])
            np.testing.assert_array_equal(p, s["p"][:, j]); zs.append(z)
        result[key] = {**s, "z": np.column_stack(zs)}
    return result


def raw_fold(outer, stage, meta, grid, episodes, inventory, checked, directory, budget, bank_state=None):
    normal = stage == "calibrate"
    fit_keys = sorted(k for k, r in meta.items() if r["fold"] == (outer+1)%3 and r["filter_pass"] is True and r["variant"] in pf.NORMALS)
    query_keys = sorted(k for k, r in meta.items() if r["fold"] == outer or (normal and r["filter_pass"] is True))
    if normal and any(meta[k]["variant"] not in pf.NORMALS for k in query_keys): raise ValueError("non-normal calibration query")
    if not normal and fit_keys != bank_state["fit_keys"]: raise ValueError("fit keys changed at restore")
    data = {k: empty_stream(grid[k]) for k in query_keys}
    condition_keys = sorted({(str(t), meta[k]["episode_index"]) for k in query_keys for t in grid[k]["tags"]})
    fit_ps = {k: pf.routes(k, episodes, checked, inventory) for k in fit_keys} if normal else None
    banks, costs, sample_pairs = {}, [], []
    for tag, ep_index in condition_keys:
        budget.check(); condition = f"{tag}/ep{ep_index}"; filename = f"bank_{tag}_ep{ep_index}.npz"
        build_start = time.monotonic()
        if normal:
            donors = [k for k in fit_keys if meta[k]["episode_index"] == ep_index and tag in grid[k]["tags"]]
            bank = am.build_bank([(meta[k]["scenario"], k, grid[k]["ends"][grid[k]["tags"] == tag], fit_ps[k]) for k in donors])
            sha = ai.save_npz(directory/filename, **bank.state())
            bank_info = {"path": str((directory/filename).relative_to(OUT)), "sha256": sha,
                         "fit_keys": donors, "scenario_count": len(bank.scenarios), "windows": len(bank.ends),
                         "compressed_bytes": (directory/filename).stat().st_size,
                         "tensor_bytes": sum(v.nbytes for v in bank.state().values())}
        else:
            if condition not in bank_state["banks"]: raise ValueError(f"unseen condition: {condition}")
            bank_info = bank_state["banks"][condition]
            body = checked.read(OUT/bank_info["path"], bank_info["sha256"])
            bank = am.Bank.restore(ai.npz_bytes(body)); del body
        banks[condition] = bank_info
        costs.append({"condition": condition, "build_or_restore_seconds": time.monotonic()-build_start,
                      "bank_windows": len(bank.ends), "scenario_count": len(bank.scenarios)})
        keys = [k for k in query_keys if meta[k]["episode_index"] == ep_index and tag in grid[k]["tags"]]
        condition_start = time.monotonic()
        for qi, key in enumerate(keys):
            budget.check()
            indices = np.flatnonzero(grid[key]["tags"] == tag); ends = grid[key]["ends"][indices]
            ps = fit_ps[key] if normal and key in fit_ps else pf.routes(key, episodes, checked, inventory)
            start = time.monotonic(); raw, rows, distances = am.neighbour_scores(ps, ends, bank, meta[key]["scenario"])
            costs.append({"condition": condition, "key": key, "looks": len(ends), "search_seconds": time.monotonic()-start})
            data[key]["raw"][indices] = raw; data[key]["donor_rows"][indices] = rows; data[key]["donor_distances"][indices] = distances
            if qi == 0:
                sample_pairs.append({"condition": condition, "query_key": key, "indices": [int(indices[0]), int(indices[-1])]})
            del ps, raw, rows, distances
            if (qi+1) % 50 == 0:
                print(json.dumps({"stage": stage, "fold": outer, "condition": condition, "queries_done": qi+1, "queries": len(keys)}), flush=True)
        print(json.dumps({"stage": stage, "fold": outer, "condition_complete": condition,
                          "queries": len(keys), "seconds": round(time.monotonic()-condition_start, 2), "rss_gib": pf.rss()}), flush=True)
        del bank
    for s in data.values():
        if not np.isfinite(s["raw"]).all() or np.any(s["donor_rows"] < 0): raise ValueError("unscored endpoints")
    return data, {"fit_keys": fit_keys, "banks": banks, "costs": costs, "audit_queries": sample_pairs}


def prohibit(*args, **kwargs):
    raise AssertionError("score stage is restore-only: building or fitting forbidden")


def run(stage, source_sha, threshold_sha=None):
    start = time.monotonic(); normal = stage == "calibrate"
    guard = ai.Guard(normal); guard.install(); sources = verify_sources(source_sha)
    checked = pf.CheckedInputs(guard)
    frozen, previous = None, 0
    if not normal:
        if threshold_sha is None: raise ValueError("score requires threshold SHA")
        frozen = checked.json(OUT/"calibrate/threshold_manifest.json", threshold_sha)
        if frozen["source_manifest_sha256"] != source_sha: raise ValueError("source/threshold freeze mismatch")
        old_log = json.loads((OUT/"calibrate/run_manifest.json").read_text())
        if old_log["status"] != "completed" or old_log["threshold_manifest_sha256"] != threshold_sha: raise ValueError("normal stage incomplete")
        previous = old_log["elapsed_seconds"]
    budget = Budget(previous)
    directory = OUT/stage; directory.mkdir(exist_ok=False)
    ai.write_json(directory/"started.json", {"source_manifest_sha256": source_sha, "threshold_manifest_sha256": threshold_sha,
                  "utc": datetime.now(timezone.utc).isoformat()})
    try:
        if normal:
            meta, grid, episodes, inventory = pf.normal_inputs(checked)
            support = am.support_audit(meta, grid)
            if support["unsupported_queries"]: raise ValueError("normal bank support failed")
            ai.write_json(directory/"support.json", support)
            prior, old, _ = ai.baseline(checked, True)
        else:
            meta, prior, episodes, inventory, old = ai.score_inputs(checked); grid = prior
        prior = replay_baseline(meta, prior, old)
        states, combined, fold_records = {}, {}, {}
        counts = {n: [old["cells"][n]["folds"][str(f)]["calibrations"][n]["n_reference"] for f in range(3)]
                  for n in ("S", "CW", "TU")}
        for n in am.CELLS: counts[n] = []
        with ExitStack() as scope:
            if not normal:
                for owner, name in ((am, "build_bank"), (ae, "calibrate"), (trm3_g, "calibrate_g")):
                    scope.enter_context(patch.object(owner, name, prohibit))
            for outer in range(3):
                folder = directory/f"fold{outer}"; folder.mkdir()
                data, record = raw_fold(outer, stage, meta, grid, episodes, inventory, checked, folder, budget,
                                         None if normal else frozen["folds"][str(outer)])
                fit_keys = record["fit_keys"]
                cal_keys = sorted(k for k, r in meta.items() if r["fold"] == (outer+2)%3 and r["filter_pass"] is True and r["variant"] in pf.NORMALS)
                calibrations = {}
                for j, name in enumerate(am.CELLS):
                    streams = {k: ae.stream(k, s, s["raw"][:, j]) for k, s in data.items()}
                    cal = (ae.calibrate(name, [streams[k] for k in fit_keys], [streams[k] for k in cal_keys]) if normal else
                           trm3_g.calibration_from_state(frozen["calibrations"][str(outer)][name]))
                    counts[name].append(cal.n_reference)
                    if cal.n_reference != counts["S"][outer] or cal.horizon["censored_endpoints"] != 0: raise ValueError("calibration contract changed")
                    calibrations[name] = cal.state_dict()
                    for key, s in streams.items():
                        z, p = ae.score(name, s, cal)
                        data[key].setdefault("z", np.empty_like(data[key]["raw"]))[:, j] = z
                        data[key].setdefault("p", np.empty_like(data[key]["raw"]))[:, j] = p
                states[str(outer)] = calibrations
                record["cal_keys"] = cal_keys
                record["streams_sha256"] = ai.save_streams(folder/"look_streams.npz", data, am.CELLS)
                ai.write_json(folder/"record.json", record); fold_records[str(outer)] = record
                for role, keys in (("eval", [k for k in data if meta[k]["fold"] == outer]),
                                   *(([("fit", fit_keys), ("cal", cal_keys)]) if normal else [])):
                    for j, name in enumerate(am.CELLS):
                        ai.export(folder/f"{name}_{role}.jsonl", {k: data[k] for k in keys}, name, j, outer, role, fit_keys, meta, record["banks"])
                for key, s in data.items():
                    if meta[key]["fold"] != outer: continue
                    if key in combined: raise ValueError("episode evaluated twice")
                    combined[key] = {**{f: s[f] for f in ("ends", "tags", "ordinals")},
                                     **{f: np.column_stack((prior[key][f], s[f])) for f in ("raw", "z", "p")}}
                del data
                budget.check()
        if set(combined) != set(meta): raise ValueError("missing evaluated episode")
        ai.write_json(directory/"episode_metadata.json", meta)
        ai.save_streams(directory/"look_streams.npz", combined, ae.CELLS)
        if normal:
            points = ae.freeze_points(meta, {k: s["p"] for k, s in combined.items()}, counts)
            for n in ("S", "CW", "TU"):
                if points["grid"][n] != old["grid"]["cells"][n]["grid"]: raise ValueError("frozen baseline working point grid changed")
            manifest = {"source_manifest_sha256": source_sha, "calibrations": states, "folds": fold_records,
                        "workpoints": points["points"], "grid": points["grid"], "reference_counts": counts,
                        "normal_input_sha256": checked.hashes, "normal_output_sha256": hashes(directory),
                        "fold_table": old["fold_table"], "length_cutpoints": [219, 382], "evidence_role": "normal-first G-dev development"}
            ai.write_json(directory/"threshold_manifest.json", manifest)
            threshold_sha = pf.digest((directory/"threshold_manifest.json").read_bytes())
            print(json.dumps({"threshold_manifest_sha256": threshold_sha, "primary_workpoints": points["points"]["matched"]["0.05"]}), flush=True)
        else:
            normal_file = OUT/"calibrate/look_streams.npz"
            normals = ai.unpack(ai.npz_bytes(checked.read(normal_file, frozen["normal_output_sha256"]["look_streams.npz"])))
            for key, s in normals.items():
                for field in ("raw", "z", "p", "ends", "tags", "ordinals"):
                    np.testing.assert_array_equal(s[field], combined[key][field])
            if counts != frozen["reference_counts"]: raise ValueError("reference counts changed")
            result, ledger = ae.evaluate(meta, combined, frozen["workpoints"], counts)
            result.update(normal_replay_exact=True, threshold_manifest_sha256=threshold_sha)
            ai.write_json(directory/"result.json", result); ai.write_json(directory/"alarm_ledger.json", ledger)
        verify_sources(source_sha); checked.verify_again(); budget.check()
        ai.write_json(directory/"run_manifest.json", {"status": "completed", "source_manifest_sha256": source_sha,
                     "threshold_manifest_sha256": threshold_sha, "input_sha256": checked.hashes,
                     "output_sha256": hashes(directory), "elapsed_seconds": time.monotonic()-start,
                     "previous_stage_seconds": previous, "peak_rss_gib": pf.rss(), "access_guard": guard.summary()})
        print(json.dumps({"stage": stage, "status": "completed", "seconds": time.monotonic()-start, "peak_rss_gib": pf.rss()}), flush=True)
    except Exception as exc:
        ai.write_json(directory/"failure.json", {"status": "failed", "type": type(exc).__name__, "message": str(exc),
                     "source_manifest_sha256": source_sha, "threshold_manifest_sha256": threshold_sha,
                     "input_sha256": checked.hashes, "access_guard": guard.summary(), "elapsed_seconds": time.monotonic()-start})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, choices=("freeze", "calibrate", "score"))
    parser.add_argument("--source-sha256"); parser.add_argument("--threshold-sha256")
    opt = parser.parse_args(); torch.set_num_threads(1)
    if opt.stage == "freeze": source_freeze()
    elif not opt.source_sha256: parser.error("source SHA required")
    else: run(opt.stage, opt.source_sha256, opt.threshold_sha256)
