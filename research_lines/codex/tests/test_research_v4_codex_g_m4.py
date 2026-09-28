"""Synthetic-only mathematical and access tests for M4; no dataset contents."""
import copy
from pathlib import Path
import sys
import unittest

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from research_v2 import trm3_g
from research_v4.codex_g_m4 import M4AccessGuard, M3, no_cache_write
from research_v4.codex_g_m4_math import (
    FEATURES, LAGS, NormalEventBank, event_stage, local_events,
    probability_projection, summarize_events, tensors, window_features,
)

torch.set_num_threads(4)


def synthetic(t=50):
    logits = torch.randn(24, t, 32, generator=torch.Generator().manual_seed(40))
    ids = logits.topk(4, -1).indices
    return tensors(ids, torch.softmax(logits, -1))


def reference():
    qs = torch.full((24, 32), .125, dtype=torch.float64)
    qs[:, :4] = .001
    qc = torch.full((3, 24, 32), 1/32, dtype=torch.float64)
    return qs, qc, qc[0], torch.full((3, 24), .3, dtype=torch.float64)


class AlgebraTest(unittest.TestCase):
    def test_exact_projection_identity_and_independent_sum(self):
        p, a, _ = synthetic()
        q = reference()[0]
        r = torch.where(q < .02, -q.log(), 0.)
        out, error = probability_projection(p, a, r, torch.full((50, 24), .3))
        self.assertLess(error, 1e-12)
        torch.testing.assert_close(out[:, 0], (p * r).sum((1, 2)))
        torch.testing.assert_close(out[:, 0], out[:, 1:].sum(-1))
        torch.testing.assert_close(out[:, 4], (p * ~a * r).sum((1, 2)))

    def test_uniform_selected_weights_have_zero_within_component(self):
        ids = torch.arange(4).expand(24, 20, 4)
        p, a, _ = tensors(ids, torch.ones(24, 20, 32) / 32)
        q = reference()[0]
        r = torch.where(q < .02, -q.log(), 0.)
        out, _ = probability_projection(p, a, r, torch.full((20, 24), .125))
        torch.testing.assert_close(out[:, 2:4], torch.zeros(20, 2, dtype=torch.float64))

    def test_token_products_not_product_of_window_means(self):
        p, a, _ = synthetic()
        q = reference()[0]
        r = torch.where(q < .02, -q.log(), 0.)
        out, _ = probability_projection(p, a, r, torch.full((50, 24), .3))
        wrong = (((p * a).sum(-1).mean(0) - .3) * ((a * r).sum(-1) / 4).mean(0)).sum()
        self.assertGreater(float((out[:, 2].mean() - wrong).abs()), 1e-6)

    def test_JS_partition_and_frozen_grid(self):
        p, a, _ = synthetic()
        tags = ("analysis",) * 20 + ("commentary",) * 10 + ("final",) * 20
        result = window_features(p, a, tags, *reference())
        ends, scores, _, _, _ = result
        native = trm3_g.segmented_windows(torch.ones(50, 1), tags, trm3_g.view_of("V1"), 8)
        np.testing.assert_array_equal(ends, native[0])
        np.testing.assert_allclose(scores[:, 3], scores[:, 4] + scores[:, 5], atol=1e-15)
        np.testing.assert_allclose(scores[:, 6], scores[:, 7] + scores[:, 8], atol=1e-15)
        np.testing.assert_allclose(scores[:, 9], scores[:, 10:14].sum(-1), atol=1e-12)
        np.testing.assert_allclose(scores[:, 16:19].sum(-1), scores[:, 19], atol=1e-12)

    def test_causal_prefix(self):
        p, a, _ = synthetic()
        tags = ("analysis",) * 50
        full = window_features(p, a, tags, *reference())
        prefix = window_features(p[:20], a[:20], tags[:20], *reference())
        np.testing.assert_array_equal(prefix[0], full[0][:len(prefix[0])])
        np.testing.assert_allclose(prefix[1], full[1][:len(prefix[0])], atol=1e-12, rtol=0)
        p2 = p.clone(); p2[20:] = 1/32
        changed = window_features(p2, a, tags, *reference())
        np.testing.assert_allclose(prefix[1], changed[1][:len(prefix[0])], atol=1e-12, rtol=0)

    def test_no_rare_coordinate_has_zero_S_and_finite_shares(self):
        p, a, _ = synthetic()
        qs, qc, qg, m0 = reference(); qs.fill_(.125)
        raw = window_features(p, a, ("analysis",) * 50, qs, qc, qg, m0)[1]
        self.assertTrue(np.isfinite(raw).all())
        np.testing.assert_array_equal(raw[:, [0, 1, 4, 7, *range(9, 20)]], 0.)


