"""Post-result arithmetic/coverage audit of M4; no detector or new selection rule."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import time
from unittest.mock import patch

import numpy as np
from safetensors.torch import load as load_bytes

from research_v2 import io_g, trm3
from research_v4.codex_g_m1 import LABELS, ROOT, RUN, sha, write_json
from research_v4.codex_g_m2a import META_FIELDS
from research_v4.codex_g_m4 import M3, M4AccessGuard, OUT, cohorts, no_cache_write, read_json
from research_v4.codex_g_m4_math import TAGS, event_stage, segment_ids


def stable_inventory(ep, meta, rare):
    """Independently reconstruct eligible stable supports, without reading logits."""
    ids = ep.top_k_ids.numpy().transpose(1, 0, 2)
    support = np.sort(ids, -1)
    runs = segment_ids(ep.channel_tags, [int(s["global_token_offset"]) for s in ep.step_spans])
    n = min(len(ids), meta["h_end"] + 1)
    rows = []
    for t in range(1, n):
        if runs[t] != runs[t - 1] or ep.channel_tags[t] not in TAGS:
            continue
        for layer in np.flatnonzero((support[t] == support[t - 1]).all(-1)):
            experts = tuple(int(x) for x in support[t, layer])
            key = (int(layer), experts, ep.channel_tags[t])
            rows.append((t, key, bool(rare[layer, list(experts)].any())))
    return rows


def qualify(bank):
    return {key for key, counts in bank.items() if sum(counts.values()) >= 5 and len(counts) >= 3}


def coverage(rows, qualified, e, x):
    groups = defaultdict(Counter)
    for t, key, is_rare in rows:
        stage = event_stage(t, e, x)
        phases = ["whole", stage]
        if stage in ("E", "E_to_X"):
            if x is not None and t < x:
                phases.append(stage + "_strict_preX")
            elif x is None:
                phases.append(stage + "_noX")
        for phase in phases:
            for field, condition in (("events", True), ("matched", key in qualified),
                                     ("rare_events", is_rare), ("rare_matched", is_rare and key in qualified)):
                groups[phase][field] += int(condition)
    return dict(groups)


def local_strata(local_rows, fields):
    out = {}
    for kind in ("entry", "stable"):
        phases = sorted({p for r in local_rows if r["attack_bearing"] for p in r[kind]})
        for phase in phases:
            rows = [r for r in local_rows if r["attack_bearing"] and r[kind].get(phase, {}).get("matched_events")]
            groups = {}
            for field in ("family", "domain_group", "injection_channel", "tier", "trajectory_class", "fold"):
                groups[field] = {}
                for value in sorted({str(r[field]) for r in rows}):
                    chosen = [r for r in rows if str(r[field]) == value]
                    avg = np.mean([r[kind][phase]["residual"] for r in chosen], 0)
                    groups[field][value] = {"n": len(chosen), "residual": dict(zip(fields[kind], avg.tolist()))}
            out[f"{kind}/{phase}"] = {"n": len(rows), "strata": groups}
    return out


def run():
    started = time.monotonic()
    guard = M4AccessGuard(ROOT)
    guard.install()
    destination = OUT / "audit"
    if destination.exists():
        raise ValueError("preserve previous audit; output must not exist")
    original = read_json(OUT / "run_manifest.json")
    assert original["status"] == "completed" and original["access_guard"]["blocked_attempts"] == 0
    for name, digest in original["source_sha256"].items():
        assert sha((ROOT / name).read_bytes()) == digest, name
    for name, digest in original["input_sha256"].items():
        assert sha((ROOT / name).read_bytes()) == digest, name
    for name, digest in original["output_sha256"].items():
        assert sha((OUT / name).read_bytes()) == digest, name
    checks = {}
    with np.load(OUT / "look_features.npz", allow_pickle=False) as z:
        raw, keys, offsets, ends = z["raw"], z["keys"].tolist(), z["offsets"], z["ends"]
        assert raw.shape == (166133, 20) and len(keys) == 784
        assert np.isfinite(raw).all() and np.isfinite(z["percentiles"]).all()
        for name, a, b in (("RP_partition", raw[:, 9], raw[:, 10:14].sum(1)),
                           ("JS_channel_partition", raw[:, 3], raw[:, 4] + raw[:, 5]),
                           ("JS_global_partition", raw[:, 6], raw[:, 7] + raw[:, 8]),
                           ("S_layer_shares", raw[:, 16:19].sum(1), raw[:, 19])):
            checks[name] = float(np.max(np.abs(a - b)))
            np.testing.assert_allclose(a, b, atol=1e-10, rtol=0)
        assert np.all(raw[:, 2] + 1e-10 >= raw[:, 0])
        assert np.all(raw[:, 1] >= -1e-10) and np.all(raw[:, 1] <= 96)
        for i in range(len(keys)):
            ee = ends[offsets[i]:offsets[i + 1]]
            assert len(ee) <= 352 and (np.diff(ee) > 0).all()
    local_rows = [json.loads(line) for line in (OUT / "local_episode_readings.jsonl").read_text().splitlines()]
    by_key = {r["key"]: r for r in local_rows}
    assert set(by_key) == set(keys) and len(local_rows) == 784
    for row in local_rows:
        for kind in ("entry", "stable"):
            for cell in row[kind].values():
                if cell["matched_events"]:
                    np.testing.assert_allclose(np.array(cell["matched_raw"]) - cell["control"], cell["residual"], atol=1e-12, rtol=0)

    peaks = [json.loads(line) for line in (OUT / "normal_reference_peaks.jsonl").read_text().splitlines()]
    peak_composition = {}
    for statistic in ("S", "CU"):
        a = np.array([r["raw"] for r in peaks if r["statistic"] == statistic])
        assert len(a) == 293
        avg = a.mean(0)
        peak_composition[statistic] = {"episodes": len(a), "raw_mean": dict(zip(original["features"], avg.tolist())),
            "JS_rare_ratio_of_means": float(avg[4] / avg[3]),
            "JS_rare_mean_of_ratios": float(np.mean(a[:, 4] / a[:, 3]))}

    # Coverage-only replay, without probabilities and without changing the frozen banks' rule.
    frozen = read_json(M3 / "calibrate/threshold_manifest.json")
    prior_banks = read_json(OUT / "reference_banks.json")
    coverage_rows = []
    with patch.object(io_g, "load_file", lambda path, **kwargs: load_bytes(guard.check_path(path).read_bytes())), \
         patch.object(io_g, "save_file", no_cache_write):
        episodes = io_g.load_g(RUN, labels=LABELS, cache_dir=M3 / "topk_cache", tag_scope="message", variant_overrides="auto", verify_tokens=True)
        assert {trm3.trace_key(e) for e in episodes} == set(keys)
        for fold in range(3):
            q = np.array(frozen["cells"]["S"]["folds"][str(fold)]["statistics"]["S"]["q"])
            rare = q < .02
            bank = defaultdict(Counter)
            for ep in episodes:
                key = trm3.trace_key(ep)
                if by_key[key]["fold"] == (fold + 2) % 3 and ep.normal and ep.filter_pass is True:
                    for _, support, _ in stable_inventory(ep, by_key[key], rare):
                        bank[support][key] += 1
            qualified = qualify(bank)
            previous = prior_banks[str(fold)]["stable"]
            assert len(bank) == previous["bins"] and len(qualified) == previous["qualified_bins"]
            assert sum(sum(c.values()) for c in bank.values()) == previous["events"]
            for ep in episodes:
                key = trm3.trace_key(ep)
                meta = by_key[key]
                if meta["fold"] != fold:
                    continue
                cov = coverage(stable_inventory(ep, meta, rare), qualified, meta["anchor"]["anchor"], meta["anchor"]["x"])
                assert set(cov) == set(meta["stable"])
                for phase, counts in cov.items():
                    assert counts["events"] == meta["stable"][phase]["events"]
                    assert counts["matched"] == meta["stable"][phase]["matched_events"]
                coverage_rows.append({**{k: meta[k] for k in META_FIELDS}, "coverage": cov})
    coverage_summary = {}
    for cohort, rows in cohorts(coverage_rows).items():
        coverage_summary[cohort] = {}
        for phase in sorted({phase for r in rows for phase in r["coverage"]}):
            present = [r for r in rows if phase in r["coverage"]]
            counts = sum((r["coverage"][phase] for r in present), Counter())
            coverage_summary[cohort][phase] = {**{k: counts[k] for k in ("events", "matched", "rare_events", "rare_matched")}, "episodes": len(present),
                "episodes_rare": sum(r["coverage"][phase]["rare_events"] > 0 for r in present),
                "episodes_rare_matched": sum(r["coverage"][phase]["rare_matched"] > 0 for r in present)}

    assert guard.blocked_attempts == 0
    destination.mkdir()
    write_json(destination / "readouts.json", {
        "normal_peak_composition": peak_composition, "stable_support_coverage": coverage_summary,
        "local_strata": local_strata(local_rows, {k: original[k + "_fields"] for k in ("entry", "stable")}),
    })
    write_json(destination / "checks.json", {
        "role": "post-result descriptive arithmetic and coverage audit; not a detector or new hypothesis test",
        "checks": checks, "all_local_residuals_reproduced": True, "all_stable_counts_and_matches_reproduced": True,
        "all_M4_sources_inputs_outputs_hash_verified": True, "episodes": 784, "looks": 166133,
        "original_manifest_sha256": sha((OUT / "run_manifest.json").read_bytes()),
        "audit_source_sha256": sha(Path(__file__).read_bytes()), "elapsed_seconds": time.monotonic() - started,
        "access_guard": guard.summary(), "readouts_sha256": sha((destination / "readouts.json").read_bytes()),
    })
    print(json.dumps(read_json(destination / "checks.json")), flush=True)


if __name__ == "__main__":
    run()
