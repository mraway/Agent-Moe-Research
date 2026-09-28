from __future__ import annotations
import unittest
import numpy as np
from research_v4 import codex_g_alg_a03n_math as m, codex_g_alg_a03n_run as run
from research_v4 import codex_g_alg_a03n_audit as audit


class MathTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(931)
        self.x = self.rng.normal(size=(64, 8))
        self.scenarios = np.repeat(np.arange(8), 8)
        self.keys = np.asarray([f"k{i//4}" for i in range(64)])
        self.w = m.old.balanced_weights(self.keys, self.scenarios)

    def test_grouped_mixture_matches_literal(self):
        gm, gc, counts, _ = m.grouped_moments(self.x, self.keys, self.scenarios)
        mu, cov = m.mixture(gm, gc, counts)
        expected, ec, _ = audit.literal_moments(self.x, self.keys, self.scenarios)
        np.testing.assert_allclose(mu, expected, atol=1e-13)
        np.testing.assert_allclose(cov, ec, atol=1e-13)

    def test_delete_is_whole_scenario(self):
        gm, gc, counts, groups = m.grouped_moments(self.x, self.keys, self.scenarios)
        for g in range(4):
            choose = np.arange(4) != g; keep = groups != g
            mu, cov = m.mixture(gm[choose], gc[choose], counts[choose])
            em, ec, _ = audit.literal_moments(self.x[keep], self.keys[keep], self.scenarios[keep])
            np.testing.assert_allclose(mu, em, atol=1e-13)
            np.testing.assert_allclose(cov, ec, atol=1e-13)

    def test_unequal_episode_looks_balanced(self):
        x = np.asarray([[0.], [0.], [2.], [8.]])
        keys = np.asarray(["a", "a", "b", "c"]); groups = np.asarray([0, 0, 0, 1])
        mu, cov, w = audit.literal_moments(x, keys, groups)
        self.assertEqual(float(mu[0]), 4.5)
        np.testing.assert_allclose(w, [.125, .125, .25, .5])
        np.testing.assert_allclose(m.old.moments(x, w)[1], cov)

    def test_invalid_group_ids(self):
        with self.assertRaises(ValueError): m.grouped_moments(self.x, self.keys, self.scenarios+1)

    def test_invalid_mixture(self):
        with self.assertRaises(ValueError): m.mixture([[0]], [[[1]]], [-1])

    def test_sigma_matches_parent(self):
        cov = np.cov(self.x.T, ddof=0)
        np.testing.assert_array_equal(m.sigma(cov), m.old.matrices(cov, 4)[2])

    def test_spectrum_linear_solve_and_energy(self):
        cov = np.cov(self.x.T); mu = self.x.mean(0)
        _, _, parts, energy = m.spectrum(self.x, mu, cov)
        np.testing.assert_allclose(parts.sum(1), audit.literal_score(self.x, mu, cov), atol=1e-12)
        np.testing.assert_allclose(energy.sum(1), np.square(self.x-mu).sum(1), atol=1e-12)
        self.assertTrue((parts >= 0).all())

    def test_invalid_covariance(self):
        with self.assertRaises(ValueError): m.sigma(np.zeros((8, 8)))
        with self.assertRaises(ValueError): m.sigma(np.full((8, 8), np.nan))

    def test_eigen_sign_invariance(self):
        mu = self.x.mean(0); vals, vec, parts, _ = m.spectrum(self.x, mu, np.cov(self.x.T))
        projected = np.square((self.x-mu) @ (-vec))/vals/8
        np.testing.assert_allclose(projected.reshape(64, 4, 2).sum(-1), parts)

    def test_centres_deterministic_and_monotonic(self):
        a, info = m.two_centres(self.x, self.w)
        b, again = m.two_centres(self.x, self.w)
        np.testing.assert_array_equal(a, b); self.assertEqual(info, again)
        self.assertTrue(np.all(np.diff(info["objective_history"]) <= 1e-10))
        self.assertEqual(info["status"], "converged")

    def test_empty_cluster_not_restarted(self):
        _, info = m.two_centres(np.ones((8, 4)), np.ones(8))
        self.assertEqual(info["status"], "empty_cluster")

    def test_invalid_centres_weights(self):
        with self.assertRaises(ValueError): m.two_centres(self.x, np.zeros(64))

    def test_centre_label_alignment(self):
        c, _ = m.two_centres(self.x, self.w)
        np.testing.assert_array_equal(m.align_centres(c, c[::-1]), c)

    def test_literal_distances(self):
        centres = self.x[[3, 9]]
        np.testing.assert_allclose(m.distances(self.x, centres), np.stack([np.square(self.x-c).sum(1) for c in centres], 1), atol=1e-12)

    def test_support_mass(self):
        centres, _ = m.two_centres(self.x, self.w)
        s = m.cluster_support(self.x, self.w, self.scenarios, centres)
        self.assertAlmostEqual(sum(s["mass"]), 1)
        self.assertEqual(sum(s["dominant_scenarios"]), 8)

    def test_scenario_not_window_mean(self):
        rows = [{"scenario": "a"}]*3+[{"scenario": "b"}]
        self.assertEqual(m.scenario_mean([0, 0, 0, 1], rows), .5)

    def test_complete_diagnosis_and_restore(self):
        model = m.old.fit((self.x, self.x), self.w, layer_width=4)
        states, report = m.diagnose(self.x, self.keys, self.scenarios, model, self.x[:5])
        self.assertEqual(states["scores"].shape, (5, 5))
        self.assertEqual(states["parts"].shape, (5, 5, 4))
        for i in range(5):
            np.testing.assert_allclose(states["scores"][i], audit.literal_score(self.x[:5], states["means"][i], states["covariances"][i]), atol=1e-12)
        restored = m.old.Model.restore(model.state())
        again, _ = m.diagnose(self.x, self.keys, self.scenarios, restored, self.x[:5])
        for name in states: np.testing.assert_array_equal(states[name], again[name])
        self.assertEqual(len(report["models"]), 5)


