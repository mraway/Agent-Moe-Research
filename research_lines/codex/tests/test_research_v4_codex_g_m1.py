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
from research_v4.codex_g_m1_math import (
    NormalPercentiles, band_scores, cluster_mean, diagnostic_summary,
    js_divergence, representations, stage_bounds, token_diagnostics,
)
from research_v4.codex_g_m1 import describe_interval, score_interval, window_features


def synthetic(t=12, layers=24, experts=32, k=4):
    generator = torch.Generator().manual_seed(17)
    logits = torch.randn(layers, t, experts, generator=generator)
    ids = logits.topk(k, dim=-1).indices
    weights = torch.softmax(logits.gather(-1, ids), -1)
    return ids, weights, logits


class RepresentationTest(unittest.TestCase):
    def test_simplex_and_actual_weights(self):
        ids, weights, logits = synthetic()
        rep, contract = representations(ids, weights, logits)
        self.assertEqual(tuple(rep.shape), (3, 12, 24, 32))
        torch.testing.assert_close(rep.sum(-1), torch.ones(3, 12, 24))
        torch.testing.assert_close(rep[1].permute(1, 0, 2).gather(-1, ids), weights)
        self.assertLess(contract["gate_simplex_max_error"], 1e-6)
        self.assertEqual(contract["gate_reconstruction_max_error"], 0)

    def test_observed_ids_are_authority_under_ties(self):
        logits = torch.zeros(3, 2, 8)
        ids = torch.tensor([0, 1, 2, 3]).expand(3, 2, 4)
        rep, _ = representations(ids, torch.full((3, 2, 4), .25), logits)
        torch.testing.assert_close(rep[0, :, :, :4], torch.full((2, 3, 4), .25))
        self.assertEqual(float(rep[0, :, :, 4:].sum()), 0)
        torch.testing.assert_close(rep[0], rep[1])

    def test_contract_failures(self):
        for defect in ("duplicate", "range", "negative", "sum", "nonfinite", "reconstruction", "shape"):
            ids, weights, logits = synthetic()
            if defect == "duplicate": ids[0, 0, 1] = ids[0, 0, 0]
            if defect == "range": ids[0, 0, 1] = 32
            if defect == "negative": weights[0, 0, 0] = -1
            if defect == "sum": weights *= 2
            if defect == "nonfinite": logits[0, 0, 0] = torch.nan
            if defect == "reconstruction": weights[:] = .25
            if defect == "shape": weights = weights[:, :-1]
            with self.subTest(defect=defect), self.assertRaises(ValueError):
                representations(ids, weights, logits)

    def test_js_values_and_broadcast(self):
        p, q = torch.tensor([1., 0.]), torch.tensor([0., 1.])
        self.assertEqual(float(js_divergence(p, p)), 0)
        self.assertAlmostEqual(float(js_divergence(p, q)), math.log(2))
        values = js_divergence(p.expand(3, 2), q)
        self.assertEqual(tuple(values.shape), (3,))

    def test_band_means(self):
        p = torch.zeros(3, 2, 24, 2)
        q = torch.zeros_like(p)
        p[..., 0] = 1
        q[..., 0] = 1
        q[:, :, 16:, 0] = 0
        q[:, :, 16:, 1] = 1
        scores = band_scores(p, q)
        np.testing.assert_allclose(scores[:, :, 0], math.log(2) / 3)
        np.testing.assert_allclose(scores[:, :, 1:3], 0)
        np.testing.assert_allclose(scores[:, :, 3], math.log(2))

    def test_causal_prefix(self):
        ids, weights, logits = synthetic(t=20)
        a, _ = representations(ids, weights, logits)
        b, _ = representations(ids[:, :12], weights[:, :12], logits[:, :12])
        torch.testing.assert_close(a[:, :12], b)
        da = token_diagnostics(a, ids, ("analysis",) * 20, [0])
        db = token_diagnostics(b, ids[:, :12], ("analysis",) * 12, [0])
        for key in da:
            np.testing.assert_allclose(da[key][:12], db[key])
        wa, wb = window_features(a, ("analysis",) * 20), window_features(b, ("analysis",) * 12)
        np.testing.assert_array_equal(wa[0][:len(wb[0])], wb[0])
        torch.testing.assert_close(wa[1][:len(wb[1])], wb[1])

    def test_weight_change_with_identical_support(self):
        logits = torch.zeros(3, 5, 8)
        logits[:, 1:, 0] = 1
        ids = torch.arange(4).expand(3, 5, 4)
        weights = torch.softmax(logits.gather(-1, ids), -1)
        rep, _ = representations(ids, weights, logits)
        diag = token_diagnostics(rep, ids, ("analysis",) * 5, [0, 3])
        self.assertEqual(float(js_divergence(rep[0, 0], rep[0, 1]).sum()), 0)
        self.assertTrue((diag["W_change"][1] > 0).all())
        self.assertFalse(diag["valid_pair"][0])
        self.assertFalse(diag["valid_pair"][3])
        self.assertEqual(int(diag["stable_mask"].sum()), 9)
        summary = diagnostic_summary(diag, 0, 4)
        self.assertEqual(summary["stable_layer_fraction"], 1)
        self.assertGreater(summary["W_change_stable"], 0)

    def test_channel_boundaries_and_missing_stable_are_not_zero(self):
        ids, weights, logits = synthetic(t=4)
        rep, _ = representations(ids, weights, logits)
        diag = token_diagnostics(rep, ids, ("analysis", "final", "other", "final"), [0])
        self.assertFalse(diag["valid_pair"].any())
        summary = diagnostic_summary(diag, 0, 3)
        self.assertEqual(summary["tokens"], 3)
        self.assertIsNone(summary["W_change_stable"])
        self.assertIsNone(summary["stable_layer_fraction"])

    def test_grid_excludes_short_runs_and_counts_looks(self):
        rep, _ = representations(*synthetic(t=390))
        tags = ("analysis",) * 4 + ("final",) * 10 + ("analysis",) * 376
        ends, means, out_tags, ordinals = window_features(rep, tags)
        self.assertEqual(len(ends), 352)
        self.assertEqual(int(ends[0]), 11)
        self.assertGreater(int(ends[-1]), 351)
        self.assertEqual(out_tags[0], "final")
        self.assertEqual(int(ordinals[0]), 0)
        self.assertEqual(tuple(means.shape), (352, 3, 24, 32))


