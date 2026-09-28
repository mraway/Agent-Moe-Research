"""A03-C0: shared identity-C1 on immutable A03 W raw scores; no route/model reads."""
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
from research_v4 import codex_g_alg_a01_eval as ae, codex_g_alg_a03_math as math
from research_v4 import codex_g_alg_a03_run_v1_1 as parent
from research_v4 import codex_g_alg_a03c0_eval as ev

ROOT, OLD = pf.ROOT, parent.OUT
OUT = pf.BASE/"alg_a03c0_raw_calibration_v1"
PARENT_SOURCE_SHA = "90f4cbc2da6697fb622db32f000a59aa6f05794e2417d8d62af575e99e7a7a3c"
PARENT_THRESHOLD_SHA = "01f1fd1d56c3c13787621b67582dfffb27de2157c7d1a8129475d2648325b45c"
PARENT_LOGS = {"calibrate": "29a85b4bbe81df03a7f09497805edf6b15ea8a38d5a684f842ec29ff540bce62",
               "score": "3e7b9c1943ebffb30602222907b0f089e78854c540247bd0e02590bd4ed77c45"}
D_REPORT = "docs/research_v4/codex_g_alg_a03d_report.md"
D_REPORT_SHA = "893c1370807927bc057326f8f5b7b83d1e825447447448010aa25af12850ccc5"
EXTRA = ("docs/research_v4/codex_g_alg_a03c0_spec_v1.md", D_REPORT,
         "scripts/research_v4/codex_g_alg_a03c0_run.py", "scripts/research_v4/codex_g_alg_a03c0_eval.py",
         "scripts/research_v4/codex_g_alg_a03c0_audit.py", "tests/test_research_v4_codex_g_alg_a03c0.py")
BASE_W = tuple(n.removesuffix("_raw") for n in ev.NEW)


class Guard(parent.Guard):
    def check_path(self, path):
        p = super().check_path(path)
        forbidden = p.suffix == ".safetensors" or p.name == "trace.json" or (p.name.startswith("model_") and p.suffix == ".npz")
        if self.normal and any(r == p or r in p.parents for r in (OUT/"score", OUT/"audit", pf.BASE/"alg_a03d_timing_tail_v1/run")):
            forbidden = True
        if forbidden:
            self.blocked_attempts += 1
            raise PermissionError("A03-C0 refuses raw traces/models/routes or mixed-arm normal-stage inputs")
        return p


def parent_sources(checked):
    state = checked.json(OLD/"execution_source_manifest.json", PARENT_SOURCE_SHA)
    for p, h in state["source_sha256"].items(): checked.read(ROOT/p, h)
    return state["source_sha256"]


def freeze():
    guard = Guard(True); guard.install(); checked = pf.CheckedInputs(guard)
    checked.read(ROOT/D_REPORT, D_REPORT_SHA)
    sources = {**parent_sources(checked), **pf.source_snapshot(), **{p: pf.digest((ROOT/p).read_bytes()) for p in EXTRA}}
    OUT.mkdir(exist_ok=False)
    ai.write_json(OUT/"source_manifest.json", {"source_sha256": sources, "created_utc": datetime.now(timezone.utc).isoformat(),
                  "parent_source_sha256": PARENT_SOURCE_SHA, "parent_threshold_sha256": PARENT_THRESHOLD_SHA,
                  "budget_seconds": 600, "memory_limit_gib": 2, "cpu_threads": 1,
                  "role": "normal-first adaptive G-dev raw-calibration ablation; not independent confirmation"})
    print(json.dumps({"source_manifest_sha256": pf.digest((OUT/"source_manifest.json").read_bytes())}), flush=True)


def verify_sources(sha, checked):
    manifest = checked.json(OUT/"source_manifest.json", sha)
    for p, h in manifest["source_sha256"].items(): checked.read(ROOT/p, h)
    if not set(pf.source_snapshot()).issubset(manifest["source_sha256"]): raise ValueError("unfrozen dependency")
    parent_sources(checked); checked.read(ROOT/D_REPORT, D_REPORT_SHA)


def prohibit(*args, **kwargs): raise AssertionError("A03-C0 never refits raw geometry or standardized scales")


def calibrate_identity(name, fit, cal):
    return trm3_g.calibrate_g(fit, cal, ae.config(name), view=trm3_g.view_of("V1"), statistic=name,
                             pool="g_dev_target_rotation", tag_scope="message", standardise=False,
                             force_h=ae.FULL_H, bucket_size=32, min_bucket_traces=30,
                             min_channel_windows=30, min_channel_traces=10, pooled_fallback=True)


