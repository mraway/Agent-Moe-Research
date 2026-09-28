"""Independent full-bank matching, scalar cache replay and summary audit for M12."""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
import json
import time
import numpy as np

from research_v4 import codex_g_mech_m12 as m12
from research_v4 import codex_g_mech_m11_audit as a11
from research_v4 import codex_g_mech_m10_audit as a10
from research_v4.codex_g_mech_m9_audit import enumerate_experts
from research_v4.codex_g_m8_audit import close, manual_episode_means
from research_v4.codex_g_m1 import ROOT, sha, write_json


def brute_history(q, bank, a, scenarios, length):
    """No production matcher or suffix helper; enumerate the entire structural bank."""
    assert length in (1, 2, 4, 8) and a["valid"][q]
    eligible = [int(d) for d in bank if a["valid"][d] and a["episode"][d] != a["episode"][q]
                and tuple(a["structure"][d]) == tuple(a["structure"][q])]
    counts = [0, 0, 0]
    if not eligible: return [], 1, counts
    eligible = [d for d in eligible if abs(float(a["tu"][d])-float(a["tu"][q])) <= .25]
    if not eligible: return [], 2, counts
    eligible = [d for d in eligible if int(a["tokens"][d, 7]) == int(a["tokens"][q, 7])]
    if not eligible: return [], 3, counts
    eligible = [d for d in eligible if all(int(a["tokens"][d, j]) == int(a["tokens"][q, j]) for j in range(8-length, 8))]
    if not eligible: return [], 7, counts
    eligible = [d for d in eligible if scenarios[int(a["episode"][d])] != scenarios[int(a["episode"][q])]]
    counts = [len(eligible), len({int(a["episode"][d]) for d in eligible}),
              len({scenarios[int(a["episode"][d])] for d in eligible})]
    best = {}
    for d in eligible:
        ep = int(a["episode"][d])
        rank = (abs(float(a["tu"][d])-float(a["tu"][q])), abs(int(a["ends"][d])-int(a["ends"][q])),
                int(a["ends"][d]), str(a["keys"][ep]))
        if ep not in best or rank < best[ep][0]: best[ep] = (rank, d)
    candidates = [v[1] for v in sorted(best.values())]
    if len(candidates) < 2: return [], 4, counts
    potential = [(i, j) for i, j in combinations(range(len(candidates)), 2)
                 if scenarios[int(a["episode"][candidates[i]])] != scenarios[int(a["episode"][candidates[j]])]]
    if not potential: return [], 5, counts
    valid = [(i, j) for i, j in potential if abs(float(a["tu"][candidates[i]])-float(a["tu"][candidates[j]])) <= .25]
    if not valid: return [], 6, counts
    i, j = min(valid)
    return [candidates[i], candidates[j]], 0, counts


