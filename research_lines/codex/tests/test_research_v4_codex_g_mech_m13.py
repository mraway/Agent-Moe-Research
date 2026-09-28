"""Synthetic M13 bank, matching, paired-readout and independent audit checks."""
from __future__ import annotations

from pathlib import Path
import json
import os
import sys
import unittest
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src")); sys.path.insert(0, str(ROOT/"scripts"))
from research_v4 import codex_g_mech_m13 as m13, codex_g_mech_m13_math as mm
from research_v4 import codex_g_mech_m13_audit as audit


def fixture(episodes=tuple(range(6))):
    n = len(episodes)
    a = dict(keys=np.array([f"ep{i}" for i in range(max(episodes)+1)]), episode=np.array(episodes), ends=np.arange(n)+10,
             structure=np.zeros((n, 6), dtype=int), tokens=np.tile(np.arange(8), (n, 1)), tu=np.zeros(n), valid=np.ones(n, bool))
    meta = {str(k): dict(variant="attack", attack_bearing=True, x=None, e=None, trajectory_class="silent",
                          family="family", tier="T1", injection_channel="direct_user", scenario=f"s{i}",
                          stop_reason="eos", domain_group="d", fold=0) for i, k in enumerate(a["keys"])}
    meta["ep0"].update(x=40, e=10, trajectory_class="execution")
    for k in ("ep1", "ep2"): meta[k].update(variant="clean", attack_bearing=False, trajectory_class="silent", filter_pass=True)
    return a, meta


def matcher(a, meta, bank=None):
    f = m13.m10.features_of(a)
    return mm.ControlMatcher(f, np.arange(3, len(a["ends"])) if bank is None else bank, m13.categories(f, meta))


