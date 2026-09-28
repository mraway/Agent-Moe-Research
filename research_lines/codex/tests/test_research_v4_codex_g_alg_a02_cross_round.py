import unittest

import numpy as np

from research_v4 import codex_g_alg_a02_cross_round as c


class CrossRoundTests(unittest.TestCase):
    def bank(self, xs, groups):
        roots = np.asarray(xs, dtype=np.float32).reshape(-1, 1)
        scenarios = tuple(sorted(set(groups)))
        return c.d.am.Bank((roots, roots), (roots, roots), np.zeros(len(xs), dtype=int),
                           scenarios, np.array([scenarios.index(g) for g in groups]),
                           np.array([f'{g}-{i}' for i,g in enumerate(groups)]), np.arange(len(xs)))

    def test_pool_never_counts_same_scenario_twice(self):
        a = self.bank([.1, .2, .3, .4], ["a", "b", "c", "query"])
        b = self.bank([.001, .002, .003, .8], ["a", "a", "a", "d"])
        value, rows = c.neighbours(np.array([0.]), {"0": a, "1": b}, 0, "query")
        self.assertEqual({r[1] for r in rows}, {"a", "b", "c"})
        self.assertAlmostEqual(value, (.001**2+.2**2+.3**2)/6, places=7)

    def test_pool_monotonicity_and_query_exclusion(self):
        a = self.bank([.1, .2, .3, 0.], ["a", "b", "c", "query"])
        b = self.bank([.4, .5, .6, 0.], ["a", "b", "d", "query"])
        q = np.array([0.]); both, rows = c.neighbours(q, {"0": a, "1": b}, 0, "query")
        self.assertLessEqual(both, min(c.neighbours(q, {"0": a}, 0, "query")[0], c.neighbours(q, {"1": b}, 0, "query")[0]))
        self.assertNotIn("query", [r[1] for r in rows])

    def test_insufficient_support_is_failure(self):
        with self.assertRaises(ValueError):
            c.neighbours(np.array([0.]), {"0": self.bank([.1, .2], ["a", "b"])}, 0, "query")


if __name__ == "__main__":
    unittest.main()
