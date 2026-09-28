"""Replay stored M2-B matched/stage arithmetic without rereading raw routing."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

import numpy as np

from research_v4.codex_g_m1 import ROOT, BASE, sha, write_json
from research_v4.codex_g_m1_math import BANDS
from research_v4.codex_g_m2a import M2AccessGuard, archive_members
from research_v4.codex_g_m2b import M1, M2, MATCH_SHA, read_json


def run(output):
    guard = M2AccessGuard(ROOT)
    guard.install()
    output = guard.check_path(output)
    if output.parent != BASE.resolve() or (output / "audit_checks.json").exists():
        raise ValueError("new audit result in a valid M2-B directory required")
    manifest = read_json(output / "run_manifest.json")
    assert manifest["status"] == "completed" and manifest["access_guard"]["blocked_attempts"] == 0
    for group in ("source_sha256", "input_sha256"):
        for name, expected in manifest[group].items(): assert sha((ROOT / name).read_bytes()) == expected, name
    for name, expected in manifest["output_sha256"].items(): assert sha((output / name).read_bytes()) == expected, name
    assert sha((M2 / "match_manifest.json").read_bytes()) == MATCH_SHA
    graph = read_json(M2 / "match_manifest.json")
    arrays = archive_members(output / "look_scores.npz", ("keys", "offsets", "ends", "raw", "percentiles", "physical"))
    original = archive_members(M1 / "look_scores.npz", ("keys", "offsets", "ends", "raw", "percentiles"))
    for name in ("keys", "offsets", "ends"): np.testing.assert_array_equal(arrays[name], original[name])
    for name in ("raw", "percentiles"):
        assert np.isfinite(arrays[name]).all()
        np.testing.assert_array_equal(arrays[name][:, :3], original[name])
    assert arrays["raw"].shape[1:] == (8, 4) and arrays["physical"].shape[1:] == (3, 4)
    assert np.isfinite(arrays["physical"]).all()
    index = {key: i for i, key in enumerate(arrays["keys"].tolist())}
    def get(key, scale):
        i = index[key]
        a, b = arrays["offsets"][i:i + 2]
        return arrays["percentiles" if scale == "percentile" else scale][a:b]
    def equal(a, b): np.testing.assert_allclose(a, b, atol=1e-12, rtol=0)
    matched = {(r["query_id"], r["scope"], r["quality"]): r for r in graph["matches"] if r["status"] == "matched"}
    rows = [json.loads(line) for line in (output / "paired_readings.jsonl").read_text().splitlines()]
    assert len(rows) == len(matched) == 305
    matrices = 0
    for row in rows:
        match = matched[(row["query_id"], row["scope"], row["quality"])]
        query = graph["queries"][row["query_id"]]
        assert row["key"] == query["key"] and row["kind"] == query["kind"]
        for scale in ("raw", "percentile", "physical"):
            fields = {}
            for name, indices in query["groups"].items():
                target = get(row["key"], scale)[indices].mean(0)
                normal = np.mean([get(d["key"], scale)[d["groups"][name]].mean(0) for d in match["donors"]], axis=0)
                fields.update({name + "_attack": target, name + "_control": normal, name + "_residual": target - normal})
                for donor, saved in zip(match["donors"], row["donor_means"]):
                    assert donor["key"] == saved["key"]
                    equal(get(donor["key"], scale)[donor["groups"][name]].mean(0), saved[scale][name])
            if "pre" in query["groups"]:
                fields["change_attack"] = fields["post_attack"] - fields["pre_attack"]
                fields["change_control"] = fields["post_control"] - fields["pre_control"]
                fields["did"] = fields["change_attack"] - fields["change_control"]
            if "middle" in query["groups"]:
                fields["middle_minus_E_residual"] = fields["middle_residual"] - fields["E_residual"]
                fields["X_minus_middle_residual"] = fields["X_residual"] - fields["middle_residual"]
            assert set(fields) == set(row["values"][scale])
            for name, value in fields.items():
                equal(value, row["values"][scale][name])
                matrices += 1
            if row["placebo"]:
                placebo = {name: np.mean([d[scale][name] for d in row["donor_means"] if d["variant"] == "clean"], axis=0)
                           - np.mean([d[scale][name] for d in row["donor_means"] if d["variant"] == "benign_control"], axis=0)
                           for name in query["groups"]}
                if "pre" in placebo: placebo["change"] = placebo["post"] - placebo["pre"]
                for name, value in placebo.items(): equal(value, row["placebo"][scale][name])
    summary = read_json(output / "matched_summary.json")
    for cell, block in summary.items():
        subset = [r for r in rows if "/".join((r["scope"], r["quality"], r["kind"])) == cell]
        assert len(subset) == block["n"]
        for scale, fields in block["means"].items():
            for field, value in fields.items(): equal(value, np.mean([r["values"][scale][field] for r in subset], axis=0))
        for scale in ("raw", "percentile"):
            for field, effects in block["effects"][scale].items():
                values = np.array([r["values"][scale][field] for r in subset])
                equal(effects["P_minus_ablations"]["family"]["mean"], (values[:, 2, None] - values[:, 4:8]).mean(0))
                equal(effects["V_minus_W"]["family"]["mean"], (values[:, 3] - values[:, 1]).mean(0))
    episodes = [json.loads(line) for line in (output / "episode_metrics.jsonl").read_text().splitlines()]
    assert len(episodes) == 784
    intervals = 0
    for row in episodes:
        i = index[row["key"]]
        ends = arrays["ends"][arrays["offsets"][i]:arrays["offsets"][i + 1]]
        e, x = row["anchor"]["anchor"], row["anchor"]["x"]
        bounds = {"whole": (0, row["h_end"]), "pre_E": None, "E": None, "E_to_X": None, "X": None, "post_X": None}
        if row["attack_bearing"] and e is not None: bounds.update(pre_E=(max(0, e - 16), e - 1), E=(e, e + 16))
        if row["attack_bearing"] and e is not None and x is not None: bounds.update(E_to_X=(e + 17, x - 1), X=(x, x + 16), post_X=(x + 17, x + 64))
        for name, span in bounds.items():
            block = row["intervals"][name]
            positions = np.flatnonzero((ends >= span[0]) & (ends <= span[1])) if span else np.zeros(0, dtype=int)
            assert len(positions) == block["looks"]
            for scale in ("raw", "percentile", "physical"):
                if len(positions): equal(block[scale], get(row["key"], scale)[positions].mean(0))
                else: assert block[scale] is None
            for b in range(4):
                d = block["dynamics"]
                assert 0 <= d["stable_pairs"][b] <= d["valid_pairs"][b]
                if d["stable_pairs"][b]: equal(d["means"][0][b], sum(d["means"][i][b] for i in range(1, 4)))
                else: assert all(d["means"][i][b] is None for i in range(4))
            intervals += 1
    contract = read_json(output / "data_contract.json")["per_episode"]
    maxima = {name: max(c[name] for c in contract) for name in (
        "P_sum_roundoff_max", "identity_max_error", "V_W_max_error", "basis_simplex_max_error",
        "dynamic_identity_error", "dynamic_direct_js_error")}
    result = {"status": "passed", "audit_source_sha256": sha(Path(__file__).read_bytes()),
              "source_input_output_hashes_verified": True, "original_U_W_P_arrays_identical": True,
              "paired_rows": len(rows), "matched_matrix_fields_recomputed": matrices,
              "episode_intervals_recomputed": intervals, "probability_contract_maxima": maxima,
              "arms": dict(Counter(r["variant"] for r in episodes)), "access_guard": guard.summary()}
    write_json(output / "audit_checks.json", result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    run(parser.parse_args().output)
