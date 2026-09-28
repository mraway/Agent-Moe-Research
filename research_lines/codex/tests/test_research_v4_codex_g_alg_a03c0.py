"""Synthetic only: no dataset/artifact access, no model construction or downloads."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from research_v2 import trm3_g
from research_v4 import codex_g_alg_a01_eval as ae, codex_g_alg_a01_io as ai
from research_v4 import codex_g_alg_a03c0_run as run, codex_g_alg_a03c0_eval as ev
from research_v4 import codex_g_alg_a03c0_audit as audit


def stream(key, scores, tags=None, ordinals=None):
    scores = np.asarray(scores); n = len(scores)
    grid = {"ends": np.arange(7, 7+n), "tags": np.array(tags or ["analysis"]*n),
            "ordinals": np.arange(n) if ordinals is None else np.asarray(ordinals)}
    return ae.stream(key, grid, scores)


class C0Tests(unittest.TestCase):
    def test_identity_shared_calibration_literal_and_no_second_window(self):
        fit = [stream(f"f{i}", np.arange(20)*.1+i) for i in range(8)]
        cal = [stream(f"c{i}", np.arange(20)*.2+i) for i in range(8)]
        for name in ev.NEW:
            with patch.object(trm3_g, "fit_channel_standardiser", run.prohibit):
                c = run.calibrate_identity(name, fit, cal)
            q = stream("q", np.linspace(0, 12, 20))
            z, p = ae.score(name, q, c)
            np.testing.assert_array_equal(z, q.scores)
            np.testing.assert_array_equal(p, audit.literal_p(q.scores, [s.scores.max() for s in cal]))
            self.assertEqual(len(p), len(q.scores)); self.assertEqual(c.horizon["censored_endpoints"], 0)

    def test_fit_values_do_not_learn_scale(self):
        cal = [stream(f"c{i}", [1., 2.+i]) for i in range(5)]
        a = run.calibrate_identity(ev.NEW[0], [stream("fit", [1., 2.])], cal)
        b = run.calibrate_identity(ev.NEW[0], [stream("fit", [1e9, -2e9])], cal)
        self.assertEqual(a.state_dict(), b.state_dict())

    def test_channel_and_ordinal_invariance_at_fixed_raw(self):
        c = run.calibrate_identity(ev.NEW[1], [stream("f", [0., 1.])], [stream("c", [1., 2.])])
        a = stream("a", [0., 1., 4.]); b = stream("b", [0., 1., 4.], ["commentary", "final", "analysis"], [0, 31, 999])
        for x, y in zip(ae.score(ev.NEW[1], a, c), ae.score(ev.NEW[1], b, c)): np.testing.assert_array_equal(x, y)

    def test_future_append_never_changes_prefix(self):
        c = run.calibrate_identity(ev.NEW[2], [stream("f", [1., 3.])], [stream(f"c{i}", [1., float(i)]) for i in range(4)])
        short = ae.score(ev.NEW[2], stream("s", [1., 2., 1.]), c)[1]
        long = ae.score(ev.NEW[2], stream("l", [1., 2., 1., 99., 0.]), c)[1]
        np.testing.assert_array_equal(short, long[:3])

    def test_ties_floor_empty_and_nonfinite(self):
        np.testing.assert_array_equal(audit.literal_p([1., 2., 3.], [1., 2.]), [1., 2/3, 1/3])
        self.assertEqual(len(audit.literal_p([], [1.])), 0)
        with self.assertRaises(ValueError): audit.literal_p([1.], [])
        with self.assertRaises(ValueError): stream("x", [np.nan])

    def test_restore_without_recalibration(self):
        name = ev.NEW[0]
        c = run.calibrate_identity(name, [stream("f", [1., 2.])], [stream("c", [2., 4.])])
        body = json.loads(json.dumps(c.state_dict()))
        with patch.object(trm3_g, "calibrate_g", run.prohibit):
            restored = trm3_g.calibration_from_state(body)
            np.testing.assert_array_equal(ae.score(name, stream("q", [3., 9.]), c)[1], ae.score(name, stream("q", [3., 9.]), restored)[1])

    def test_guards_and_training_tripwires(self):
        for normal in (True, False):
            g = run.Guard(normal)
            for p in (run.ROOT/"artifacts/agent_v2/dataset_g/g_conf/x.json", run.OLD/"calibrate/fold0/model_analysis.npz",
                      run.pf.CACHE/"topk_cache/g_dev/x.safetensors", run.pf.GDEV/"x/trace.json"):
                with self.assertRaises(PermissionError): g.check_path(p)
        for p in (run.OLD/"score/look_streams.npz", run.OUT/"score/result.json", run.OUT/"audit/checks.json"):
            with self.assertRaises(PermissionError): run.Guard(True).check_path(p)
        with self.assertRaises(AssertionError): run.prohibit()

    def test_roles_normal_quality_and_cross_scenario(self):
        meta = {f"k{f}": {"fold": f, "scenario": f"s{f}", "variant": "clean", "filter_pass": True} for f in range(3)}
        meta["bad"] = {"fold": 1, "scenario": "bad", "variant": "clean", "filter_pass": False}
        record = {"fit_keys": ["k1"], "cal_keys": ["k2"]}
        self.assertEqual(run.role_keys(meta, record, 0), {"fit": ["k1"], "cal": ["k2"], "eval": ["k0"]})
        meta["k2"]["scenario"] = "s1"
        with self.assertRaises(ValueError): run.role_keys(meta, record, 0)

    def test_sixteen_cells_and_all_vs_filtered_workpoints(self):
        self.assertEqual(len(ev.CELLS), 16)
        meta = {str(i): {"variant": "clean", "filter_pass": i < 100} for i in range(200)}
        ps = {k: np.ones((1, 16)) for k in meta}
        for i in range(5): ps[str(i)][:] = .05
        for i in range(100, 110): ps[str(i)][0, 15] = .05
        out = ev.freeze_points(meta, ps, {n: [99]*3 for n in ev.CELLS})
        self.assertEqual(out["points"]["matched"]["0.05"][ev.NEW[-1]]["measured_far"], .05)
        self.assertLess(out["points"]["matched_all"]["0.05"][ev.NEW[-1]]["alpha"], .05)
        meta["0"]["variant"] = "attack"
        with self.assertRaises(ValueError): ev.freeze_points(meta, ps, {})

    def test_disabled_empty_and_raw_export_contract(self):
        meta = {"q": {"variant": "clean", "filter_pass": True}}
        r = ev.freeze_points(meta, {"q": np.empty((0, 16))}, {n: [9]*3 for n in ev.CELLS})
        self.assertEqual(r["grids"]["all"]["S"][-1]["measured_far"], 0)
        s = {"ends": np.array([7]), "tags": np.array(["final"]), "ordinals": np.array([0]), "raw": np.ones((1, 3))}
        with tempfile.TemporaryDirectory(prefix="c0-export-") as folder:
            path = Path(folder)/"test.jsonl"; run.export(path, {"g_dev|q": s}, ["g_dev|q"], ev.NEW[-1], 2, 0, "eval", ["fit"])
            row = json.loads(path.read_text())
            self.assertEqual(row["schema"], "dataset-g-look-scores-1.0.0")
            self.assertIs(row["config"]["standardise"], False); self.assertEqual(row["fit_pool_sha256"], ai.fit_hash(["fit"]))

    def test_prefix_penalty_primary_comparison_and_pair_audit(self):
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
        self.assertEqual(result["primary_gain_gate"]["comparison"], "matched_all/0.01/W_full_raw_minus_W_full")
        self.assertEqual(audit.prior.audit_pairs(meta, result), 24)
        self.assertEqual(result["early_deadlines_matched_all_descriptive"]["0.05"][ev.NEW[-1]]["-8"]["count"], 0)

    def test_combine_needs_all_keys_and_does_not_modify_old_columns(self):
        s = {"ends": np.array([7]), "tags": np.array(["final"]), "ordinals": np.array([0]),
             **{f: np.ones((1, 13)) for f in ("raw", "z", "p")}}
        fresh = {"x": {f: np.zeros((1, 3)) for f in ("raw", "z", "p")}}
        merged = run.combine({"x": s}, fresh)["x"]
        np.testing.assert_array_equal(merged["p"][:, :13], s["p"]); self.assertEqual(merged["p"].shape, (1, 16))
        with self.assertRaises(ValueError): run.combine({"x": s}, {})

    def test_default_resource_gates(self):
        with self.assertRaises(RuntimeError): run.parent.Budget(601).check()
        with patch.object(pf := run.pf, "rss", return_value=2.01):
            with self.assertRaises(RuntimeError): run.parent.Budget().check()


if __name__ == "__main__": unittest.main()
