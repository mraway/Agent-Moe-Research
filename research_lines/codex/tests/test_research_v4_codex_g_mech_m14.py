"""Synthetic temporal, censoring, reference and donor-selection contracts."""
from __future__ import annotations

import itertools
import os
from pathlib import Path
import sys
import unittest
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src")); sys.path.insert(0, str(ROOT/"scripts"))
from research_v4 import codex_g_mech_m14 as run, codex_g_mech_m14_math as mm
from research_v4 import codex_g_mech_m14_audit as audit


def fixture(episodes=12, length=48):
    keys = np.array([f"g_dev|test-{i}#ep0" for i in range(episodes)])
    n = episodes*length; ep = np.repeat(np.arange(episodes), length)
    metadata = {str(k): dict(variant="clean", filter_pass=True, family=f"f{i%3}", tier="T0", scenario=f"s{i}",
                            trajectory_class="silent", attack_bearing=False, e=None, x=None, token_count=length+7)
                for i, k in enumerate(keys)}
    a = dict(keys=keys, episode=ep, ends=np.tile(np.arange(7, 7+length), episodes),
             structure=np.zeros((n, 6), int), values=np.tile(np.arange(length), (episodes, 6, 1)).transpose(0, 2, 1).reshape(n, 6).astype(float),
             valid=np.ones(n, bool))
    return a, metadata


class TemporalTest(unittest.TestCase):
    def test_order_signal_differs_with_identical_histogram(self):
        contiguous = mm.sequence_metrics([.99]*4+[.1]*4, .9)
        scattered = mm.sequence_metrics([.99, .1]*4, .9)
        self.assertEqual(contiguous[1], scattered[1]); self.assertGreater(contiguous[2], scattered[2])
        self.assertEqual(contiguous[4], 32); self.assertEqual(scattered[4], 8)

    def test_permutation_null_is_exact(self):
        for n in range(2, 8):
            for k in range(n+1):
                values = []
                for high in itertools.combinations(range(n), k):
                    seq = np.zeros(n); seq[list(high)] = 1
                    values.append(mm.sequence_metrics(seq, .9)[2])
                self.assertAlmostEqual(float(np.mean(values)), 0, places=13)

    def test_brute_sequence_metrics(self):
        rng = np.random.default_rng(5)
        for n in range(1, 50):
            seq = rng.random(n)
            for threshold in (.9, .95):
                np.testing.assert_allclose(mm.sequence_metrics(seq, threshold), audit.brute_metrics(seq, threshold), atol=1e-12)

    def test_missing_and_censored_are_not_recovery(self):
        self.assertTrue(np.isnan(mm.sequence_metrics([.99], .9)[2]))
        self.assertEqual(mm.sequence_metrics([.99, .99], .9)[5:].tolist(), [1, 1])
        self.assertEqual(mm.sequence_metrics([.1, .99, .1], .9)[5:].tolist(), [0, 0])

    def test_threshold_is_strict(self):
        self.assertEqual(mm.sequence_metrics([.9, .9], .9)[1], 0)

    def test_nonoverlap_never_crosses_boundaries(self):
        a, _ = fixture(2, 25); a["structure"][10:18, 1] = 1; a["structure"][18:25, 3] = 1
        segments, identities = mm.runs(a)
        self.assertEqual(segments, [(0, 10), (10, 18), (18, 25), (25, 50)])
        for offset in (0, 4):
            for row in mm.grids(a, segments, offset):
                ix = row[2]+8*np.arange(row[3]); self.assertTrue((identities[ix] == row[1]).all())
                self.assertTrue((np.diff(a["ends"][ix]) == 8).all())


class ReferenceAndMatchTest(unittest.TestCase):
    def test_percentiles_midrank_and_self_exclusion(self):
        a, m = fixture(); ref = mm.fit_reference(a, m); ranks = mm.percentiles(a, m, ref)
        for q in (0, 19, 47, 101, len(a["ends"])-1):
            for col in range(3): self.assertAlmostEqual(ranks[q, col], audit.direct_rank(a, m, q, col), places=12)
        self.assertAlmostEqual(ranks[0, 0], .5/48)

    def test_attack_never_fits_reference(self):
        a, m = fixture(); m[str(a["keys"][0])].update(variant="attack", attack_bearing=True)
        before = mm.fit_reference(a, m); a["values"][:48] = 1e8; after = mm.fit_reference(a, m)
        for key in before: np.testing.assert_array_equal(before[key], after[key])
        self.assertEqual(mm.percentiles(a, m, after)[0, 1], 1)

    def test_reference_requires_ten_other_episodes(self):
        a, m = fixture(10); ranks = mm.percentiles(a, m, mm.fit_reference(a, m))
        self.assertTrue(np.isnan(ranks).all())

    def test_episode_equal_reference_not_token_equal(self):
        a, m = fixture(); a["values"][:48, 3:] = 100
        a["values"][48:, 3:] = 0
        # The high-valued normal episode has only one look, others have 48.
        keep = np.r_[0, np.arange(48, len(a["ends"]))]
        for name in ("episode", "ends", "structure", "values", "valid"): a[name] = a[name][keep]
        ranks = mm.percentiles(a, m, mm.fit_reference(a, m))
        self.assertAlmostEqual(ranks[1, 1], 5/11)
        self.assertAlmostEqual(ranks[1, 1], audit.direct_rank(a, m, 1, 1))

    def test_matching_uses_only_initial_values(self):
        a, m = fixture(); ranks = np.full((len(a["ends"]), 3), .5); segments, _ = mm.runs(a)
        grid = mm.grids(a, segments, 0); before, _ = mm.segment_graph(a, m, ranks, grid)
        ranks[grid[0, 2]+8*np.arange(1, grid[0, 3])] = .99
        after, _ = mm.segment_graph(a, m, ranks, grid)
        np.testing.assert_array_equal(before, after)

    def test_matching_scenario_length_family_and_distinct_episode(self):
        a, m = fixture(); ranks = np.full((len(a["ends"]), 3), .5); grid = mm.grids(a, mm.runs(a)[0], 0)
        graph, _ = mm.segment_graph(a, m, ranks, grid)
        self.assertNotIn(0, graph[0, 0]); self.assertEqual(graph[1, 0].tolist(), [3, 6, 9])
        grid[3, 3] -= 1
        graph, _ = mm.segment_graph(a, m, ranks, grid)
        self.assertEqual(graph[1, 0].tolist(), [6, 9, -1])


