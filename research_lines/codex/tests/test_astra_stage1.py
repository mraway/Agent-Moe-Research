"""Stage-1 mathematical / causal contracts; no target data used by tests."""

import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from phase_a.routing_monitor import (
    DiagonalGeometry, conformal_threshold, fit_static_bank, history_tail,
    monitor_stream, onset_hit, score_static, shuffle_reference_layers,
    signatures, state_scores, synthetic_monitor_audit, validate_group_roles,
)
import run_astra_stage1 as runner


def routing(tokens=40, offset=0):
    base = torch.arange(8)[None, None, :]
    token = torch.arange(tokens)[None, :, None]
    layer = torch.arange(16)[:, None, None]
    return (base + token * 3 + layer * 2 + offset) % 64


class CalibrationTest(unittest.TestCase):
    def test_small_alpha_abstains_instead_of_clamping(self):
        value = conformal_threshold(list(range(60)), .01)
        self.assertEqual(value["threshold"], math.inf)
        self.assertFalse(value["attainable"])
        self.assertEqual(value["effective_alpha_upper_bound"], 0)

    def test_group_order_statistic(self):
        value = conformal_threshold(list(range(60)), .1)
        self.assertEqual(value["rank"], 55)
        self.assertEqual(value["threshold"], 54)
        self.assertAlmostEqual(value["effective_alpha_upper_bound"], 6/61)

    def test_ties_are_conservative(self):
        self.assertEqual(history_tail([1]*60, 1), 1)
        self.assertIsNone(monitor_stream([7, 8], [1., 1.], 1.)["first_alarm"])
        self.assertEqual(history_tail([1]*60, 2), 1/61)

    def test_empty_calibration_is_not_zero_evidence(self):
        for values in ([], [-math.inf]*60, [math.nan], [math.inf]):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    conformal_threshold(values, .1)

    def test_some_empty_paths_remain_in_denominator(self):
        cal = [-math.inf] * 10 + list(range(50))
        self.assertEqual(conformal_threshold(cal, .1)["groups"], 60)

    def test_disjoint_group_roles_required(self):
        validate_group_roles(["a"], ["b"], ["c"])
        with self.assertRaises(ValueError):
            validate_group_roles(["a"], ["a"], ["c"])
        with self.assertRaises(ValueError):
            validate_group_roles([], ["a"], ["c"])

    def test_length_cap_is_not_exchangeability(self):
        audit = runner.synthetic_calibration_audit()
        self.assertLess(abs(audit["matched_group_far"] - 6/61), .02)
        self.assertGreater(audit["long_target_capped_to_calibration_max_far"], .15)


class CurrentStateTest(unittest.TestCase):
    def test_normal_has_no_episode(self):
        result = monitor_stream(range(100), [0.]*100, 1.)
        self.assertEqual(result["final_state"], "NORMAL")
        self.assertEqual(result["episode_count"], 0)

    def test_single_spike_recovers_but_history_stays(self):
        result = monitor_stream(range(60), [0.]*10 + [10.] + [0.]*49, 5.)
        self.assertEqual(result["final_state"], "RECOVERING")
        self.assertEqual(result["first_alarm"], 10)
        self.assertEqual(result["outputs"][-1]["history_max"], 10.)
        self.assertNotIn("SUSTAINED", [r["state"] for r in result["outputs"]])

    def test_persistent_score_is_sustained(self):
        result = monitor_stream(range(60), [0.]*10 + [10.]*50, 5.)
        self.assertEqual(result["final_state"], "SUSTAINED")

    def test_second_episode_resets_current_not_first_alarm(self):
        values = [0.]*10 + [10.]*40 + [0.]*40 + [10.]*40
        result = monitor_stream(range(len(values)), values, 5.)
        self.assertEqual(result["episode_count"], 2)
        self.assertEqual(result["first_alarm"], 10)
        self.assertEqual(result["outputs"][-1]["episode_start"], 90)
        self.assertEqual(result["final_state"], "SUSTAINED")

    def test_short_tail_is_censored(self):
        result = monitor_stream(range(20), [0.]*10 + [10.]*10, 5.)
        self.assertEqual(result["final_state"], "UNCERTAIN")
        self.assertTrue(result["censored"])

    def test_prefix_invariance(self):
        values = [0.]*10 + [10.]*40 + [0.]*40
        full = monitor_stream(range(len(values)), values, 5.)["outputs"]
        for length in (1, 10, 11, 30, 60, 89):
            self.assertEqual(full[:length], monitor_stream(range(length), values[:length], 5.)["outputs"])

    def test_invalid_stream_rejected(self):
        for ends, scores in (([7, 9], [1., 1.]), ([7], []), ([7], [math.nan]),
                             ([7], [math.inf]), ([-1], [1.]), ([7.5], [1.])):
            with self.subTest(ends=ends, scores=scores):
                with self.assertRaises(ValueError):
                    monitor_stream(ends, scores, 1.)

    def test_empty_stream_is_unobserved_not_an_alarm(self):
        result = monitor_stream([], [], 1.)
        self.assertEqual(result["outputs"], [])
        self.assertIsNone(result["first_alarm"])

    def test_infinite_threshold_never_fires(self):
        self.assertIsNone(monitor_stream([7, 8], [1e10, 1e20], math.inf)["first_alarm"])

    def test_legacy_max_state_defect_reproduced(self):
        case = synthetic_monitor_audit()["single_spike_then_normal"]
        self.assertEqual(case["local_state"], "RECOVERING")
        self.assertEqual(case["legacy_accumulated_state"], "SUSTAINED")


