"""A02 synthetic tests only; no experimental traces, caches or labels are read."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from research_v2 import trm3_g
from research_v4 import codex_g_alg_a01_math as old, codex_g_alg_a01_eval as ae
from research_v4 import codex_g_alg_a01_io as ai
from research_v4 import codex_g_alg_a02_math as math, codex_g_alg_a02_eval as ev
from research_v4 import codex_g_alg_a02_run as run, codex_g_alg_a02_audit as audit


def records():
    rng = np.random.default_rng(37); rows = []
    for i in range(6):
        ids = np.argsort(rng.random((24, 12, 32)), axis=-1)[..., :4]
        ps = old.representations(ids, rng.normal(size=(24, 12, 32)))
        rows.append((f"s{i//2}" if i < 4 else f"s{i}", f"k{i}", np.arange(7, 12), ps))
    return rows


class A02Tests(unittest.TestCase):
    def test_mean_kernel_equivalent_to_old_on_same_bank(self):
        rs = records(); b = math.build_bank(rs); ob = old.build_bank(rs)
        a = math.neighbour_scores(rs[0][3], np.arange(7, 12), b, "s0")
        c = old.neighbour_scores(rs[0][3], np.arange(7, 12), ob, "s0")
        for x, y in zip(a, c): np.testing.assert_allclose(x, y[:, :2], rtol=0, atol=2e-6)

    def test_exclude_all_arms_rounds_of_scenario(self):
        rs = records(); b = math.build_bank(rs)
        _, rows, _ = math.neighbour_scores(rs[0][3], [7], b, "s0")
        groups = b.scenario_index[rows]
        self.assertFalse(any(b.scenarios[g] == "s0" for g in groups.ravel()))
        self.assertTrue(np.all(np.diff(np.sort(groups, axis=-1), axis=-1) > 0))

    def test_independent_enum_and_restore(self):
        rs = records(); b = math.build_bank(rs); restored = math.Bank.restore(b.state())
        a = math.neighbour_scores(rs[1][3], [7, 11], b, "outside")
        c = math.neighbour_scores(rs[1][3], [7, 11], restored, "outside")
        for x, y in zip(a, c): np.testing.assert_array_equal(x, y)
        self.assertLess(run.nearest_audit(rs[1][3], 7, b, "outside", a[0][0], a[1][0], a[2][0]), 2e-6)

    def test_union_distance_cannot_increase(self):
        rs = records(); subset = [rs[i] for i in (0, 2, 4)]
        a = math.neighbour_scores(rs[5][3], [7, 11], math.build_bank(subset), "outside")[0]
        b = math.neighbour_scores(rs[5][3], [7, 11], math.build_bank(rs), "outside")[0]
        self.assertTrue(np.all(b <= a+2e-6))

    def test_insufficient_distinct_scenarios_and_bad_blocks(self):
        rs = records()
        with self.assertRaises(ValueError): math.build_bank(rs[:4])
        b = math.build_bank([rs[i] for i in (0, 2, 4)])
        with self.assertRaises(ValueError): math.neighbour_scores(rs[0][3], [7], b, "s0")
        with self.assertRaises(ValueError): math.neighbour_scores(rs[0][3], [7], b, "outside", bank_block=0)

    def test_causal_prefix_and_block_tolerance(self):
        rs = records(); b = math.build_bank(rs); ps = rs[0][3]
        a = math.neighbour_scores(ps, [7, 8], b, "outside")[0]
        c = math.neighbour_scores(tuple(p[:9] for p in ps), [7, 8], b, "outside", query_block=1, bank_block=3)[0]
        np.testing.assert_allclose(a, c, atol=2e-6, rtol=0)

    def test_normal_and_all_matching_use_different_denominators(self):
        meta = {str(i): {"variant": "clean", "filter_pass": i < 100} for i in range(200)}
        ps = {k: np.ones((1, 7)) for k in meta}
        for i in range(5): ps[str(i)][0, :] = .05
        for i in range(100, 110): ps[str(i)][0, 6] = .05
        out = ev.freeze_points(meta, ps, {n: [99]*3 for n in ev.CELLS})
        self.assertAlmostEqual(out["points"]["matched"]["0.05"]["W_pool"]["measured_far"], .05)
        self.assertLess(out["points"]["matched_all"]["0.05"]["W_pool"]["alpha"], .05)
        self.assertEqual(len(out["grids"]["all"]["S"]), 101)

    def test_normal_only_empty_and_disabled_points(self):
        meta = {"q": {"variant": "clean", "filter_pass": True}}
        counts = {n: [9]*3 for n in ev.CELLS}
        out = ev.freeze_points(meta, {"q": np.empty((0, 7))}, counts)
        self.assertEqual(out["grids"]["all"]["S"][-1]["measured_far"], 0)
        out = ev.freeze_points(meta, {"q": np.full((1, 7), .1)}, counts)
        self.assertEqual(out["points"]["budget"]["0.01"]["W_pool"]["alpha"], 0)
        meta["q"]["variant"] = "attack"
        with self.assertRaises(ValueError): ev.freeze_points(meta, {}, counts)

    def test_calibration_restore_literal_z_p_and_no_resmoothing(self):
        grid = {"ends": np.arange(7, 27), "tags": np.array(["analysis"]*7+["final"]*13),
                "ordinals": np.r_[np.arange(7), np.arange(13)]}
        for name in math.CELLS:
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
        for root in (run.OUT/"score", ai.OUT/"score", ai.BASE/"m7_full_episode_v1/score"):
            with self.assertRaises(PermissionError): g.check_path(root/"look_streams.npz")
        for normal in (True, False):
            with self.assertRaises(PermissionError): run.Guard(normal).check_path(ai.ROOT/"artifacts/agent_v2/dataset_g/g_conf/x.json")
        with patch.object(math, "build_bank", run.prohibit):
            with self.assertRaises(AssertionError): math.build_bank(records())

    def test_export_only_channel_and_exclude_entire_scenario(self):
        meta = {"g_dev|q": {"scenario": "same"}, "other_round": {"scenario": "same"}, "donor": {"scenario": "new"}}
        s = {"ends": np.array([7]), "tags": np.array(["analysis"]), "ordinals": np.array([0]), "raw": np.ones((1, 2))}
        with tempfile.TemporaryDirectory(prefix="a02-export-") as d:
            p = Path(d)/"stream.jsonl"
            run.export(p, {"g_dev|q": s}, "W_pool", 1, 0, "fit", list(meta), meta, {"analysis": {"sha256": "abc"}})
            r = json.loads(p.read_text()); self.assertEqual(r["fit_pool_sha256"], ai.fit_hash(["donor"]))
            self.assertEqual(r["config"]["condition"], ["channel"])
            self.assertEqual(r["schema"], "dataset-g-look-scores-1.0.0")

    def test_support_pools_all_rounds_and_scenarios(self):
        meta, grid = {}, {}
        for fold in range(3):
            for s in range(4):
                for ep in range(2):
                    k = f"f{fold}s{s}e{ep}"
                    meta[k] = {"fold": fold, "scenario": f"f{fold}s{s}", "episode_index": ep, "variant": "clean", "filter_pass": True}
                    grid[k] = {"tags": ("analysis",)}  # tuple regression: must not compare tuple to scalar
        out = math.support_audit(meta, grid)
        self.assertEqual(out["unsupported_queries"], [])
        for r in out["banks"].values():
            b = r["conditions"]["analysis"]
            self.assertEqual((b["windows"], b["scenarios"], b["minimum_scenarios_after_exclusion"]), (8, 4, 3))

    def test_full_fold_build_then_restore_without_fitting(self):
        meta, grids, ps = {}, {}, {}
        base = records()
        for fold in range(3):
            for scenario in range(4):
                for ep in range(2):
                    k = f"f{fold}s{scenario}e{ep}"
                    meta[k] = {"fold": fold, "scenario": f"f{fold}s{scenario}", "episode_index": ep,
                               "variant": "clean", "filter_pass": True}
                    grids[k] = {"ends": np.arange(7, 12), "tags": np.array(["analysis"]*5), "ordinals": np.arange(5)}
                    ps[k] = base[(scenario+ep)%len(base)][3]
        class Checked:
            def read(self, path, sha):
                body = path.read_bytes()
                if run.pf.digest(body) != sha: raise AssertionError("bank changed")
                return body
        with tempfile.TemporaryDirectory(prefix="a02-fold-") as d, patch.object(run.pf, "routes", side_effect=lambda k, *args: ps[k]), patch.object(run, "OUT", Path(d)):
            a, b = Path(d)/"cal", Path(d)/"score"; a.mkdir(); b.mkdir()
            normal, record = run.raw_fold(0, True, meta, grids, {}, {}, Checked(), a, run.Budget())
            with patch.object(math, "build_bank", run.prohibit):
                restored, _ = run.raw_fold(0, False, meta, grids, {}, {}, Checked(), b, run.Budget(), record)
            for k, s in restored.items():
                for field in ("raw", "donor_rows", "donor_distances"):
                    np.testing.assert_array_equal(s[field], normal[k][field])
            self.assertEqual(len(record["banks"]), 1)
            self.assertEqual(len(record["banks"]["analysis"]["fit_keys"]), 8)

    def test_evaluation_pre_E_penalty_primary_and_all_matching(self):
        meta, streams = {}, {}
        for i in range(15):
            k = f"g_dev|s{i}"; normal = i < 12
            meta[k] = {"variant": "clean" if normal else "attack", "filter_pass": True if normal else None,
                       "scenario": f"s{i}", "fold": i%3, "episode_index": 0, "length_group": "short", "token_count": 60,
                       "family": f"family{i%3}", "tier": "one", "domain_group": "d", "injection_channel": "user",
                       "e": None if normal else 10, "x": None if normal else 20, "complete_16": not normal,
                       "has_hit_look": not normal, "old_incomplete_16": False, "attack_bearing": not normal,
                       "silent": False, "trajectory_class": "on_task" if normal else "executed"}
            p = np.ones((33, 7))
            if i == 12: p[:] = .01
            if i == 13: p[8:] = .01
            streams[k] = {"ends": np.arange(7, 40), "p": p}
        wp = {n: {"alpha": .05} for n in ev.CELLS}
        result, ledger = ev.evaluate(meta, streams, {m: {"0.05": wp} for m in ("matched", "matched_all")}, {n: [9]*3 for n in ev.CELLS})
        for mode in ("matched", "matched_all"):
            r = result["readings"][mode]["0.05"]["W_pool"]
            self.assertEqual(r["timely_recall"], {"count": 1, "n": 3, "rate": 1/3})
            self.assertEqual(r["classification"]["pre_E"], 1)
            self.assertEqual(ledger[f"{mode}/0.05"]["W_pool"]["g_dev|s13"], 15)
        self.assertFalse(result["primary_gain_gate"]["pass"])

    def test_resource_cap_not_inherited_from_a01(self):
        with self.assertRaises(RuntimeError): run.Budget(601).check()
        with patch.object(run.pf, "rss", return_value=2.01):
            with self.assertRaises(RuntimeError): run.Budget().check()


if __name__ == "__main__": unittest.main()
