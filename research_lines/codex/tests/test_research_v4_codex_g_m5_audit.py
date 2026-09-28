from copy import deepcopy
import unittest

from research_v4.codex_g_m5_audit import check_cell


def fixture():
    block = {"events": 1, "raw": [2.] * 4 + [1, 1, 1], "control": [1.] * 4 + [1, 1, 1],
             "residual": [1.] * 4 + [0, 0, 0]}
    return {"eligible_events": 2,
            "native": {k: deepcopy(block) for k in ("B", "C", "H", "HP")},
            "aligned_H": {k: deepcopy(block) for k in ("B", "C", "H")},
            "aligned_HP": {k: deepcopy(block) for k in ("H", "HP")}}


class AuditTest(unittest.TestCase):
    def test_valid_arithmetic(self):
        self.assertEqual(check_cell(fixture()), 0)

    def test_detects_changed_target_query_average(self):
        c = fixture()
        c["aligned_H"]["B"]["raw"][0] += 1
        c["aligned_H"]["B"]["residual"][0] += 1
        with self.assertRaises(AssertionError):
            check_cell(c)

    def test_detects_binary_residual_and_non_nested_coverage(self):
        c = fixture()
        c["native"]["H"]["raw"][4] += 1
        c["native"]["H"]["residual"][4] += 1
        with self.assertRaises(AssertionError):
            check_cell(c)
        c = fixture()
        c["native"]["HP"]["events"] = 2
        with self.assertRaises(AssertionError):
            check_cell(c)

    def test_missing_is_not_zero_effect(self):
        c = fixture()
        for mode in ("native", "aligned_H", "aligned_HP"):
            for block in c[mode].values():
                block.update(events=0, raw=None, control=None, residual=None)
        self.assertEqual(check_cell(c), 0)
        c["native"]["B"]["residual"] = [0] * 7
        with self.assertRaises(AssertionError):
            check_cell(c)


if __name__ == "__main__":
    unittest.main()
