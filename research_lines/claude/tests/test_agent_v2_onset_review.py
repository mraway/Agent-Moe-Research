"""Tests for onset blind-packet and review validation utilities."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_agent_v2_onset_review_packet import (  # noqa: E402
    opaque_case_id,
    visible_token_pieces,
)
from validate_agent_v2_onset_review import (  # noqa: E402
    locate_unique_span,
    validate_review,
)
from analyze_agent_v2_onset_structural_audit import (  # noqa: E402
    sign_counts,
    value_summary,
)
from evaluate_agent_v2_onset_agreement import cohen_kappa  # noqa: E402


class OnsetReviewTests(unittest.TestCase):
    def test_structural_summaries(self) -> None:
        self.assertEqual(value_summary([0, 2, 4])["median"], 2)
        self.assertEqual(
            sign_counts([-2, 0, 0, 3]),
            {"negative": 1, "zero": 2, "positive": 1},
        )

    def test_cohen_kappa(self) -> None:
        self.assertEqual(cohen_kappa([True, False], [True, False]), 1.0)
        self.assertAlmostEqual(
            cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]),
            0.0,
        )

    def test_visible_token_pieces_strip_stop_suffix(self) -> None:
        self.assertEqual(
            visible_token_pieces(["hello", " world", "<stop>"], "hello world"),
            ["hello", " world"],
        )

    def test_visible_token_pieces_accept_small_display_normalization(self) -> None:
        self.assertEqual(
            visible_token_pieces(["x ", "��", " y", "<stop>"], "x ≈ y"),
            ["x ", "≈", " y"],
        )

    def test_visible_token_pieces_preserve_cross_token_replacement_index(self) -> None:
        self.assertEqual(
            visible_token_pieces(["x �", "�", " y", "<stop>"], "x ≈ y"),
            ["x ≈", "", " y"],
        )

    def test_opaque_case_id_is_stable_and_hides_trace(self) -> None:
        first = opaque_case_id("trace-1")
        self.assertEqual(first, opaque_case_id("trace-1"))
        self.assertNotEqual(first, opaque_case_id("trace-2"))
        self.assertNotIn("trace-1", first)

    def test_unique_span_aligns_partial_token_boundaries(self) -> None:
        pieces = ["Hello", " world", "."]
        span = locate_unique_span(pieces, "Hello world.", "lo wo")
        self.assertEqual(span["token_start"], 0)
        self.assertEqual(span["token_end"], 1)

    def test_duplicate_evidence_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "not unique"):
            locate_unique_span(["once once"], "once once", "once")

    def test_review_event_hierarchy_and_alignment(self) -> None:
        packet = [
            {
                "case_id": "case",
                "final_output": "I will write it. SELECT 1",
                "output_tokens": [
                    {"index": 0, "text": "I will"},
                    {"index": 1, "text": " write it."},
                    {"index": 2, "text": " SELECT"},
                    {"index": 3, "text": " 1"},
                ],
            }
        ]
        event = lambda evidence: {  # noqa: E731
            "evidence": evidence,
            "rationale": "test",
            "confidence": "high",
        }
        review = [
            {
                "case_id": "case",
                "trajectory_class": "execution",
                "engagement": event("I will write it"),
                "commitment": event("I will write it"),
                "execution": event("SELECT"),
                "task_specific_transition_sentence": True,
                "support_resume": None,
                "reviewer": "reviewer-a",
                "overall_confidence": "high",
            }
        ]

        result = validate_review(packet, review)

        self.assertEqual(result[0]["execution"]["span"]["token_start"], 2)

    def test_class_event_mismatch_is_rejected(self) -> None:
        packet = [
            {
                "case_id": "case",
                "final_output": "No",
                "output_tokens": [{"index": 0, "text": "No"}],
            }
        ]
        review = [
            {
                "case_id": "case",
                "trajectory_class": "silent",
                "engagement": {
                    "evidence": "No",
                    "rationale": "test",
                    "confidence": "high",
                },
                "commitment": None,
                "execution": None,
                "task_specific_transition_sentence": False,
                "support_resume": None,
                "reviewer": "reviewer-a",
                "overall_confidence": "high",
            }
        ]
        with self.assertRaisesRegex(ValueError, "trajectory/event presence mismatch"):
            validate_review(packet, review)


if __name__ == "__main__":
    unittest.main()
