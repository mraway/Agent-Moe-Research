"""M10 synthetic tests; no dataset, model or tokenizer content access."""
from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import os
import sys
import unittest
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src")); sys.path.insert(0, str(ROOT/"scripts"))
from research_v4 import codex_g_mech_m10 as m10, codex_g_mech_m10_math as mm
from research_v4.codex_g_mech_m10_audit import brute_pair, manual_values, manual_edges, verify_subset


def fixture():
    n = 7
    a = dict(keys=np.array([f"ep{i}" for i in range(n)]), episode=np.arange(n),
             ends=np.arange(100, 100+n), structure=np.zeros((n, 6), dtype=int),
             tokens=np.full((n, 8), 290), tu=np.array([4., 4.01, 4.02, 4.03, 4.04, 4.05, 4.06]), valid=np.ones(n, bool))
    f = m10.features_of(a); scenarios = np.array([f"s{i}" for i in range(n)])
    return f, a, scenarios


class MatchingTest(unittest.TestCase):
    def test_nearest_score_blind_distinct_pair(self):
        f, a, sc = fixture(); matcher = mm.PairMatcher(f, np.arange(1, 7), sc)
        self.assertEqual(matcher.select(0), ([1, 2], 0))
        self.assertEqual(brute_pair(0, np.arange(1, 7), a, sc)[:2], ([1, 2], 0))
        self.assertEqual({x.name for x in fields(f)}, {"keys", "episode", "ends", "structure", "tokens", "tu", "valid"})
        a["values"] = np.full((7, 6), 1e20)  # No route column is ever passed into the selector.
        self.assertEqual(matcher.select(0), ([1, 2], 0))

    def test_episode_dedup_before_pairing(self):
        f, a, sc = fixture(); f.episode[2] = 1
        self.assertEqual(mm.PairMatcher(f, np.arange(1, 7), sc).select(0), ([1, 3], 0))
        self.assertEqual(brute_pair(0, np.arange(1, 7), a, sc)[:2], ([1, 3], 0))

    def test_query_scenario_excluded_and_normal_scenarios_distinct(self):
        f, a, sc = fixture(); sc[1] = sc[0]; sc[3] = sc[2]
        matcher = mm.PairMatcher(f, np.arange(1, 7), sc)
        self.assertEqual(matcher.select(0), ([2, 4], 0))
        self.assertEqual(matcher.select(0, distinct=False), ([2, 3], 0))
        self.assertEqual(brute_pair(0, np.arange(1, 7), a, sc)[:2], ([2, 4], 0))

    def test_mutual_caliper_not_just_query_edges(self):
        f, a, sc = fixture(); f.tu[:3] = [4., 3.8, 4.2]
        self.assertEqual(mm.PairMatcher(f, [1, 2], sc).select(0), ([], 6))
        self.assertEqual(brute_pair(0, [1, 2], a, sc)[:2], ([], 6))

    def test_failure_codes_and_empty_bank(self):
        f, a, sc = fixture()
        self.assertEqual(mm.PairMatcher(f, [], sc).select(0), ([], 1))
        f.tu[1:3] = 8.
        self.assertEqual(mm.PairMatcher(f, [1, 2], sc).select(0), ([], 2))
        f.tu[1:3] = 4.; f.tokens[1:3, -1] = 11
        self.assertEqual(mm.PairMatcher(f, [1, 2], sc).select(0), ([], 3))
        f.tokens[1:3, -1] = 290; sc[1] = sc[0]
        self.assertEqual(mm.PairMatcher(f, [1, 2], sc).select(0), ([], 4))
        sc[1] = sc[2]
        self.assertEqual(mm.PairMatcher(f, [1, 2], sc).select(0), ([], 5))
        f.valid[0] = False
        with self.assertRaises(ValueError): mm.PairMatcher(f, [1, 2], sc).select(0)

    def test_structure_current_token_and_tight_condition(self):
        f, a, sc = fixture(); f.structure[1, 1] = 1; f.tokens[2, -1] = 11; f.tu[3:5] = [4.15, 4.16]
        matcher = mm.PairMatcher(f, np.arange(1, 5), sc)
        self.assertEqual(matcher.select(0), ([3, 4], 0))
        self.assertEqual(matcher.select(0, caliper=.1), ([], 3))

    def test_random_full_bank_independent_replay(self):
        rng = np.random.default_rng(1231)
        for _ in range(20):
            f, a, sc = fixture()
            f.tu[:] = rng.uniform(3.7, 4.3, 7); f.tokens[:, -1] = rng.integers(10, 12, 7)
            f.structure[:, 4] = rng.integers(0, 2, 7); sc[2] = sc[1]
            matcher = mm.PairMatcher(f, np.arange(7), sc)
            for q in range(7):
                for distinct in (False, True):
                    for caliper in (.1, .25):
                        self.assertEqual(matcher.select(q, distinct=distinct, caliper=caliper),
                                         brute_pair(q, np.arange(7), a, sc, distinct=distinct, caliper=caliper)[:2])


