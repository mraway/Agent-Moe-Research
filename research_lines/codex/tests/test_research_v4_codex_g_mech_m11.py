"""Synthetic M11 checks; never read dataset content, tokenizer or model weights."""
from __future__ import annotations

from pathlib import Path
import os
import sys
import unittest
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src")); sys.path.insert(0, str(ROOT/"scripts"))
from research_v4 import codex_g_mech_m11 as m11, codex_g_mech_m11_math as mm
from research_v4 import codex_g_mech_m11_audit as audit


def synthetic_vectors():
    rng = np.random.default_rng(192)
    ids = np.array([[rng.choice(32, 4, replace=False) for _ in range(24)] for _ in range(7)])
    logits = rng.normal(size=(7, 24, 32)); rare = rng.random(size=(3, 24, 32)) < .2
    return ids, logits, rare


class VectorTest(unittest.TestCase):
    def test_actual_ids_override_recomputed_topk(self):
        ids = np.array([[[0, 1]]]); logits = np.array([[[0., 0., 1000.]]])
        v = mm.routing_vectors(ids, logits)
        np.testing.assert_array_equal(v[0, :2, 0], [[.5, .5, 0.], [.5, .5, 0.]])
        self.assertEqual(v[0, 2, 0, 2], 1.)

    def test_same_support_different_weights(self):
        ids = np.tile(np.arange(4), (2, 24, 1)); logits = np.zeros((2, 24, 32)); logits[1, :, 0] = 2
        v = mm.routing_vectors(ids, logits); d = mm.pair_metrics(v, np.array([[0, 1]]), np.zeros((1, 24, 32), bool))
        self.assertTrue((d[0, 0] == 0).all()); self.assertTrue((d[0, 4] > 0).all()); self.assertTrue((d[0, 8] > 0).all())

    def test_unselected_change_affects_P_not_U_or_W(self):
        ids = np.tile(np.arange(4), (2, 24, 1)); logits = np.ones((2, 24, 32)); logits[1, :, 4:] -= 3
        v = mm.routing_vectors(ids, logits); d = mm.pair_metrics(v, np.array([[0, 1]]), np.zeros((1, 24, 32), bool))
        np.testing.assert_array_equal(v[0, :2], v[1, :2]); self.assertTrue((d[0, 8] > 0).all())

    def test_translation_invariant_and_simplex(self):
        ids, logits, _ = synthetic_vectors(); v = mm.routing_vectors(ids, logits)
        np.testing.assert_allclose(v, mm.routing_vectors(ids, logits+900), atol=2e-14)
        np.testing.assert_allclose(v.sum(-1), 1., atol=1e-14)
        self.assertTrue((v >= 0).all())

    def test_independent_scalar_softmax(self):
        ids, logits, _ = synthetic_vectors()
        np.testing.assert_allclose(mm.routing_vectors(ids, logits), audit.scalar_vectors(ids, logits), rtol=0, atol=1e-15)

    def test_invalid_geometry_and_duplicates(self):
        ids, logits, _ = synthetic_vectors()
        with self.assertRaises(ValueError): mm.routing_vectors(ids.astype(float), logits)
        with self.assertRaises(ValueError): mm.routing_vectors(ids[:, :3], logits)
        ids[:, :, 1] = ids[:, :, 0]
        with self.assertRaises(ValueError): mm.routing_vectors(ids, logits)
        ids, logits, _ = synthetic_vectors(); logits[0, 0, 0] = np.nan
        with self.assertRaises(ValueError): mm.routing_vectors(ids, logits)