class RecoveryTest(unittest.TestCase):
    def make(self):
        a, m = fixture(); k = str(a["keys"][0]); m[k].update(variant="attack", attack_bearing=True, e=4, x=42, trajectory_class="execution")
        labels = {str(key): dict(e_analysis=None, e_final=None, recovery_spans=[]) for key in a["keys"]}
        labels[k] = dict(e_analysis=4, e_final=None, recovery_spans=[dict(channel="analysis", explicit_correction=False,
                           re_execution=True, span=dict(token_start_global=16, token_end_global=39))])
        return a, m, labels

    def test_actual_spans_can_precede_X_without_explicit_correction(self):
        a, m, labels = self.make(); events, counts, _, _ = mm.recovery_inventory(a, m, labels)
        self.assertEqual(events[0]["relation"], "before_X"); self.assertEqual(counts["counts"]["explicit_correction"], 0)
        self.assertEqual(events[0]["reasons"], ["available"]*3+["short_labelled_span"])

    def test_no_picking_later_more_convenient_recovery(self):
        a, m, labels = self.make(); rec = labels[str(a["keys"][0])]["recovery_spans"][0]
        rec["span"]["token_end_global"] = 18
        labels[str(a["keys"][0])]["recovery_spans"].append({**rec, "span": dict(token_start_global=24, token_end_global=47)})
        events, counts, _, _ = mm.recovery_inventory(a, m, labels)
        self.assertEqual(len(events), 1); self.assertEqual(counts["counts"]["spans"], 2)
        self.assertTrue(all(p < 0 for p in events[0]["posts"]))

    def test_channel_switch_not_paired_recovery(self):
        a, m, labels = self.make(); a["structure"][16:48, 1] = 2
        events, _, _, _ = mm.recovery_inventory(a, m, labels)
        self.assertEqual(events[0]["reasons"][0], "channel_or_step_boundary")

    def test_pseudo_R_future_does_not_select_donor(self):
        a, m, labels = self.make(); ranks = np.full((len(a["ends"]), 3), .5)
        events, _, index, ids = mm.recovery_inventory(a, m, labels)
        ds, ps, _ = mm.recovery_graph(a, m, ranks, events, index, ids)
        altered = dict(index)
        for ep, end in list(altered):
            if ep != 0 and end > 16: del altered[(ep, end)]
        ds2, ps2, _ = mm.recovery_graph(a, m, ranks, events, altered, ids)
        np.testing.assert_array_equal(ds, ds2); self.assertTrue((ps >= 0).any()); self.assertTrue((ps2 < 0).all())

    def test_common_curve_requires_all_query_and_donor_blocks(self):
        a, m, labels = self.make(); ranks = np.full((len(a["ends"]), 3), .5)
        events, _, index, ids = mm.recovery_inventory(a, m, labels)
        ds, ps, _ = mm.recovery_graph(a, m, ranks, events, index, ids)
        v, _ = mm.recovery_values(a, ranks, events, ds, ps)
        common, _ = mm.recovery_values(a, ranks, events, ds, ps, common=True)
        self.assertTrue(np.isfinite(v[0, 0, :3]).all()); self.assertTrue(np.isnan(common).all())

    def test_invalid_recovery_timing_rejected(self):
        a, m, labels = self.make(); labels[str(a["keys"][0])]["recovery_spans"][0]["span"]["token_start_global"] = 1
        with self.assertRaises(ValueError): mm.recovery_inventory(a, m, labels)


class GuardTest(unittest.TestCase):
    def test_only_M7_M8_and_exact_Gdev_labels(self):
        guard = run.M14AccessGuard(ROOT)
        for p in (run.LABELS, run.OUT/"result.json", run.m8.OUT/"look_inventory.npz"): guard.check_path(p)
        for p in (ROOT/"artifacts/agent_v2/dataset_g/g_conf/trace.json", ROOT/"artifacts/agent_v2/dataset_g/annotations/g_conf/final_unblinded.jsonl",
                  ROOT/"artifacts/agent_v2/codex_g/m3_online_mass_v1/topk_cache/g_dev/a.safetensors"):
            with self.assertRaises(PermissionError): guard.check_path(p)

    def test_old_outputs_cannot_be_written(self):
        guard = run.M14AccessGuard(ROOT)
        with self.assertRaises(PermissionError): guard.audit("open", (str(run.m8.OUT/"look_inventory.npz"), None, os.O_WRONLY))


if __name__ == "__main__": unittest.main()
