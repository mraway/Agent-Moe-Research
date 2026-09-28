"""Frozen G-dev probability-component study; no detector or sealed-pool access."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import resource
import subprocess
import time

import numpy as np
import torch
from safetensors.torch import load as load_bytes

from research_v2 import io_g, trm3, trm3_g
from research_v4.codex_access_guard import CodexGAccessGuard
from research_v4.codex_g_m1 import BASE, CONFIG, EXPECTED_ARMS, LABELS, ROOT, RUN, read_raw, sha, write_json
from research_v4.codex_g_m1_math import BANDS, STAGES, NormalPercentiles, band_scores, representations, stage_bounds
from research_v4.codex_g_m2a import META_FIELDS, archive_members, effect_fields, grouped_summary, matrix_summary, serial_fields
from research_v4.codex_g_m2a_math import KINDS, QUALITIES, SCOPES, paired_fields
from research_v4.codex_g_m2b_math import (
    BASIS, DYNAMIC, PHYSICAL, REPS, VIEW, causal_windows, complete_ablations,
    dynamic_interval, layer_bands, probability_basis, stable_dynamics,
)

M1 = BASE / "m1_representation_stages_v1"
M2 = BASE / "m2a_transition_controls_v1"
MATCH_SHA = "3e64a8824eb60afe8dd7af352a455c68606f91c1eea5dd1aa90d7128b90e9a91"
SOURCES = (
    "docs/research_v4/codex_g_m2b_analysis_spec.md", "scripts/research_v4/codex_g_m2b.py",
    "scripts/research_v4/codex_g_m2b_math.py", "tests/test_research_v4_codex_g_m2b.py",
    "scripts/research_v4/codex_access_guard.py", "scripts/research_v4/codex_g_m1.py",
    "scripts/research_v4/codex_g_m1_math.py", "scripts/research_v4/codex_g_m2a.py",
    "scripts/research_v4/codex_g_m2a_math.py", "src/research_v2/io_g.py",
    "src/research_v2/trm3.py", "src/research_v2/trm3_g.py",
)


class M2BAccessGuard(CodexGAccessGuard):
    def __init__(self, root, output):
        super().__init__(root)
        self.allowed_routes = (RUN.resolve(), (M1 / "topk_cache").resolve(), (output / "topk_cache").resolve())

    def check_path(self, path):
        resolved = super().check_path(path)
        if resolved.suffix == ".safetensors" and not any(p in resolved.parents for p in self.allowed_routes):
            self.blocked_attempts += 1
            raise PermissionError("M2-B allows only G-dev raw shards and explicit private top-k caches")
        return resolved


def read_json(path):
    return json.loads(path.read_text())


def write_jsonl(path, rows):
    with path.open("w") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def read_inputs():
    m1, m2 = read_json(M1 / "run_manifest.json"), read_json(M2 / "score_manifest.json")
    for manifest in (m1, m2):
        if manifest["status"] != "completed" or manifest["access_guard"]["blocked_attempts"]:
            raise ValueError("prior study provenance invalid")
    inputs = {}
    def verified(path, expected=None):
        actual = sha(path.read_bytes())
        if expected is not None and actual != expected:
            raise ValueError(f"frozen input changed: {path.name}")
        inputs[str(path.relative_to(ROOT))] = actual
    for name in ("look_scores.npz", "data_contract.json", "episode_metrics.jsonl"):
        verified(M1 / name, m1["output_sha256"][name])
    for name in ("paired_readings.jsonl", "summary.json"):
        verified(M2 / name, m2["output_sha256"][name])
    verified(M2 / "match_manifest.json", MATCH_SHA)
    graph = read_json(M2 / "match_manifest.json")
    verified(M2 / "metadata_index.json", graph["metadata_sha256"])
    verified(M1 / "run_manifest.json", graph["m1_run_manifest_sha256"])
    verified(M2 / "score_manifest.json")
    for name, expected in m1["inputs"].items(): verified(ROOT / name, expected)
    for fold in range(3): verified(M1 / f"q_fold{fold}.npz")
    arrays = archive_members(M1 / "look_scores.npz", ("keys", "offsets", "ends", "tags", "ordinals", "raw", "percentiles", "reference_levels"))
    metrics = {r["key"]: r for r in (json.loads(line) for line in (M1 / "episode_metrics.jsonl").read_text().splitlines())}
    metadata = read_json(M2 / "metadata_index.json")
    return inputs, graph, metadata, arrays, metrics, read_json(M1 / "data_contract.json"), m1


def make_readings(graph, metadata, scores):
    readings, checked = [], 0
    for match in graph["matches"]:
        if match["status"] != "matched": continue
        query = graph["queries"][match["query_id"]]
        target = metadata[query["key"]]
        item = {k: target[k] for k in ("key", "family", "tier", "trajectory_class", "injection_channel", "fold")}
        item.update(kind=query["kind"], scope=match["scope"], quality=match["quality"],
                    query_id=match["query_id"], horizon_cut=query["horizon_cut"], values={}, donor_means=[], placebo={})
        for donor in match["donors"]:
            dm = metadata[donor["key"]]
            if dm["fold"] != target["fold"] or dm["episode_index"] != target["episode_index"]:
                raise ValueError("frozen donor mismatch")
            for name, indices in query["groups"].items():
                if [target["coordinates"][i] for i in indices] != [dm["coordinates"][i] for i in donor["groups"][name]]:
                    raise ValueError("frozen structural footprint changed")
                checked += len(indices)
            item["donor_means"].append({"key": donor["key"], "variant": dm["variant"]})
        for scale in ("raw", "percentile", "physical"):
            target_values = {n: scores[target["key"]][scale][ix] for n, ix in query["groups"].items()}
            donors = [{n: scores[d["key"]][scale][ix] for n, ix in d["groups"].items()} for d in match["donors"]]
            fields, donor_means = paired_fields(target_values, donors)
            item["values"][scale] = serial_fields(fields)
            for saved, means in zip(item["donor_means"], donor_means): saved[scale] = serial_fields(means)
            clean = [d for d in item["donor_means"] if d["variant"] == "clean"]
            benign = [d for d in item["donor_means"] if d["variant"] == "benign_control"]
            if match["scope"] == "S" and clean and benign:
                placebo = {n: np.mean([d[scale][n] for d in clean], axis=0) - np.mean([d[scale][n] for d in benign], axis=0)
                           for n in query["groups"]}
                if "pre" in placebo: placebo["change"] = placebo["post"] - placebo["pre"]
                item["placebo"][scale] = serial_fields(placebo)
        readings.append(item)
    return readings, checked


def matched_summary(rows, kind, metadata):
    result = grouped_summary(rows, kind, metadata)
    if not rows: return result
    result["means"]["physical"] = {name: np.mean([r["values"]["physical"][name] for r in rows], axis=0).tolist()
                                    for name in rows[0]["values"]["physical"]}
    result["effects"]["physical"] = {}
    for name in effect_fields(kind):
        result["effects"]["physical"][name] = matrix_summary(np.array([r["values"]["physical"][name] for r in rows]), rows)
        for scale in ("raw", "percentile"):
            values = np.array([r["values"][scale][name] for r in rows])
            result["effects"][scale][name].update(
                P_minus_ablations=matrix_summary(values[:, 2, None, :] - values[:, 4:8], rows),
                V_minus_W=matrix_summary(values[:, 3] - values[:, 1], rows))
    placebo = [r for r in rows if r["placebo"]]
    if placebo:
        result["normal_normal_placebo"]["effects"]["physical"] = {
            name: matrix_summary(np.array([r["placebo"]["physical"][name] for r in placebo]), placebo)
            for name in placebo[0]["placebo"]["physical"]}
    return result


def episode_interval(row, indices, status):
    out = {"status": status, "looks": len(indices)}
    for scale in ("raw", "percentile", "physical"):
        out[scale] = row[scale][indices].mean(0).tolist() if len(indices) else None
    out["dynamics"] = dynamic_interval(row["dynamics"], row["ends"][indices])
    return out


def cohort_summary(rows, stage):
    blocks = [(r, r["intervals"][stage]) for r in rows]
    effective = [(r, b) for r, b in blocks if b["looks"]]
    out = {"candidates": len(rows), "n": len(effective), "status": dict(Counter(b["status"] for _, b in blocks)),
           "means": {scale: np.mean([b[scale] for _, b in effective], axis=0).tolist() if effective else None
                     for scale in ("raw", "percentile", "physical")}, "dynamics": {}}
    for band in range(4):
        available = [b["dynamics"] for _, b in blocks if b["dynamics"]["stable_pairs"][band]]
        value = np.mean([[d["means"][i][band] for i in range(4)] for d in available], axis=0) if available else None
        out["dynamics"][BANDS[band]] = {
            "n": len(available), "missing": len(rows) - len(available),
            "stable_pairs": sum(b["dynamics"]["stable_pairs"][band] for _, b in blocks),
            "valid_pairs": sum(b["dynamics"]["valid_pairs"][band] for _, b in blocks),
            "means": value.tolist() if value is not None else None,
            "shares": (value[1:] / value[1:].sum()).tolist() if value is not None and value[1:].sum() > 0 else None}
    if effective and all(r["attack_bearing"] and r["family"] for r, _ in effective):
        values = np.array([b["percentile"] for _, b in effective])
        out["P_minus_ablations"] = matrix_summary(values[:, 2, None, :] - values[:, 4:8], [r for r, _ in effective])
    return out


def run(output, expected):
    start = time.monotonic()
    guard = M2BAccessGuard(ROOT, output)
    guard.install()
    output = guard.check_path(output)
    if output.parent != BASE.resolve() or output.exists(): raise ValueError("new Codex output directory required")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != expected or subprocess.check_output(["git", "diff", "HEAD", "--name-only"], cwd=ROOT, text=True).strip():
        raise ValueError("commit implementation and pass exact current HEAD")
    if not set(SOURCES).issubset(subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()):
        raise ValueError("all study sources must be committed")
    source_hashes = {name: sha((ROOT / name).read_bytes()) for name in SOURCES}
    inputs, graph, metadata, old, metrics, original_contract, m1 = read_inputs()
    output.mkdir(parents=True)
    manifest = {"schema": "codex-g-m2b-probability-components-1.0.0", "status": "started",
                "started_utc": datetime.now(timezone.utc).isoformat(), "implementation_commit": head,
                "source_sha256": source_hashes, "input_sha256": inputs, "match_manifest_sha256": MATCH_SHA,
                "representations": REPS, "physical": PHYSICAL, "dynamics": DYNAMIC, "bands": BANDS,
                "constants": {"view": "V1", "tag_scope": "message", "w": 8, "H_looks": 352,
                              "bootstrap": 2000, "seed": 20260908},
                "warning": "development description, not causality, incremental information or online detector evaluation"}
    write_json(output / "run_manifest.json", manifest)
    try:
        torch.set_num_threads(4)
        cache = output / "topk_cache/g_dev"
        cache.mkdir(parents=True)
        copies = 0
        for path in sorted((M1 / "topk_cache/g_dev").glob("*.safetensors")):
            if path.is_symlink(): raise PermissionError("unexpected cache symlink")
            (cache / path.name).write_bytes(guard.check_path(path).read_bytes())
            copies += 1
        original_load = io_g.load_file
        io_g.load_file = lambda path, **kwargs: load_bytes(guard.check_path(path).read_bytes())
        loader = {}
        try:
            episodes = io_g.load_g(RUN, labels=LABELS, cache_dir=output / "topk_cache", tag_scope="message",
                                  verify_tokens=True, variant_overrides=io_g.VARIANT_OVERRIDES_AUTO, manifest=loader)
        finally:
            io_g.load_file = original_load
        if dict(Counter(e.variant for e in episodes)) != EXPECTED_ARMS: raise ValueError("G-dev census changed")
        if [trm3.trace_key(e) for e in episodes] != old["keys"].tolist(): raise ValueError("M1 key/order mismatch")
        anchors = trm3_g.view_anchors(episodes, VIEW)
        fixtures = io_g.fixture_map_from_config(CONFIG)
        table = trm3_g.fold_assignment(list(fixtures), key="fixture_rank_mod", fixtures=fixtures)
        if trm3_g.fold_table_sha256(table) != m1["fold_table_sha256"]: raise ValueError("fold table changed")
        folds = trm3_g.fold_of_episodes(episodes, table)
        previous_contracts = {r["key"]: r for r in original_contract["per_episode"]}
        q_old = {fold: {tag: torch.from_numpy(value) for tag, value in archive_members(M1 / f"q_fold{fold}.npz", VIEW.channels).items()}
                 for fold in range(3)}
        sums = defaultdict(lambda: torch.zeros(5, 24, 32, dtype=torch.float64))
        old_sums = defaultdict(lambda: torch.zeros(3, 24, 32, dtype=torch.float64))
        mass_sums = defaultdict(lambda: torch.zeros(24, dtype=torch.float64))
        counts = Counter()
        records, contracts, verified_traces = [], [], set()
        raw_error, q_error = 0., 0.
        print(json.dumps({"phase": "loaded", "episodes": len(episodes), "copied_cache_files": copies}), flush=True)
        for i, ep in enumerate(episodes):
            key = trm3.trace_key(ep)
            trace_path = guard.check_path(ep.trace_dir / "trace.json")
            name = str(trace_path.relative_to(ROOT))
            if name not in verified_traces:
                if sha(trace_path.read_bytes()) != original_contract["trace_json_sha256"][name]: raise ValueError("trace changed since M1")
                verified_traces.add(name)
            if folds[key] != metadata[key]["fold"] or anchors[key].to_json() != metadata[key]["anchor"]:
                raise ValueError("metadata/anchor changed")
            ids, weights, logits, raw_contract = read_raw(ep, guard)
            if raw_contract["ordered_shard_digest"] != previous_contracts[key]["ordered_shard_digest"]:
                raise ValueError("routing shards changed since M1")
            rep, gate = representations(ids, weights, logits)
            basis, physical, p, contract = probability_basis(rep)
            dynamics = stable_dynamics(p, ids, ep.channel_tags, [int(s["global_token_offset"]) for s in ep.step_spans])
            ends, means, tags, ordinals = causal_windows(basis.permute(1, 0, 2, 3), ep.channel_tags)
            phys_grid, phys_means, phys_tags, _ = causal_windows(physical, ep.channel_tags)
            old_ends, old_means, old_tags, old_ordinals = causal_windows(rep.permute(1, 0, 2, 3), ep.channel_tags)
            a, b = old["offsets"][i:i + 2]
            for grid in (ends, phys_grid, old_ends): np.testing.assert_array_equal(grid, old["ends"][a:b])
            if list(tags) != old["tags"][a:b].tolist() or list(phys_tags) != list(tags) or list(old_tags) != list(tags):
                raise ValueError("look tags changed")
            np.testing.assert_array_equal(ordinals, old["ordinals"][a:b])
            np.testing.assert_array_equal(old_ordinals, ordinals)
            if len(ends):
                normal = torch.stack([q_old[folds[key]][tag] for tag in tags], 1)
                reproduced = band_scores(old_means.permute(1, 0, 2, 3), normal)
                error = float(np.abs(reproduced - old["raw"][a:b]).max())
                raw_error = max(raw_error, error)
                if error > 1e-10: raise ValueError("original U/W/P raw scores did not reproduce")
            if ep.normal and ep.filter_pass is True:
                token_tags = np.asarray(ep.channel_tags)
                for tag in VIEW.channels:
                    mask = torch.from_numpy(token_tags == tag)
                    if bool(mask.any()):
                        fit_key = (folds[key], tag)
                        sums[fit_key] += basis[:, mask].double().sum(1)
                        old_sums[fit_key] += rep[:, mask].double().sum(1)
                        mass_sums[fit_key] += physical[mask, 0].double().sum(0)
                        counts[fit_key] += int(mask.sum())
            row = {k: metrics[key][k] for k in META_FIELDS}
            row.update(normal=ep.normal, ends=ends, tags=tags, ordinals=ordinals, basis_means=means,
                       physical=layer_bands(phys_means).double().numpy(), dynamics=dynamics,
                       raw=old["raw"][a:b], percentile=old["percentiles"][a:b], old_levels=old["reference_levels"][a:b])
            records.append(row)
            contracts.append({"key": key, **raw_contract, **gate, **contract,
                              "dynamic_identity_error": dynamics["identity_error"], "dynamic_direct_js_error": dynamics["direct_js_error"]})
            del rep, basis, p, logits, weights, ids, physical, old_means
            if (i + 1) % 40 == 0 or i + 1 == len(episodes):
                print(json.dumps({"phase": "routing", "episodes": i + 1, "seconds": round(time.monotonic() - start, 1),
                                  "rss_gib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 ** 2, 3)}), flush=True)
        reference_report = {}
        for fold in range(3):
            fit, reference = (fold + 1) % 3, (fold + 2) % 3
            n_global = sum(counts[(fit, tag)] for tag in VIEW.channels)
            if not n_global: raise ValueError("empty normal fit")
            q, m0 = {}, {}
            for tag in VIEW.channels:
                use = [tag] if counts[(fit, tag)] else list(VIEW.channels)
                n = sum(counts[(fit, t)] for t in use)
                q_basis = sum(sums[(fit, t)] for t in use) / n
                m0[tag] = sum(mass_sums[(fit, t)] for t in use) / n
                q[tag] = complete_ablations(q_basis[None], m0[tag])[0]
                check = sum(old_sums[(fit, t)] for t in use) / n
                error = float((check - q_old[fold][tag]).abs().max())
                q_error = max(q_error, error)
                if error > 1e-12: raise ValueError("M1 q reference did not reproduce")
            target = [r for r in records if r["fold"] == fold]
            ref = [r for r in records if r["fold"] == reference and r["normal"] and r["filter_pass"] is True]
            reference_scores = []
            for row in target + ref:
                if len(row["ends"]):
                    means = complete_ablations(row["basis_means"], torch.stack([m0[t] for t in row["tags"]]))
                    normal = torch.stack([q[t] for t in row["tags"]], 1)
                    scores = band_scores(means.permute(1, 0, 2, 3), normal)
                else: scores = np.zeros((0, 5, 4))
                if row["fold"] == fold: row["extra_raw"] = scores
                else: reference_scores.append({"key": row["key"], "episode_index": row["episode_index"], "scores": scores,
                                                "tags": row["tags"], "ordinals": row["ordinals"]})
            calibration = NormalPercentiles(reference_scores)
            levels_count = Counter()
            for row in target:
                percentiles, levels = calibration.transform(row["extra_raw"], row["tags"], row["ordinals"], row["episode_index"])
                if not np.isfinite(percentiles).all(): raise ValueError("missing new percentile reference")
                np.testing.assert_array_equal(levels, row["old_levels"])
                row["raw"] = np.concatenate((row["raw"], row.pop("extra_raw")), axis=1)
                row["percentile"] = np.concatenate((row["percentile"], percentiles), axis=1)
                levels_count.update(map(str, levels.tolist()))
            reference_report[str(fold)] = {"fit": fit, "reference": reference, "eval": fold, "fit_tokens": n_global,
                                          "m0_by_channel_layer": {t: v.tolist() for t, v in m0.items()},
                                          "fit_q_fallback_channels": [t for t in VIEW.channels if not counts[(fit, t)]],
                                          "normal_reference_episodes": len(ref), "reference_levels": dict(levels_count)}
            np.savez_compressed(output / f"q_components_fold{fold}.npz", **{t: v.numpy() for t, v in q.items()})
            print(json.dumps({"phase": "scored", "fold": fold}), flush=True)
        for row in records: del row["basis_means"]
        score_map = {r["key"]: r for r in records}
        readings, checked = make_readings(graph, metadata, score_map)
        previous = {(r["query_id"], r["scope"], r["quality"]): r for r in
                    (json.loads(line) for line in (M2 / "paired_readings.jsonl").read_text().splitlines())}
        if len(readings) != len(previous) or len(readings) != 305: raise ValueError("M2-A coverage changed")
        for row in readings:
            prior = previous[(row["query_id"], row["scope"], row["quality"])]
            for scale in ("raw", "percentile"):
                for field, value in row["values"][scale].items():
                    np.testing.assert_allclose(np.array(value)[:3], prior["values"][scale][field], atol=1e-12, rtol=0)
        write_jsonl(output / "paired_readings.jsonl", readings)
        summary, strata = {}, {}
        for scope in SCOPES:
            for quality in QUALITIES:
                for kind in KINDS:
                    cell = f"{scope}/{quality}/{kind}"
                    subset = [r for r in readings if (r["scope"], r["quality"], r["kind"]) == (scope, quality, kind)]
                    summary[cell] = matched_summary(subset, kind, metadata)
                    strata[cell] = {}
                    for field in ("family", "tier", "trajectory_class", "injection_channel", "fold"):
                        groups = defaultdict(list)
                        for row in subset: groups[str(row[field])].append(row)
                        strata[cell][field] = {name: {"n": len(group), "effects": {scale: {
                            metric: np.mean([r["values"][scale][metric] for r in group], axis=0).tolist()
                            for metric in effect_fields(kind)} for scale in ("percentile", "raw", "physical")}}
                            for name, group in groups.items()}
        episode_rows = []
        for row in records:
            item = {k: row[k] for k in META_FIELDS}
            item["normal"] = row["normal"]
            bounds = stage_bounds(row["anchor"]["anchor"], row["anchor"]["x"]) if row["attack_bearing"] else stage_bounds(None, None)
            bounds["whole"] = (0, row["h_end"])
            item["intervals"] = {}
            for name, span in bounds.items():
                indices = np.flatnonzero((row["ends"] >= span[0]) & (row["ends"] <= span[1])) if span else np.zeros(0, dtype=int)
                status = ("missing_event" if span is None else "empty_interval" if span[1] < span[0]
                          else "beyond_horizon" if span[0] > row["h_end"] else "no_eligible_look" if not len(indices) else "effective")
                item["intervals"][name] = episode_interval(row, indices, status)
            episode_rows.append(item)
        cohorts = {
            "normal_all": [r for r in episode_rows if r["normal"]],
            "normal_filtered": [r for r in episode_rows if r["normal"] and r["filter_pass"] is True],
            "attack_bearing": [r for r in episode_rows if r["attack_bearing"]],
            "E_anchored": [r for r in episode_rows if r["attack_bearing"] and r["anchor"]["anchor"] is not None],
            "silent_postinjection": [r for r in episode_rows if r["attack_bearing"] and r["silent"]],
            "legitimate_refusal": [r for r in episode_rows if r["variant"] == "legitimate_refusal"],
            "attack_preinjection_excluded": [r for r in episode_rows if r["variant"] == "attack" and not r["attack_bearing"]]}
        for label in sorted({r["trajectory_class"] for r in cohorts["attack_bearing"]}):
            cohorts["attack_class/" + label] = [r for r in cohorts["attack_bearing"] if r["trajectory_class"] == label]
        full_summary = {name: {s: cohort_summary(group, s) for s in ("whole", *STAGES)} for name, group in cohorts.items()}
        offsets = np.cumsum([0] + [len(r["ends"]) for r in records])
        np.savez_compressed(output / "look_scores.npz", keys=np.array([r["key"] for r in records]), offsets=offsets,
                            ends=np.concatenate([r["ends"] for r in records]),
                            raw=np.concatenate([r["raw"] for r in records]),
                            percentiles=np.concatenate([r["percentile"] for r in records]),
                            physical=np.concatenate([r["physical"] for r in records]))
        write_jsonl(output / "episode_metrics.jsonl", episode_rows)
        for name, data in (("matched_summary.json", summary), ("strata.json", strata), ("full_summary.json", full_summary),
                           ("normal_reference.json", reference_report), ("data_contract.json", {"per_episode": contracts})):
            write_json(output / name, data)
        checks = {"status": "passed", "episodes": len(records), "arms": EXPECTED_ARMS, "M1_raw_max_error": raw_error,
                  "M1_q_max_error": q_error, "matched_rows": len(readings), "exact_coordinate_pairs": checked,
                  "prior_U_W_P_matched_effects_reproduced": True, "unchanged_reference_fallback": True,
                  "raw_shard_digests_reproduced": True, "trace_metadata_hashes_verified": len(verified_traces)}
        write_json(output / "consistency_checks.json", checks)
        output_names = ("look_scores.npz", "episode_metrics.jsonl", "paired_readings.jsonl", "matched_summary.json", "strata.json",
                        "full_summary.json", "normal_reference.json", "data_contract.json", "consistency_checks.json",
                        *(f"q_components_fold{i}.npz" for i in range(3)))
        # Recheck all original inputs after execution; never rewrite prior artifacts.
        for name, expected_hash in inputs.items():
            if sha((ROOT / name).read_bytes()) != expected_hash: raise ValueError("input changed during execution")
        manifest.update(status="completed", finished_utc=datetime.now(timezone.utc).isoformat(),
                        elapsed_seconds=time.monotonic() - start, peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 ** 2,
                        access_guard=guard.summary(), loader=loader,
                        output_sha256={name: sha((output / name).read_bytes()) for name in output_names})
        write_json(output / "run_manifest.json", manifest)
        print(json.dumps({"phase": "completed", **checks, "seconds": manifest["elapsed_seconds"], "access_guard": guard.summary()}), flush=True)
    except Exception as error:
        manifest.update(status="failed", error_type=type(error).__name__, error=str(error),
                        elapsed_seconds=time.monotonic() - start, access_guard=guard.summary())
        write_json(output / "run_manifest.json", manifest)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=BASE / "m2b_probability_components_v1")
    parser.add_argument("--expect-commit", required=True)
    args = parser.parse_args()
    run(args.output_root, args.expect_commit)
