from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_normal_manifold_time_uniform_calibration import (  # noqa: E402
    _group_alarm_rows,
    build_risk_shape,
    calibrate_group_threshold,
    normalize_stream,
    risk_bin,
    wilson_interval,
)


def _row(group: str, arm: str, scores: list[float]) -> dict:
    return {
        "trace_id": f"{group}--{arm}",
        "pair_group_id": group,
        "batch": "test",
        "arm": arm,
        "positive": False,
        "streams": {"token_endpoint_z": {"scores": scores}},
    }


class TimeUniformCalibrationTests(unittest.TestCase):
    def test_risk_bins_have_frozen_closed_boundaries(self) -> None:
        expected = {
            1: "1-8",
            8: "1-8",
            9: "9-16",
            16: "9-16",
            17: "17-32",
            32: "17-32",
            33: "33-64",
            64: "33-64",
            65: "65-128",
            128: "65-128",
            129: "129+",
            500: "129+",
        }
        self.assertEqual({step: risk_bin(step) for step in expected}, expected)
        with self.assertRaises(ValueError):
            risk_bin(0)

    def test_risk_shape_gives_each_group_one_q90_per_bin(self) -> None:
        rows = [
            _row("a", "clean", [0.0] * 8),
            _row("a", "benign_control", [0.0] * 8),
            _row("b", "clean", [10.0]),
            _row("c", "clean", [20.0]),
        ]
        shape = build_risk_shape(rows, "token_endpoint_z")
        first = shape["bins"]["1-8"]
        self.assertEqual(first["group_count"], 3)
        self.assertEqual(first["center"], 10.0)
        self.assertFalse(shape["bins"]["9-16"]["applicable"])

    def test_normalization_uses_risk_bin_location_and_scale(self) -> None:
        shape = {
            "bins": {
                "1-8": {"applicable": True, "center": 1.0, "scale": 2.0},
                "9-16": {"applicable": True, "center": 3.0, "scale": 4.0},
            }
        }
        values = normalize_stream([1.0] * 8 + [7.0], shape)
        self.assertEqual(values[:8], [0.0] * 8)
        self.assertEqual(values[8], 1.0)

    def test_group_threshold_uses_rank_91_of_100_group_maxima(self) -> None:
        rows = []
        for index in range(100):
            rows.extend(
                (
                    _row(f"g{index:03d}", "clean", [float(index)]),
                    _row(f"g{index:03d}", "benign_control", [float(index) - 0.5]),
                )
            )
        calibration = calibrate_group_threshold(
            rows, lambda row: row["streams"]["token_endpoint_z"]["scores"]
        )
        self.assertEqual(calibration["trace_count"], 100)
        self.assertEqual(calibration["order_statistic_rank"], 91)
        self.assertEqual(calibration["threshold"], 90.0)
        self.assertEqual(calibration["strict_exceedance_count"], 9)

    def test_pair_group_false_alarm_is_union_of_two_arms(self) -> None:
        shared = {
            "fold": 3,
            "workflow": "order_status",
            "workflow_family": "status_only",
            "benign_family": "family",
            "planned_channel": "direct_user",
            "eligible_endpoint_count": 20,
        }
        rows = [
            {
                **shared,
                "trace_id": "x--clean",
                "pair_group_id": "x",
                "arm": "clean",
                "false_alarm": False,
                "maximum": 1.0,
                "first_alarm_risk_step": None,
            },
            {
                **shared,
                "trace_id": "x--benign",
                "pair_group_id": "x",
                "arm": "benign_control",
                "false_alarm": True,
                "maximum": 2.0,
                "first_alarm_risk_step": 7,
            },
        ]
        groups = _group_alarm_rows(rows)
        self.assertEqual(len(groups), 1)
        self.assertTrue(groups[0]["false_alarm"])
        self.assertEqual(groups[0]["first_alarm_risk_step"], 7)

    def test_wilson_interval_handles_zero_events(self) -> None:
        interval = wilson_interval(0, 60)
        self.assertEqual(interval["rate"], 0.0)
        self.assertEqual(interval["lower"], 0.0)
        self.assertGreater(interval["upper"], 0.0)
        self.assertLess(interval["upper"], 0.10)


if __name__ == "__main__":
    unittest.main()
