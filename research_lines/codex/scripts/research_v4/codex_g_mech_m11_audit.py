"""Independent cache-to-distribution and distribution-to-distance M11 replay."""
from __future__ import annotations

import json
import math
import time
import numpy as np

from research_v4 import codex_g_mech_m11 as m11
from research_v4 import codex_g_mech_m10_audit as a10
from research_v4.codex_g_mech_m9_audit import enumerate_experts, verify_summary
from research_v4.codex_g_m8_audit import close, manual_episode_means
from research_v4.codex_g_m1 import ROOT, sha, write_json


def scalar_vectors(ids, logits):
    n, layers, experts = logits.shape; k = ids.shape[-1]
    out = np.zeros((n, 3, layers, experts))
    for i in range(n):
        for layer in range(layers):
            chosen = [int(e) for e in ids[i, layer]]
            assert len(set(chosen)) == k
            scores = [float(z) for z in logits[i, layer]]
            top = max(scores); positive = [math.exp(z-top) for z in scores]; norm = math.fsum(positive)
            top_selected = max(scores[e] for e in chosen)
            weights = [math.exp(scores[e]-top_selected) for e in chosen]; norm_selected = math.fsum(weights)
            out[i, 2, layer] = [p/norm for p in positive]
            for j, e in enumerate(chosen):
                out[i, 0, layer, e] = 1/k; out[i, 1, layer, e] = weights[j]/norm_selected
    return out


def scalar_distances(vectors, nodes, rare):
    layers, experts = vectors.shape[-2:]
    out = np.zeros((len(nodes), 12, layers))
    def entropy(v): return -math.fsum(float(p)*math.log2(float(p)) for p in v if p > 0)
    for i, (left, right) in enumerate(nodes):
        for rep in range(3):
            for layer in range(layers):
                p, q = vectors[left, rep, layer], vectors[right, rep, layer]
                coordinate = [abs(float(p[e])-float(q[e]))/2 for e in range(experts)]
                whole = math.fsum(coordinate)
                sparse = math.fsum(coordinate[e] for e in range(experts) if rare[i, layer, e])
                common = math.fsum(coordinate[e] for e in range(experts) if not rare[i, layer, e])
                js = entropy((p+q)/2)-(entropy(p)+entropy(q))/2
                assert js >= -1e-12
                out[i, rep*4:rep*4+4, layer] = (whole, sparse, common, max(0., js))
    return out


def scalar_triplets(distances, indices):
    out = np.zeros((len(indices), 12, distances.shape[-1], 3))
    for i, (qa, qb, ab) in enumerate(indices):
        for d in range(12):
            for layer in range(distances.shape[-1]):
                attack = (float(distances[qa, d, layer])+float(distances[qb, d, layer]))/2
                normal = float(distances[ab, d, layer])
                out[i, d, layer] = (attack, normal, attack-normal)
    return out


def scalar_aggregate(terms):
    values = np.zeros((len(terms), 144)); profiles = np.zeros((len(terms), 864))
    for d in range(12):
        for metric in range(3):
            for b, layers in enumerate((range(24), range(8), range(8, 16), range(16, 24))):
                values[:, d*12+b*3+metric] = sum((terms[:, d, layer, metric] for layer in layers), np.zeros(len(terms)))/len(layers)
            for layer in range(24): profiles[:, d*72+layer*3+metric] = terms[:, d, layer, metric]
    return values, profiles


def legacy_fraction_copy(reading, qs, values, a):
    assert reading["positive_tolerance"] == 1e-12
    _, means = manual_episode_means(qs, values, a)
    copy = dict(reading)
    if len(means):
        close(reading["positive_fraction"], (means > 1e-12).mean(0))
        # Only adapt the old validator's strict >0 convention; all other fields
        # and the new tolerance-based fractions are independently verified.
        copy["positive_fraction"] = (means > 0).mean(0).tolist()
    return copy


def verify_effect(reading, qs, values, a, metadata):
    verify_summary(legacy_fraction_copy(reading, qs, values, a), qs, values, a, metadata)


def verify_subset(r, qs, ds, values, profiles, a, metadata):
    adjusted = {**r, "effect": legacy_fraction_copy(r["effect"], qs, values, a),
                "layers": legacy_fraction_copy(r["layers"], qs, profiles, a)}
    a10.verify_subset(adjusted, qs, ds, values, profiles, a, metadata)


def verify_conditional(r, qs, terms, mask, a, metadata, *, normal_only):
    counts = [sum(bool(v) for v in row) for row in mask]
    selected = np.array([i for i, count in enumerate(counts) if count], dtype=np.int64)
    values = []
    for i in selected:
        layers = [l for l in range(24) if mask[i, l]]
        values.append([sum(float(terms[i, d, l, metric]) for l in layers)/len(layers)
                       for d in range(12) for metric in ((1,) if normal_only else (0, 1, 2))])
    values = np.array(values).reshape((-1, 12 if normal_only else 36))
    verify_effect(r["effect"], qs[selected], values, a, metadata)
    assert r["candidate_looks"] == len(qs) and r["eligible_layer_pairs"] == sum(counts)
    assert r["layer_counts"] == [sum(bool(row[l]) for row in mask) for l in range(24)]
    assert r["look_layer_counts"] == [{"query_row": int(q), "count": n} for q, n in zip(qs, counts, strict=True)]
    a10.verify_distribution(r["query_distribution"], a, metadata, qs[selected])


