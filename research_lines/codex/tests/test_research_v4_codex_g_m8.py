"""M8 synthetic tests: never open project data, labels, or routing caches."""
from __future__ import annotations

from dataclasses import fields
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
from research_v4 import codex_g_m8 as m8, codex_g_m8_math as mm
from research_v4.codex_g_m8_audit import brute_select, verify_effect


def example():
    # Query, two windows of donor-a, donor-b, donor-c, and a structural mismatch.
    return mm.MatchFeatures(
        keys=np.array(["q", "a", "b", "c", "d"]),
        episode=np.array([0, 1, 1, 2, 3, 4]), ends=np.array([20, 19, 20, 21, 22, 20]),
        structure=np.array([[0, 0, 0, 0, 0, 0]]*5+[[1, 0, 0, 0, 0, 0]]),
        tokens=np.array([[1, 2, 3, 4, 5, 6, 7, 8], [1, 2, 3, 4, 5, 6, 7, 8],
                         [1, 2, 3, 4, 5, 6, 7, 8], [9, 2, 3, 4, 5, 6, 7, 8],
                         [1, 2, 3, 4, 5, 6, 7, 9], [1, 2, 3, 4, 5, 6, 7, 8]]),
        tu=np.array([5., 5.04, 5.05, 5.2, 5.02, 5.]), valid=np.ones(6, dtype=bool))


def meta(f):
    return {str(k): dict(variant="attack" if k == "q" else "clean", filter_pass=k != "d", family="f0" if k in ("q", "a") else "f1",
                        tier="T0", domain_group="d", injection_channel="u", trajectory_class="execution" if k == "q" else "silent",
                        attack_bearing=k == "q", x=20 if k == "q" else None, e=10, scenario=str(k)) for k in f.keys}


class GeometryTest(unittest.TestCase):
    def test_causal_prefix_not_final_length(self):
        tokens = np.arange(40); ends = np.arange(7, 40)
        step, good, windows = mm.window_geometry(ends, tokens, [12, 28])
        ps, pg, pw = mm.window_geometry(ends[ends < 23], tokens[:23], [12, 11])
        np.testing.assert_array_equal(ps, step[ends < 23]); np.testing.assert_array_equal(pg, good[ends < 23])
        np.testing.assert_array_equal(pw, windows[ends < 23])
        changed = tokens.copy(); changed[23:] += 100
        cs, cg, cw = mm.window_geometry(ends, changed, [12, 18, 10])
        np.testing.assert_array_equal(cs[ends < 23], ps); np.testing.assert_array_equal(cg[ends < 23], pg)
        np.testing.assert_array_equal(cw[ends < 23], pw)

    def test_cross_step_and_short_step(self):
        ends = np.arange(7, 25)
        step, good, windows = mm.window_geometry(ends, np.arange(25), [12, 3, 10])
        self.assertEqual(ends[~good].tolist(), list(range(12, 22)))
        self.assertEqual(step[ends == 15].tolist(), [2])
        np.testing.assert_array_equal(windows[0], np.arange(8))
        with self.assertRaises(ValueError): mm.window_geometry([7], np.arange(10), [9])
        with self.assertRaises(ValueError): mm.window_geometry([6], np.arange(10), [10])
        s, g, w = mm.window_geometry([], [], [])
        self.assertEqual(w.shape, (0, 8)); self.assertEqual(len(s), 0)


