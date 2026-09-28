from __future__ import annotations

import sys
import unittest
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from phase_a.normal_manifold import (  # noqa: E402
    age_bin,
    evenly_spaced_positions,
    finite_upper_threshold,
    probability_window_signatures,
    selection_window_signatures,
    shuffled_top_k_ids,
    workflow_family,
)
from run_normal_manifold_p1_knn import _pairwise_distance  # noqa: E402
from run_normal_manifold_p2_pca import (  # noqa: E402
    _condition_means,
    _fit_band,
)
from run_normal_manifold_p3_forecast import (  # noqa: E402
    _cusum,
    _weighted_mean,
    _weighted_quantile_columns,
)


class NormalManifoldTests(unittest.TestCase):
    def test_metadata_conditioning(self) -> None:
        self.assertEqual(workflow_family("knowledge_qa"), "knowledge_qa")
        self.assertEqual(workflow_family("order_status"), "status_only")
        self.assertEqual(
            workflow_family("subscription_and_knowledge"), "status_and_knowledge"
        )
        self.assertEqual(age_bin(15), "8-15")
        self.assertEqual(age_bin(16), "16-31")
        self.assertEqual(age_bin(128), "128+")

    def test_selection_windows_are_normalized_and_causal(self) -> None:
        ids = torch.empty((16, 5, 8), dtype=torch.long)
        for layer in range(16):
            for token in range(5):
                ids[layer, token] = torch.arange(8) + ((layer + token) % 8)
        ends, signatures = selection_window_signatures(ids, 3)
        self.assertEqual(ends.tolist(), [2, 3, 4])
        self.assertEqual(tuple(signatures.shape), (3, 16, 64))
        self.assertTrue(torch.allclose(signatures.sum(dim=-1), torch.ones(3, 16)))
        expected = torch.zeros(64)
        for token in range(3):
            expected.scatter_add_(0, ids[0, token], torch.ones(8))
        expected /= 24.0
        self.assertTrue(torch.allclose(signatures[0, 0], expected))

    def test_probability_windows_are_normalized(self) -> None:
        probabilities = torch.rand(16, 6, 64)
        probabilities /= probabilities.sum(dim=-1, keepdim=True)
        ends, signatures = probability_window_signatures(probabilities, 4)
        self.assertEqual(ends.tolist(), [3, 4, 5])
        self.assertTrue(torch.allclose(signatures.sum(dim=-1), torch.ones(3, 16), atol=1e-6))

    def test_shuffle_preserves_each_layer_load_and_is_deterministic(self) -> None:
        ids = torch.randint(0, 64, (16, 20, 8))
        first = shuffled_top_k_ids(ids, "trace-a")
        second = shuffled_top_k_ids(ids, "trace-a")
        self.assertTrue(torch.equal(first, second))
        for layer in range(16):
            before = torch.bincount(ids[layer].flatten(), minlength=64)
            after = torch.bincount(first[layer].flatten(), minlength=64)
            self.assertTrue(torch.equal(before, after))

    def test_hellinger_distance(self) -> None:
        left = torch.zeros((2, 16, 64))
        right = torch.zeros((2, 16, 64))
        left[0, :, 0] = 1.0
        left[1, :, 1] = 1.0
        right.copy_(left)
        distances = _pairwise_distance(left, right, (tuple(range(5, 11)), tuple(range(11, 16))))
        self.assertTrue(torch.allclose(torch.diag(distances), torch.zeros(2)))
        self.assertTrue(torch.allclose(distances[0, 1], torch.tensor(1.0)))

    def test_finite_threshold_and_even_sampling(self) -> None:
        result = finite_upper_threshold(list(range(9)), alpha=0.20)
        self.assertEqual(result["order_statistic_rank"], 8)
        self.assertEqual(result["threshold"], 7.0)
        self.assertEqual(evenly_spaced_positions(3, 8), (0, 1, 2))
        self.assertEqual(len(evenly_spaced_positions(100, 8)), 8)

    def test_conditional_means_shrink_toward_age_mean(self) -> None:
        features = torch.zeros((3, 16, 64))
        features[0] += 1.0
        features[1] += 3.0
        features[2] += 9.0
        global_mean, age_means, cell_means, counts = _condition_means(
            features,
            ("status_only", "status_only", "knowledge_qa"),
            ("8-15", "8-15", "16-31"),
            True,
        )
        self.assertTrue(torch.allclose(global_mean, torch.full((16, 64), 13.0 / 3.0)))
        self.assertTrue(torch.allclose(age_means["8-15"], torch.full((16, 64), 2.0)))
        self.assertTrue(
            torch.allclose(cell_means["status_only::8-15"], torch.full((16, 64), 2.0))
        )
        self.assertEqual(counts["status_only::8-15"], 2)

    def test_pca_band_has_fixed_rank_and_reconstructs_training_span(self) -> None:
        generator = torch.Generator().manual_seed(7)
        residuals = torch.randn((40, 16, 64), generator=generator)
        band = _fit_band(residuals, tuple(range(5, 11)))
        self.assertEqual(tuple(band.components.shape), (16, 6 * 64))
        self.assertEqual(tuple(band.eigenvalues.shape), (16,))
        identity = band.components @ band.components.T
        self.assertTrue(torch.allclose(identity, torch.eye(16), atol=1e-5))
        self.assertTrue(bool((band.eigenvalues > 0).all()))

    def test_weighted_forecast_statistics(self) -> None:
        values = torch.tensor([[0.0, 10.0], [2.0, 20.0], [8.0, 30.0]])
        weights = torch.tensor([1.0, 2.0, 1.0])
        self.assertTrue(
            torch.allclose(_weighted_mean(values, weights), torch.tensor([3.0, 20.0]))
        )
        median = _weighted_quantile_columns(values, weights, 0.5)
        self.assertTrue(torch.allclose(median, torch.tensor([2.0, 20.0])))

    def test_cusum_resets_and_accumulates_surprise(self) -> None:
        values = _cusum(torch.tensor([0.5, 3.0, 0.0, 2.0]))
        self.assertTrue(torch.allclose(values, torch.tensor([0.0, 2.0, 1.0, 2.0])))


if __name__ == "__main__":
    unittest.main()
