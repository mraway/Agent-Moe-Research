from __future__ import annotations

import sys
import unittest
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_agent_v2_horizon384_mechanism_robustness import (  # noqa: E402
    contrast,
    fixed_block_delta,
    probability_jsd_delta,
)


class Horizon384MechanismRobustnessTests(unittest.TestCase):
    def test_fixed_block_delta_uses_only_endpoints_inside_windows(self) -> None:
        row = {
            "streams": {
                "nonoverlap_token_mean8_z": {
                    "endpoints": [7, 15, 23, 31, 39, 47, 55, 63, 71],
                    "scores": [9.0, 1.0, 3.0, 9.0, 9.0, 5.0, 7.0, 9.0, 11.0],
                }
            }
        }
        result = fixed_block_delta(row, 10)
        assert result is not None
        self.assertEqual(result["early_block_count"], 2)
        self.assertEqual(result["late_block_count"], 4)
        self.assertAlmostEqual(result["early_mean"], 2.0)
        self.assertAlmostEqual(result["late_mean"], 8.0)
        self.assertAlmostEqual(result["continuation_delta"], 6.0)

    def test_fixed_block_delta_requires_both_windows(self) -> None:
        row = {
            "streams": {
                "nonoverlap_token_mean8_z": {
                    "endpoints": [7, 15],
                    "scores": [1.0, 2.0],
                }
            }
        }
        self.assertIsNone(fixed_block_delta(row, 0))

    def test_contrast_keeps_positive_class_as_execution(self) -> None:
        result = contrast([2.0, 3.0], [-2.0, -1.0])
        self.assertEqual(result["ranking"]["auroc"], 1.0)
        self.assertGreater(
            result["execution_minus_bounded_bootstrap"]["mean_contrast"], 0.0
        )

    def test_probability_jsd_delta_is_zero_for_stationary_profile(self) -> None:
        profile = torch.tensor([[0.25, 0.75], [0.6, 0.4]])
        probabilities = profile[:, None, :].expand(2, 80, 2).clone()
        result = probability_jsd_delta(probabilities, profile, 4)
        assert result is not None
        self.assertAlmostEqual(result["early_mean_jsd"], 0.0)
        self.assertAlmostEqual(result["late_mean_jsd"], 0.0)
        self.assertAlmostEqual(result["continuation_delta"], 0.0)

    def test_probability_jsd_delta_requires_full_horizon(self) -> None:
        profile = torch.tensor([[0.5, 0.5]])
        probabilities = profile[:, None, :].expand(1, 63, 2).clone()
        self.assertIsNone(probability_jsd_delta(probabilities, profile, 0))


if __name__ == "__main__":
    unittest.main()
