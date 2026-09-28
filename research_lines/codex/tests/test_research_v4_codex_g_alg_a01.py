"""Synthetic-only A01 tests: these never open an experimental trace or routing cache."""
from io import BytesIO
import unittest

import numpy as np

from research_v4 import codex_g_alg_a01_math as am
from research_v4.codex_g_alg_a01_preflight import A01NormalGuard, ROOT, CACHE


def random_ps(seed, count=12):
    rng = np.random.default_rng(seed)
    ids = np.argsort(rng.random((24, count, 32)), axis=-1)[..., :4]
    logits = rng.normal(size=(24, count, 32)).astype(np.float32)
    return am.representations(ids, logits)


def records(n=5, count=12):
    return [(f"s{i}", f"k{i}", np.arange(7, count), random_ps(i, count)) for i in range(n)]


def brute_force(ps, ends, records_, scenario):
    scores, donors = [], []
    for end in ends:
        cell_scores, cell_donors = [], []
        for ci in range(4):
            rep = ci % 2
            a = ps[rep][end-7:end+1].astype(np.float64)
            if ci < 2:
                a = a.mean(0)
            by_scenario = {}
            for s, key, de, dp in sorted(records_, key=lambda r: (r[0], r[1])):
                if s == scenario:
                    continue
                for b_end in de:
                    b = dp[rep][b_end-7:b_end+1].astype(np.float64)
                    if ci < 2:
                        b = b.mean(0)
                    distance = .5*((np.sqrt(a)-np.sqrt(b))**2).sum()/24/(8 if ci >= 2 else 1)
                    candidate = (distance, key, int(b_end))
                    if s not in by_scenario or candidate < by_scenario[s]:
                        by_scenario[s] = candidate
            nearest = sorted((d, s, k, e) for s, (d, k, e) in by_scenario.items())[:3]
            cell_scores.append(np.mean([n[0] for n in nearest]))
            cell_donors.append([(k, e) for _, _, k, e in nearest])
        scores.append(cell_scores); donors.append(cell_donors)
    return np.asarray(scores), donors


class RepresentationTests(unittest.TestCase):
    def test_uses_actual_selection_not_logit_topk(self):
        ids = np.broadcast_to(np.arange(4), (24, 9, 4)).copy()
        logits = np.zeros((24, 9, 32), np.float32); logits[..., 31] = 100
        logits[..., 0] = 2
        u, w = am.representations(ids, logits)
        np.testing.assert_allclose(u.sum(-1), 1, atol=1e-6)
        np.testing.assert_allclose(w.sum(-1), 1, atol=1e-6)
        self.assertTrue((w[..., 31] == 0).all())
        self.assertTrue((u[..., :4] == .25).all())
        self.assertTrue((w[..., 0] > w[..., 1]).all())

    def test_rejects_bad_selection_and_nonfinite_logits(self):
        ids = np.zeros((24, 9, 4), np.int64)
        logits = np.zeros((24, 9, 32), np.float32)
        with self.assertRaises(ValueError): am.representations(ids, logits)
        ids[:] = np.arange(4); logits[0, 0, 0] = np.nan
        with self.assertRaises(ValueError): am.representations(ids, logits)
        logits[:] = 0; ids[0, 0, 0] = 32
        with self.assertRaises(ValueError): am.representations(ids, logits)

    def test_features_reject_bad_simplex_or_future_endpoint(self):
        u, _ = random_ps(0)
        for bad in (u*2, np.full_like(u, np.nan), -u):
            with self.assertRaises(ValueError): am.features(bad, [7])
        with self.assertRaises(ValueError): am.features(u, [len(u)])
        with self.assertRaises(ValueError): am.features(u, [6])

    def test_mean_loses_permutation_ordered_retains_it(self):
        p = np.zeros((8, 24, 32), np.float32)
        for t in range(8): p[t, :, t] = 1
        q = p[::-1].copy()
        tr, mr = am.features(p, [7]); tq, mq = am.features(q, [7])
        np.testing.assert_array_equal(mr, mq)
        self.assertAlmostEqual(float(.5*((tr-tq)**2).sum()/8), 1, places=6)

    def test_window_boundaries_and_monotonicity(self):
        tags = np.array(["analysis"]*9+["final"]*9)
        steps = np.array([0]*9+[1]*9)
        np.testing.assert_array_equal(am.valid_ends(np.array([7, 8, 16, 17]), tags, steps), [7, 8, 16, 17])
        for ends in ([9], [7, 7], [18], [6]):
            with self.assertRaises(ValueError): am.valid_ends(np.array(ends), tags, steps)
        with self.assertRaises(ValueError): am.valid_ends(np.array([9]), ["final"]*18, steps)


