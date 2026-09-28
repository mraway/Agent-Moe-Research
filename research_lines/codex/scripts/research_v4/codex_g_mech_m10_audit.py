"""Independent M10 full-bank pair selection, cache replay and estimand audit."""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
import json
import time
import numpy as np

from research_v4 import codex_g_mech_m10 as m10
from research_v4.codex_g_mech_m9_audit import enumerate_experts, verify_summary
from research_v4.codex_g_m8_audit import close, manual_episode_means
from research_v4.codex_g_m1 import ROOT, sha, write_json


def brute_pair(q, bank, a, scenarios, *, distinct=True, caliper=.25):
    """All candidates in the entire bank or its exact structural equivalence cell."""
    bank = np.asarray(bank, dtype=np.int64)
    eligible = bank[a["valid"][bank] & (a["episode"][bank] != a["episode"][q])
                    & (a["structure"][bank] == a["structure"][q]).all(1)]
    tried = len(eligible)
    if not tried: return [], 1, tried
    eligible = [int(d) for d in eligible if abs(float(a["tu"][d]-a["tu"][q])) <= caliper]
    if not eligible: return [], 2, tried
    eligible = [d for d in eligible if a["tokens"][d, 7] == a["tokens"][q, 7]]
    if not eligible: return [], 3, tried
    best = {}
    for d in eligible:
        ep = int(a["episode"][d])
        if scenarios[ep] == scenarios[a["episode"][q]]: continue
        rank = (abs(float(a["tu"][q]-a["tu"][d])), abs(int(a["ends"][q])-int(a["ends"][d])),
                int(a["ends"][d]), str(a["keys"][ep]))
        if ep not in best or rank < best[ep][0]: best[ep] = (rank, d)
    candidates = [d for rank, d in sorted(best.values())]
    if len(candidates) < 2: return [], 4, tried
    potential = [(i, j) for i, j in combinations(range(len(candidates)), 2)
                 if not distinct or scenarios[a["episode"][candidates[i]]] != scenarios[a["episode"][candidates[j]]]]
    if not potential: return [], 5, tried
    valid = [(i, j) for i, j in potential if abs(float(a["tu"][candidates[i]]-a["tu"][candidates[j]])) <= caliper]
    if not valid: return [], 6, tried
    i, j = min(valid)
    return [candidates[i], candidates[j]], 0, tried


def manual_values(current, lookup, queries, pairs):
    main = np.zeros((len(queries), 56)); profile = np.zeros((len(queries), 192))
    for qi, (q, donors) in enumerate(zip(queries, pairs, strict=True)):
        nodes = [current[lookup[int(d)]] for d in [q, *donors]]
        for stat in range(2):
            for band, layers in enumerate((range(24), range(8), range(8, 16), range(16, 24))):
                qv, av, bv = [sum(float(node[layer, stat]) for layer in layers) for node in nodes]
                ad = (abs(qv-av)+abs(qv-bv))/2; nd = abs(av-bv)
                start = (stat*4+band)*7
                main[qi, start:start+7] = [qv, (av+bv)/2, ad, nd, ad-nd, qv-(av+bv)/2,
                                         float(qv-max(av, bv) > 1e-9)]
            for layer in range(24):
                qv, av, bv = [float(node[layer, stat]) for node in nodes]
                ad = (abs(qv-av)+abs(qv-bv))/2; nd = abs(av-bv)
                start = (stat*24+layer)*4
                profile[qi, start:start+4] = [ad, nd, ad-nd, qv-(av+bv)/2]
    return main, profile


def manual_edges(a, qs, ds):
    rows = []
    for q, (d1, d2) in zip(qs, ds, strict=True):
        rows.append([abs(float(a[field][i])-float(a[field][j]))
                     for field in ("tu", "ends") for i, j in ((q, d1), (q, d2), (d1, d2))])
    return np.array(rows).reshape((-1, 6))


def verify_distribution(reading, a, metadata, rows):
    eps = sorted(set(int(a["episode"][d]) for d in rows))
    assert reading["episodes"] == len(eps) and reading["looks"] == len(rows)
    if not eps: return
    for field in ("family", "tier", "domain_group", "injection_channel"):
        assert reading[field+"_episodes"] == dict(Counter(str(metadata[str(a["keys"][e])][field]) for e in eps))
    for j, name in enumerate(("fold", "tag", "episode_index", "step", "ordinal_bin", "end_bin")):
        assert reading[name+"_looks"] == dict(Counter(str(int(a["structure"][d, j])) for d in rows))


