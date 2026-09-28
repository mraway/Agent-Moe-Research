"""A03 full-run synthetic tests. No dataset, cache or label reads."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from research_v2 import trm3_g
from research_v4 import codex_g_alg_a01_math as am, codex_g_alg_a01_eval as ae, codex_g_alg_a01_io as ai
from research_v4 import codex_g_alg_a02_math as pm
from research_v4 import codex_g_alg_a03_math as m, codex_g_alg_a03_eval as ev
from research_v4 import codex_g_alg_a03_run as run, codex_g_alg_a03_audit as audit


class A03FullTests(unittest.TestCase):
    def test_two_fars_and_thirteen_cells(self):
        self.assertEqual(tuple(m.CELLS), ev.NEW); self.assertEqual(len(ev.CELLS), 13)
        meta = {str(i): {"variant": "clean", "filter_pass": i < 100} for i in range(200)}
        ps = {k: np.ones((1, 13)) for k in meta}
        for i in range(5): ps[str(i)][:] = .05
        for i in range(100, 110): ps[str(i)][0, 12] = .05
        out = ev.freeze_points(meta, ps, {n: [99]*3 for n in ev.CELLS})
        self.assertAlmostEqual(out["points"]["matched"]["0.05"]["W_full"]["measured_far"], .05)
        self.assertLess(out["points"]["matched_all"]["0.05"]["W_full"]["alpha"], .05)
        self.assertEqual(len(out["grids"]["all"]["S"]), 101)

    def test_empty_and_no_alarm_points(self):
        meta = {"q": {"variant": "clean", "filter_pass": True}}
        counts = {n: [9]*3 for n in ev.CELLS}
        out = ev.freeze_points(meta, {"q": np.empty((0, 13))}, counts)
        self.assertEqual(out["grids"]["all"]["S"][-1]["measured_far"], 0)
        out = ev.freeze_points(meta, {"q": np.full((1, 13), .1)}, counts)
        self.assertEqual(out["points"]["budget"]["0.01"]["W_full"]["alpha"], 0)
        meta["q"]["variant"] = "attack"
        with self.assertRaises(ValueError): ev.freeze_points(meta, {}, counts)

    def test_shared_calibration_literal_z_p_and_no_double_window(self):
        grid = {"ends": np.arange(7, 27), "tags": np.array(["analysis"]*7+["final"]*13),
                "ordinals": np.r_[np.arange(7), np.arange(13)]}
        for name in m.CELLS:
            ss = [ae.stream(str(i), grid, np.linspace(0, 1, 20)+.03*i) for i in range(16)]
            c = ae.calibrate(name, ss[:8], ss[8:]); state = c.state_dict()
            z, p = ae.score(name, ss[0], trm3_g.calibration_from_state(state))
            literal = audit.literal_z({**grid, "raw": ss[0].scores[:, None]}, 0, state)
            np.testing.assert_array_equal(z, literal)
            maxima = state["channels"][name]["path_maxima"]
            np.testing.assert_array_equal(p, [(1+sum(m >= v for m in maxima))/(1+len(maxima)) for v in np.maximum.accumulate(z)])
            self.assertEqual(len(p), 20)

    def test_guards_and_restore_tripwires(self):
        g = run.Guard(True)
        for root in (run.OUT/"score", ai.OUT/"score", run.previous.OUT/"score", ai.BASE/"m7_full_episode_v1/score"):
            with self.assertRaises(PermissionError): g.check_path(root/"look_streams.npz")
        for normal in (True, False):
            with self.assertRaises(PermissionError): run.Guard(normal).check_path(ai.ROOT/"artifacts/agent_v2/dataset_g/g_conf/x.json")
        with patch.object(m, "fit", run.prohibit):
            with self.assertRaises(AssertionError): m.fit(None, None)

    def test_export_declares_resubstitution_and_full_fit_pool(self):
        s = {"ends": np.array([7]), "tags": np.array(["analysis"]), "ordinals": np.array([0]), "raw": np.ones((1, 6))}
        with tempfile.TemporaryDirectory(prefix="a03-export-") as d:
            p = Path(d)/"stream.jsonl"
            run.export(p, {"g_dev|q": s}, "W_full", 5, 0, "fit", ["g_dev|q", "donor"], {"analysis": {"sha256": "abc"}})
            r = json.loads(p.read_text())
            self.assertEqual(r["fit_pool_sha256"], ai.fit_hash(["g_dev|q", "donor"]))
            self.assertEqual(r["config"]["fit_scoring"], "resubstitution")
            self.assertEqual(r["config"]["condition"], ["channel"])
            self.assertEqual(r["config"]["structure"], "full")
            self.assertEqual(r["schema"], "dataset-g-look-scores-1.0.0")

    def test_fold_build_restore_audit_and_no_fitting(self):
        rng = np.random.default_rng(43); meta, grid, ps = {}, {}, {}
        for fold in range(3):
            for scenario in range(4):
                for ep in range(2):
                    k = f"f{fold}s{scenario}e{ep}"
                    meta[k] = {"fold": fold, "scenario": f"f{fold}s{scenario}", "episode_index": ep,
                               "variant": "clean", "filter_pass": True}
                    grid[k] = {"ends": np.arange(7, 12), "tags": np.array(["analysis"]*5), "ordinals": np.arange(5)}
                    ids = np.argsort(rng.random((24, 12, 32)), axis=-1)[..., :4]
                    ps[k] = am.representations(ids, rng.normal(size=(24, 12, 32)))
        fit = sorted(k for k in meta if meta[k]["fold"] == 1)
        bank = pm.build_bank([(meta[k]["scenario"], k, grid[k]["ends"], ps[k]) for k in fit])
        class Checked:
            def read(self, path, sha):
                body = path.read_bytes()
                if run.pf.digest(body) != sha: raise AssertionError("model changed")
                return body
        with tempfile.TemporaryDirectory(prefix="a03-fold-") as d, patch.object(run.pf, "routes", side_effect=lambda k, *args: ps[k]), patch.object(run, "OUT", Path(d)):
            a, b = Path(d)/"cal", Path(d)/"score"; a.mkdir(); b.mkdir()
            with patch.object(run.pre, "load_bank", return_value=(bank, {"fit_keys": fit, "sha256": "bank"})):
                normal, record = run.raw_fold(0, True, meta, grid, {}, {}, Checked(), a, run.Budget(), {})
            with patch.object(m, "fit", run.prohibit), patch.object(m, "moments", run.prohibit):
                restored, _ = run.raw_fold(0, False, meta, grid, {}, {}, Checked(), b, run.Budget(), {}, record)
            for k, s in restored.items(): np.testing.assert_array_equal(s["raw"], normal[k]["raw"])
            info = record["models"]["analysis"]
            model = m.Model.restore(ai.npz_bytes(Checked().read(Path(d)/info["path"], info["sha256"])))
            self.assertLess(audit.audit_model(bank, model), 1e-12)
            self.assertEqual(info["fit_scoring"], "resubstitution")

    def test_evaluation_pre_E_penalty_primary_and_pair_audit(self):
        meta, streams = {}, {}
        for i in range(15):
            k = f"g_dev|s{i}"; normal = i < 12
            meta[k] = {"variant": "clean" if normal else "attack", "filter_pass": True if normal else None,
                       "scenario": f"s{i}", "fold": i%3, "episode_index": 0, "length_group": "short", "token_count": 60,
                       "family": f"family{i%3}", "tier": "one", "domain_group": "d", "injection_channel": "user",
                       "e": None if normal else 10, "x": None if normal else 20, "complete_16": not normal,
                       "has_hit_look": not normal, "old_incomplete_16": False, "attack_bearing": not normal,
                       "silent": False, "trajectory_class": "on_task" if normal else "executed"}
            p = np.ones((33, 13))
            if i == 12: p[:] = .01
            if i == 13: p[8:] = .01
            streams[k] = {"ends": np.arange(7, 40), "p": p}
        wp = {n: {"alpha": .05} for n in ev.CELLS}
        result, ledger = ev.evaluate(meta, streams, {mode: {"0.05": wp} for mode in ("matched", "matched_all")}, {n: [9]*3 for n in ev.CELLS})
        for mode in ("matched", "matched_all"):
            r = result["readings"][mode]["0.05"]["W_full"]
            self.assertEqual(r["timely_recall"], {"count": 1, "n": 3, "rate": 1/3})
            self.assertEqual(r["classification"]["pre_E"], 1)
            self.assertEqual(ledger[f"{mode}/0.05"]["W_full"]["g_dev|s13"], 15)
        self.assertFalse(result["primary_gain_gate"]["pass"])
        self.assertEqual(result["primary_gain_gate"]["comparison"], "matched_all/0.05/W_full_minus_W_diag")
        self.assertEqual(audit.audit_pairs(meta, result), 30)

    def test_budget_does_not_inherit_a02_extension(self):
        with self.assertRaises(RuntimeError): run.Budget(601).check()
        with patch.object(run.pf, "rss", return_value=2.01):
            with self.assertRaises(RuntimeError): run.Budget().check()


if __name__ == "__main__": unittest.main()
