"""Synthetic-only tests; no G routing, annotations or sealed metadata are read."""
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
from research_v4 import codex_g_m3 as m3
from research_v4.codex_g_m3_statistics import IdentityJS, SelectedMass, TAGS
from test_research_v4_detectors_g import make_episode
from test_research_v4_v3_2 import normal_labels, attack_labels

torch.set_num_threads(4)


def episode(i=0, variant="clean", tokens=45, scenario=None):
    generator = torch.Generator().manual_seed(57 + i)
    labels = attack_labels(e=18, x=30) if variant == "attack" else normal_labels()
    out = make_episode(f"s{i:03}--{variant}", generator=generator, tokens=tokens, variant=variant,
                       labels=labels, scenario=scenario or f"s{i:03}", family=f"fam{i % 4}", analysis=16, commentary=12)
    out.logits = torch.randn((24, tokens, 32), generator=generator) + torch.linspace(4., 0., 32)
    out.top_k_ids = out.logits.topk(4, -1).indices
    return out


class StatisticsTest(unittest.TestCase):
    def setUp(self):
        self.pool = [episode(i) for i in range(4)]

    def test_uses_native_grid_and_width(self):
        for cls in (IdentityJS, SelectedMass):
            stat = cls().fit(self.pool, m3.VIEW)
            self.assertIs(type(stat).stream, trm3_g.GStatistic.stream)
            self.assertEqual(stat.window_width, 8)
            ends, _, tags, ordinals = stat.stream(self.pool[0], m3.VIEW)
            native = trm3_g.segmented_windows(torch.ones(45, 1), self.pool[0].channel_tags, m3.VIEW, 8)
            np.testing.assert_array_equal(ends, native[0])
            self.assertEqual(tags, native[2])
            np.testing.assert_array_equal(ordinals, native[3])

    def test_causal_prefix_and_future_mutation(self):
        for cls in (IdentityJS, SelectedMass):
            stat = cls().fit(self.pool, m3.VIEW)
            original = self.pool[0]
            altered = copy.deepcopy(original)
            altered.logits[:, 33:] *= -30
            altered.top_k_ids[:, 33:] = altered.logits[:, 33:].topk(4, -1).indices
            ends, scores, _, _ = stat.stream(original, m3.VIEW)
            changed = stat.stream(altered, m3.VIEW)[1]
            np.testing.assert_array_equal(scores[ends < 33], changed[ends < 33])
            prefix = copy.deepcopy(original)
            prefix.logits = prefix.logits[:, :33]
            prefix.top_k_ids = prefix.top_k_ids[:, :33]
            prefix.token_ids = prefix.token_ids[:33]
            prefix.channel_tags = prefix.channel_tags[:33]
            pe, ps, _, _ = stat.stream(prefix, m3.VIEW)
            np.testing.assert_array_equal(pe, ends[ends < 33])
            np.testing.assert_array_equal(ps, scores[ends < 33])

    def test_state_json_roundtrip_and_tamper(self):
        for cls in (IdentityJS, SelectedMass):
            stat = cls().fit(self.pool, m3.VIEW)
            frozen = json.loads(json.dumps(stat.state_dict()))
            restored = cls().load_state(frozen)
            np.testing.assert_array_equal(stat.stream(self.pool[1], m3.VIEW)[1], restored.stream(self.pool[1], m3.VIEW)[1])
            self.assertEqual(stat.describe()["fingerprint"], restored.describe()["fingerprint"])
            frozen["config"]["window_width"] = 9
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                cls().load_state(frozen)
            with self.assertRaisesRegex(ValueError, "config"):
                cls(window_width=4).load_state(stat.state_dict())

    def test_selected_mass_uses_full_probability_not_topk_normalization(self):
        target = episode()
        target.logits.zero_()
        target.top_k_ids = torch.tensor([2, 7, 15, 31]).expand(24, 45, 4).clone()
        stat = SelectedMass().fit(self.pool, m3.VIEW)
        torch.testing.assert_close(stat.per_token(target), torch.full((45, 24), .125, dtype=torch.float64), rtol=0, atol=0)
        target2 = copy.deepcopy(target)
        target2.logits.scatter_(-1, target2.top_k_ids, 2.)
        expected = 4 * np.exp(2) / (4 * np.exp(2) + 28)
        torch.testing.assert_close(stat.per_token(target2), torch.full((45, 24), expected, dtype=torch.float64), rtol=1e-6, atol=1e-8)
        target3 = copy.deepcopy(target2)
        # Deliberately non-argtopk cached IDs prove use of ACTUAL cached support.
        target3.top_k_ids = torch.tensor([0, 1, 3, 4]).expand(24, 45, 4).clone()
        self.assertTrue(bool((stat.per_token(target3) < stat.per_token(target2)).all()))

    def test_identity_q_is_channel_token_weighted(self):
        stat = IdentityJS().fit(self.pool, m3.VIEW)
        for c, tag in enumerate(TAGS):
            tokens = [stat._uniform(e)[torch.tensor([t == tag for t in e.channel_tags])] for e in self.pool]
            torch.testing.assert_close(stat.q[c], torch.cat(tokens).mean(0), rtol=0, atol=0)
        features = torch.cat((stat.q.reshape(3, -1), torch.eye(3, dtype=torch.float64)), 1)
        torch.testing.assert_close(stat.window_score(features), torch.zeros(3, dtype=torch.float64), rtol=0, atol=0)

    def test_fit_refuses_attacks_or_unfiltered_normals(self):
        bad = episode(9, "attack")
        for cls in (IdentityJS, SelectedMass):
            with self.assertRaises(ValueError): cls().fit([*self.pool, bad], m3.VIEW)
            unfiltered = episode()
            unfiltered.labels = io_g.normalise_label_row(normal_labels(filter_pass=False))
            with self.assertRaises(ValueError): cls().fit([unfiltered], m3.VIEW)


