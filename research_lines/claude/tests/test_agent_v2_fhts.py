from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_agent_v2_fhts import (  # noqa: E402
    calibrate_loso_thresholds,
    finite_horizon_candidates,
    finite_sample_upper_threshold,
    predict_first_state,
    summarize_scan,
)


class AgentV2FHTSTests(unittest.TestCase):
    def test_scan_uses_frozen_disjoint_windows(self) -> None:
        scores = [0.0] * 64
        scores[0:16] = [4.0] * 16
        scores[16:32] = [100.0] * 16
        scores[32:64] = [1.0] * 32

        rows = finite_horizon_candidates(list(range(64)), scores)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["start"], 0)
        self.assertEqual(rows[0]["decision_token"], 63)
        self.assertEqual(rows[0]["early_mean"], 4.0)
        self.assertEqual(rows[0]["late_mean"], 1.0)
        self.assertEqual(rows[0]["recovery_score"], 3.0)
        self.assertEqual(rows[0]["persistence_score"], 1.0)

    def test_candidate_is_unchanged_by_unobserved_future(self) -> None:
        prefix = [float(index % 7) for index in range(64)]
        first = finite_horizon_candidates(list(range(64)), prefix)[0]
        extended = finite_horizon_candidates(
            list(range(80)), [*prefix, *([999.0] * 16)]
        )[0]
        self.assertEqual(first, extended)

    def test_short_trace_has_no_opportunity(self) -> None:
        result = summarize_scan(list(range(63)), [0.0] * 63)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["candidate_count"], 0)

    def test_scan_rejects_noncontiguous_endpoints(self) -> None:
        with self.assertRaisesRegex(ValueError, "contiguous zero-based"):
            finite_horizon_candidates([0, 2], [1.0, 2.0])

    def test_threshold_uses_frozen_finite_sample_rank(self) -> None:
        result = finite_sample_upper_threshold(range(1, 11), alpha=0.20)
        self.assertEqual(result["rank_one_based"], 9)
        self.assertEqual(result["threshold"], 9.0)

    def test_loso_calibration_excludes_both_held_out_controls(self) -> None:
        rows = []
        for group_index, value in enumerate((1.0, 2.0, 3.0)):
            group = f"g{group_index}"
            for arm in ("clean", "benign_control"):
                rows.append(
                    {
                        "trace_id": f"{group}-{arm}",
                        "pair_group_id": group,
                        "arm": arm,
                        "eligible": True,
                        "recovery_max": value,
                        "persistence_max": value + 10.0,
                    }
                )
            rows.append(
                {
                    "trace_id": f"{group}-attack",
                    "pair_group_id": group,
                    "arm": "attack",
                    "eligible": True,
                    "recovery_max": 1000.0,
                    "persistence_max": 1000.0,
                }
            )

        thresholds = calibrate_loso_thresholds(
            rows, minimum_calibration_traces=4
        )

        self.assertEqual(thresholds["g0"]["recovery"]["calibration_count"], 4)
        self.assertEqual(thresholds["g0"]["recovery"]["threshold"], 3.0)
        self.assertEqual(
            thresholds["g0"]["excluded_trace_ids"],
            ["g0-benign_control", "g0-clean"],
        )

    def test_first_alarm_state_is_frozen_and_strict(self) -> None:
        candidates = [
            {
                "start": 0,
                "decision_token": 63,
                "recovery_score": 1.0,
                "persistence_score": 1.0,
            },
            {
                "start": 1,
                "decision_token": 64,
                "recovery_score": 3.0,
                "persistence_score": 2.0,
            },
            {
                "start": 2,
                "decision_token": 65,
                "recovery_score": 0.0,
                "persistence_score": 99.0,
            },
        ]

        result = predict_first_state(candidates, 2.0, 2.0)

        self.assertEqual(result["state"], "recovered")
        self.assertEqual(result["decision_token"], 64)
        self.assertTrue(result["recovery_ever_alarm"])
        self.assertTrue(result["persistence_ever_alarm"])

    def test_simultaneous_first_crossing_is_ambiguous(self) -> None:
        result = predict_first_state(
            [
                {
                    "start": 7,
                    "decision_token": 70,
                    "recovery_score": 3.0,
                    "persistence_score": 4.0,
                }
            ],
            2.0,
            2.0,
        )
        self.assertEqual(result["state"], "ambiguous")


if __name__ == "__main__":
    unittest.main()