class NeighbourTests(unittest.TestCase):
    def test_all_four_exact_scores_against_independent_enumeration(self):
        rs = records(); bank = am.build_bank(rs)
        ps, ends = random_ps(20), np.array([7, 9, 11])
        actual, rows, _ = am.neighbour_scores(ps, ends, bank, "query", query_block=2, bank_block=7)
        expected, donors = brute_force(ps, ends, rs, "query")
        np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-6)
        for qi in range(len(ends)):
            for ci in range(4):
                self.assertEqual([(str(bank.keys[j]), int(bank.ends[j])) for j in rows[qi, ci]], donors[qi][ci])

    def test_entire_scenario_exclusion_including_other_arm(self):
        rs = records()
        query = random_ps(55)
        rs.extend([("self", "self/clean", np.array([7, 8]), query),
                   ("self", "self/benign_control", np.array([7, 8]), query)])
        bank = am.build_bank(rs)
        actual, rows, _ = am.neighbour_scores(query, [7, 8], bank, "self")
        expected, _ = brute_force(query, [7, 8], rs, "self")
        np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-6)
        self.assertNotIn(bank.scenarios.index("self"), bank.scenario_index[rows])

    def test_distinct_scenarios_not_distinct_windows(self):
        rs = records(); bank = am.build_bank(rs)
        _, rows, _ = am.neighbour_scores(rs[0][3], [7], bank, "q")
        for ci in range(4):
            self.assertEqual(len(set(bank.scenario_index[rows[0, ci]])), 3)

    def test_prefix_invariance(self):
        bank = am.build_bank(records())
        full = random_ps(21, 28); prefix = tuple(p[:12] for p in full)
        before = am.neighbour_scores(prefix, np.arange(7, 12), bank, "q")
        after = am.neighbour_scores(full, np.arange(7, 28), bank, "q")
        np.testing.assert_allclose(before[0], after[0][:5], rtol=0, atol=2e-6)
        np.testing.assert_array_equal(before[1], after[1][:5])

    def test_block_invariance(self):
        bank = am.build_bank(records())
        ps = random_ps(37); ends = np.arange(7, 12)
        a = am.neighbour_scores(ps, ends, bank, "q", query_block=1, bank_block=1)
        b = am.neighbour_scores(ps, ends, bank, "q", query_block=3, bank_block=11)
        np.testing.assert_allclose(a[0], b[0], rtol=0, atol=2e-6)
        np.testing.assert_array_equal(a[1], b[1])

    def test_save_restore_without_refitting(self):
        bank = am.build_bank(records())
        buf = BytesIO(); np.savez_compressed(buf, **bank.state()); buf.seek(0)
        with np.load(buf, allow_pickle=False) as state: restored = am.Bank.restore(dict(state))
        a = am.neighbour_scores(random_ps(19), [7, 8], bank, "q")
        b = am.neighbour_scores(random_ps(19), [7, 8], restored, "q")
        for av, bv in zip(a, b): np.testing.assert_array_equal(av, bv)

    def test_ties_are_lexicographic(self):
        ps = random_ps(0, 8)
        rs = [(s, key, np.array([7]), ps) for s, key in
              (("z", "z"), ("a", "a/2"), ("a", "a/1"), ("b", "b"), ("c", "c"))]
        bank = am.build_bank(rs)
        _, rows, _ = am.neighbour_scores(ps, [7], bank, "q")
        for ci in range(4): self.assertEqual(list(bank.keys[rows[0, ci]]), ["a/1", "b", "c"])

    def test_insufficient_bank_fails_not_zero(self):
        with self.assertRaises(ValueError): am.build_bank(records(2))
        with self.assertRaises(ValueError): am.build_bank([])
        bank = am.build_bank(records(3))
        with self.assertRaises(ValueError): am.neighbour_scores(random_ps(0), [7], bank, "s0")

    def test_empty_query_preserved(self):
        bank = am.build_bank(records())
        s, rows, _ = am.neighbour_scores(random_ps(0), [], bank, "q")
        self.assertEqual(s.shape, (0, 4)); self.assertEqual(rows.shape, (0, 4, 3))


class ProtocolTests(unittest.TestCase):
    def meta(self, n=4):
        meta, streams = {}, {}
        for fold in range(3):
            for s in range(n):
                key = f"f{fold}s{s}"
                meta[key] = {"fold": fold, "scenario": key, "filter_pass": True,
                             "variant": "clean", "episode_index": 0}
                streams[key] = {"ends": np.array([7, 8]), "tags": np.array(["final", "final"])}
        return meta, streams

    def test_all_rotations_and_self_exclusion_coverage(self):
        meta, streams = self.meta()
        result = am.support_audit(meta, streams)
        self.assertEqual(result["status"], "coverage_passed")
        for outer, row in result["banks"].items():
            self.assertEqual({meta[k]["fold"] for k in row["fit_keys"]}, {(int(outer)+1)%3})
        meta, streams = self.meta(n=3)
        result = am.support_audit(meta, streams)
        self.assertEqual(len(result["unsupported_queries"]), 9)
        self.assertEqual({r["role"] for r in result["unsupported_queries"]}, {"fit"})

    def test_unseen_channel_does_not_fallback(self):
        meta, streams = self.meta()
        streams["f0s0"]["tags"] = np.array(["commentary", "final"])
        result = am.support_audit(meta, streams)
        failures = [r for r in result["unsupported_queries"] if r["condition"] == "commentary/ep0"]
        self.assertEqual(len(failures), 3)
        self.assertTrue(all(r["available_scenarios_after_exclusion"] == 0 for r in failures))

    def test_round_conditions_and_filtered_fit(self):
        meta, streams = self.meta()
        meta["f0s0"]["episode_index"] = 1
        meta["f1s0"]["filter_pass"] = None
        result = am.support_audit(meta, streams)
        self.assertNotIn("f1s0", result["banks"]["0"]["fit_keys"])
        self.assertTrue(any(r["condition"] == "final/ep1" for r in result["unsupported_queries"]))

    def test_guard_rejects_sealed_attack_and_unapproved_cache(self):
        guard = A01NormalGuard(ROOT)
        for path in (ROOT/"artifacts/agent_v2/dataset_g/g_conf/trace.json",
                     ROOT/"artifacts/agent_v2/dataset_g/annotations/g_conf/labels.jsonl",
                     CACHE/"topk_cache/g_dev/g-dev-001--attack--ep0.safetensors",
                     ROOT/"models/weights.safetensors",
                     ROOT/"artifacts/agent_v2/codex_g/m7_full_episode_v1/score/episode_metadata.json"):
            with self.assertRaises(PermissionError): guard.check_path(path)
        guard.check_path(CACHE/"topk_cache/g_dev/g-dev-001--clean--ep0.safetensors")


if __name__ == "__main__": unittest.main()
