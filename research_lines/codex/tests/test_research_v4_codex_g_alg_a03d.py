import unittest
import numpy as np
from research_v4 import codex_g_alg_a03d_diagnostic as d
from research_v4 import codex_g_alg_a03_math as math


class DiagnosticTests(unittest.TestCase):
    def test_deadline_is_relative_to_x_not_x_plus_16(self):
        row = {"e": 10, "x": 50}
        self.assertFalse(d.early_hit(row, 43, -8))
        self.assertTrue(d.early_hit(row, 42, -8))

    def test_pre_e_is_never_rescued(self):
        row = {"e": 10, "x": 50}
        for offset in d.DELTAS: self.assertFalse(d.early_hit(row, 9, offset))
        self.assertFalse(d.early_hit(row, None, 16))

    def test_empty_early_interval(self):
        self.assertFalse(d.early_hit({"e": 10, "x": 20}, 10, -16))

    def test_history_peak_is_not_current_z(self):
        stream = {"ends": np.array([7, 8, 9]), "tags": np.array(["analysis"]*3), "ordinals": np.arange(3),
                  "raw": np.array([[1.], [4.], [2.]]), "z": np.array([[0.], [6.], [1.]]),
                  "p": np.array([[1.], [.2], [.1]])}
        event = d.event(stream, 0, .1)
        self.assertEqual((event["end"], event["peak_end"], event["z"], event["peak_z"]), (9, 8, 1., 6.))

    def test_no_alarm_and_tie_inclusion(self):
        s = {"p": np.array([[.1], [.05]])}
        self.assertIsNone(d.first_index(s, 0, .01)); self.assertEqual(d.first_index(s, 0, .05), 1)

    def test_signed_decomposition_equals_linear_solve(self):
        rng = np.random.default_rng(713)
        roots = [rng.normal(size=(40, 8)), rng.normal(size=(40, 8))]
        model = math.fit(roots, np.full(40, 1/40), layer_width=4)
        delta = rng.normal(size=8)
        for name, matrix in zip(("diag", "block", "full"), math.matrices(model.cov[1], 4)):
            values = d.signed_contributions(delta, model)[name]
            np.testing.assert_allclose(values.sum(), delta@np.linalg.solve(matrix, delta)/8, rtol=1e-12)

    def test_forbid_training(self):
        with self.assertRaises(AssertionError): d.prohibit()

    def test_access_guard_refuses_sealed_and_external_routes(self):
        guard = d.old.Guard(False)
        for path in (d.ROOT/"artifacts/agent_v2/dataset_g/g_conf/noopen.json",
                     d.ROOT/"artifacts/agent_v2/dataset_g/annotations/g_conf/noopen.json",
                     d.ROOT/"artifacts/not_allowed.safetensors"):
            with self.assertRaises(PermissionError): guard.check_path(path)

    def test_spec_constants(self):
        self.assertEqual(d.DELTAS, (-64, -32, -16, -8, -1, 0, 8, 16))
        self.assertEqual(len(d.TIMING), 6); self.assertEqual(len(d.PAIRS), 3)


if __name__ == "__main__": unittest.main()
