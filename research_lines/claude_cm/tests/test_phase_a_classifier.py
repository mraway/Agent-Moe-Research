from __future__ import annotations

import sys
import unittest
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase_a.classifier import (  # noqa: E402
    average_precision,
    balanced_accuracy_threshold,
    binary_auroc,
    classification_metrics,
    extract_features,
    fit_normal_centroid_classifier,
    fit_ridge_classifier,
    leave_one_group_out_scores,
    prefix_sequence,
)
from phase_a.routing_analysis import RoutingSequence  # noqa: E402


class PhaseAClassifierTest(unittest.TestCase):
    def _sequence(self) -> RoutingSequence:
        probabilities = torch.tensor(
            [
                [
                    [0.8, 0.2, 0.0],
                    [0.1, 0.7, 0.2],
                    [0.2, 0.1, 0.7],
                ]
            ]
        )
        return RoutingSequence(
            token_ids=(11, 12, 13),
            token_texts=("a", "b", "c"),
            probabilities=probabilities,
            top_k_ids=probabilities.topk(2, dim=-1).indices,
        )

    def test_prefix_caps_at_available_tokens(self) -> None:
        sequence = self._sequence()

        short = prefix_sequence(sequence, 2)
        capped = prefix_sequence(sequence, 10)

        self.assertEqual(short.token_ids, (11, 12))
        self.assertEqual(capped.token_ids, sequence.token_ids)

    def test_feature_families_have_expected_shape(self) -> None:
        sequence = self._sequence()

        probability = extract_features(sequence, "route_probability")
        selection = extract_features(sequence, "route_selection")
        token_hash = extract_features(sequence, "token_hash", token_hash_dimension=16)
        length = extract_features(sequence, "length")

        self.assertEqual(probability.shape, (3,))
        self.assertEqual(selection.shape, (3,))
        self.assertEqual(token_hash.shape, (16,))
        self.assertAlmostEqual(float(token_hash.norm().item()), 1.0, places=6)
        self.assertTrue(torch.equal(length, torch.tensor([3.0])))

    def test_ridge_and_centroid_score_separated_examples(self) -> None:
        features = torch.tensor(
            [[0.0, 1.0], [0.1, 0.9], [1.0, 0.0], [0.9, 0.1]]
        )
        labels = torch.tensor([False, False, True, True])

        ridge = fit_ridge_classifier(features, labels.float() * 2.0 - 1.0)
        centroid = fit_normal_centroid_classifier(features, labels)

        ridge_scores = ridge.score(features)
        centroid_scores = centroid.score(features)
        self.assertGreater(
            float(ridge_scores[labels].mean()), float(ridge_scores[~labels].mean())
        )
        self.assertGreater(
            float(centroid_scores[labels].mean()),
            float(centroid_scores[~labels].mean()),
        )

    def test_leave_one_group_out_keeps_observation_order(self) -> None:
        features = torch.tensor(
            [[0.0], [0.1], [1.0], [0.0], [0.2], [1.1], [0.0], [0.1], [0.9]]
        )
        labels = torch.tensor(
            [False, False, True, False, False, True, False, False, True]
        )
        groups = ["a", "a", "a", "b", "b", "b", "c", "c", "c"]

        scores = leave_one_group_out_scores(
            features, labels, groups, classifier="ridge"
        )

        self.assertEqual(scores.shape, (9,))
        self.assertEqual(binary_auroc(scores, labels), 1.0)

    def test_metrics_handle_ties_and_matched_groups(self) -> None:
        scores = torch.tensor([0.0, 0.0, 0.0, 0.1, 0.2, 0.3])
        labels = torch.tensor([False, False, True, False, False, True])
        groups = ["a", "a", "a", "b", "b", "b"]

        metrics = classification_metrics(scores, labels, groups)

        self.assertAlmostEqual(binary_auroc(scores[:3], labels[:3]), 0.5)
        self.assertAlmostEqual(average_precision(scores[:3], labels[:3]), 1.0 / 3.0)
        self.assertEqual(metrics["group_top1_count"], 1)
        self.assertAlmostEqual(metrics["mean_group_margin"], 0.05, places=6)

    def test_threshold_tie_break_prefers_no_alerts_for_constant_scores(self) -> None:
        scores = torch.zeros(6)
        labels = torch.tensor([False, False, True, False, False, True])

        result = balanced_accuracy_threshold(scores, labels)

        self.assertGreater(result["threshold"], 0.0)
        self.assertAlmostEqual(result["balanced_accuracy"], 0.5)


if __name__ == "__main__":
    unittest.main()
