"""Replay M2-A arithmetic directly from M1 arrays; post-hoc donor-arm inspection."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
from io import BytesIO
import json
from pathlib import Path

import numpy as np

from research_v4.codex_g_m2a import BASE, M1, ROOT, M2AccessGuard
from research_v4.codex_g_m2a_math import cluster_grid


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def slim_grid(values, rows):
    result = cluster_grid(np.asarray(values), [r["family"] for r in rows])
    return {key: value for key, value in result.items() if key != "cluster_sizes"}


def run(output):
    guard = M2AccessGuard(ROOT)
    guard.install()
    output = guard.check_path(output)
    if output.parent != BASE.resolve() or (output / "audit_checks.json").exists():
        raise ValueError("audit needs a valid M2-A output with no existing audit result")
    read = lambda name: json.loads((output / name).read_text())
    inventory, scoring = read("inventory_manifest.json"), read("score_manifest.json")
    assert inventory["status"] == scoring["status"] == "completed"
    assert inventory["access_guard"]["blocked_attempts"] == scoring["access_guard"]["blocked_attempts"] == 0
    for name, expected in scoring["source_sha256"].items():
        assert digest(ROOT / name) == expected, name
    for name, expected in scoring["output_sha256"].items():
        assert digest(output / name) == expected, name
    assert digest(output / "match_manifest.json") == scoring["match_manifest_sha256"] == inventory["match_manifest_sha256"]
    assert digest(output / "metadata_index.json") == inventory["metadata_sha256"]
    assert digest(output / "coverage.json") == inventory["coverage_sha256"]
    graph, metadata, summary = read("match_manifest.json"), read("metadata_index.json"), read("summary.json")
    assert digest(M1 / "look_scores.npz") == graph["m1_score_sha256"]
    with np.load(BytesIO((M1 / "look_scores.npz").read_bytes()), allow_pickle=False) as archive:
        arrays = {name: archive[name] for name in archive.files}
    index = {str(key): i for i, key in enumerate(arrays["keys"])}
    def get(key, scale, indices):
        i = index[key]
        name = "raw" if scale == "raw" else "percentiles"
        start, stop = arrays["offsets"][i:i + 2]
        return arrays[name][start:stop][indices].mean(0)
    def equal(actual, expected):
        np.testing.assert_allclose(actual, expected, atol=1e-12, rtol=0)
    matches = {(m["query_id"], m["scope"], m["quality"]): m for m in graph["matches"] if m["status"] == "matched"}
    rows = [json.loads(line) for line in (output / "paired_readings.jsonl").read_text().splitlines()]
    assert len(rows) == len(matches) == 305
    checked, verified_fields = 0, 0
    single_channel_e = []
    for row in rows:
        match = matches[(row["query_id"], row["scope"], row["quality"])]
        query = graph["queries"][row["query_id"]]
        target = metadata[row["key"]]
        assert query["key"] == row["key"] and query["kind"] == row["kind"]
        assert target["attack_bearing"] and target["variant"] == "attack"
        if row["scope"] == "S" and row["quality"] == "filtered" and row["kind"] == "E_did":
            tags = {target["coordinates"][i][0] for indices in query["groups"].values() for i in indices}
            if len(tags) == 1:
                single_channel_e.append(row)
        for name, indices in query["groups"].items():
            lo, hi = query["bounds"][name]
            assert all(lo <= target["ends"][i] <= min(hi, target["h_end"]) for i in indices)
        for donor, saved in zip(match["donors"], row["donor_means"]):
            assert donor["key"] == saved["key"]
            dm = metadata[donor["key"]]
            assert dm["variant"] in {"clean", "benign_control", "benign_lexical"}
            assert dm["fold"] == target["fold"] and dm["episode_index"] == target["episode_index"]
            if row["quality"] == "filtered": assert dm["filter_pass"] is True
            if row["scope"] == "S": assert dm["scenario"] == target["scenario"]
            if row["scope"] == "F":
                assert dm["fixture"] == target["fixture"] and dm["routine_template_id"] == target["routine_template_id"]
            for name, positions in donor["groups"].items():
                assert len(positions) == len(query["groups"][name])
                assert [dm["coordinates"][i] for i in positions] == [target["coordinates"][i] for i in query["groups"][name]]
                checked += len(positions)
                for scale in ("raw", "percentile"):
                    equal(saved[scale][name], get(donor["key"], scale, positions))
        if row["scope"] == "F":
            assert len({metadata[d["key"]]["scenario"] for d in match["donors"]}) >= 3
        for scale in ("raw", "percentile"):
            replay = {}
            for name, indices in query["groups"].items():
                attack = get(row["key"], scale, indices)
                control = np.mean([get(d["key"], scale, d["groups"][name]) for d in match["donors"]], axis=0)
                replay.update({name + "_attack": attack, name + "_control": control, name + "_residual": attack - control})
            if "pre" in query["groups"]:
                replay["change_attack"] = replay["post_attack"] - replay["pre_attack"]
                replay["change_control"] = replay["post_control"] - replay["pre_control"]
                replay["did"] = replay["change_attack"] - replay["change_control"]
            if "middle" in query["groups"]:
                replay["middle_minus_E_residual"] = replay["middle_residual"] - replay["E_residual"]
                replay["X_minus_middle_residual"] = replay["X_residual"] - replay["middle_residual"]
            assert set(replay) == set(row["values"][scale])
            for name, expected in replay.items():
                equal(row["values"][scale][name], expected)
                verified_fields += 1
            if row["placebo"]:
                values = {}
                for name in query["groups"]:
                    clean = np.mean([d[scale][name] for d in row["donor_means"] if d["variant"] == "clean"], axis=0)
                    benign = np.mean([d[scale][name] for d in row["donor_means"] if d["variant"] == "benign_control"], axis=0)
                    values[name] = clean - benign
                if "pre" in values: values["change"] = values["post"] - values["pre"]
                for name, expected in values.items(): equal(row["placebo"][scale][name], expected)
    for cell, block in summary.items():
        scope, quality, kind = cell.split("/")
        subset = [r for r in rows if (r["scope"], r["quality"], r["kind"]) == (scope, quality, kind)]
        assert len(subset) == block["n"]
        for scale, fields in block.get("means", {}).items():
            for name, value in fields.items(): equal(value, np.mean([r["values"][scale][name] for r in subset], axis=0))
    # Predeclared placebo cohort, further decomposed by normal arm AFTER results.
    arm_decomposition = {}
    for kind in ("E_level", "X_level", "E_did", "X_did"):
        cohort = [r for r in rows if (r["scope"], r["quality"], r["kind"]) == ("S", "filtered", kind) and r["placebo"]]
        effects = {}
        for arm in ("clean", "benign_control"):
            values = []
            for row in cohort:
                normal = [d["percentile"] for d in row["donor_means"] if d["variant"] == arm]
                post = np.mean([d["post"] for d in normal], axis=0)
                if kind.endswith("did"):
                    control = post - np.mean([d["pre"] for d in normal], axis=0)
                    value = np.asarray(row["values"]["percentile"]["change_attack"]) - control
                else:
                    value = np.asarray(row["values"]["percentile"]["post_attack"]) - post
                values.append(value)
            effects["attack_minus_" + arm] = slim_grid(values, cohort)
        arm_decomposition[kind] = {"n": len(cohort), "effects": effects}
    out = {"status": "passed", "audit_source_sha256": digest(Path(__file__)),
           "input_and_output_hashes_verified": True, "paired_rows": len(rows),
           "exact_coordinate_pairs_replayed": checked, "paired_matrix_fields_recomputed": verified_fields,
           "pre_windows_contain_no_look_at_or_after_anchor": True,
           "normal_normal_placebo_recomputed": True,
           "posthoc_arm_decomposition": arm_decomposition,
           "matched_E_did_single_channel_subset": {"n": len(single_channel_e),
               "classes": dict(Counter(r["trajectory_class"] for r in single_channel_e)),
               "did": slim_grid([r["values"]["percentile"]["did"] for r in single_channel_e], single_channel_e)},
           "warning": "Arm decomposition and single-channel slice are post-hoc diagnostics, not new primary comparisons; all CIs condition on donors.",
           "access_guard": guard.summary()}
    (output / "audit_checks.json").write_text(json.dumps(out, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({k: v for k, v in out.items() if k not in ("posthoc_arm_decomposition", "matched_E_did_single_channel_subset")}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    run(parser.parse_args().output)
