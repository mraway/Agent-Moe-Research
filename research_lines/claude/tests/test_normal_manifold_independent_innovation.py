from __future__ import annotations

import sys
import unittest
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_normal_manifold_independent_innovation import (  # noqa: E402
    METHOD_ORDER,
    _align_streams,
    _fixed_nonoverlap,
    _leave_one_trace_out_raw,
    _nonoverlap_token_aggregates,
    _phase_values,
    _staggered_pair,
    _token_selection_signatures,
)


def _feature(layer0: tuple[float, float], layer1: tuple[float, float]) -> torch.Tensor:
    return torch.tensor((layer0, layer1), dtype=torch.float32)


class IndependentInnovationTests(unittest.TestCase):
    def test_token_signature_is_normalized_and_has_one_endpoint_per_token(self) -> None:
        top_k = torch.arange(8).reshape(1, 1, 8).repeat(16, 3, 1)
        ends, signatures = _token_selection_signatures(top_k)
        self.assertEqual(ends.tolist(), [0, 1, 2])
        self.assertEqual(tuple(signatures.shape), (3, 16, 64))
        probabilities = signatures.square().sum(dim=2)
        self.assertTrue(torch.allclose(probabilities, torch.ones_like(probabilities)))

    def test_leave_one_trace_out_excludes_every_same_trace_anchor(self) -> None:
        features = torch.stack(
            tuple(_feature((1, 0), (1, 0)) for _ in range(6))
        )
        trace_ids = ("a", "a", "b", "b", "c", "c")
        _, neighbors = _leave_one_trace_out_raw(
            features, trace_ids, neighbor_k=2, bands=((0,), (1,))
        )
        for trace_id, row in zip(trace_ids, neighbors.tolist(), strict=True):
            self.assertTrue(all(trace_ids[index] != trace_id for index in row))

    def test_fixed_nonoverlap_keeps_episode_anchored_lane(self) -> None:
        values = torch.arange(12, dtype=torch.float32)
        ends = torch.arange(7, 19)
        selected, selected_ends = _fixed_nonoverlap(values, ends, width=4)
        self.assertEqual(selected_ends.tolist(), [7, 11, 15])
        self.assertEqual(selected.tolist(), [0.0, 4.0, 8.0])

    def test_staggered_pair_uses_same_lane_disjoint_windows(self) -> None:
        values = torch.tensor([1.0, 9.0, 3.0, 7.0, 5.0])
        ends = torch.tensor([7, 8, 15, 16, 23])
        current, minimum, aligned_ends = _staggered_pair(values, ends, gap=8)
        self.assertEqual(aligned_ends.tolist(), [15, 16, 23])
        self.assertEqual(current.tolist(), [3.0, 7.0, 5.0])
        self.assertEqual(minimum.tolist(), [1.0, 7.0, 3.0])

    def test_nonoverlap_token_aggregates_discard_only_incomplete_suffix(self) -> None:
        values = torch.tensor([0.0, 1.0, 2.0, 3.0, 10.0, 20.0, 30.0, 40.0, 99.0])
        means, q25, ends = _nonoverlap_token_aggregates(values, width=4)
        self.assertEqual(ends.tolist(), [3, 7])
        self.assertEqual(means.tolist(), [1.5, 25.0])
        self.assertAlmostEqual(float(q25[0]), 0.75)
        self.assertAlmostEqual(float(q25[1]), 17.5)

    def test_phase_uses_method_specific_routing_lookback(self) -> None:
        row = {
            "positive": True,
            "evidence_onset": 5,
            "streams": {
                "staggered_state_min2_z": {
                    "endpoints": [4, 5, 19, 20, 21],
                    "scores": [0.0, 1.0, 2.0, 3.0, 4.0],
                }
            },
        }
        phases = _phase_values(row, "staggered_state_min2_z")
        self.assertEqual(phases["drift_pre_onset"], [0.0])
        self.assertEqual(phases["drift_mixed_transition"], [1.0, 2.0])
        self.assertEqual(phases["drift_fully_post_onset"], [3.0, 4.0])

    def test_align_streams_uses_only_common_causal_endpoints(self) -> None:
        row = {
            "streams": {
                "sliding_state_z": {
                    "endpoints": [7, 8, 9, 10],
                    "scores": [1.0, 2.0, 3.0, 4.0],
                },
                "token_endpoint_z": {
                    "endpoints": [0, 7, 9, 11],
                    "scores": [50.0, 10.0, 30.0, 60.0],
                },
            }
        }
        baseline, token = _align_streams(
            row, "sliding_state_z", "token_endpoint_z"
        )
        self.assertEqual(baseline, [1.0, 3.0])
        self.assertEqual(token, [10.0, 30.0])

    def test_method_matrix_is_frozen_and_complete(self) -> None:
        self.assertEqual(
            METHOD_ORDER,
            (
                "sliding_state_z",
                "nonoverlap_state_z",
                "staggered_state_current_z",
                "staggered_state_min2_z",
                "token_endpoint_z",
                "nonoverlap_token_mean8_z",
                "nonoverlap_token_q25_8_z",
            ),
        )


if __name__ == "__main__":
    unittest.main()
