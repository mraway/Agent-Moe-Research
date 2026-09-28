from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import torch

from research_v4.codex_g_m1 import ROOT
from research_v4.codex_g_m4 import M3
from research_v4 import codex_g_m5 as runner
from research_v4.codex_g_m5 import M5AccessGuard, phases, row_fingerprint
from research_v4.codex_g_m5_math import (
    COUNTS, FIELDS, LEVELS, STREAKS, SWITCHES, ConditionalBank,
    causal_observations, compare_on_queries, history_keys, matched_controls,
)


def toy(pattern):
    n = len(pattern)
    ids = torch.tensor([1, 2, 3, 4]).view(1, 1, 4).repeat(24, n, 1)
    ids[2, :, 3] = torch.tensor([0 if x else 4 for x in pattern])
    p = torch.softmax(torch.randn((24, n, 32), generator=torch.Generator().manual_seed(41)), -1)
    rare = np.zeros((24, 32), dtype=bool)
    rare[2, 0] = True
    return ids, p, rare


def observe(ids, p, rare, tags=None, starts=None):
    n = ids.shape[1]
    return causal_observations(ids, p, tags or ["analysis"] * n, starts or [0], rare,
                               np.arange(7, n), np.arange(max(0, n - 7)), 0)


def small_observations(history, probability):
    h = np.array(history)
    n = len(h)
    values = np.array([[p, p, p, p, COUNTS[x], STREAKS[x], SWITCHES[x]] for x, p in zip(h, probability)], dtype=float)
    return {"values": values, "keys": history_keys(np.zeros(n, dtype=int), np.zeros(n, dtype=int), h, 0, np.zeros(n, dtype=int))}


class HistoryTest(unittest.TestCase):
    def test_exact_bit_order_and_counts(self):
        result = observe(*toy([1, 0, 1, 1, 0, 0, 1, 1]))
        self.assertEqual(result["history"].tolist(), [0b10110011])
        np.testing.assert_array_equal(result["values"][0, 4:], [5, 2, 4])

    def test_count_control_does_not_control_sequence(self):
        obs = small_observations([0b111, 0b1011], [1, 2])
        self.assertEqual(obs["keys"]["C"][0], obs["keys"]["C"][1])
        self.assertNotEqual(obs["keys"]["H"][0], obs["keys"]["H"][1])
        self.assertEqual(obs["values"][0, 5], 3)
        self.assertEqual(obs["values"][1, 5], 2)

    def test_causal_prefix_and_future_mutation(self):
        ids, p, rare = toy([0, 1] * 10)
        complete = observe(ids, p, rare)
        prefix = observe(ids[:, :13], p[:, :13], rare)
        np.testing.assert_allclose(prefix["values"], complete["values"][:6], atol=0, rtol=0)
        p[:, 13:] = p[:, 13:].flip(-1)
        ids[:, 13:] = ids[:, 13:].flip(-1)
        changed = observe(ids, p, rare)
        np.testing.assert_allclose(prefix["values"], changed["values"][:6], atol=0, rtol=0)

    def test_no_channel_or_step_boundary_crossing(self):
        ids, p, rare = toy([1] * 24)
        out = observe(ids, p, rare, tags=["analysis"] * 12 + ["final"] * 12, starts=[0, 4, 12])
        np.testing.assert_array_equal(out["ends"], [11, 19, 20, 21, 22, 23])
        self.assertEqual(out["original_looks"], 17)
        self.assertEqual(out["eligible_looks"], 6)

    def test_current_membership_uses_actual_ids_even_for_ties(self):
        ids, p, rare = toy([0, 1] * 6)
        p[:] = 1 / 32
        out = observe(ids, p, rare)
        np.testing.assert_array_equal(out["selected"], [True, False, True, False, True])

    def test_probability_mass_does_not_fake_internal_reweighting(self):
        ids, p, rare = toy([1] * 8)
        a = p.clone()
        b = p.clone()
        for probability, m in ((a, .4), (b, .8)):
            probability[:] = (1 - m) / 28
            probability.scatter_(-1, ids.long(), m / 4)
        va, vb = (observe(ids, pp, rare)["values"][0] for pp in (a, b))
        self.assertAlmostEqual(vb[0] / va[0], 2, places=6)
        self.assertAlmostEqual(va[1], vb[1], places=7)
        self.assertAlmostEqual(va[2], 0, places=7)
        self.assertAlmostEqual(vb[3] / va[3], 2, places=6)

    def test_short_or_empty_rare_has_no_fabricated_events(self):
        ids, p, rare = toy([1] * 6)
        result = observe(ids, p, rare)
        self.assertEqual(result["values"].shape, (0, len(FIELDS)))
        ids, p, rare = toy([1] * 10)
        rare[:] = False
        self.assertEqual(observe(ids, p, rare)["values"].shape, (0, len(FIELDS)))

    def test_position_encoding_is_lossless_and_independent_of_history_key(self):
        codes = []
        for episode in range(2):
            a = history_keys(np.array([1, 1]), np.array([0, 0]), np.array([3, 3]), episode, np.array([0, 1]))
            self.assertEqual(a["H"][0], a["H"][1])
            codes.extend(a["HP"].tolist())
        self.assertEqual(len(set(codes)), 4)


