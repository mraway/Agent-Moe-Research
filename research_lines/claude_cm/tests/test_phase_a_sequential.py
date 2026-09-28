from __future__ import annotations

import sys
import unittest
import math
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase_a.routing_analysis import RoutingSequence
from phase_a.sequential import (
    adjacent_block_selection_jsd,
    causal_history_median_difference,
    evenly_spaced_indices,
    finite_sample_upper_threshold,
    persistent_scores,
    window_end_indices,
    window_features,
)


class PhaseASequentialTest(unittest.TestCase):
    def _sequence(self) -> RoutingSequence:
        probabilities = torch.tensor(
            [
                [
                    [0.7, 0.2, 0.1],
                    [0.1, 0.7, 0.2],
                    [0.2, 0.1, 0.7],
                ]
            ]
        )
        top_k_ids = torch.tensor([[[0, 1], [1, 2], [2, 0]]])
        return RoutingSequence(
            token_ids=(11, 12, 13),
            token_texts=("a", "b", "c"),
            probabilities=probabilities,
            top_k_ids=top_k_ids,
        )

    def test_window_end_indices_are_inclusive(self) -> None:
        self.assertEqual(window_end_indices(5, 3).tolist(), [2, 3, 4])
        self.assertEqual(window_end_indices(2, 3).tolist(), [])

    def test_route_selection_windows_count_top_k_membership(self) -> None:
        ends, features = window_features(self._sequence(), "route_selection", 2)
        self.assertEqual(ends.tolist(), [1, 2])
        torch.testing.assert_close(
            features,
            torch.tensor([[0.5, 1.0, 0.5], [0.5, 0.5, 1.0]]),
        )

    def test_route_window_longer_than_sequence_has_no_alarm_opportunity(self) -> None:
        ends, features = window_features(self._sequence(), "route_selection", 4)
        self.assertEqual(ends.tolist(), [])
        self.assertEqual(tuple(features.shape), (0, 3))

    def test_route_probability_windows_are_causal_means(self) -> None:
        ends, features = window_features(self._sequence(), "route_probability", 2)
        self.assertEqual(ends.tolist(), [1, 2])
        torch.testing.assert_close(
            features,
            torch.tensor([[0.4, 0.45, 0.15], [0.15, 0.4, 0.45]]),
        )

    def test_token_hash_windows_are_unit_normalized(self) -> None:
        ends, features = window_features(
            self._sequence(), "token_hash", 2, token_hash_dimension=32
        )
        self.assertEqual(ends.tolist(), [1, 2])
        torch.testing.assert_close(features.norm(dim=1), torch.ones(2))

    def test_persistent_score_requires_every_score_in_run(self) -> None:
        values, ends = persistent_scores(
            torch.tensor([0.8, 0.3, 0.7, 0.9]),
            torch.tensor([7, 8, 9, 10]),
            2,
        )
        torch.testing.assert_close(values, torch.tensor([0.3, 0.3, 0.7]))
        self.assertEqual(ends.tolist(), [8, 9, 10])

    def test_evenly_spaced_indices_are_unique_and_inclusive(self) -> None:
        self.assertEqual(evenly_spaced_indices(3, 9, 3), (3, 6, 9))
        self.assertEqual(evenly_spaced_indices(4, 4, 3), (4,))
        self.assertEqual(evenly_spaced_indices(5, 4, 3), ())

    def test_adjacent_block_selection_jsd_is_causal_and_aligned(self) -> None:
        sequence = self._sequence()
        ends, scores = adjacent_block_selection_jsd(sequence, width=1)

        self.assertEqual(ends.tolist(), [1, 2])
        self.assertEqual(tuple(scores.shape), (2,))

        changed_future = RoutingSequence(
            token_ids=sequence.token_ids + (99,),
            token_texts=sequence.token_texts + ("future",),
            probabilities=torch.cat(
                (sequence.probabilities, sequence.probabilities[:, :1]), dim=1
            ),
            top_k_ids=torch.cat(
                (sequence.top_k_ids, sequence.top_k_ids[:, :1]), dim=1
            ),
        )
        future_ends, future_scores = adjacent_block_selection_jsd(
            changed_future, width=1
        )
        self.assertEqual(future_ends.tolist(), [1, 2, 3])
        torch.testing.assert_close(scores, future_scores[:2])

    def test_adjacent_block_selection_jsd_detects_distribution_change(self) -> None:
        sequence = RoutingSequence(
            token_ids=(1, 2, 3, 4),
            token_texts=("a", "b", "c", "d"),
            probabilities=torch.full((1, 4, 2), 0.5),
            top_k_ids=torch.tensor([[[0], [0], [1], [1]]]),
        )

        ends, scores = adjacent_block_selection_jsd(sequence, width=2)

        self.assertEqual(ends.tolist(), [3])
        torch.testing.assert_close(scores, torch.tensor([math.log(2.0)]))

    def test_finite_sample_upper_threshold_uses_declared_order_statistic(self) -> None:
        values = torch.arange(1, 11, dtype=torch.float32)

        threshold = finite_sample_upper_threshold(values, alpha=0.20)

        self.assertEqual(threshold, 9.0)

    def test_causal_history_median_difference_respects_gap(self) -> None:
        relative, ends = causal_history_median_difference(
            torch.tensor([1.0, 2.0, 4.0, 8.0]),
            torch.tensor([7, 8, 9, 10]),
            gap=2,
        )

        self.assertEqual(ends.tolist(), [9, 10])
        torch.testing.assert_close(
            relative, torch.tensor([3.0, 6.5], dtype=torch.float64)
        )

    def test_causal_history_median_difference_rejects_future_reordering(self) -> None:
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            causal_history_median_difference(
                torch.tensor([1.0, 2.0]), torch.tensor([8, 7]), gap=1
            )


if __name__ == "__main__":
    unittest.main()