class BoundaryTests(unittest.TestCase):
    def test_query_roles_ties_and_missing_channels(self):
        meta = {f"k{f}": {"fold": f, "scenario": f"s{f}", "variant": "clean", "filter_pass": True,
                          "episode_index": 0} for f in range(3)}
        threshold = {"folds": {str(f): {"fit_keys": [f"k{(f+1)%3}"], "cal_keys": [f"k{(f+2)%3}"]} for f in range(3)}}
        raw = np.zeros((3, 6)); raw[:, 5] = [1, 3, 3]
        stream = {"ends": np.asarray([7, 8, 9]), "tags": np.asarray(["analysis"]*3), "raw": raw}
        data = {f: {k: stream for k in meta} for f in range(3)}
        tail = [{"key": "k0", "tag": "analysis", "end": 7}]
        rows, missing = run.selections(threshold, meta, data, tail)
        self.assertEqual(len(rows), 19); self.assertEqual(len(missing), 18)
        for row in rows:
            if row["kind"] != "old_tail": self.assertEqual(row["end"], 8)
            self.assertEqual(meta[row["key"]]["fold"], {"eval": row["fold"], "fit": (row["fold"]+1)%3, "cal": (row["fold"]+2)%3}[row["role"]])

    def test_guard_refuses_sealed_attack_and_mixed_score(self):
        g = run.Guard()
        paths = (run.ROOT/"artifacts/agent_v2/dataset_g/g_conf/trace.json",
                 run.ROOT/"artifacts/agent_v2/dataset_g/annotations/g_conf/final_unblinded.jsonl",
                 run.pf.CACHE/"topk_cache/g_dev/g-dev-001--attack--ep0.safetensors",
                 run.c0.OUT/"score/result.json")
        for p in paths:
            with self.assertRaises(PermissionError): g.check_path(p)

    def test_guard_allows_frozen_normal_model_and_own_audit(self):
        g = run.Guard()
        for p in (run.parent.OUT/"calibrate/fold0/model_analysis.npz", run.OUT/"audit/checks.json"):
            self.assertEqual(g.check_path(p), p.resolve())

    def test_detector_operations_tripwire(self):
        with self.assertRaises(AssertionError): run.prohibit()


if __name__ == "__main__": unittest.main()