def load_parent(checked, normal):
    state = checked.json(OLD/"calibrate/threshold_manifest.json", PARENT_THRESHOLD_SHA)
    stage = "calibrate" if normal else "score"
    log = checked.json(OLD/stage/"run_manifest.json", PARENT_LOGS[stage])
    if log["status"] != "completed" or log["source_manifest_sha256"] != PARENT_SOURCE_SHA:
        raise ValueError("changed/incomplete A03")
    meta = checked.json(OLD/stage/"episode_metadata.json", log["output_sha256"]["episode_metadata.json"])
    data = ai.npz_bytes(checked.read(OLD/stage/"look_streams.npz", log["output_sha256"]["look_streams.npz"]))
    if list(data["columns"]) != list(ev.BASELINES): raise ValueError("old columns changed")
    streams = ai.unpack(data)
    expected = dict(clean=192, benign_control=192, benign_lexical=24)
    if not normal: expected.update(attack=352, legitimate_refusal=24)
    if Counter(r["variant"] for r in meta.values()) != expected or set(meta) != set(streams): raise ValueError("cohort changed")
    if any(r["fold"] != state["fold_table"][r["scenario"]] for r in meta.values()): raise ValueError("fold drift")
    if not normal and sum(len(s["ends"]) for s in streams.values()) != 190284: raise ValueError("look coverage changed")
    return state, meta, streams, log


def role_keys(meta, record, outer):
    roles = {"fit": sorted(k for k, r in meta.items() if r["fold"] == (outer+1)%3 and r["variant"] in pf.NORMALS and r["filter_pass"] is True),
             "cal": sorted(k for k, r in meta.items() if r["fold"] == (outer+2)%3 and r["variant"] in pf.NORMALS and r["filter_pass"] is True),
             "eval": sorted(k for k, r in meta.items() if r["fold"] == outer)}
    if record["fit_keys"] != roles["fit"] or record["cal_keys"] != roles["cal"]: raise ValueError("role keys changed")
    groups = {role: {meta[k]["scenario"] for k in keys} for role, keys in roles.items()}
    if any(groups[a] & groups[b] for a, b in (("fit", "cal"), ("fit", "eval"), ("cal", "eval"))):
        raise ValueError("scenario role leakage")
    return roles


def export(path, data, keys, name, source_col, fold, role, fit_keys):
    with path.open("x") as f:
        for k in keys:
            s = data[k]
            row = {"schema": "dataset-g-look-scores-1.0.0", "statistic": name, "batch": "g_dev", "key": k,
                   "trace_id": k.split("|", 1)[-1], "view": "V1", "tag_scope": "message", "window_width": 8,
                   "ends": s["ends"].tolist(), "scores": s["raw"][:, source_col].tolist(),
                   "tags": list(map(str, s["tags"])), "ordinals": s["ordinals"].tolist(),
                   "fit_pool_sha256": ai.fit_hash(fit_keys), "config": {"outer_fold": fold, "role": role,
                   "representation": "W", "structure": name.removeprefix("W_").removesuffix("_raw"),
                   "layers": list(range(24)), "window_summary": "sqrt(mean8/24)", "dimension": 768,
                   "rho": .5, "eta": .01, "geometry_condition": ["channel"], "standardise": False,
                   "standardisation": "identity", "calibration": "shared_C1_full_path_max",
                   "observation": "M7_complete_episode", "geometry_refit": False,
                   "geometry_source_threshold_sha256": PARENT_THRESHOLD_SHA}}
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+"\n")


def combine(prior, fresh):
    if set(prior) != set(fresh): raise ValueError("missing new score")
    return {k: {**{f: np.asarray(s[f]) for f in ("ends", "tags", "ordinals")},
                **{f: np.column_stack((s[f], fresh[k][f])) for f in ("raw", "z", "p")}} for k, s in prior.items()}


def peak_channels(data, keys, column):
    return dict(Counter(str(data[k]["tags"][int(np.argmax(data[k]["raw"][:, column]))]) for k in keys if len(data[k]["ends"])))


