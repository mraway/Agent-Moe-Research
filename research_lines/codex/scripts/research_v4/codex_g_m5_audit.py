"""Post-result invariant/coverage audit; does not fit or alter the M5 study."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import time

import numpy as np

from research_v2 import io_g
from research_v4.codex_g_m1 import ROOT, sha, write_json
from research_v4.codex_g_m4 import cohorts, read_json
from research_v4.codex_g_m5 import OUT, M5AccessGuard, row_fingerprint
from research_v4.codex_g_m5_math import COUNTS, FIELDS, LEVELS, STREAKS, SWITCHES


def check_cell(cell):
    """Independent arithmetic for each saved episode/status/stage."""
    counts = [cell["native"][k]["events"] for k in LEVELS]
    assert cell["eligible_events"] >= counts[0] >= counts[1] >= counts[2] >= counts[3] >= 0
    error = 0.
    for mode, levels in (("native", LEVELS), ("aligned_H", ("B", "C", "H")), ("aligned_HP", ("H", "HP"))):
        for level in levels:
            block = cell[mode][level]
            n = block["events"]
            if not n:
                assert all(block[f] is None for f in ("raw", "control", "residual"))
                continue
            a, c, r = (np.array(block[f]) for f in ("raw", "control", "residual"))
            assert all(np.isfinite(v).all() and v.shape == (7,) for v in (a, c, r))
            difference = float(np.max(np.abs(a - c - r)))
            error = max(error, difference)
            assert difference < 1e-10
            if level in ("C", "H", "HP"):
                np.testing.assert_allclose(r[[4] if level == "C" else [4, 5, 6]], 0, atol=1e-12)
        if mode != "native":
            fine = "H" if mode == "aligned_H" else "HP"
            reference = cell[mode][fine]
            assert cell["native"][fine] == reference
            for level in levels:
                assert cell[mode][level]["events"] == reference["events"]
                assert cell[mode][level]["raw"] == reference["raw"]
    return error


def audit():
    started = time.monotonic()
    guard = M5AccessGuard(ROOT, "audit")
    guard.install()
    dest = OUT / "audit"
    if dest.exists():
        raise ValueError("preserve prior audit output")
    logs = {}
    for stage in ("normal", "score"):
        manifest = read_json(OUT / stage / "run_manifest.json")
        assert manifest["status"] == "completed" and manifest["access_guard"]["blocked_attempts"] == 0
        for name, digest in manifest["source_sha256"].items():
            assert sha((ROOT / name).read_bytes()) == digest, name
        for name, digest in manifest["input_sha256"].items():
            assert sha((ROOT / name).read_bytes()) == digest, name
        for name, digest in manifest["output_sha256"].items():
            assert sha((OUT / stage / name).read_bytes()) == digest, name
        logs[stage] = sha((OUT / stage / "run_manifest.json").read_bytes())
    bank_checks = 0
    with np.load(OUT / "normal/banks.npz", allow_pickle=False) as z:
        for fold in range(3):
            for level in LEVELS:
                codes, means, counts = (z[f"f{fold}__{level}__{field}"] for field in ("codes", "means", "counts"))
                assert np.all(counts >= [5, 3])
                assert means.shape == (len(codes), 7) and np.isfinite(means).all()
                if level == "C":
                    np.testing.assert_allclose(means[:, 4], codes % 9, atol=1e-12)
                elif level in ("H", "HP"):
                    h = codes % 256 if level == "H" else (codes // (64 * 16)) % 256
                    np.testing.assert_allclose(means[:, 4:], np.stack((COUNTS[h], STREAKS[h], SWITCHES[h]), -1), atol=1e-12)
                bank_checks += len(codes)
    rows = [json.loads(line) for line in (OUT / "score/episode_readings.jsonl").read_text().splitlines()]
    assert len(rows) == len({r["key"] for r in rows}) == 784
    normal = [r for r in rows if r["variant"] in io_g.NORMAL_VARIANTS]
    frozen = read_json(OUT / "normal/normal_freeze.json")
    assert row_fingerprint(normal) == frozen["normal_fingerprint"]
    errors = [check_cell(c) for r in rows for c in r["groups"].values()]
    summary = read_json(OUT / "score/summary.json")
    coverage = {}
    effect_checks = 0
    for cohort, members in cohorts(rows).items():
        coverage[cohort] = {}
        for group, expected in summary[cohort].items():
            eligible = [r for r in members if r["groups"][group]["eligible_events"]]
            assert expected["eligible_episodes"] == len(eligible)
            assert expected["eligible_events"] == sum(r["groups"][group]["eligible_events"] for r in eligible)
            coverage[cohort][group] = {}
            for mode, levels in (("native", LEVELS), ("aligned_H", ("B", "C", "H")), ("aligned_HP", ("H", "HP"))):
                for level in levels:
                    valid = [r for r in eligible if r["groups"][group][mode][level]["events"]]
                    cell = expected[mode][level]
                    assert cell["matched_episodes"] == len(valid)
                    assert cell["matched_events"] == sum(r["groups"][group][mode][level]["events"] for r in valid)
                    for field in ("raw", "control", "residual"):
                        observed = cell[field]
                        assert observed["n"] == len(valid)
                        if valid:
                            np.testing.assert_allclose(observed["mean"], np.mean([r["groups"][group][mode][level][field] for r in valid], 0), atol=1e-12)
                            effect_checks += 1
                        else:
                            assert observed["mean"] is None
                        if "bootstrap" in observed:
                            for by in ("family", "family_tier"):
                                census = Counter(r["family"] if by == "family" else r["family"] + "|" + r["tier"] for r in valid)
                                assert observed["bootstrap"][by]["cluster_sizes"] == dict(census)
                    if mode == "native" and eligible:
                        fractions = [r["groups"][group][mode][level]["events"] / r["groups"][group]["eligible_events"] for r in eligible]
                        counts = [r["groups"][group][mode][level]["events"] for r in eligible]
                        coverage[cohort][group][level] = {"episode_equal_coverage_quantiles_0_25_50_75_100": np.quantile(fractions, [0, .25, .5, .75, 1]).tolist(),
                            "matched_events_quantiles_0_25_50_75_100": np.quantile(counts, [0, .25, .5, .75, 1]).tolist(),
                            "episodes_with_at_most_5_matched_observations": sum(v <= 5 for v in counts)}
    assert guard.blocked_attempts == 0
    dest.mkdir()
    write_json(dest / "coverage_distribution.json", coverage)
    result = {"role": "post-result arithmetic and coverage audit; no new hypothesis test or fit", "manifest_sha256": logs,
        "audit_source_sha256": sha(Path(__file__).read_bytes()), "episodes": 784, "episode_group_checks": len(errors),
        "max_raw_minus_control_residual_error": max(errors), "qualified_bank_bins_checked": bank_checks,
        "summary_mean_checks": effect_checks, "normal_exact_replay": True, "paired_query_sets_exact": True,
        "bootstrap_family_censuses_verified": True, "all_original_source_input_output_hashes_verified": True,
        "elapsed_seconds": time.monotonic() - started, "access_guard": guard.summary(),
        "coverage_sha256": sha((dest / "coverage_distribution.json").read_bytes())}
    write_json(dest / "checks.json", result)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    audit()
