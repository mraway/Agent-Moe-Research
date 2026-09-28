from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from research_v4.codex_g_m1_math import js_divergence, representations
from research_v4.codex_g_m1 import window_features
from research_v4.codex_g_m2b import M2BAccessGuard, cohort_summary, make_readings, matched_summary
from research_v4.codex_g_m2b_math import (
    REPS, causal_windows, complete_ablations, dynamic_interval, layer_bands,
    partition_js, probability_basis, stable_dynamics,
)


def synthetic(t=20):
    logits = torch.randn(24, t, 32, generator=torch.Generator().manual_seed(20))
    ids = logits.topk(4, dim=-1).indices
    weights = torch.softmax(logits.gather(-1, ids), -1)
    rep, _ = representations(ids, weights, logits)
    return rep, ids


class ComponentTest(unittest.TestCase):
    def test_probability_identity_and_ablated_components(self):
        rep, _ = synthetic()
        basis, physical, p, contract = probability_basis(rep)
        self.assertEqual(tuple(basis.shape), (5, 20, 24, 32))
        self.assertEqual(tuple(physical.shape), (20, 3, 24))
        torch.testing.assert_close(basis.sum(-1), torch.ones(5, 20, 24))
        mask = rep[0] > 0
        m = (p * mask).sum(-1, keepdim=True)
        torch.testing.assert_close(m * basis[0].double() + (1 - m) * basis[1].double(), p, atol=1e-8, rtol=1e-6)
        torch.testing.assert_close((basis[2] * mask).sum(-1), physical[:, 0])
        torch.testing.assert_close(basis[2] * ~mask, p.float() * ~mask)
        torch.testing.assert_close(basis[3] * mask, p.float() * mask)
        torch.testing.assert_close(basis[4] * mask, basis[2] * mask)
        torch.testing.assert_close(basis[4] * ~mask, basis[3] * ~mask)
        self.assertLess(contract["identity_max_error"], 1e-12)
        self.assertLess(contract["V_W_max_error"], 1e-6)

    def test_uniform_logits_keep_observed_support(self):
        ids = torch.arange(4).expand(24, 10, 4)
        rep, _ = representations(ids, torch.full((24, 10, 4), .25), torch.zeros(24, 10, 32))
        basis, physical, _, _ = probability_basis(rep)
        torch.testing.assert_close(physical[:, 0], torch.full((10, 24), .125))
        torch.testing.assert_close(physical[:, 1:], torch.ones(10, 2, 24))
        torch.testing.assert_close(basis[0], rep[0])
        for index in (2, 3, 4): torch.testing.assert_close(basis[index], rep[2])

    def test_fixed_mass_uses_supplied_normal_reference(self):
        rep, _ = synthetic()
        basis, _, _, _ = probability_basis(rep)
        m0 = torch.full((24,), .4, dtype=torch.float64)
        result = complete_ablations(basis.permute(1, 0, 2, 3), m0)
        mask = rep[0] > 0
        torch.testing.assert_close((result[:, 1] * mask).sum(-1), m0.expand(20, 24), atol=1e-7, rtol=0)
        torch.testing.assert_close(result[:, 0], basis[0].double())
        with self.assertRaises(ValueError): complete_ablations(basis.permute(1, 0, 2, 3), torch.zeros(24))

    def test_product_of_means_is_not_used_for_mass_ablations(self):
        # Explicit two-token mass/support covariance: average products differs.
        ids = torch.stack((torch.arange(4), torch.arange(4, 8)), 0).expand(24, 2, 4)
        logits = torch.zeros(24, 2, 32)
        logits[:, 0, :4] = 3
        logits[:, 1, 4:8] = .5
        rep, _ = representations(ids, torch.full((24, 2, 4), .25), logits)
        basis, physical, p, _ = probability_basis(rep)
        mask = rep[0] > 0
        m = (p * mask).sum(-1, keepdim=True)
        r = (~mask).double() / 28
        exact = (m * rep[0] + (1 - m) * r).mean(0)
        torch.testing.assert_close(basis[4].double().mean(0), exact, atol=1e-8, rtol=1e-6)
        wrong = physical[:, 0].mean(0)[:, None] * rep[0].mean(0) + (1 - physical[:, 0].mean(0)[:, None]) * r.mean(0)
        self.assertGreater(float((exact - wrong).abs().max()), .01)

    def test_generalized_grid_matches_M1(self):
        rep, _ = synthetic(t=390)
        tags = ("analysis",) * 4 + ("final",) * 10 + ("analysis",) * 376
        a = window_features(rep, tags)
        b = causal_windows(rep.permute(1, 0, 2, 3), tags)
        np.testing.assert_array_equal(a[0], b[0])
        torch.testing.assert_close(a[1], b[1])
        np.testing.assert_array_equal(a[3], b[3])
        self.assertEqual(len(b[0]), 352)
        self.assertGreater(int(b[0][-1]), 351)

    def test_causal_prefix_and_fixed_layer_bands(self):
        rep, _ = synthetic()
        a = probability_basis(rep)
        b = probability_basis(rep[:, :12])
        torch.testing.assert_close(a[0][:, :12], b[0])
        torch.testing.assert_close(a[1][:12], b[1])
        wa = causal_windows(a[1], ("analysis",) * 20)
        wb = causal_windows(b[1], ("analysis",) * 12)
        torch.testing.assert_close(wa[1][:len(wb[0])], wb[1])
        values = torch.zeros(2, 3, 24)
        values[..., 16:] = 3
        torch.testing.assert_close(layer_bands(values), torch.tensor([1., 0., 0., 3.]).expand(2, 3, 4))

    def test_degenerate_mass_rejected(self):
        rep, _ = synthetic()
        rep[2] = rep[0]
        with self.assertRaises(ValueError): probability_basis(rep)


