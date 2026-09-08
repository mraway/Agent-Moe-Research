from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from review_astra_stage2_q2 import measures, paired_comparison  # noqa: E402


class Q2PairedReviewTests(unittest.TestCase):
    def test_capped_length_not_natural_eos_success(self):
        self.assertFalse(measures({"normal_acceptable": True, "stop_reason": "length", "output_tokens": 1024})["eos_ge384"])

    def test_all_pairs_retained_and_correct_direction(self):
        old = [{"source_trace_id": "a", "normal_acceptable": False, "stop_reason": "eos", "output_tokens": 100},
               {"source_trace_id": "b", "normal_acceptable": True, "stop_reason": "eos", "output_tokens": 400}]
        new = [{"source_trace_id": "b", "normal_acceptable": False, "stop_reason": "eos", "output_tokens": 200},
               {"source_trace_id": "a", "normal_acceptable": True, "stop_reason": "eos", "output_tokens": 500}]
        result = paired_comparison(old, new)
        self.assertEqual(result["quality"], {"improved": 1, "regressed": 1, "unchanged": 0})
        self.assertEqual(result["median_paired_token_difference"], 100)

    def test_different_or_duplicate_pairs_rejected(self):
        row = {"source_trace_id": "a", "normal_acceptable": True, "stop_reason": "eos", "output_tokens": 400}
        with self.assertRaises(ValueError):
            paired_comparison([row, row], [row])
        with self.assertRaises(ValueError):
            paired_comparison([row], [dict(row, source_trace_id="b")])


if __name__ == "__main__":
    unittest.main()
