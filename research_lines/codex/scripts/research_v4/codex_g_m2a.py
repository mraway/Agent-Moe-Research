"""Two-stage score-blind matching then descriptive comparison on OPEN G-dev."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import resource
import subprocess
import time

import numpy as np

from research_v2 import io_g
from research_v4.codex_access_guard import CodexGAccessGuard
from research_v4.codex_g_m1_math import BANDS, REPRESENTATIONS
from research_v4.codex_g_m2a_math import (
    CHANNELS, KINDS, QUALITIES, SCOPES, cluster_grid, donor_influence, look_geometry,
    match_footprint, paired_fields, query_groups,
)

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/agent_v2/codex_g"
M1 = BASE / "m1_representation_stages_v1"
DEV = ROOT / "artifacts/agent_v2/dataset_g/g_dev"
CONFIG = ROOT / "configs/dataset_g/g_dev.json"
SOURCES = (
    "docs/research_v4/codex_g_m2a_analysis_spec.md", "scripts/research_v4/codex_g_m2a.py",
    "scripts/research_v4/codex_g_m2a_math.py", "tests/test_research_v4_codex_g_m2a.py",
    "scripts/research_v4/codex_access_guard.py", "src/research_v2/io_g.py",
    "scripts/research_v4/codex_g_m1_math.py",
)
META_FIELDS = ("key", "source_trace_id", "scenario", "variant", "filter_pass", "episode_index",
               "family", "tier", "domain_group", "trajectory_class", "silent", "attack_bearing",
               "injection_channel", "fold", "token_count", "h_end", "anchor")


class M2AccessGuard(CodexGAccessGuard):
    def check_path(self, path):
        resolved = super().check_path(path)
        if resolved.suffix == ".safetensors" and self.repo_root / "artifacts" in resolved.parents:
            self.blocked_attempts += 1
            raise PermissionError("M2-A reuses frozen scores: routing shard/cache reads forbidden")
        return resolved


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def read_json(path, guard):
    return json.loads(guard.check_path(path).read_text())


def check_implementation(expected):
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != expected or subprocess.check_output(["git", "diff", "HEAD", "--name-only"], cwd=ROOT, text=True).strip():
        raise ValueError("expected implementation HEAD must be current and tracked worktree clean")
    tracked = set(subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines())
    if not set(SOURCES).issubset(tracked):
        raise ValueError("all M2-A study source files must be committed")
    return {name: sha((ROOT / name).read_bytes()) for name in SOURCES}


def archive_members(path, names):
    """Select only named members; inventory never requests raw/percentiles."""
    with np.load(BytesIO(path.read_bytes()), allow_pickle=False) as archive:
        return {name: archive[name] for name in names}


def read_metadata(guard):
    original = read_json(M1 / "run_manifest.json", guard)
    if original["status"] != "completed" or original["access_guard"]["blocked_attempts"]:
        raise ValueError("M1 provenance is not valid")
    for name in ("episode_metrics.jsonl", "look_scores.npz", "data_contract.json"):
        if sha((M1 / name).read_bytes()) != original["output_sha256"][name]:
            raise ValueError(f"M1 artifact changed: {name}")
    if sha(CONFIG.read_bytes()) != original["inputs"][str(CONFIG.relative_to(ROOT))]:
        raise ValueError("G-dev config differs from M1")
    # Strip score-bearing summaries immediately; no such field enters matching.
    rows = []
    for line in (M1 / "episode_metrics.jsonl").read_text().splitlines():
        source = json.loads(line)
        rows.append({key: source[key] for key in META_FIELDS})
    if len(rows) != 784 or len({r["key"] for r in rows}) != 784:
        raise ValueError("M1 episode census mismatch")
    layout = archive_members(M1 / "look_scores.npz", ("keys", "offsets", "ends", "tags", "ordinals"))
    if layout["keys"].tolist() != [r["key"] for r in rows]:
        raise ValueError("archive key order mismatch")
    fixtures = io_g.fixture_map_from_config(CONFIG)
    by_trace = defaultdict(list)
    for row in rows:
        by_trace[row["source_trace_id"]].append(row)
    contract = read_json(M1 / "data_contract.json", guard)
    for index, (name, expected) in enumerate(sorted(contract["trace_json_sha256"].items())):
        path = guard.check_path(ROOT / name)
        if DEV not in path.parents or path.name != "trace.json":
            raise PermissionError("metadata source is not an OPEN G-dev trace")
        data = path.read_bytes()
        if sha(data) != expected:
            raise ValueError(f"G-dev trace metadata changed: {name}")
        trace = json.loads(data)
        steps = io_g._generation_steps(trace)
        for row in by_trace[trace["trace_id"]]:
            selected = steps[row["episode_index"]]
            if any("channel_segments" not in s for s in selected):
                raise ValueError("explicit channel segments required; no routing/token-vocabulary fallback")
            row["fixture"] = fixtures[row["scenario"]]
            row["routine_template_id"] = str(trace.get("routine_template_id") or "")
            if not row["routine_template_id"]:
                raise ValueError("missing routine template metadata")
            row["steps"] = [{k: s[k] for k in ("agent_step", "global_token_offset", "output_token_count")} for s in selected]
            row["token_tags"] = list(io_g.channel_tag_array(io_g.segment_spans(selected), row["token_count"], scope="message"))
        if (index + 1) % 200 == 0:
            print(json.dumps({"phase": "metadata", "traces": index + 1}), flush=True)
    metadata = {}
    for index, row in enumerate(rows):
        start, stop = layout["offsets"][index:index + 2]
        ends = layout["ends"][start:stop]
        tags = layout["tags"][start:stop].tolist()
        expected_ends = np.array([t for a, b, _ in io_g.channel_runs(row["token_tags"], CHANNELS)
                                  for t in range(a + 7, b)], dtype=int)[:352]
        if not np.array_equal(ends, expected_ends) or tags != [row["token_tags"][int(t)] for t in ends]:
            raise ValueError("M2 metadata does not reproduce frozen M1 look grid")
        row["ends"] = ends.tolist()
        row["coordinates"] = look_geometry(row["token_tags"], row["steps"], ends)
        row["coordinate_lookup"] = {c: i for i, c in enumerate(row["coordinates"]) if c is not None}
        del row["token_tags"]
        if len(row["coordinate_lookup"]) != sum(c is not None for c in row["coordinates"]):
            raise ValueError("duplicate structural coordinate")
        metadata[row["key"]] = row
    return metadata, original


def census(rows):
    fields = ("variant", "family", "trajectory_class", "injection_channel", "tier", "fold")
    result = {"n": len(rows), **{field: dict(Counter(str(r[field]) for r in rows)) for field in fields}}
    for field in ("token_count",):
        result[field + "_quantiles"] = np.quantile([r[field] for r in rows], [0, .25, .5, .75, 1]).tolist() if rows else None
    for field in ("anchor", "x"):
        values = [r["anchor"][field] for r in rows if r["anchor"][field] is not None]
        result[field + "_quantiles"] = np.quantile(values, [0, .25, .5, .75, 1]).tolist() if values else None
    return result


def coverage_block(matches, queries, metadata):
    matched = [m for m in matches if m["status"] == "matched"]
    rejected = [m for m in matches if m["status"] != "matched"]
    query_rows = lambda group: [metadata[queries[m["query_id"]]["key"]] for m in group]
    keys = [d["key"] for m in matched for d in m["donors"]]
    return {"candidates": len(matches), "matched": len(matched),
            "status": dict(Counter(m["status"] for m in matches)),
            "candidate_donor_counts": dict(Counter(str(m["candidate_donors"]) for m in matches)),
            "complete_donor_counts": dict(Counter(str(len(m["donors"])) for m in matched)),
            "matched_horizon_cut": sum(queries[m["query_id"]]["horizon_cut"] for m in matched),
            "matched_census": census(query_rows(matched)), "unmatched_census": census(query_rows(rejected)),
            "distinct_donor_episodes": len(set(keys)),
            "distinct_donor_scenarios": len({metadata[k]["scenario"] for k in keys}),
            "max_donor_reuse": max(Counter(keys).values(), default=0)}


def inventory(output, expected):
    start = time.monotonic()
    guard = M2AccessGuard(ROOT)
    guard.install()
    output = guard.check_path(output)
    if output.parent != BASE.resolve() or output.exists():
        raise ValueError("inventory output must be a NEW immediate child of codex_g")
    source_hashes = check_implementation(expected)
    output.mkdir(parents=True)
    manifest = {"schema": "codex-g-m2a-inventory-1.0.0", "status": "started", "implementation_commit": expected,
                "started_utc": datetime.now(timezone.utc).isoformat(), "source_sha256": source_hashes,
                "policy": "score-blind complete-footprint matching; S/F separate; filtered/all separate"}
    write_json(output / "inventory_manifest.json", manifest)
    try:
        metadata, m1 = read_metadata(guard)
        targets = [r for r in metadata.values() if r["attack_bearing"] and r["anchor"]["anchor"] is not None]
        if len(targets) != 198:
            raise ValueError("shared E target census mismatch")
        queries, matches = [], []
        for target in targets:
            for kind in KINDS:
                query = {"key": target["key"], "kind": kind, **query_groups(target, np.asarray(target["ends"]), kind)}
                query_id = len(queries)
                queries.append(query)
                for scope in SCOPES:
                    for quality in QUALITIES:
                        matches.append({"query_id": query_id, **match_footprint(target, query, metadata, scope, quality)})
        serial = {key: {k: v for k, v in row.items() if k != "coordinate_lookup"} for key, row in metadata.items()}
        write_json(output / "metadata_index.json", serial)
        graph = {"schema": "codex-g-m2a-match-graph-1.0.0", "queries": queries, "matches": matches,
                 "metadata_sha256": sha((output / "metadata_index.json").read_bytes()),
                 "m1_score_sha256": m1["output_sha256"]["look_scores.npz"],
                 "m1_run_manifest_sha256": sha((M1 / "run_manifest.json").read_bytes())}
        write_json(output / "match_manifest.json", graph)
        coverage = {}
        for scope in SCOPES:
            for quality in QUALITIES:
                for kind in KINDS:
                    subset = [m for m in matches if m["scope"] == scope and m["quality"] == quality
                              and queries[m["query_id"]]["kind"] == kind]
                    coverage[f"{scope}/{quality}/{kind}"] = coverage_block(subset, queries, metadata)
        write_json(output / "coverage.json", coverage)
        manifest.update(status="completed", elapsed_seconds=time.monotonic() - start,
                        match_manifest_sha256=sha((output / "match_manifest.json").read_bytes()),
                        metadata_sha256=graph["metadata_sha256"], coverage_sha256=sha((output / "coverage.json").read_bytes()),
                        m1_inputs=m1["inputs"], m1_implementation_commit=m1["implementation_commit"],
                        m1_data_contract_sha256=m1["output_sha256"]["data_contract.json"],
                        access_guard=guard.summary())
        write_json(output / "inventory_manifest.json", manifest)
        print(json.dumps({"phase": "inventory_completed", "match_manifest_sha256": manifest["match_manifest_sha256"],
                          "matched": {k: v["matched"] for k, v in coverage.items()}, "access_guard": guard.summary()}, indent=2), flush=True)
    except Exception as error:
        manifest.update(status="failed", error=str(error), access_guard=guard.summary())
        write_json(output / "inventory_manifest.json", manifest)
        raise


def serial_fields(fields):
    return {name: value.tolist() for name, value in fields.items()}


def effect_fields(kind):
    if kind.endswith("level"):
        return ("post_residual",)
    if kind.endswith("did"):
        return ("post_residual", "did")
    return ("E_residual", "middle_residual", "X_residual", "middle_minus_E_residual", "X_minus_middle_residual")


def matrix_summary(values, rows):
    families = [r["family"] for r in rows]
    tiers = [r["family"] + "|" + r["tier"] for r in rows]
    return {"family": cluster_grid(values, families), "family_tier": cluster_grid(values, tiers)}


def grouped_summary(rows, kind, metadata):
    if not rows:
        return {"n": 0}
    names = list(rows[0]["values"]["percentile"])
    result = {"n": len(rows), "family_count": len({r["family"] for r in rows}), "means": {}, "effects": {}}
    for scale in ("percentile", "raw"):
        result["means"][scale] = {name: np.array([r["values"][scale][name] for r in rows]).mean(0).tolist() for name in names}
        result["effects"][scale] = {}
        for name in effect_fields(kind):
            values = np.array([r["values"][scale][name] for r in rows])
            result["effects"][scale][name] = {
                "residual": matrix_summary(values, rows),
                "W_minus_U": matrix_summary(values[:, 1] - values[:, 0], rows),
                "P_minus_U": matrix_summary(values[:, 2] - values[:, 0], rows),
            }
    placebo = [r for r in rows if r.get("placebo")]
    result["normal_normal_placebo"] = {"n": len(placebo), "effects": {}}
    if placebo:
        for scale in ("percentile", "raw"):
            result["normal_normal_placebo"]["effects"][scale] = {
                name: matrix_summary(np.array([r["placebo"][scale][name] for r in placebo]), placebo)
                for name in placebo[0]["placebo"][scale]}
    # Fixed single-donor-scenario influence diagnostic on the primary event effect.
    primary = "post" if kind.endswith("level") else "change" if kind.endswith("did") else "X"
    def donor_value(d):
        return np.asarray(d["percentile"]["post"]) - np.asarray(d["percentile"]["pre"]) if primary == "change" else np.asarray(d["percentile"][primary])
    result["donor_scenario_influence"] = donor_influence(
        [np.asarray(r["values"]["percentile"][f"{primary}_attack"]) for r in rows],
        [[donor_value(d) for d in r["donor_means"]] for r in rows],
        [[metadata[d["key"]]["scenario"] for d in r["donor_means"]] for r in rows])
    weights = Counter()
    for row in rows:
        for donor in row["donor_means"]:
            weights[donor["key"]] += 1 / len(row["donor_means"]) / len(rows)
    result["donor_weight_concentration"] = {"episodes": len(weights), "max_share": max(weights.values()),
                                             "squared_share_sum": sum(w * w for w in weights.values()),
                                             "top5": weights.most_common(5)}
    return result


def score(output, expected, match_sha):
    start = time.monotonic()
    guard = M2AccessGuard(ROOT)
    guard.install()
    output = guard.check_path(output)
    if output.parent != BASE.resolve() or (output / "score_manifest.json").exists():
        raise ValueError("score output must be an existing, not-yet-scored M2-A inventory")
    source_hashes = check_implementation(expected)
    inventory_manifest = read_json(output / "inventory_manifest.json", guard)
    if inventory_manifest["status"] != "completed" or source_hashes != inventory_manifest["source_sha256"]:
        raise ValueError("inventory/code freeze is not intact")
    graph_bytes = (output / "match_manifest.json").read_bytes()
    if sha(graph_bytes) != match_sha or match_sha != inventory_manifest["match_manifest_sha256"]:
        raise ValueError("match graph sha256 mismatch")
    graph = json.loads(graph_bytes)
    if sha((output / "metadata_index.json").read_bytes()) != graph["metadata_sha256"]:
        raise ValueError("metadata index changed after matching")
    if sha((M1 / "look_scores.npz").read_bytes()) != graph["m1_score_sha256"]:
        raise ValueError("M1 frozen scores changed")
    if sha((M1 / "run_manifest.json").read_bytes()) != graph["m1_run_manifest_sha256"]:
        raise ValueError("M1 provenance changed after matching")
    manifest = {"schema": "codex-g-m2a-score-1.0.0", "status": "started", "implementation_commit": expected,
                "source_sha256": source_hashes, "match_manifest_sha256": match_sha,
                "inventory_manifest_sha256": sha((output / "inventory_manifest.json").read_bytes()),
                "started_utc": datetime.now(timezone.utc).isoformat(),
                "interpretation": "development diagnostics, not FAR/recall/AUROC or a causal effect; CIs condition on frozen donors and M1 references"}
    write_json(output / "score_manifest.json", manifest)
    try:
        metadata = read_json(output / "metadata_index.json", guard)
        arrays = archive_members(M1 / "look_scores.npz", ("keys", "offsets", "raw", "percentiles"))
        if not np.isfinite(arrays["raw"]).all() or not np.isfinite(arrays["percentiles"]).all():
            raise ValueError("nonfinite M1 scores")
        scores = {key: {scale: arrays[array][arrays["offsets"][i]:arrays["offsets"][i + 1]]
                        for scale, array in (("raw", "raw"), ("percentile", "percentiles"))}
                  for i, key in enumerate(arrays["keys"].tolist())}
        readings = []
        checked_mappings = 0
        for match in graph["matches"]:
            if match["status"] != "matched":
                continue
            query = graph["queries"][match["query_id"]]
            target = metadata[query["key"]]
            item = {k: target[k] for k in ("key", "family", "tier", "trajectory_class", "injection_channel", "fold")}
            item.update(kind=query["kind"], scope=match["scope"], quality=match["quality"], query_id=match["query_id"],
                        horizon_cut=query["horizon_cut"], values={}, donor_means=[], placebo={})
            for donor in match["donors"]:
                dm = metadata[donor["key"]]
                if dm["fold"] != target["fold"] or dm["episode_index"] != target["episode_index"]:
                    raise ValueError("frozen donor eligibility disagreement")
                for name, indices in query["groups"].items():
                    mapped = donor["groups"][name]
                    if [target["coordinates"][i] for i in indices] != [dm["coordinates"][j] for j in mapped]:
                        raise ValueError("frozen coordinate mapping disagreement")
                    checked_mappings += len(indices)
                item["donor_means"].append({"key": donor["key"], "variant": dm["variant"]})
            for scale in ("percentile", "raw"):
                target_values = {name: scores[target["key"]][scale][indices] for name, indices in query["groups"].items()}
                donor_values = [{name: scores[d["key"]][scale][indices] for name, indices in d["groups"].items()} for d in match["donors"]]
                fields, donor_means = paired_fields(target_values, donor_values)
                item["values"][scale] = serial_fields(fields)
                for saved, means in zip(item["donor_means"], donor_means):
                    saved[scale] = serial_fields(means)
                if match["scope"] == "S":
                    clean = [d for d in item["donor_means"] if d["variant"] == "clean"]
                    benign = [d for d in item["donor_means"] if d["variant"] == "benign_control"]
                    if clean and benign:
                        placebo = {name: np.array([d[scale][name] for d in clean]).mean(0) - np.array([d[scale][name] for d in benign]).mean(0)
                                   for name in query["groups"]}
                        if "pre" in placebo:
                            placebo["change"] = placebo["post"] - placebo["pre"]
                        item["placebo"][scale] = serial_fields(placebo)
            readings.append(item)
        with (output / "paired_readings.jsonl").open("w") as handle:
            for item in readings:
                handle.write(json.dumps(item, ensure_ascii=False, allow_nan=False) + "\n")
        summaries, strata = {}, {}
        for scope in SCOPES:
            for quality in QUALITIES:
                for kind in KINDS:
                    key = f"{scope}/{quality}/{kind}"
                    selected = [r for r in readings if r["scope"] == scope and r["quality"] == quality and r["kind"] == kind]
                    summaries[key] = grouped_summary(selected, kind, metadata)
                    strata[key] = {}
                    for field in ("family", "tier", "trajectory_class", "injection_channel", "fold"):
                        groups = defaultdict(list)
                        for row in selected:
                            groups[str(row[field])].append(row)
                        strata[key][field] = {
                            name: {"n": len(group), "percentile": {
                                metric: np.array([r["values"]["percentile"][metric] for r in group]).mean(0).tolist()
                                for metric in effect_fields(kind)}} for name, group in sorted(groups.items())}
                    print(json.dumps({"phase": "summary", "cell": key, "n": len(selected)}), flush=True)
        write_json(output / "summary.json", summaries)
        write_json(output / "strata.json", strata)
        write_json(output / "consistency_checks.json", {
            "status": "passed", "matched_rows": len(readings), "checked_exact_coordinate_pairs": checked_mappings,
            "raw_and_percentile_finite": True, "score_source_sha256_verified": True,
            "inventory_sha256_verified": True, "metadata_grid_reproduces_M1": True})
        manifest.update(status="completed", elapsed_seconds=time.monotonic() - start,
                        peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 ** 2,
                        access_guard=guard.summary(), output_sha256={name: sha((output / name).read_bytes()) for name in (
                            "paired_readings.jsonl", "summary.json", "strata.json", "consistency_checks.json")})
        write_json(output / "score_manifest.json", manifest)
        print(json.dumps({"phase": "completed", "matched_readings": len(readings), "seconds": manifest["elapsed_seconds"],
                          "access_guard": guard.summary()}), flush=True)
    except Exception as error:
        manifest.update(status="failed", error=str(error), access_guard=guard.summary())
        write_json(output / "score_manifest.json", manifest)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("inventory", "score"))
    parser.add_argument("--output-root", type=Path, default=BASE / "m2a_transition_controls_v1")
    parser.add_argument("--expect-commit", required=True)
    parser.add_argument("--match-sha256")
    args = parser.parse_args()
    if args.phase == "inventory":
        inventory(args.output_root, args.expect_commit)
    else:
        if not args.match_sha256:
            parser.error("score phase requires --match-sha256 from the completed inventory")
        score(args.output_root, args.expect_commit, args.match_sha256)
