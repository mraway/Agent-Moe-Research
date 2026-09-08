from __future__ import annotations

import sys
import unittest
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_normal_manifold_p1_label_free import (  # noqa: E402
    BaseScores,
    _continuous_age_standardize,
    _cusum,
    _history_streams,
    _rolling_mean,
)
from audit_normal_manifold_p1_label_free import (  # noqa: E402
    _age_band,
    _escaped_newline_suffix,
    _length_band,
    _maximum_run,
)


class LabelFreeNormalManifoldTests(unittest.TestCase):
    def test_continuous_age_standardization_has_no_empirical_ceiling(self) -> None:
        reference_raw = torch.linspace(1.0, 4.0, steps=128)
        reference_ends = torch.arange(7, 135)
        raw = torch.tensor([2.0, 20.0])
        ends = torch.tensor([32, 32])
        median, scale, standardized = _continuous_age_standardize(
            raw,
            ends,
            reference_raw,
            reference_ends,
            local_count=128,
        )
        self.assertTrue(torch.allclose(median[0], median[1]))
        self.assertTrue(torch.allclose(scale[0], scale[1]))
        self.assertGreater(float(standardized[1]), float(standardized[0]) + 5.0)

    def test_age_scale_floor_prevents_zero_local_scale(self) -> None:
        reference_raw = torch.cat((torch.ones(8), torch.arange(2.0, 10.0)))
        reference_ends = torch.arange(7, 23)
        _, scale, standardized = _continuous_age_standardize(
            torch.tensor([2.0]),
            torch.tensor([8]),
            reference_raw,
            reference_ends,
            local_count=8,
            scale_floor_fraction=0.25,
        )
        self.assertGreater(float(scale[0]), 0.0)
        self.assertTrue(torch.isfinite(standardized).all())

    def test_rolling_mean_is_causal_and_endpoint_aligned(self) -> None:
        values, ends = _rolling_mean(
            torch.tensor([1.0, 2.0, 3.0, 4.0]), torch.tensor([7, 8, 9, 10]), 3
        )
        self.assertTrue(torch.allclose(values, torch.tensor([2.0, 3.0])))
        self.assertEqual(ends.tolist(), [9, 10])

    def test_standard_and_leaky_cusum(self) -> None:
        values = torch.tensor([0.0, 2.0, 2.0, -1.0])
        ends = torch.arange(7, 11)
        standard, standard_ends = _cusum(values, ends, allowance=0.5, rho=1.0)
        leaky, leaky_ends = _cusum(values, ends, allowance=0.5, rho=0.5)
        self.assertTrue(torch.allclose(standard, torch.tensor([0.0, 1.5, 3.0, 1.5])))
        self.assertTrue(torch.allclose(leaky, torch.tensor([0.0, 1.5, 2.25, 0.0])))
        self.assertTrue(torch.equal(standard_ends, ends))
        self.assertTrue(torch.equal(leaky_ends, ends))

    def test_history_streams_are_complete(self) -> None:
        base = BaseScores(
            ends=torch.arange(7, 19),
            raw=torch.ones(12),
            local_median=torch.zeros(12),
            local_scale=torch.ones(12),
            standardized=torch.arange(12, dtype=torch.float32),
            neighbor_indices=torch.zeros((12, 5), dtype=torch.long),
        )
        streams = _history_streams(base)
        self.assertEqual(
            set(streams),
            {
                "endpoint_z",
                "rolling_mean_4",
                "rolling_mean_8",
                "cusum_0_5",
                "leaky_cusum_0_95_0_5",
            },
        )
        self.assertEqual(streams["rolling_mean_4"][1].tolist(), list(range(10, 19)))
        self.assertEqual(streams["rolling_mean_8"][1].tolist(), list(range(14, 19)))

    def test_zoom_audit_bins_and_runs(self) -> None:
        self.assertEqual(_age_band(31), "7-31")
        self.assertEqual(_age_band(128), "128+")
        self.assertEqual(_length_band(127), "64-127")
        self.assertEqual(_length_band(192), "192")
        self.assertEqual(_maximum_run([False, True, True, False, True]), 2)

    def test_zoom_audit_locates_literal_escaped_newline_suffix(self) -> None:
        rows = [
            {"token_text": "answer"},
            {"token_text": "\\"},
            {"token_text": "n"},
            {"token_text": "\\n"},
        ]
        result = _escaped_newline_suffix("answer\\n\\n", rows)
        self.assertEqual(result["sequence_count"], 2)
        self.assertEqual(result["token_start"], 1)
        self.assertEqual(result["token_count"], 3)


if __name__ == "__main__":
    unittest.main()