def floor_tails(meta, data):
    out = {}
    for name in (*BASE_W, *ev.NEW):
        col = ev.CELLS.index(name); out[name] = {}
        for alpha in (1/105, 1/96, 1/95):
            rows = []
            for k, s in sorted(data.items()):
                ix = np.flatnonzero(s["p"][:, col] <= alpha)
                if not len(ix): continue
                i = int(ix[0]); rows.append({"key": k, "fold": meta[k]["fold"], "filter_pass": meta[k]["filter_pass"],
                                             "end": int(s["ends"][i]), "tag": str(s["tags"][i]),
                                             "raw": float(s["raw"][i, col]), "z": float(s["z"][i, col]), "p": float(s["p"][i, col])})
            out[name][str(alpha)] = {"all": len(rows), "filtered": sum(r["filter_pass"] is True for r in rows), "events": rows}
    return out


def experiment(normal, checked, folder, budget, source_sha, frozen=None):
    old, meta, prior, parent_log = load_parent(checked, normal)
    if not normal and frozen["parent_threshold_sha256"] != PARENT_THRESHOLD_SHA: raise ValueError("wrong geometry freeze")
    fresh, states, records = {}, {}, {}
    counts = dict(old["reference_counts"]); counts.update({n: [] for n in ev.NEW})
    for outer in range(3):
        budget.check(); record = old["folds"][str(outer)]; roles = role_keys(meta, record, outer)
        directory = folder/f"fold{outer}"; directory.mkdir()
        if normal:
            data = ai.unpack(ai.npz_bytes(checked.read(OLD/f"calibrate/fold{outer}/look_streams.npz", record["streams_sha256"])))
            columns = math.CELLS
        else: data, columns = {k: prior[k] for k in roles["eval"]}, ev.BASELINES
        local = {k: {"raw": np.empty((len(prior[k]["ends"]), 3)), "z": np.empty((len(prior[k]["ends"]), 3)),
                     "p": np.empty((len(prior[k]["ends"]), 3))} for k in roles["eval"]}
        states[str(outer)], channel_mix = {}, {}
        for j, name in enumerate(ev.NEW):
            base = name.removesuffix("_raw"); ci = columns.index(base)
            build = lambda k: ae.stream(k, data[k], data[k]["raw"][:, ci])
            cal = (calibrate_identity(name, [build(k) for k in roles["fit"]], [build(k) for k in roles["cal"]]) if normal
                   else trm3_g.calibration_from_state(frozen["calibrations"][str(outer)][name]))
            states[str(outer)][name] = cal.state_dict(); counts[name].append(cal.n_reference)
            if cal.n_reference != old["reference_counts"][base][outer] or cal.horizon["censored_endpoints"]:
                raise ValueError("calibration count/horizon changed")
            for k in roles["eval"]:
                s = build(k); z, p = ae.score(name, s, cal)
                np.testing.assert_array_equal(z, s.scores)
                np.testing.assert_array_equal(s.scores, prior[k]["raw"][:, ev.BASELINES.index(base)])
                local[k]["raw"][:, j] = s.scores; local[k]["z"][:, j] = z; local[k]["p"][:, j] = p
                for f in ("ends", "tags", "ordinals"): np.testing.assert_array_equal(data[k][f], prior[k][f])
            export_roles = ("fit", "cal", "eval") if normal else ("eval",)
            channel_mix[name] = {}
            for role in export_roles:
                export(directory/f"{name}_{role}.jsonl", data, roles[role], name, ci, outer, role, roles["fit"])
                if normal: channel_mix[name][role] = peak_channels(data, roles[role], ci)
        fresh.update(local)
        records[str(outer)] = {"fit_keys": roles["fit"], "cal_keys": roles["cal"], "eval_keys": roles["eval"],
                               "raw_path_peak_channels": channel_mix, "source_streams_sha256": record["streams_sha256"] if normal else parent_log["output_sha256"]["look_streams.npz"]}
        ai.write_json(directory/"record.json", records[str(outer)])
        print(json.dumps({"stage": "calibrate" if normal else "score", "fold_complete": outer,
                          "normal_reference_count": counts[ev.NEW[0]][-1], "rss_gib": pf.rss()}), flush=True)
        if normal: del data
    combined = combine(prior, fresh)
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
        manifest = {"source_manifest_sha256": source_sha, "parent_threshold_sha256": PARENT_THRESHOLD_SHA,
                    "calibrations": states, "folds": records, "reference_counts": counts,
                    "workpoints": points["points"], "grids": points["grids"], "fold_table": old["fold_table"],
                    "length_cutpoints": old["length_cutpoints"], "normal_input_sha256": checked.hashes,
                    "normal_floor_tail_diagnostics": floor_tails(meta, combined),
                    "normal_output_sha256": parent.hashes(folder), "primary": "matched_all/0.01/W_full_raw_minus_W_full",
                    "role": "normal-first fixed raw identity calibration; adaptive G-dev development"}
        ai.write_json(folder/"threshold_manifest.json", manifest)
        return pf.digest((folder/"threshold_manifest.json").read_bytes())
    normals = ai.unpack(ai.npz_bytes(checked.read(OUT/"calibrate/look_streams.npz", frozen["normal_output_sha256"]["look_streams.npz"])))
    for k, s in normals.items():
        for f in ("ends", "tags", "ordinals", "raw", "z", "p"): np.testing.assert_array_equal(s[f], combined[k][f])
    if counts != frozen["reference_counts"]: raise ValueError("new reference count drift")
    result, ledger = ev.evaluate(meta, combined, frozen["workpoints"], counts)
    old_result = checked.json(OLD/"score/result.json", parent_log["output_sha256"]["result.json"])
    old_ledger = checked.json(OLD/"score/alarm_ledger.json", parent_log["output_sha256"]["alarm_ledger.json"])
    for mode, levels in old_result["readings"].items():
        for level, cells in levels.items():
            for n in ev.BASELINES:
                if result["readings"][mode][level][n] != cells[n] or ledger[f"{mode}/{level}"][n] != old_ledger[f"{mode}/{level}"][n]:
                    raise ValueError("old result/first-alarm replay failed")
    result.update(normal_replay_exact=True, old_thirteen_raw_z_p_and_readings_exact=True)
    ai.write_json(folder/"result.json", result); ai.write_json(folder/"alarm_ledger.json", ledger)
    return None