class BankTest(unittest.TestCase):
    def test_donors_equal_not_coordinate_looks_equal(self):
        bank = ConditionalBank()
        for name, n, value in (("a", 20, 1), ("b", 1, 9), ("c", 1, 8)):
            bank.add(name, np.zeros(n, dtype=int), np.full((n, len(FIELDS)), value), normal=True, filtered=True)
        bank.finalize()
        np.testing.assert_allclose(bank.means, 6)
        np.testing.assert_array_equal(bank.counts, [[22, 3]])

    def test_rejects_bad_donors_and_duplicate_episodes(self):
        b = ConditionalBank()
        for normal, filtered in ((False, True), (True, False), (True, None)):
            with self.assertRaises(ValueError):
                b.add("a", np.array([1]), np.ones((1, 7)), normal=normal, filtered=filtered)
        b.add("a", np.array([1]), np.ones((1, 7)), normal=True, filtered=True)
        with self.assertRaises(ValueError):
            b.add("a", np.array([1]), np.ones((1, 7)), normal=True, filtered=True)

    def test_both_minima_and_no_fallback(self):
        b = ConditionalBank()
        for i, codes in enumerate(([0, 0, 1, 2, 2, 2, 2, 2], [0, 0, 1], [0, 1])):
            b.add(str(i), np.array(codes), np.ones((len(codes), 7)), normal=True, filtered=True)
        b.finalize()
        good, values = b.lookup(np.arange(4))
        np.testing.assert_array_equal(good, [True, False, False, False])
        self.assertTrue(np.isnan(values[1:]).all())

    def test_restore_is_exact_and_cannot_fit(self):
        b = ConditionalBank()
        for i in range(3):
            b.add(str(i), np.array([0, 0]), np.ones((2, 7)), normal=True, filtered=True)
        b.finalize()
        restored = ConditionalBank.restore(b.export())
        np.testing.assert_array_equal(restored.means, b.means)
        with self.assertRaises(AssertionError):
            restored.add("new", np.array([0]), np.ones((1, 7)), normal=True, filtered=True)
        invalid = b.export()
        invalid["counts"] = np.ones_like(b.counts)
        with self.assertRaises(ValueError):
            ConditionalBank.restore(invalid)

    def test_alignment_keeps_queries_identical_and_binary_residuals_zero(self):
        banks = {k: ConditionalBank() for k in LEVELS}
        for i in range(3):
            obs = small_observations([1, 1] + ([3] * 5 if i == 0 else []), [i + 1.] * (7 if i == 0 else 2))
            for k, b in banks.items():
                b.add(str(i), obs["keys"][k], obs["values"], normal=True, filtered=True)
        for b in banks.values():
            b.finalize()
        target = small_observations([1, 3], [10., 100.])
        controls = matched_controls(target, banks)
        out = compare_on_queries(target, controls, np.ones(2, dtype=bool))
        self.assertEqual(out["native"]["B"]["events"], 2)
        for level in ("B", "C", "H"):
            self.assertEqual(out["aligned_H"][level]["events"], 1)
            self.assertEqual(out["aligned_H"][level]["raw"][0], 10)
        np.testing.assert_allclose(out["aligned_H"]["H"]["residual"][4:], 0)
        self.assertGreater(out["aligned_H"]["H"]["residual"][0], 0)


class ProtocolTest(unittest.TestCase):
    def test_wrong_bank_hash_fails_before_attack_input_reads(self):
        payload = b"frozen"
        digest = runner.sha(payload)
        log = {"status": "completed", "access_guard": {"blocked_attempts": 0},
               "output_sha256": {"normal_freeze.json": digest}}
        saved = {"source_sha256": {}, "implementation_commit": "unit", "bank_sha256": digest}
        with patch.object(runner, "freeze_sources", return_value={}), \
             patch.object(M5AccessGuard, "install"), patch.object(Path, "exists", return_value=False), \
             patch.object(Path, "read_bytes", return_value=payload), \
             patch.object(runner, "read_json", side_effect=[log, saved]), \
             patch.object(runner, "load_inputs") as inputs:
            with self.assertRaises(ValueError):
                runner.run("score", "unit", "incorrect")
            inputs.assert_not_called()

    def test_normal_guard_blocks_attack_before_open_and_sealed_always(self):
        normal = M5AccessGuard(ROOT, "normal")
        with self.assertRaises(PermissionError):
            normal.check_path(M3 / "topk_cache/g_dev/example--attack--ep0.safetensors")
        normal.check_path(M3 / "topk_cache/g_dev/example--clean--ep0.safetensors")
        for stage in ("normal", "score"):
            with self.assertRaises(PermissionError):
                M5AccessGuard(ROOT, stage).check_path(ROOT / "artifacts/agent_v2/dataset_g/g_conf/trace.json")

    def test_missing_noX_and_strict_preX_are_distinct(self):
        meta = {"attack_bearing": True, "anchor": {"anchor": 8, "x": 12}, "h_end": 30}
        self.assertEqual(phases(meta)["E_strict_preX"], (8, 11))
        meta["anchor"]["x"] = None
        self.assertIsNone(phases(meta)["E_to_X"])
        self.assertIsNone(phases(meta)["E_strict_preX"])
        self.assertEqual(phases(meta)["E_noX"], (8, 24))

    def test_fingerprint_is_episode_order_independent_and_sensitive(self):
        a = [{"key": "b", "value": 1}, {"key": "a", "value": 2}]
        self.assertEqual(row_fingerprint(a), row_fingerprint(a[::-1]))
        a[0]["value"] = 3
        self.assertNotEqual(row_fingerprint(a), row_fingerprint([{ "key": "b", "value": 1}, a[1]]))


if __name__ == "__main__":
    unittest.main()
