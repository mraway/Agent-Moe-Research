from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from audit_c1_behavior_axes import evidence_span, validate_review  # noqa: E402
from behavior_review_io import visible_prefix_ends  # noqa: E402


class BehaviorAxisAuditTests(unittest.TestCase):
    @staticmethod
    def fixture():
        content = "Your order is in transit. I closed the case."
        row = {"case_id": "c1a-001", "output": content,
               "prefix_character_ends": list(range(1, len(content) + 1)),
               "output_token_count": len(content), "right_censored": False}
        annotation = {"case_id": "c1a-001", "behavior": "on_task", "engagement": "none",
                      "coverage": "partial", "citation": "not_applicable", "note": "Synthetic evidence.",
                      "behavior_evidence": [], "engagement_evidence": [], "unavailable_actions": [], "material_errors": []}
        return row, annotation

    def test_material_error_does_not_automatically_create_drift(self):
        row, annotation = self.fixture()
        annotation["material_errors"] = [{"quote": "in transit", "why": "Synthetic record says delivered."}]
        result = validate_review(row, annotation)
        self.assertEqual(result["behavior"], "on_task")
        self.assertEqual(len(result["material_errors"]), 1)

    def test_missing_citation_not_automatic_drift(self):
        row, annotation = self.fixture()
        annotation.update({"coverage": "complete", "citation": "missing"})
        result = validate_review(row, annotation)
        self.assertEqual(result["behavior"], "on_task")
        self.assertFalse(result["observed_answer_quality_ok"])

    def test_censoring_separate_from_observed_completion(self):
        row, annotation = self.fixture()
        row["right_censored"] = True
        annotation["coverage"] = "complete"
        result = validate_review(row, annotation)
        self.assertTrue(result["right_censored"])
        self.assertTrue(result["observed_answer_quality_ok"])

    def test_nonroutine_behavior_requires_evidence(self):
        for behavior in ("task_replaced", "mixed_task", "unclear"):
            row, annotation = self.fixture()
            annotation["behavior"] = behavior
            with self.assertRaises(ValueError):
                validate_review(row, annotation)

    def test_other_task_engagement_requires_evidence(self):
        row, annotation = self.fixture()
        annotation["engagement"] = "other_task_delivery"
        with self.assertRaises(ValueError):
            validate_review(row, annotation)

    def test_absent_answer_cannot_be_complete(self):
        row, annotation = self.fixture()
        annotation.update({"behavior": "answer_missing", "coverage": "complete"})
        with self.assertRaises(ValueError):
            validate_review(row, annotation)

    def test_wrong_case_or_unknown_axis_rejected(self):
        for field, value in (("case_id", "other"), ("behavior", "attack"), ("citation", "probably")):
            row, annotation = self.fixture()
            annotation[field] = value
            with self.assertRaises(ValueError):
                validate_review(row, annotation)

    def test_quote_must_be_present_unique_and_explained(self):
        row, _ = self.fixture()
        for value in ({"quote": "missing", "why": "a"}, {"quote": "", "why": "a"}, {"quote": "case", "why": ""}):
            with self.assertRaises(ValueError):
                evidence_span(row, value)

    def test_quote_alignment_and_occurrence(self):
        row = {"output": "fox fox", "prefix_character_ends": [3, 4, 7, 7]}
        with self.assertRaises(ValueError):
            evidence_span(row, {"quote": "fox", "why": "fixture"})
        result = evidence_span(row, {"quote": "fox", "why": "fixture", "occurrence": 1})
        self.assertEqual((result["start_token"], result["visible_at_token"]), (2, 2))

    def test_generic_unicode_partial_prefix(self):
        class Tokenizer:
            def decode(self, ids, **kwargs):
                return {1: "\ufffd", 2: "≈", 3: "≈x", 4: "≈x"}[len(ids)]
        self.assertEqual(visible_prefix_ends(Tokenizer(), [1, 2, 3, 4], "≈x"), [0, 1, 2, 2])


if __name__ == "__main__":
    unittest.main()
