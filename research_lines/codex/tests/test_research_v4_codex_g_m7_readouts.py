"""Synthetic checks for explicitly post-result M7 descriptions."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
from research_v4.codex_g_m7_readouts import class_controls, resolution, legacy_state


class ReadoutTest(unittest.TestCase):
    def test_keep_all_arms_and_attack_only_scopes_distinct(self):
        m = {"a": {"variant": "attack", "trajectory_class": "over_refusal"},
             "b": {"variant": "clean", "trajectory_class": "over_refusal"}}
        r = class_controls(m, {"a": None, "b": 7})["over_refusal"]
        self.assertEqual(r["all_arms"], {"alarms": 1, "n": 2})
        self.assertEqual(r["attack_arm"], {"alarms": 0, "n": 1})

    def test_resolution_is_not_observation_censoring(self):
        m = {"a": {"variant": "attack", "x": 30, "fold": 0}}
        support = {"0": {"min_possible_p": 1/95}}
        r = resolution(m, {"a": None}, .01, support)
        self.assertEqual(r["0"]["calibration_forced_silent_count"], 1)
        with self.assertRaises(AssertionError): resolution(m, {"a": 15}, .01, support)

    def test_legacy_cut_hits_kept_not_deleted(self):
        m = {"e": 10, "x": 400, "old_h_end": 352}
        self.assertTrue(legacy_state(100, m)); self.assertFalse(legacy_state(9, m))
        self.assertFalse(legacy_state(None, m))
