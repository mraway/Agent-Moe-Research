from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_routing_observation_atlas import (  # noqa: E402
    _all_split_jsd,
    _boundary_change,
    _normalized_center,
    _scalar_pearson,
    _segment_stats,
    _token_index_at_character,
)
from phase_a.routing_analysis import RoutingSequence  # noqa: E402


class RoutingObservationAtlasTest(unittest.TestCase):
    def _change_sequence(self) -> RoutingSequence:
        probabilities = torch.tensor(
            [
                [
                    [1.0, 0.0],
                    [1.0, 0.0],
                    [0.0, 1.0],
                    [0.0, 1.0],
                ]
            ]
        )
        return RoutingSequence(
            token_ids=(1, 2, 3, 4),
            token_texts=("a", "b", "c", "d"),
            probabilities=probabilities,
            top_k_ids=torch.tensor([[[0], [0], [1], [1]]]),
        )

    def test_segment_selection_is_a_probability_distribution(self) -> None:
        sequence = self._change_sequence()
        stats = _segment_stats(
            sequence.probabilities,
            sequence.top_k_ids,
            torch.tensor([True, True, False, False]),
        )

        torch.testing.assert_close(stats.selection.sum(dim=-1), torch.ones(1))
        torch.testing.assert_close(stats.selection, torch.tensor([[1.0, 0.0]]))

    def test_boundary_is_the_split_between_pre_and_post_tokens(self) -> None:
        change = _boundary_change(self._change_sequence(), center=2, width=2)

        self.assertIsNotNone(change)
        assert change is not None
        torch.testing.assert_close(
            change.probability_jsd, torch.tensor([math.log(2.0)])
        )
        torch.testing.assert_close(change.selection_tv, torch.ones(1))
        torch.testing.assert_close(
            change.probability_delta, torch.tensor([[-1.0, 1.0]])
        )

    def test_all_split_jsd_contains_the_only_eligible_split(self) -> None:
        scores = _all_split_jsd(self._change_sequence(), width=2)

        torch.testing.assert_close(scores, torch.tensor([math.log(2.0)]))

    def test_normalized_center_is_not_clamped(self) -> None:
        self.assertEqual(_normalized_center(2, 100, 40), 1)
        self.assertEqual(_normalized_center(50, 100, 40), 20)

    def test_scalar_pearson_handles_paired_and_constant_values(self) -> None:
        self.assertAlmostEqual(_scalar_pearson([1.0, 2.0], [2.0, 4.0]), 1.0)
        self.assertIsNone(_scalar_pearson([1.0, 1.0], [2.0, 3.0]))
        with self.assertRaises(ValueError):
            _scalar_pearson([1.0], [1.0, 2.0])

    def test_character_index_maps_to_token_containing_evidence_start(self) -> None:
        pieces = ["Once", " upon", " a", " time"]

        self.assertEqual(_token_index_at_character(pieces, 0), 0)
        self.assertEqual(_token_index_at_character(pieces, 4), 1)
        self.assertEqual(_token_index_at_character(pieces, 10), 2)
        with self.assertRaises(ValueError):
            _token_index_at_character(pieces, len("".join(pieces)))


if __name__ == "__main__":
    unittest.main()