def verify_subset(r, qs, ds, values, profiles, a, metadata):
    verify_summary(r["effect"], qs, values, a, metadata)
    verify_summary(r["layers"], qs, profiles, a, metadata)
    verify_distribution(r["query_distribution"], a, metadata, qs)
    if not len(qs): return
    edges = manual_edges(a, qs, ds)
    close(r["balance"]["episode_equal_mean"], manual_episode_means(qs, edges, a)[1].mean(0))
    close(r["balance"]["edge_max"], edges.max(0))
    epcounts = Counter(int(a["episode"][q]) for q in qs)
    reuse, weights, sw = Counter(), defaultdict(float), defaultdict(float)
    for q, pair in zip(qs, ds, strict=True):
        for d in pair:
            key = str(a["keys"][a["episode"][d]]); reuse[key] += 1
            weight = 1/(2*len(epcounts)*epcounts[int(a["episode"][q])])
            weights[key] += weight; sw[metadata[key]["scenario"]] += weight
    assert r["donor_episode_reuse"] == dict(reuse)
    assert r["distinct_donor_episodes"] == len(reuse) and r["distinct_donor_scenarios"] == len(sw)
    for name, source in (("donor_effective_weight", weights), ("donor_scenario_effective_weight", sw)):
        assert set(r[name]) == set(source)
        close([r[name][k] for k in sorted(source)], [source[k] for k in sorted(source)])
        close(sum(source.values()), 1.)
    close(r["max_donor_effective_weight"], max(weights.values()))
    close(r["max_donor_scenario_effective_weight"], max(sw.values()))
    verify_distribution(r["donor_distribution"], a, metadata, ds.ravel())


