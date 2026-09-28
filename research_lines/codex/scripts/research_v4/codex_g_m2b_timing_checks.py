"""Post-hoc timing and normal-arm sensitivity, without changing M2-B matches."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

import numpy as np

from research_v4.codex_g_m1 import ROOT, BASE, sha, write_json
from research_v4.codex_g_m2a import M2AccessGuard, matrix_summary
from research_v4.codex_g_m2b import M2, MATCH_SHA, read_json


def run(output):
    guard = M2AccessGuard(ROOT)
    guard.install()
    output = guard.check_path(output)
    if output.parent != BASE.resolve() or (output / "posthoc_timing_checks.json").exists():
        raise ValueError("new sensitivity result required")
    manifest = read_json(output / "run_manifest.json")
    assert manifest["status"] == "completed" and manifest["access_guard"]["blocked_attempts"] == 0
    assert sha((output / "paired_readings.jsonl").read_bytes()) == manifest["output_sha256"]["paired_readings.jsonl"]
    assert sha((M2 / "match_manifest.json").read_bytes()) == MATCH_SHA
    graph = read_json(M2 / "match_manifest.json")
    assert sha((M2 / "metadata_index.json").read_bytes()) == graph["metadata_sha256"]
    metadata = read_json(M2 / "metadata_index.json")
    rows = [json.loads(line) for line in (output / "paired_readings.jsonl").read_text().splitlines()]
    e_rows = [r for r in rows if (r["scope"], r["quality"], r["kind"]) == ("S", "filtered", "E_did")]
    cohorts = {"X_missing": [], "entire_E_post_before_X": [], "E_post_includes_X_or_later": [], "same_single_channel": []}
    timing = []
    for row in e_rows:
        m, q = metadata[row["key"]], graph["queries"][row["query_id"]]
        post_ends = [m["ends"][i] for i in q["groups"]["post"]]
        e, x = m["anchor"]["anchor"], m["anchor"]["x"]
        group = "X_missing" if x is None else "entire_E_post_before_X" if max(post_ends) < x else "E_post_includes_X_or_later"
        cohorts[group].append(row)
        tags = {m["coordinates"][i][0] for indices in q["groups"].values() for i in indices}
        if len(tags) == 1: cohorts["same_single_channel"].append(row)
        timing.append({"key": row["key"], "E": e, "X": x, "post_last": max(post_ends),
                       "X_minus_E": x - e if x is not None else None, "group": group})
    timing_summary = {}
    for name, cohort in cohorts.items():
        timing_summary[name] = {"n": len(cohort), "classes": dict(Counter(r["trajectory_class"] for r in cohort)),
                                "m_did": matrix_summary(np.array([r["values"]["physical"]["did"][0] for r in cohort]), cohort)}
    arms = {}
    for kind in ("E_level", "X_level", "E_did", "X_did"):
        cohort = [r for r in rows if (r["scope"], r["quality"], r["kind"]) == ("S", "filtered", kind) and r["placebo"]]
        effects = {}
        for arm in ("clean", "benign_control"):
            values = []
            for row in cohort:
                normal = [d["physical"] for d in row["donor_means"] if d["variant"] == arm]
                post = np.mean([d["post"] for d in normal], axis=0)
                if kind.endswith("did"):
                    control = post - np.mean([d["pre"] for d in normal], axis=0)
                    value = np.asarray(row["values"]["physical"]["change_attack"]) - control
                else:
                    value = np.asarray(row["values"]["physical"]["post_attack"]) - post
                values.append(value[0])
            effects[arm] = matrix_summary(np.asarray(values), cohort)
        arms[kind] = {"n": len(cohort), "m_effects": effects}
    distributions = {}
    for kind in ("E_did", "X_level", "X_did"):
        cohort = [r for r in rows if (r["scope"], r["quality"], r["kind"]) == ("S", "filtered", kind)]
        field = "did" if kind.endswith("did") else "post_residual"
        values = np.array([r["values"]["physical"][field][0][0] for r in cohort])
        distributions[kind] = {"n": len(cohort), "positive": int((values > 0).sum()), "nonpositive": int((values <= 0).sum()),
                                "quantiles": np.quantile(values, [0, .25, .5, .75, 1]).tolist()}
    result = {"status": "completed", "source_sha256": sha(Path(__file__).read_bytes()),
              "warning": "POST-HOC diagnostics on unchanged stored effects, not new primary cohorts or alarm rates. No rematching or retraining.",
              "timing_cohorts": timing_summary, "timing_per_episode": timing,
              "separate_normal_arms": arms, "episode_effect_distribution": distributions,
              "access_guard": guard.summary()}
    write_json(output / "posthoc_timing_checks.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "timing_per_episode"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    run(parser.parse_args().output)
