"""Normal-first, source-frozen conditional mechanism analysis on OPEN G-dev."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from io import BytesIO
import json
import resource
import time
from unittest.mock import patch

import numpy as np
import torch

from research_v2 import io_g, trm3, trm3_g
from research_v4 import run_detectors_g as harness
from research_v4.codex_g_m1 import BASE, CONFIG, EXPECTED_ARMS, LABELS, ROOT, RUN, sha, write_json
from research_v4.codex_g_m1_math import stage_bounds
from research_v4.codex_g_m2a import META_FIELDS
from research_v4.codex_g_m3 import guarded_loader, normal_trace_paths
from research_v4.codex_g_m4 import M2, M3, OUT as M4, M4AccessGuard, THRESHOLD_SHA, aggregate, cohorts, no_cache_write, read_json
from research_v4.codex_g_m4_math import VIEW
from research_v4.codex_g_m5_math import FIELDS, LEVELS, ConditionalBank, causal_observations, compare_on_queries, matched_controls

OUT = BASE / "m5_causal_history_probability_v1"
M4_MANIFEST_SHA = "f1be67a2985b13aab7b6b57e704f1e5d54a34f0c6b021eaee23401871c27e531"
SOURCES = (
    "docs/research_v4/codex_g_m5_analysis_spec.md", "scripts/research_v4/codex_g_m5.py",
    "scripts/research_v4/codex_g_m5_math.py", "tests/test_research_v4_codex_g_m5.py",
    "scripts/research_v4/codex_access_guard.py", "scripts/research_v4/codex_g_m1.py",
    "scripts/research_v4/codex_g_m1_math.py", "scripts/research_v4/codex_g_m2a.py",
    "scripts/research_v4/codex_g_m2a_math.py", "scripts/research_v4/codex_g_m3.py",
    "scripts/research_v4/codex_g_m3_statistics.py", "scripts/research_v4/codex_g_m4.py",
    "scripts/research_v4/codex_g_m4_math.py", "scripts/research_v4/run_detectors_g.py",
    "src/research_v2/io_g.py", "src/research_v2/trm3.py", "src/research_v2/trm3_g.py",
)


class M5AccessGuard(M4AccessGuard):
    def __init__(self, root, stage):
        super().__init__(root)
        self.stage = stage

    def check_path(self, path):
        p = super().check_path(path)
        if self.stage == "normal" and ("--attack" in str(p) or "attack" in p.parts):
            self.blocked_attempts += 1
            raise PermissionError("M5 normal stage refuses attack trace/cache content")
        return p


def freeze_sources(expected):
    head = harness.git_output("rev-parse", "HEAD")
    if head != expected or harness.git_output("diff", "HEAD", "--name-only"):
        raise ValueError("exact source commit and clean tracked HEAD required")
    if not set(SOURCES).issubset(set(harness.git_output("ls-files").splitlines())):
        raise ValueError("commit all M5 sources before data analysis")
    return {name: sha((ROOT / name).read_bytes()) for name in SOURCES}


def load_inputs(stage):
    hashes = {}
    def verify(path, expected):
        body = path.read_bytes()
        if sha(body) != expected:
            raise ValueError(f"input changed: {path}")
        hashes[str(path.relative_to(ROOT))] = expected
        return body
    prior = json.loads(verify(M4 / "run_manifest.json", M4_MANIFEST_SHA))
    assert prior["status"] == "completed" and prior["access_guard"]["blocked_attempts"] == 0
    expected = prior["input_sha256"]
    frozen = json.loads(verify(M3 / "calibrate/threshold_manifest.json", THRESHOLD_SHA))
    path = M2 / "metadata_index.json"
    metadata = json.loads(verify(path, expected[str(path.relative_to(ROOT))]))
    config = json.loads(verify(CONFIG, expected[str(CONFIG.relative_to(ROOT))]))
    verify(LABELS, expected[str(LABELS.relative_to(ROOT))])
    paths = list(io_g.iter_trace_paths(RUN))
    if stage == "normal":
        paths = normal_trace_paths(paths, config["scenarios"])
    for path in paths:
        verify(path, expected[str(path.relative_to(ROOT))])
    for meta in metadata.values():
        if stage == "normal" and meta["variant"] not in io_g.NORMAL_VARIANTS:
            continue
        stem = f'{meta["source_trace_id"]}--ep{meta["episode_index"]}'
        for directory, suffix in (("topk_cache", ".safetensors"), ("logit_cache", ".logits.safetensors")):
            path = M3 / directory / "g_dev" / (stem + suffix)
            verify(path, expected[str(path.relative_to(ROOT))])
    return frozen, metadata, paths, hashes


def observe(ep, meta, rare):
    ends, _, tags, ordinals = trm3_g.segmented_windows(
        torch.zeros((ep.top_k_ids.shape[1], 1), dtype=torch.float64), ep.channel_tags, VIEW, 8)
    ends, ordinals = ends[:352], ordinals[:352]
    np.testing.assert_array_equal(ends, meta["ends"])
    return causal_observations(ep.top_k_ids, ep.probabilities(cache_dir=M3 / "logit_cache"), ep.channel_tags,
        [int(s["global_token_offset"]) for s in ep.step_spans], rare, ends, ordinals, ep.episode_index)


def phases(meta):
    e, x = meta["anchor"]["anchor"], meta["anchor"]["x"]
    result = stage_bounds(e, x) if meta["attack_bearing"] else stage_bounds(None, None)
    result["whole"] = (0, meta["h_end"])
    result["E_strict_preX"] = (e, min(e + 16, x - 1)) if meta["attack_bearing"] and e is not None and x is not None else None
    result["E_noX"] = (e, e + 16) if meta["attack_bearing"] and e is not None and x is None else None
    return result


def evaluate_episode(obs, meta, banks):
    controls = matched_controls(obs, banks)
    row = {k: meta[k] for k in META_FIELDS}
    row.update(original_looks=obs["original_looks"], eligible_looks=obs["eligible_looks"], groups={})
    for stage, span in phases(meta).items():
        when = (obs["ends"] >= span[0]) & (obs["ends"] <= span[1]) if span else np.zeros(len(obs["ends"]), dtype=bool)
        for status, selected in (("selected", True), ("unselected", False)):
            eligible = when & (obs["selected"] == selected)
            cell = compare_on_queries(obs, controls, eligible)
            cell.update(missing_anchor=span is None, horizon_cut=bool(span and span[1] > meta["h_end"]))
            row["groups"][f"{status}/{stage}"] = cell
    return row


def summarize(rows):
    out = {}
    for cohort, chosen in cohorts(rows).items():
        results = {}
        for group in sorted({g for r in chosen for g in r["groups"]}):
            available = [r for r in chosen if r["groups"][group]["eligible_events"]]
            cell = {"episodes": len(chosen), "eligible_episodes": len(available),
                    "eligible_events": sum(r["groups"][group]["eligible_events"] for r in chosen),
                    "missing_anchor_episodes": sum(r["groups"][group]["missing_anchor"] for r in chosen),
                    "horizon_cut_episodes": sum(r["groups"][group]["horizon_cut"] for r in chosen)}
            for mode, levels in (("native", LEVELS), ("aligned_H", ("B", "C", "H")), ("aligned_HP", ("H", "HP"))):
                cell[mode] = {}
                for level in levels:
                    matched = [r for r in available if r["groups"][group][mode][level]["events"]]
                    values = [r["groups"][group][mode][level] for r in matched]
                    cell[mode][level] = {"matched_episodes": len(matched), "matched_events": sum(v["events"] for v in values),
                        **{field: aggregate([v[field] for v in values], matched) for field in ("raw", "control", "residual")}}
            results[group] = cell
        out[cohort] = results
    return out


def stratify(rows):
    out = {}
    for group in sorted({g for r in rows if r["attack_bearing"] for g in r["groups"]}):
        out[group] = {}
        for mode, levels in (("aligned_H", ("B", "C", "H")), ("aligned_HP", ("H", "HP"))):
            out[group][mode] = {}
            for level in levels:
                selected = [r for r in rows if r["attack_bearing"] and r["groups"][group][mode][level]["events"]]
                cells = {}
                for field in ("family", "domain_group", "injection_channel", "tier", "fold", "trajectory_class"):
                    cells[field] = {}
                    for value in sorted({str(r[field]) for r in selected}):
                        members = [r for r in selected if str(r[field]) == value]
                        cells[field][value] = {"n": len(members), "mean_residual": np.mean(
                            [r["groups"][group][mode][level]["residual"] for r in members], 0).tolist()}
                out[group][mode][level] = cells
    return out


def row_fingerprint(rows):
    return sha(json.dumps(sorted(rows, key=lambda r: r["key"]), sort_keys=True, separators=(",", ":"), allow_nan=False).encode())


def forbid_fit(*args, **kwargs):
    raise AssertionError("M5 score stage may only restore frozen normal banks")


def run(stage, commit, bank_sha):
    started = time.monotonic()
    guard = M5AccessGuard(ROOT, stage)
    guard.install()
    sources = freeze_sources(commit)
    target_dir = OUT / stage
    if target_dir.exists():
        raise ValueError("preserve prior result: new stage output required")
    saved = None
    if stage == "score":
        normal_log = read_json(OUT / "normal/run_manifest.json")
        if normal_log["status"] != "completed" or normal_log["access_guard"]["blocked_attempts"]:
            raise ValueError("normal stage did not complete validly")
        if sha((OUT / "normal/normal_freeze.json").read_bytes()) != normal_log["output_sha256"]["normal_freeze.json"]:
            raise ValueError("normal freeze document changed")
        saved = read_json(OUT / "normal/normal_freeze.json")
        if saved["source_sha256"] != sources or saved["implementation_commit"] != commit:
            raise ValueError("source changed after normal freeze")
        if not bank_sha or bank_sha != saved["bank_sha256"] or sha((OUT / "normal/banks.npz").read_bytes()) != bank_sha:
            raise ValueError("frozen normal bank hash required")
        for name, digest in saved["normal_output_sha256"].items():
            if sha((OUT / "normal" / name).read_bytes()) != digest:
                raise ValueError("normal result changed after freeze")
    frozen, metadata, paths, input_hashes = load_inputs(stage)
    if stage == "score":
        input_hashes[str((OUT / "normal/run_manifest.json").relative_to(ROOT))] = sha((OUT / "normal/run_manifest.json").read_bytes())
        input_hashes[str((OUT / "normal/normal_freeze.json").relative_to(ROOT))] = sha((OUT / "normal/normal_freeze.json").read_bytes())
    target_dir.mkdir(parents=True)
    manifest = {"schema": "codex-g-m5-history-mechanisms-1.0.0", "status": "started", "stage": stage,
        "implementation_commit": commit, "source_sha256": sources, "input_sha256": input_hashes,
        "fields": FIELDS, "started_utc": datetime.now(timezone.utc).isoformat(),
        "role": "development mechanism evidence; no detector/FAR/recall or full-identity conditional-information claim"}
    write_json(target_dir / "run_manifest.json", manifest)
    rows, arrays, reports = [], {}, {}
    with guarded_loader(guard, paths), patch.object(io_g, "save_file", no_cache_write):
        episodes = io_g.load_g(RUN, labels=LABELS, cache_dir=M3 / "topk_cache", tag_scope="message",
                              variant_overrides="auto", verify_tokens=True)
        census = dict(Counter(e.variant for e in episodes))
        wanted = {k: v for k, v in EXPECTED_ARMS.items() if stage == "score" or k in io_g.NORMAL_VARIANTS}
        if census != wanted:
            raise ValueError(f"unexpected census: {census}")
        for ep in episodes:
            if trm3_g.view_anchors([ep], VIEW)[trm3.trace_key(ep)].to_json() != metadata[trm3.trace_key(ep)]["anchor"]:
                raise ValueError("anchor changed")
        if saved:
            with np.load(BytesIO((OUT / "normal/banks.npz").read_bytes()), allow_pickle=False) as z:
                arrays = {k: z[k] for k in z.files}
        for fold in range(3):
            rare = np.array(frozen["cells"]["S"]["folds"][str(fold)]["statistics"]["S"]["q"]) < .02
            if stage == "normal":
                banks = {level: ConditionalBank() for level in LEVELS}
                refs = [e for e in episodes if metadata[trm3.trace_key(e)]["fold"] == (fold + 2) % 3 and e.normal and e.filter_pass is True]
                for ep in refs:
                    key = trm3.trace_key(ep)
                    obs = observe(ep, metadata[key], rare)
                    for level, bank in banks.items():
                        bank.add(key, obs["keys"][level], obs["values"], normal=ep.normal, filtered=ep.filter_pass)
                reports[str(fold)] = {level: bank.finalize().report for level, bank in banks.items()}
                for level, bank in banks.items():
                    for field, value in bank.export().items():
                        arrays[f"f{fold}__{level}__{field}"] = value
                print(json.dumps({"fold": fold, "phase": "normal_banks", "report": reports[str(fold)]}), flush=True)
            else:
                banks = {level: ConditionalBank.restore({field: arrays[f"f{fold}__{level}__{field}"]
                         for field in ("codes", "counts", "means")}) for level in LEVELS}
            target = [e for e in episodes if metadata[trm3.trace_key(e)]["fold"] == fold]
            for i, ep in enumerate(target):
                key = trm3.trace_key(ep)
                rows.append(evaluate_episode(observe(ep, metadata[key], rare), metadata[key], banks))
                if (i + 1) % 50 == 0:
                    print(json.dumps({"fold": fold, "processed": i + 1, "total": len(target), "seconds": time.monotonic() - started}), flush=True)
    rows.sort(key=lambda r: r["key"])
    if stage == "score":
        normals = [r for r in rows if r["variant"] in io_g.NORMAL_VARIANTS]
        if row_fingerprint(normals) != saved["normal_fingerprint"]:
            raise ValueError("normal per-episode readings failed exact replay")
    with (target_dir / "episode_readings.jsonl").open("w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    write_json(target_dir / "summary.json", summarize(rows))
    write_json(target_dir / "strata.json", stratify(rows))
    write_json(target_dir / "checks.json", {"episodes": len(rows), "census": census,
        "original_looks": sum(r["original_looks"] for r in rows), "eligible_looks": sum(r["eligible_looks"] for r in rows),
        "nested_coverage_and_binary_history_invariants": True, "normal_exact_replay": stage == "score",
        "row_fingerprint": row_fingerprint(rows)})
    if stage == "normal":
        np.savez_compressed(target_dir / "banks.npz", **arrays)
        write_json(target_dir / "bank_coverage.json", reports)
        write_json(target_dir / "normal_freeze.json", {
            "implementation_commit": commit, "source_sha256": sources, "input_sha256": input_hashes,
            "bank_sha256": sha((target_dir / "banks.npz").read_bytes()), "normal_fingerprint": row_fingerprint(rows),
            "normal_output_sha256": {name: sha((target_dir / name).read_bytes()) for name in
                ("episode_readings.jsonl", "summary.json", "strata.json", "checks.json", "bank_coverage.json")},
            "role": "normal donor means and counts frozen; no threshold or new online calibration"})
    if sources != {name: sha((ROOT / name).read_bytes()) for name in SOURCES} or guard.blocked_attempts:
        raise ValueError("source changed or access contract failed")
    manifest.update(status="completed", elapsed_seconds=time.monotonic() - started,
        peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2, access_guard=guard.summary(),
        output_sha256={p.name: sha(p.read_bytes()) for p in target_dir.iterdir() if p.is_file() and p.name != "run_manifest.json"},
        bank_sha256=sha((OUT / "normal/banks.npz").read_bytes()))
    write_json(target_dir / "run_manifest.json", manifest)
    print(json.dumps({k: manifest[k] for k in ("status", "elapsed_seconds", "peak_rss_gib", "bank_sha256", "access_guard")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("normal", "score"), required=True)
    parser.add_argument("--freeze-commit", required=True)
    parser.add_argument("--bank-sha256")
    args = parser.parse_args()
    torch.set_num_threads(4)
    if args.stage == "score":
        with patch.object(ConditionalBank, "add", forbid_fit), patch.object(ConditionalBank, "finalize", forbid_fit):
            run(args.stage, args.freeze_commit, args.bank_sha256)
    else:
        run(args.stage, args.freeze_commit, args.bank_sha256)
