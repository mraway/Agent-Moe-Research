"""POSTHOC M14 coverage accounting and existing fixed-cohort readouts.

Added after the main results and independent audit. No rematching, new threshold,
new significance test or modification of the frozen main measurements.
"""
from __future__ import annotations

from collections import Counter
import json
import numpy as np
from research_v4 import codex_g_mech_m14 as m
from research_v4.codex_g_m1 import ROOT, sha, write_json


def run():
    guard = m.M14AccessGuard(ROOT); guard.install()
    log = json.loads((m.OUT/"run_manifest.json").read_text())
    assert log["status"] == "completed"
    for name, expected in {**log["source_sha256"], **log["input_sha256"]}.items(): assert sha((ROOT/name).read_bytes()) == expected
    for name, expected in log["output_sha256"].items(): assert sha((m.OUT/name).read_bytes()) == expected
    assert json.loads((m.OUT/"audit/checks.json").read_text())["status"] == "PASS"
    source = "scripts/research_v4/codex_g_mech_m14_readouts.py"
    assert not m.m8.m7.harness.git_output("diff", "HEAD", "--name-only")
    assert source in m.m8.m7.harness.git_output("ls-files").splitlines()
    read = m.m8.m7.read_npz; a, metadata, labels = m.read_inputs()
    graph = read(m.OUT/"temporal_graph.npz"); metrics = read(m.OUT/"temporal_metrics.npz")
    ranks = read(m.OUT/"local_percentiles.npz")["ranks"]
    events = json.loads((m.OUT/"recovery_events.json").read_text()); result = json.loads((m.OUT/"result.json").read_text())
    serial = {}
    for offset in (0, 4):
        grid = graph[f"offset{offset}_grid"]; donors = graph[f"offset{offset}_donors"][0]; serial[str(offset)] = {}
        for group in m.GROUPS:
            good = np.array([m.mm.normal_mask(a["keys"], metadata)[r[0]] if group == "normal_filtered" else
                             m.mm.group_of(metadata[str(a["keys"][r[0]])]) == group for r in grid]) & (grid[:, 3] >= 2)
            nodes = np.flatnonzero(good & (donors >= 0).any(1)); allnodes = np.flatnonzero(good)
            item = dict(all_channel_segments=dict(Counter(str(x) for x in grid[allnodes, 5])),
                        matched_channel_segments=dict(Counter(str(x) for x in grid[nodes, 5])),
                        matched_block_counts=dict(Counter(str(x) for x in grid[nodes, 3])),
                        matched_n_at_least_3_segments=int((grid[nodes, 3] >= 3).sum()),
                        matched_n_at_least_3_episodes=len(set(grid[nodes[grid[nodes, 3] >= 3], 0].tolist())),
                        same_family_matched_segments=int(((graph[f"offset{offset}_donors"][1, allnodes] >= 0).any(1)).sum()),
                        temporal_identification={})
            for ti, threshold in enumerate((.9, .95)):
                informative = []
                for node in nodes:
                    row = grid[node]; h = ranks[row[2]+8*np.arange(row[3]), 1] > threshold; k = int(h.sum())
                    if 2 <= k < len(h): informative.append(int(node))
                    if len(h) == 2 or k <= 1 or k == len(h): assert abs(metrics[f"offset{offset}"][node, ti, 1, 2]) < 1e-12
                item["temporal_identification"][str(threshold)] = dict(nontrivial_CW_order_segments=len(informative),
                    nontrivial_CW_order_episodes=len(set(grid[informative, 0].tolist())))
            serial[str(offset)][group] = item
    segments, _ = m.mm.runs(a); missing = Counter(); missing_by_group = {}
    for ev in events:
        if ev["pre"] >= 0: continue
        spans = [(int(a["ends"][lo])-7, int(a["ends"][hi-1])) for lo, hi in segments if a["episode"][lo] == ev["episode"]]
        starts = [start for start, end in spans if start <= ev["start"] <= end]
        cause = "first_8_tokens_of_run" if starts and 0 <= ev["start"]-starts[0] <= 7 else "no_reconstructible_run_cover"
        missing[cause] += 1; missing_by_group.setdefault(ev["group"], Counter())[cause] += 1
    after = []
    data = a["values"][:, :3]
    for j, ev in enumerate(events):
        if ev["relation"] != "after_X": continue
        ds = graph["recovery_donors"][0, j]; dp = graph["recovery_posts"][0, j]
        good = (dp >= 0).all(1) & (ds >= 0)
        assert all(p >= 0 for p in ev["posts"]) and good.any()
        baseline = ranks[ev["pre"]]; profile = ranks[ev["posts"]]
        after.append(dict(key=ev["key"], family=metadata[ev["key"]]["family"], E=metadata[ev["key"]]["e"], X=metadata[ev["key"]]["x"],
                          R=ev["start"], R_end=ev["end"], channel=ev["channel"], re_execution=ev["re_execution"],
                          baseline_rank=baseline.tolist(), post_ranks=profile.tolist(),
                          baseline_CW=float(data[ev["pre"], 1]), post_CW=data[ev["posts"], 1].tolist(),
                          common_donor_keys=[str(a["keys"][a["episode"][d]]) for d in ds[good]],
                          common_excess_CW_rank=metrics["recovery_common"][0, j, :, 14].tolist()))
    normal_families = Counter(str(row["family"]) for row in metadata.values() if row["variant"] in ("clean", "benign_control", "benign_lexical"))
    out = dict(evidence_role="POSTHOC support and arithmetic description, not new hypothesis testing", implementation_commit=m.m8.m7.harness.git_output("rev-parse", "HEAD"),
               source_sha256={source: sha((ROOT/source).read_bytes())}, main_manifest_sha256=sha((m.OUT/"run_manifest.json").read_bytes()),
               serial_support=serial, missing_pre_look_causes=dict(missing), missing_pre_look_by_group={k: dict(v) for k, v in missing_by_group.items()},
               normal_family_metadata=dict(normal_families), after_X_fixed_cohort=after,
               after_X_common_curve=result["recovery"]["after_X"]["levels"]["structural"]["same_cohort_same_donors_four_blocks"],
               access_guard=guard.summary())
    target = m.OUT/"readouts"; target.mkdir(exist_ok=True)
    if (target/"support_and_returns.json").exists(): raise ValueError("refusing to overwrite M14 readouts")
    write_json(target/"support_and_returns.json", out)
    print(json.dumps({"status": "completed", "missing_pre_look_causes": out["missing_pre_look_causes"], "normal_family_metadata": out["normal_family_metadata"],
                      "post_X_cases": len(after), "sha256": sha((target/"support_and_returns.json").read_bytes())}), flush=True)


if __name__ == "__main__": run()
