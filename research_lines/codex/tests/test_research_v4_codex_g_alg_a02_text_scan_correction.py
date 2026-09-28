import unittest

import numpy as np

from research_v4 import codex_g_alg_a02_text_scan_correction as f


class FakeTokenizer:
    def decode(self, tokens, skip_special_tokens=False):
        return "".join(chr(t) for t in tokens)


class TextCorrectionTests(unittest.TestCase):
    def test_tuple_list_array_same_matches(self):
        text = "date 2026-08-17."
        ep = dict(token_ids=np.array([ord(c) for c in text]), step_ids=np.zeros(len(text), dtype=int))
        expected = None
        for tags in (tuple(["analysis"]*len(text)), ["analysis"]*len(text), np.array(["analysis"]*len(text))):
            ep["tags"] = tags
            actual = f.date_segments(FakeTokenizer(), {"k": ep}, ["k"])
            self.assertEqual(len(actual), 1)
            if expected is None: expected = actual
            self.assertEqual(actual, expected)

    def test_never_join_across_step_or_channel(self):
        text = "2026-08-17"
        ep = dict(token_ids=np.array([ord(c) for c in text]), step_ids=np.array([0]*5+[1]*5), tags=["analysis"]*10)
        self.assertEqual(f.date_segments(FakeTokenizer(), {"k": ep}, ["k"]), [])
        ep["step_ids"][:] = 0; ep["tags"][4] = "final"
        self.assertEqual(f.date_segments(FakeTokenizer(), {"k": ep}, ["k"]), [])

    def test_non_date_is_not_evidence(self):
        text = "KB-001"
        ep = dict(token_ids=np.array([ord(c) for c in text]), step_ids=np.zeros(len(text), dtype=int), tags=["analysis"]*len(text))
        self.assertEqual(f.date_segments(FakeTokenizer(), {"k": ep}, ["k"]), [])


if __name__ == "__main__":
    unittest.main()
