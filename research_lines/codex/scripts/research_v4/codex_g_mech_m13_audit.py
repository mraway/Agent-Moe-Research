"""Independent bank, fourth-node, scalar-route and shared-background M13 audit."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import time
import numpy as np

from research_v4 import codex_g_mech_m13 as m13
from research_v4 import codex_g_mech_m11_audit as a11
from research_v4 import codex_g_mech_m10_audit as a10
from research_v4.codex_g_m8_audit import close, manual_episode_means
from research_v4.codex_g_m1 import ROOT, sha, write_json

EDGES = ((0, 1), (0, 2), (1, 2), (3, 1), (3, 2), (0, 3))


def brute_bank(a, metadata, group):
    eps, valid, stage = [], [], []
    for ei, key in enumerate(a["keys"]):
        row = metadata[str(key)]
        if not (row["variant"] == "attack" and row["attack_bearing"] and row["x"] is None and row["trajectory_class"] == group): continue
        eps.append(ei)
        if group in ("engaged_only", "committed_no_execution"): assert row["e"] is not None
        for d in np.flatnonzero(a["episode"] == ei):
            if not a["valid"][d]: continue
            valid.append(int(d))
            if group in ("silent", "over_refusal") or row["e"] <= a["ends"][d] <= row["e"]+16: stage.append(int(d))
    stats = {"episodes": len(eps), "episode_keys": [str(a["keys"][e]) for e in eps],
             "episodes_with_valid_looks": len({int(a["episode"][d]) for d in valid}), "valid_looks": len(valid),
             "episodes_with_stage_looks": len({int(a["episode"][d]) for d in stage}), "stage_looks": len(stage),
             "stop_reasons": dict(Counter(str(metadata[str(a["keys"][e])]["stop_reason"]) for e in eps))}
    return np.array(sorted(stage), dtype=np.int64), stats


def brute_control(q, normals, bank, a, metadata, length, level):
    if any(d < 0 for d in normals): return -1, 1, [0, 0, 0]
    def row(d): return metadata[str(a["keys"][a["episode"][d]])]
    c = [int(d) for d in bank if a["valid"][d] and tuple(a["structure"][d]) == tuple(a["structure"][q])]
    if not c: return -1, 2, [0, 0, 0]
    excluded = {row(int(d))["scenario"] for d in [q, *normals]}
    c = [d for d in c if row(d)["scenario"] not in excluded]
    if not c: return -1, 3, [0, 0, 0]
    c = [d for d in c if row(d)["injection_channel"] == row(q)["injection_channel"]]
    if not c: return -1, 4, [0, 0, 0]
    if level >= 1:
        c = [d for d in c if row(d)["family"] == row(q)["family"]]
        if not c: return -1, 5, [0, 0, 0]
    if level >= 2:
        c = [d for d in c if row(d)["tier"] == row(q)["tier"]]
        if not c: return -1, 6, [0, 0, 0]
    c = [d for d in c if all(abs(float(a["tu"][d])-float(a["tu"][n])) <= .25 for n in [q, *normals])]
    if not c: return -1, 7, [0, 0, 0]
    c = [d for d in c if all(int(a["tokens"][d, j]) == int(a["tokens"][q, j]) for j in range(8-length, 8))]
    if not c: return -1, 8, [0, 0, 0]
    ranks = [(abs(float(a["tu"][d])-float(a["tu"][q])), abs(int(a["ends"][d])-int(a["ends"][q])),
              int(a["ends"][d]), str(a["keys"][a["episode"][d]]), d) for d in c]
    return min(ranks)[-1], 0, [len(c), len({int(a["episode"][d]) for d in c}), len({row(d)["scenario"] for d in c})]


def manual_balance(a, nodes):
    return np.array([[abs(float(a[var][row[i]])-float(a[var][row[j]])) for var in ("tu", "ends") for i, j in EDGES]
                     for row in nodes]).reshape((-1, 12))


def scalar_measures(d, indices):
    out = np.zeros((len(indices), 5, 24, 7))
    for i, edges in enumerate(indices):
        for j in range(5):
            for l in range(24):
                qa, qb, ab, ca, cb, qc = [float(d[e, j, l]) for e in edges]
                aq, ac = (qa+qb)/2, (ca+cb)/2
                dq, dc = aq-ab, ac-ab
                out[i, j, l] = aq, ac, ab, dq, dc, dq-dc, qc
    return out


def scalar_aggregate(terms):
    values = np.zeros((len(terms), 140)); layers = np.zeros((len(terms), 840))
    for d in range(5):
        for metric in range(7):
            for bi, selected in enumerate((range(24), range(8), range(8, 16), range(16, 24))):
                values[:, d*28+bi*7+metric] = sum((terms[:, d, l, metric] for l in selected), np.zeros(len(terms)))/len(selected)
            for l in range(24): layers[:, d*168+l*7+metric] = terms[:, d, l, metric]
    return values, layers


def verify_subset(r, nodes, values, profiles, a, metadata):
    qs, ds, cs = nodes[:, 0], nodes[:, 1:3], nodes[:, 3]
    a11.verify_subset(r, qs, ds, values, profiles, a, metadata)
    a10.verify_distribution(r["control_distribution"], a, metadata, cs)
    if not len(qs): return
    edges = manual_balance(a, nodes)
    assert r["four_balance"]["columns"] == [f"{var}_{i}_{j}" for var in ("TU", "end") for i, j in EDGES]
    close(r["four_balance"]["episode_equal_mean"], manual_episode_means(qs, edges, a)[1].mean(0))
    close(r["four_balance"]["edge_max"], edges.max(0))
    epcounts = Counter(int(a["episode"][q]) for q in qs); reuse, weights, sw = Counter(), defaultdict(float), defaultdict(float)
    for q, c in zip(qs, cs, strict=True):
        key = str(a["keys"][a["episode"][c]]); reuse[key] += 1
        w = 1/(len(epcounts)*epcounts[int(a["episode"][q])]); weights[key] += w; sw[metadata[key]["scenario"]] += w
    assert r["control_reuse"] == dict(reuse)
    for field, source in (("control_effective_weight", weights), ("control_scenario_effective_weight", sw)):
        assert set(r[field]) == set(source)
        close([r[field][k] for k in sorted(source)], [source[k] for k in sorted(source)])
    close(r["max_control_effective_weight"], max(weights.values())); close(r["max_control_scenario_effective_weight"], max(sw.values()))
    assert r["control_stop_reasons"] == dict(Counter(str(metadata[k]["stop_reason"]) for k in reuse))
    expected = []
    for q, c in zip(qs, cs, strict=True):
        e = metadata[str(a["keys"][a["episode"][c]])]["e"]
        expected.append({"query_row": int(q), "control_row": int(c), "offset": None if e is None else int(a["ends"][c])-int(e)})
    assert r["control_E_offsets"] == expected


def verify_conditional(r, qs, terms, mask, a, metadata):
    chosen, values, counts = [], [], []
    for i, row in enumerate(mask):
        layers = [l for l in range(24) if row[l]]; counts.append(len(layers))
        if not layers: continue
        chosen.append(i)
        values.append([sum(float(terms[i, d, l, metric]) for l in layers)/len(layers) for d in range(5) for metric in range(7)])
    selected = np.array(chosen, dtype=int); values = np.array(values).reshape((-1, 35))
    a11.verify_effect(r["effect"], qs[selected], values, a, metadata)
    assert r["candidate_looks"] == len(qs) and r["eligible_layer_quads"] == sum(counts)
    assert r["layer_counts"] == [sum(bool(row[l]) for row in mask) for l in range(24)]
    assert r["look_layer_counts"] == [{"query_row": int(q), "count": n} for q, n in zip(qs, counts, strict=True)]
    a10.verify_distribution(r["query_distribution"], a, metadata, qs[selected])


def replay_summaries(a, metadata, graph, records, terms, same, result):
    values, profiles = scalar_aggregate(terms); lookup = {tuple(row[:4]): i for i, row in enumerate(records)}
    good = graph["reasons"] == 0; checks = 0
    def indices(cell, mask): return np.array([lookup[(*cell, int(q))] for q in np.flatnonzero(mask)], dtype=int)
    def check(r, cell, mask):
        idx = indices(cell, mask); verify_subset(r, records[idx, 4:], values[idx], profiles[idx], a, metadata)
    def pair(r, left, right, mask):
        ia, ib = indices(left, mask), indices(right, mask)
        for field, source in (("effect", values), ("layers", profiles)):
            a11.verify_effect(r[field], graph["queries"][mask], source[ib]-source[ia], a, metadata)
    for li, length in enumerate((1, 8)):
        base = (graph["normals"][li] >= 0).all(1)
        for gi, group in enumerate(("silent", "engaged_only", "committed_no_execution", "over_refusal")):
            common = good[li, gi].all(0)
            for ci, level in enumerate(("channel", "channel_family", "channel_family_tier")):
                r = result["matrix"][f"L{length}"][group][level]; mask = good[li, gi, ci]; cell = (li, gi, ci)
                idx = indices(cell, mask); qs = graph["queries"][mask]
                assert r["candidate_episodes"] == 126 and r["candidate_looks"] == len(graph["queries"])
                assert r["base_looks"] == int(base.sum()) and r["base_episodes"] == len(set(a["episode"][graph["queries"][base]].tolist()))
                labels = ("matched", "no_base_normal_pair", "no_structural_control", "scenario_conflict", "injection_channel_mismatch",
                          "family_mismatch", "tier_mismatch", "tu_outside_caliper", "suffix_mismatch")
                assert r["failure_looks"] == dict(Counter(labels[int(x)] for x in graph["reasons"][li, gi, ci]))
                if base.any(): close(r["candidates_episode_equal"], manual_episode_means(graph["queries"][base], graph["candidates"][li, gi, ci, base], a)[1].mean(0))
                else: assert r["candidates_episode_equal"] is None
                pos = mask.copy(); pos[np.flatnonzero(mask)] = manual_balance(a, records[idx, 4:])[:, 6:].max(1) <= 16
                bc = mask & good[li, gi, 0]; lc = mask & good[1-li, gi, ci]
                for field, c, sel in (("matched", cell, mask), ("endpoint_diameter_le16", cell, pos), ("three_level_common", cell, common),
                    ("channel_common_baseline", (li, gi, 0), bc), ("channel_common_current", cell, bc),
                    ("length_common_L1", (0, gi, ci), lc), ("length_common_L8", (1, gi, ci), lc)):
                    check(r[field], c, sel); checks += 2
                pair(r["paired_level_change"], (li, gi, 0), cell, bc); pair(r["paired_length_change"], (0, gi, ci), (1, gi, ci), lc); checks += 4
                verify_conditional(r["same_four_support_layers"], qs, terms[idx], same[idx], a, metadata); checks += 1
                assert r["changed_controls_on_channel_common"] == sum(graph["controls"][li, gi, ci, q] != graph["controls"][li, gi, 0, q] for q in np.flatnonzero(bc))
            print(json.dumps({"audit_stage": "control_summaries_replayed", "length": length, "group": group}), flush=True)
    return checks


def run():
    start = time.monotonic(); guard = m13.M13AccessGuard(ROOT, "audit"); guard.install(); out = m13.OUT/"audit"
    if out.exists(): raise ValueError("refusing to overwrite M13 audit")
    body = (m13.OUT/"run_manifest.json").read_bytes(); log = json.loads(body)
    assert log["status"] == "completed" and not log["access_guard"]["blocked_attempts"]
    assert m13.source_freeze(log["implementation_commit"])[1] == log["source_sha256"]
    m13.prior_inputs()
    for path, digest in log["input_sha256"].items(): assert sha((ROOT/path).read_bytes()) == digest
    for name, digest in log["output_sha256"].items(): assert sha((m13.OUT/name).read_bytes()) == digest
    assert log["graph_sha256"] == log["output_sha256"]["control_graph.npz"]
    read = m13.m8.m7.read_npz
    a = read(m13.m8.OUT/"look_inventory.npz"); old = read(m13.m12.OUT/"triplet_graph.npz")
    g = read(m13.OUT/"control_graph.npz"); v = read(m13.OUT/"routing_vectors.npz"); quad = read(m13.OUT/"quad_metrics.npz")
    metadata = json.loads((m13.m8.OUT/"episode_metadata.json").read_text()); result = json.loads((m13.OUT/"result.json").read_text())
    bank_counts = json.loads((m13.OUT/"bank_counts.json").read_text())
    assert result["bank_counts"] == bank_counts and result["excluded_pre_injection_episodes"] == 88
    assert sum(r["variant"] == "attack" and not r["attack_bearing"] for r in metadata.values()) == 88
    np.testing.assert_array_equal(g["queries"], old["queries"][old["phases"] == 0])
    np.testing.assert_array_equal(g["normals"], old["pairs"][[0, 3]][:, old["phases"] == 0])
    assert g["lengths"].tolist() == [1, 8] and g["levels"].tolist() == ["channel", "channel_family", "channel_family_tier"]
    assert g["groups"].tolist() == ["silent", "engaged_only", "committed_no_execution", "over_refusal"]
    assert result["columns"] == list(m13.mm.COLUMNS) and result["layer_columns"] == list(m13.mm.LAYER_COLUMNS)
    selections = 0
    for gi, group in enumerate(g["groups"]):
        bank, stats = brute_bank(a, metadata, str(group)); assert stats == bank_counts[str(group)]
        assert len(stats["episode_keys"]) == (40, 33, 12, 53)[gi]
        np.testing.assert_array_equal(g[f"bank_{group}"], bank); cells = defaultdict(list)
        for d in bank: cells[tuple(a["structure"][d])].append(int(d))
        for li, length in enumerate((1, 8)):
            for ci in range(3):
                for qi, q in enumerate(g["queries"]):
                    c, reason, counts = brute_control(int(q), g["normals"][li, qi], cells[tuple(a["structure"][q])], a, metadata, length, ci)
                    assert c == g["controls"][li, gi, ci, qi] and reason == g["reasons"][li, gi, ci, qi]
                    assert counts == g["candidates"][li, gi, ci, qi].tolist(); selections += 1
        print(json.dumps({"audit_stage": "control_matching_replayed", "group": str(group)}), flush=True)
    assert (np.diff(g["candidates"], axis=2) <= 0).all() and (np.diff((g["reasons"] == 0).astype(int), axis=2) <= 0).all()
    records = np.array([(li, gi, ci, qi, int(q), *map(int, g["normals"][li, qi]), int(g["controls"][li, gi, ci, qi]))
                         for li in range(2) for gi in range(4) for ci in range(3) for qi, q in enumerate(g["queries"])
                         if g["reasons"][li, gi, ci, qi] == 0], dtype=np.int64).reshape((-1, 8))
    np.testing.assert_array_equal(quad["records"], records)
    edges = np.array(sorted({tuple(sorted((int(r[4+i]), int(r[4+j])))) for r in records for i, j in EDGES}), dtype=np.int64).reshape((-1, 2))
    np.testing.assert_array_equal(quad["edges"], edges); elook = {tuple(e): i for i, e in enumerate(edges)}
    indices = np.array([[elook[tuple(sorted((r[4+i], r[4+j])))] for i, j in EDGES] for r in records], dtype=np.int64).reshape((-1, 6))
    np.testing.assert_array_equal(quad["edge_indices"], indices)
    used = v["used"]; np.testing.assert_array_equal(used, sorted(set(records[:, 4:].ravel().tolist())))
    rebuilt = np.zeros_like(v["vectors"])
    for ei, key in enumerate(a["keys"]):
        loc = np.flatnonzero(a["episode"][used] == ei)
        if not len(loc): continue
        assert str(key).startswith("g_dev|"); stem = str(key).split("|", 1)[1].replace("#ep", "--ep"); base = m13.m8.m7.m6.M3
        top = m13.m8.load_bytes(guard.check_path(base/"topk_cache/g_dev"/(stem+".safetensors")).read_bytes())
        logits = m13.m8.load_bytes(guard.check_path(base/"logit_cache/g_dev"/(stem+".logits.safetensors")).read_bytes())["router_logits"].double().numpy()
        ends = a["ends"][used[loc]]; ids = top["top_k_ids"].numpy()[:, ends]
        np.testing.assert_array_equal(v["actual_ids"][loc], ids.transpose(1, 0, 2))
        for ri, end in zip(loc, ends, strict=True): np.testing.assert_array_equal(top["token_ids"].numpy()[end-7:end+1], a["tokens"][used[ri]])
        rebuilt[loc] = a11.scalar_vectors(ids.transpose(1, 0, 2), logits[:, ends].transpose(1, 0, 2))
        np.testing.assert_array_equal(v["folds"][loc], np.full(len(loc), metadata[str(key)]["fold"]))
    close(v["vectors"], rebuilt); close(v["vectors"].sum(-1), np.ones(v["vectors"].shape[:-1]))
    prior_v = read(m13.m12.OUT/"routing_vectors.npz"); overlap, here, there = np.intersect1d(used, prior_v["used"], return_indices=True)
    np.testing.assert_array_equal(v["vectors"][here], prior_v["vectors"][there]); assert result["replay"]["M12_overlap_looks"] == len(overlap)
    nlook = {int(d): i for i, d in enumerate(used)}
    nodes = np.array([[nlook[int(d)] for d in e] for e in edges], dtype=np.int64).reshape((-1, 2))
    np.testing.assert_array_equal(v["folds"][nodes[:, 0]], v["folds"][nodes[:, 1]])
    distances = a11.scalar_distances(rebuilt, nodes, np.zeros((len(edges), 24, 32), bool))[:, [0, 4, 8, 7, 11]]
    close(quad["distances"], distances); terms = scalar_measures(distances, indices); close(quad["terms"], terms)
    same = np.zeros((len(records), 24), bool)
    for ri, row in enumerate(records):
        for l in range(24):
            sets = [set(v["actual_ids"][nlook[int(d)], l]) for d in row[4:]]
            same[ri, l] = sets[0] == sets[1] == sets[2] == sets[3]
    np.testing.assert_array_equal(quad["same"], same)
    prior_m = read(m13.m12.OUT/"pair_metrics.npz")
    prior_idx = {(int(r[1]), int(r[3])): i for i, r in enumerate(prior_m["records"]) if r[0] == 0}
    for i, row in enumerate(records):
        old_term = prior_m["terms"][prior_idx[(0 if row[0] == 0 else 3, int(row[4]))]][[0, 4, 8, 7, 11]]
        close(terms[i][..., [0, 2, 3]], old_term)
    close(terms[:, :, :, 5], terms[:, :, :, 3]-terms[:, :, :, 4])
    checks = replay_summaries(a, metadata, g, records, terms, same, result)
    assert result["used_looks"] == len(used) and result["quadruplets"] == len(records) and result["unique_edges"] == len(edges)
    for path, digest in log["input_sha256"].items(): assert sha((ROOT/path).read_bytes()) == digest
    assert m13.source_freeze(log["implementation_commit"])[1] == log["source_sha256"]
    report = {"status": "PASS", "control_selections_replayed": selections, "used_looks_rebuilt": len(used),
              "unique_edges_replayed": len(edges), "quadruplets_replayed": len(records), "summaries_and_intervals_replayed": checks,
              "M12_query_normal_terms_replayed": len(records), "distance_max_error": float(abs(quad["distances"]-distances).max(initial=0.)),
              "run_manifest_sha256": sha(body), "elapsed_seconds": time.monotonic()-start, "access_guard": guard.summary()}
    assert not report["access_guard"]["blocked_attempts"]
    out.mkdir(); write_json(out/"checks.json", report); print(json.dumps(report), flush=True)


if __name__ == "__main__": run()
