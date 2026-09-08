from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_agent_v2_engagement_mechanism import (  # noqa: E402
    _window,
    bootstrap_mean_contrast,
    continuation_delta,
)


def _row(scores: list[float]) -> dict:
    return {
        "streams": {
            "token_endpoint_z": {
                "endpoints": list(range(len(scores))),
                "scores": scores,
            }
        }
    }


class EngagementMechanismTests(unittest.TestCase):
    def test_window_requires_every_closed_interval_endpoint(self) -> None:
        row = _row([float(value) for value in range(10)])
        self.assertEqual(_window(row, 2, 5), [2.0, 3.0, 4.0, 5.0])
        self.assertEqual(_window(row, 8, 12), [])

    def test_continuation_delta_uses_frozen_windows(self) -> None:
        scores = [0.0] * 80
        for index in range(10, 26):
            scores[index] = 3.0
        for index in range(42, 74):
            scores[index] = 1.0
        result = continuation_delta(_row(scores), 10)
        assert result is not None
        self.assertEqual(result["early_mean"], 3.0)
        self.assertEqual(result["late_mean"], 1.0)
        self.assertEqual(result["continuation_delta"], -2.0)

    def test_continuation_delta_rejects_censored_late_window(self) -> None:
        self.assertIsNone(continuation_delta(_row([0.0] * 50), 10))

    def test_bootstrap_contrast_is_deterministic_and_directional(self) -> None:
        first = bootstrap_mean_contrast([2.0, 3.0, 4.0], [-2.0, -1.0, 0.0])
        second = bootstrap_mean_contrast([2.0, 3.0, 4.0], [-2.0, -1.0, 0.0])
        self.assertEqual(first, second)
        self.assertGreater(first["mean_contrast"], 0.0)
        self.assertGreater(first["ci95"][0], 0.0)


if __name__ == "__main__":
    unittest.main()