class EventsTest(unittest.TestCase):
    def event(self, steps=(0,), h=24):
        ids = torch.tensor([0, 1, 2, 3]).expand(24, 25, 4).clone()
        ids[0, 8:17, 3] = 4
        p, a, ind = tensors(ids, torch.ones(24, 25, 32) / 32)
        rarity = torch.zeros(24, 32); rarity[0, 4] = 1
        return local_events(p, a, ind, ("analysis",) * 25, steps, rarity, h)

    def test_entry_absence_residence_and_lags(self):
        events = self.event()
        self.assertEqual(len(events["entry"]), 1)
        t, key, values = events["entry"][0]
        self.assertEqual((t, key), (8, (0, 4, "analysis")))
        np.testing.assert_array_equal(values[:len(LAGS)], 1/32)
        np.testing.assert_array_equal(values[len(LAGS):2 * len(LAGS)], 1.)
        np.testing.assert_array_equal(values[-2:], [1, 9])

    def test_entry_never_crosses_step_or_horizon(self):
        self.assertEqual(len(self.event(steps=(0, 10))["entry"]), 0)
        self.assertEqual(len(self.event(h=15)["entry"]), 0)

    def test_stable_pair_preserves_exact_support(self):
        events = self.event()
        selected_layer0 = [t for t, key, _ in events["stable"] if key[0] == 0]
        self.assertNotIn(8, selected_layer0)
        self.assertNotIn(17, selected_layer0)
        self.assertIn(9, selected_layer0)
        self.assertEqual(events["valid_layer_pairs"], 24 * 24)

    def test_bank_uses_episode_equal_donors_not_event_equal(self):
        bank = NormalEventBank()
        key = (1, 3, "final")
        bank.add("a", [(t, key, np.array([0.])) for t in range(10)])
        bank.add("b", [(0, key, np.array([9.]))])
        bank.add("c", [(0, key, np.array([9.]))])
        bank.finalize()
        self.assertEqual(bank.reference[key]["episodes"], 3)
        self.assertEqual(bank.reference[key]["events"], 12)
        self.assertEqual(bank.reference[key]["mean"][0], 6.)

    def test_bank_requires_both_minimums_and_never_falls_back(self):
        bank = NormalEventBank()
        for i in range(3): bank.add(str(i), [(0, "small", np.array([1.]))])
        bank.add("a", [(t, "few_episodes", np.array([1.])) for t in range(10)])
        bank.finalize()
        self.assertEqual(bank.reference, {})
        summary = summarize_events([(12, "missing", np.array([2.]))], bank, 10, 30, 8)
        self.assertEqual(summary["E"]["matched_events"], 0)
        self.assertIsNone(summary["E"]["residual"])

    def test_E_X_overlap_and_future_footprint_do_not_claim_preX(self):
        self.assertEqual(event_stage(12, 10, 12), "X")
        bank = NormalEventBank().finalize()
        out = summarize_events([(12, "a", np.array([1.]))], bank, 10, 19, 8)
        self.assertIn("E", out)
        self.assertNotIn("E_strict_preX", out)
        out = summarize_events([(12, "a", np.array([1.]))], bank, 10, 21, 8)
        self.assertIn("E_strict_preX", out)


class AccessTest(unittest.TestCase):
    def test_only_audited_M3_routing_cache_is_allowed(self):
        guard = M4AccessGuard(ROOT)
        allowed = M3 / "logit_cache/g_dev/x.safetensors"
        self.assertEqual(guard.check_path(allowed), allowed.resolve())
        for path in (ROOT / "artifacts/agent_v2/dataset_g/g_dev/steps/0000_decode.safetensors",
                     ROOT / "artifacts/agent_v2/dataset_g/g_conf/trace.json",
                     ROOT / "artifacts/agent_v2/dataset_g/annotations/g_conf/final_unblinded.jsonl"):
            with self.assertRaises(PermissionError): guard.check_path(path)
        with self.assertRaises(AssertionError): no_cache_write()


if __name__ == "__main__":
    unittest.main()
