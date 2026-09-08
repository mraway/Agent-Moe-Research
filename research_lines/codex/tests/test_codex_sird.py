"""Unit tests for the preregistered Codex SIRD experiment."""

from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_codex_sird import (  # noqa: E402
    classify_after_alarm,
    empirical_upper_tail,
    first_alarm,
    innovation_distance_matrix,
    leave_trace_out_raw,
    state_distance_matrix,
    block_state_innovation,
    validate_execution_lock,
)


class CodexSirdTest(unittest.TestCase):
    def test_execution_lock_requires_explicit_authorization(self) -> None:
        with self.assertRaises(PermissionError):
            validate_execution_lock(False)

    def test_execution_lock_rejects_changed_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "plan.md"
            changed.write_text("changed\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_execution_lock(True, changed)

    def test_block_state_and_signed_innovation_use_disjoint_blocks(self) -> None:
        top_k = torch.zeros((16, 16, 8), dtype=torch.long)
        top_k[:, 8:, :] = 1
        endpoints, states, innovations = block_state_innovation(top_k)
        self.assertEqual(endpoints.tolist(), [15])
        self.assertEqual(tuple(states.shape), (1, 16, 64))
        self.assertTrue(torch.allclose(states[0, :, 1], torch.ones(16)))
        self.assertTrue(torch.allclose(innovations[0, :, 1], torch.ones(16)))
        self.assertTrue(torch.allclose(innovations[0, :, 0], -torch.ones(16)))

    def test_state_and_innovation_distances_preserve_direction(self) -> None:
        state_a = torch.zeros((1, 16, 64))
        state_b = torch.zeros((1, 16, 64))
        state_a[:, :, 0] = 1.0
        state_b[:, :, 1] = 1.0
        self.assertAlmostEqual(float(state_distance_matrix(state_a, state_a)[0, 0]), 0.0)
        self.assertAlmostEqual(float(state_distance_matrix(state_a, state_b)[0, 0]), 1.0)
        innovation = state_b - state_a
        self.assertAlmostEqual(
            float(innovation_distance_matrix(innovation, -innovation)[0, 0]),
            math.sqrt(2.0),
            places=6,
        )

    def test_leave_trace_out_excludes_every_same_source_anchor(self) -> None:
        features = torch.zeros((6, 16, 64))
        for index in range(6):
            features[index, :, index] = 1.0
        trace_ids = ("a", "a", "b", "b", "c", "c")
        raw = leave_trace_out_raw(
            features, trace_ids, state_distance_matrix, neighbor_k=2
        )
        self.assertTrue(torch.allclose(raw, torch.ones_like(raw)))

    def test_empirical_rank_is_tie_conservative(self) -> None:
        p, z = empirical_upper_tail(
            torch.tensor([1.0, 2.0, 4.0]), torch.tensor([1.0, 2.0, 2.0, 3.0])
        )
        self.assertTrue(torch.allclose(p, torch.tensor([1.0, 0.8, 0.2])))
        self.assertTrue(torch.allclose(z, -torch.log(p)))

    def test_first_alarm_is_strict_and_freezes_first_crossing(self) -> None:
        row = {
            "endpoints": [15, 16, 17],
            "sird": [2.0, 2.1, 3.0],
            "state_only": [1.0, 1.0, 4.0],
            "innovation_only": [2.0, 2.1, 1.0],
        }
        alarm = first_alarm(row, "sird", 2.0)
        self.assertEqual(alarm["endpoint"], 16)
        self.assertEqual(alarm["head"], "innovation_only")

    def test_state_classification_uses_next_disjoint_endpoint(self) -> None:
        row = {
            "endpoints": [15, 23, 31],
            "state_p": [0.01, 0.20, 0.05],
        }
        self.assertEqual(
            classify_after_alarm(row, {"endpoint": 15}), "recovering"
        )
        self.assertEqual(
            classify_after_alarm(row, {"endpoint": 23}), "sustained"
        )
        self.assertEqual(
            classify_after_alarm(row, {"endpoint": 31}), "uncertain_censored"
        )


if __name__ == "__main__":
    unittest.main()