def replay_summaries(a, metadata, graph, records, terms, normal_same, three_same, result):
    values, profiles = a11.scalar_aggregate(terms)
    lookup = {(int(row[1]), int(row[2])): i for i, row in enumerate(records)}
    good = graph["reasons"] == 0; common = good.all(0); checks = 0
    def check_subset(reading, ci, mask):
        qi = np.flatnonzero(mask); ri = np.array([lookup[ci, int(q)] for q in qi], dtype=int)
        a11.verify_subset(reading, graph["queries"][qi], graph["pairs"][ci, qi], values[ri], profiles[ri], a, metadata)
    for hi, phase in enumerate(graph["phase_names"]):
        ph = graph["phases"] == hi
        for ci, length in enumerate((1, 2, 4, 8)):
            r = result["matrix"][str(phase)][f"L{length}"]; match = ph & good[ci]; co = match & good[0]
            qi = np.flatnonzero(match); ri = np.array([lookup[ci, int(q)] for q in qi], dtype=int)
            qs, ds = graph["queries"][qi], graph["pairs"][ci, qi]
            fixed = ph & graph["fixed_retained"][ci]
            assert r["candidate_looks"] == int(ph.sum())
            assert r["candidate_episodes"] == 126
            assert r["episodes_with_query_looks"] == len(set(a["episode"][graph["queries"][ph]].tolist()))
            labels = ("matched", "no_common_structure", "tu_outside_caliper", "current_token_mismatch",
                      "fewer_than_two_other_scenario_episodes", "no_distinct_scenario_pair", "mutual_tu_failed", "history_suffix_mismatch")
            assert r["failure_looks"] == dict(Counter(labels[int(x)] for x in graph["reasons"][ci, ph]))
            assert r["candidate_columns"] == ["suffix_eligible_looks", "suffix_eligible_episodes", "suffix_eligible_scenarios"]
            if ph.any(): close(r["candidate_counts_episode_equal"], manual_episode_means(graph["queries"][ph], graph["candidate_counts"][ci, ph], a)[1].mean(0))
            else: assert r["candidate_counts_episode_equal"] is None
            pos = match.copy(); pos[qi] = a10.manual_edges(a, qs, ds)[:, 3:].max(1) <= 16
            for field, c, mask in (("matched", ci, match), ("endpoint_diameter_le16", ci, pos),
                   ("all_length_common", ci, ph & common), ("L1_common_baseline", 0, co), ("L1_common_history", ci, co),
                   ("fixed_graph_retained", 0, fixed), ("fixed_graph_rejected", 0, ph & good[0] & ~fixed)):
                check_subset(r[field], c, mask); checks += 2
            paired_qi = np.flatnonzero(co)
            left = np.array([lookup[0, int(q)] for q in paired_qi], dtype=int)
            right = np.array([lookup[ci, int(q)] for q in paired_qi], dtype=int)
            for field, source in (("effect", values), ("layers", profiles)):
                a11.verify_effect(r["paired_change"][field], graph["queries"][paired_qi], source[right]-source[left], a, metadata); checks += 1
            prev = good[max(0, ci-1)]
            for field, mask in (("new_vs_previous_queries", match & ~prev),
                                ("lost_vs_previous_queries", ph & prev & ~good[ci]),
                                ("new_vs_L1_queries", match & ~good[0])):
                assert r[field] == graph["queries"][mask].tolist()
            assert r["changed_donor_pairs_on_L1_common"] == sum(graph["pairs"][ci, q].tolist() != graph["pairs"][0, q].tolist() for q in paired_qi)
            for field, mask, normal_only in (("normal_same_support_layers", normal_same[ri], True),
                                             ("three_same_support_layers", three_same[ri], False)):
                a11.verify_conditional(r[field], qs, terms[ri], mask, a, metadata, normal_only=normal_only); checks += 1
        print(json.dumps({"audit_stage": "history_summaries_replayed", "phase": str(phase)}), flush=True)
    return checks


