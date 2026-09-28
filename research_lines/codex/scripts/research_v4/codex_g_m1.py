"""Fixed G-dev-only, mechanism-first representation/stage analysis (not a detector)."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import time
from typing import Any

import numpy as np
import torch
from safetensors.torch import load as load_bytes

from research_v2 import io_g, trm3, trm3_g
from research_v4.codex_access_guard import CodexGAccessGuard
from research_v4.codex_g_m1_math import (
    BANDS, REPRESENTATIONS, STAGES, NormalPercentiles, band_scores, cluster_mean,
    diagnostic_summary, finite_mean, representations, stage_bounds, token_diagnostics,
)

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/agent_v2/codex_g"
RUN = ROOT / "artifacts/agent_v2/dataset_g/g_dev"
LABELS = ROOT / "artifacts/agent_v2/dataset_g/annotations/g_dev/final_unblinded.jsonl"
CONFIG = ROOT / "configs/dataset_g/g_dev.json"
EXPECTED_ARMS = {"attack": 352, "benign_control": 192, "clean": 192,
                 "benign_lexical": 24, "legitimate_refusal": 24}
WIDTH, HORIZON = 8, 352
VIEW = trm3_g.view_of("V1")
SOURCE_FILES = (
    "scripts/research_v4/codex_g_m1.py", "scripts/research_v4/codex_g_m1_math.py",
    "scripts/research_v4/codex_access_guard.py", "tests/test_research_v4_codex_g_m1.py",
    "docs/codex_g_plan.md", "docs/research_v4/codex_g_m1_analysis_spec.md",
    "src/research_v2/io_g.py", "src/research_v2/trm3.py", "src/research_v2/trm3_g.py",
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def mean_grid(values: np.ndarray) -> dict[str, dict[str, float | None]]:
    return {rep: {band: finite_mean(values[:, r, b]) for b, band in enumerate(BANDS)}
            for r, rep in enumerate(REPRESENTATIONS)}


def window_features(rep: torch.Tensor, tags: tuple[str, ...]) -> tuple:
    """Shared causal grid; no attack/event metadata; H counts looks, not tokens."""
    ends, means, out_tags, ordinals = trm3_g.segmented_windows(
        rep.permute(1, 0, 2, 3).reshape(rep.shape[1], -1), tags, VIEW, WIDTH)
    n = min(HORIZON, len(ends))
    return (ends[:n], means[:n].reshape(n, 3, rep.shape[2], rep.shape[3]).contiguous(),
            out_tags[:n], ordinals[:n])


def describe_interval(row: dict, bounds: tuple[int, int] | None, diag: dict) -> dict:
    if bounds is None:
        return {"status": "missing_event", "looks": 0, "dynamics": {"tokens": 0}}
    lo, hi = bounds
    h_end = row["h_end"]
    clipped = min(hi, h_end)
    selected = np.flatnonzero((row["ends"] >= lo) & (row["ends"] <= clipped))
    status = ("empty_interval" if hi < lo else "beyond_horizon" if lo > h_end
              else "no_eligible_look" if not len(selected) else "effective")
    return {
        "status": status, "bounds": [lo, hi], "clipped_end": clipped,
        "horizon_cut": hi > h_end, "looks": len(selected),
        "channels": sorted({row["tags"][int(i)] for i in selected}),
        "indices": selected,
        "dynamics": diagnostic_summary(diag, lo, clipped),
    }


def score_interval(block: dict, row: dict) -> dict:
    result = {key: value for key, value in block.items() if key != "indices"}
    indices = block.get("indices", np.zeros(0, dtype=int))
    exact = indices[row["levels"][indices] == 0]
    result.update(raw=mean_grid(row["scores"][indices]),
                  percentile=mean_grid(row["percentiles"][indices]),
                  exact_percentile=mean_grid(row["percentiles"][exact]),
                  exact_looks=len(exact),
                  exact_fraction=len(exact) / len(indices) if len(indices) else None,
                  reference_levels=dict(Counter(str(int(v)) for v in row["levels"][indices])))
    return result


def read_raw(ep: io_g.GEpisode, guard: CodexGAccessGuard) -> tuple:
    blocks = defaultdict(list)
    digest = hashlib.sha256()
    count = 0
    for step in ep.step_spans:
        first = int(step["routing_step_index_first_decode"])
        for offset in range(int(step["output_token_count"])):
            path = guard.check_path(ep.trace_dir / "steps" / f"{first + offset:06d}_decode.safetensors")
            if RUN not in path.parents:
                raise PermissionError("raw shard escaped the sole allowed G-dev pool")
            raw = path.read_bytes()
            digest.update((str(path.relative_to(ROOT)) + "\t" + sha(raw) + "\n").encode())
            shard = load_bytes(raw)
            for key in ("top_k_ids", "top_k_weights", "router_logits", "token_ids"):
                blocks[key].append(shard[key])
            count += 1
    ids, weights, logits = [torch.cat(blocks[key], dim=1)
                           for key in ("top_k_ids", "top_k_weights", "router_logits")]
    tokens = torch.cat(blocks["token_ids"])
    if (not torch.equal(ids.long(), ep.top_k_ids)
            or not torch.equal(tokens.long(), ep.token_ids)
            or tuple(logits.shape) != (24, ep.token_count, 32)
            or tuple(ids.shape) != (24, ep.token_count, 4) or count != ep.token_count):
        raise ValueError(f"raw / loader data contract failed: {trm3.trace_key(ep)}")
    return ids, weights, logits, {"shards": count, "ordered_shard_digest": digest.hexdigest()}


def aggregate_blocks(rows: list[dict], stage: str, *, exact: bool = False) -> dict:
    blocks = [row[stage] if stage == "whole" else row["stages"][stage] for row in rows]
    field = "exact_percentile" if exact else "percentile"
    output = {
        "candidate_episodes": len(rows), "status": dict(Counter(b["status"] for b in blocks)),
        "horizon_cut": sum(bool(b.get("horizon_cut")) for b in blocks),
        "looks": sum(b["looks"] for b in blocks),
        "exact_looks": sum(b.get("exact_looks", 0) for b in blocks),
        "percentile": {rep: {band: finite_mean([b[field][rep][band] for b in blocks])
                             for band in BANDS} for rep in REPRESENTATIONS},
    }
    comparisons = {}
    for rep in ("W", "P"):
        for band in BANDS:
            paired = [(b[field][rep][band] - b[field]["U"][band], row)
                      for row, b in zip(rows, blocks)
                      if b[field][rep][band] is not None and b[field]["U"][band] is not None]
            values = [v for v, _ in paired]
            families = [str(row["family"] or row["scenario"]) for _, row in paired]
            tiers = [str(row["family"] or row["scenario"]) + "|" + str(row["tier"])
                     for _, row in paired]
            comparisons[f"{rep}-U/{band}"] = {
                "family": cluster_mean(values, families),
                "family_tier": cluster_mean(values, tiers),
            }
    output["paired_differences"] = comparisons
    keys = ("uniformisation_js", "gate_concentration", "stable_layer_fraction",
            "W_change_stable", "P_change_stable", "W_change_all", "P_change_all")
    output["dynamics"] = {key: {"n": sum(b["dynamics"].get(key) is not None for b in blocks),
                                      "mean": finite_mean([b["dynamics"].get(key) for b in blocks])}
                          for key in keys}
    for key in ("tokens", "valid_layer_pairs", "stable_layer_pairs"):
        output["dynamics"][key] = sum(b["dynamics"].get(key, 0) for b in blocks)
    output["dynamics"]["stable_pairs_by_layer"] = np.sum(
        [b["dynamics"].get("stable_pairs_by_layer", [0] * 24) for b in blocks], axis=0).tolist() if blocks else [0] * 24
    output["reference_levels"] = dict(sum((Counter(b["reference_levels"]) for b in blocks), Counter()))
    return output


def paired_stages(rows: list[dict], before: str, after: str, same_channel: bool) -> dict:
    eligible = []
    for row in rows:
        a, b = row["stages"][before], row["stages"][after]
        if a["percentile"]["U"]["all"] is None or b["percentile"]["U"]["all"] is None:
            continue
        if same_channel and (len(a.get("channels", [])) != 1 or a["channels"] != b.get("channels", [])):
            continue
        eligible.append((row, a, b))
    return {rep: cluster_mean(
        [b["percentile"][rep]["all"] - a["percentile"][rep]["all"] for _, a, b in eligible],
        [row["family"] or row["scenario"] for row, _, _ in eligible]) for rep in REPRESENTATIONS}


def summarise(rows: list[dict]) -> dict:
    positives = [r for r in rows if r["attack_bearing"] and r["anchor"]["anchor"] is not None]
    subsets = {
        "normal_all": [r for r in rows if r["normal"]],
        "normal_filtered": [r for r in rows if r["normal"] and r["filter_pass"] is True],
        "silent_postinjection": [r for r in rows if r["attack_bearing"] and r["silent"]],
        "attack_preinjection_excluded": [r for r in rows if r["variant"] == "attack" and not r["attack_bearing"]],
        "attack_bearing": [r for r in rows if r["attack_bearing"]],
        "E_anchored": positives,
    }
    strata = {}
    for field in ("variant", "trajectory_class", "filter_pass", "injection_channel", "family", "tier", "fold"):
        grouped = defaultdict(list)
        for row in rows:
            # Pre-injection attack turns must not inflate the silent/class comparisons.
            if field == "trajectory_class" and row["variant"] == "attack" and not row["attack_bearing"]:
                continue
            grouped[str(row[field])].append(row)
        strata[field] = {name: {"n": len(group), "whole": aggregate_blocks(group, "whole"),
                               "stages": {s: aggregate_blocks(
                                   [r for r in group if r["attack_bearing"] and r["anchor"]["anchor"] is not None], s)
                                          for s in STAGES}}
                         for name, group in sorted(grouped.items())}
    return {
        "warning": "DESCRIPTIVE G-dev event-aligned diagnostics; not FAR, recall, AUROC, conformal p values, causality or independent confirmation.",
        "arms": dict(Counter(r["variant"] for r in rows)),
        "episodes": len(rows), "scenarios": len({r["scenario"] for r in rows}),
        "traces": len({r["source_trace_id"] for r in rows}),
        "attack_bearing": len(subsets["attack_bearing"]),
        "attack_preinjection_excluded": len(subsets["attack_preinjection_excluded"]),
        "E_exclusion_reasons": dict(Counter(r["anchor"]["reason"] for r in rows if r["attack_bearing"])),
        "injection_point_sources": dict(Counter(r["injection_point"]["source"] for r in rows if r["attack_bearing"])),
        "X_counts": {
            "labelled": sum(r["anchor"]["x"] is not None for r in positives),
            "beyond_h_end": sum(r["anchor"]["x"] is not None and r["anchor"]["x"] > r["h_end"] for r in positives),
            "X_plus16_beyond_h_end": sum(r["anchor"]["x"] is not None and r["anchor"]["x"] + 16 > r["h_end"] for r in positives),
        },
        "stages": {s: aggregate_blocks(positives, s) for s in STAGES},
        "stages_exact_reference": {s: aggregate_blocks(positives, s, exact=True) for s in STAGES},
        "paired_stage_changes": {
            "E_minus_pre_E_same_channel": paired_stages(positives, "pre_E", "E", True),
            "X_minus_E": paired_stages(positives, "E", "X", False),
            "X_minus_E_same_channel": paired_stages(positives, "E", "X", True),
        },
        "whole_subsets": {name: aggregate_blocks(group, "whole") for name, group in subsets.items()},
        "strata": strata,
    }


def run(output: Path, expected_commit: str) -> None:
    start = time.monotonic()
    guard = CodexGAccessGuard(ROOT)
    guard.install()
    output = guard.check_path(output)
    if output.parent != BASE.resolve() or output.exists():
        raise ValueError("output must be a new immediate child of codex_g; refusing overwrite")
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != expected_commit:
        raise ValueError("HEAD disagrees with expected implementation commit")
    if subprocess.check_output(["git", "diff", "HEAD", "--name-only"], cwd=ROOT, text=True).strip():
        raise ValueError("tracked worktree changes present; commit implementation before reading routing")
    tracked = set(subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines())
    if not set(SOURCE_FILES).issubset(tracked):
        raise ValueError("uncommitted study source files")
    torch.set_num_threads(4)
    source_hashes = {name: sha((ROOT / name).read_bytes()) for name in SOURCE_FILES}
    output.mkdir(parents=True)
    manifest = {
        "schema": "codex-g-m1-mechanism-diagnostics-1.0.0", "status": "started",
        "started_utc": datetime.now(timezone.utc).isoformat(), "implementation_commit": head,
        "code_and_plan_sha256": source_hashes,
        "constants": {"view": "V1", "tag_scope": "message", "width": WIDTH,
                      "horizon_looks": HORIZON, "representations": REPRESENTATIONS, "bands": BANDS,
                      "fold_key": "fixture_rank_mod", "reference_min_windows": 30,
                      "reference_min_episodes": 10, "bootstrap_replicates": 2000, "seed": 20260908,
                      "fit_token_scope": "all retained tokens of disjoint filtered-normal fit episodes"},
        "inputs": {str(p.relative_to(ROOT)): sha(guard.check_path(p).read_bytes()) for p in (LABELS, CONFIG)},
        "access_guard": guard.summary(),
    }
    write_json(output / "run_manifest.json", manifest)
    try:
        # Use a private copy of the OPEN G-dev cache only. Every native decode is from
        # checked bytes, including the frozen loader's process-local load_file binding.
        cache = output / "topk_cache"
        source_cache = ROOT / "artifacts/agent_v2/research_v4/g_routing_cache/g_dev"
        copies = 0
        if source_cache.is_dir():
            (cache / "g_dev").mkdir(parents=True)
            for path in sorted(source_cache.glob("*.safetensors")):
                source = guard.check_path(path)
                if path.is_symlink() or source.parent != source_cache.resolve():
                    raise PermissionError("unexpected shared-cache link")
                (cache / "g_dev" / path.name).write_bytes(source.read_bytes())
                copies += 1
        original_load_file = io_g.load_file
        io_g.load_file = lambda path, **kwargs: load_bytes(guard.check_path(path).read_bytes())
        loader_manifest = {}
        try:
            episodes = io_g.load_g(RUN, labels=LABELS, cache_dir=cache, tag_scope="message",
                                  verify_tokens=True, variant_overrides=io_g.VARIANT_OVERRIDES_AUTO,
                                  manifest=loader_manifest)
        finally:
            io_g.load_file = original_load_file
        if dict(Counter(e.variant for e in episodes)) != EXPECTED_ARMS:
            raise ValueError("G-dev arm census differs from the fixed study input")
        fixtures = io_g.fixture_map_from_config(CONFIG)
        table = trm3_g.fold_assignment(list(fixtures), key="fixture_rank_mod", fixtures=fixtures)
        folds = trm3_g.fold_of_episodes(episodes, table)
        if None in folds.values():
            raise ValueError("unmapped episode fold")
        anchors = trm3_g.view_anchors(episodes, VIEW)
        manifest.update(loader=loader_manifest, copied_open_cache_files=copies,
                        fold_table=table, fold_table_sha256=trm3_g.fold_table_sha256(table),
                        fixture_crosstab=trm3_g.fold_fixture_crosstab(table, fixtures))
        write_json(output / "run_manifest.json", manifest)
        print(json.dumps({"phase": "loaded", "episodes": len(episodes), "arms": EXPECTED_ARMS,
                          "fold_table_sha256": manifest["fold_table_sha256"]}), flush=True)
        q_sums = defaultdict(lambda: torch.zeros(3, 24, 32, dtype=torch.float64))
        q_counts = Counter()
        records, contracts = [], []
        trace_hashes = {}
        for index, ep in enumerate(episodes):
            key = trm3.trace_key(ep)
            ids, weights, logits, contract = read_raw(ep, guard)
            rep, gate_contract = representations(ids, weights, logits)
            contract.update(gate_contract, key=key, tokens=ep.token_count)
            contracts.append(contract)
            trace_path = guard.check_path(ep.trace_dir / "trace.json")
            if str(trace_path.relative_to(ROOT)) not in trace_hashes:
                trace_hashes[str(trace_path.relative_to(ROOT))] = sha(trace_path.read_bytes())
            if ep.normal and ep.filter_pass is True:
                tags = np.asarray(ep.channel_tags)
                for tag in VIEW.channels:
                    mask = torch.from_numpy(tags == tag)
                    if bool(mask.any()):
                        q_sums[(folds[key], tag)] += rep[:, mask].double().sum(1)
                        q_counts[(folds[key], tag)] += int(mask.sum())
            ends, means, tags, ordinals = window_features(rep, ep.channel_tags)
            row = {
                "key": key, "source_trace_id": ep.source_trace_id, "scenario": ep.pair_group_id,
                "variant": ep.variant, "normal": ep.normal, "filter_pass": ep.filter_pass,
                "episode_index": ep.episode_index, "injection_channel": ep.channel,
                "family": ep.attack_family_id, "tier": ep.wording_tier, "domain_group": ep.domain_group,
                "trajectory_class": ep.labels.get("trajectory_class", ""),
                "silent": bool(ep.labels.get("silent")), "attack_bearing": trm3_g.injection_present(ep),
                "injection_point": trm3_g.injection_point(ep), "anchor": anchors[key].to_json(),
                "fold": folds[key], "token_count": ep.token_count,
                "ends": ends, "means": means, "tags": tags, "ordinals": ordinals,
                "h_end": int(ends[-1]) if len(ends) else -1,
            }
            diag = token_diagnostics(rep, ids, ep.channel_tags,
                                     [int(s["global_token_offset"]) for s in ep.step_spans])
            bounds = stage_bounds(anchors[key].anchor, anchors[key].x) if row["attack_bearing"] else stage_bounds(None, None)
            row["stages"] = {s: describe_interval(row, bounds[s], diag) for s in STAGES}
            row["whole"] = describe_interval(row, (0, row["h_end"]), diag)
            records.append(row)
            del rep, logits, weights, ids, diag
            if (index + 1) % 20 == 0 or index + 1 == len(episodes):
                print(json.dumps({"phase": "routing", "episodes": index + 1,
                                  "seconds": round(time.monotonic() - start, 1),
                                  "rss_gib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 ** 2, 3)}), flush=True)
        fold_reports = {}
        for fold in range(3):
            fit, reference = (fold + 1) % 3, (fold + 2) % 3
            q_global = sum(q_sums[(fit, tag)] for tag in VIEW.channels)
            global_count = sum(q_counts[(fit, tag)] for tag in VIEW.channels)
            if not global_count:
                raise ValueError("no filtered-normal fit tokens")
            q = {tag: (q_sums[(fit, tag)] / q_counts[(fit, tag)] if q_counts[(fit, tag)]
                       else q_global / global_count) for tag in VIEW.channels}
            target = [r for r in records if r["fold"] == fold]
            ref = [r for r in records if r["fold"] == reference and r["normal"] and r["filter_pass"] is True]
            reference_scores = []
            for row in target + ref:
                if len(row["ends"]):
                    normal = torch.stack([q[tag] for tag in row["tags"]], dim=1)
                    scores = band_scores(row["means"].permute(1, 0, 2, 3), normal)
                else:
                    scores = np.zeros((0, 3, 4), dtype=float)
                if row["fold"] == fold:
                    row["scores"] = scores
                else:
                    reference_scores.append({"key": row["key"], "episode_index": row["episode_index"],
                                             "scores": scores, "tags": row["tags"], "ordinals": row["ordinals"]})
            percentile = NormalPercentiles(reference_scores)
            for row in target:
                row["percentiles"], row["levels"] = percentile.transform(
                    row["scores"], row["tags"], row["ordinals"], row["episode_index"])
            fold_reports[str(fold)] = {
                "fit": fit, "reference": reference, "eval": fold,
                "normal_fit_episodes": sum(r["fold"] == fit and r["normal"] and r["filter_pass"] is True for r in records),
                "normal_reference_episodes": len(ref), "eval_episodes": len(target),
                "fit_tokens_by_channel": {tag: q_counts[(fit, tag)] for tag in VIEW.channels},
                "fit_q_fallback_channels": [tag for tag in VIEW.channels if not q_counts[(fit, tag)]],
                "reference_keys": {repr(key): len(value) for key, value in percentile.references.items()},
                "reference_levels": dict(Counter(str(int(level)) for row in target for level in row["levels"])),
            }
            np.savez_compressed(output / f"q_fold{fold}.npz", **{tag: value.numpy() for tag, value in q.items()})
            print(json.dumps({"phase": "scored", "fold": fold}), flush=True)
        metrics = []
        for row in records:
            clean = {key: value for key, value in row.items() if key not in {
                "means", "ends", "tags", "ordinals", "scores", "percentiles", "levels", "stages", "whole"}}
            clean["stages"] = {s: score_interval(row["stages"][s], row) for s in STAGES}
            clean["whole"] = score_interval(row["whole"], row)
            metrics.append(clean)
        offsets = np.cumsum([0] + [len(row["ends"]) for row in records])
        np.savez_compressed(output / "look_scores.npz", keys=np.array([r["key"] for r in records]),
                            offsets=offsets, ends=np.concatenate([r["ends"] for r in records]),
                            tags=np.concatenate([np.asarray(r["tags"]) for r in records]),
                            ordinals=np.concatenate([r["ordinals"] for r in records]),
                            raw=np.concatenate([r["scores"] for r in records]),
                            percentiles=np.concatenate([r["percentiles"] for r in records]),
                            reference_levels=np.concatenate([r["levels"] for r in records]))
        with (output / "episode_metrics.jsonl").open("w") as handle:
            for row in metrics:
                handle.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        write_json(output / "data_contract.json", {
            "episodes": len(contracts), "tokens_and_shards": sum(c["shards"] for c in contracts),
            "gate_simplex_max_error": max(c["gate_simplex_max_error"] for c in contracts),
            "gate_reconstruction_max_error": max(c["gate_reconstruction_max_error"] for c in contracts),
            "per_episode": contracts, "trace_json_sha256": trace_hashes,
        })
        write_json(output / "fold_reference_diagnostics.json", fold_reports)
        print(json.dumps({"phase": "summarising"}), flush=True)
        summary = summarise(metrics)
        write_json(output / "summary.json", summary)
        manifest.update(status="completed", finished_utc=datetime.now(timezone.utc).isoformat(),
                        elapsed_seconds=time.monotonic() - start,
                        peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 ** 2,
                        access_guard=guard.summary(),
                        output_sha256={name: sha((output / name).read_bytes()) for name in (
                            "episode_metrics.jsonl", "look_scores.npz", "summary.json", "data_contract.json",
                            "fold_reference_diagnostics.json")})
        write_json(output / "run_manifest.json", manifest)
        print(json.dumps({"phase": "completed", "output": str(output),
                          "seconds": manifest["elapsed_seconds"], "blocked_attempts": guard.blocked_attempts}), flush=True)
    except Exception as error:
        manifest.update(status="failed", error_type=type(error).__name__, error=str(error),
                        elapsed_seconds=time.monotonic() - start, access_guard=guard.summary())
        write_json(output / "run_manifest.json", manifest)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=BASE / "m1_representation_stages_v1")
    parser.add_argument("--expect-commit", required=True)
    args = parser.parse_args()
    run(args.output_root, args.expect_commit)


if __name__ == "__main__":
    main()
