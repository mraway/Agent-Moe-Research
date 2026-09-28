"""M6 synthetic-only tests: no G data, labels or sealed metadata are read."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from research_v2 import io_g, trm3, trm3_g
from research_v4 import codex_g_m6 as m6
from research_v4.codex_g_m6_statistics import RelativeWeightRare, PersistentRare, causal_streak, selected_log_margin
from research_v4.codex_g_m6_audit import first, reason, exact_mcnemar
from test_research_v4_codex_g_m3 import episode
from test_research_v4_v3_2 import normal_labels

torch.set_num_threads(4)


class StatisticsTest(unittest.TestCase):
    def setUp(self):
        self.pool = [episode(i) for i in range(4)]

    def test_zero_strength_exact_S_without_loading_logits(self):
        s = trm3_g.RareSurprisal().fit(self.pool, m6.VIEW)
        for cls in (RelativeWeightRare, PersistentRare):
            stat = cls(strength=0).fit(self.pool, m6.VIEW)
            torch.testing.assert_close(s.q, stat.q, rtol=0, atol=0)
            with patch.object(self.pool[0], "router_logits", side_effect=AssertionError("unnecessary logits")):
                np.testing.assert_array_equal(s.stream(self.pool[0], m6.VIEW)[1], stat.stream(self.pool[0], m6.VIEW)[1])

    def test_log_margin_actual_support_and_mass_invariance(self):
        logits = torch.tensor([[[1., 4., 2., -1., 15., 8.]]], dtype=torch.float64)
        ids = torch.tensor([[[0, 1, 2, 3]]])  # Deliberately not argtopk.
        expected = torch.tensor([[[2., 5., 3., 0.]]], dtype=torch.float64)
        g = selected_log_margin(logits, ids)
        torch.testing.assert_close(g, expected, rtol=0, atol=0)
        p = logits.softmax(-1).gather(-1, ids)
        w = p / p.sum(-1, keepdim=True)
        for values in (p, w):
            torch.testing.assert_close(g, (values / values.amin(-1, keepdim=True)).log(), rtol=1e-14, atol=1e-14)
        changed = logits.clone(); changed[:, :, 4:] += 50
        torch.testing.assert_close(g, selected_log_margin(changed, ids), rtol=0, atol=0)
        torch.testing.assert_close(g, selected_log_margin(logits + 1000, ids), rtol=0, atol=0)
        torch.testing.assert_close(selected_log_margin(logits * 0, ids), g * 0, rtol=0, atol=0)

    def test_streak_reference_loop_and_boundaries(self):
        g = torch.Generator().manual_seed(567)
        ids = torch.stack([torch.randperm(7, generator=g)[:3] for _ in range(46)]).reshape(2, 23, 3)
        tags = ["analysis"] * 10 + ["final"] * 13
        starts = (0, 7, 16)
        observed = causal_streak(ids, 7, tags, starts)
        expected = torch.zeros_like(ids)
        for layer in range(2):
            counts = [0] * 7
            for t in range(23):
                if t in starts or (t and tags[t] != tags[t-1]): counts = [0] * 7
                selected = ids[layer, t].tolist()
                counts = [min(8, counts[e] + 1) if e in selected else 0 for e in range(7)]
                expected[layer, t] = torch.tensor([counts[e] for e in selected])
        torch.testing.assert_close(observed, expected, rtol=0, atol=0)
        always = torch.zeros((1, 20, 1), dtype=torch.long)
        self.assertEqual(causal_streak(always, 2, ["analysis"] * 20)[0, :, 0].tolist(), list(range(1, 9)) + [8] * 12)

    def test_order_information_with_equal_counts(self):
        a = torch.tensor([0, 0, 0, 0, 1, 1, 1, 1]).reshape(1, 8, 1)
        b = torch.tensor([0, 1, 0, 1, 0, 1, 0, 1]).reshape(1, 8, 1)
        self.assertEqual(int((a == 0).sum()), int((b == 0).sum()))
        self.assertGreater(int(causal_streak(a, 2, ["final"]*8).sum()), int(causal_streak(b, 2, ["final"]*8).sum()))

    def test_native_grid_monotone_addition_and_exact_scalar(self):
        s = trm3_g.RareSurprisal().fit(self.pool, m6.VIEW)
        target = self.pool[0]
        for cls in (RelativeWeightRare, PersistentRare):
            stat = cls().fit(self.pool, m6.VIEW)
            self.assertIs(type(stat).stream, trm3_g.GStatistic.stream)
            se, ss, st, so = s.stream(target, m6.VIEW)
            e, scores, t, o = stat.stream(target, m6.VIEW)
            np.testing.assert_array_equal(e, se); np.testing.assert_array_equal(o, so)
            self.assertEqual(t, st); self.assertTrue((scores >= ss - 1e-10).all())
            if cls is PersistentRare: self.assertTrue((scores <= 2*ss + 1e-10).all())
            ids = target.top_k_ids
            manual = torch.zeros(target.token_count, dtype=torch.float64)
            extra = stat.extra(target, ids)
            for l in range(24):
                for pos in range(target.token_count):
                    for j in range(4):
                        manual[pos] += stat.surprisal[l, ids[l, pos, j]] * (1 + extra[l, pos, j])
            torch.testing.assert_close(stat.per_token(target).flatten(), manual, rtol=1e-12, atol=1e-12)
            self.assertEqual(stat.top_coordinates(target, 12), [])

    def test_causal_future_mutation_and_prefix_with_future_step_spans(self):
        original = self.pool[0]
        original.step_spans = ({"global_token_offset": 0}, {"global_token_offset": 11}, {"global_token_offset": 35})
        for cls in (RelativeWeightRare, PersistentRare):
            stat = cls().fit(self.pool, m6.VIEW)
            altered = copy.deepcopy(original)
            altered.logits[:, 33:] *= -30
            altered.top_k_ids[:, 33:] = altered.logits[:, 33:].topk(4, -1).indices
            ends, scores, _, _ = stat.stream(original, m6.VIEW)
            changed = stat.stream(altered, m6.VIEW)[1]
            np.testing.assert_array_equal(scores[ends < 33], changed[ends < 33])
            prefix = copy.deepcopy(original)
            prefix.logits = prefix.logits[:, :33]
            prefix.top_k_ids = prefix.top_k_ids[:, :33]
            prefix.token_ids = prefix.token_ids[:33]
            prefix.channel_tags = prefix.channel_tags[:33]
            pe, ps, _, _ = stat.stream(prefix, m6.VIEW)
            np.testing.assert_array_equal(pe, ends[ends < 33]); np.testing.assert_array_equal(ps, scores[ends < 33])

    def test_state_roundtrip_config_and_tamper(self):
        for cls in (RelativeWeightRare, PersistentRare):
            stat = cls().fit(self.pool, m6.VIEW)
            frozen = json.loads(json.dumps(stat.state_dict()))
            restored = cls().load_state(frozen)
            np.testing.assert_array_equal(stat.stream(self.pool[0], m6.VIEW)[1], restored.stream(self.pool[0], m6.VIEW)[1])
            with self.assertRaisesRegex(ValueError, "config"): cls(strength=0).load_state(frozen)
            frozen["q"][0][0] += .1
            with self.assertRaisesRegex(ValueError, "fingerprint"): cls().load_state(frozen)

    def test_fit_normal_only(self):
        for cls in (RelativeWeightRare, PersistentRare):
            with self.assertRaises(ValueError): cls().fit([episode(1, "attack")], m6.VIEW)
            bad = episode(); bad.labels = io_g.normalise_label_row(normal_labels(filter_pass=False))
            with self.assertRaises(ValueError): cls().fit([bad], m6.VIEW)


class ProtocolTest(unittest.TestCase):
    def test_guard_refuses_attack_in_stage1_and_all_other_routes(self):
        guard = m6.M6AccessGuard(ROOT, "calibrate")
        for path in (m6.RUN / "x--attack/trace.json", m6.M3 / "topk_cache/g_dev/x--attack--ep0.safetensors",
                     m6.RUN / "x--clean/steps/0000.safetensors", ROOT / "artifacts/agent_v2/dataset_g/g_conf/SEALED.json",
                     ROOT / "artifacts/agent_v2/dataset_g/annotations/g_conf/final_unblinded.jsonl"):
            with self.assertRaises(PermissionError): guard.check_path(path)
        good = m6.M3 / "logit_cache/g_dev/x--clean--ep0.logits.safetensors"
        self.assertEqual(guard.check_path(good), good.resolve())
        with self.assertRaises(AssertionError): m6.no_cache_write()

    def test_single_budget_and_exact_boundary_rules(self):
        for name in m6.CELLS:
            config = trm3_g.config_for_g([name], alpha=.1)
            self.assertEqual(len(config.channels), 1)
            self.assertEqual(config.channels[0].alpha, .1)
            self.assertEqual(trm3.effective_alpha(config, 99)["alpha_eff"], .1)
        ends = np.arange(7, 50)
        self.assertEqual(reason(10, 11, 30, ends), "pre_E")
        self.assertEqual(reason(11, 11, 30, ends), "hit")
        self.assertEqual(reason(46, 11, 30, ends), "hit")
        self.assertEqual(reason(47, 11, 30, ends), "late")
        self.assertEqual(reason(49, 11, 100, ends), "hit")
        self.assertEqual(reason(None, 60, 100, ends), "unreachable")
        self.assertEqual(first([7, 8, 9], [.2, .1, .09], .1), 8)
        self.assertEqual(exact_mcnemar(0, 0), 1.)
        self.assertEqual(exact_mcnemar(5, 0), .0625)

    def test_three_cells_two_stages_no_refit_and_raw_recording(self):
        normals, targets = [], []
        for i in range(18):
            normal = episode(i, "clean", scenario=f"s{i:03}")
            normals.append(normal)
            targets.extend((normal, episode(i + 200, "attack", scenario=f"s{i:03}")))
        table = {f"s{i:03}": i % 3 for i in range(18)}
        pools = m6.harness.fold_pools(normals, table, folds=3, filtered_only=True, normals_only_eval=True)
        states, cells, saved = {}, {}, {}
        with m6.collect_raw(saved):
            for name in m6.CELLS:
                cells[name] = m6.harness.run_cell_v32(name, args=m6.shared_args("calibrate"), view=m6.VIEW,
                    target_pool=normals, pools=pools, manifest_cell=None, cutpoints=(44, 46))
                states[name] = {"folds": cells[name]["_fold_states"]}
        self.assertEqual(set(saved), set(m6.CELLS))
        grid, points = m6.freeze_workpoints(cells, normals)
        for point in points.values(): self.assertLessEqual(point["measured_far"], point["reference_far"])
        replay_pools = m6.harness.fold_pools(targets, table, folds=3, filtered_only=True, normals_only_eval=False)
        with m6.restore_only():
            for name in m6.CELLS:
                replay = m6.harness.run_cell_v32(name, args=m6.shared_args("score"), view=m6.VIEW,
                    target_pool=targets, pools=replay_pools, manifest_cell=states[name], cutpoints=(44, 46))
                self.assertEqual(m6.m3.decision_fingerprint(cells[name]), m6.m3.decision_fingerprint(replay, cells[name]["_decisions"]))
            with self.assertRaises(AssertionError): m6.freeze_workpoints(cells, normals)
            for name in m6.CELLS:
                with self.assertRaises(AssertionError): trm3_g.STATISTICS[name]().fit(normals, m6.VIEW)


if __name__ == "__main__":
    unittest.main()