class TimingTest(unittest.TestCase):
    def test_no_tolerance_inflation(self):
        self.assertEqual(onset_hit(19, [10, 10], 8), {"start_point": False, "definite": False, "possible": False})

    def test_interval_definite_possible(self):
        self.assertEqual(onset_hit(19, [10, 14], 8), {"start_point": False, "definite": False, "possible": True})
        self.assertEqual(onset_hit(16, [10, 14], 8), {"start_point": True, "definite": True, "possible": True})
        self.assertFalse(onset_hit(9, [10, 14], 8)["possible"])

    def test_early_alarm_cannot_be_replaced(self):
        result = monitor_stream(range(20), [0.]*3 + [2.]*17, 1.)
        self.assertFalse(onset_hit(result["first_alarm"], [10, 10], 8)["start_point"])


class RepresentationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(2)
        cls.bank = fit_static_bank([routing(offset=i) for i in (0, 10, 20)])

    def test_wgm_extraction_matches_source_equations(self):
        x = torch.arange(30, dtype=torch.float).reshape(10, 3)
        model = DiagonalGeometry.fit([x[:4], x[4:]])
        expected_z = (x-x.mean(0)) / (x.std(0)+1e-3)
        expected = ((expected_z-expected_z.mean(0))**2).sum(1)
        self.assertTrue(torch.allclose(model.score(x), expected))

    def test_reference_shuffle_preserves_whole_layer_vectors(self):
        ref = self.bank.anchors
        shuffled = shuffle_reference_layers(ref)
        for layer in range(16):
            self.assertEqual(sorted(map(tuple, ref[:, layer].tolist())), sorted(map(tuple, shuffled[:, layer].tolist())))
        self.assertFalse(torch.equal(ref, shuffled))

    def test_joint_compatibility_not_implied_by_individual_layers(self):
        bank = torch.zeros(10, 16, 64)
        bank[:5, :, 0] = 1
        bank[5:, :, 1] = 1
        query = torch.zeros(1, 16, 64)
        query[:, :8, 0] = 1
        query[:, 8:, 1] = 1
        joint, independent = state_scores(query, bank)
        self.assertAlmostEqual(float(joint[0]), .5)
        self.assertAlmostEqual(float(independent[0]), 0.)

    def test_within_window_order_is_invisible(self):
        x = routing(tokens=8)
        _, original = signatures(x)
        _, reversed_order = signatures(x.flip(1))
        self.assertTrue(torch.equal(original, reversed_order))

    def test_scoring_prefix_invariance_and_first_endpoint(self):
        ends, scores = score_static(routing(31), self.bank)
        pends, pscores = score_static(routing(24), self.bank)
        self.assertEqual(pends[0], 7)
        self.assertEqual(pends, ends[:len(pends)])
        for key in scores:
            self.assertTrue(torch.allclose(torch.tensor(pscores[key]), torch.tensor(scores[key][:len(pends)]), atol=1e-5), key)

    def test_short_trace_has_no_eligible_window(self):
        ends, scores = score_static(routing(7), self.bank)
        self.assertEqual(ends, [])
        self.assertTrue(all(not values for values in scores.values()))

    def test_invalid_shape_and_expert(self):
        with self.assertRaises(ValueError):
            signatures(torch.zeros(2, 10, 8).long())
        with self.assertRaises(ValueError):
            signatures(torch.full((16, 10, 8), 64).long())


class ExecutionBoundaryTest(unittest.TestCase):
    def test_frozen_content_change_is_rejected(self):
        with patch.object(runner, "read_json", return_value={"hashes": {"some_path": "old"}}):
            with patch.object(runner, "digest", return_value="new"):
                with self.assertRaises(ValueError):
                    runner.verify_freeze()

    def test_write_to_claude_or_historical_results_rejected(self):
        for path in (ROOT / ".claude/test.json", ROOT / "artifacts/agent_v2/codex_sird/result.json"):
            with self.assertRaises(ValueError):
                runner.write_new(path, {})


if __name__ == "__main__":
    unittest.main()
