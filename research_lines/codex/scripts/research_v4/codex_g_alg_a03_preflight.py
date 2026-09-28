"""A03 normal-only support, covariance and resource preflight. No attack scoring."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import subprocess
import time
import numpy as np
import torch

from research_v4 import codex_g_alg_a01_math as am, codex_g_alg_a01_io as ai
from research_v4 import codex_g_alg_a01_preflight as pf
from research_v4 import codex_g_alg_a02_run as previous
from research_v4 import codex_g_alg_a02_math as pm
from research_v4 import codex_g_alg_a03_math as math

ROOT, OUT = ai.ROOT, ai.BASE/"alg_a03_joint_covariance_v1"
SPEC = "docs/research_v4/codex_g_alg_a03_spec_v1.md"
SOURCE_SHA_A02 = "ab32e02728b72d26b52e5b171821aad838f0135f8a5c607c1b07d485b4e1c71a"
THRESHOLD_SHA_A02 = "554cadf9d94cd615810c6d3c63dcc0ff7d56b693b41f8b0ccaa89fad5d6763ae"
CAL_LOG_SHA_A02 = "bad55f21ff4fb9a7d2d36fe45a8088f7d84ac99466e2b7e6dbe82acf04129770"
M12_REPORT = "docs/research_v4/codex_g_mech_m12_report.md"
M12_SHA = "d6ffbf0f435a06e25a66ff735bfdbc7250852a4edb3e45e95a86d750db797c1f"
EXTRA = (SPEC, M12_REPORT, "scripts/research_v4/codex_g_alg_a03_math.py",
         "scripts/research_v4/codex_g_alg_a03_preflight.py", "tests/test_research_v4_codex_g_alg_a03.py")


class Guard(previous.Guard):
    def check_path(self, path):
        p = super().check_path(path)
        if self.normal and any(root == p or root in p.parents for root in (OUT/"score", OUT/"audit")):
            self.blocked_attempts += 1
            raise PermissionError("A03 normal stage refuses mixed-arm evaluated artifacts")
        return p


def snapshot():
    return {**pf.source_snapshot(), **{p: pf.digest((ROOT/p).read_bytes()) for p in EXTRA}}


def freeze():
    guard = Guard(True); guard.install()
    if pf.digest((ROOT/M12_REPORT).read_bytes()) != M12_SHA:
        raise ValueError("M12 snapshot changed")
    OUT.mkdir(exist_ok=False)
    ai.write_json(OUT/"preflight_source_manifest.json", {"source_sha256": snapshot(),
                  "created_utc": datetime.now(timezone.utc).isoformat(),
                  "head_provenance": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  "cpu_threads": 1, "budget_seconds": 600, "memory_limit_gib": 2,
                  "role": "normal-only feasibility freeze; NOT full runner or threshold freeze"})
    print(json.dumps({"source_manifest_sha256": pf.digest((OUT/"preflight_source_manifest.json").read_bytes())}), flush=True)


def verify_sources(sha):
    body = (OUT/"preflight_source_manifest.json").read_bytes()
    if pf.digest(body) != sha:
        raise ValueError("A03 preflight manifest changed")
    sources = json.loads(body)["source_sha256"]
    for path, expected in sources.items():
        if pf.digest((ROOT/path).read_bytes()) != expected:
            raise ValueError(f"A03 frozen source changed: {path}")
    if not set(pf.source_snapshot()).issubset(sources):
        raise ValueError("unfrozen imported dependency")
    return sources


def previous_normal(checked):
    source = checked.json(previous.OUT/"source_manifest.json", SOURCE_SHA_A02)
    # Literal verification: a later A03 import must not change A02's historical module set.
    for p, sha in source["source_sha256"].items():
        checked.read(ROOT/p, sha)
    state = checked.json(previous.OUT/"calibrate/threshold_manifest.json", THRESHOLD_SHA_A02)
    log = checked.json(previous.OUT/"calibrate/run_manifest.json", CAL_LOG_SHA_A02)
    if log["status"] != "completed" or state["source_manifest_sha256"] != SOURCE_SHA_A02:
        raise ValueError("A02 normal freeze incomplete or changed")
    return state, log


def load_bank(fold, tag, state, meta, grid, checked):
    info = state["folds"][str(fold)]["banks"][tag]
    bank = pm.Bank.restore(ai.npz_bytes(checked.read(previous.OUT/info["path"], info["sha256"])))
    keys = sorted(k for k, r in meta.items() if r["variant"] in pf.NORMALS and r["filter_pass"] is True
                  and r["fold"] == (fold+1)%3 and tag in grid[k]["tags"])
    if keys != sorted(info["fit_keys"]) or set(map(str, bank.keys)) != set(keys):
        raise ValueError("normal covariance fit pool changed")
    scenarios = tuple(sorted({meta[k]["scenario"] for k in keys}))
    if scenarios != bank.scenarios or len(scenarios) < 3:
        raise ValueError("insufficient/changed scenario support")
    for k in keys:
        mask = bank.keys == k
        np.testing.assert_array_equal(bank.ends[mask], grid[k]["ends"][grid[k]["tags"] == tag])
        np.testing.assert_array_equal(bank.scenario_index[mask], np.full(mask.sum(), scenarios.index(meta[k]["scenario"])))
    for roots in bank.mean_roots:
        if roots.shape != (len(bank.ends), 768) or not np.isfinite(roots).all() or np.any(roots < 0):
            raise ValueError("invalid audited mean roots")
        np.testing.assert_allclose(np.square(roots.astype(np.float64)).sum(1), 1., rtol=0, atol=2e-6)
    return bank, info


def independent_scores(roots, model):
    """Explicit matrix inverse action, independent of whitening score implementation."""
    out = np.empty((len(roots[0]), 6))
    for rep in range(2):
        delta = np.asarray(roots[rep], np.float64)-model.mu[rep]
        cov = model.cov[rep]; d = len(cov); v = float(np.trace(cov)/d)
        diagonal = np.diag(np.diag(cov)+.01*v)
        full = .5*cov+np.diag(.5*np.diag(cov)+.01*v)
        block = np.zeros_like(full)
        for start in range(0, d, model.layer_width):
            block[start:start+model.layer_width, start:start+model.layer_width] = full[start:start+model.layer_width, start:start+model.layer_width]
        for structure, matrix in enumerate((diagonal, block, full)):
            out[:, 2*structure+rep] = np.sum(delta*np.linalg.solve(matrix, delta.T).T, axis=1)/d
    return out


def preflight(checked, budget):
    meta, grid, episodes, inventory = pf.normal_inputs(checked)
    state, _ = previous_normal(checked)
    support = pm.support_audit(meta, grid)
    if support["unsupported_queries"]:
        raise ValueError("unsupported normal channel")
    results = []
    for fold in range(3):
        for tag in sorted(support["banks"][str(fold)]["conditions"]):
            budget.check(); start = time.monotonic()
            bank, info = load_bank(fold, tag, state, meta, grid, checked)
            load_seconds = time.monotonic()-start
            weights = math.balanced_weights(bank.keys, bank.scenario_index)
            start = time.monotonic(); model = math.fit(bank.mean_roots, weights)
            fit_seconds = time.monotonic()-start
            diagnostics = math.diagnostics(model)
            # Independent mixture moments: scenario -> episode -> window, without weights helper.
            mean_error = 0.
            for rep, roots in enumerate(bank.mean_roots):
                means = [np.mean([roots[bank.keys == k].astype(np.float64).mean(0)
                                  for k in sorted(set(bank.keys[bank.scenario_index == group]))], axis=0)
                         for group in range(len(bank.scenarios))]
                expected = np.mean(means, axis=0)
                mean_error = max(mean_error, float(np.max(abs(expected-model.mu[rep]))))
            if mean_error > 1e-12:
                raise ValueError("independent balanced mean audit failed")
            candidates = sorted(k for k, r in meta.items() if r["fold"] == fold and r["filter_pass"] is True and tag in grid[k]["tags"])
            if not candidates:
                raise ValueError("missing preflight normal query")
            key = candidates[0]; ends = grid[key]["ends"][grid[key]["tags"] == tag][:64]
            ps = pf.routes(key, episodes, checked, inventory)
            start = time.monotonic(); roots = [am.features(p, ends)[1] for p in ps]
            raw = math.score(roots, model); score_seconds = time.monotonic()-start
            take = sorted({0, len(ends)-1})
            literal = independent_scores([r[take] for r in roots], model)
            score_error = float(np.max(abs(literal-raw[take])))
            np.testing.assert_allclose(literal, raw[take], rtol=1e-10, atol=1e-10)
            restored = math.Model.restore(model.state())
            np.testing.assert_array_equal(math.score(roots, restored), raw)
            queries = [k for k, r in meta.items() if r["fold"] == fold or r["filter_pass"] is True]
            row = {"fold": fold, "tag": tag, "fit_episodes": len(set(bank.keys)), "fit_scenarios": len(bank.scenarios),
                   "fit_looks": len(bank.ends), "query_key": key, "query_looks": len(ends),
                   "normal_full_looks": sum(int(np.count_nonzero(grid[k]["tags"] == tag)) for k in queries),
                   "load_seconds": load_seconds, "fit_seconds": fit_seconds, "score_seconds": score_seconds,
                   "seconds_per_look": score_seconds/len(ends), "tensor_bytes": sum(a.nbytes for a in model.state().values()),
                   "independent_balanced_mean_error": mean_error, "independent_score_error": score_error,
                   "normal_bank_sha256": info["sha256"], "diagnostics": diagnostics}
            results.append(row)
            print(json.dumps({"preflight": row, "rss_gib": pf.rss()}), flush=True)
            del bank, model, restored, ps, roots; budget.check()
    normal_looks = sum(r["normal_full_looks"] for r in results)
    if normal_looks != 263374:
        raise ValueError("full normal coverage changed")
    # Fastest fit time is never used to extrapolate slow channels/episodes.
    projected = (1.5*(sum(r["fit_seconds"]+r["load_seconds"] for r in results)
                      +(normal_looks+190284)*max(r["seconds_per_look"] for r in results))
                 +120+time.monotonic()-budget.start)
    return {"normal_episodes": 408, "filtered_episodes": 293, "banks": results,
            "full_normal_looks": normal_looks, "projected_total_seconds": projected,
            "cost_gate_pass": projected <= 600,
            "all_eval_look_count": 190284, "all_eval_look_count_source": "previously audited M7/A02 report; no attack artifact opened",
            "claim": "normal-only numerical/support feasibility; no new FAR, recall or detection claim"}


def run(sha):
    start = time.monotonic(); guard = Guard(True); guard.install(); verify_sources(sha)
    checked = pf.CheckedInputs(guard); budget = previous.Budget()
    folder = OUT/"preflight"; folder.mkdir(exist_ok=False)
    try:
        result = preflight(checked, budget)
        ai.write_json(folder/"result.json", result)
        verify_sources(sha); checked.verify_again(); budget.check()
        ai.write_json(folder/"run_manifest.json", {"status": "completed", "source_manifest_sha256": sha,
                      "input_sha256": checked.hashes, "output_sha256": previous.hashes(folder),
                      "elapsed_seconds": time.monotonic()-start, "peak_rss_gib": pf.rss(), "access_guard": guard.summary()})
        print(json.dumps({"status": "completed", "seconds": time.monotonic()-start,
                          "cost_gate_pass": result["cost_gate_pass"], "projected_total_seconds": result["projected_total_seconds"],
                          "result_sha256": pf.digest((folder/"result.json").read_bytes()), "rss_gib": pf.rss()}), flush=True)
    except Exception as exc:
        ai.write_json(folder/"failure.json", {"status": "failed", "type": type(exc).__name__, "message": str(exc),
                      "elapsed_seconds": time.monotonic()-start, "input_sha256": checked.hashes, "access_guard": guard.summary()})
        raise


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stage", choices=("freeze", "preflight"), required=True); p.add_argument("--source-sha256")
    args = p.parse_args(); torch.set_num_threads(1)
    if args.stage == "freeze": freeze()
    elif not args.source_sha256: p.error("source SHA required")
    else: run(args.source_sha256)
