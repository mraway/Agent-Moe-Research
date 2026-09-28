"""Synthetic-only A03 tests: structure, balancing, covariance and causality."""
import unittest
import numpy as np
from research_v4 import codex_g_alg_a03_math as m
from research_v4 import codex_g_alg_a03_preflight as p
from research_v4 import codex_g_alg_a01_math as am


class A03Tests(unittest.TestCase):
    def data(self):
        rng = np.random.default_rng(783)
        roots = tuple(rng.normal(size=(24, 8)) for _ in range(2))
        keys = np.repeat(["a", "b", "c", "d", "e"], [2, 4, 5, 6, 7])
        groups = np.repeat([0, 0, 1, 2, 2], [2, 4, 5, 6, 7])
        return roots, keys, groups

    def test_equal_scenario_episode_weights(self):
        _, keys, groups = self.data(); w = m.balanced_weights(keys, groups)
        for s in range(3): self.assertAlmostEqual(w[groups == s].sum(), 1/3)
        for k, expected in zip(("a", "b", "c", "d", "e"), (1/6, 1/6, 1/3, 1/6, 1/6)):
            self.assertAlmostEqual(w[keys == k].sum(), expected)

    def test_balancing_invalid_input(self):
        for keys, groups in (([], []), (["a", "a"], [0, 1]), (["a"], [0, 1])):
            with self.assertRaises(ValueError): m.balanced_weights(keys, groups)

    def test_replication_does_not_reweight_episode(self):
        roots, keys, groups = self.data(); w = m.balanced_weights(keys, groups)
        extra = np.flatnonzero(keys == "a")
        roots2 = np.concatenate((roots[0], roots[0][extra])); keys2 = np.concatenate((keys, keys[extra]))
        groups2 = np.concatenate((groups, groups[extra])); w2 = m.balanced_weights(keys2, groups2)
        for a, b in zip(m.moments(roots[0], w), m.moments(roots2, w2)):
            np.testing.assert_allclose(a, b, rtol=0, atol=1e-14)

    def test_moments_independent_literal_outer_products(self):
        roots, keys, groups = self.data(); w = m.balanced_weights(keys, groups)
        mu, c = m.moments(roots[0], w, block=5)
        mean = sum(q*x for q, x in zip(w, roots[0]))
        cov = sum(q*np.outer(x-mean, x-mean) for q, x in zip(w, roots[0]))
        np.testing.assert_allclose(mu, mean, rtol=0, atol=1e-14)
        np.testing.assert_allclose(c, cov, rtol=0, atol=1e-14)

    def test_same_diagonal_only_offdiagonal_structure_changes(self):
        roots, keys, groups = self.data()
        _, cov = m.moments(roots[0], m.balanced_weights(keys, groups))
        diag, block, full = m.matrices(cov, 4)
        np.testing.assert_allclose(np.diag(diag), np.diag(block), rtol=0, atol=1e-15)
        np.testing.assert_allclose(np.diag(diag), np.diag(full), rtol=0, atol=1e-15)
        np.testing.assert_array_equal(block[:4, 4:], 0)
        np.testing.assert_allclose(block[:4, :4], full[:4, :4], rtol=0, atol=1e-15)
        np.testing.assert_allclose(full[:4, 4:], .5*cov[:4, 4:], rtol=0, atol=1e-15)
        for matrix in (diag, block, full): self.assertGreater(np.linalg.eigvalsh(matrix).min(), 0)

    def test_zero_variance_rejected_and_constant_coordinates_regularized(self):
        with self.assertRaises(ValueError): m.matrices(np.zeros((8, 8)), 4)
        cov = np.diag([0., 0., 1., 1., 1., 1., 1., 1.])
        for matrix in m.matrices(cov, 4): self.assertGreater(np.linalg.eigvalsh(matrix).min(), 0)

    def test_diagonal_covariance_all_structures_equal(self):
        matrices = m.matrices(np.diag(np.arange(1., 9.)), 4)
        for matrix in matrices[1:]: np.testing.assert_array_equal(matrix, matrices[0])

    def test_whitening_scores_match_independent_solve(self):
        roots, keys, groups = self.data(); model = m.fit(roots, m.balanced_weights(keys, groups), 4)
        raw = m.score(roots, model, 7)
        np.testing.assert_allclose(raw, p.independent_scores(roots, model), rtol=1e-12, atol=1e-12)

    def test_state_restore_exact_and_no_fitting(self):
        roots, keys, groups = self.data(); model = m.fit(roots, m.balanced_weights(keys, groups), 4)
        restored = m.Model.restore(model.state())
        np.testing.assert_array_equal(m.score(roots, model), m.score(roots, restored))
        state = model.state(); state["rho"] = np.asarray(.2)
        with self.assertRaises(ValueError): m.Model.restore(state)

    def test_joint_model_distinguishes_broken_pair(self):
        rng = np.random.default_rng(822)
        latent = rng.normal(size=(100, 1))
        roots = latent*np.ones((1, 8)) + .05*rng.normal(size=(100, 8))
        model = m.fit((roots, roots), np.full(100, .01), 4)
        common = np.ones(8); broken = common.copy(); broken[4:] *= -1
        # Equal marginal magnitudes, opposite cross-layer relationships.
        query = np.stack((model.mu[0]+common, model.mu[0]+broken))
        scores = m.score((query, query), model)
        self.assertAlmostEqual(scores[0, 0], scores[1, 0])
        self.assertAlmostEqual(scores[0, 2], scores[1, 2])
        self.assertGreater(scores[1, 4], scores[0, 4]*2)

    def test_mean_window_causality(self):
        rng = np.random.default_rng(527)
        ps = rng.random((20, 24, 32)).astype(np.float32); ps /= ps.sum(-1, keepdims=True)
        ends = np.array([7, 8, 10]); before = am.features(ps, ends)[1]
        ps[11:] = 1/32
        np.testing.assert_array_equal(before, am.features(ps, ends)[1])

    def test_empty_queries_keep_shape(self):
        roots, keys, groups = self.data(); model = m.fit(roots, m.balanced_weights(keys, groups), 4)
        self.assertEqual(m.score((roots[0][:0], roots[1][:0]), model).shape, (0, 6))

    def test_normal_guard_refuses_mixed_a03_outputs(self):
        guard = p.Guard(True)
        for folder in ("score", "audit"):
            with self.assertRaises(PermissionError): guard.check_path(p.OUT/folder/"anything.json")
        self.assertEqual(guard.check_path(p.OUT/"preflight/result.json"), p.OUT/"preflight/result.json")

    def test_diagnostics_finite(self):
        roots, keys, groups = self.data(); model = m.fit(roots, m.balanced_weights(keys, groups), 4)
        for r in m.diagnostics(model):
            self.assertGreaterEqual(r["covariance_participation_rank"], 1)
            self.assertLessEqual(r["covariance_participation_rank"], 8+1e-12)
            self.assertGreaterEqual(r["full_regularized_condition_number"], 1)


if __name__ == "__main__": unittest.main()
