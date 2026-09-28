"""Boundary checks for the descriptive M3 export audit; synthetic arrays only."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from research_v4.codex_g_m3_audit import first, reason


class AuditBoundaryTest(unittest.TestCase):
    def test_first_alarm_is_inclusive_and_chronological(self):
        self.assertEqual(first([7, 8, 9], [.2, .1, .01], .1), 8)
        self.assertIsNone(first([7, 8], [.2, .11], .1))
        self.assertIsNone(first([], [], .1))

    def test_pre_E_is_a_miss_even_when_future_alarms_would_hit(self):
        alarm = first([7, 10, 12], [.01, .01, .01], .1)
        self.assertEqual(reason(alarm, 10, 12, [7, 10, 12]), "pre_E")

    def test_both_hit_boundaries_are_closed(self):
        self.assertEqual(reason(10, 10, 12, [7, 10, 12, 28]), "hit")
        self.assertEqual(reason(28, 10, 12, [7, 10, 12, 28]), "hit")
        self.assertEqual(reason(29, 10, 12, [7, 10, 12, 28, 29]), "late")

    def test_horizon_before_X_does_not_remove_a_reachable_window(self):
        self.assertEqual(reason(12, 10, 300, [7, 10, 12]), "hit")
        self.assertEqual(reason(None, 10, 300, [7, 10, 12]), "no_alarm")

    def test_no_endpoint_in_window_is_unreachable(self):
        self.assertEqual(reason(7, 10, 300, [7]), "unreachable")
        self.assertEqual(reason(None, 10, 12, [7, 29]), "unreachable")


if __name__ == "__main__":
    unittest.main()
