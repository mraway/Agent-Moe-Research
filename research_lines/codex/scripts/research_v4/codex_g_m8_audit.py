"""Independent exhaustive M8 matching/estimand replay; no Matcher calls."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
import time

import numpy as np

from research_v4 import codex_g_m8 as m8
from research_v4.codex_g_m1 import ROOT, sha, write_json


def brute_select(q, bank, a, version):
    """Enumerate every structurally eligible donor, then every eligible window.

    `bank` is the whole pool (synthetic tests) or its exact structural cell. No
    nearest-neighbour truncation, outcome columns, or production matcher used.
    """
    qep = int(a["episode"][q]); qt = a["tokens"][q]; end = int(a["ends"][q])
    bank = np.asarray(bank, dtype=np.int64)
    same = (a["valid"][bank] & (a["episode"][bank] != qep)
            & (a["structure"][bank] == a["structure"][q]).all(axis=1))
    c = bank[same]; tried = len(c)
    if not len(c): return [], 1, tried
    c = c[np.abs(a["tu"][c]-a["tu"][q]) <= (.10 if version == "L1_tight" else .25)]
    if not len(c): return [], 2, tried
    if version != "L0":
        c = c[a["tokens"][c, 7] == qt[7]]
        if not len(c): return [], 3, tried
    if version == "L8":
        c = c[(a["tokens"][c] == qt).all(axis=1)]
        if not len(c): return [], 4, tried
    best = {}
    for d in c:
        ep = int(a["episode"][d])
        rank = (abs(float(a["tu"][q]-a["tu"][d])), abs(end-int(a["ends"][d])), int(a["ends"][d]))
        if ep not in best or rank < best[ep][0]: best[ep] = (rank, int(d))
    all_episodes = [(rank+(str(a["keys"][ep]),), d) for ep, (rank, d) in best.items()]
    chosen = [d for _, d in sorted(all_episodes)[:3]]
    return chosen, 0, tried


def manual_episode_means(queries, values, a):
    rows = defaultdict(list)
    for q, v in zip(queries, values, strict=True): rows[int(a["episode"][q])].append(v)
    episodes = sorted(rows)
    return episodes, np.array([np.mean(rows[e], axis=0) for e in episodes]).reshape((-1, values.shape[1]))


def close(actual, expected):
    if expected is None: assert actual is None
    else: np.testing.assert_allclose(actual, expected, rtol=1e-11, atol=1e-11)


def verify_effect(reading, qs, effects, a, metadata):
    eps, means = manual_episode_means(qs, effects, a)
    assert reading["episodes"] == len(eps) and reading["looks"] == len(qs)
    if not len(eps):
        assert reading["mean"] is None and not reading["episode_values"]
        return
    close(reading["mean"], means.mean(0)); close(reading["median"], np.median(means, axis=0))
    close(reading["positive_fraction"], (means > 0).mean(0))
    counts = Counter(int(a["episode"][q]) for q in qs)
    for row, ep, value in zip(reading["episode_values"], eps, means, strict=True):
        assert row["key"] == a["keys"][ep] and row["looks"] == counts[ep]
        close(row["value"], value)
    for name in ("family", "family_tier"):
        labels = [str(metadata[str(a["keys"][e])]["family"])+(
            "|"+str(metadata[str(a["keys"][e])]["tier"]) if name == "family_tier" else "") for e in eps]
        sizes = dict(sorted(Counter(labels).items()))
        read = reading[name]
        assert read["cluster_sizes"] == sizes and read["cluster_count"] == len(sizes)
        assert read["replicates"] == 2000 and read["seed"] == 802608
        groups = list(sizes)
        draws = np.random.default_rng(802608).integers(len(groups), size=(2000, len(groups)))
        sums = np.array([sum((means[i] for i, label in enumerate(labels) if label == g), np.zeros(6)) for g in groups])
        ns = np.array(list(sizes.values()))
        bootstrap = sums[draws].sum(1)/ns[draws].sum(1)[:, None]
        close(read["mean_ci95"], np.percentile(bootstrap, [2.5, 97.5], axis=0).T)


def manual_z(a, original, manifest):
    out = np.zeros((len(a["ends"]), 3))
    for ep, key in enumerate(a["keys"]):
        start, stop = (int(x) for x in a["offsets"][ep:ep+2])
        for j, name in enumerate(("S", "CW", "TU")):
            fold = str(a["structure"][start, 0]) if stop > start else None
            if fold is None: continue
            state = manifest["cells"][name]["folds"][fold]["calibrations"][name]["standardiser"]
            for i in range(start, stop):
                tag = ("analysis", "commentary", "final")[int(a["structure"][i, 1])]
                stats = state["stats"].get(tag)
                ordinal = int(original["ordinals"][i])
                if stats is None: stats = state["pooled"]; ordinal = i-start
                bucket = min(ordinal//int(stats["bucket_size"]), int(stats["cap"]))
                out[i, j] = (original["raw"][i, j]-stats["mu"][bucket])/stats["sd"][bucket]
    return out


def run():
    started = time.monotonic()
    guard = m8.M8AccessGuard(ROOT, "audit"); guard.install()
    out = m8.OUT / "audit"
    if out.exists(): raise ValueError("refusing to overwrite M8 audit")
    log_raw = (m8.OUT / "run_manifest.json").read_bytes(); log = json.loads(log_raw)
    assert log["status"] == "completed" and not log["access_guard"]["blocked_attempts"]
    assert m8.source_freeze(log["implementation_commit"])[1] == log["source_sha256"]
    m8.prior_inputs()
    for name, digest in log["output_sha256"].items(): assert sha((m8.OUT / name).read_bytes()) == digest
    for path, digest in log["input_sha256"].items(): assert sha((ROOT / path).read_bytes()) == digest
    a = m8.m7.read_npz(m8.OUT / "look_inventory.npz")
    g = m8.m7.read_npz(m8.OUT / "match_graph.npz")
    effects = m8.m7.read_npz(m8.OUT / "residuals.npz")["effects"]
    metadata = json.loads((m8.OUT / "episode_metadata.json").read_text())
    result = json.loads((m8.OUT / "result.json").read_text())
    manifest = json.loads((m8.m7.OUT / "calibrate/threshold_manifest.json").read_text())
    original = m8.m7.read_npz(m8.m7.OUT / "score/look_streams.npz")
    for field in ("keys", "offsets", "ends", "ordinals"):
        np.testing.assert_array_equal(a[field], original[field])
    np.testing.assert_array_equal(a["values"][:, :3], original["raw"])
    np.testing.assert_array_equal(a["tu"], original["raw"][:, 2])
    np.testing.assert_array_equal(a["values"][:, 3:], manual_z(a, original, manifest))
    assert tuple(g["pools"]) == ("normal_filtered", "normal_all", "over_refusal", "engaged_only", "committed_no_execution", "legitimate_refusal")
    assert tuple(g["versions"]) == ("L0", "L1", "L8", "L1_tight")
    assert tuple(g["phase_names"]) == ("E_at", "X_pre", "X_at")
    pools = {n: [] for n in g["pools"]}; phase_rows = {n: [] for n in g["phase_names"]}
    reconstructed_tu = []
    for ei, key in enumerate(a["keys"]):
        r = metadata[str(key)]
        rows = np.arange(int(a["offsets"][ei]), int(a["offsets"][ei+1]))
        cache_name = str(key).split("|", 1)[1].replace("#ep", "--ep")+".safetensors"
        token_path = guard.check_path(m8.m7.m6.M3 / "topk_cache/g_dev" / cache_name)
        token_ids = m8.load_bytes(token_path.read_bytes())["token_ids"].numpy()
        assert token_ids.shape == (r["token_count"],)
        token_windows = np.array([token_ids[int(a["ends"][i])-7:int(a["ends"][i])+1] for i in rows], dtype=np.int64).reshape((-1, 8))
        np.testing.assert_array_equal(a["tokens"][rows], token_windows)
        boundaries = np.cumsum(r["step_output_lengths"])
        assert int(boundaries[-1]) == r["token_count"]
        tu_state = manifest["cells"]["TU"]["folds"][str(r["fold"])]["statistics"]["TU"]
        tables = {}
        for tag, counts in tu_state["counts"].items():
            n = sum(counts.values()) + .5*(len(counts)+1)
            tables[tag] = ({int(k): -math.log((v+.5)/n) for k, v in counts.items()}, -math.log(.5/n))
        for i in rows:
            end = int(a["ends"][i]); ordinal = int(a["ordinals"][i]); tag = str(original["tags"][i])
            step = sum(end >= b for b in boundaries); first_step = sum(end-7 >= b for b in boundaries)
            assert int(a["episode"][i]) == ei
            assert bool(a["valid"][i]) == (step == first_step)
            np.testing.assert_array_equal(a["structure"][i], [r["fold"], ("analysis", "commentary", "final").index(tag), r["episode_index"], step, ordinal//32, end//64])
            table, unknown = tables[tag]
            reconstructed_tu.append(sum(table.get(int(t), unknown) for t in a["tokens"][i])/8)
            if r["variant"] == "attack" and r["x"] is not None:
                for phase, lo, hi in (("E_at", r["e"], r["e"]+16), ("X_pre", r["x"]-16, r["x"]-1), ("X_at", r["x"], r["x"]+16)):
                    if lo <= end <= hi: phase_rows[phase].append(int(i))
        if r["variant"] in ("clean", "benign_control", "benign_lexical"):
            pools["normal_all"].append(ei)
            if r["filter_pass"] is True: pools["normal_filtered"].append(ei)
        if r["variant"] == "legitimate_refusal": pools["legitimate_refusal"].append(ei)
        if r["variant"] == "attack" and r["attack_bearing"] and r["x"] is None and r["trajectory_class"] in ("over_refusal", "engaged_only", "committed_no_execution"):
            pools[r["trajectory_class"]].append(ei)
    close(a["tu"], reconstructed_tu)
    expected_queries, expected_phases = [], []
    for hi, phase in enumerate(g["phase_names"]):
        for i in phase_rows[phase]:
            if a["valid"][i]: expected_queries.append(i); expected_phases.append(hi)
    np.testing.assert_array_equal(g["queries"], expected_queries); np.testing.assert_array_equal(g["phases"], expected_phases)
    assert {k: len(v) for k, v in pools.items()} == result["donor_pools"] == m8.EXPECTED_POOLS
    count, candidates_tested = 0, 0
    for pi, pool in enumerate(g["pools"]):
        bank = np.flatnonzero(np.isin(a["episode"], pools[pool]) & a["valid"])
        cells = defaultdict(list)
        for i in bank: cells[tuple(a["structure"][i])].append(int(i))
        for vi, version in enumerate(g["versions"]):
            for qi, q in enumerate(g["queries"]):
                chosen, reason, tried = brute_select(int(q), cells.get(tuple(a["structure"][q]), []), a, str(version))
                candidates_tested += tried
                np.testing.assert_array_equal(g["donors"][pi, vi, qi], chosen+[-1]*(3-len(chosen)))
                assert int(g["reasons"][pi, vi, qi]) == reason
                if chosen: close(effects[pi, vi, qi], a["values"][q]-sum((a["values"][d] for d in chosen), np.zeros(6))/len(chosen))
                else: assert np.isnan(effects[pi, vi, qi]).all()
                count += 1
        print(json.dumps({"audit": "all_donors_and_residuals_replayed", "pool": str(pool)}), flush=True)
    for hi, phase in enumerate(g["phase_names"]):
        mask = g["phases"] == hi; qs = g["queries"][mask]
        for pi, pool in enumerate(g["pools"]):
            for vi, version in enumerate(g["versions"]):
                reading = result["matrix"][str(phase)][str(pool)][str(version)]
                assert reading["candidate_episodes"] == 126
                assert reading["original_query_looks"] == len(phase_rows[phase])
                assert reading["cross_step_query_looks"] == sum(not a["valid"][i] for i in phase_rows[phase])
                assert reading["eligible_query_looks"] == len(qs)
                assert reading["eligible_query_episodes"] == len(set(a["episode"][qs]))
                all_qs = np.array(phase_rows[phase], dtype=np.int64)
                assert reading["episodes_with_original_query_looks"] == len(set(a["episode"][all_qs]))
                for name, query_rows in (("all_original_query_mean", all_qs), ("all_eligible_query_mean", qs)):
                    expected = manual_episode_means(query_rows, a["values"][query_rows], a)[1].mean(0) if len(query_rows) else None
                    close(reading[name], expected)
                ds = g["donors"][pi, vi, mask]; es = effects[pi, vi, mask]
                labels = ("matched", "no_common_structure", "tu_outside_caliper", "current_token_mismatch", "entire_window_mismatch")
                assert reading["failure_looks"] == dict(Counter(labels[r] for r in g["reasons"][pi, vi, mask]))
                for name, minimum in (("at_least_one", 1), ("at_least_three", 3)):
                    keep = (ds >= 0).sum(1) >= minimum; sub = reading[name]; selected = qs[keep]
                    n = len(set(a["episode"][selected])); nn = len(set(a["episode"][qs]))
                    assert sub["matched_episodes"] == n and sub["matched_looks"] == int(keep.sum())
                    close(sub["episode_fraction_of_candidates"], n/126)
                    close(sub["episode_fraction_of_query_episodes"], n/nn if nn else None)
                    close(sub["look_fraction_of_eligible"], len(selected)/len(qs) if len(qs) else None)
                    verify_effect(sub["effect"], selected, es[keep], a, metadata)
                    used = ds[keep]; used = used[used >= 0]; reuse = Counter(a["episode"][used].tolist())
                    assert sub["distinct_donor_episodes"] == len(reuse)
                    assert sub["distinct_donor_scenarios"] == len({metadata[str(a["keys"][ep])]["scenario"] for ep in reuse})
                    assert sub["max_donor_reuse"] == max(reuse.values(), default=0)
                    if len(selected):
                        assert sub["donor_episode_query_window_counts"] == {str(a["keys"][ep]): n for ep, n in sorted(reuse.items())}
                        close(sub["query_mean"], manual_episode_means(selected, a["values"][selected], a)[1].mean(0))
                        dmeans = np.array([np.mean(a["values"][row[row >= 0]], axis=0) for row in ds[keep]])
                        close(sub["donor_mean"], manual_episode_means(selected, dmeans, a)[1].mean(0))
                        b = np.array([[np.mean(abs(a["tu"][row[row >= 0]]-a["tu"][q])),
                                       np.mean(abs(a["ends"][row[row >= 0]]-a["ends"][q]))] for q, row in zip(selected, ds[keep], strict=True)])
                        close(sub["balance"]["episode_equal_mean"], manual_episode_means(selected, b, a)[1].mean(0))
                        weights = defaultdict(float); qcounts = Counter(a["episode"][selected].tolist())
                        for q, row in zip(selected, ds[keep], strict=True):
                            dd = row[row >= 0]
                            for d in dd: weights[str(a["keys"][a["episode"][d]])] += 1/(len(qcounts)*qcounts[int(a["episode"][q])]*len(dd))
                        assert set(weights) == set(sub["donor_effective_weight"])
                        for key, w in weights.items(): close(sub["donor_effective_weight"][key], w)
                        close(sub["max_donor_effective_weight"], max(weights.values()))
            for left, right in (("L0", "L1"), ("L1", "L1_tight")):
                li, ri = list(g["versions"]).index(left), list(g["versions"]).index(right)
                keep = (g["donors"][pi, ri, mask] >= 0).any(1)
                reading = result["common_query_comparisons"][str(phase)][str(pool)][left+"_to_"+right]
                assert reading["query_looks"] == int(keep.sum()) and reading["same_queries"] == qs[keep].tolist()
                left_e, right_e = effects[pi, li, mask][keep], effects[pi, ri, mask][keep]
                for name, values in ((left, left_e), (right, right_e), ("right_minus_left", right_e-left_e)):
                    verify_effect(reading[name], qs[keep], values, a, metadata)
    out.mkdir()
    report = {"status": "PASS", "matching_selections_replayed": count, "structural_candidate_checks": candidates_tested,
              "all_residuals_and_cluster_intervals_replayed": True, "current_z_independent_exact_replay": True,
              "run_manifest_sha256": sha(log_raw), "elapsed_seconds": time.monotonic()-started,
              "access_guard": guard.summary(),
              "scope": "All score-blind donor choices, failure reasons, six-column residuals, episode weighting, family/family-tier CIs and common-query comparisons; inventory TU replay and M7 hashes. Not a causal-identification audit."}
    assert not report["access_guard"]["blocked_attempts"]
    write_json(out / "checks.json", report); print(json.dumps(report), flush=True)


if __name__ == "__main__": run()
