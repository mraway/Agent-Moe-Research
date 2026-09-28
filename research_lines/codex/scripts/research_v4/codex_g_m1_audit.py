"""Post-run integrity checks and labelled descriptive slices; no refitting/scoring.

Reads only this line's M1 artifacts, the recorded source files and OPEN G-dev
config/annotation hashes. Never opens routing data or any sealed-pool content.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
from io import BytesIO
import json
from pathlib import Path

import numpy as np

from research_v4.codex_access_guard import CodexGAccessGuard
from research_v4.codex_g_m1_math import BANDS, REPRESENTATIONS, STAGES, cluster_mean, finite_mean

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "artifacts/agent_v2/codex_g"


def slim_comparison(values, rows):
    result = cluster_mean(values, [r["family"] or r["scenario"] for r in rows])
    return {key: value for key, value in result.items() if key != "cluster_sizes"}


def compact(rows):
    def block(stage):
        blocks = [r["whole"] if stage == "whole" else r["stages"][stage] for r in rows]
        return {"effective": sum(b["percentile"]["U"]["all"] is not None for b in blocks),
                "percentile": {rep: finite_mean([b["percentile"][rep]["all"] for b in blocks])
                               for rep in REPRESENTATIONS}}
    return {"n": len(rows), "whole": block("whole"), "stages": {s: block(s) for s in STAGES}}


def run(directory):
    guard = CodexGAccessGuard(ROOT)
    guard.install()
    directory = guard.check_path(directory)
    if directory.parent != BASE.resolve():
        raise ValueError("audit only accepts this line's immediate artifact subdirectory")
    target = directory / "audit_checks.json"
    if target.exists():
        raise FileExistsError("refusing to overwrite an existing audit")
    manifest = json.loads((directory / "run_manifest.json").read_text())
    assert manifest["status"] == "completed"
    assert manifest["access_guard"]["blocked_attempts"] == 0
    for field in ("code_and_plan_sha256", "inputs"):
        for name, expected in manifest[field].items():
            actual = hashlib.sha256(guard.check_path(ROOT / name).read_bytes()).hexdigest()
            assert actual == expected, ("hash mismatch", name)
    for name, expected in manifest["output_sha256"].items():
        assert hashlib.sha256((directory / name).read_bytes()).hexdigest() == expected, name
    rows = [json.loads(line) for line in (directory / "episode_metrics.jsonl").read_text().splitlines()]
    summary = json.loads((directory / "summary.json").read_text())
    # Materialise each archive member once: NpzFile otherwise decompresses the
    # full member again on every episode/interval subscript.
    with np.load(BytesIO((directory / "look_scores.npz").read_bytes()), allow_pickle=False) as archive:
        arrays = {name: archive[name] for name in archive.files}
    assert len(rows) == 784 and len({r["key"] for r in rows}) == 784
    assert arrays["keys"].tolist() == [r["key"] for r in rows]
    assert arrays["raw"].shape == arrays["percentiles"].shape
    assert arrays["raw"].shape[1:] == (3, 4)
    assert np.isfinite(arrays["raw"]).all() and np.isfinite(arrays["percentiles"]).all()
    assert arrays["raw"].min() >= 0 and arrays["raw"].max() <= np.log(2) + 1e-10
    assert arrays["percentiles"].min() >= 0 and arrays["percentiles"].max() <= 1
    offsets = arrays["offsets"]
    assert offsets[0] == 0 and offsets[-1] == len(arrays["raw"])
    checked_intervals = 0
    for i, row in enumerate(rows):
        start, stop = offsets[i:i+2]
        assert 0 <= stop - start <= 352
        ends = arrays["ends"][start:stop]
        tags = arrays["tags"][start:stop]
        ordinals = arrays["ordinals"][start:stop]
        assert np.all(np.diff(ends) > 0)
        assert row["h_end"] == int(ends[-1])
        assert ends[-1] < row["token_count"]
        for tag in set(tags):
            assert np.all(np.diff(ordinals[tags == tag]) == 1)
        for stage in ("whole", *STAGES):
            block = row["whole"] if stage == "whole" else row["stages"][stage]
            if "bounds" not in block:
                mask = np.zeros(len(ends), dtype=bool)
            else:
                lo, hi = block["bounds"]
                mask = (ends >= lo) & (ends <= min(hi, row["h_end"]))
            assert int(mask.sum()) == block["looks"]
            for field, array_name in (("raw", "raw"), ("percentile", "percentiles"), ("exact_percentile", "percentiles")):
                selected = mask.copy()
                if field == "exact_percentile":
                    selected &= arrays["reference_levels"][start:stop] == 0
                values = arrays[array_name][start:stop][selected]
                for r, rep in enumerate(REPRESENTATIONS):
                    for b, band in enumerate(BANDS):
                        expected = block[field][rep][band]
                        if len(values):
                            assert abs(expected - float(values[:, r, b].mean())) < 1e-12
                        else:
                            assert expected is None
            dynamics = block["dynamics"]
            if "stable_layer_pairs" in dynamics:
                assert 0 <= dynamics["stable_layer_pairs"] <= dynamics["valid_layer_pairs"]
                assert sum(dynamics["stable_pairs_by_layer"]) == dynamics["stable_layer_pairs"]
            checked_intervals += 1
    assert sum(r["normal"] for r in rows) == 408
    assert sum(r["normal"] and r["filter_pass"] is True for r in rows) == 293
    attacks = [r for r in rows if r["attack_bearing"]]
    silent = [r for r in attacks if r["silent"]]
    assert len(attacks) == 264 and len(silent) == 40
    assert all(not (r["episode_index"] == 0 and r["injection_channel"] == "multi_turn_user") for r in silent)
    for stage in STAGES:
        paired = [r for r in attacks if r["stages"][stage]["percentile"]["U"]["all"] is not None]
        for rep in ("W", "P"):
            delta = [r["stages"][stage]["percentile"][rep]["all"] - r["stages"][stage]["percentile"]["U"]["all"] for r in paired]
            recorded = summary["stages"][stage]["paired_differences"][f"{rep}-U/all"]["family"]
            assert len(delta) == recorded["n"]
            assert abs(float(np.mean(delta)) - recorded["mean"]) < 1e-12
    group = defaultdict(list)
    for row in attacks:
        group[row["trajectory_class"]].append(row)
    common = [r for r in attacks if all(r["stages"][s]["percentile"]["U"]["all"] is not None for s in ("E", "E_to_X", "X"))]
    paired = [r for r in attacks if all(r["stages"][s]["percentile"]["U"]["all"] is not None for s in ("E", "X"))]
    same_e = [r for r in attacks if all(r["stages"][s]["percentile"]["U"]["all"] is not None for s in ("pre_E", "E"))
              and len(r["stages"]["E"]["channels"]) == 1
              and r["stages"]["E"]["channels"] == r["stages"]["pre_E"]["channels"]]
    normal_folds = {str(f): compact([r for r in rows if r["fold"] == f and r["normal"] and r["filter_pass"] is True]) for f in range(3)}
    changes = {}
    for before, after in (("E", "E_to_X"), ("E_to_X", "X")):
        changes[f"{after}_minus_{before}"] = {
            rep: slim_comparison([r["stages"][after]["percentile"][rep]["all"] - r["stages"][before]["percentile"][rep]["all"] for r in common], common)
            for rep in REPRESENTATIONS}
    e_transitions = Counter(str(r["stages"]["pre_E"].get("channels")) + " -> " + str(r["stages"]["E"].get("channels")) for r in same_e)
    x_transitions = Counter(str(r["stages"]["E"]["channels"]) + " -> " + str(r["stages"]["X"]["channels"]) for r in paired)
    # Case choices are post-hoc illustrations, not further evidence or new thresholds.
    illustrations = {}
    for rep in ("W", "P"):
        ranked = sorted(paired, key=lambda r: r["stages"]["X"]["percentile"][rep]["all"] - r["stages"]["X"]["percentile"]["U"]["all"])
        illustrations[rep] = [{"selection": direction, "key": r["key"], "family": r["family"],
                               "class": r["trajectory_class"], "E": r["anchor"]["anchor"], "X": r["anchor"]["x"],
                               "X_percentile": {s: r["stages"]["X"]["percentile"][s]["all"] for s in REPRESENTATIONS}}
                              for direction, r in (("lowest_increment", ranked[0]), ("highest_increment", ranked[-1]))]
    out = {
        "audit_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "status": "passed", "scope": "post-run audit; no refit, threshold, routing read or change to M1 primary outputs",
        "source_and_open_input_hashes_verified": True, "artifact_hashes_verified": True,
        "episode_intervals_recomputed": checked_intervals, "total_looks": len(arrays["raw"]),
        "reference_levels": dict(Counter(str(int(v)) for v in arrays["reference_levels"])),
        "attack_only_trajectory_classes": {name: compact(group) for name, group in sorted(group.items())},
        "classification_note": "Original summary.strata.trajectory_class groups the literal label across arms. Its silent=438 is NOT a silent-attack denominator; the preregistered main silent_postinjection=40 is correct. Use this attack-only table for attack outcome classes; legitimate-refusal arm remains a separate control.",
        "common_E_intermediate_X": compact(common), "common_stage_changes_posthoc": changes,
        "E_pre_E_same_channel_class_counts": dict(Counter(r["trajectory_class"] for r in same_e)),
        "E_pre_E_same_channel_transitions": dict(e_transitions),
        "E_X_paired_channel_transitions": dict(x_transitions),
        "normal_filtered_by_fold": normal_folds,
        "posthoc_illustrations": illustrations, "access_guard": guard.summary(),
    }
    target.write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    run(parser.parse_args().directory)
