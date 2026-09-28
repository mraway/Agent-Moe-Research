from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase_a.routing_analysis import (  # noqa: E402
    RoutingSequence,
    compare_aligned_token_routes,
    compare_routing_profiles,
    concatenate_routing_sequences,
    contiguous_rolling_max,
    find_unique_text_token_indices,
    jensen_shannon_divergence,
    matched_prefix_diagnostics,
    rolling_max,
    score_sequence,
)


class PhaseARoutingAnalysisTest(unittest.TestCase):
    def test_jsd_is_zero_for_equal_distributions_and_symmetric(self) -> None:
        p = torch.tensor([[0.8, 0.2], [0.1, 0.9]])
        q = torch.tensor([[0.8, 0.2], [0.7, 0.3]])

        self.assertTrue(torch.equal(jensen_shannon_divergence(p, p), torch.zeros(2)))
        self.assertTrue(
            torch.allclose(
                jensen_shannon_divergence(p, q),
                jensen_shannon_divergence(q, p),
            )
        )

    def test_jsd_for_disjoint_events_is_log_two(self) -> None:
        p = torch.tensor([1.0, 0.0])
        q = torch.tensor([0.0, 1.0])

        value = float(jensen_shannon_divergence(p, q).item())

        self.assertAlmostEqual(value, math.log(2.0), places=6)

    def test_rolling_max_uses_fixed_width_and_half_open_span(self) -> None:
        result = rolling_max(torch.tensor([0.0, 1.0, 3.0, 1.0]), width=2)

        self.assertEqual(result["start"], 1)
        self.assertEqual(result["end"], 3)
        self.assertAlmostEqual(result["value"], 2.0)

    def test_contiguous_rolling_max_rejects_windows_with_gaps(self) -> None:
        scores = torch.tensor([1.0, 10.0, 10.0, 2.0, 4.0, 6.0])
        eligible = torch.tensor([True, False, True, True, True, True])

        result = contiguous_rolling_max(scores, eligible, width=3)

        assert result is not None
        self.assertEqual(result["start"], 2)
        self.assertEqual(result["end"], 5)
        self.assertAlmostEqual(result["value"], 16.0 / 3.0, places=6)

    def test_sequence_score_finds_anomalous_window(self) -> None:
        layers, tokens, experts = 16, 10, 3
        probabilities = torch.zeros(layers, tokens, experts)
        probabilities[..., 0] = 1.0
        probabilities[:, 4:6, :] = torch.tensor([0.0, 1.0, 0.0])
        top_k_ids = probabilities.topk(2, dim=-1).indices
        sequence = RoutingSequence(
            token_ids=tuple(range(tokens)),
            token_texts=tuple(str(index) for index in range(tokens)),
            probabilities=probabilities,
            top_k_ids=top_k_ids,
        )
        profile = torch.zeros(layers, experts)
        profile[..., 0] = 1.0

        result = score_sequence(sequence, profile, window_width=2)

        self.assertEqual(result["max_window_jsd_w8"]["start"], 4)
        self.assertEqual(result["max_window_jsd_w8"]["end"], 6)
        self.assertAlmostEqual(
            result["max_window_jsd_w8"]["value"], math.log(2.0), places=6
        )

    def test_matched_prefix_stops_before_first_different_token(self) -> None:
        left_probabilities = torch.tensor(
            [[[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]]]
        )
        right_probabilities = torch.tensor(
            [[[1.0, 0.0], [0.0, 1.0], [1.0, 0.0]]]
        )
        left = RoutingSequence(
            token_ids=(1, 2, 3),
            token_texts=("a", "b", "c"),
            probabilities=left_probabilities,
            top_k_ids=left_probabilities.topk(1, dim=-1).indices,
        )
        right = RoutingSequence(
            token_ids=(1, 2, 4),
            token_texts=("a", "b", "d"),
            probabilities=right_probabilities,
            top_k_ids=right_probabilities.topk(1, dim=-1).indices,
        )

        result = matched_prefix_diagnostics(left, right)

        self.assertEqual(result["shared_prefix_tokens"], 2)
        self.assertEqual(result["shared_prefix_text"], "ab")
        self.assertAlmostEqual(result["mean_jsd"], math.log(2.0) / 2, places=6)
        self.assertEqual(result["max_token_index"], 1)

    def test_text_span_locator_includes_overlapping_edge_tokens(self) -> None:
        probabilities = torch.full((1, 3, 2), 0.5)
        sequence = RoutingSequence(
            token_ids=(1, 2, 3),
            token_texts=("abc", "def", "ghi"),
            probabilities=probabilities,
            top_k_ids=probabilities.topk(1, dim=-1).indices,
        )

        indices = find_unique_text_token_indices(sequence, "cdefg")

        self.assertEqual(indices, (0, 1, 2))

    def test_aligned_route_comparison_stops_at_token_mismatch(self) -> None:
        left_probabilities = torch.tensor(
            [[[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]]]
        )
        right_probabilities = torch.tensor(
            [[[1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]]
        )
        left = RoutingSequence(
            token_ids=(1, 2, 3),
            token_texts=("a", "b", "c"),
            probabilities=left_probabilities,
            top_k_ids=left_probabilities.topk(1, dim=-1).indices,
        )
        right = RoutingSequence(
            token_ids=(1, 2, 4),
            token_texts=("a", "b", "d"),
            probabilities=right_probabilities,
            top_k_ids=right_probabilities.topk(1, dim=-1).indices,
        )

        result = compare_aligned_token_routes(left, right)

        self.assertEqual(result["aligned_tokens"], 2)
        self.assertAlmostEqual(result["mean_token_layer_jsd"], math.log(2.0) / 2, places=6)
        self.assertAlmostEqual(result["mean_actual_top8_overlap"], 0.5)

    def test_concatenate_routing_sequences_preserves_token_order(self) -> None:
        probabilities = torch.full((1, 1, 2), 0.5)
        first = RoutingSequence(
            token_ids=(1,),
            token_texts=("a",),
            probabilities=probabilities,
            top_k_ids=probabilities.topk(1, dim=-1).indices,
        )
        second = RoutingSequence(
            token_ids=(2,),
            token_texts=("b",),
            probabilities=probabilities,
            top_k_ids=probabilities.topk(1, dim=-1).indices,
        )

        combined = concatenate_routing_sequences((first, second))

        self.assertEqual(combined.token_ids, (1, 2))
        self.assertEqual(combined.token_texts, ("a", "b"))
        self.assertEqual(combined.probabilities.shape, (1, 2, 2))

    def test_profile_comparison_reports_disjoint_expert_use(self) -> None:
        left_probabilities = torch.tensor([[[1.0, 0.0], [1.0, 0.0]]])
        right_probabilities = torch.tensor([[[0.0, 1.0], [0.0, 1.0]]])
        left = RoutingSequence(
            token_ids=(1, 2),
            token_texts=("a", "b"),
            probabilities=left_probabilities,
            top_k_ids=left_probabilities.topk(1, dim=-1).indices,
        )
        right = RoutingSequence(
            token_ids=(3, 4),
            token_texts=("c", "d"),
            probabilities=right_probabilities,
            top_k_ids=right_probabilities.topk(1, dim=-1).indices,
        )

        result = compare_routing_profiles(left, right)

        self.assertAlmostEqual(result["mean_layer_centroid_jsd"], math.log(2.0), places=6)
        self.assertEqual(result["mean_probability_top8_overlap"], 0.0)
        self.assertEqual(result["mean_frequent_top8_overlap"], 0.0)


if __name__ == "__main__":
    unittest.main()
