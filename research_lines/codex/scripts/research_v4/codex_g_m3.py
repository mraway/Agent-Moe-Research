"""G-dev only, source-frozen, normal-first M3 driver using the unmodified G harness."""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import resource
import shutil
import subprocess
import time
from unittest.mock import patch

import numpy as np
import torch
from safetensors.torch import load as load_bytes

from research_v2 import io_g, trm3, trm3_g
from research_v4 import run_detectors_g as harness
from research_v4.codex_access_guard import CodexGAccessGuard
from research_v4.codex_g_m1 import BASE, CONFIG, EXPECTED_ARMS, LABELS, ROOT, RUN, sha, write_json
from research_v4.codex_g_m3_statistics import register
from research_v4.prereg_power_sim import simulate_cell

OUT = BASE / "m3_online_mass_v1"
OLD_CACHE = BASE / "m1_representation_stages_v1/topk_cache"
SINGLES = ("S", "P", "CU", "CM")
CELLS = (*SINGLES, "CUM")
VIEW = trm3_g.view_of("V1")
SOURCES = (
    "docs/research_v4/codex_g_m3_analysis_spec.md",
    "scripts/research_v4/codex_g_m3.py", "scripts/research_v4/codex_g_m3_statistics.py",
    "tests/test_research_v4_codex_g_m3.py", "scripts/research_v4/codex_access_guard.py",
    "scripts/research_v4/codex_g_m1.py", "scripts/research_v4/codex_g_m1_math.py",
    "scripts/research_v4/run_detectors_g.py", "scripts/research_v4/prereg_power_sim.py",
    "src/research_v2/io_g.py", "src/research_v2/trm3.py", "src/research_v2/trm3_g.py",
)


class M3AccessGuard(CodexGAccessGuard):
    def __init__(self, root, output, stage):
        super().__init__(root)
        self.stage = stage
        self.allowed_routes = (RUN.resolve(), OLD_CACHE.resolve(), output.resolve())
        self.content_paths = set()

    def check_path(self, path):
        resolved = super().check_path(path)
        if self.stage == "calibrate" and ("--attack" in str(resolved) or "attack" in resolved.parts):
            self.blocked_attempts += 1
            raise PermissionError("M3 stage 1 refuses attack trace/cache content before opening")
        if resolved.suffix == ".safetensors":
            if not any(p in resolved.parents for p in self.allowed_routes):
                self.blocked_attempts += 1
                raise PermissionError("M3 routing must be G-dev or an explicit Codex cache")
        if RUN.resolve() in resolved.parents and resolved.name in ("trace.json", "manifest.jsonl"):
            self.content_paths.add(str(resolved.relative_to(ROOT)))
        return resolved


def path_source_id(path):
    return (f"{path.parent.parent.name}--{path.parent.name}"
            if path.parent.name in io_g.KNOWN_VARIANTS else path.parent.name)


def normal_trace_paths(paths, scenarios):
    """Only path names + subset config: do not open a trace to decide its arm."""
    allowed = set()
    for s in scenarios:
        factory = s.get("factory") or {}
        if factory.get("normal_variant") == io_g.LEGITIMATE_REFUSAL:
            continue
        for arm in factory.get("collected_arms", ()):
            if arm in (io_g.CLEAN, io_g.BENIGN_CONTROL):
                allowed.add(f'{s["pair_group_id"]}--{arm}')
    return [p for p in paths if path_source_id(p) in allowed]


@contextmanager
def guarded_loader(guard, selected_paths):
    original = io_g.load_file
    iterator = io_g.iter_trace_paths
    io_g.load_file = lambda path, **kwargs: load_bytes(guard.check_path(path).read_bytes())
    io_g.iter_trace_paths = lambda directory: list(selected_paths) if Path(directory).resolve() == RUN.resolve() else iterator(directory)
    try:
        yield
    finally:
        io_g.load_file = original
        io_g.iter_trace_paths = iterator