def run():
    started = time.monotonic(); guard = m10.M10AccessGuard(ROOT, "audit"); guard.install()
    out = m10.OUT/"audit"
    if out.exists(): raise ValueError("refusing to overwrite M10 audit")
    log_body = (m10.OUT/"run_manifest.json").read_bytes(); log = json.loads(log_body)
    assert log["status"] == "completed" and not log["access_guard"]["blocked_attempts"]
    assert m10.source_freeze(log["implementation_commit"])[1] == log["source_sha256"]
    m10.prior_inputs()
    for path, digest in log["input_sha256"].items(): assert sha((ROOT/path).read_bytes()) == digest
    for name, digest in log["output_sha256"].items(): assert sha((m10.OUT/name).read_bytes()) == digest
    assert log["graph_sha256"] == log["output_sha256"]["triplet_graph.npz"]
    reader = m10.m9.m8.m7.read_npz
    a = reader(m10.m9.m8.OUT/"look_inventory.npz"); old_graph = reader(m10.m9.m8.OUT/"match_graph.npz")
    graph = reader(m10.OUT/"triplet_graph.npz"); terms = reader(m10.OUT/"current_terms.npz")
    old_terms = reader(m10.m9.OUT/"window_terms.npz")
    metadata = json.loads((m10.m9.m8.OUT/"episode_metadata.json").read_text())
    assert sum(r["variant"] == "attack" and r["x"] is not None for r in metadata.values()) == 126
    manifest = json.loads((m10.m9.m8.m7.OUT/"calibrate/threshold_manifest.json").read_text())
    result = json.loads((m10.OUT/"result.json").read_text())
    cards = json.loads((m10.OUT/"triplet_cards.json").read_text())
    assert tuple(result["columns"]) == tuple(m10.mm.COLUMNS) == tuple(cards["columns"])
    assert len(result["columns"]) == 56 and len(result["layer_columns"]) == 192
    for field in ("queries", "phases", "phase_names"):
        np.testing.assert_array_equal(graph[field], old_graph[field])
    primary_x = (graph["phases"] == 2) & (graph["reasons"][0] == 0)
    assert (old_graph["donors"][0, 1, primary_x] >= 0).any(1).all()
    assert ((old_graph["phases"] == 2) & (old_graph["donors"][0, 1] >= 0).any(1)).sum() == 45
    configs = (("filtered_distinct", True, True, .25), ("filtered_shared_allowed", True, False, .25),
               ("all_distinct", False, True, .25), ("filtered_distinct_tight", True, True, .10))
    assert graph["cells"].tolist() == [c[0] for c in configs]
    scenarios = [metadata[str(k)]["scenario"] for k in a["keys"]]
    checks = candidates_tested = 0
    for ci, (name, filtered, distinct, caliper) in enumerate(configs):
        bank_eps = [ei for ei, k in enumerate(a["keys"]) if metadata[str(k)]["variant"] in
                    ("clean", "benign_control", "benign_lexical") and (not filtered or metadata[str(k)]["filter_pass"] is True)]
        assert len(bank_eps) == (293 if filtered else 408)
        bank = np.flatnonzero(np.isin(a["episode"], bank_eps) & a["valid"])
        cells = defaultdict(list)
        for d in bank: cells[tuple(a["structure"][d])].append(int(d))
        for qi, q in enumerate(graph["queries"]):
            ds, reason, tried = brute_pair(int(q), cells[tuple(a["structure"][q])], a, scenarios, distinct=distinct, caliper=caliper)
            assert int(graph["reasons"][ci, qi]) == reason
            assert graph["pairs"][ci, qi].tolist() == (ds if ds else [-1, -1])
            checks += 1; candidates_tested += tried
        print(json.dumps({"audit_stage": "full_candidate_matching_replayed", "cell": name}), flush=True)
    expected_used = sorted(set(graph["queries"][(graph["reasons"] == 0).any(0)].tolist()) |
                           set(graph["pairs"][graph["pairs"] >= 0].tolist()))
    np.testing.assert_array_equal(terms["used"], expected_used)
    used, current = terms["used"], terms["current"]; maximum = np.zeros(2)
    for ei, key in enumerate(a["keys"]):
        loc = np.flatnonzero(a["episode"][used] == ei)
        if not len(loc): continue
        name = str(key).split("|", 1)[1].replace("#ep", "--ep")
        base = m10.m9.m8.m7.m6.M3
        top = m10.m9.m8.load_bytes(guard.check_path(base/"topk_cache/g_dev"/(name+".safetensors")).read_bytes())
        logits = m10.m9.m8.load_bytes(guard.check_path(base/"logit_cache/g_dev"/(name+".logits.safetensors")).read_bytes())["router_logits"].double().numpy()
        row = metadata[str(key)]; state = manifest["cells"]["S"]["folds"][str(row["fold"])]["statistics"]["S"]
        rebuilt = enumerate_experts(top["top_k_ids"].numpy(), logits, np.array(state["q"]), state["config"]["rare_threshold"])
        for i in loc:
            d = int(used[i]); end = int(a["ends"][d]); close(current[i], rebuilt[end])
            np.testing.assert_array_equal(top["token_ids"].numpy()[end-7:end+1], a["tokens"][d])
            assert a["structure"][d, 0] == row["fold"]
            average = rebuilt[end-7:end+1].mean(0).sum(0)
            close(average, a["values"][d, :2]); maximum = np.maximum(maximum, abs(average-a["values"][d, :2]))
    overlap, here, there = np.intersect1d(used, old_terms["used"], return_indices=True)
    close(current[here], old_terms["terms"][there, -1])
    assert result["used_looks"] == len(used) and result["replay"]["M9_overlap_looks"] == len(overlap)
    close(result["replay"]["M8_window_max_error"], maximum)
    lookup = {int(d): i for i, d in enumerate(used)}
    common = (graph["reasons"] == 0).all(0); summaries = 0; expected_card_keys = []
    card_by_key = {(r["phase"], r["cell"], r["query_row"]): r for r in cards["cards"]}
    assert len(card_by_key) == len(cards["cards"])
    for hi, phase in enumerate(graph["phase_names"]):
        ph = graph["phases"] == hi
        for ci, (name, _, _, _) in enumerate(configs):
            keep = ph & (graph["reasons"][ci] == 0); qs, ds = graph["queries"][keep], graph["pairs"][ci, keep]
            v, p = manual_values(current, lookup, qs, ds)
            row = result["matrix"][str(phase)][name]
            assert row["candidate_episodes"] == 126 and row["candidate_looks"] == int(ph.sum())
            labels = ("matched", "no_common_structure", "tu_outside_caliper", "current_token_mismatch",
                      "fewer_than_two_other_scenario_episodes", "no_distinct_scenario_pair", "mutual_tu_failed")
            assert row["failure_looks"] == dict(Counter(labels[int(r)] for r in graph["reasons"][ci, ph]))
            masks = {"matched": np.ones(len(qs), bool), "four_cell_common": common[keep]}
            if ci == 0: masks["endpoint_diameter_le16"] = manual_edges(a, qs, ds)[:, 3:].max(1) <= 16
            for sub, mask in masks.items():
                verify_subset(row[sub], qs[mask], ds[mask], v[mask], p[mask], a, metadata); summaries += 2
            for q, pair, value in zip(qs, ds, v, strict=True):
                ck = (str(phase), name, int(q)); expected_card_keys.append(ck); card = card_by_key[ck]
                assert card["donor_rows"] == pair.tolist(); close(card["values"], value)
                for r, d in zip(card["nodes"], [q, *pair], strict=True):
                    assert r == {"row": int(d), "key": str(a["keys"][a["episode"][d]]),
                                 "scenario": metadata[str(a["keys"][a["episode"][d]])]["scenario"],
                                 "end": int(a["ends"][d]), "tu": float(a["tu"][d]),
                                 "token_id": int(a["tokens"][d, -1]), "window_ids": a["tokens"][d].tolist()}
    assert set(card_by_key) == set(expected_card_keys)
    for path, digest in log["input_sha256"].items(): assert sha((ROOT/path).read_bytes()) == digest
    assert m10.source_freeze(log["implementation_commit"])[1] == log["source_sha256"]
    out.mkdir()
    report = {"status": "PASS", "pair_selections_replayed": checks, "structural_candidates_tested": candidates_tested,
              "used_looks_rebuilt": len(used), "triplet_cards_replayed": len(cards["cards"]),
              "summaries_and_cluster_intervals_replayed": summaries, "run_manifest_sha256": sha(log_body),
              "elapsed_seconds": time.monotonic()-started, "access_guard": guard.summary()}
    assert not report["access_guard"]["blocked_attempts"]
    write_json(out/"checks.json", report); print(json.dumps(report), flush=True)


if __name__ == "__main__": run()