class BankAndMatchTest(unittest.TestCase):
    def test_control_bank_excludes_preinjection_and_preserves_silent_no_E(self):
        a, meta = fixture(tuple(range(8)))
        meta["ep3"].update(attack_bearing=False)
        meta["ep4"].update(trajectory_class="engaged_only", e=14)
        meta["ep5"].update(trajectory_class="committed_no_execution", e=100)
        meta["ep6"].update(trajectory_class="over_refusal", stop_reason="malformed_tool_call")
        banks, counts = mm.control_banks(m13.m10.features_of(a), meta)
        self.assertEqual(banks["silent"].tolist(), [7])
        self.assertEqual(banks["engaged_only"].tolist(), [4])
        self.assertEqual(banks["committed_no_execution"].tolist(), [])
        self.assertEqual(counts["committed_no_execution"]["episodes"], 1)
        self.assertEqual(banks["over_refusal"].tolist(), [6])
        for group in mm.GROUPS:
            expected, stats = audit.brute_bank(a, meta, group)
            np.testing.assert_array_equal(banks[group], expected); self.assertEqual(counts[group], stats)

    def test_event_stage_closed_interval_and_invalid_looks(self):
        a, meta = fixture((0, 1, 2, 3, 3, 3, 3)); a["ends"][3:] = [19, 20, 36, 37]
        meta["ep3"].update(trajectory_class="engaged_only", e=20)
        banks, _ = mm.control_banks(m13.m10.features_of(a), meta)
        self.assertEqual(banks["engaged_only"].tolist(), [4, 5])
        a["valid"][5] = False
        self.assertEqual(mm.control_banks(m13.m10.features_of(a), meta)[0]["engaged_only"].tolist(), [4])
        meta["ep3"]["e"] = None
        with self.assertRaises(ValueError): mm.control_banks(m13.m10.features_of(a), meta)

    def test_filters_channel_family_tier_and_never_changes_normals(self):
        a, meta = fixture(); meta["ep3"]["family"] = "other"; meta["ep4"]["tier"] = "T0"
        m = matcher(a, meta); normals = np.array([1, 2]); original = normals.copy()
        for level, expected in enumerate((3, 4, 5)):
            c, reason, counts = m.select(0, normals, length=8, level=level)
            self.assertEqual((c, reason), (expected, 0)); self.assertEqual(counts.tolist(), [3-level]*3)
        np.testing.assert_array_equal(normals, original)
        meta["ep5"]["injection_channel"] = "tool_output"
        self.assertEqual(matcher(a, meta).select(0, normals, length=8, level=2)[1], 6)

    def test_all_four_scenarios_distinct(self):
        a, meta = fixture(); meta["ep3"]["scenario"] = meta["ep0"]["scenario"]
        meta["ep4"]["scenario"] = meta["ep1"]["scenario"]
        self.assertEqual(matcher(a, meta).select(0, [1, 2], length=8, level=0)[:2], (5, 0))
        meta["ep5"]["scenario"] = meta["ep2"]["scenario"]
        self.assertEqual(matcher(a, meta).select(0, [1, 2], length=8, level=0)[1], 3)

    def test_TU_all_three_new_edges_not_only_query(self):
        a, meta = fixture(); a["tu"][1] = .2; a["tu"][3:] = -.2
        self.assertEqual(matcher(a, meta).select(0, [1, 2], length=1, level=0)[1], 7)
        a["tu"][5] = .1
        self.assertEqual(matcher(a, meta).select(0, [1, 2], length=1, level=0)[:2], (5, 0))

    def test_suffix_includes_current_and_L8_is_stricter(self):
        a, meta = fixture(); a["tokens"][3:5, 0] = 99
        m = matcher(a, meta)
        self.assertEqual(m.select(0, [1, 2], length=1, level=0)[0], 3)
        self.assertEqual(m.select(0, [1, 2], length=8, level=0)[0], 5)
        a["tokens"][5, -1] = 99
        self.assertEqual(matcher(a, meta).select(0, [1, 2], length=8, level=0)[1], 8)

    def test_deterministic_ranking_all_looks_without_dedup(self):
        a, meta = fixture((0, 1, 2, 3, 3, 4)); a["tu"][:] = [0, 0, 0, .1, .05, .05]
        m = matcher(a, meta, np.array([5, 4, 3]))
        self.assertEqual(m.select(0, [1, 2], length=8, level=0)[0], 4)
        self.assertEqual(m.select(0, [1, 2], length=8, level=0)[2].tolist(), [3, 2, 2])

    def test_missing_base_and_all_failure_categories(self):
        a, meta = fixture(); m = matcher(a, meta)
        self.assertEqual(m.select(0, [-1, -1], length=8, level=0)[1], 1)
        a["structure"][3:, 1] = 1
        self.assertEqual(matcher(a, meta).select(0, [1, 2], length=8, level=0)[1], 2)
        a["structure"][:] = 0
        for k in ("ep3", "ep4", "ep5"): meta[k]["injection_channel"] = "tool_output"
        self.assertEqual(matcher(a, meta).select(0, [1, 2], length=8, level=0)[1], 4)
        for k in ("ep3", "ep4", "ep5"): meta[k].update(injection_channel="direct_user", family="other")
        self.assertEqual(matcher(a, meta).select(0, [1, 2], length=8, level=1)[1], 5)

    def test_no_outcome_or_score_arguments_and_invalid_cell(self):
        a, meta = fixture(); m = matcher(a, meta)
        with self.assertRaises(TypeError): m.select(0, [1, 2], length=8, level=0, score=10)
        with self.assertRaises(TypeError): m.select(0, [1, 2], length=8, level=0, outcome="silent")
        with self.assertRaises(ValueError): m.select(0, [1, 2], length=4, level=0)
        with self.assertRaises(ValueError): m.select(0, [1], length=8, level=0)

    def test_independent_matcher_randomized_candidates(self):
        rng = np.random.default_rng(1313); a, meta = fixture(tuple([0, 1, 2]+rng.integers(3, 18, 90).tolist()))
        a["tokens"] = rng.integers(0, 2, a["tokens"].shape); a["tu"][3:] = rng.uniform(-.5, .5, len(a["ends"])-3)
        a["structure"][:, 3] = rng.integers(0, 2, len(a["ends"])); a["structure"][:3, 3] = 0
        for ei, key in enumerate(a["keys"]):
            if ei >= 3: meta[str(key)].update(family="family" if ei%2 else "other", tier="T1" if ei%3 else "T2")
        bank = np.arange(3, len(a["ends"])); m = matcher(a, meta, bank)
        for length in (1, 8):
            for level in range(3):
                c, reason, counts = m.select(0, [1, 2], length=length, level=level)
                self.assertEqual((c, reason, counts.tolist()), audit.brute_control(0, [1, 2], bank, a, meta, length, level))