class DistanceTest(unittest.TestCase):
    def test_normal_variation_can_exceed_query_normal_distance(self):
        # Normal q happens to be at the center: signed residual zero is not stability.
        np.testing.assert_array_equal(mm.contrasts(10., 0., 20.), [10., 10., 10., 20., -10., 0., 0.])

    def test_three_point_exchangeability_identity(self):
        vals = [np.array([1., 10., -5.]), np.array([5., -3., 2.]), np.array([25., 4., 2.])]
        diffs = [mm.contrasts(vals[i], vals[(i+1) % 3], vals[(i+2) % 3])[..., 4] for i in range(3)]
        np.testing.assert_allclose(np.mean(diffs, axis=0), 0., atol=1e-14)

    def test_query_only_increase_and_identical_ties(self):
        np.testing.assert_array_equal(mm.contrasts(20., 2., 2.), [20., 2., 18., 0., 18., 18., 1.])
        np.testing.assert_array_equal(mm.contrasts(2., 2., 2.), [2., 2., 0., 0., 0., 0., 0.])
        self.assertEqual(mm.contrasts(2.+1e-10, 2., 2.)[-1], 0.)

    def test_donor_order_invariance(self):
        q = np.array([4., 5.]); a = np.array([1., 6.]); b = np.array([3., 7.])
        np.testing.assert_array_equal(mm.contrasts(q, a, b), mm.contrasts(q, b, a))

    def test_per_layer_scores_conserved_but_distances_not_additive(self):
        current = np.zeros((3, 24, 2)); current[0, 0, :] = 10.; current[1:, 8, :] = 10.
        features = mm.current_features(current)
        np.testing.assert_allclose(features[:, 0], features[:, 1:4].sum(1))
        values, profiles = mm.triplet_values(current, np.arange(3), np.array([0]), np.array([[1, 2]]))
        self.assertEqual(values[0, mm.COLUMNS.index("S/total/distance_excess")], 0.)
        self.assertEqual(values[0, mm.COLUMNS.index("S/early/distance_excess")], 10.)
        self.assertEqual(values[0, mm.COLUMNS.index("S/middle/distance_excess")], 10.)
        v, p = manual_values(current, dict(enumerate(range(3))), [0], np.array([[1, 2]]))
        np.testing.assert_allclose(values, v); np.testing.assert_allclose(profiles, p)

    def test_independent_all_layer_replay(self):
        current = np.random.default_rng(21).uniform(0, 15, size=(12, 24, 2))
        qs = np.array([0, 3, 6, 9]); ds = qs[:, None]+[1, 2]
        v, p = mm.triplet_values(current, np.arange(12), qs, ds)
        av, ap = manual_values(current, dict(enumerate(range(12))), qs, ds)
        np.testing.assert_allclose(v, av, atol=1e-12); np.testing.assert_allclose(p, ap, atol=1e-12)

    def test_empty_and_invalid_triplets(self):
        v, p = mm.triplet_values(np.zeros((3, 24, 2)), np.arange(3), np.array([], int), np.empty((0, 2), int))
        self.assertEqual(v.shape, (0, 56)); self.assertEqual(p.shape, (0, 192))
        with self.assertRaises(ValueError): mm.triplet_values(np.zeros((3, 24, 2)), np.arange(3), np.array([0]), np.array([[-1, -1]]))
        with self.assertRaises(ValueError): mm.contrasts(np.nan, 0., 0.)
        with self.assertRaises(ValueError): mm.contrasts(np.ones(2), np.zeros(3), np.zeros(3))