def source_freeze(expected):
    head = harness.git_output("rev-parse", "HEAD")
    if head != expected or harness.git_output("diff", "HEAD", "--name-only"):
        raise ValueError("commit sources and pass exact clean tracked HEAD")
    tracked = set(harness.git_output("ls-files").splitlines())
    if not set(SOURCES).issubset(tracked):
        raise ValueError("all study sources must be committed")
    return head, {name: sha((ROOT / name).read_bytes()) for name in SOURCES}


def shared_args(stage, output, alpha=.1):
    return harness._args([
        "--target", str(RUN), "--target-labels", str(LABELS),
        "--cal-from-target", "--cal-folds", "3", "--fold-key", "fixture_rank_mod",
        "--cal-filtered-only", "--force-h", "352", "--expect-h", "352",
        "--view", "V1", "--tag-scope", "message", "--alpha", str(alpha),
        "--window-s", "8", "--window-p", "8", "--bucket-size", "32",
        "--min-bucket-traces", "30", "--min-channel-windows", "30", "--min-channel-traces", "10",
        "--tertile-cutpoints-from-target", "--temporal-d", "24", "--require-quality-labels",
        "--no-attribution", "--anchor", "x", "--hit-window", "e_view_to_anchor_plus_h",
        "--tolerance-bands", "0,4,5,8", "--bootstrap-replicates", "2000",
        "--prob-cache-dir", str(output / "logit_cache"), "--outputs", "primary",
        "--stage", stage, "--normal-only-smoke" if stage == "calibrate" else "--dev-smoke",
    ])


def score_joint(target, pools, states, args, cutpoints):
    """No fit/calibrate: native total-budget fusion of the two SINGLE frozen states."""
    names = ("CU", "CM")
    config = trm3_g.config_for_g(names, alpha=args.alpha, temporal_d=24)
    outputs, decisions, tracks, folds = {}, {}, {}, {}
    for fold, pool in sorted(pools.items()):
        stats = {name: trm3_g.build_statistic(name, harness.statistic_config(args, name)) for name in names}
        cal = {}
        for name in names:
            frozen = states[name]["folds"][str(fold)]
            stats[name].load_state(frozen["statistics"][name])
            cal[name] = trm3_g.calibration_from_state(frozen["calibrations"][name])
        merged = cal["CU"]
        merged.reference.channels = {name: cal[name].reference.channels[name] for name in names}
        merged.reference.k_cal = {name: 352 for name in names}
        assert all(c.horizon["H"] == 352 for c in cal.values())
        streams = trm3_g.episode_streams(stats, pool["eval"], VIEW)
        local = harness.score_episodes(pool["eval"], streams, args=args, view=VIEW, key="CU",
                    statistics=stats, calibration=merged, config=config,
                    standardisers={name: cal[name].standardiser for name in names})
        outputs.update(local[0]); decisions.update(local[1]); tracks.update(local[2])
        folds[str(fold)] = {
            "H": 352, "n_cal": merged.n_reference, "rotation": pool["rotation"],
            "alpha": args.alpha, "budgets": {s.name: s.alpha for s in config.channels},
            "alpha_eff": trm3.effective_alpha(config, merged.n_reference)["alpha_eff"],
            "alpha_eff_detail": trm3.effective_alpha(config, merged.n_reference),
            "attainability": trm3_g.attainability(config, merged.n_reference),
            "alarm_threshold_z": {name: merged.reference.channels[name].threshold(args.alpha / 2) for name in names},
            "far": harness.fold_far_block(local[1], pool["eval"], args.alpha),
            "state_source": "CU and CM single-cell frozen states; no additional fit or calibration",
        }
    anchors = trm3_g.view_anchors(target, VIEW)
    ends = {key: d.ends for key, d in decisions.items()}
    summaries = {trm3.trace_key(e): trm3.summarize_trace(outputs[trm3.trace_key(e)], e, 0) for e in target}
    metrics = trm3_g.evaluate_g(outputs, target, config, VIEW, anchors=anchors, decisions=decisions,
                              tertile_cutpoints=cutpoints, bands=(0, 4, 5, 8))
    metrics["positives_anchored"] = trm3_g.anchored_positives(summaries, ends, anchors, target,
                        which="x", window="e_view_to_anchor_plus_h", bands=(0, 4, 5, 8))
    metrics["hysteresis"] = harness.hysteresis_summary(tracks, target, args)
    metrics["hysteresis_note"] = "Unchanged shared OR convention: fused running p, CU instantaneous p. Descriptive only, not symmetric joint recovery."
    return {"statistic": "CUM", "config": config.to_json(), "folds": folds, "metrics": metrics,
            "fold_summary": {"alpha_eff_weighted": harness._weighted_alpha_eff(folds)},
            "_decisions": decisions, "_anchors": anchors, "_ends": ends, "_summaries": summaries}