class DynamicTest(unittest.TestCase):
    mask = torch.tensor([True, True, False, False])

    def test_js_chain_rule_and_unequal_group_masses(self):
        p = torch.tensor([.1, .3, .2, .4], dtype=torch.float64)
        q = torch.tensor([.24, .36, .32, .08], dtype=torch.float64)
        parts = partition_js(p, q, self.mask)
        self.assertAlmostEqual(float(parts[0]), float(js_divergence(p, q)), places=13)
        self.assertAlmostEqual(float(parts[0]), float(parts[1:].sum()), places=13)
        self.assertTrue(bool((parts > 0).all()))
        a, b = p[:2].sum(), q[:2].sum()
        v, w = p[:2] / a, q[:2] / b
        mixture = (a * v + b * w) / (a + b)
        selected = .5 * a * (v * (v / mixture).log()).sum() + .5 * b * (w * (w / mixture).log()).sum()
        self.assertAlmostEqual(float(parts[2]), float(selected), places=13)

    def test_only_mass_changes(self):
        parts = partition_js(torch.tensor([.1, .1, .4, .4]), torch.tensor([.3, .3, .2, .2]), self.mask)
        self.assertGreater(float(parts[1]), 0)
        torch.testing.assert_close(parts[2:], torch.zeros(2, dtype=torch.float64), atol=1e-12, rtol=0)

    def test_only_selected_conditional_changes(self):
        parts = partition_js(torch.tensor([.125, .375, .25, .25]), torch.tensor([.375, .125, .25, .25]), self.mask)
        self.assertGreater(float(parts[2]), 0)
        self.assertAlmostEqual(float(parts[1] + parts[3]), 0, places=12)

    def test_only_tail_conditional_changes(self):
        parts = partition_js(torch.tensor([.25, .25, .125, .375]), torch.tensor([.25, .25, .375, .125]), self.mask)
        self.assertGreater(float(parts[3]), 0)
        self.assertAlmostEqual(float(parts[1] + parts[2]), 0, places=12)

    def test_boundary_support_change_and_empty_are_not_zero(self):
        ids = torch.arange(4).expand(24, 6, 4).clone()
        ids[:, 4:] = torch.arange(4, 8)
        p = torch.softmax(torch.randn(6, 24, 32, generator=torch.Generator().manual_seed(9)), -1)
        diag = stable_dynamics(p, ids, ("analysis", "analysis", "final", "final", "final", "final"), [0, 3])
        np.testing.assert_array_equal(diag["count"][:, 0], [0, 24, 0, 0, 0, 24])
        empty = dynamic_interval(diag, np.array([0, 2, 3, 4]))
        self.assertIsNone(empty["means"][0][0])
        good = dynamic_interval(diag, np.array([1, 5]))
        self.assertEqual(good["stable_pairs"][0], 48)
        self.assertAlmostEqual(good["means"][0][0], sum(x[0] for x in good["means"][1:]), places=12)

    def test_invalid_js_inputs(self):
        for q in (torch.zeros(4), torch.tensor([1., -1., 1., 1.]), torch.full((4,), torch.nan)):
            with self.assertRaises(ValueError): partition_js(torch.ones(4), q, self.mask)