class ProtocolTest(unittest.TestCase):
    def test_normal_path_filter_needs_no_trace_reads(self):
        scenarios = [
            {"pair_group_id": "s1", "factory": {"collected_arms": ["clean", "benign_control", "attack"]}},
            {"pair_group_id": "s2", "factory": {"collected_arms": ["clean"], "normal_variant": "legitimate_refusal"}},
            {"pair_group_id": "s3", "factory": {"collected_arms": ["clean"], "normal_variant": "benign_lexical"}},
        ]
        paths = [Path(f"/unused/{s}/trace.json") for s in ("s1--clean", "s1--attack", "s1--benign_control", "s2--clean", "s3--clean")]
        with patch.object(Path, "read_text", side_effect=AssertionError("no reads")):
            selected = m3.normal_trace_paths(paths, scenarios)
        self.assertEqual([p.parent.name for p in selected], ["s1--clean", "s1--benign_control", "s3--clean"])
        nested = [Path("/unused/batch/s1/clean/trace.json"), Path("/unused/batch/s1/attack/trace.json")]
        self.assertEqual(m3.normal_trace_paths(nested, scenarios), nested[:1])

    def test_access_guards_before_any_open(self):
        guard = m3.M3AccessGuard(ROOT, m3.OUT, "calibrate")
        for path in (m3.RUN / "x--attack/trace.json", m3.RUN / "x/attack/trace.json", m3.OUT / "x--attack--ep1.safetensors",
                     ROOT / "artifacts/agent_v2/dataset_g/g_conf/trace.json",
                     ROOT / "artifacts/agent_v2/dataset_g/annotations/g_conf/labels.jsonl"):
            with self.assertRaises(PermissionError): guard.check_path(path)
        self.assertEqual(guard.check_path(m3.RUN / "x--clean/trace.json"), (m3.RUN / "x--clean/trace.json").resolve())

    def test_total_joint_budget(self):
        config = trm3_g.config_for_g(["CU", "CM"], alpha=.1)
        self.assertEqual(config.alpha, .1)
        self.assertEqual([c.alpha for c in config.channels], [.05, .05])
        self.assertEqual([c.weight for c in config.channels], [.5, .5])
        effective = trm3.effective_alpha(config, 99)
        self.assertAlmostEqual(effective["alpha_eff"], .10)

    def test_two_stage_joint_reuses_native_single_states(self):
        normals, target = [], []
        for i in range(18):
            clean = episode(i, "clean", scenario=f"s{i:03}")
            control = episode(i + 100, "benign_control", scenario=f"s{i:03}")
            normals.extend((clean, control))
            target.extend((clean, control, episode(i + 200, "attack", scenario=f"s{i:03}")))
        table = {f"s{i:03}": i % 3 for i in range(18)}
        pools = m3.harness.fold_pools(normals, table, folds=3, filtered_only=True, normals_only_eval=True)
        args = m3.shared_args("calibrate", m3.OUT)
        states, single = {}, {}
        for name in m3.SINGLES:
            single[name] = m3.harness.run_cell_v32(name, args=args, view=m3.VIEW, target_pool=normals, pools=pools,
                                                manifest_cell=None, cutpoints=(44, 46))
            states[name] = {"folds": single[name]["_fold_states"]}
        joint = m3.score_joint(normals, pools, states, args, (44, 46))
        for key, decision in joint["_decisions"].items():
            cu, cm = [single[n]["_decisions"][key] for n in ("CU", "CM")]
            expected = np.minimum(1, 2 * np.minimum(cu.p_fused, cm.p_fused))
            np.testing.assert_array_equal(decision.p_fused, expected)
        replay_pools = m3.harness.fold_pools(target, table, folds=3, filtered_only=True, normals_only_eval=False)
        args2 = m3.shared_args("score", m3.OUT)
        with m3.restore_only():
            for name in m3.SINGLES:
                replay = m3.harness.run_cell_v32(name, args=args2, view=m3.VIEW, target_pool=target, pools=replay_pools,
                                               manifest_cell=states[name], cutpoints=(44, 46))
                self.assertEqual(m3.decision_fingerprint(single[name]), m3.decision_fingerprint(replay, single[name]["_decisions"]))
                m3.add_diagnostics(replay, target, .1)
            joint_replay = m3.score_joint(target, replay_pools, states, args2, (44, 46))
            self.assertEqual(m3.decision_fingerprint(joint), m3.decision_fingerprint(joint_replay, joint["_decisions"]))
            with self.assertRaises(AssertionError): m3.freeze_workpoints(single, normals)


if __name__ == "__main__":
    unittest.main()