class MatchingTest(unittest.TestCase):
    def test_nested_controls_one_donor_episode_one_vote(self):
        f = example(); matcher = mm.Matcher(f, np.arange(6))
        self.assertEqual(matcher.select(0, "L0"), ([4, 1, 3], 0))
        self.assertEqual(matcher.select(0, "L1"), ([1, 3], 0))
        self.assertEqual(matcher.select(0, "L8"), ([1], 0))
        self.assertEqual(matcher.select(0, "L1_tight"), ([1], 0))
        for version in mm.VERSIONS:
            expected, reason, _ = brute_select(0, np.arange(6), vars(f), version)
            self.assertEqual(matcher.select(0, version), (expected, reason))

    def test_tie_breaks_endpoint_then_episode_key_and_bank_order(self):
        f = example(); f.tu[:] = 5.; f.tokens[:] = f.tokens[0]
        f.ends[1:5] = 20
        self.assertEqual(mm.Matcher(f, [4, 3, 2, 1]).select(0, "L8"), ([2, 3, 4], 0))
        # For real data endpoints are unique within an episode. Enforce that here.
        f.ends[1] = 19
        for bank in ([1, 2, 3, 4], [4, 2, 1, 3]):
            self.assertEqual(mm.Matcher(f, bank).select(0, "L8"), ([2, 3, 4], 0))

    def test_each_failure_stage_and_self_exclusion(self):
        f = example()
        self.assertEqual(mm.Matcher(f, [0, 5]).select(0, "L0"), ([], 1))
        f.tu[1] = 5.3
        self.assertEqual(mm.Matcher(f, [1]).select(0, "L0"), ([], 2))
        self.assertEqual(mm.Matcher(f, [4]).select(0, "L1"), ([], 3))
        self.assertEqual(mm.Matcher(f, [3]).select(0, "L8"), ([], 4))
        f.valid[1] = False
        self.assertEqual(mm.Matcher(f, [1]).select(0, "L0"), ([], 1))
        f.valid[0] = False
        with self.assertRaises(ValueError): mm.Matcher(f, [3]).select(0, "L0")

    def test_api_cannot_see_route_scores_alarms_or_labels(self):
        self.assertEqual({x.name for x in fields(mm.MatchFeatures)}, {"keys", "episode", "ends", "structure", "tokens", "tu", "valid"})
        f = example(); expected = mm.Matcher(f, range(6)).select(0, "L1")
        inventory = {**vars(f), "values": np.zeros((6, 6)), "alarms": np.zeros(6)}
        before = brute_select(0, range(6), inventory, "L1")
        inventory["values"][:] = 1e9; inventory["alarms"][:] = 1
        self.assertEqual(before, brute_select(0, range(6), inventory, "L1"))
        self.assertEqual(expected, before[:2])

    def test_caliper_exact_boundary(self):
        f = example(); f.tu[:] = 0.; f.tu[1] = .25
        self.assertEqual(mm.Matcher(f, [1]).select(0, "L1"), ([1], 0))
        f.tu[1] = np.nextafter(.25, 1.)
        self.assertEqual(mm.Matcher(f, [1]).select(0, "L1"), ([], 2))

    def test_randomized_exhaustive_agreement_and_no_future_features(self):
        rng = np.random.default_rng(71)
        n = 150
        f = mm.MatchFeatures(np.array([f"e{i:02}" for i in range(30)]), np.repeat(np.arange(30), 5),
                             np.tile(np.arange(10, 15), 30), np.zeros((n, 6), dtype=int),
                             rng.integers(0, 2, size=(n, 8)), rng.normal(0, .3, size=n), np.ones(n, dtype=bool))
        matcher = mm.Matcher(f, np.arange(n))
        for query in range(0, n, 7):
            for version in mm.VERSIONS:
                d, r, _ = brute_select(query, np.arange(n), vars(f), version)
                self.assertEqual(matcher.select(query, version), (d, r))


