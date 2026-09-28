"""Source-frozen, OPEN G-dev-only mechanism analysis; no new online detector."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from io import BytesIO
import json
from pathlib import Path
import resource
import time
from unittest.mock import patch

import numpy as np
import torch
from safetensors.torch import load as load_bytes

from research_v2 import io_g, trm3, trm3_g
from research_v4 import run_detectors_g as harness
from research_v4.codex_access_guard import CodexGAccessGuard
from research_v4.codex_g_m1 import BASE, CONFIG, EXPECTED_ARMS, LABELS, ROOT, RUN, sha, write_json
from research_v4.codex_g_m1_math import NormalPercentiles, stage_bounds
from research_v4.codex_g_m2a import META_FIELDS, archive_members, effect_fields, matrix_summary
from research_v4.codex_g_m2a_math import KINDS, QUALITIES, SCOPES, paired_fields
from research_v4.codex_g_m4_math import (
    FEATURES, ENTRY_FIELDS, STABLE_FIELDS, TAGS, VIEW, NormalEventBank,
    local_events, summarize_events, tensors, window_features,
)

OUT = BASE / "m4_rare_probability_mechanisms_v1"
M2 = BASE / "m2a_transition_controls_v1"
M2B = BASE / "m2b_probability_components_v1"
M3 = BASE / "m3_online_mass_v1"
MATCH_SHA = "3e64a8824eb60afe8dd7af352a455c68606f91c1eea5dd1aa90d7128b90e9a91"
THRESHOLD_SHA = "efc9b415aecfc303aa0030983624a97c66b6a3dd16805d619e692f2dad9431de"
SOURCES = (
    "docs/research_v4/codex_g_m4_analysis_spec.md", "scripts/research_v4/codex_g_m4.py",
    "scripts/research_v4/codex_g_m4_math.py", "tests/test_research_v4_codex_g_m4.py",
    "scripts/research_v4/codex_access_guard.py", "scripts/research_v4/codex_g_m1.py",
    "scripts/research_v4/codex_g_m1_math.py", "scripts/research_v4/codex_g_m2a.py",
    "scripts/research_v4/codex_g_m2a_math.py", "scripts/research_v4/run_detectors_g.py",
    "src/research_v2/io_g.py", "src/research_v2/trm3.py", "src/research_v2/trm3_g.py",
)


class M4AccessGuard(CodexGAccessGuard):
    def check_path(self, path):
        resolved = super().check_path(path)
        if resolved.suffix == ".safetensors" and not any((M3 / part).resolve() in resolved.parents for part in ("topk_cache", "logit_cache")):
            self.blocked_attempts += 1
            raise PermissionError("M4 permits only the already-audited private M3 cache, no raw routing or other pools")
        return resolved


def no_cache_write(*args, **kwargs):
    raise AssertionError("M4 must not generate or alter routing caches")


def read_json(path):
    return json.loads(path.read_text())


def inputs():
    hashes = {}
    def verify(path, expected=None):
        body = path.read_bytes()
        digest = sha(body)
        if expected is not None and digest != expected:
            raise ValueError(f"prior input changed: {path.name}")
        hashes[str(path.relative_to(ROOT))] = digest
        return body
    frozen = json.loads(verify(M3 / "calibrate/threshold_manifest.json", THRESHOLD_SHA))
    for name, expected in frozen["inputs"].items(): verify(ROOT / name, expected)
    graph = json.loads(verify(M2 / "match_manifest.json", MATCH_SHA))
    metadata = json.loads(verify(M2 / "metadata_index.json", graph["metadata_sha256"]))
    for stage in ("calibrate", "score"):
        log = json.loads(verify(M3 / stage / "run_manifest.json"))
        if log["status"] != "completed" or log["access_guard"]["blocked_attempts"]:
            raise ValueError("M3 run was not valid")
        for name, expected in log["output_sha256"].items(): verify(M3 / stage / name, expected)
    audit = json.loads(verify(M3 / "audit/checks.json"))
    body = verify(M3 / "audit/look_streams.npz", audit["streams_sha256"])
    with np.load(BytesIO(body), allow_pickle=False) as z:
        prior = {k: z[k] for k in z.files}
    m2b = json.loads(verify(M2B / "run_manifest.json"))
    reference = json.loads(verify(M2B / "normal_reference.json", m2b["output_sha256"]["normal_reference.json"]))
    for record in metadata.values():
        stem = f'{record["source_trace_id"]}--ep{record["episode_index"]}'
        for directory, suffix in (("topk_cache", ".safetensors"), ("logit_cache", ".logits.safetensors")):
            path = M3 / directory / "g_dev" / (stem + suffix)
            verify(path, frozen["normal_cache_sha256" if directory == "topk_cache" else "normal_logits_sha256"].get("g_dev/" + path.name))
    # Every current trace is also checked against M3's recorded opened input set.
    contract = read_json(M3 / "score/data_contract.json")
    for path, expected in contract["trace_sha256"].items(): verify(ROOT / path, expected)
    return hashes, frozen, graph, metadata, reference, prior


def parameters(frozen, reference, fold):
    fs = str(fold)
    s = frozen["cells"]["S"]["folds"][fs]["statistics"]["S"]
    cu = frozen["cells"]["CU"]["folds"][fs]["statistics"]["CU"]["payload"]
    q_select = torch.tensor(s["q"], dtype=torch.float64)
    q_channel = torch.tensor(cu["q"], dtype=torch.float64)
    counts = torch.tensor(cu["channel_token_counts"], dtype=torch.float64)
    assert cu["tags"] == list(TAGS)
    q_global = (q_channel * counts[:, None, None]).sum(0) / counts.sum()
    m0 = torch.tensor([reference[fs]["m0_by_channel_layer"][tag] for tag in TAGS], dtype=torch.float64)
    return q_select, q_channel, q_global, m0


def measure(ep, meta, params):
    p, mask, ids = tensors(ep.top_k_ids, ep.probabilities(cache_dir=M3 / "logit_cache"))
    q, qc, qg, m0 = params
    ends, raw, tags, ordinals, error = window_features(p, mask, ep.channel_tags, q, qc, qg, m0)
    np.testing.assert_array_equal(ends, meta["ends"])
    rarity = torch.where(q < .02, -q.log(), 0.)
    local = local_events(p, mask, ids, ep.channel_tags, [int(s["global_token_offset"]) for s in ep.step_spans], rarity, meta["h_end"])
    record = {k: meta[k] for k in META_FIELDS}
    record.update(ends=ends, raw=raw, tags=tags, ordinals=ordinals, identity_error=error)
    return record, local


def aggregate(values, rows):
    if not values:
        return {"n": 0, "mean": None}
    values = np.asarray(values, dtype=float)
    if not np.isfinite(values).all(): raise ValueError("nonfinite aggregate")
    result = {"n": len(values), "mean": values.mean(0).tolist()}
    if all(r["attack_bearing"] and r["family"] for r in rows):
        result["bootstrap"] = matrix_summary(values, rows)
    return result


def cohorts(rows):
    return {
        "normal_all": [r for r in rows if r["variant"] in io_g.NORMAL_VARIANTS],
        "normal_filtered": [r for r in rows if r["variant"] in io_g.NORMAL_VARIANTS and r["filter_pass"] is True],
        "benign_lexical": [r for r in rows if r["variant"] == io_g.BENIGN_LEXICAL],
        "legitimate_refusal": [r for r in rows if r["variant"] == io_g.LEGITIMATE_REFUSAL],
        "attack_bearing": [r for r in rows if r["attack_bearing"]],
        "silent_injected": [r for r in rows if r["attack_bearing"] and r["silent"]],
        "pre_injection": [r for r in rows if r["variant"] == io_g.ATTACK and not r["attack_bearing"]],
    }


def pair_readings(graph, metadata, scores):
    out, mappings = [], 0
    for match in graph["matches"]:
        if match["status"] != "matched": continue
        query = graph["queries"][match["query_id"]]
        target = metadata[query["key"]]
        row = {k: target[k] for k in META_FIELDS}
        row.update(query_id=match["query_id"], kind=query["kind"], scope=match["scope"], quality=match["quality"],
                   values={}, placebo={}, horizon_cut=query["horizon_cut"])
        for donor in match["donors"]:
            dm = metadata[donor["key"]]
            assert dm["fold"] == target["fold"] and dm["episode_index"] == target["episode_index"]
            for group, indices in query["groups"].items():
                assert [target["coordinates"][i] for i in indices] == [dm["coordinates"][i] for i in donor["groups"][group]]
                mappings += len(indices)
        for scale in ("raw", "percentile"):
            a = {g: scores[target["key"]][scale][ix] for g, ix in query["groups"].items()}
            ds = [{g: scores[d["key"]][scale][ix] for g, ix in d["groups"].items()} for d in match["donors"]]
            fields, means = paired_fields(a, ds)
            row["values"][scale] = {k: v.tolist() for k, v in fields.items()}
            clean = [v for v, d in zip(means, match["donors"]) if metadata[d["key"]]["variant"] == "clean"]
            benign = [v for v, d in zip(means, match["donors"]) if metadata[d["key"]]["variant"] == "benign_control"]
            if match["scope"] == "S" and clean and benign:
                placebo = {g: np.mean([v[g] for v in clean], 0) - np.mean([v[g] for v in benign], 0) for g in query["groups"]}
                if "pre" in placebo: placebo["change"] = placebo["post"] - placebo["pre"]
                row["placebo"][scale] = {k: v.tolist() for k, v in placebo.items()}
        out.append(row)
    if len(out) != 305: raise ValueError("frozen matching coverage changed")
    return out, mappings


def summarize_pairs(readings):
    summaries, strata = {}, {}
    for scope in SCOPES:
        for quality in QUALITIES:
            for kind in KINDS:
                key = f"{scope}/{quality}/{kind}"
                rows = [r for r in readings if (r["scope"], r["quality"], r["kind"]) == (scope, quality, kind)]
                cell = {"n": len(rows), "effects": {}, "means": {}, "normal_normal_placebo": {}}
                if rows:
                    for scale in ("raw", "percentile"):
                        cell["effects"][scale] = {field: aggregate([r["values"][scale][field] for r in rows], rows) for field in effect_fields(kind)}
                        cell["means"][scale] = {field: np.mean([r["values"][scale][field] for r in rows], 0).tolist() for field in rows[0]["values"][scale]}
                        placebo = [r for r in rows if r["placebo"]]
                        cell["normal_normal_placebo"][scale] = {field: aggregate([r["placebo"][scale][field] for r in placebo], placebo)
                                                              for field in placebo[0]["placebo"][scale]} if placebo else {}
                summaries[key] = cell
                strata[key] = {}
                for field in ("family", "tier", "domain_group", "injection_channel", "fold", "trajectory_class"):
                    groups = defaultdict(list)
                    for row in rows: groups[str(row[field])].append(row)
                    strata[key][field] = {name: {"n": len(group), "effects": {scale: {
                        metric: np.mean([r["values"][scale][metric] for r in group], 0).tolist() for metric in effect_fields(kind)}
                        for scale in ("raw", "percentile")}} for name, group in groups.items()}
    return summaries, strata


def full_intervals(records):
    for row in records:
        bounds = stage_bounds(row["anchor"]["anchor"], row["anchor"]["x"]) if row["attack_bearing"] else stage_bounds(None, None)
        bounds["whole"] = (0, row["h_end"])
        row["intervals"] = {}
        for name, span in bounds.items():
            ix = np.flatnonzero((row["ends"] >= span[0]) & (row["ends"] <= span[1])) if span else []
            row["intervals"][name] = {"looks": len(ix), "missing_event": span is None,
                 "horizon_cut": bool(span and span[1] > row["h_end"]),
                 **{scale: row[scale][ix].mean(0).tolist() if len(ix) else None for scale in ("raw", "percentile")}}
    summary = {}
    for cohort, rows in cohorts(records).items():
        summary[cohort] = {"episodes": len(rows), "intervals": {}}
        for stage in ("whole", "pre_E", "E", "E_to_X", "X", "post_X"):
            available = [r for r in rows if r["intervals"][stage]["looks"]]
            summary[cohort]["intervals"][stage] = {
                "n": len(available), "horizon_cut": sum(r["intervals"][stage]["horizon_cut"] for r in available),
                **{scale: aggregate([r["intervals"][stage][scale] for r in available], available) for scale in ("raw", "percentile")}}
    return summary


def local_summaries(rows):
    output = {}
    for cohort, group in cohorts(rows).items():
        output[cohort] = {"episodes": len(group), "valid_layer_pairs": sum(r["valid_layer_pairs"] for r in group), "kinds": {}}
        for kind in ("entry", "stable"):
            phases = sorted({phase for r in group for phase in r[kind]})
            block = {}
            for phase in phases:
                available = [r for r in group if phase in r[kind]]
                matched = [r for r in available if r[kind][phase]["matched_events"]]
                block[phase] = {"episodes_with_events": len(available), "episodes_with_matching": len(matched),
                    "events": sum(r[kind][phase]["events"] for r in available),
                    "matched_events": sum(r[kind][phase]["matched_events"] for r in available),
                    "raw": aggregate([r[kind][phase]["raw"] for r in available], available),
                    **{field: aggregate([r[kind][phase][field] for r in matched], matched) for field in ("matched_raw", "control", "residual")}}
            output[cohort]["kinds"][kind] = block
    return output


def dump_jsonl(path, rows):
    with path.open("w") as f:
        for row in rows: f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")


def run(expected):
    start = time.monotonic()
    guard = M4AccessGuard(ROOT)
    guard.install()
    head = harness.git_output("rev-parse", "HEAD")
    if head != expected or harness.git_output("diff", "HEAD", "--name-only"):
        raise ValueError("commit sources, then pass exact current clean tracked HEAD")
    if not set(SOURCES).issubset(set(harness.git_output("ls-files").splitlines())):
        raise ValueError("all M4 sources must be committed")
    if OUT.exists(): raise ValueError("new output directory required")
    sources = {name: sha((ROOT / name).read_bytes()) for name in SOURCES}
    hashes, frozen, graph, metadata, reference, prior = inputs()
    OUT.mkdir()
    manifest = {"schema": "codex-g-m4-mechanisms-1.0.0", "status": "started", "implementation_commit": head,
                "source_sha256": sources, "input_sha256": hashes, "started_utc": datetime.now(timezone.utc).isoformat(),
                "features": FEATURES, "entry_fields": ENTRY_FIELDS, "stable_fields": STABLE_FIELDS,
                "role": "G-dev mechanism description only, not a new detector or causal effect"}
    write_json(OUT / "run_manifest.json", manifest)
    old_slices = {k: slice(int(prior["offsets"][i]), int(prior["offsets"][i + 1])) for i, k in enumerate(prior["keys"].tolist())}
    records, local_rows, peaks, bank_reports, folds_info = [], [], [], {}, {}
    raw_errors = {"S": 0., "CU": 0., "P": 0.}
    with patch.object(io_g, "load_file", lambda path, **kwargs: load_bytes(guard.check_path(path).read_bytes())), \
         patch.object(io_g, "save_file", no_cache_write):
        load_report = {}
        episodes = io_g.load_g(RUN, labels=LABELS, cache_dir=M3 / "topk_cache", tag_scope="message",
                              variant_overrides="auto", verify_tokens=True, manifest=load_report)
        if dict(Counter(e.variant for e in episodes)) != EXPECTED_ARMS: raise ValueError("episode census changed")
        if set(trm3.trace_key(e) for e in episodes) != set(metadata): raise ValueError("metadata keys changed")
        for ep in episodes:
            if trm3_g.view_anchors([ep], VIEW)[trm3.trace_key(ep)].to_json() != metadata[trm3.trace_key(ep)]["anchor"]:
                raise ValueError("shared anchor changed")
        for fold in range(3):
            params = parameters(frozen, reference, fold)
            target = [e for e in episodes if metadata[trm3.trace_key(e)]["fold"] == fold]
            refs = [e for e in episodes if metadata[trm3.trace_key(e)]["fold"] == (fold + 2) % 3 and e.normal and e.filter_pass is True]
            banks = {kind: NormalEventBank() for kind in ("entry", "stable")}
            ref_records = []
            calibrations = {name: trm3_g.calibration_from_state(frozen["cells"][name]["folds"][str(fold)]["calibrations"][name]) for name in ("S", "CU")}
            reference_maxima = defaultdict(list)
            for ep in refs:
                key = trm3.trace_key(ep)
                row, local = measure(ep, metadata[key], params)
                ref_records.append({"key": key, "episode_index": ep.episode_index, "scores": row["raw"], "tags": row["tags"], "ordinals": row["ordinals"]})
                for kind, bank in banks.items(): bank.add(key, local[kind])
                for name, col in (("S", 0), ("CU", 3)):
                    stream = trm3_g.EpisodeStream(key, row["ends"], row["raw"][:, col], row["tags"], row["ordinals"])
                    z = calibrations[name].standardiser.standardize(stream)
                    if len(z):
                        i = int(np.argmax(z)); reference_maxima[name].append(float(z[i]))
                        peaks.append({"fold": fold, "key": key, "statistic": name, "end": int(row["ends"][i]),
                                      "tag": row["tags"][i], "zmax": float(z[i]), "raw": row["raw"][i].tolist()})
            normal_percentiles = NormalPercentiles(ref_records)
            for name, cal in calibrations.items():
                np.testing.assert_allclose(sorted(reference_maxima[name]), sorted(cal.reference.channels[name].path_maxima), atol=1e-7, rtol=0)
            for bank in banks.values(): bank.finalize()
            bank_reports[str(fold)] = {kind: bank.summary() for kind, bank in banks.items()}
            folds_info[str(fold)] = {"reference_episodes": len(refs), "target_episodes": len(target),
                "rare_coordinates": int((params[0] < .02).sum()), "rare_by_layer": (params[0] < .02).sum(-1).tolist()}
            print(json.dumps({"fold": fold, "phase": "reference_banks", "banks": bank_reports[str(fold)]}), flush=True)
            for ep in target:
                key = trm3.trace_key(ep)
                row, local = measure(ep, metadata[key], params)
                row["percentile"], row["reference_levels"] = normal_percentiles.transform(row["raw"], row["tags"], row["ordinals"], ep.episode_index)
                if not np.isfinite(row["percentile"]).all(): raise ValueError("missing percentile reference")
                previous = prior["raw"][old_slices[key]]
                np.testing.assert_array_equal(row["ends"], prior["ends"][old_slices[key]])
                for name, column, old_column, divisor in (("S", 0, 0, 1), ("CU", 3, 2, 1), ("P", 2, 1, 96)):
                    error = float(np.max(np.abs(row["raw"][:, column] / divisor - previous[:, old_column]))) if len(previous) else 0.
                    raw_errors[name] = max(raw_errors[name], error)
                    if error > 1e-8: raise ValueError(f"M3 {name} raw score failed to reproduce")
                local_row = {k: row[k] for k in META_FIELDS}
                local_row["valid_layer_pairs"] = local["valid_layer_pairs"]
                e, x = row["anchor"]["anchor"], row["anchor"]["x"]
                for kind, bank in banks.items():
                    local_row[kind] = summarize_events(local[kind], bank, e, x, 8 if kind == "entry" else 0)
                records.append(row); local_rows.append(local_row)
            print(json.dumps({"fold": fold, "phase": "target_complete", "seconds": time.monotonic() - start}), flush=True)
    scores = {r["key"]: r for r in records}
    readings, mappings = pair_readings(graph, metadata, scores)
    summary, strata = summarize_pairs(readings)
    full = full_intervals(records)
    local_summary = local_summaries(local_rows)
    records.sort(key=lambda r: r["key"])
    offsets = np.cumsum([0] + [len(r["ends"]) for r in records])
    np.savez_compressed(OUT / "look_features.npz", keys=np.array([r["key"] for r in records]), offsets=offsets,
         ends=np.concatenate([r["ends"] for r in records]), raw=np.concatenate([r["raw"] for r in records]),
         percentiles=np.concatenate([r["percentile"] for r in records]), features=np.array(FEATURES))
    dump_jsonl(OUT / "paired_readings.jsonl", readings)
    dump_jsonl(OUT / "local_episode_readings.jsonl", local_rows)
    dump_jsonl(OUT / "episode_intervals.jsonl", [{**{k: r[k] for k in META_FIELDS}, "intervals": r["intervals"]} for r in records])
    dump_jsonl(OUT / "normal_reference_peaks.jsonl", peaks)
    for filename, payload in (("matched_summary.json", summary), ("matched_strata.json", strata),
                              ("full_summary.json", full), ("local_summary.json", local_summary), ("reference_banks.json", bank_reports)):
        write_json(OUT / filename, payload)
    write_json(OUT / "consistency_checks.json", {"raw_reproduction_max_error": raw_errors,
        "probability_identity_max_error": max(r["identity_error"] for r in records), "episodes": len(records),
        "looks": int(offsets[-1]), "matched_rows": len(readings), "exact_coordinate_checks": mappings,
        "reference_maxima_reproduced": True, "folds": folds_info, "loader": load_report})
    if sources != {name: sha((ROOT / name).read_bytes()) for name in SOURCES}: raise ValueError("source changed during run")
    manifest.update(status="completed", elapsed_seconds=time.monotonic() - start,
         peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2, access_guard=guard.summary(),
         output_sha256={p.name: sha(p.read_bytes()) for p in OUT.iterdir() if p.is_file() and p.name != "run_manifest.json"})
    if guard.blocked_attempts: raise ValueError("unexpected blocked access")
    write_json(OUT / "run_manifest.json", manifest)
    print(json.dumps({k: manifest[k] for k in ("status", "elapsed_seconds", "peak_rss_gib", "access_guard")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze-commit", required=True)
    args = parser.parse_args()
    torch.set_num_threads(4)
    run(args.freeze_commit)