class DistanceTest(unittest.TestCase):
    def test_JS_zero_mass_bounds_and_symmetry(self):
        p = np.array([[1., 0.], [0.25, .75], [0., 1.]]); q = p[::-1]
        np.testing.assert_array_equal(mm.js_divergence(p, p), np.zeros(3))
        np.testing.assert_allclose(mm.js_divergence(p, q), [1., 0., 1.])
        np.testing.assert_array_equal(mm.js_divergence(p, q), mm.js_divergence(q, p))

    def test_U_overlap_identity_and_JS_is_same_for_uniform_sets(self):
        ids = np.array([[[0, 1, 2, 3]], [[0, 1, 4, 5]]]); v = mm.routing_vectors(ids, np.zeros((2, 1, 8)))
        d = mm.pair_metrics(v, np.array([[0, 1]]), np.zeros((1, 1, 8), bool))
        self.assertEqual(d[0, 0, 0], .5); self.assertEqual(d[0, 3, 0], .5)

    def test_independent_distances_and_partition_conservation(self):
        ids, logits, rare = synthetic_vectors(); v = mm.routing_vectors(ids, logits); edges = np.array([[0, 1], [0, 2], [1, 2]])
        d = mm.pair_metrics(v, edges, rare)
        np.testing.assert_allclose(d, audit.scalar_distances(v, edges, rare), rtol=0, atol=2e-15)
        for off in (0, 4, 8): np.testing.assert_allclose(d[:, off], d[:, off+1]+d[:, off+2], atol=1e-15)
        self.assertTrue((d >= 0).all() and (d <= 1+mm.ZERO_TOL).all())

    def test_symmetry_and_same_vector_zero(self):
        ids, logits, rare = synthetic_vectors(); v = mm.routing_vectors(ids, logits)
        np.testing.assert_array_equal(mm.pair_metrics(v, np.array([[0, 0]]), rare[:1]), np.zeros((1, 12, 24)))
        np.testing.assert_array_equal(mm.pair_metrics(v, np.array([[0, 1]]), rare[:1]), mm.pair_metrics(v, np.array([[1, 0]]), rare[:1]))

    def test_zero_rare_projection_can_have_nonzero_full_distance(self):
        ids = np.tile(np.arange(4), (2, 24, 1)); ids[1, :, :] += 4
        v = mm.routing_vectors(ids, np.zeros((2, 24, 32)))
        d = mm.pair_metrics(v, np.array([[0, 1]]), np.zeros((1, 24, 32), bool))
        np.testing.assert_array_equal(d[0, 1], np.zeros(24)); np.testing.assert_array_equal(d[0, 0], np.ones(24))
        np.testing.assert_array_equal(d[0, 0], d[0, 2])

    def test_triplet_permutation_null_and_independent_aggregation(self):
        ids, logits, rare = synthetic_vectors(); v = mm.routing_vectors(ids, logits)
        d = mm.pair_metrics(v, np.array([[0, 1], [0, 2], [1, 2]]), rare[:1].repeat(3, axis=0))
        indices = np.array([[0, 1, 2], [0, 2, 1], [1, 2, 0]])
        t = mm.triplet_measures(d, indices)
        np.testing.assert_allclose(t[:, :, :, 2].mean(0), 0., atol=1e-15)
        np.testing.assert_allclose(t, audit.scalar_triplets(d, indices), atol=1e-15)
        main, profile = mm.aggregate_layers(t); expected, ep = audit.scalar_aggregate(t)
        np.testing.assert_allclose(main, expected, atol=1e-15); np.testing.assert_allclose(profile, ep, atol=1e-15)
        reshaped = main.reshape((3, 12, 4, 3))
        np.testing.assert_allclose(reshaped[:, :, 0], reshaped[:, :, 1:].mean(2), atol=1e-15)


