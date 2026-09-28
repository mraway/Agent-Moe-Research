"""Synthetic tests for the explicitly post-result descriptive ledger."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from research_v4.codex_g_m6_readouts import paired_binary, paired_timing


class ReadoutsTest(unittest.TestCase):
    def test_binary_partition(self):
        keys = list("abcd")
        a = dict(zip(keys, (True, True, False, False)))
        b = dict(zip(keys, (True, False, True, False)))
        self.assertEqual(paired_binary(a, b, keys), {"n": 4, "both": 1, "only_baseline": ["b"], "only_candidate": ["c"], "neither": 1})

    def test_conditional_timing_direction(self):
        result = paired_timing({"a": 20, "b": 30, "c": 40}, {"a": 10, "b": 30, "c": 43}, list("abc"))
        self.assertEqual([result[k] for k in ("n", "earlier", "equal", "later")], [3, 1, 1, 1])
        self.assertEqual(result["median_candidate_minus_baseline_tokens"], 0.)
        self.assertIsNone(paired_timing({}, {}, [])["median_candidate_minus_baseline_tokens"])


if __name__ == "__main__":
    unittest.main()
