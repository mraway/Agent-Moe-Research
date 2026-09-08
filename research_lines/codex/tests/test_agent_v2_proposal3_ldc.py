from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from run_agent_v2_proposal3_ldc import (  # noqa: E402
    classify_trajectory,
    calculate,
    first_alarm,
    method_direction_score,
    path_maximum,
    second_largest,
    token_selection_signatures,
    trajectory_at_start,
    validate_execution_lock,
)


def score_row(
    early: list[float],
    middle: list[float],
    late: list[float],
    pooled: list[float] | None = None,
) -> dict:
    length = len(early)
    if not (len(middle) == len(late) == length):
        raise ValueError("synthetic streams must align")
    return {
        "trace_id": "trace",
        "pair_group_id": "group",
        "arm": "attack",
        "token_count": length,
        "band_scores": {
            "early": early,
            "middle": middle,
            "late": late,
            "pooled": pooled if pooled is not None else [0.0] * length,
        },
    }


class Proposal3LDCTests(unittest.TestCase):
    def test_current_preregistration_matches_embedded_hash(self) -> None:
        validate_execution_lock(True)

    def test_calculate_refuses_missing_explicit_execution_guard_before_io(self) -> None:
        missing = Path("/definitely/not/read")
        with self.assertRaisesRegex(PermissionError, "execute-routing-analysis"):
            calculate(
                missing,
                missing,
                missing,
                missing,
                missing,
                missing,
                missing,
                missing,
            )

    def test_preregistration_hash_lock_rejects_modified_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "changed-plan.md"
            changed.write_text("changed preregistration\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                validate_execution_lock(True, changed)

    def test_token_signature_is_sqrt_top8_frequency(self) -> None:
        ids = torch.arange(8).reshape(1, 1, 8).expand(16, 2, 8).clone()
        signatures = token_selection_signatures(ids)
        self.assertEqual(tuple(signatures.shape), (2, 16, 64))
        self.assertAlmostEqual(float(signatures[0, 0, 0]), (1.0 / 8.0) ** 0.5)
        self.assertEqual(float(signatures[0, 0, 9]), 0.0)

    def test_second_largest_is_exact_two_of_three_statistic(self) -> None:
        self.assertEqual(second_largest([9.0, 1.0, 4.0]), 4.0)
        with self.assertRaises(ValueError):
            second_largest([1.0, 2.0])

    def test_trajectory_uses_frozen_nonadjacent_windows(self) -> None:
        row = score_row(
            [2.0] * 16 + [99.0] * 16 + [0.0] * 32,
            [3.0] * 16 + [99.0] * 16 + [1.0] * 32,
            [0.0] * 16 + [99.0] * 16 + [4.0] * 32,
            [1.0] * 16 + [99.0] * 16 + [2.0] * 32,
        )
        result = trajectory_at_start(row, 0)
        self.assertEqual(result["engagement_score"], 2.0)
        self.assertEqual(result["early"]["middle"], 3.0)
        self.assertEqual(result["late"]["middle"], 1.0)
        self.assertEqual(result["delta"]["middle"], -2.0)
        self.assertEqual(result["consensus_delta"], -2.0)
        self.assertEqual(result["engagement_visible_at"], 15)
        self.assertEqual(result["classification_visible_at"], 63)

    def test_incomplete_future_is_censored_not_clamped(self) -> None:
        row = score_row([2.0] * 40, [2.0] * 40, [0.0] * 40)
        result = trajectory_at_start(row, 0)
        self.assertEqual(classify_trajectory(result, 1.0), "engaged_censored")
        self.assertIsNone(result["delta"])

    def test_two_active_recovery_votes_are_required(self) -> None:
        row = score_row(
            [2.0] * 16 + [0.0] * 16 + [0.0] * 32,
            [3.0] * 16 + [0.0] * 16 + [1.0] * 32,
            [0.5] * 64,
        )
        result = trajectory_at_start(row, 0)
        self.assertEqual(classify_trajectory(result, 1.0), "engaged_recovered")

    def test_split_active_votes_are_uncertain(self) -> None:
        row = score_row(
            [2.0] * 16 + [0.0] * 16 + [3.0] * 32,
            [3.0] * 16 + [0.0] * 16 + [1.0] * 32,
            [0.5] * 64,
        )
        result = trajectory_at_start(row, 0)
        self.assertEqual(classify_trajectory(result, 1.0), "engaged_uncertain")

    def test_strict_threshold_equality_does_not_alarm(self) -> None:
        row = score_row([1.0] * 16, [1.0] * 16, [0.0] * 16)
        self.assertIsNone(first_alarm(row, 1.0))
        self.assertEqual(path_maximum(row), 1.0)

    def test_first_alarm_does_not_skip_to_later_better_candidate(self) -> None:
        early = [0.0] * 72
        middle = [0.0] * 72
        late = [0.0] * 72
        for index in range(4, 20):
            early[index] = 2.0
            middle[index] = 2.0
        row = score_row(early, middle, late)
        result = first_alarm(row, 0.4)
        assert result is not None
        self.assertEqual(result["start"], 0)

    def test_comparator_direction_scores_are_not_ldc_median(self) -> None:
        row = score_row(
            [2.0] * 16 + [0.0] * 16 + [3.0] * 32,
            [2.0] * 16 + [0.0] * 16 + [3.0] * 32,
            [2.0] * 16 + [0.0] * 16 + [0.0] * 32,
            [2.0] * 16 + [0.0] * 16 + [4.0] * 32,
        )
        result = trajectory_at_start(row, 0)
        self.assertEqual(method_direction_score(result, "ldc"), 1.0)
        self.assertEqual(method_direction_score(result, "late_only_fhts"), -2.0)
        self.assertEqual(method_direction_score(result, "pooled_all_layer_fhts"), 2.0)


if __name__ == "__main__":
    unittest.main()