def run():
    start = time.monotonic(); guard = m11.M11AccessGuard(ROOT, "audit"); guard.install()
    out = m11.OUT/"audit"
    if out.exists(): raise ValueError("refusing to overwrite M11 audit")
    body = (m11.OUT/"run_manifest.json").read_bytes(); log = json.loads(body)
    assert log["status"] == "completed" and not log["access_guard"]["blocked_attempts"]
    assert m11.source_freeze(log["implementation_commit"])[1] == log["source_sha256"]
    m11.prior_inputs()
    for path, digest in log["input_sha256"].items(): assert sha((ROOT/path).read_bytes()) == digest
    for name, digest in log["output_sha256"].items(): assert sha((m11.OUT/name).read_bytes()) == digest
    assert log["M10_graph_sha256"] == sha((m11.m10.OUT/"triplet_graph.npz").read_bytes())
    read = m11.m10.m9.m8.m7.read_npz
    a = read(m11.m10.m9.m8.OUT/"look_inventory.npz"); graph = read(m11.m10.OUT/"triplet_graph.npz")
    old = read(m11.m10.OUT/"current_terms.npz"); v = read(m11.OUT/"routing_vectors.npz"); pair = read(m11.OUT/"pair_metrics.npz")
    metadata = json.loads((m11.m10.m9.m8.OUT/"episode_metadata.json").read_text())
    manifest = json.loads((m11.m10.m9.m8.m7.OUT/"calibrate/threshold_manifest.json").read_text())
    prior = json.loads((m11.m10.OUT/"result.json").read_text()); result = json.loads((m11.OUT/"result.json").read_text())
    assert tuple(result["columns"]) == m11.mm.COLUMNS and tuple(result["layer_columns"]) == m11.mm.LAYER_COLUMNS
    assert tuple(result["conditional_columns"]) == m11.mm.CONDITIONAL_COLUMNS
    assert tuple(result["normal_conditional_columns"]) == m11.mm.DISTANCES
    rows = [(int(graph["phases"][qi]), ci, qi, int(q), *[int(d) for d in graph["pairs"][ci, qi]])
            for ci in range(4) for qi, q in enumerate(graph["queries"]) if graph["reasons"][ci, qi] == 0]
    records = np.array(sorted(rows)).reshape((-1, 6))
    np.testing.assert_array_equal(pair["records"], records)
    edges = np.array(sorted({tuple(sorted((int(i), int(j)))) for row in records
                            for i, j in ((row[3], row[4]), (row[3], row[5]), (row[4], row[5]))}))
    np.testing.assert_array_equal(pair["edges"], edges)
    elook = {tuple(edge): i for i, edge in enumerate(edges)}
    indices = np.array([[elook[tuple(sorted((int(i), int(j))))] for i, j in ((row[3], row[4]), (row[3], row[5]), (row[4], row[5]))] for row in records])
    np.testing.assert_array_equal(pair["edge_indices"], indices)
    used = v["used"]; np.testing.assert_array_equal(used, old["used"])
    np.testing.assert_array_equal(used, sorted(set(records[:, 3:6].ravel().tolist())))
    lookup = {int(d): i for i, d in enumerate(used)}
    expected_vectors = np.zeros_like(v["vectors"]); rebuilt_s = np.zeros((len(used), 24))
    for ei, key in enumerate(a["keys"]):
        loc = np.flatnonzero(a["episode"][used] == ei)
        if not len(loc): continue
        stem = str(key).split("|", 1)[1].replace("#ep", "--ep"); base = m11.m10.m9.m8.m7.m6.M3
        top = m11.m10.m9.m8.load_bytes(guard.check_path(base/"topk_cache/g_dev"/(stem+".safetensors")).read_bytes())
        logits = m11.m10.m9.m8.load_bytes(guard.check_path(base/"logit_cache/g_dev"/(stem+".logits.safetensors")).read_bytes())["router_logits"].double().numpy()
        ends = a["ends"][used[loc]]; ids = top["top_k_ids"].numpy()[:, ends]
        np.testing.assert_array_equal(ids.transpose(1, 0, 2), v["actual_ids"][loc])
        np.testing.assert_array_equal(top["token_ids"].numpy()[ends], a["tokens"][used[loc], -1])
        rebuilt = scalar_vectors(ids.transpose(1, 0, 2), logits[:, ends].transpose(1, 0, 2))
        close(v["vectors"][loc], rebuilt); expected_vectors[loc] = rebuilt
        row = metadata[str(key)]; state = manifest["cells"]["S"]["folds"][str(row["fold"])]["statistics"]["S"]
        contributions = enumerate_experts(ids, logits[:, ends], np.array(state["q"]), state["config"]["rare_threshold"])
        close(contributions, old["current"][loc]); rebuilt_s[loc] = contributions[:, :, 0]
        np.testing.assert_array_equal(v["folds"][loc], np.full(len(loc), row["fold"]))
    close(v["vectors"].sum(-1), np.ones(v["vectors"].shape[:-1]))
    rare = np.array([np.array(manifest["cells"]["S"]["folds"][str(k)]["statistics"]["S"]["q"]) < .02 for k in range(3)])
    np.testing.assert_array_equal(v["rare"], rare)
    nodes = np.array([[lookup[int(d)] for d in edge] for edge in edges])
    assert np.array_equal(v["folds"][nodes[:, 0]], v["folds"][nodes[:, 1]])
    distances = scalar_distances(expected_vectors, nodes, rare[v["folds"][nodes[:, 0]]])
    close(pair["distances"], distances)
    for i, (left, right) in enumerate(nodes):
        overlap = [len(set(v["actual_ids"][left, l]) & set(v["actual_ids"][right, l])) for l in range(24)]
        close(distances[i, 0], 1-np.array(overlap)/4); close(distances[i, 0], distances[i, 3])
    terms = scalar_triplets(distances, indices); close(pair["terms"], terms)
    for off in (0, 4, 8): close(terms[:, off], terms[:, off+1]+terms[:, off+2])
    values, profiles = scalar_aggregate(terms)
    normal_same = np.zeros((len(records), 24), bool); three_same = normal_same.copy(); floor = np.zeros(len(records), bool)
    for i, row in enumerate(records):
        q, d1, d2 = [lookup[int(d)] for d in row[3:6]]
        floor[i] = all(rebuilt_s[n, l] == 0 for n in (d1, d2) for l in range(24))
        for l in range(24):
            sq, s1, s2 = [set(v["actual_ids"][n, l]) for n in (q, d1, d2)]
            normal_same[i, l] = s1 == s2; three_same[i, l] = sq == s1 == s2
    for field, expected in (("normal_same", normal_same), ("three_same", three_same), ("normal_zero", floor)):
        np.testing.assert_array_equal(pair[field], expected)
    common = (graph["reasons"] == 0).all(0); summaries = 0
    for hi, phase in enumerate(graph["phase_names"]):
        for ci, name in enumerate(graph["cells"]):
            idx = np.flatnonzero((records[:, 0] == hi) & (records[:, 1] == ci)); qs, ds = records[idx, 3], records[idx, 4:6]
            row = result["matrix"][str(phase)][str(name)]; old_row = prior["matrix"][str(phase)][str(name)]
            for field in ("candidate_episodes", "candidate_looks", "failure_looks"): assert row[field] == old_row[field]
            masks = {"matched": np.ones(len(idx), bool), "four_cell_common": common[records[idx, 2]]}
            if ci == 0: masks["endpoint_diameter_le16"] = a10.manual_edges(a, qs, ds)[:, 3:].max(1) <= 16
            for sub, mask in masks.items():
                verify_subset(row[sub], qs[mask], ds[mask], values[idx][mask], profiles[idx][mask], a, metadata); summaries += 2
                assert (row[sub]["effect"]["episodes"], row[sub]["effect"]["looks"]) == (old_row[sub]["effect"]["episodes"], old_row[sub]["effect"]["looks"])
            if ci == 0:
                diag = result["diagnostics"][str(phase)]; select = floor[idx]
                z = diag["normal_both_S_zero"]
                verify_effect(z["effect"], qs[select], values[idx][select], a, metadata); summaries += 1
                assert z["candidate_looks"] == len(idx)
                a10.verify_distribution(z["query_distribution"], a, metadata, qs[select])
                for rep, off in zip(("U", "W", "P"), (0, 4, 8), strict=True):
                    assert z["normal_nonzero_look_counts"][rep] == int((terms[idx][select, off, :, 1].mean(1) > 1e-12).sum())
                for field, mask, normal_only in (("normal_same_support_layers", normal_same[idx], True), ("three_same_support_layers", three_same[idx], False)):
                    verify_conditional(diag[field], qs, terms[idx], mask, a, metadata, normal_only=normal_only); summaries += 1
        print(json.dumps({"audit_stage": "summaries_replayed", "phase": str(phase)}), flush=True)
    assert result["used_looks"] == len(used) and result["triplets"] == len(records) and result["unique_edges"] == len(edges)
    for path, digest in log["input_sha256"].items(): assert sha((ROOT/path).read_bytes()) == digest
    assert m11.source_freeze(log["implementation_commit"])[1] == log["source_sha256"]
    out.mkdir()
    report = {"status": "PASS", "used_looks_rebuilt": len(used), "distributions_rebuilt": len(used)*24*3,
              "unique_edges_replayed": len(edges), "triplets_replayed": len(records), "summaries_and_intervals_replayed": summaries,
              "distance_max_error": float(abs(pair["distances"]-distances).max()), "run_manifest_sha256": sha(body),
              "elapsed_seconds": time.monotonic()-start, "access_guard": guard.summary()}
    assert not report["access_guard"]["blocked_attempts"]
    write_json(out/"checks.json", report); print(json.dumps(report), flush=True)


if __name__ == "__main__": run()