class MeasurementTest(unittest.TestCase):
    def test_shared_normal_background_cancels_and_swap_changes_sign(self):
        d = np.zeros((6, 5, 24)); d[:, :, :] = np.array([.4, .6, .3, .2, .4, .2])[:, None, None]
        idx = np.array([np.arange(6)]); terms = mm.measures(d, idx)
        np.testing.assert_allclose(terms[0, 0, 0], [.5, .3, .3, .2, 0., .2, .2], atol=1e-15)
        d[2] = .8; changed = mm.measures(d, idx)
        np.testing.assert_array_equal(terms[..., 5], changed[..., 5])
        swapped = mm.measures(d, np.array([[3, 4, 2, 0, 1, 5]]))
        np.testing.assert_allclose(swapped[..., 5], -changed[..., 5], atol=1e-15)
        np.testing.assert_allclose(changed, audit.scalar_measures(d, idx), atol=1e-15)

    def test_independent_vectors_distances_and_layer_aggregation(self):
        rng = np.random.default_rng(313); ids = np.array([[rng.choice(32, 4, replace=False) for _ in range(24)] for _ in range(4)])
        logits = rng.normal(size=(4, 24, 32)); vec = m13.m11.mm.routing_vectors(ids, logits)
        nodes = np.array(mm.EDGE_ROLES); d = mm.distances(vec, nodes)
        expected = audit.a11.scalar_distances(audit.a11.scalar_vectors(ids, logits), nodes, np.zeros((6, 24, 32), bool))[:, [0, 4, 8, 7, 11]]
        np.testing.assert_allclose(d, expected, atol=2e-15)
        terms = mm.measures(d, np.array([np.arange(6)])); v, p = mm.aggregate(terms); ev, ep = audit.scalar_aggregate(terms)
        np.testing.assert_allclose(v, ev, atol=1e-15); np.testing.assert_allclose(p, ep, atol=1e-15)
        self.assertEqual(v.shape, (1, 140)); self.assertEqual(p.shape, (1, 840))

    def test_same_four_support_mask_and_empty(self):
        terms = np.ones((3, 5, 24, 7)); terms[0, :, 0] = 3.
        mask = np.zeros((3, 24), bool); mask[0, 0] = True; mask[1, 1:3] = True
        keep, v, counts = mm.masked_values(terms, mask)
        self.assertEqual(keep.tolist(), [True, True, False]); self.assertEqual(counts.tolist(), [1, 2, 0])
        np.testing.assert_array_equal(v[0], np.full(35, 3.)); np.testing.assert_array_equal(v[1], np.ones(35))
        self.assertEqual(mm.masked_values(terms, np.zeros_like(mask))[1].shape, (0, 35))
        self.assertEqual(mm.aggregate(np.empty((0, 5, 24, 7)))[0].shape, (0, 140))

    def test_six_edges_and_graph_preserve_normal_pairs(self):
        graph = dict(queries=np.array([0]), normals=np.tile([[1, 2]], (2, 1, 1)),
                     controls=np.full((2, 4, 3, 1), 3), reasons=np.ones((2, 4, 3, 1), int))
        graph["reasons"][0, 0, :, 0] = 0
        rec, edges, idx = mm.records_and_edges(graph)
        self.assertEqual(len(rec), 3); self.assertEqual(len(edges), 6)
        for row in rec: self.assertEqual(row[4:].tolist(), [0, 1, 2, 3])
        np.testing.assert_array_equal(idx[0], idx[1]); np.testing.assert_array_equal(idx[1], idx[2])

    def test_full_summaries_with_common_support_and_independent_audit(self):
        a, meta = fixture(); f = m13.m10.features_of(a)
        g = dict(queries=np.array([0, 1]), normals=np.tile([[2, 3], [2, 3]], (2, 1, 1)),
                 controls=np.tile([4, 5], (2, 4, 3, 1)), reasons=np.zeros((2, 4, 3, 2), int),
                 candidates=np.ones((2, 4, 3, 2, 3), int))
        g["controls"][1, :, 1:, 1] = -1; g["reasons"][1, :, 1:, 1] = 5
        g["controls"][:, 3] = -1; g["reasons"][:, 3] = 2
        rec, edges, idx = mm.records_and_edges(g)
        ids = np.tile(np.arange(4), (6, 24, 1)); logits = np.zeros((6, 24, 32)); logits[0, :, 0] = 1
        vec = m13.m11.mm.routing_vectors(ids, logits); terms = mm.measures(mm.distances(vec, edges), idx)
        same = np.ones((len(rec), 24), bool); result = json.loads(json.dumps({"matrix": m13.summarize(f, meta, g, rec, terms, same)}))
        self.assertEqual(result["matrix"]["L8"]["silent"]["channel_family"]["matched"]["effect"]["looks"], 1)
        self.assertEqual(result["matrix"]["L8"]["over_refusal"]["channel"]["matched"]["effect"]["looks"], 0)
        self.assertEqual(audit.replay_summaries(a, meta, g, rec, terms, same, result), 456)

    def test_query_episode_equal_and_reused_control_weights(self):
        a, meta = fixture((0, 0, 1, 2, 3, 4)); f = m13.m10.features_of(a)
        nodes = np.array([[0, 3, 4, 5], [1, 3, 4, 5], [2, 3, 4, 5]])
        v = np.array([[0.], [2.], [10.]])
        r = m13.subset(f, meta, nodes, v, v)
        self.assertEqual(r["effect"]["mean"], [5.5]); self.assertEqual(r["control_effective_weight"], {"ep4":1.})
        audit.verify_subset(r, nodes, v, v, a, meta)

    def test_guard_denies_sealed_other_lines_and_old_writes(self):
        guard = m13.M13AccessGuard(ROOT, "analysis")
        for p in (ROOT/"artifacts/agent_v2/dataset_g/g_conf/trace.json", ROOT/"artifacts/agent_v2/dataset_g/annotations/g_conf/final_unblinded.jsonl",
                  ROOT/"artifacts/agent_v2/codex_g/algorithm_example/result.json", m13.m10.m9.TOKENIZER):
            with self.assertRaises(PermissionError): guard.check_path(p)
        guard.check_path(m13.OUT/"result.json")
        for p in (m13.m12.OUT/"result.json", m13.m8.OUT/"look_inventory.npz"):
            with self.assertRaises(PermissionError): guard.audit("open", (str(p), "w", os.O_WRONLY))


if __name__ == "__main__": unittest.main()
