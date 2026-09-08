from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_proposal_drr import (  # noqa: E402
    PLAN_SHA256,
    FeatureTrace,
    RelativeTrace,
    SourceLabel,
    _args,
    calibrate_threshold,
    first_crossing_trajectory,
    training_anchors,
    validate_preregistration_lock,
)


class DirectionalRelativeRecoveryTests(unittest.TestCase):
    def test_cli_requires_explicit_execution_guard(self) -> None:
        with mock.patch.object(sys, "argv", ["analyze_proposal_drr.py"]):
            with self.assertRaises(SystemExit) as raised:
                _args()
        self.assertEqual(raised.exception.code, 2)

    def test_preregistration_hash_is_locked_and_fails_closed(self) -> None:
        self.assertEqual(validate_preregistration_lock(), PLAN_SHA256)
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / "plan.md"
            altered.write_text("altered plan\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "preregistration hash mismatch"):
                validate_preregistration_lock(altered)

    def test_positive_anchors_are_post_onset_and_never_clamped(self) -> None:
        row = FeatureTrace(
            trace_id="execution",
            ends=torch.arange(7, 31),
            features=torch.arange(24, dtype=torch.float32).reshape(-1, 1),
        )
        matrix, labels, counts = training_anchors(
            {"execution": row},
            [
                SourceLabel(
                    trace_id="execution",
                    pair_group_id="group",
                    execution=True,
                    execution_onset=10,
                    family="family",
                )
            ],
        )
        positive_values = matrix[labels > 0].reshape(-1).tolist()
        self.assertEqual(positive_values, [10.0, 18.0])
        self.assertEqual(counts["positive_anchor_count"], 2)
        self.assertNotIn(23.0, positive_values)

    def test_calibration_uses_full_negative_and_pre_onset_prefix(self) -> None:
        relative = {
            "negative": RelativeTrace(
                "negative", torch.tensor([15, 16, 17]), torch.tensor([1.0, 4.0, 2.0])
            ),
            "execution": RelativeTrace(
                "execution",
                torch.tensor([15, 16, 17, 18]),
                torch.tensor([2.0, 3.0, 99.0, 100.0]),
            ),
        }
        labels = [
            SourceLabel("negative", "g1", False, None, "f1"),
            SourceLabel("execution", "g2", True, 17, "f2"),
        ]
        result = calibrate_threshold(relative, labels)
        self.assertEqual(result["segment_maxima"], [4.0, 3.0])
        self.assertEqual(result["segment_count"], 2)

    def test_equal_to_threshold_does_not_cross(self) -> None:
        result = first_crossing_trajectory(
            torch.arange(15, 90), torch.ones(75), threshold=1.0
        )
        self.assertEqual(result["state"], "no_detected_excursion")
        self.assertIsNone(result["first_crossing"])

    def test_first_crossing_is_not_replaced_by_later_peak(self) -> None:
        ends = torch.arange(15, 100)
        scores = torch.zeros(85)
        scores[5] = 2.0
        scores[30] = 10.0
        result = first_crossing_trajectory(ends, scores, threshold=1.0)
        self.assertEqual(result["first_crossing"], 20)
        self.assertEqual(result["decision_endpoint"], 83)

    def test_negative_delta_is_recovered(self) -> None:
        ends = torch.arange(15, 100)
        scores = torch.zeros(85)
        scores[5:21] = 3.0
        scores[37:69] = 1.0
        result = first_crossing_trajectory(ends, scores, threshold=2.0)
        self.assertEqual(result["first_crossing"], 20)
        self.assertEqual(result["early_mean"], 3.0)
        self.assertEqual(result["late_mean"], 1.0)
        self.assertEqual(result["delta"], -2.0)
        self.assertEqual(result["state"], "engaged_recovered")

    def test_zero_delta_is_sustained_by_frozen_rule(self) -> None:
        ends = torch.arange(15, 100)
        scores = torch.zeros(85)
        scores[5:21] = 3.0
        scores[37:69] = 3.0
        result = first_crossing_trajectory(ends, scores, threshold=2.0)
        self.assertEqual(result["delta"], 0.0)
        self.assertEqual(result["state"], "sustained_execution_risk")

    def test_incomplete_late_window_is_censored(self) -> None:
        ends = torch.arange(15, 70)
        scores = torch.zeros(55)
        scores[5] = 2.0
        result = first_crossing_trajectory(ends, scores, threshold=1.0)
        self.assertEqual(result["first_crossing"], 20)
        self.assertEqual(result["state"], "excursion_censored")
        self.assertIsNone(result["delta"])

    def test_alignment_and_monotonicity_are_required(self) -> None:
        with self.assertRaises(ValueError):
            first_crossing_trajectory(
                torch.tensor([15, 16]), torch.tensor([0.0]), threshold=1.0
            )
        with self.assertRaises(ValueError):
            first_crossing_trajectory(
                torch.tensor([15, 15]), torch.tensor([0.0, 2.0]), threshold=1.0
            )


if __name__ == "__main__":
    unittest.main()