def public(cell):
    return {k: v for k, v in cell.items() if not k.startswith("_")}


def decision_fingerprint(cell, keys=None):
    keys = sorted(cell["_decisions"] if keys is None else keys)
    return sha(json.dumps({k: [cell["_decisions"][k].ends, cell["_decisions"][k].p_fused] for k in keys},
                          sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def freeze_workpoints(cells, target):
    grid = harness.matched_alpha_inputs(cells, target, alpha=.1)
    reference = grid["cells"]["S"]["measured_far_at_alpha"]
    points = {}
    for name in CELLS:
        if name == "S":
            row = {"alpha": .1, "measured_far": reference}
        else:
            rows = [r for r in grid["cells"][name]["grid"] if r["measured_far"] <= reference]
            row = max(rows, key=lambda r: r["alpha"])
        points[name] = {**row, "reference": "S@0.10 filtered-normal FAR", "reference_far": reference,
                        "far_gap": row["measured_far"] - reference, "source": "stage1_normals_frozen"}
    return grid, points


def compare_pair(a, b, target, aa, ab):
    anchors = a["_anchors"]
    def hits(cell, alpha):
        return trm3_g.window_hits_at_alpha(cell["_decisions"], anchors, cell["_ends"], alpha,
                        which="x", window="e_view_to_anchor_plus_h", horizon=16, band=0, convention="penalty")
    ha, hb = hits(a, aa), hits(b, ab)
    assert set(ha) == set(hb)
    families = {trm3.trace_key(e): e.attack_family_id or e.pair_group_id for e in target}
    tiers = {trm3.trace_key(e): f'{families[trm3.trace_key(e)]}|{e.wording_tier}' for e in target}
    bootstrap = trm3_g.cluster_bootstrap_paired(ha, hb, families, replicates=2000)
    condition = harness.two_condition_block(bootstrap)
    condition["passes_unadjusted_conjunction"] = bool(condition["ci_excludes_zero"] and condition["mcnemar_p"] < .05)
    normal_keys, _ = harness.matching_normal_keys(target)
    return {"primary": a["statistic"], "secondary": b["statistic"], "alpha_a": aa, "alpha_b": ab,
            "filtered_far_a": trm3_g.measured_far(a["_decisions"], normal_keys, aa),
            "filtered_far_b": trm3_g.measured_far(b["_decisions"], normal_keys, ab),
            "bootstrap": bootstrap, "family_x_tier": trm3_g.cluster_bootstrap_paired(ha, hb, tiers, replicates=2000),
            "two_condition": condition, "only_a": sorted(k for k in ha if ha[k] and not hb[k]),
            "only_b": sorted(k for k in ha if hb[k] and not ha[k]),
            "uncertainty": "Conditional on normal-selected workpoints; G-dev exploration, not confirmation"}


def add_diagnostics(cell, target, alpha):
    cell["family_census"] = harness.positive_family_census(cell, target)
    cell["E_diagnostic_hits"] = trm3_g.window_hits_at_alpha(cell["_decisions"], cell["_anchors"], cell["_ends"], alpha,
                    which="e_view", window="anchor_plus_h", horizon=16, band=0)
    positive = cell["metrics"]["positives_anchored"]
    cell["stratum_worst"] = {}
    for key, groups in positive.items():
        if key.startswith("by_") and isinstance(groups, dict):
            available = {g: v for g, v in groups.items() if isinstance(v, dict) and v.get("recall") is not None}
            if available:
                worst = min(available, key=lambda g: (available[g]["recall"], g))
                cell["stratum_worst"][key] = {"group": worst, **available[worst]}


def forbid_retraining(*args, **kwargs):
    raise AssertionError("stage 2 forbids fitting, recalibrating, or workpoint selection")


@contextmanager
def restore_only():
    with patch.object(trm3_g, "calibrate_g", forbid_retraining), \
         patch.object(trm3_g, "matched_alpha_by_measured_far", forbid_retraining), \
         patch.object(harness, "matched_alpha_inputs", forbid_retraining), \
         patch.object(trm3_g.STATISTICS["S"], "fit", forbid_retraining), \
         patch.object(trm3_g.STATISTICS["P"], "fit", forbid_retraining), \
         patch.object(trm3_g.STATISTICS["CU"], "fit", forbid_retraining), \
         patch.object(trm3_g.STATISTICS["CM"], "fit", forbid_retraining):
        yield


def routing_cache_hashes(directory):
    return {str(p.relative_to(directory)): sha(p.read_bytes()) for p in sorted(directory.rglob("*.safetensors"))}


def run(stage, expected, expected_manifest=None):
    start = time.monotonic()
    register()
    guard = M3AccessGuard(ROOT, OUT, stage)
    guard.install()
    head, sources = source_freeze(expected)
    stage_dir = OUT / stage
    if stage_dir.exists():
        raise ValueError("refusing to overwrite an existing M3 stage")
    inputs = {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in (CONFIG, LABELS)}
    subset = json.loads(CONFIG.read_text())
    scenarios = subset["scenarios"]
    fixtures = io_g.fixture_map_from_config(CONFIG)
    table = trm3_g.fold_assignment(list(fixtures), folds=3, key="fixture_rank_mod", fixtures=fixtures)
    paths = io_g.iter_trace_paths(RUN)
    selected_paths = normal_trace_paths(paths, scenarios) if stage == "calibrate" else paths
    states = {}
    if stage == "score":
        raw = (OUT / "calibrate/threshold_manifest.json").read_bytes()
        if not expected_manifest or sha(raw) != expected_manifest:
            raise ValueError("pass the exact stage-1 manifest FILE sha256")
        manifest = json.loads(raw)
        if manifest["source_sha256"] != sources or manifest["inputs"] != inputs or manifest["fold_table"] != table:
            raise ValueError("frozen source/input/fold mismatch")
        if manifest["normal_cache_sha256"] != routing_cache_hashes(OUT / "topk_cache"):
            raise ValueError("stage-1 top-k cache changed")
        if manifest["normal_logits_sha256"] != routing_cache_hashes(OUT / "logit_cache"):
            raise ValueError("stage-1 full-logit cache changed")
        states = manifest["cells"]
    stage_dir.mkdir(parents=True)
    log = {"stage": stage, "status": "started", "started_utc": datetime.now(timezone.utc).isoformat(),
           "implementation_commit": head, "source_sha256": sources, "inputs": inputs,
           "threshold_manifest_sha256": expected_manifest, "evidence_role": "G-dev development only"}
    write_json(stage_dir / "run_manifest.json", log)
    # Reuse only the selected traces' previously audited private top-k cache. Raw
    # logits are read by GEpisode through the guarded byte decoder and cached here.
    source_ids = {path_source_id(p) for p in selected_paths}
    copied = {}
    for source in sorted((OLD_CACHE / "g_dev").glob("*.safetensors")):
        if source.stem.rsplit("--ep", 1)[0] not in source_ids:
            continue
        destination = OUT / "topk_cache/g_dev" / source.name
        if not destination.exists():
            guard.check_path(source)
            copied[source.name] = sha(source.read_bytes())
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    load_report = {}
    with guarded_loader(guard, selected_paths):
        episodes = io_g.load_g(RUN, labels=LABELS, variants=io_g.NORMAL_VARIANTS if stage == "calibrate" else None,
                      tag_scope="message", variant_overrides="auto", cache_dir=OUT / "topk_cache",
                      verify_tokens=True, manifest=load_report)
        counts = dict(Counter(e.variant for e in episodes))
        expected_counts = {k: v for k, v in EXPECTED_ARMS.items() if k in io_g.NORMAL_VARIANTS} if stage == "calibrate" else EXPECTED_ARMS
        if counts != expected_counts or any(e.filter_pass is None for e in episodes if e.variant in io_g.NORMAL_VARIANTS):
            raise ValueError(f"data/label contract failed: {counts}")
        trace_inputs = {str(p.relative_to(ROOT)): sha(p.read_bytes()) for p in selected_paths}
        if stage == "score":
            if any(trace_inputs[k] != v for k, v in manifest["normal_trace_sha256"].items()):
                raise ValueError("stage-1 normal trace changed")
        pools = harness.fold_pools(episodes, table, folds=3, filtered_only=True, normals_only_eval=stage == "calibrate")
        tertiles = harness.tertiles_from_normals(pools, episodes, filtered_only=True) if stage == "calibrate" else manifest["length_tertiles"]
        cutpoints = tertiles["cutpoints"]
        args = shared_args(stage, OUT)
        cells = {}
        print(json.dumps({"stage": stage, "episodes": counts, "cutpoints": cutpoints}), flush=True)
        def singles():
            for name in SINGLES:
                print(f"{stage}: {name}", flush=True)
                cell = harness.run_cell_v32(name, args=args, view=VIEW, target_pool=episodes, pools=pools,
                                 manifest_cell=states.get(name), cutpoints=cutpoints)
                cells[name] = cell
                if stage == "calibrate":
                    states[name] = {"folds": cell["_fold_states"]}
        if stage == "calibrate":
            singles()
            cells["CUM"] = score_joint(episodes, pools, states, args, cutpoints)
            for cell in cells.values():
                cell["gates"] = harness.gate_block(cell, episodes, pools, cutpoints=cutpoints, filtered_only=True)
            grid, points = freeze_workpoints(cells, episodes)
            manifest = {"schema": "codex-g-m3-thresholds-1.0.0", "source_sha256": sources, "implementation_commit": head,
                        "inputs": inputs, "fold_table": table, "length_tertiles": tertiles, "cells": states,
                        "joint": {"config": cells["CUM"]["config"], "folds": cells["CUM"]["folds"]},
                        "matched_alpha_inputs": grid, "workpoints": points,
                        "normal_decision_sha256": {n: decision_fingerprint(c) for n, c in cells.items()},
                        "normal_trace_sha256": trace_inputs,
                        "normal_cache_sha256": routing_cache_hashes(OUT / "topk_cache"),
                        "normal_logits_sha256": routing_cache_hashes(OUT / "logit_cache")}
            write_json(stage_dir / "threshold_manifest.json", manifest)
            manifest_sha = sha((stage_dir / "threshold_manifest.json").read_bytes())
            write_json(stage_dir / "threshold_manifest.sha256.json", {"file_sha256": manifest_sha})
            write_json(stage_dir / "normal_results.json", {n: public(c) for n, c in cells.items()})
            print(json.dumps({"threshold_manifest_sha256": manifest_sha, "workpoints": points}), flush=True)
            log["threshold_manifest_sha256"] = manifest_sha
        else:
            with restore_only():
                singles()
                cells["CUM"] = score_joint(episodes, pools, states, args, cutpoints)
                normal_keys = sorted(trm3.trace_key(e) for e in episodes if e.variant in io_g.NORMAL_VARIANTS)
                normal_replay = {n: decision_fingerprint(c, normal_keys) == manifest["normal_decision_sha256"][n] for n, c in cells.items()}
                if not all(normal_replay.values()):
                    raise ValueError(f"normal replay failed: {normal_replay}")
                matched = {}
                for name in CELLS:
                    alpha = manifest["workpoints"][name]["alpha"]
                    at = shared_args(stage, OUT, alpha)
                    print(f"matched replay: {name}, alpha={alpha}", flush=True)
                    matched[name] = (score_joint(episodes, pools, states, at, cutpoints) if name == "CUM" else
                        harness.run_cell_v32(name, args=at, view=VIEW, target_pool=episodes, pools=pools,
                                            manifest_cell=states[name], cutpoints=cutpoints))
                    assert decision_fingerprint(matched[name]) == decision_fingerprint(cells[name])
                    add_diagnostics(cells[name], episodes, .1)
                    add_diagnostics(matched[name], episodes, alpha)
                    for cell in (cells[name], matched[name]):
                        cell["gates"] = harness.gate_block(cell, episodes, pools, cutpoints=cutpoints, filtered_only=True)
            comparisons = {}
            for a, b in (("CUM", "CU"), ("CU", "S"), ("CM", "CU"), ("CUM", "S"), ("S", "P")):
                comparisons[f"{a}_minus_{b}"] = {
                    "role": "sole_primary" if (a, b) == ("CUM", "CU") else "descriptive",
                    "nominal": compare_pair(cells[a], cells[b], episodes, .1, .1),
                    "matched": compare_pair(cells[a], cells[b], episodes, manifest["workpoints"][a]["alpha"], manifest["workpoints"][b]["alpha"])}
            census = cells["CU"]["family_census"]
            sizes = [v["reachable"] for v in census["positives_by_family"].values()]
            null = {"required": len(sizes) < 16, "actual_family_sizes": sizes, "cells": []}
            if null["required"]:
                for rho in (.15, .30):
                    null["cells"].append(simulate_cell(n=sum(sizes), delta=0, rho=rho, family_sizes=sizes,
                                                      replicates=2000, bootstrap=2000, seed=20260907))
            write_json(stage_dir / "result.json", {"nominal": {n: public(c) for n, c in cells.items()},
                       "matched": {n: public(c) for n, c in matched.items()}, "workpoints": manifest["workpoints"],
                       "comparisons": comparisons, "normal_replay": normal_replay, "null_simulation": null,
                       "threshold_manifest_sha256": expected_manifest})
            with (stage_dir / "decisions.jsonl").open("w") as handle:
                for name, cell in cells.items():
                    for key, decision in sorted(cell["_decisions"].items()):
                        handle.write(json.dumps({"statistic": name, **decision.to_json()}) + "\n")
            print(json.dumps({"normal_replay": normal_replay, "primary_comparison": comparisons["CUM_minus_CU"]}), flush=True)
    write_json(stage_dir / "data_contract.json", {"arm_counts": counts, "loader": load_report,
                "source_cache_copied_sha256": copied, "trace_sha256": trace_inputs,
                "opened_trace_or_manifest_paths": sorted(guard.content_paths), "stage1_path_filtered_traces": len(selected_paths)})
    log.update(status="completed", elapsed_seconds=time.monotonic() - start,
               peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2, access_guard=guard.summary(),
               output_sha256={p.name: sha(p.read_bytes()) for p in stage_dir.iterdir() if p.is_file() and p.name != "run_manifest.json"})
    if log["access_guard"]["blocked_attempts"]:
        raise ValueError("unexpected blocked access")
    if sources != {name: sha((ROOT / name).read_bytes()) for name in SOURCES}:
        raise ValueError("source changed during run")
    write_json(stage_dir / "run_manifest.json", log)
    print(json.dumps({k: log[k] for k in ("status", "elapsed_seconds", "peak_rss_gib", "access_guard")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, choices=("calibrate", "score"))
    parser.add_argument("--freeze-commit", required=True)
    parser.add_argument("--manifest-sha256")
    options = parser.parse_args()
    torch.set_num_threads(4)
    run(options.stage, options.freeze_commit, options.manifest_sha256)
