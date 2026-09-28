"""Synthetic M12 tests. No data, routing caches, model or sealed files read."""
from __future__ import annotations

from pathlib import Path
import json
import os
import sys
import unittest
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src")); sys.path.insert(0, str(ROOT/"scripts"))
from research_v4 import codex_g_mech_m12 as m12, codex_g_mech_m12_math as mm
from research_v4 import codex_g_mech_m12_audit as audit


def synthetic(episodes=(0, 1, 2, 3)):
    n = len(episodes)
    a = dict(keys=np.array([f"ep{i}" for i in range(max(episodes)+1)]), episode=np.array(episodes),
             ends=np.arange(n)+10, structure=np.zeros((n, 6), dtype=int),
             tokens=np.tile(np.arange(8), (n, 1)), tu=np.zeros(n), valid=np.ones(n, bool))
    scenarios = [f"s{i}" for i in range(len(a["keys"]))]
    return a, scenarios


def matcher(a, scenarios, bank=None):
    return mm.HistoryPairMatcher(m12.m10.features_of(a), np.arange(1, len(a["ends"])) if bank is None else bank, scenarios)


class HistoryMatchingTest(unittest.TestCase):
    def test_L1_exact_old_pair_matcher_and_deterministic_ties(self):
        a, sc = synthetic((0, 1, 1, 2, 3)); a["tu"][:] = [0, .05, .05, .1, .2]
        new = matcher(a, sc); old = m12.m10.mm.PairMatcher(m12.m10.features_of(a), np.arange(1, 5), sc)
        ds, reason, counts = new.select(0, length=1)
        self.assertEqual((ds, reason), old.select(0)); self.assertEqual(ds, [1, 3])
        np.testing.assert_array_equal(counts, [4, 3, 3])
        self.assertEqual(matcher(a, sc, np.array([4, 3, 2, 1])).select(0, length=1)[:2], (ds, reason))

    def test_suffix_includes_current_and_filters_before_dedup(self):
        a, sc = synthetic((0, 1, 1, 2)); a["tu"][:] = [0, 0, .1, .2]; a["tokens"][1, -2] = 99
        m = matcher(a, sc)
        self.assertEqual(m.select(0, length=1)[:2], ([1, 3], 0))
        self.assertEqual(m.select(0, length=2)[:2], ([2, 3], 0))
        a["tokens"][2:, -1] = 99
        self.assertEqual(matcher(a, sc).select(0, length=2)[1], 7)
        a["tokens"][1, -1] = 99
        self.assertEqual(matcher(a, sc).select(0, length=2)[1], 3)

    def test_raw_support_nested_but_pair_support_can_increase(self):
        a, sc = synthetic((0, 1, 1, 2)); a["tu"][:] = [0, -.15, .23, .20]; a["tokens"][1, -2] = 99
        m = matcher(a, sc); lo = m.select(0, length=1); hi = m.select(0, length=2)
        self.assertEqual(lo[1], 6); self.assertEqual(hi[:2], ([3, 2], 0))
        self.assertTrue((hi[2] <= lo[2]).all())

    def test_scenario_exclusion_and_mutual_TU(self):
        a, sc = synthetic(); sc[1] = sc[0]
        self.assertEqual(matcher(a, sc).select(0, length=8)[:2], ([2, 3], 0))
        sc[3] = sc[2]; self.assertEqual(matcher(a, sc).select(0, length=8)[1], 5)
        a, sc = synthetic(); a["tu"][:] = [0, -.2, .2, .24]
        self.assertEqual(matcher(a, sc).select(0, length=1)[:2], ([2, 3], 0))
        a["tu"][3] = .5; self.assertEqual(matcher(a, sc).select(0, length=1)[1], 6)

    def test_failure_counts_and_structural_constraints(self):
        a, sc = synthetic(); a["structure"][1:, 3] = 1
        self.assertEqual(matcher(a, sc).select(0, length=4)[1], 1)
        a["structure"][:] = 0; a["tu"][1:] = 1
        self.assertEqual(matcher(a, sc).select(0, length=4)[1], 2)
        a["tu"][:] = 0; sc[1:] = [sc[0]]*3
        ds, reason, counts = matcher(a, sc).select(0, length=4)
        self.assertEqual((ds, reason), ([], 4)); np.testing.assert_array_equal(counts, [0, 0, 0])

    def test_long_suffix_ignores_earlier_tokens_but_L8_does_not(self):
        a, sc = synthetic(); a["tokens"][1:, 0] = 99
        for length in (1, 2, 4): self.assertEqual(matcher(a, sc).select(0, length=length)[1], 0)
        self.assertEqual(matcher(a, sc).select(0, length=8)[1], 7)

    def test_fixed_graph_never_rematches_and_is_nested(self):
        a, sc = synthetic((0, 1, 1, 2)); a["tokens"][1, -2] = 99
        f = m12.m10.features_of(a); pairs = np.array([[1, 3], [-1, -1]])
        qs = np.array([0, 0]); prior = np.ones(2, bool)
        for length in (1, 2, 4, 8):
            kept = mm.fixed_retained(f, qs, pairs, length)
            self.assertTrue((kept <= prior).all()); prior = kept
        self.assertEqual(prior.tolist(), [False, False])
        self.assertEqual(matcher(a, sc).select(0, length=2)[1], 0)

    def test_independent_matching_random_cells_all_lengths(self):
        rng = np.random.default_rng(1284)
        a, sc = synthetic(tuple([0]+rng.integers(1, 15, size=80).tolist()))
        a["tu"] = rng.uniform(-.5, .5, len(a["ends"])); a["tu"][0] = 0.
        a["tokens"] = rng.integers(0, 2, a["tokens"].shape)
        a["structure"][:, 0] = rng.integers(0, 2, len(a["ends"]))
        a["valid"][5::7] = False
        m = matcher(a, sc); bank = np.arange(1, len(a["ends"]))
        for length in (1, 2, 4, 8):
            ds, reason, counts = m.select(0, length=length)
            expected = audit.brute_history(0, bank, a, sc, length)
            self.assertEqual((ds, reason, counts.tolist()), expected)

    def test_no_scores_accepted_invalid_history_or_cross_step(self):
        a, sc = synthetic(); m = matcher(a, sc)
        with self.assertRaises(TypeError): m.select(0, length=2, scores=np.arange(4))
        with self.assertRaises(ValueError): m.select(0, length=3)
        a["valid"][0] = False
        with self.assertRaises(ValueError): matcher(a, sc).select(0, length=2)