class SummaryAndGuardTest(unittest.TestCase):
    def test_common_support_and_position_subset_do_not_rematch(self):
        f, a, sc = fixture()
        metadata = {str(k): dict(family=f"f{i % 2}", tier=f"T{i % 3}", domain_group="d", injection_channel="c", scenario=sc[i])
                    for i, k in enumerate(f.keys)}
        f.ends[6] = 150
        graph = dict(queries=np.array([0, 1, 2]), phases=np.array([2, 2, 2]),
                     phase_names=np.array(["E_at", "X_pre", "X_at"]),
                     pairs=np.tile(np.array([[4, 5], [4, 6], [4, 5]]), (4, 1, 1)),
                     reasons=np.zeros((4, 3), int))
        graph["reasons"][3, 2] = 4; graph["pairs"][3, 2] = -1
        matrix, cards = m10.summarize(f, metadata, graph, np.ones((7, 24, 2)), np.arange(7))
        primary = matrix["X_at"]["filtered_distinct"]
        self.assertEqual(primary["matched"]["effect"]["looks"], 3)
        self.assertEqual(primary["four_cell_common"]["effect"]["looks"], 2)
        self.assertEqual(primary["endpoint_diameter_le16"]["effect"]["looks"], 2)
        self.assertEqual(matrix["E_at"]["filtered_distinct"]["matched"]["effect"]["looks"], 0)
        self.assertEqual(len(cards), 11)

    def test_episode_equal_summary_reuse_and_empty(self):
        f, a, sc = fixture(); f.episode[1] = 0
        metadata = {str(k): dict(family=f"f{i % 2}", tier=f"T{i % 3}", domain_group="d", injection_channel="c", scenario=sc[i])
                    for i, k in enumerate(f.keys)}
        current = np.ones((7, 24, 2)); current[0:2] = 0; current[2] = 10.
        qs = np.array([0, 1, 2]); ds = np.tile([4, 5], (3, 1)); lookup = np.arange(7)
        v, p = mm.triplet_values(current, lookup, qs, ds)
        r = m10.subset(f, metadata, qs, ds, v, p)
        self.assertEqual(r["effect"]["mean"][0], 120.)
        verify_subset(r, qs, ds, v, p, a, metadata)
        empty = m10.subset(f, metadata, qs[:0], ds[:0], v[:0], p[:0])
        verify_subset(empty, qs[:0], ds[:0], v[:0], p[:0], a, metadata)
        np.testing.assert_allclose(mm.edge_distances(f, qs, ds), manual_edges(a, qs, ds))

    def test_guard_old_writes_raw_traces_weights_and_sealed(self):
        guard = m10.M10AccessGuard(ROOT, "analysis")
        for p in (ROOT/"artifacts/agent_v2/dataset_g/g_conf/trace.json",
                  ROOT/"artifacts/agent_v2/dataset_g/annotations/g_conf/final_unblinded.jsonl",
                  ROOT/"artifacts/agent_v2/dataset_g/g_dev/example/trace.json",
                  ROOT/"artifacts/hf_cache/model.safetensors", m10.m9.TOKENIZER):
            with self.assertRaises(PermissionError): guard.check_path(p)
        guard.check_path(m10.OUT/"result.json")
        for p in (m10.m9.OUT/"result.json", m10.m9.m8.OUT/"result.json", m10.m9.m8.m7.OUT/"calibrate/threshold_manifest.json"):
            with self.assertRaises(PermissionError): guard.audit("open", (str(p), "w", os.O_WRONLY))


if __name__ == "__main__": unittest.main()
