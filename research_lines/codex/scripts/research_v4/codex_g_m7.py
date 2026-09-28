"""Frozen normal-first full-episode study, using only OPEN G-dev private caches."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from io import BytesIO
import json
import resource
import time
from unittest.mock import patch

import numpy as np
import torch

from research_v2 import io_g, trm3, trm3_g
from research_v4 import codex_g_m3 as m3, codex_g_m6 as m6, run_detectors_g as harness
from research_v4.codex_g_m1 import BASE, CONFIG, EXPECTED_ARMS, LABELS, ROOT, RUN, sha, write_json
from research_v4.codex_g_m7_statistics import register
from research_v4.codex_g_m7_math import CELLS, LEVELS, FULL_H, first, summarize, comparisons, workpoints

OUT = BASE / "m7_full_episode_v1"
VIEW = trm3_g.view_of("V1")
PRIOR_SHA = "dabe500771f212bcc75f5d2075c1588c9a44c502a34ea7ff66430a6c1f9f1077"
PRIOR_LOG_SHA = {"calibrate": "817b2b54cb2734f235dcb7a0d1c5246bcf22ffb7f605e7c5390d2fd43811575b",
                 "score": "fa9de123b15dce0924fa66b5bbdff4d0fea4967539288b3deb8e44e704e1cd2a"}
SOURCES = tuple(sorted(set(m6.SOURCES) | {
    "docs/research_v4/codex_g_paper_evidence_plan.md", "docs/research_v4/codex_g_m7_analysis_spec.md",
    "scripts/research_v4/codex_g_m7.py", "scripts/research_v4/codex_g_m7_math.py",
    "scripts/research_v4/codex_g_m7_statistics.py", "scripts/research_v4/codex_g_m7_audit.py",
    "tests/test_research_v4_codex_g_m7.py"}))


def source_freeze(expected):
    head = harness.git_output("rev-parse", "HEAD")
    if head != expected or harness.git_output("diff", "HEAD", "--name-only"):
        raise ValueError("commit sources and pass exact clean tracked HEAD")
    if not set(SOURCES).issubset(set(harness.git_output("ls-files").splitlines())):
        raise ValueError("all M7 sources must be committed before data access")
    return head, {p: sha((ROOT / p).read_bytes()) for p in SOURCES}


def args_for(stage):
    args = m3.shared_args(stage, OUT)
    args.force_h = args.expect_h = FULL_H
    args.prob_cache_dir = str(m6.M3 / "logit_cache")
    return args


def complete_normal_grid(cells, episodes):
    """Include all attainable p levels, even ones no normal path happened to hit.

    At low FAR, using ONLY observed normal p values can incorrectly force alpha=0
    when a smaller positive level gives zero normal alarms and detects positives.
    The extra levels depend only on frozen normal reference counts, never attacks.
    """
    grid = harness.matched_alpha_inputs(cells, episodes, alpha=.1)
    normal_keys, _ = harness.matching_normal_keys(episodes)
    for name in CELLS:
        counts = [s["calibrations"][name]["n_reference"] for s in cells[name]["_fold_states"].values()]
        levels = {0.} | {k/(n+1) for n in counts for k in range(1, n+2)}
        grid["cells"][name]["grid"] = [{"alpha": a, "measured_far": trm3_g.measured_far(cells[name]["_decisions"], normal_keys, a)} for a in sorted(levels)]
    grid["grid_rule"] = "union of ALL k/(n_cal+1) attainable levels plus zero, normal-frozen reference counts only"
    return grid


@contextmanager
def restore_only():
    with ExitStack() as stack:
        for owner, method in ((trm3_g, "calibrate_g"), (harness, "matched_alpha_inputs"),
                              (trm3_g, "matched_alpha_by_measured_far"),
                              *((trm3_g.STATISTICS[n], "fit") for n in CELLS)):
            stack.enter_context(patch.object(owner, method, m3.forbid_retraining))
        yield


def read_npz(path, expected=None):
    body = path.read_bytes()
    if expected is not None and sha(body) != expected:
        raise ValueError("prior stream hash changed")
    with np.load(BytesIO(body), allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def unpack(z):
    return {str(k): {f: z[f][int(z["offsets"][i]):int(z["offsets"][i+1])]
                    for f in ("ends", "raw", "p", "tags", "ordinals")}
            for i, k in enumerate(z["keys"])}


def saved_arrays(path, saved, cells):
    keys = sorted(cells["S"]["_decisions"])
    arrays = {f: [] for f in ("ends", "raw", "p", "tags", "ordinals")}
    for key in keys:
        base = saved["S"][key]
        for name in CELLS:
            for field in ("ends", "tags", "ordinals"):
                np.testing.assert_array_equal(base[field], saved[name][key][field])
        arrays["ends"].append(base["ends"])
        arrays["raw"].append(np.column_stack([saved[n][key]["raw"] for n in CELLS]))
        arrays["p"].append(np.column_stack([cells[n]["_decisions"][key].p_fused for n in CELLS]))
        arrays["tags"].append(np.array(base["tags"]))
        arrays["ordinals"].append(base["ordinals"])
    result = {"keys": np.array(keys), "columns": np.array(CELLS),
              "offsets": np.cumsum([0] + [len(v) for v in arrays["ends"]]),
              **{f: np.concatenate(v) for f, v in arrays.items()}}
    np.savez_compressed(path, **result)
    return unpack(result)


def coverage_metadata(episodes, streams, old, table, cuts):
    anchors = trm3_g.view_anchors(episodes, VIEW)
    metadata, phase_rows = {}, {}
    for ep in episodes:
        key = trm3.trace_key(ep); stream = streams[key]; ends = stream["ends"]
        expected, _, _, _ = trm3_g.segmented_windows(torch.ones((ep.token_count, 1)), ep.channel_tags, VIEW, 8)
        np.testing.assert_array_equal(ends, expected)
        if ep.token_count >= FULL_H:
            raise ValueError("unbounded-mode sentinel reached; do not truncate")
        a = anchors[key]; e = a.anchor; x = a.x
        previous = old[key]["ends"]
        old_end = int(previous[-1]) if len(previous) else -1
        step_lengths = [int(s.get("output_token_count") or len(s.get("output_token_ids") or ())) for s in ep.step_spans]
        row = {"variant": ep.variant, "filter_pass": ep.filter_pass, "scenario": ep.pair_group_id,
               "family": ep.attack_family_id, "tier": ep.wording_tier, "domain_group": ep.domain_group,
               "injection_channel": ep.channel, "fold": table[ep.pair_group_id], "token_count": ep.token_count,
               "silent": bool(ep.labels.get("silent")), "attack_bearing": trm3_g.injection_present(ep),
               "trajectory_class": ep.labels.get("trajectory_class"), "episode_index": ep.episode_index,
               "length_group": "short" if ep.token_count <= cuts[0] else "medium" if ep.token_count <= cuts[1] else "long",
               "e": e, "x": x, "anchor_reason": a.reason,
               "last_look": int(ends[-1]) if len(ends) else None, "look_count": len(ends),
               "old_h_end": old_end, "old_incomplete_16": bool(x is not None and x+16 > old_end),
               "x_generated": bool(x is not None and x < ep.token_count),
               "complete_16": bool(x is not None and x+16 < ep.token_count),
               "last_look_covers_16": bool(x is not None and len(ends) and x+16 <= ends[-1]),
               "has_hit_look": bool(e is not None and x is not None and np.any((ends >= e) & (ends <= x+16))),
               "stop_reason": ep.stop_reason, "step_stop_reasons": [s.get("stop_reason") for s in ep.step_spans],
               "step_output_lengths": step_lengths, "step_1024_limit_observed": any(n >= 1024 for n in step_lengths),
               "tokens_without_endpoint": ep.token_count - len(ends), "detector_censored_looks": 0}
        metadata[key] = row
        phases = {"whole": np.ones(len(ends), dtype=bool)}
        for tag in ("analysis", "commentary", "final"):
            phases[f"channel/{tag}"] = stream["tags"] == tag
        for label, anchor in (("E", e), ("X", x)):
            for suffix, lo, hi in (("pre", -16, -1), ("at", 0, 16)):
                phases[f"{label}/{suffix}"] = ((ends >= anchor+lo) & (ends <= anchor+hi)) if anchor is not None else np.zeros(len(ends), bool)
        phase_rows[key] = {p: {"looks": int(ix.sum()), "raw_mean": stream["raw"][ix].mean(0).tolist() if ix.any() else None}
                           for p, ix in phases.items()}
    return metadata, phase_rows


def phase_summary(metadata, phases):
    grouped = defaultdict(list)
    for key, row in metadata.items():
        cohort = (f'attack/{row["trajectory_class"]}' if row["attack_bearing"] else
                  "pre_injection" if row["variant"] == "attack" else row["variant"])
        for label, values in phases[key].items():
            if values["looks"]:
                grouped[(cohort, label)].append(values["raw_mean"])
    return {f"{cohort}/{phase}": {"episodes": len(v), "raw_mean_episode_equal": np.mean(v, axis=0).tolist()}
            for (cohort, phase), v in sorted(grouped.items())}


def evaluate(metadata, streams, points):
    readings, ledger, paired = {}, {}, {}
    for mode, levels in points.items():
        readings[mode] = {}; paired[mode] = {}
        for level, values in levels.items():
            block = {}; ledger_key = f"{mode}/{level}"; ledger[ledger_key] = {}
            for col, name in enumerate(CELLS):
                alarms = {k: first(s["ends"], s["p"][:, col], values[name]["alpha"]) for k, s in streams.items()}
                block[name] = {"workpoint": values[name], **summarize(metadata, alarms)}
                ledger[ledger_key][name] = alarms
            if mode == "matched":
                paired[mode][level] = comparisons(metadata, block)
            readings[mode][level] = block
    return readings, ledger, paired


def run(stage, expected, expected_manifest=None):
    start = time.monotonic(); register()
    guard = m6.M6AccessGuard(ROOT, stage); guard.install()
    head, sources = source_freeze(expected)
    stage_dir = OUT / stage
    if stage_dir.exists():
        raise ValueError("refusing to overwrite an existing M7 stage")
    prior = m6.checked_json(m6.OUT / "calibrate/threshold_manifest.json", PRIOR_SHA)
    prior_log = m6.checked_json(m6.OUT / stage / "run_manifest.json", PRIOR_LOG_SHA[stage])
    old = unpack(read_npz(m6.OUT / stage / "look_streams.npz", prior_log["output_sha256"]["look_streams.npz"]))
    inventory = m6.checked_json(m6.INVENTORY, m6.INVENTORY_SHA)["input_sha256"]
    hashes = {}
    def verify(path):
        key = str(path.relative_to(ROOT)); digest = sha(path.read_bytes())
        if digest != inventory[key]:
            raise ValueError(f"audited input changed: {key}")
        hashes[key] = digest
    for path in (CONFIG, LABELS): verify(path)
    scenarios = json.loads(CONFIG.read_text())["scenarios"]
    fixtures = io_g.fixture_map_from_config(CONFIG)
    table = trm3_g.fold_assignment(list(fixtures), folds=3, key="fixture_rank_mod", fixtures=fixtures)
    paths = io_g.iter_trace_paths(RUN)
    selected = m3.normal_trace_paths(paths, scenarios) if stage == "calibrate" else paths
    manifest, states = {}, {}
    if stage == "score":
        if not expected_manifest: raise ValueError("stage 2 requires threshold file SHA")
        manifest = m6.checked_json(OUT / "calibrate/threshold_manifest.json", expected_manifest)
        if manifest["source_sha256"] != sources or manifest["fold_table"] != table:
            raise ValueError("source/folds changed since normal freeze")
        states = manifest["cells"]
    ids = {m3.path_source_id(p) for p in selected}
    for path in selected: verify(path)
    for part in ("topk_cache", "logit_cache"):
        for path in sorted((m6.M3 / part / "g_dev").glob("*.safetensors")):
            if path.name.split("--ep", 1)[0] in ids: verify(path)
    if stage == "score" and any(hashes.get(k) != v for k, v in manifest["normal_input_sha256"].items()):
        raise ValueError("normal inputs changed")
    stage_dir.mkdir(parents=True)
    log = {"status": "started", "stage": stage, "implementation_commit": head, "source_sha256": sources,
           "input_sha256": hashes, "started_utc": datetime.now(timezone.utc).isoformat(),
           "prior_threshold_sha256": PRIOR_SHA, "prior_run_sha256": PRIOR_LOG_SHA[stage],
           "threshold_manifest_sha256": expected_manifest, "evidence_role": "G-dev development only; full generated episodes"}
    write_json(stage_dir / "run_manifest.json", log)
    loaded = {}
    with m3.guarded_loader(guard, selected), patch.object(io_g, "save_file", m6.no_cache_write):
        episodes = io_g.load_g(RUN, labels=LABELS, variants=io_g.NORMAL_VARIANTS if stage == "calibrate" else None,
                              tag_scope="message", variant_overrides="auto", cache_dir=m6.M3 / "topk_cache",
                              verify_tokens=True, manifest=loaded)
        counts = dict(Counter(e.variant for e in episodes))
        assert counts == ({k: v for k, v in EXPECTED_ARMS.items() if k in io_g.NORMAL_VARIANTS} if stage == "calibrate" else EXPECTED_ARMS)
        assert sum(e.filter_pass is True for e in episodes if e.normal) == 293
        pools = harness.fold_pools(episodes, table, folds=3, filtered_only=True, normals_only_eval=stage == "calibrate")
        tertiles = harness.tertiles_from_normals(pools, episodes, filtered_only=True) if stage == "calibrate" else manifest["length_tertiles"]
        cells, saved = {}, {}
        print(json.dumps({"stage": stage, "episodes": counts, "observation_mode": "full_episode"}), flush=True)
        def singles():
            with m6.collect_raw(saved):
                for name in CELLS:
                    print(f"{stage}: {name}", flush=True)
                    cells[name] = harness.run_cell_v32(name, args=args_for(stage), view=VIEW, target_pool=episodes,
                                                      pools=pools, manifest_cell=states.get(name), cutpoints=tertiles["cutpoints"])
                    if stage == "calibrate": states[name] = {"folds": cells[name]["_fold_states"]}
        if stage == "calibrate": singles()
        else:
            with restore_only(): singles()
        for name in ("S", "CW"):
            for fold, state in states[name]["folds"].items():
                assert state["statistics"][name] == prior["cells"][name]["folds"][fold]["statistics"][name]
        for name in CELLS:
            for state in states[name]["folds"].values():
                horizon = state["calibrations"][name]["horizon"]
                assert horizon["censored_endpoints"] == 0 and horizon["H"] == FULL_H
        streams = saved_arrays(stage_dir / "look_streams.npz", saved, cells)
        for key, previous in old.items():
            size = len(previous["ends"])
            np.testing.assert_array_equal(streams[key]["ends"][:size], previous["ends"])
            np.testing.assert_array_equal(streams[key]["raw"][:size, :2], previous["raw"][:, :2])
        metadata, phases = coverage_metadata(episodes, streams, old, table, tertiles["cutpoints"])
        write_json(stage_dir / "episode_metadata.json", metadata)
        write_json(stage_dir / "phase_readouts.json", {"columns": CELLS, "episodes": phases, "summary": phase_summary(metadata, phases),
                   "note": "unmatched descriptive raw score means, not a causal or conditional mechanism contrast"})
        if stage == "calibrate":
            grid = complete_normal_grid(cells, episodes)
            points = workpoints(grid)
            support = {n: {f: {"n_reference": state["calibrations"][n]["n_reference"],
                                   "min_possible_p": 1/(state["calibrations"][n]["n_reference"]+1),
                                   "horizon": state["calibrations"][n]["horizon"]}
                           for f, state in states[n]["folds"].items()} for n in CELLS}
            manifest = {"schema": "codex-g-m7-full-episode-1.0.0", "implementation_commit": head, "source_sha256": sources,
                        "normal_input_sha256": hashes, "fold_table": table, "length_tertiles": tertiles,
                        "cells": states, "workpoints": points, "grid": grid, "support": support,
                        "observation_mode": "complete_generated_episode; integer H is an uncensoring API sentinel",
                        "normal_decision_sha256": {n: m3.decision_fingerprint(c) for n, c in cells.items()}}
            write_json(stage_dir / "threshold_manifest.json", manifest)
            log["threshold_manifest_sha256"] = sha((stage_dir / "threshold_manifest.json").read_bytes())
            write_json(stage_dir / "threshold_manifest.sha256.json", {"file_sha256": log["threshold_manifest_sha256"]})
            write_json(stage_dir / "normal_results.json", {n: m3.public(c) for n, c in cells.items()})
            print(json.dumps({"threshold_manifest_sha256": log["threshold_manifest_sha256"], "workpoints": points}), flush=True)
        else:
            keys = [trm3.trace_key(e) for e in episodes if e.normal]
            replay = {n: m3.decision_fingerprint(c, keys) == manifest["normal_decision_sha256"][n] for n, c in cells.items()}
            assert all(replay.values())
            readings, ledger, paired = evaluate(metadata, streams, manifest["workpoints"])
            for n in CELLS:
                shared = cells[n]["metrics"]
                own = readings["nominal"]["0.1"][n]
                for den in ("all", "filtered"):
                    assert own["control_alarms"][f"normal_{den}"]["count"] == shared["far"][den]["alarm_count"]
                native = shared["positives_anchored"]["recall"]["x_window"]
                # On genuinely incomplete generation, native reachability has a
                # different estimand. Only compare its complete-observation subset.
                native_rows = shared["positives_anchored"]["per_episode"]
                eligible = own["hits"]
                assert own["timely_recall"]["count"] == sum(bool(native_rows[k]["hit_plus_16"]) for k in eligible)
                if len(eligible) == native["reachable_count"]:
                    assert own["timely_recall"]["count"] == native["hit_count"]
            legacy = {k: {n: {"alarm": first(old[k]["ends"], old[k]["p"][:, j], prior["workpoints"][n]["alpha"]),
                               "alpha": prior["workpoints"][n]["alpha"]} for j, n in enumerate(("S", "CW"))}
                      for k, r in metadata.items() if r["variant"] == "attack" and r["x"] is not None}
            write_json(stage_dir / "result.json", {"readings": readings, "comparisons": paired, "normal_replay": replay,
                       "threshold_manifest_sha256": expected_manifest, "shared_nominal": {n: m3.public(c) for n, c in cells.items()}})
            write_json(stage_dir / "alarm_ledger.json", {"first_alarms": ledger, "M6_matched_first_alarms": legacy})
            print(json.dumps({"matched_10pct": {n: readings["matched"]["0.1"][n]["timely_recall"] for n in CELLS},
                              "matched_1pct": {n: readings["matched"]["0.01"][n]["timely_recall"] for n in CELLS}}), flush=True)
    write_json(stage_dir / "data_contract.json", {"arm_counts": counts, "loader": loaded,
               "opened_trace_or_manifest_paths": sorted(guard.content_paths), "path_filtered_trace_count": len(selected),
               "cache_writes": 0, "complete_look_coverage": True, "S_CW_old_prefix_raw_exact": True,
               "total_looks": sum(r["look_count"] for r in metadata.values()), "full_generated_tokens": sum(e.token_count for e in episodes)})
    assert sources == {p: sha((ROOT / p).read_bytes()) for p in SOURCES}
    assert all(sha((ROOT / p).read_bytes()) == v for p, v in hashes.items())
    log.update(status="completed", elapsed_seconds=time.monotonic()-start,
               peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2, access_guard=guard.summary(),
               output_sha256={p.name: sha(p.read_bytes()) for p in stage_dir.iterdir() if p.is_file() and p.name != "run_manifest.json"})
    assert not log["access_guard"]["blocked_attempts"]
    write_json(stage_dir / "run_manifest.json", log)
    print(json.dumps({k: log[k] for k in ("status", "elapsed_seconds", "peak_rss_gib", "access_guard")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("calibrate", "score"), required=True)
    parser.add_argument("--freeze-commit", required=True)
    parser.add_argument("--manifest-sha256")
    opt = parser.parse_args(); torch.set_num_threads(4)
    run(opt.stage, opt.freeze_commit, opt.manifest_sha256)