def run(stage, source_sha, threshold_sha=None, cal_log_sha=None):
    start = time.monotonic(); normal = stage == "calibrate"; guard = Guard(normal); guard.install()
    checked = pf.CheckedInputs(guard); verify_sources(source_sha, checked); frozen = None; previous = 0.
    if not normal:
        log = checked.json(OUT/"calibrate/run_manifest.json", cal_log_sha)
        if log["status"] != "completed" or log["source_manifest_sha256"] != source_sha: raise ValueError("normal freeze incomplete")
        frozen = checked.json(OUT/"calibrate/threshold_manifest.json", threshold_sha)
        if frozen["source_manifest_sha256"] != source_sha or log["threshold_manifest_sha256"] != threshold_sha: raise ValueError("wrong normal freeze")
        previous = log["elapsed_seconds"]
    budget = parent.Budget(previous); folder = OUT/stage; folder.mkdir(exist_ok=False)
    status, error, thsha = "failed", None, threshold_sha
    try:
        torch.set_num_threads(1); torch.set_num_interop_threads(1)
        with ExitStack() as scope:
            for owner, name in ((math, "fit"), (math, "moments"), (pf, "routes"), (trm3_g, "fit_channel_standardiser")):
                scope.enter_context(patch.object(owner, name, prohibit))
            if not normal:
                scope.enter_context(patch.object(trm3_g, "calibrate_g", prohibit))
                scope.enter_context(patch.object(ev, "freeze_points", prohibit))
            returned = experiment(normal, checked, folder, budget, source_sha, frozen)
            if normal: thsha = returned
        verify_sources(source_sha, checked); checked.verify_again(); budget.check(); status = "completed"
    except Exception as exc:
        error = repr(exc); raise
    finally:
        log = {"status": status, "error": error, "source_manifest_sha256": source_sha, "threshold_manifest_sha256": thsha,
               "input_sha256": checked.hashes, "output_sha256": parent.hashes(folder), "previous_seconds": previous,
               "elapsed_seconds": time.monotonic()-start, "peak_gib": pf.rss(), "access_guard": guard.summary()}
        ai.write_json(folder/"run_manifest.json", log)
        print(json.dumps({k: log[k] for k in ("status", "error", "elapsed_seconds", "previous_seconds", "peak_gib", "threshold_manifest_sha256")}), flush=True)
        print(json.dumps({"run_manifest_sha256": pf.digest((folder/"run_manifest.json").read_bytes())}), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("stage", choices=("freeze", "calibrate", "score"))
    p.add_argument("--source-sha"); p.add_argument("--threshold-sha"); p.add_argument("--cal-log-sha"); a = p.parse_args()
    if a.stage == "freeze": freeze()
    else:
        if not a.source_sha or (a.stage == "score" and (not a.threshold_sha or not a.cal_log_sha)): p.error("required frozen SHA missing")
        run(a.stage, a.source_sha, a.threshold_sha, a.cal_log_sha)