class GraphAndSummaryTest(unittest.TestCase):
    def test_full_summary_preserves_graph_support_and_diagnostic_denominators(self):
        a = dict(keys=np.array([f"q{i}" for i in range(4)]), episode=np.arange(4), ends=np.arange(4),
                 structure=np.zeros((4, 6), int), tokens=np.zeros((4, 8), int), tu=np.zeros(4), valid=np.ones(4, bool))
        f = m11.m10.features_of(a)
        metadata = {k: dict(family="f"+k, tier="T0", domain_group="d", injection_channel="c", scenario="s"+k) for k in a["keys"]}
        graph = dict(queries=np.array([0, 1]), phases=np.array([0, 2]), reasons=np.zeros((4, 2), int),
                     pairs=np.tile([[2, 3], [2, 3]], (4, 1, 1)), phase_names=np.array(["E_at", "X_pre", "X_at"]),
                     cells=np.array([x[0] for x in m11.m10.mm.CELLS]))
        records, edges, indices = mm.records_and_edges(graph)
        ids = np.tile(np.arange(4), (4, 24, 1)); logits = np.zeros((4, 24, 32)); logits[1, :, 0] = 1
        vectors = mm.routing_vectors(ids, logits)
        d = mm.pair_metrics(vectors, edges, np.zeros((len(edges), 24, 32), bool)); terms = mm.triplet_measures(d, indices)
        old = {"matrix": {str(ph): {str(cell): {"candidate_episodes": 126, "candidate_looks": int((graph["phases"] == hi).sum()),
                                                  "failure_looks": {"matched": int((graph["phases"] == hi).sum())}}
                                    for cell in graph["cells"]} for hi, ph in enumerate(graph["phase_names"])}}
        same = np.ones((len(records), 24), bool); zero = np.ones(len(records), bool)
        matrix, diag = m11.summarize({"features": f, "metadata": metadata}, graph, records, terms, same, same, zero, old)
        self.assertEqual(matrix["X_at"]["filtered_distinct"]["matched"]["effect"]["looks"], 1)
        self.assertEqual(matrix["X_pre"]["filtered_distinct"]["matched"]["effect"]["looks"], 0)
        self.assertEqual(diag["X_at"]["three_same_support_layers"]["eligible_layer_pairs"], 24)
        self.assertEqual(diag["E_at"]["normal_both_S_zero"]["normal_nonzero_look_counts"], {"U":0,"W":0,"P":0})
        v, p = audit.scalar_aggregate(terms)
        idx = np.flatnonzero((records[:, 0] == 2) & (records[:, 1] == 0))
        audit.verify_subset(matrix["X_at"]["filtered_distinct"]["matched"], records[idx, 3], records[idx, 4:], v[idx], p[idx], a, metadata)

    def test_graph_exact_reuse_and_edge_dedup(self):
        graph = dict(queries=np.array([0, 1]), phases=np.array([0, 2]), reasons=np.full((4, 2), 4), pairs=np.full((4, 2, 2), -1))
        graph["reasons"][:2, :] = 0; graph["pairs"][:2, :, :] = [[2, 3], [2, 3]]
        original = {k: v.copy() for k, v in graph.items()}
        records, edges, indices = mm.records_and_edges(graph)
        self.assertEqual(len(records), 4); self.assertEqual(len(edges), 5)
        np.testing.assert_array_equal(records[:, :2], [[0, 0], [0, 1], [2, 0], [2, 1]])
        for i, row in enumerate(records):
            for j, nodes in enumerate(((row[3], row[4]), (row[3], row[5]), (row[4], row[5]))):
                self.assertEqual(edges[indices[i, j]].tolist(), sorted(nodes))
        for k in graph: np.testing.assert_array_equal(graph[k], original[k])

    def test_mask_uses_common_layers_and_excludes_empty_looks(self):
        terms = np.ones((3, 12, 24, 3)); terms[0, :, 0] = 5
        mask = np.zeros((3, 24), bool); mask[0, 0] = True; mask[1, [2, 8]] = True
        keep, values, counts = mm.masked_layers(terms, mask)
        np.testing.assert_array_equal(keep, [True, True, False]); np.testing.assert_array_equal(counts, [1, 2, 0])
        np.testing.assert_array_equal(values[0], np.full(36, 5.)); np.testing.assert_array_equal(values[1], np.ones(36))
        self.assertEqual(mm.masked_layers(terms, mask, normal_only=True)[1].shape, (2, 12))
        self.assertEqual(mm.masked_layers(terms, np.zeros((3, 24), bool))[1].shape, (0, 36))

    def test_episode_equal_tolerance_and_conditional_summary_replay(self):
        a = dict(keys=np.array(["q0", "q1"]), episode=np.array([0, 0, 1]), ends=np.arange(3),
                 structure=np.zeros((3, 6), int), tokens=np.zeros((3, 8), int), tu=np.zeros(3), valid=np.ones(3, bool))
        f = m11.m10.features_of(a); metadata = {k: dict(family="f"+k, tier="T0", domain_group="d", injection_channel="c") for k in a["keys"]}
        values = np.array([[1e-14], [1e-14], [10.]])
        r = m11.summary(f, metadata, np.arange(3), values)
        self.assertEqual(r["positive_fraction"], [.5]); self.assertAlmostEqual(r["mean"][0], 5.)
        audit.verify_effect(r, np.arange(3), values, a, metadata)
        terms = np.ones((3, 12, 24, 3)); terms[2] = 5.; mask = np.zeros((3, 24), bool); mask[[0, 2], :2] = True
        for normal in (True, False):
            r = m11.conditional_summary(f, metadata, np.arange(3), terms, mask, normal_only=normal)
            self.assertEqual(r["effect"]["mean"][0], 3.)
            audit.verify_conditional(r, np.arange(3), terms, mask, a, metadata, normal_only=normal)

    def test_empty_triplets_and_empty_summaries(self):
        t = mm.triplet_measures(np.empty((0, 12, 24)), np.empty((0, 3), int))
        self.assertEqual(mm.aggregate_layers(t)[0].shape, (0, 144))
        self.assertEqual(mm.aggregate_layers(t)[1].shape, (0, 864))
        with self.assertRaises(ValueError): mm.masked_layers(t, np.ones((1, 24), bool))

    def test_normal_zero_mask_requires_both_normals_all_layers(self):
        stored = np.zeros((4, 24, 2)); stored[2, 23, 0] = 4.
        node_indices = np.array([[0, 1, 2], [0, 1, 3]])
        mask = stored[node_indices[:, 1:], :, 0].sum((1, 2)) == 0
        np.testing.assert_array_equal(mask, [False, True])

    def test_guard_blocks_old_writes_other_lines_and_sealed(self):
        guard = m11.M11AccessGuard(ROOT, "analysis")
        for path in (ROOT/"artifacts/agent_v2/dataset_g/g_conf/trace.json",
                     ROOT/"artifacts/agent_v2/dataset_g/annotations/g_conf/final_unblinded.jsonl",
                     ROOT/"artifacts/agent_v2/dataset_g/g_dev/example/trace.json", m11.m10.m9.TOKENIZER,
                     ROOT/"artifacts/agent_v2/codex_g/algorithm_example/result.json"):
            with self.assertRaises(PermissionError): guard.check_path(path)
        guard.check_path(m11.OUT/"result.json")
        for path in (m11.m10.OUT/"result.json", m11.m10.m9.m8.m7.OUT/"calibrate/threshold_manifest.json"):
            with self.assertRaises(PermissionError): guard.audit("open", (str(path), "w", os.O_WRONLY))


if __name__ == "__main__": unittest.main()
