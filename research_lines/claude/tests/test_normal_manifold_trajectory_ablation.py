from __future__ import annotations

import sys
import unittest
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_normal_manifold_age_free_ablation import _pair_group_maxima  # noqa: E402
from run_normal_manifold_trajectory_ablation import (  # noqa: E402
    _align_minimum,
    _conditional_successor_raw,
    _leave_one_trace_out_transition_scores,
    _phase_values,
    _risk_step_curve,
    _rolling_quantile,
)


def _feature(layer0: tuple[float, float], layer1: tuple[float, float]) -> torch.Tensor:
    return torch.tensor((layer0, layer1), dtype=torch.float32)


class TrajectoryAblationTests(unittest.TestCase):
    def test_conditional_successor_uses_predecessor_gate(self) -> None:
        query_pre = torch.stack((_feature((1, 0), (1, 0)),))
        query_next = torch.stack((_feature((0, 1), (0, 1)),))
        reference_pre = torch.stack(
            (
                _feature((1, 0), (1, 0)),
                _feature((0.99, 0.01), (0.99, 0.01)),
                _feature((0, 1), (0, 1)),
            )
        )
        reference_next = torch.stack(
            (
                _feature((0, 1), (0, 1)),
                _feature((1, 0), (1, 0)),
                _feature((0, 1), (0, 1)),
            )
        )
        raw, candidates = _conditional_successor_raw(
            query_pre,
            query_next,
            reference_pre,
            reference_next,
            candidate_count=2,
            neighbor_k=1,
            bands=((0,), (1,)),
        )
        self.assertEqual(set(candidates[0].tolist()), {0, 1})
        self.assertAlmostEqual(float(raw[0]), 0.0, places=6)

    def test_leave_one_trace_out_transition_excludes_all_same_trace_edges(self) -> None:
        features = torch.stack(
            tuple(_feature((1, 0), (1, 0)) for _ in range(6))
        )
        trace_ids = ("a", "a", "b", "b", "c", "c")
        _, candidates = _leave_one_trace_out_transition_scores(
            features,
            features,
            trace_ids,
            candidate_count=2,
            neighbor_k=1,
            bands=((0,), (1,)),
        )
        for trace_id, row in zip(trace_ids, candidates.tolist(), strict=True):
            self.assertTrue(all(trace_ids[index] != trace_id for index in row))

    def test_rolling_q25_is_causal_and_suppresses_one_peak(self) -> None:
        values = torch.tensor((0.0, 0.0, 0.0, 100.0, 4.0))
        ends = torch.tensor((7, 8, 9, 10, 11))
        scores, score_ends = _rolling_quantile(values, ends, width=4, quantile=0.25)
        self.assertEqual(score_ends.tolist(), [10, 11])
        self.assertAlmostEqual(float(scores[0]), 0.0)
        self.assertAlmostEqual(float(scores[1]), 0.0)

    def test_joint_stream_aligns_endpoints_and_takes_minimum(self) -> None:
        scores, ends = _align_minimum(
            torch.tensor((1.0, 5.0, 3.0)),
            torch.tensor((10, 11, 12)),
            torch.tensor((4.0, 2.0, 8.0)),
            torch.tensor((11, 12, 13)),
        )
        self.assertEqual(ends.tolist(), [11, 12])
        self.assertEqual(scores.tolist(), [4.0, 2.0])

    def test_phase_uses_method_specific_fully_post_boundary(self) -> None:
        row = {
            "positive": True,
            "evidence_onset": 5,
            "streams": {
                "joint_floor_4": {
                    "endpoints": [4, 5, 15, 16, 17],
                    "scores": [0.0, 1.0, 2.0, 3.0, 4.0],
                }
            },
        }
        phases = _phase_values(row, "joint_floor_4")
        self.assertEqual(phases["drift_pre_onset"], [0.0])
        self.assertEqual(phases["drift_mixed_transition"], [1.0, 2.0])
        self.assertEqual(phases["drift_fully_post_onset"], [3.0, 4.0])

    def test_pair_group_calibration_collapses_correlated_arms(self) -> None:
        rows = _pair_group_maxima(
            ("a-clean", "a-benign", "b-clean"),
            ("a", "a", "b"),
            (1.0, 3.0, 2.0),
        )
        self.assertEqual([row["maximum"] for row in rows], [3.0, 2.0])

    def test_risk_step_curve_uses_bounded_prefix_and_is_monotone(self) -> None:
        rows = [
            {
                "positive": False,
                "streams": {"m": {"scores": [0.0] * 7 + [2.0]}},
            },
            {
                "positive": False,
                "streams": {"m": {"scores": [0.0] * 20}},
            },
        ]
        curve = _risk_step_curve(rows, "m", threshold=1.0)
        counts = [row["alarm_by_step_count"] for row in curve]
        self.assertEqual(counts[:2], [0, 1])
        self.assertEqual(counts, sorted(counts))
        self.assertEqual(curve[-1]["risk_step"], "full")


if __name__ == "__main__":
    unittest.main()
