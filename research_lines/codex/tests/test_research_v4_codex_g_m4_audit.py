from collections import Counter
from types import SimpleNamespace
import unittest

import numpy as np
import torch

from research_v4.codex_g_m4_audit import coverage, qualify, stable_inventory


class CoverageTest(unittest.TestCase):
    def test_exact_support_rare_and_horizon(self):
        ids = torch.arange(4).view(1, 1, 4).expand(24, 5, 4).clone()
        ids[1, 2, 3] = 4
        ep = SimpleNamespace(top_k_ids=ids, channel_tags=["analysis"] * 3 + ["final"] * 2,
                             step_spans=[{"global_token_offset": 0}])
        rare = np.zeros((24, 32), dtype=bool)
        rare[0, 0] = True
        rows = stable_inventory(ep, {"h_end": 3}, rare)
        self.assertEqual(len(rows), 47)
        self.assertEqual(sum(r[2] for r in rows), 2)
        self.assertTrue(all(r[0] in (1, 2) for r in rows))

    def test_reordered_support_is_same_but_step_boundary_is_not(self):
        ids = torch.arange(4).view(1, 1, 4).expand(24, 3, 4).clone()
        ids[:, 1] = ids[:, 1].flip(-1)
        ep = SimpleNamespace(top_k_ids=ids, channel_tags=["analysis"] * 3,
                             step_spans=[{"global_token_offset": 0}, {"global_token_offset": 2}])
        rows = stable_inventory(ep, {"h_end": 2}, np.zeros((24, 32), dtype=bool))
        self.assertEqual(len(rows), 24)

    def test_minima_use_distinct_donors(self):
        self.assertEqual(qualify({"yes": Counter(a=2, b=2, c=1), "short": Counter(a=1, b=1, c=1),
                                  "same": Counter(a=10)}), {"yes"})

    def test_count_rare_matching_separately_and_noX_explicit(self):
        rows = [(4, "a", True), (5, "b", False), (6, "c", True)]
        c = coverage(rows, {"a", "b"}, e=4, x=6)
        self.assertEqual(dict(c["whole"]), {"events": 3, "matched": 2, "rare_events": 2, "rare_matched": 1})
        self.assertEqual(c["E_strict_preX"]["events"], 2)
        self.assertEqual(c["X"]["events"], 1)
        self.assertIn("E_noX", coverage(rows, set(), e=4, x=None))


if __name__ == "__main__":
    unittest.main()
