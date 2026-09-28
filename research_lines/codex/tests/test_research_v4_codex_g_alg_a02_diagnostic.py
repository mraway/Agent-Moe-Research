import unittest

import numpy as np

from research_v4 import codex_g_alg_a02_diagnostic as d


class SelectionTests(unittest.TestCase):
    def fixture(self):
        meta, grids = {}, {}
        for key, scenario in (("target", "t"), ("a", "a"), ("a2", "a"), ("b", "b"), ("c", "c"), ("d", "d")):
            meta[key] = dict(scenario=scenario, fold=0, episode_index=1, variant="clean", filter_pass=True)
            grids[key] = dict(ends=np.array([17, 18]), tags=np.array(["final", "final"]),
                              ordinals=np.array([10, 11]), raw=np.array([100., -100.]))
        return meta, grids

    def test_structural_selection_distinct_scenario_and_stable_ties(self):
        meta, grids = self.fixture()
        result = d.select_controls(meta, grids, "target", 18, {"t"})
        self.assertEqual([c["key"] for c in result["controls"]], ["a", "b", "c"])
        self.assertEqual([c["end"] for c in result["controls"]], [18, 18, 18])

    def test_scores_never_enter_selection(self):
        meta, grids = self.fixture()
        before = d.select_controls(meta, grids, "target", 18, {"t"})
        for s in grids.values():
            s["raw"][:] = np.nan
            s["p"] = np.array([0., 1.])
        self.assertEqual(before, d.select_controls(meta, grids, "target", 18, {"t"}))

    def test_wrong_fold_round_arm_and_quality_excluded(self):
        meta, grids = self.fixture()
        meta["a"]["fold"] = 1
        meta["a2"]["episode_index"] = 0
        meta["b"]["variant"] = "benign_control"
        meta["c"]["filter_pass"] = False
        result = d.select_controls(meta, grids, "target", 18, {"t"})
        self.assertEqual(result["status"], "insufficient")
        self.assertEqual([c["key"] for c in result["controls"]], ["d"])

    def test_ordinal_precedes_global_endpoint(self):
        meta, grids = self.fixture()
        grids["a"]["ends"] = np.array([107, 108])
        grids["b"]["ordinals"] = np.array([0, 1])
        result = d.select_controls(meta, grids, "target", 18, {"t"}, count=4)
        self.assertLess([c["key"] for c in result["controls"]].index("a2"),
                        [c["key"] for c in result["controls"]].index("b"))


class NumericTests(unittest.TestCase):
    def bank(self):
        p = np.zeros((4, 24, 32), dtype=np.float32)
        for i in range(4):
            p[i, :, i] = 1.
        roots = np.sqrt(p/24).reshape(4, -1)
        return d.am.Bank((roots, roots), (roots, roots), np.array([7, 7, 7, 7]),
                         ("a", "b", "c", "d"), np.arange(4), np.array(["a", "b", "c", "d"]), np.array([7]*4))

    def test_float64_neighbours_exclude_whole_scenario(self):
        bank = self.bank()
        score, rows, distances = d.direct_neighbours(bank.mean_roots[0][0], bank, 0, "a")
        self.assertEqual(rows, [1, 2, 3])
        self.assertAlmostEqual(score, 1., places=6)
        self.assertEqual(distances[0], 0.)

    def test_layer_coordinate_contributions_sum_to_hellinger(self):
        q = np.zeros((8, 24, 32), dtype=np.float32)
        donor = q.copy(); q[:, :, 0] = 1.; donor[:, :, 1] = 1.
        terms = d.distance_contributions(q, donor)
        self.assertAlmostEqual(float(terms.sum()), 1.)
        np.testing.assert_allclose(terms.sum(-1), np.full(24, 1/24))
        np.testing.assert_array_equal(d.distance_contributions(q, q), np.zeros((24, 32)))

    def test_actual_selected_weights_not_full_softmax(self):
        ids = np.broadcast_to(np.array([0, 1, 2, 3]), (24, 8, 4)).copy()
        logits = np.zeros((24, 8, 32), dtype=np.float32)
        logits[:, :, 0] = np.log(2.); logits[:, :, 31] = 100.
        u, w = d.am.representations(ids, logits)
        self.assertAlmostEqual(float(w[0, 0, 0]), .4, places=6)
        self.assertEqual(float(w[0, 0, 31]), 0.)
        self.assertEqual(float(u[0, 0, 0]), .25)

    def test_radius_uses_fit_donor_and_excludes_scenario(self):
        bank = self.bank()
        streams = {"a": dict(ends=np.array([7]), raw=np.array([[1., 1.]]),
                             donor_rows=np.array([[[1, 2, 3], [1, 2, 3]]]))}
        meta = {k: {"scenario": k} for k in ("a", "b", "c", "d")}
        result = d.local_radius(bank, 0, 0, streams, {"a"}, meta)
        self.assertEqual(result["value"], 1.)
        with self.assertRaises(ValueError):
            d.local_radius(bank, 0, 0, streams, set(), meta)
        streams["a"]["donor_rows"][0, 0, 0] = 0
        with self.assertRaises(ValueError):
            d.local_radius(bank, 0, 0, streams, {"a"}, meta)

    def test_bucket_pooled_fallback_uses_global_index(self):
        b = dict(bucket_size=32, cap=1, mu=[0., 1.], sd=[2., 4.],
                 trace_counts=[30, 30], window_counts=[100, 100], reused_buckets=[])
        state = {"standardiser": {"stats": {"final": b}, "pooled": b}}
        self.assertEqual(d.bucket_parameters(state, "final", 0, 50)["bucket"], 0)
        pooled = d.bucket_parameters(state, "analysis", 0, 50)
        self.assertEqual(pooled["bucket"], 1)
        self.assertTrue(pooled["pooled_fallback"])

    def test_running_max_p_replay_and_ties(self):
        b = dict(bucket_size=32, cap=0, mu=[0.], sd=[2.], trace_counts=[30], window_counts=[100], reused_buckets=[])
        state = {"standardiser": {"stats": {"final": b}, "pooled": b},
                 "channels": {"U_mean": {"path_maxima": [-1., 1., 3.]}}}
        s = dict(raw=np.array([[2.], [0.], [8.]]), z=np.array([[1.], [0.], [4.]]),
                 p=np.array([[.75], [.75], [.25]]), tags=np.array(["final"]*3), ordinals=np.arange(3))
        _, running, _ = d.replay(state, s, 0, "U_mean")
        np.testing.assert_array_equal(running, [1., 1., 4.])

    def test_nonfinite_values_rejected(self):
        with self.assertRaises(ValueError):
            d.quantiles([1., np.nan])

    def test_mixed_scored_artifact_guard(self):
        guard = d.Guard(d.ROOT)
        with self.assertRaises(PermissionError):
            guard.check_path(d.OLD/"score/result.json")
        with self.assertRaises(PermissionError):
            guard.check_path(d.ROOT/"artifacts/agent_v2/dataset_g/g_conf/trace.json")


if __name__ == "__main__":
    unittest.main()