class PercentileTest(unittest.TestCase):
    def references(self, count=10, tag="analysis", ordinal=0, episode_index=0):
        return [{"key": str(i), "tags": [tag] * 3, "ordinals": np.arange(ordinal, ordinal + 3),
                 "episode_index": episode_index, "scores": np.full((3, 3, 4), .5)} for i in range(count)]

    def test_ties_and_fixed_fallback(self):
        ref = NormalPercentiles(self.references())
        values = np.stack([np.full((3, 4), v) for v in (0, .5, 1)])
        output, level = ref.transform(values, ["analysis"] * 3, np.array([0, 1, 2]), 0)
        np.testing.assert_allclose(output[:, 0, 0], [0, .5, 1])
        np.testing.assert_array_equal(level, [0, 0, 0])
        _, fallback = ref.transform(values, ["analysis"] * 3, np.array([0, 1, 2]), 1)
        np.testing.assert_array_equal(fallback, [1, 1, 1])
        _, fallback = ref.transform(values, ["analysis"] * 3, np.array([64, 65, 66]), 0)
        np.testing.assert_array_equal(fallback, [2, 2, 2])
        _, fallback = ref.transform(values, ["analysis"] * 3, np.array([64, 65, 66]), 1)
        np.testing.assert_array_equal(fallback, [3, 3, 3])
        _, fallback = ref.transform(values, ["final"] * 3, np.array([0, 1, 2]), 0)
        np.testing.assert_array_equal(fallback, [4, 4, 4])

    def test_sparse_missing_and_empty(self):
        ref = NormalPercentiles(self.references(count=9))
        output, level = ref.transform(np.zeros((1, 3, 4)), ["analysis"], np.array([0]), 0)
        self.assertTrue(np.isnan(output).all())
        self.assertEqual(int(level[0]), 255)
        output, level = ref.transform(np.zeros((0, 3, 4)), [], np.array([], dtype=int), 0)
        self.assertEqual(output.shape, (0, 3, 4))
        self.assertEqual(len(level), 0)

    def test_windows_do_not_substitute_for_distinct_episodes(self):
        rows = self.references()
        for row in rows: row["key"] = "same"
        self.assertEqual(len(NormalPercentiles(rows).references), 0)

    def test_reference_not_duplicated_by_buckets(self):
        rows = self.references()
        for row in rows:
            row["tags"] *= 2
            row["ordinals"] = np.array([0, 1, 2, 32, 33, 34])
            row["scores"] = np.zeros((6, 3, 4))
        ref = NormalPercentiles(rows)
        self.assertEqual(len(ref.references[(4,)]), 60)
        self.assertEqual(len(ref.references[(0, "analysis", 0, 0)]), 30)


class SummaryTest(unittest.TestCase):
    def test_bootstrap_equal_episode_weight_and_reproducibility(self):
        result = cluster_mean([0, 0, 1], ["a", "a", "b"])
        self.assertAlmostEqual(result["mean"], 1 / 3)
        self.assertEqual(result, cluster_mean([0, 0, 1], ["a", "a", "b"]))
        self.assertEqual(result["cluster_sizes"], {"a": 2, "b": 1})
        self.assertEqual(cluster_mean([], [])["n"], 0)
        self.assertIsNone(cluster_mean([1], ["a"])["ci"])

    def test_stage_bounds_and_horizon_clipping(self):
        bounds = stage_bounds(2, 30)
        self.assertEqual(bounds["pre_E"], (0, 1))
        self.assertEqual(bounds["E"], (2, 18))
        self.assertEqual(bounds["X"], (30, 46))
        ids, weights, logits = synthetic(t=40)
        rep, _ = representations(ids, weights, logits)
        diag = token_diagnostics(rep, ids, ("analysis",) * 40, [0])
        row = {"h_end": 35, "ends": np.arange(7, 36), "tags": ["analysis"] * 29,
               "scores": np.zeros((29, 3, 4)), "percentiles": np.full((29, 3, 4), .5),
               "levels": np.zeros(29, dtype=np.uint8)}
        block = score_interval(describe_interval(row, bounds["X"], diag), row)
        self.assertEqual(block["looks"], 6)
        self.assertTrue(block["horizon_cut"])
        self.assertEqual(block["percentile"]["U"]["all"], .5)
        self.assertEqual(block["exact_fraction"], 1)
        missing = score_interval(describe_interval(row, None, diag), row)
        self.assertIsNone(missing["percentile"]["U"]["all"])
        self.assertEqual(missing["status"], "missing_event")


if __name__ == "__main__":
    torch.set_num_threads(2)
    unittest.main()
