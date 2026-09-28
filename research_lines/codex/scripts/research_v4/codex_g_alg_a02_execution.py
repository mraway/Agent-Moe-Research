"""Approved A02 resource-only extension. Frozen statistical code stays untouched."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import subprocess
import time
from unittest.mock import patch
import torch
from research_v4 import codex_g_alg_a02_run as core, codex_g_alg_a02_audit as audit
from research_v4 import codex_g_alg_a01_io as ai, codex_g_alg_a01_preflight as pf

BASE_SHA = "ab32e02728b72d26b52e5b171821aad838f0135f8a5c607c1b07d485b4e1c71a"
PREFLIGHT_SHA = "e679bc1c4dffa76c436841dbb36b565e552b7475e9906c2872c62d7d1bc96d50"
PREFLIGHT_LOG_SHA = "d246da173b297791c574d78a7b5aadf5daac70a7fc496d6ce2f009f58e3409e5"
OUT = core.OUT/"execution_v1"
LIMIT = 1200
EXTRA = ("docs/research_v4/codex_g_alg_a02_execution_v1.md",
         "scripts/research_v4/codex_g_alg_a02_execution.py", "tests/test_research_v4_codex_g_alg_a02_execution.py")


def literal_sources(body, sha):
    if pf.digest(body) != sha: raise ValueError("source manifest SHA mismatch")
    frozen = json.loads(body)["source_sha256"]
    for path, expected in frozen.items():
        if pf.digest((ai.ROOT/path).read_bytes()) != expected: raise ValueError(f"frozen source changed: {path}")
    return frozen


def projection_gate(pre):
    if pre["support"]["unsupported_queries"]: raise ValueError("normal donor support failed")
    if pre["projected_total_seconds"] > LIMIT: raise RuntimeError("projection exceeds approved 1200 seconds")
    return {"approved_limit_seconds": LIMIT, "projection_seconds": pre["projected_total_seconds"],
            "original_600_second_gate": pre["cost_gate_pass"], "approved_gate_pass": True}


def freeze():
    guard = core.Guard(True); guard.install(); checked = pf.CheckedInputs(guard)
    base = literal_sources((core.OUT/"source_manifest.json").read_bytes(), BASE_SHA)
    pre = checked.json(core.OUT/"preflight/result.json", PREFLIGHT_SHA)
    checked.json(core.OUT/"preflight/run_manifest.json", PREFLIGHT_LOG_SHA)
    decision = projection_gate(pre)
    source = {**base, **pf.source_snapshot(), **{p: pf.digest((ai.ROOT/p).read_bytes()) for p in EXTRA}}
    # Do not silently refresh an old source even if a newly imported dependency overlaps it.
    if any(source[p] != h for p, h in base.items()): raise ValueError("old source scope differs")
    OUT.mkdir(exist_ok=False)
    ai.write_json(OUT/"source_manifest.json", {"source_sha256": source, "original_source_manifest_sha256": BASE_SHA,
                  "preflight_result_sha256": PREFLIGHT_SHA, "preflight_log_sha256": PREFLIGHT_LOG_SHA,
                  "created_utc": datetime.now(timezone.utc).isoformat(), "budget": decision,
                  "head_provenance": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ai.ROOT, text=True).strip(),
                  "cpu_threads": 1, "memory_limit_gib": 2, "scope": "resource-only extension; original algorithm/source and preflight unchanged"})
    print(json.dumps({"execution_manifest_sha256": pf.digest((OUT/"source_manifest.json").read_bytes()), "budget": decision}), flush=True)


def verify(sha):
    base = literal_sources((core.OUT/"source_manifest.json").read_bytes(), BASE_SHA)
    source = literal_sources((OUT/"source_manifest.json").read_bytes(), sha)
    if any(source.get(p) != h for p, h in base.items()): raise ValueError("original source chain not preserved")
    if not set(pf.source_snapshot()).issubset(source): raise ValueError("unfrozen execution dependency")
    return source


class Budget20(core.Budget):
    def check(self):
        seconds = self.previous+time.monotonic()-self.start
        if seconds > LIMIT or pf.rss() > 2:
            raise RuntimeError(f"approved A02 resource limit: cumulative_seconds={seconds:.2f}, peakGiB={pf.rss():.3f}")


def run(stage, execution_sha, threshold_sha=None):
    start = time.monotonic(); normal = stage == "calibrate"
    guard = core.Guard(normal); guard.install(); verify(execution_sha)
    checked = pf.CheckedInputs(guard)
    checked.json(core.OUT/"preflight/run_manifest.json", PREFLIGHT_LOG_SHA)
    pre = checked.json(core.OUT/"preflight/result.json", PREFLIGHT_SHA); decision = projection_gate(pre)
    stages = ["preflight"]+([] if normal else ["calibrate"])+(["score"] if stage == "audit" else [])
    previous = core.previous_seconds(stages); budget = Budget20(previous)
    frozen = None
    if not normal:
        if not threshold_sha: raise ValueError("threshold SHA required after normal freeze")
        log = json.loads((core.OUT/"calibrate/run_manifest.json").read_text())
        if log["execution_manifest_sha256"] != execution_sha or log["threshold_manifest_sha256"] != threshold_sha:
            raise ValueError("calibration execution freeze mismatch")
        binding = checked.json(OUT/"threshold_binding.json", log["threshold_binding_sha256"])
        if binding != {"original_source_manifest_sha256": BASE_SHA, "execution_manifest_sha256": execution_sha,
                       "threshold_manifest_sha256": threshold_sha}: raise ValueError("threshold binding mismatch")
        frozen = checked.json(core.OUT/"calibrate/threshold_manifest.json", threshold_sha)
        if frozen["source_manifest_sha256"] != BASE_SHA: raise ValueError("algorithm freeze mismatch")
    if stage == "audit":
        def verify_core(expected):
            if expected != BASE_SHA: raise ValueError("audit algorithm SHA mismatch")
            return verify(execution_sha)
        ai.write_json(OUT/"audit_started.json", {"execution_manifest_sha256": execution_sha,
                      "threshold_manifest_sha256": threshold_sha, "utc": datetime.now(timezone.utc).isoformat()})
        try:
            with patch.object(core, "Budget", Budget20), patch.object(core, "verify_sources", verify_core):
                audit.main(BASE_SHA, threshold_sha)
            verify(execution_sha); checked.verify_again(); budget.check()
            ai.write_json(OUT/"audit_binding.json", {"execution_manifest_sha256": execution_sha,
                          "original_source_manifest_sha256": BASE_SHA, "threshold_manifest_sha256": threshold_sha,
                          "audit_checks_sha256": pf.digest((core.OUT/"audit/checks.json").read_bytes()),
                          "wrapper_elapsed_seconds": time.monotonic()-start, "cumulative_seconds": previous+time.monotonic()-start})
        except Exception as exc:
            ai.write_json(OUT/"audit_failure.json", {"type": type(exc).__name__, "message": str(exc),
                          "elapsed_seconds": time.monotonic()-start, "previous_seconds": previous})
            raise
        return
    directory = core.OUT/stage; directory.mkdir(exist_ok=False)
    ai.write_json(directory/"started.json", {"source_manifest_sha256": BASE_SHA, "execution_manifest_sha256": execution_sha,
                  "threshold_manifest_sha256": threshold_sha, "utc": datetime.now(timezone.utc).isoformat(), "budget": decision})
    try:
        new_sha = core.experiment(normal, checked, directory, budget, BASE_SHA, frozen)
        binding_sha = None
        if normal:
            threshold_sha = new_sha
            ai.write_json(OUT/"threshold_binding.json", {"original_source_manifest_sha256": BASE_SHA,
                          "execution_manifest_sha256": execution_sha, "threshold_manifest_sha256": threshold_sha})
            binding_sha = pf.digest((OUT/"threshold_binding.json").read_bytes())
            print(json.dumps({"threshold_manifest_sha256": threshold_sha, "threshold_binding_sha256": binding_sha}), flush=True)
        verify(execution_sha); checked.verify_again(); budget.check()
        ai.write_json(directory/"run_manifest.json", {"status": "completed", "source_manifest_sha256": BASE_SHA,
                      "execution_manifest_sha256": execution_sha, "threshold_manifest_sha256": threshold_sha,
                      "threshold_binding_sha256": binding_sha, "input_sha256": checked.hashes, "output_sha256": core.hashes(directory),
                      "elapsed_seconds": time.monotonic()-start, "previous_stage_seconds": previous, "peak_rss_gib": pf.rss(),
                      "access_guard": guard.summary(), "budget": decision})
        print(json.dumps({"stage": stage, "status": "completed", "seconds": time.monotonic()-start,
                          "cumulative_seconds": previous+time.monotonic()-start, "rss_gib": pf.rss()}), flush=True)
    except Exception as exc:
        ai.write_json(directory/"failure.json", {"type": type(exc).__name__, "message": str(exc),
                      "execution_manifest_sha256": execution_sha, "threshold_manifest_sha256": threshold_sha,
                      "input_sha256": checked.hashes, "elapsed_seconds": time.monotonic()-start,
                      "previous_seconds": previous, "access_guard": guard.summary()})
        raise


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--stage", choices=("freeze", "calibrate", "score", "audit"), required=True)
    p.add_argument("--execution-sha256"); p.add_argument("--threshold-sha256")
    args = p.parse_args(); torch.set_num_threads(1)
    if args.stage == "freeze": freeze()
    elif not args.execution_sha256: p.error("execution SHA required")
    else: run(args.stage, args.execution_sha256, args.threshold_sha256)
