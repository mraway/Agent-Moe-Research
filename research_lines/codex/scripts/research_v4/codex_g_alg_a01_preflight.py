"""Normal-only A01 coverage and exact-search cost gate. Never scores attacks."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

import numpy as np
import torch
from safetensors.torch import load as load_tensor_bytes

from research_v2 import io_g, trm3_g
from research_v4.codex_access_guard import CodexGAccessGuard
from research_v4 import codex_g_alg_a01_math as am

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/agent_v2/codex_g"
OUT = BASE / "alg_a01_joint_neighbors_v1"
M7 = BASE / "m7_full_episode_v1/calibrate"
CACHE = BASE / "m3_online_mass_v1"
GDEV = ROOT / "artifacts/agent_v2/dataset_g/g_dev"
M7_LOG_SHA = "16e6c8e6c5f3ccd9b9217877bf8c450072a10bbd667db2e6896ecca5928d4222"
M7_THRESHOLD_SHA = "3f46f5f107f8011a717370db020c357fb66e805758e8a00668d0aca47a1775f7"
INVENTORY = BASE / "m4_rare_probability_mechanisms_v1/run_manifest.json"
INVENTORY_SHA = "f1be67a2985b13aab7b6b57e704f1e5d54a34f0c6b021eaee23401871c27e531"
SPEC = "docs/research_v4/codex_g_alg_a01_spec_v1.md"
TESTS = "tests/test_research_v4_codex_g_alg_a01.py"
NORMALS = {"clean", "benign_control", "benign_lexical"}


def digest(body):
    return sha256(body).hexdigest()


def write_json(path, value):
    with path.open("x") as f:
        f.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n")


class A01NormalGuard(CodexGAccessGuard):
    def check_path(self, path):
        resolved = super().check_path(path)
        if "--attack" in str(resolved) or "attack" in resolved.parts:
            self.blocked_attempts += 1
            raise PermissionError("A01 preflight refuses attack content")
        if (BASE / "m7_full_episode_v1/score") in resolved.parents:
            self.blocked_attempts += 1
            raise PermissionError("A01 preflight refuses mixed-arm scored artifacts")
        if resolved.suffix == ".safetensors" and not any(
            (CACHE/part/"g_dev") in resolved.parents for part in ("topk_cache", "logit_cache")
        ):
            self.blocked_attempts += 1
            raise PermissionError("A01 allows existing private G-dev caches only")
        return resolved


class CheckedInputs:
    def __init__(self, guard):
        self.guard = guard
        self.hashes = {}

    def read(self, path, expected):
        path = self.guard.check_path(path)
        body = path.read_bytes()
        if digest(body) != expected:
            raise ValueError(f"frozen input changed: {path.relative_to(ROOT)}")
        self.hashes[str(path.relative_to(ROOT))] = expected
        return body

    def json(self, path, expected):
        return json.loads(self.read(path, expected))

    def verify_again(self):
        for path, expected in self.hashes.items():
            if digest((ROOT/path).read_bytes()) != expected:
                raise ValueError(f"input changed during run: {path}")


def source_snapshot():
    paths = {SPEC, TESTS, "scripts/research_v4/codex_g_alg_a01_preflight.py"}
    for module in tuple(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if not filename:
            continue
        p = Path(filename).resolve()
        if p.suffix == ".py" and any(ROOT/part in p.parents for part in ("src", "scripts")):
            paths.add(str(p.relative_to(ROOT)))
    return {p: digest((ROOT/p).read_bytes()) for p in sorted(paths)}


def freeze():
    # No git writes; source hashes, not unrelated concurrent HEAD changes, are the gate.
    body = {"schema": "codex-g-alg-a01-preflight-source-1.0.0", "source_sha256": source_snapshot(),
            "head_provenance": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "role": "preflight only, not a calibration or confirmation freeze"}
    OUT.mkdir(parents=True, exist_ok=False)
    write_json(OUT/"preflight_source_manifest.json", body)
    print(json.dumps({"preflight_source_manifest_sha256": digest((OUT/"preflight_source_manifest.json").read_bytes()),
                      "source_files": len(body["source_sha256"])}), flush=True)


def checked_sources(expected):
    body = (OUT/"preflight_source_manifest.json").read_bytes()
    if digest(body) != expected:
        raise ValueError("preflight source manifest hash mismatch")
    sources = json.loads(body)["source_sha256"]
    if sources != source_snapshot():
        raise ValueError("scoped source files changed")
    return sources


def normal_inputs(checked):
    log = checked.json(M7/"run_manifest.json", M7_LOG_SHA)
    manifest = checked.json(M7/"threshold_manifest.json", M7_THRESHOLD_SHA)
    get = lambda name: checked.json(M7/name, log["output_sha256"][name])
    meta, contract = get("episode_metadata.json"), get("data_contract.json")
    if len(meta) != 408 or any(r["variant"] not in NORMALS for r in meta.values()):
        raise ValueError("expected frozen 408 normal episodes only")
    if sum(r["filter_pass"] is True for r in meta.values()) != 293:
        raise ValueError("filtered normal denominator changed")
    if any(r["fold"] != manifest["fold_table"][r["scenario"]] for r in meta.values()):
        raise ValueError("fold-table mismatch")
    body = checked.read(M7/"look_streams.npz", log["output_sha256"]["look_streams.npz"])
    with np.load(BytesIO(body), allow_pickle=False) as z:
        arrays = {k: z[k] for k in ("keys", "offsets", "ends", "tags", "ordinals")}
    streams = {str(k): {f: arrays[f][int(arrays["offsets"][i]):int(arrays["offsets"][i+1])]
                       for f in ("ends", "tags", "ordinals")} for i, k in enumerate(arrays["keys"])}
    if set(streams) != set(meta):
        raise ValueError("normal stream keys differ")
    inventory = checked.json(INVENTORY, INVENTORY_SHA)["input_sha256"]
    episodes = {}
    for rel in sorted(contract["opened_trace_or_manifest_paths"]):
        if not rel.endswith("/trace.json"):
            continue
        path = checked.guard.check_path(ROOT/rel)
        if GDEV not in path.parents:
            raise ValueError("trace is not in G-dev")
        trace = checked.json(path, inventory[rel])
        for ep in trace["episodes"]:
            key = f'g_dev|{trace["trace_id"]}#ep{ep["episode_index"]}'
            if key not in meta:
                raise ValueError("unexpected trace episode")
            count = meta[key]["token_count"]
            if ep["generated_token_count"] != count:
                raise ValueError("token count changed")
            tags = io_g.channel_tag_array(io_g.segment_spans(ep["steps"]), count, scope="message")
            step_ids = np.full(count, -1, dtype=np.int64)
            token_ids = np.full(count, -1, dtype=np.int64)
            for step_index, step in enumerate(ep["steps"]):
                start, n = step["global_token_offset"], step["output_token_count"]
                if np.any(step_ids[start:start+n] != -1):
                    raise ValueError("overlapping steps")
                step_ids[start:start+n] = step_index
                events = [e for e in trace["events"] if e["kind"] == "model_generation" and
                          e["episode_index"] == ep["episode_index"] and e["agent_step"] == step["agent_step"]]
                if len(events) != 1 or len(events[0]["output_token_ids"]) != n:
                    raise ValueError("model generation events disagree")
                token_ids[start:start+n] = events[0]["output_token_ids"]
            if np.any(step_ids < 0) or np.any(token_ids < 0):
                raise ValueError("uncovered token positions")
            ends, _, st, ordinals = trm3_g.segmented_windows(torch.ones((count, 1)), tags, trm3_g.view_of("V1"), am.WIDTH)
            for field, actual in (("ends", ends), ("tags", st), ("ordinals", ordinals)):
                np.testing.assert_array_equal(streams[key][field], actual)
            am.valid_ends(ends, tags, step_ids)
            episodes[key] = {"token_ids": token_ids, "tags": tags, "step_ids": step_ids,
                             "stem": f'{trace["trace_id"]}--ep{ep["episode_index"]}'}
    if set(episodes) != set(meta):
        raise ValueError("incomplete normal trace inventory")
    return meta, streams, episodes, inventory


def routes(key, episodes, checked, inventory):
    stem = episodes[key]["stem"]
    arrays = []
    for part, suffix in (("topk_cache", ".safetensors"), ("logit_cache", ".logits.safetensors")):
        path = CACHE/part/"g_dev"/(stem+suffix)
        arrays.append(load_tensor_bytes(checked.read(path, inventory[str(path.relative_to(ROOT))])))
    ids = arrays[0]["top_k_ids"].numpy()
    np.testing.assert_array_equal(arrays[0]["token_ids"].numpy(), episodes[key]["token_ids"])
    logits = arrays[1]["router_logits"].float().numpy()
    return am.representations(ids, logits)


def direct_audit(ps, qe, bank, result):
    """Independent float64 coordinate formula for fixed first/last query donor pairs."""
    scores, rows, distances = result
    error = 0.0
    for qi in sorted({0, len(qe)-1}):
        for ci, cell in enumerate(am.CELLS):
            ri = ci % 2
            for j in range(am.K):
                b = rows[qi, ci, j]
                if ci < 2:
                    a = np.sqrt(ps[ri][qe[qi]-7:qe[qi]+1].astype(np.float64).mean(0)/am.LAYERS).ravel()
                    # Stored mean roots are audited against token roots as well.
                    be = bank.token_ends[b]
                    bp = bank.token_roots[ri][be-7:be+1].astype(np.float64)**2
                    reference = np.sqrt(bp.mean(0))
                else:
                    a = np.sqrt(ps[ri][qe[qi]-7:qe[qi]+1].astype(np.float64)/am.LAYERS).reshape(8, -1)
                    be = bank.token_ends[b]
                    reference = bank.token_roots[ri][be-7:be+1].astype(np.float64)
                distance = .5*((a-reference)**2).sum()/(8 if ci >= 2 else 1)
                error = max(error, abs(distance-distances[qi, ci, j]))
    if error > am.DISTANCE_TOLERANCE:
        raise ValueError("independent coordinate audit failed")
    np.testing.assert_allclose(scores, distances.mean(-1), rtol=0, atol=0)
    return error


def smoke(support, meta, streams, episodes, checked, inventory):
    rows = []
    for outer_s, block in support["banks"].items():
        outer = int(outer_s)
        # Load one fit fold at a time; never materialize ordered 6144-D windows.
        fit_ps = {k: routes(k, episodes, checked, inventory) for k in block["fit_keys"]}
        for name, condition in block["conditions"].items():
            tag, ep_index = condition["channel"], condition["episode_index"]
            eligible = sorted(k for k, r in meta.items() if r["fold"] == outer and r["filter_pass"] is True
                              and r["episode_index"] == ep_index and tag in streams[k]["tags"])
            if not eligible:
                raise ValueError(f"no prescribed normal smoke query for {outer}/{name}")
            key = eligible[0]
            start = time.monotonic()
            bank = am.build_bank([(meta[k]["scenario"], k, streams[k]["ends"][streams[k]["tags"] == tag], fit_ps[k])
                                  for k in condition["fit_keys"]])
            build_seconds = time.monotonic()-start
            ps = routes(key, episodes, checked, inventory)
            qe = streams[key]["ends"][streams[key]["tags"] == tag][:am.QUERY_BLOCK]
            start = time.monotonic()
            result = am.neighbour_scores(ps, qe, bank, meta[key]["scenario"])
            elapsed = time.monotonic()-start
            error = direct_audit(ps, qe, bank, result)
            query_keys = [k for k, r in meta.items() if r["episode_index"] == ep_index and
                          (r["fold"] == outer or r["filter_pass"] is True)]
            normal_looks = sum(np.count_nonzero(streams[k]["tags"] == tag) for k in query_keys)
            row = {"outer_fold": outer, "condition": name, "query_key": key, "smoke_looks": len(qe),
                   "bank_scenarios": len(bank.scenarios), "bank_windows": len(bank.ends),
                   "bank_bytes": sum(v.nbytes for v in bank.state().values()), "build_seconds": build_seconds,
                   "search_seconds": elapsed, "normal_stage_looks": normal_looks,
                   "projected_normal_search_seconds": elapsed/len(qe)*normal_looks,
                   "direct_formula_max_error": error,
                   "donor_scenario_counts": dict(Counter(bank.scenarios[bank.scenario_index[i]] for i in result[1].ravel()))}
            rows.append(row)
            print(json.dumps({"smoke": f"{outer}/{name}", "looks": len(qe), "seconds": round(elapsed, 4),
                              "bank_windows": len(bank.ends), "rss_gib": rss()}), flush=True)
            del bank, ps, result
            if rss() > 2:
                return {"status": "resource_gate_failed", "reason": "measured peak RSS >2 GiB", "cells": rows}
        del fit_ps
    normal_seconds = sum(r["projected_normal_search_seconds"]+r["build_seconds"] for r in rows)
    # Attack endpoint counts by condition are deliberately not read at this stage.
    # Upper-envelope estimate: 190284 publicly reported full G-dev looks at worst
    # measured per-look cost, plus normal fitting/calibration work. Factor two margin.
    worst = max(r["search_seconds"]/r["smoke_looks"] for r in rows)
    conservative_batch = 2*(normal_seconds + 190284*worst)
    return {"status": "resource_gate_failed" if conservative_batch > 600 or rss() > 2 else "preflight_passed",
            "cells": rows, "projected_normal_seconds": normal_seconds,
            "conservative_complete_batch_seconds": conservative_batch,
            "estimate_rule": "2*(all normal fit/cal/eval estimate + 190284*worst condition seconds/look); repeats normal evaluation conservatively",
            "peak_rss_gib": rss(), "resource_limits": {"seconds": 600, "rss_gib": 2, "cpu_threads": 1}}


def rss():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2


def run(expected):
    start = time.monotonic()
    guard = A01NormalGuard(ROOT); guard.install()
    checked_sources(expected)
    stage = OUT/"preflight"; stage.mkdir(exist_ok=False)
    checked = CheckedInputs(guard)
    write_json(stage/"started.json", {"source_manifest_sha256": expected, "utc": datetime.now(timezone.utc).isoformat()})
    try:
        meta, streams, episodes, inventory = normal_inputs(checked)
        support = am.support_audit(meta, streams)
        write_json(stage/"support.json", support)
        print(json.dumps({"support": support["status"], "unsupported_queries": len(support["unsupported_queries"]),
                          "normals": len(meta)}), flush=True)
        result = ({"status": "coverage_failed", "unsupported_queries": len(support["unsupported_queries"])}
                  if support["unsupported_queries"] else smoke(support, meta, streams, episodes, checked, inventory))
        checked_sources(expected); checked.verify_again()
        result.update(elapsed_seconds=time.monotonic()-start, peak_rss_gib=rss(), access_guard=guard.summary(),
                      input_sha256=checked.hashes, source_manifest_sha256=expected,
                      attack_routes_opened=0, models_loaded=0, calibration_fitted=False, detection_metrics_computed=False)
        write_json(stage/"result.json", result)
        print(json.dumps({k: result[k] for k in ("status", "elapsed_seconds", "peak_rss_gib")}), flush=True)
    except Exception as exc:
        write_json(stage/"failure.json", {"status": "failed", "type": type(exc).__name__, "message": str(exc),
                   "input_sha256": checked.hashes, "access_guard": guard.summary(), "source_manifest_sha256": expected})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("freeze", "preflight"), required=True)
    parser.add_argument("--source-sha256")
    opt = parser.parse_args()
    torch.set_num_threads(1)
    if opt.stage == "freeze":
        freeze()
    elif not opt.source_sha256:
        parser.error("preflight requires --source-sha256")
    else:
        run(opt.source_sha256)
