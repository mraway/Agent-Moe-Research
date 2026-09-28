"""Synthetic A03-R tests; no experimental data, GPU, downloads, or dataset labels."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from research_v2 import trm3_g
from research_v4 import codex_g_alg_a01_eval as ae, codex_g_alg_a01_io as ai
from research_v4 import codex_g_alg_a03_math as old
from research_v4 import codex_g_alg_a03r_math as math, codex_g_alg_a03r_run as run
from research_v4 import codex_g_alg_a03r_eval as ev, codex_g_alg_a03r_audit as audit


def stream(key, values):
    n = len(values)
    return ae.stream(key, {"ends": np.arange(7, 7+n), "tags": np.array(["analysis"]*n), "ordinals": np.arange(n)}, values)


class RTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(173)
        roots = self.rng.normal(size=(30, 8))*np.array([.0001, .001, .01, .1, 1., 2., 3., 5.])
        self.parent = old.fit([roots, roots], np.full(30, 1/30), layer_width=2)
        self.model, self.diag = math.derive(self.parent.mu[1], self.parent.cov[1], layer_width=2)

    def test_constant_and_expected_floor(self):
        self.assertEqual(math.FLOOR, .1)
        v = np.trace(self.parent.cov[1])/8
        self.assertEqual(self.diag["lower"], .1*v)
        self.assertGreater(self.diag["full"]["raised"], 0)
        self.assertLess(self.diag["full"]["condition_after"], self.diag["full"]["condition_before"])

    def test_floor_keeps_large_directions_and_caps_inverse(self):
        a = np.diag([.001, .2, 5.]); w, info = math.floored(a, .1)
        np.testing.assert_allclose(w @ w.T, np.diag([10., 5., .2]), rtol=1e-12)
        self.assertEqual(info["raised"], 1)

    def test_full_rotation_equivariance(self):
        a = np.diag([.001, .2, 5.]); rotation, _ = np.linalg.qr(self.rng.normal(size=(3, 3)))
        w, _ = math.floored(a, .1); rotated, _ = math.floored(rotation @ a @ rotation.T, .1)
        np.testing.assert_allclose(rotated @ rotated.T, rotation @ (w @ w.T) @ rotation.T, atol=1e-12)

    def test_joint_structure_not_diagonalized(self):
        w, _ = math.floored(np.array([[1., .9], [.9, 1.]]), .2)
        self.assertGreater(abs((w @ w.T)[0, 1]), .1)

    def test_all_coefficients_and_independent_solve(self):
        matrices = audit.audit_model(self.model, self.parent)
        q = self.rng.normal(size=(13, 8))
        np.testing.assert_allclose(math.score(q, self.model), audit.solve_scores(q, self.model.mu, matrices), rtol=1e-9, atol=1e-10)

    def test_no_penalty_increase(self):
        q = self.rng.normal(size=(29, 8)); before = old.score([q, q], self.parent)[:, [1, 3, 5]]
        after = math.score(q, self.model)
        self.assertTrue(np.all(after <= before+1e-10))

    def test_mean_and_cov_not_refit(self):
        np.testing.assert_array_equal(self.model.mu, self.parent.mu[1])
        np.testing.assert_array_equal(self.model.cov, self.parent.cov[1])
        np.testing.assert_array_equal(math.score(self.model.mu[None, :], self.model), np.zeros((1, 3)))

    def test_singular_covariance_supported_after_original_regularization(self):
        model, _ = math.derive(np.zeros(8), np.ones((8, 8)), 2)
        self.assertTrue(np.isfinite(math.score(np.ones((2, 8)), model)).all())

    def test_common_unit_change_invariance(self):
        scaled, _ = math.derive(3*self.model.mu, 9*self.model.cov, 2)
        q = self.rng.normal(size=(7, 8))
        np.testing.assert_allclose(math.score(3*q, scaled), math.score(q, self.model), rtol=1e-10, atol=1e-10)

    def test_disk_restore_exact_and_restore_only(self):
        q = self.rng.normal(size=(9, 8))
        with tempfile.TemporaryDirectory(prefix="a03r-state-") as tmp:
            path = Path(tmp)/"state.npz"; ai.save_npz(path, **self.model.state())
            with patch.object(math, "derive", run.prohibit), patch.object(math, "floored", run.prohibit):
                restored = math.Model.restore(ai.npz_bytes(path.read_bytes()))
                np.testing.assert_array_equal(math.score(q, restored), math.score(q, self.model))

    def test_invalid_values_shapes_and_parameters_rejected(self):
        for a in (np.array([[np.nan]]), np.array([[0.]]), np.array([[1., 2.], [0., 1.]])):
            with self.assertRaises(ValueError): math.floored(a, .1)
        for lower in (0., -1., np.nan):
            with self.assertRaises(ValueError): math.floored(np.eye(2), lower)
        with self.assertRaises(ValueError): math.derive(np.zeros(8), np.zeros((8, 8)), 2)
        with self.assertRaises(ValueError): math.derive(np.zeros(7), np.eye(7), 2)
        state = self.model.state(); state["floor"] = np.asarray(.2)
        with self.assertRaises(ValueError): math.Model.restore(state)
        with self.assertRaises(ValueError): math.score(np.full((1, 8), np.nan), self.model)
        self.assertEqual(math.score(np.empty((0, 8)), self.model).shape, (0, 3))

    def test_future_append_preserves_raw_prefix_and_shared_p(self):
        q = self.rng.normal(size=(512, 8))
        np.testing.assert_array_equal(math.score(q[:256], self.model), math.score(q, self.model)[:256])
        for name in ev.NEW:
            fit = [stream(f"fit{i}", np.arange(35)*.1+i) for i in range(12)]
            cal = [stream(f"cal{i}", np.arange(35)*.2+i) for i in range(12)]
            c = ae.calibrate(name, fit, cal)
            with patch.object(trm3_g, "calibrate_g", run.prohibit):
                restored = trm3_g.calibration_from_state(json.loads(json.dumps(c.state_dict())))
                short = ae.score(name, stream("q", [1., 2., 3.]), restored)[1]
                long = ae.score(name, stream("q", [1., 2., 3., 99., 0.]), restored)[1]
                np.testing.assert_array_equal(short, long[:3])
            self.assertTrue(c.state_dict()["standardiser"]["stats"])

    def test_rank_ties_and_empty(self):
        np.testing.assert_array_equal(audit.literal_p([1., 2., 3.], [1., 2.]), [1., 2/3, 1/3])
        self.assertEqual(len(audit.literal_p([], [1.])), 0)
        with self.assertRaises(ValueError): audit.literal_p([1.], [])

    def test_guards_and_training_tripwire(self):
        for normal in (True, False):
            g = run.Guard(normal)
            with self.assertRaises(PermissionError): g.check_path(run.ROOT/"artifacts/agent_v2/dataset_g/g_conf/trace.json")
        for path in (run.parent.OUT/"score/look_streams.npz", run.OUT/"score/result.json", run.OUT/"audit/checks.json",
                     run.pf.CACHE/"topk_cache/g_dev/g-dev-001--attack--ep0.safetensors"):
            with self.assertRaises(PermissionError): run.Guard(True).check_path(path)
        with self.assertRaises(AssertionError): run.prohibit()

    def test_roles_quality_and_scenario_separation(self):
        meta = {f"k{f}": {"fold": f, "scenario": f"s{f}", "variant": "clean", "filter_pass": True} for f in range(3)}
        meta["bad"] = {"fold": 1, "scenario": "bad", "variant": "clean", "filter_pass": False}
        record = {"fit_keys": ["k1"], "cal_keys": ["k2"]}
        self.assertEqual(run.c0.role_keys(meta, record, 0), {"fit": ["k1"], "cal": ["k2"], "eval": ["k0"]})
        meta["k2"]["scenario"] = "s1"
        with self.assertRaises(ValueError): run.c0.role_keys(meta, record, 0)

    def test_all_and_filtered_matching_differ_and_empty_denominator_retained(self):
        self.assertEqual(len(ev.CELLS), 16)
        meta = {str(i): {"variant": "clean", "filter_pass": i < 100} for i in range(200)}
        ps = {k: np.ones((1, 16)) for k in meta}
        for i in range(5): ps[str(i)][:] = .05
        for i in range(100, 110): ps[str(i)][0, 15] = .05
        out = ev.freeze_points(meta, ps, {n: [99]*3 for n in ev.CELLS})
        self.assertEqual(out["points"]["matched"]["0.05"][ev.NEW[-1]]["measured_far"], .05)
        self.assertLess(out["points"]["matched_all"]["0.05"][ev.NEW[-1]]["alpha"], .05)
        empty = ev.freeze_points({"q": {"variant": "clean", "filter_pass": True}}, {"q": np.empty((0, 16))}, {n: [9]*3 for n in ev.CELLS})
        self.assertEqual(empty["grids"]["all"]["S"][-1]["measured_far"], 0)
        meta["0"]["variant"] = "attack"
        with self.assertRaises(ValueError): ev.freeze_points(meta, ps, {})

    def test_shared_export_contract(self):
        s = {"ends": np.array([7]), "tags": np.array(["final"]), "ordinals": np.array([0]), "raw": np.ones((1, 3))}
        with tempfile.TemporaryDirectory(prefix="a03r-export-") as tmp:
            path = Path(tmp)/"out.jsonl"; run.export(path, {"g_dev|q": s}, ["g_dev|q"], ev.NEW[-1], 2, 0, "eval", ["fit"], {})
            row = json.loads(path.read_text())
            self.assertEqual(row["schema"], "dataset-g-look-scores-1.0.0")
            self.assertEqual(row["config"]["floor_ratio"], .1); self.assertTrue(row["config"]["standardise"])
            self.assertEqual(row["fit_pool_sha256"], ai.fit_hash(["fit"]))

    def test_primary_pre_e_penalty_and_early_columns(self):
        meta, streams = {}, {}
        for i in range(15):
            k = f"g_dev|s{i}"; normal = i < 12
            meta[k] = {"variant": "clean" if normal else "attack", "filter_pass": normal,
                       "scenario": f"s{i}", "fold": i%3, "episode_index": 0, "length_group": "short", "token_count": 60,
                       "family": f"family{i%3}", "tier": "one", "domain_group": "d", "injection_channel": "user",
                       "e": None if normal else 10, "x": None if normal else 20, "complete_16": not normal,
                       "has_hit_look": not normal, "old_incomplete_16": False, "attack_bearing": not normal,
                       "silent": False, "trajectory_class": "on_task" if normal else "executed"}
            p = np.ones((33, 16))
            if i == 12: p[:] = .001
            if i == 13: p[8:] = .001
            streams[k] = {"ends": np.arange(7, 40), "p": p}
        wp = {mode: {level: {n: {"alpha": float(level)} for n in ev.CELLS} for level in ("0.01", "0.05")}
              for mode in ("matched", "matched_all")}
        result, ledger = ev.evaluate(meta, streams, wp, {n: [9]*3 for n in ev.CELLS})
        self.assertEqual(result["readings"]["matched_all"]["0.01"][ev.NEW[-1]]["timely_recall"]["count"], 1)
        self.assertFalse(result["primary_gain_gate"]["pass"])
        self.assertEqual(result["primary_gain_gate"]["comparison"], "matched_all/0.01/W_full_floor_minus_W_full")
        self.assertEqual(audit.prior.audit_pairs(meta, result), 24)
        self.assertEqual(result["early_deadlines_matched_all_descriptive"]["0.05"][ev.NEW[-1]]["-8"]["count"], 0)


if __name__ == "__main__": unittest.main()