class PairedAndGuardTest(unittest.TestCase):
    def test_paired_differences_use_same_queries_with_reordering(self):
        rec = np.array([[0, 0, 0, 5, 8, 9], [0, 0, 1, 6, 8, 9],
                        [0, 1, 1, 6, 8, 9], [0, 1, 0, 5, 8, 9]])
        values = np.array([[10.], [20.], [21.], [12.]])
        lookup = mm.record_lookup(rec, 3)
        np.testing.assert_array_equal(mm.paired_values(values, lookup, np.array([0, 1]), 1), [[2.], [1.]])
        with self.assertRaises(ValueError): mm.paired_values(values, lookup, np.array([2]), 1)
        with self.assertRaises(ValueError): mm.record_lookup(np.r_[rec, rec[:1]], 3)

    def test_paired_episode_equal_not_look_equal(self):
        a, sc = synthetic((0, 0, 1)); f = m12.m10.features_of(a)
        meta = {str(k): dict(family=f"f{i}", tier="T0", domain_group="d", injection_channel="c") for i, k in enumerate(a["keys"])}
        out = m12.m11.summary(f, meta, np.arange(3), np.array([[0.], [2.], [10.]]))
        self.assertEqual(out["mean"], [5.5])

    def test_empty_pair_values(self):
        out = mm.paired_values(np.zeros((1, 4)), np.full((4, 0), -1), np.array([], dtype=int), 3)
        self.assertEqual(out.shape, (0, 4))

    def test_full_synthetic_summaries_and_independent_audit(self):
        a, sc = synthetic((0, 1, 2, 3, 4)); f = m12.m10.features_of(a)
        meta = {str(k): dict(family=f"f{i}", tier="T0", domain_group="d", injection_channel="c", scenario=sc[i]) for i, k in enumerate(a["keys"])}
        graph = dict(queries=np.array([0, 1]), phases=np.array([0, 2]), phase_names=np.array(["E_at", "X_pre", "X_at"]),
                     pairs=np.tile([[3, 4], [3, 4]], (4, 1, 1)), reasons=np.zeros((4, 2), int),
                     fixed_retained=np.ones((4, 2), bool), candidate_counts=np.zeros((4, 2, 3), int))
        graph["pairs"][2:, 1] = -1; graph["reasons"][2:, 1] = 7; graph["fixed_retained"][2:, 1] = False
        records, edges, idx = m12.m11.mm.records_and_edges(graph)
        ids = np.tile(np.arange(4), (5, 24, 1)); logits = np.zeros((5, 24, 32)); logits[0, :, 0] = 1
        vec = m12.m11.mm.routing_vectors(ids, logits)
        d = m12.m11.mm.pair_metrics(vec, edges, np.zeros((len(edges), 24, 32), bool))
        terms = m12.m11.mm.triplet_measures(d, idx); same = np.ones((len(records), 24), bool)
        result = json.loads(json.dumps({"matrix": m12.summarize(f, meta, graph, records, terms, same, same)}))
        self.assertEqual(result["matrix"]["X_at"]["L8"]["matched"]["effect"]["looks"], 0)
        self.assertEqual(result["matrix"]["X_at"]["L8"]["fixed_graph_rejected"]["effect"]["looks"], 1)
        self.assertEqual(audit.replay_summaries(a, meta, graph, records, terms, same, same, result), 216)

    def test_guard_blocks_sealed_other_lines_tokenizer_and_old_writes(self):
        guard = m12.M12AccessGuard(ROOT, "analysis")
        for p in (ROOT/"artifacts/agent_v2/dataset_g/g_conf/trace.json",
                  ROOT/"artifacts/agent_v2/dataset_g/annotations/g_conf/final_unblinded.jsonl",
                  ROOT/"artifacts/agent_v2/codex_g/algorithm_example/result.json", m12.m10.m9.TOKENIZER,
                  ROOT/"artifacts/agent_v2/dataset_g/g_dev/example/trace.json"):
            with self.assertRaises(PermissionError): guard.check_path(p)
        guard.check_path(m12.OUT/"result.json")
        for p in (m12.m11.OUT/"result.json", m12.m8.OUT/"look_inventory.npz"):
            with self.assertRaises(PermissionError): guard.audit("open", (str(p), "w", os.O_WRONLY))


if __name__ == "__main__": unittest.main()
