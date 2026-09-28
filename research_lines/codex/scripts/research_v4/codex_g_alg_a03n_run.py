"""A03-N bounded normal-only structural diagnosis, with literal frozen parent inputs."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import time
from unittest.mock import patch
import numpy as np
import torch
from research_v2 import trm3_g
from research_v4 import codex_g_alg_a01_preflight as pf, codex_g_alg_a01_io as ai
from research_v4 import codex_g_alg_a01_math as am, codex_g_alg_a03_math as oldmath
from research_v4 import codex_g_alg_a03_preflight as pre, codex_g_alg_a03_run_v1_1 as parent
from research_v4 import codex_g_alg_a03c0_run as c0, codex_g_alg_a03n_math as math

ROOT, OUT = pf.ROOT, pf.BASE/"alg_a03n_normal_structure_v1"
C0_SOURCE_SHA = "49bf5241413ecbebdb20c8afc0b401112096be1c5577b04829549befa7ae7c6d"
C0_THRESHOLD_SHA = "76db710d17c7e566c2999ab1f42e75b177d72d3cfec7a852a0c1267a44a9c1b9"
C0_REPORT = "docs/research_v4/codex_g_alg_a03c0_report.md"
C0_REPORT_SHA = "71ea68ca819fa9458a8e07bf6d6ddce8f8b10fc15795fc0b2b3604d194c75f87"
EXTRA = ("docs/research_v4/codex_g_alg_a03n_spec_v1.md", C0_REPORT,
         "scripts/research_v4/codex_g_alg_a03n_math.py", "scripts/research_v4/codex_g_alg_a03n_run.py",
         "scripts/research_v4/codex_g_alg_a03n_audit.py", "tests/test_research_v4_codex_g_alg_a03n.py")
CONDITIONS = tuple((f, tag) for f in range(3) for tag in ("analysis", "commentary", "final"))
RESERVE_SECONDS = 60.


class Guard(parent.Guard):
    def __init__(self): super().__init__(True)

    def check_path(self, path):
        p = super().check_path(path)
        if pf.BASE in p.parents and OUT not in p.parents and p != OUT:
            rel = p.relative_to(pf.BASE)
            if "score" in rel.parts or rel.parts[0] in ("alg_a03d_timing_tail_v1", "alg_a02d_normal_tail_v1"):
                self.blocked_attempts += 1
                raise PermissionError("A03-N refuses mixed-arm or posthoc trajectory artifacts")
        return p


def verify_sources(sha, checked):
    manifest = checked.json(OUT/"source_manifest.json", sha)
    for p, h in manifest["source_sha256"].items(): checked.read(ROOT/p, h)
    prior = checked.json(c0.OUT/"source_manifest.json", C0_SOURCE_SHA)
    for p, h in prior["source_sha256"].items(): checked.read(ROOT/p, h)
    checked.read(ROOT/C0_REPORT, C0_REPORT_SHA)
    if not set(pf.source_snapshot()).issubset(manifest["source_sha256"]): raise ValueError("unfrozen dependency")


def freeze():
    guard = Guard(); guard.install(); checked = pf.CheckedInputs(guard)
    prior = checked.json(c0.OUT/"source_manifest.json", C0_SOURCE_SHA)
    for p, h in prior["source_sha256"].items(): checked.read(ROOT/p, h)
    checked.read(ROOT/C0_REPORT, C0_REPORT_SHA)
    sources = {**prior["source_sha256"], **pf.source_snapshot(), **{p: pf.digest((ROOT/p).read_bytes()) for p in EXTRA}}
    OUT.mkdir(exist_ok=False)
    ai.write_json(OUT/"source_manifest.json", {"source_sha256": sources, "parent_source_sha256": C0_SOURCE_SHA,
                  "created_utc": datetime.now(timezone.utc).isoformat(), "cpu_threads": 1,
                  "budget_seconds": 600, "memory_limit_gib": 2, "other_work_reserved_seconds": RESERVE_SECONDS,
                  "role": "normal-only adaptive G-dev diagnostic; no new detector or attack evaluation"})
    print(json.dumps({"source_manifest_sha256": pf.digest((OUT/"source_manifest.json").read_bytes())}), flush=True)


def context(checked):
    threshold, meta, grid, _ = c0.load_parent(checked, True)
    a02, _ = pre.previous_normal(checked)
    tails = checked.json(c0.OUT/"calibrate/threshold_manifest.json", C0_THRESHOLD_SHA)
    return threshold, meta, grid, a02, tails


def selections(threshold, meta, fold_streams, tails):
    rows, missing = [], []
    for outer in range(3):
        roles = c0.role_keys(meta, threshold["folds"][str(outer)], outer)
        for role, keys in roles.items():
            for key in keys:
                s = fold_streams[outer][key]
                for tag in ("analysis", "commentary", "final"):
                    ix = np.flatnonzero(s["tags"] == tag)
                    if not len(ix):
                        missing.append({"fold": outer, "role": role, "key": key, "tag": tag}); continue
                    indices = {"middle": int(ix[len(ix)//2]), "peak": int(ix[np.argmax(s["raw"][ix, 5])])}
                    for event in tails:
                        if role == "eval" and event["key"] == key and event["tag"] == tag:
                            found = np.flatnonzero(s["ends"] == event["end"])
                            if len(found) != 1 or found[0] not in ix: raise ValueError("tail endpoint changed")
                            indices["old_tail"] = int(found[0])
                    for kind, i in indices.items():
                        m = meta[key]
                        if m["variant"] not in pf.NORMALS or (role != "eval" and m["filter_pass"] is not True):
                            raise ValueError("non-normal or unfiltered fit/cal query")
                        rows.append({"fold": outer, "tag": tag, "role": role, "key": key, "kind": kind,
                                     "end": int(s["ends"][i]), "expected_raw": float(s["raw"][i, 5]),
                                     "scenario": m["scenario"], "filter_pass": m["filter_pass"], "variant": m["variant"],
                                     "episode_index": m["episode_index"]})
    ids = [(r["fold"], r["tag"], r["role"], r["key"], r["kind"]) for r in rows]
    if len(set(ids)) != len(ids): raise ValueError("duplicate query id")
    return rows, missing


def build_selection(checked, threshold, meta, grid, tails, budget):
    data = {}
    for outer in range(3):
        rec = threshold["folds"][str(outer)]
        data[outer] = ai.unpack(ai.npz_bytes(checked.read(parent.OUT/f"calibrate/fold{outer}/look_streams.npz", rec["streams_sha256"])))
        for k, s in data[outer].items():
            if k not in meta: raise ValueError("mixed-arm fold stream")
            for field in ("ends", "tags", "ordinals"): np.testing.assert_array_equal(s[field], grid[k][field])
    events = tails["normal_floor_tail_diagnostics"]["W_full"][str(1/95)]["events"]
    rows, missing = selections(threshold, meta, data, events); del data
    unique = sorted({(r["key"], r["end"]) for r in rows}); index = {pair: i for i, pair in enumerate(unique)}
    features = np.empty((len(unique), 768), np.float32)
    for row in rows: row["feature_index"] = index[row["key"], row["end"]]
    pm, pg, episodes, inventory = pf.normal_inputs(checked)
    if set(pm) != set(meta): raise ValueError("normal metadata changed")
    for k in pm:
        for field in ("variant", "filter_pass", "scenario", "fold"):
            if pm[k][field] != meta[k][field]: raise ValueError("normal role changed")
        for field in ("ends", "tags", "ordinals"): np.testing.assert_array_equal(pg[k][field], grid[k][field])
    count = 0
    for key in sorted({k for k, _ in unique}):
        budget.check(); es = np.asarray([e for k, e in unique if k == key], np.int64)
        ps = pf.routes(key, episodes, checked, inventory)
        roots = am.features(ps[1], es)[1]
        features[[index[key, int(e)] for e in es]] = roots
        count += 1
        if count % 100 == 0: print(json.dumps({"normal_features_complete": count}), flush=True)
    np.testing.assert_allclose(np.square(features.astype(np.float64)).sum(1), 1, rtol=0, atol=2e-6)
    return {"queries": rows, "missing_channels": missing, "unique_windows": len(unique),
            "normal_episodes": 408, "filtered_episodes": 293, "feature_episodes": count}, features


def bank_inputs(fold, tag, ctx, checked):
    threshold, meta, grid, a02, _ = ctx
    bank, info = pre.load_bank(fold, tag, a02, meta, grid, checked)
    model_info = threshold["folds"][str(fold)]["models"][tag]
    if info["sha256"] != model_info["source_bank_sha256"]: raise ValueError("bank/model source changed")
    model = oldmath.Model.restore(ai.npz_bytes(checked.read(parent.OUT/model_info["path"], model_info["sha256"])))
    counts = [len({int(s) for s in bank.scenario_index if int(s) % 4 == g}) for g in range(4)]
    if min(counts) < 10: raise ValueError("delete group has fewer than ten scenarios")
    return bank, model, info, model_info


def one_bank(fold, tag, ctx, selection, features, checked, budget, folder):
    start = time.monotonic(); folder.mkdir(exist_ok=False)
    bank, model, bank_info, model_info = bank_inputs(fold, tag, ctx, checked)
    indices = [i for i, r in enumerate(selection["queries"]) if r["fold"] == fold and r["tag"] == tag]
    rows = [selection["queries"][i] for i in indices]
    q = features[[r["feature_index"] for r in rows]]
    lookup = {(str(k), int(e)): i for i, (k, e) in enumerate(zip(bank.keys, bank.ends))}
    overlap = 0
    for root, row in zip(q, rows):
        j = lookup.get((row["key"], row["end"]))
        if j is not None:
            np.testing.assert_array_equal(root, bank.mean_roots[1][j]); overlap += 1
    state, result = math.diagnose(bank.mean_roots[1], bank.keys, bank.scenario_index, model, q, budget.check)
    state["query_indices"] = np.asarray(indices, np.int64)
    expected = np.asarray([r["expected_raw"] for r in rows])
    np.testing.assert_allclose(state["scores"][0], expected, rtol=1e-9, atol=1e-10)
    result.update(fold=fold, tag=tag, query_count=len(rows), root_bank_overlap_checks=overlap,
                  old_raw_max_error=float(np.max(abs(state["scores"][0]-expected))),
                  summaries=math.summarise(rows, state), bank=bank_info, source_model=model_info)
    result["state_sha256"] = ai.save_npz(folder/"state.npz", **state)
    result["seconds"] = time.monotonic()-start
    ai.write_json(folder/"summary.json", result)
    budget.check()
    print(json.dumps({"fold": fold, "tag": tag, "queries": len(rows), "seconds": result["seconds"],
                      "rss_gib": pf.rss(), "cluster_status": [m["centres"]["status"] for m in result["models"]]}), flush=True)
    return result


def prior_log(checked, stage, sha, source_sha):
    log = checked.json(OUT/stage/"run_manifest.json", sha)
    if log["status"] != "completed" or log["source_manifest_sha256"] != source_sha: raise ValueError("earlier stage not complete")
    for p, h in log["input_sha256"].items(): checked.read(ROOT/p, h)
    for p, h in log["output_sha256"].items(): checked.read(OUT/stage/p, h)
    return log


def preflight(ctx, checked, folder, budget):
    selection, features = build_selection(checked, ctx[0], ctx[1], ctx[2], ctx[4], budget)
    ai.write_json(folder/"selection.json", selection); ai.save_npz(folder/"features.npz", roots=features)
    pilot = one_bank(0, "analysis", ctx, selection, features, checked, budget, folder/"pilot")
    scales = {}
    for f, tag in CONDITIONS:
        fit_n = ctx[0]["folds"][str(f)]["models"][tag]["fit_looks"]
        query_n = sum(r["fold"] == f and r["tag"] == tag for r in selection["queries"])
        scales[f"{f}/{tag}"] = max(1., fit_n/pilot["models"][0]["fit_looks"], query_n/pilot["query_count"])
    # One pilot already consumed; 8 remaining bank studies plus all 9 independent audits.
    projected = budget.previous+time.monotonic()-budget.start+2*pilot["seconds"]*(2*sum(scales.values())-1)+120
    result = {"cost_gate_pass": projected <= 600 and pf.rss() <= 2, "projected_total_seconds": projected,
              "pilot_seconds": pilot["seconds"], "scales": scales, "query_count": len(selection["queries"]),
              "unique_windows": selection["unique_windows"], "normal_episodes": 408, "filtered_episodes": 293,
              "role": "normal structural feasibility only, not a detector or multimodality claim"}
    ai.write_json(folder/"result.json", result); return result


def diagnostic(ctx, checked, folder, budget, log):
    get = lambda name: checked.json(OUT/"preflight"/name, log["output_sha256"][name])
    if not get("result.json")["cost_gate_pass"]: raise ValueError("preflight cost gate failed")
    selection = get("selection.json")
    features = ai.npz_bytes(checked.read(OUT/"preflight/features.npz", log["output_sha256"]["features.npz"]))["roots"]
    results = {"0/analysis": get("pilot/summary.json")}
    for f, tag in CONDITIONS[1:]: results[f"{f}/{tag}"] = one_bank(f, tag, ctx, selection, features, checked, budget, folder/f"fold{f}_{tag}")
    ai.write_json(folder/"result.json", {"banks": results, "query_count": len(selection["queries"]),
                  "normal_episodes": 408, "filtered_episodes": 293, "attack_evaluations": 0,
                  "role": "normal-only adaptive structure diagnosis; no new FAR or recall"})


def prohibit(*args, **kwargs): raise AssertionError("A03-N does not train detectors, calibrate or evaluate attacks")


def run(stage, source_sha, pre_log_sha=None):
    start = time.monotonic(); guard = Guard(); guard.install(); checked = pf.CheckedInputs(guard)
    verify_sources(source_sha, checked); previous = RESERVE_SECONDS; log = None
    if stage == "run":
        log = prior_log(checked, "preflight", pre_log_sha, source_sha); previous += log["elapsed_seconds"]
    budget = parent.Budget(previous); folder = OUT/stage; folder.mkdir(exist_ok=False)
    status, error = "failed", None
    try:
        torch.set_num_threads(1); torch.set_num_interop_threads(1)
        with ExitStack() as scope:
            for owner, name in ((oldmath, "fit"), (trm3_g, "calibrate_g"), (trm3_g, "score_episode"), (ai, "score_inputs")):
                scope.enter_context(patch.object(owner, name, prohibit))
            ctx = context(checked)
            if stage == "preflight": preflight(ctx, checked, folder, budget)
            else: diagnostic(ctx, checked, folder, budget, log)
        verify_sources(source_sha, checked); checked.verify_again(); budget.check(); status = "completed"
    except Exception as exc:
        error = repr(exc); raise
    finally:
        log = {"status": status, "error": error, "source_manifest_sha256": source_sha,
               "previous_seconds_including_reserve": previous, "elapsed_seconds": time.monotonic()-start,
               "peak_gib": pf.rss(), "input_sha256": checked.hashes, "output_sha256": parent.hashes(folder),
               "access_guard": guard.summary()}
        ai.write_json(folder/"run_manifest.json", log)
        print(json.dumps({k: log[k] for k in ("status", "error", "elapsed_seconds", "peak_gib")}), flush=True)
        print(json.dumps({"run_manifest_sha256": pf.digest((folder/"run_manifest.json").read_bytes())}), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=("freeze", "preflight", "run"), required=True)
    p.add_argument("--source-sha"); p.add_argument("--pre-log-sha")
    a = p.parse_args()
    if a.stage == "freeze": freeze()
    else:
        if not a.source_sha or (a.stage == "run" and not a.pre_log_sha): p.error("frozen stage hashes required")
        run(a.stage, a.source_sha, a.pre_log_sha)
