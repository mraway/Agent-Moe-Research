from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_normal_manifold_age_free_ablation import (  # noqa: E402
    _global_robust_standardize,
    _local_density_log_ratio,
    _pair_group_maxima,
    _reference_neighbor_indices,
    _spearman,
)


class AgeFreeNormalManifoldTests(unittest.TestCase):
    def test_reference_neighbors_exclude_entire_source_trace(self) -> None:
        distances = torch.tensor(
            [
                [0.0, 0.1, 0.2, 0.3],
                [0.1, 0.0, 0.4, 0.5],
                [0.2, 0.4, 0.0, 0.6],
                [0.3, 0.5, 0.6, 0.0],
            ]
        )
        trace_ids = ("a", "a", "b", "c")
        indices = _reference_neighbor_indices(distances, trace_ids, k=2)
        for row, trace_id in zip(indices.tolist(), trace_ids, strict=True):
            self.assertTrue(all(trace_ids[index] != trace_id for index in row))

    def test_global_robust_standardization_is_position_free(self) -> None:
        values = torch.tensor([2.0, 3.0])
        reference = torch.tensor([1.0, 2.0, 3.0, 4.0])
        standardized, center, scale = _global_robust_standardize(values, reference)
        self.assertAlmostEqual(center, 2.5)
        self.assertGreater(scale, 0.0)
        self.assertAlmostEqual(float(standardized[0]), -float(standardized[1]), places=6)

    def test_local_density_ratio_compares_with_neighbor_support_radius(self) -> None:
        raw = torch.tensor([2.0, 4.0])
        neighbors = torch.tensor([[0, 1, 2], [1, 2, 3]])
        reference_raw = torch.tensor([1.0, 2.0, 3.0, 4.0])
        ratios = _local_density_log_ratio(raw, neighbors, reference_raw)
        self.assertAlmostEqual(float(ratios[0]), 0.0, places=6)
        self.assertAlmostEqual(float(ratios[1]), math.log(4.0 / 3.0), places=6)

    def test_pair_group_maximum_collapses_correlated_arms(self) -> None:
        rows = _pair_group_maxima(
            ["a-clean", "a-attack", "b-clean"],
            ["a", "a", "b"],
            [1.0, 3.0, 2.0],
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["trace_count"], 2)
        self.assertEqual(rows[0]["maximum"], 3.0)
        self.assertEqual(rows[1]["maximum"], 2.0)

    def test_spearman_uses_average_tie_ranks(self) -> None:
        self.assertAlmostEqual(_spearman([1.0, 2.0, 3.0], [10.0, 20.0, 30.0]), 1.0)
        self.assertAlmostEqual(_spearman([1.0, 2.0, 3.0], [30.0, 20.0, 10.0]), -1.0)
        self.assertIsNone(_spearman([1.0, 1.0], [2.0, 3.0]))


if __name__ == "__main__":
    unittest.main()