class SummaryTest(unittest.TestCase):
    def test_episode_equal_not_look_equal_and_bootstrap_replay(self):
        f = example(); qs = np.array([0, 1, 2]); values = np.tile(np.array([10., 0., 0.])[:, None], (1, 6))
        summary = mm.effect_summary(f, meta(f), qs, values)
        np.testing.assert_array_equal(summary["mean"], np.full(6, 5.))
        self.assertNotAlmostEqual(summary["mean"][0], values[:, 0].mean())
        verify_effect(summary, qs, values, vars(f), meta(f))

    def test_coverage_denominators_empty_cells_and_three_donors(self):
        f = example(); values = np.arange(36).reshape(6, 6).astype(float)
        qs = np.array([0, 1]); ds = np.array([[3, 4, -1], [-1, -1, -1]])
        effects = np.array([values[0]-values[[3, 4]].mean(0), [np.nan]*6])
        result = mm.cell_summary(f, meta(f), qs, qs, ds, effects, np.array([0, 3]), values, 126)
        sub = result["at_least_one"]
        self.assertEqual(sub["matched_episodes"], 1); self.assertAlmostEqual(sub["episode_fraction_of_candidates"], 1/126)
        self.assertEqual(sub["look_fraction_of_eligible"], .5)
        self.assertEqual(result["at_least_three"]["effect"]["episodes"], 0)
        empty = mm.cell_summary(f, meta(f), np.array([], int), np.array([], int), np.empty((0, 3), int),
                                np.empty((0, 6)), np.array([], int), values, 126)
        self.assertIsNone(empty["at_least_one"]["look_fraction_of_eligible"])

    def test_exact_phase_ranges_not_alarm_based_queries(self):
        f = example(); r = meta(f)
        positives, phases = mm.phase_queries(f, r)
        self.assertEqual(positives, ["q"])
        self.assertEqual(phases["E_at"].tolist(), [0]); self.assertEqual(phases["X_at"].tolist(), [0])
        self.assertEqual(phases["X_pre"].tolist(), [])

    def test_pool_scopes_no_preinjection_or_silent_and_arm_not_class(self):
        f = example(); r = meta(f)
        r["a"].update(variant="legitimate_refusal", trajectory_class="engaged_only")
        r["b"].update(variant="attack", attack_bearing=True, trajectory_class="engaged_only")
        r["c"].update(variant="attack", attack_bearing=False, trajectory_class="over_refusal")
        r["d"].update(variant="attack", attack_bearing=True, trajectory_class="silent")
        pools = mm.pool_episodes(r)
        self.assertEqual(pools["legitimate_refusal"], {"a"}); self.assertEqual(pools["engaged_only"], {"b"})
        self.assertEqual(pools["over_refusal"], set()); self.assertEqual(pools["normal_all"], set())

    def test_common_subset_not_unmatched_difference(self):
        f = example(); metadata = meta(f); values = np.zeros((6, 6))
        graph = dict(queries=np.array([0, 1]), phases=np.array([2, 2]),
                     donors=np.full((6, 4, 2, 3), -1, int), reasons=np.ones((6, 4, 2), int),
                     effects=np.full((6, 4, 2, 6), np.nan))
        graph["donors"][0, 0, :, 0] = 3; graph["donors"][0, 1, 0, 0] = 4
        graph["effects"][0, 0] = [[1]*6, [100]*6]; graph["effects"][0, 1, 0] = [2]*6
        phases = {"E_at": np.array([], int), "X_pre": np.array([], int), "X_at": np.array([0, 1])}
        result = m8.summarize(f, metadata, values, graph, 126, phases)
        common = result["common_query_comparisons"]["X_at"]["normal_filtered"]["L0_to_L1"]
        self.assertEqual(common["same_queries"], [0]); self.assertEqual(common["right_minus_left"]["mean"], [1.]*6)


class GuardTest(unittest.TestCase):
    def test_only_frozen_artifacts_and_no_prior_write(self):
        guard = m8.M8AccessGuard(ROOT, "analysis")
        for path in (ROOT/"artifacts/agent_v2/dataset_g/g_conf/trace.json",
                     ROOT/"artifacts/agent_v2/dataset_g/annotations/g_conf/final_unblinded.jsonl",
                     m8.m7.m6.M3/"logit_cache/g_dev/a.safetensors"):
            with self.assertRaises(PermissionError): guard.check_path(path)
        guard.check_path(m8.m7.OUT/"score/result.json")
        import os
        with self.assertRaises(PermissionError): guard.audit("open", (str(m8.m7.OUT/"score/result.json"), "w", os.O_WRONLY))
        guard.audit("open", (str(m8.OUT/"example.json"), "w", os.O_WRONLY))


if __name__ == "__main__": unittest.main()