class MatchingTest(unittest.TestCase):
    def fixture(self):
        meta = {key: {"key": key, "family": "f", "tier": "T0", "trajectory_class": "execution",
                      "injection_channel": "direct_user", "fold": 0, "episode_index": 0, "scenario": "same",
                      "variant": variant, "coordinates": [["analysis", 0, 0, 7], ["analysis", 0, 0, 8]]}
                for key, variant in (("a", "attack"), ("c", "clean"), ("b", "benign_control"))}
        graph = {"queries": [{"key": "a", "kind": "E_did", "groups": {"pre": [0], "post": [1]}, "horizon_cut": False}],
                 "matches": [{"status": "matched", "query_id": 0, "scope": "S", "quality": "filtered",
                              "donors": [{"key": key, "groups": {"pre": [0], "post": [1]}} for key in ("c", "b")]}]}
        scores = {key: {scale: np.full((2, n, 4), value) for scale, n in (("raw", 8), ("percentile", 8), ("physical", 3))}
                  for key, value in (("a", .8), ("c", .2), ("b", .4))}
        scores["a"]["percentile"][0] = .6
        return graph, meta, scores

    def test_same_frozen_donors_and_paired_arithmetic(self):
        graph, meta, scores = self.fixture()
        rows, checked = make_readings(graph, meta, scores)
        self.assertEqual(checked, 4)
        np.testing.assert_allclose(rows[0]["values"]["percentile"]["post_residual"], .5)
        np.testing.assert_allclose(rows[0]["values"]["percentile"]["did"], .2)
        np.testing.assert_allclose(rows[0]["placebo"]["percentile"]["post"], -.2)
        result = matched_summary(rows, "E_did", meta)
        self.assertEqual(len(result["effects"]["percentile"]["did"]["P_minus_ablations"]["family"]["mean"]), 4)

    def test_structural_or_fold_changes_rejected(self):
        for field in ("fold", "coordinates"):
            graph, meta, scores = self.fixture()
            meta["c"][field] = 2 if field == "fold" else [["final", 0, 0, 7], ["final", 0, 0, 8]]
            with self.assertRaises(ValueError): make_readings(graph, meta, scores)

    def test_empty_dynamic_cohort_not_zero(self):
        row = {"attack_bearing": False, "intervals": {"whole": {
            "status": "effective", "looks": 1, "raw": np.zeros((8, 4)).tolist(), "percentile": np.zeros((8, 4)).tolist(),
            "physical": np.zeros((3, 4)).tolist(), "dynamics": {"stable_pairs": [0] * 4, "valid_pairs": [24, 8, 8, 8],
                                                                   "means": [[None] * 4 for _ in range(4)]}}}}
        result = cohort_summary([row], "whole")
        self.assertEqual(result["dynamics"]["all"]["n"], 0)
        self.assertIsNone(result["dynamics"]["all"]["means"])

    def test_guard_only_explicit_open_routing_paths(self):
        guard = M2BAccessGuard(ROOT, ROOT / "artifacts/agent_v2/codex_g/test_output")
        guard.check_path(ROOT / "artifacts/agent_v2/dataset_g/g_dev/example.safetensors")
        for path in ("artifacts/agent_v2/dataset_g/g_conf/trace.json",
                     "artifacts/agent_v2/dataset_g/annotations/g_conf/final_unblinded.jsonl",
                     "artifacts/agent_v2/codex_g/invalidated_smoke/onboarding_smoke.json",
                     "artifacts/agent_v2/dataset_g/g_fit/example.safetensors",
                     "artifacts/agent_v2/research_v4/g_routing_cache/g_conf/example.safetensors"):
            with self.subTest(path=path), self.assertRaises(PermissionError): guard.check_path(ROOT / path)


if __name__ == "__main__":
    unittest.main()