def run():
    start = time.monotonic(); guard = m12.M12AccessGuard(ROOT, "audit"); guard.install()
    out = m12.OUT/"audit"
    if out.exists(): raise ValueError("refusing to overwrite M12 audit")
    body = (m12.OUT/"run_manifest.json").read_bytes(); log = json.loads(body)
    assert log["status"] == "completed" and not log["access_guard"]["blocked_attempts"]
    assert m12.source_freeze(log["implementation_commit"])[1] == log["source_sha256"]
    m12.prior_inputs()
    for path, digest in log["input_sha256"].items(): assert sha((ROOT/path).read_bytes()) == digest
    for name, digest in log["output_sha256"].items(): assert sha((m12.OUT/name).read_bytes()) == digest
    assert log["graph_sha256"] == log["output_sha256"]["triplet_graph.npz"]
    read = m12.m8.m7.read_npz
    a = read(m12.m8.OUT/"look_inventory.npz"); old = read(m12.m10.OUT/"triplet_graph.npz")
    graph = read(m12.OUT/"triplet_graph.npz"); v = read(m12.OUT/"routing_vectors.npz"); pair = read(m12.OUT/"pair_metrics.npz")
    metadata = json.loads((m12.m8.OUT/"episode_metadata.json").read_text())
    manifest = json.loads((m12.m8.m7.OUT/"calibrate/threshold_manifest.json").read_text())
    result = json.loads((m12.OUT/"result.json").read_text())
    assert result["columns"] == list(m12.m11.mm.COLUMNS) and result["layer_columns"] == list(m12.m11.mm.LAYER_COLUMNS)
    for field in ("queries", "phases", "phase_names"): np.testing.assert_array_equal(graph[field], old[field])
    np.testing.assert_array_equal(graph["pairs"][0], old["pairs"][0]); np.testing.assert_array_equal(graph["reasons"][0], old["reasons"][0])
    assert graph["lengths"].tolist() == [1, 2, 4, 8] and graph["cells"].tolist() == ["L1", "L2", "L4", "L8"]
    bank_eps = [ei for ei, key in enumerate(a["keys"]) if metadata[str(key)]["variant"] in
                ("clean", "benign_control", "benign_lexical") and metadata[str(key)]["filter_pass"] is True]
    assert len(bank_eps) == 293
    bank = np.flatnonzero(np.isin(a["episode"], bank_eps) & a["valid"]); cells = defaultdict(list)
    for d in bank: cells[tuple(a["structure"][d])].append(int(d))
    scenarios = [metadata[str(k)]["scenario"] for k in a["keys"]]
    selections = 0
    for ci, length in enumerate((1, 2, 4, 8)):
        for qi, q in enumerate(graph["queries"]):
            ds, reason, counts = brute_history(int(q), cells[tuple(a["structure"][q])], a, scenarios, length)
            assert graph["pairs"][ci, qi].tolist() == (ds if ds else [-1, -1])
            assert int(graph["reasons"][ci, qi]) == reason and graph["candidate_counts"][ci, qi].tolist() == counts
            fixed = old["pairs"][0, qi]
            retained = all(d >= 0 for d in fixed) and all(int(a["tokens"][d, j]) == int(a["tokens"][q, j])
                         for d in fixed for j in range(8-length, 8))
            assert bool(graph["fixed_retained"][ci, qi]) == retained
            if ds and length == 8: close(a["tu"][[q, *ds]], np.full(3, a["tu"][q]))
            selections += 1
        print(json.dumps({"audit_stage": "bank_matching_replayed", "length": length}), flush=True)
    assert (np.diff(graph["candidate_counts"], axis=0) <= 0).all()
    assert (np.diff(graph["fixed_retained"].astype(int), axis=0) <= 0).all()
    records = np.array([(hi, ci, qi, int(q), *map(int, graph["pairs"][ci, qi]))
                       for hi in range(3) for ci in range(4) for qi, q in enumerate(graph["queries"])
                       if graph["phases"][qi] == hi and graph["reasons"][ci, qi] == 0], dtype=np.int64).reshape((-1, 6))
    np.testing.assert_array_equal(pair["records"], records)
    edges = np.array(sorted({tuple(sorted((int(row[i]), int(row[j])))) for row in records
                            for i, j in ((3, 4), (3, 5), (4, 5))}), dtype=np.int64).reshape((-1, 2))
    np.testing.assert_array_equal(pair["edges"], edges)
    elook = {tuple(e): i for i, e in enumerate(edges)}
    indices = np.array([[elook[tuple(sorted((row[i], row[j])))] for i, j in ((3, 4), (3, 5), (4, 5))] for row in records], dtype=np.int64).reshape((-1, 3))
    np.testing.assert_array_equal(pair["edge_indices"], indices)
    used = v["used"]; np.testing.assert_array_equal(used, sorted(set(records[:, 3:6].ravel().tolist())))
    rebuilt = np.zeros_like(v["vectors"]); window_error = np.zeros(2)
    for ei, key in enumerate(a["keys"]):
        loc = np.flatnonzero(a["episode"][used] == ei)
        if not len(loc): continue
        assert str(key).startswith("g_dev|")
        stem = str(key).split("|", 1)[1].replace("#ep", "--ep"); base = m12.m8.m7.m6.M3
        top = m12.m8.load_bytes(guard.check_path(base/"topk_cache/g_dev"/(stem+".safetensors")).read_bytes())
        logits = m12.m8.load_bytes(guard.check_path(base/"logit_cache/g_dev"/(stem+".logits.safetensors")).read_bytes())["router_logits"].double().numpy()
        ids = top["top_k_ids"].numpy(); tokens = top["token_ids"].numpy(); ends = a["ends"][used[loc]]
        row = metadata[str(key)]; state = manifest["cells"]["S"]["folds"][str(row["fold"])]["statistics"]["S"]
        for i, end in zip(loc, ends, strict=True): np.testing.assert_array_equal(tokens[end-7:end+1], a["tokens"][used[i]])
        np.testing.assert_array_equal(v["folds"][loc], np.full(len(loc), row["fold"]))
        np.testing.assert_array_equal(v["actual_ids"][loc], ids[:, ends].transpose(1, 0, 2))
        rebuilt[loc] = a11.scalar_vectors(ids[:, ends].transpose(1, 0, 2), logits[:, ends].transpose(1, 0, 2))
        all_terms = enumerate_experts(ids, logits, np.array(state["q"]), state["config"]["rare_threshold"])
        close(v["current"][loc], all_terms[ends])
        win = all_terms[ends[:, None]+np.arange(-7, 1)].mean(1).sum(1)
        close(win, a["values"][used[loc], :2]); window_error = np.maximum(window_error, abs(win-a["values"][used[loc], :2]).max(0))
    close(v["vectors"], rebuilt); close(v["vectors"].sum(-1), np.ones(v["vectors"].shape[:-1]))
    prior_v = read(m12.m11.OUT/"routing_vectors.npz")
    overlap, here, there = np.intersect1d(used, prior_v["used"], return_indices=True)
    np.testing.assert_array_equal(v["vectors"][here], prior_v["vectors"][there])
    assert result["replay"]["M11_overlap_looks"] == len(overlap)
    old_terms = read(m12.m10.m9.OUT/"window_terms.npz")
    overlap9, here9, there9 = np.intersect1d(used, old_terms["used"], return_indices=True)
    close(v["current"][here9], old_terms["terms"][there9, -1]); assert result["replay"]["M9_overlap_looks"] == len(overlap9)
    close(result["replay"]["M8_window_max_error"], window_error)
    rare = np.array([np.array(manifest["cells"]["S"]["folds"][str(k)]["statistics"]["S"]["q"]) < .02 for k in range(3)])
    np.testing.assert_array_equal(v["rare"], rare)
    nlook = {int(d): i for i, d in enumerate(used)}
    nodes = np.array([[nlook[int(d)] for d in e] for e in edges], dtype=np.int64).reshape((-1, 2))
    np.testing.assert_array_equal(v["folds"][nodes[:, 0]], v["folds"][nodes[:, 1]])
    distances = a11.scalar_distances(rebuilt, nodes, rare[v["folds"][nodes[:, 0]]]); close(pair["distances"], distances)
    terms = a11.scalar_triplets(distances, indices); close(pair["terms"], terms)
    normal_same = np.zeros((len(records), 24), bool); three_same = normal_same.copy()
    for ri, row in enumerate(records):
        for layer in range(24):
            q, a1, b1 = [set(v["actual_ids"][nlook[int(d)], layer]) for d in row[3:6]]
            normal_same[ri, layer] = a1 == b1; three_same[ri, layer] = q == a1 == b1
    np.testing.assert_array_equal(pair["normal_same"], normal_same); np.testing.assert_array_equal(pair["three_same"], three_same)
    for off in (0, 4, 8): close(terms[:, off], terms[:, off+1]+terms[:, off+2])
    checks = replay_summaries(a, metadata, graph, records, terms, normal_same, three_same, result)
    assert result["used_looks"] == len(used) and result["triplets"] == len(records) and result["unique_edges"] == len(edges)
    for path, digest in log["input_sha256"].items(): assert sha((ROOT/path).read_bytes()) == digest
    assert m12.source_freeze(log["implementation_commit"])[1] == log["source_sha256"]
    report = {"status": "PASS", "pair_selections_replayed": selections, "used_looks_rebuilt": len(used),
              "unique_edges_replayed": len(edges), "triplets_replayed": len(records), "summaries_and_intervals_replayed": checks,
              "distance_max_error": float(abs(pair["distances"]-distances).max(initial=0.)),
              "run_manifest_sha256": sha(body), "elapsed_seconds": time.monotonic()-start, "access_guard": guard.summary()}
    assert not report["access_guard"]["blocked_attempts"]
    out.mkdir(); write_json(out/"checks.json", report); print(json.dumps(report), flush=True)


if __name__ == "__main__": run()
