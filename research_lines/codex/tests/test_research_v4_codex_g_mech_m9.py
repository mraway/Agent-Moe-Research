"""Synthetic mechanism-M9 tests. No real data, tokenizer or model weights read."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src")); sys.path.insert(0, str(ROOT/"scripts"))
from research_v4 import codex_g_mech_m9 as m9, codex_g_mech_m9_math as dm
from research_v4.codex_g_mech_m9_audit import enumerate_experts, scalar_measures, verify_summary
from research_v4 import codex_g_m8_math as mm
from phase_a import generation


class DecompositionTest(unittest.TestCase):
    def test_current_identity_does_not_match_history_counterexample(self):
        terms = np.zeros((2, 8, 24, 2))
        # Identical current-token contributions; all difference in seven previous tokens.
        terms[:, -1, 12, :] = 4
        terms[0, :7, 15, :] = 8
        values, _ = dm.measures(terms)
        diff = values[0]-values[1]
        self.assertEqual(diff[1], 0.); self.assertEqual(diff[0], 7.)
        self.assertEqual(diff[3], 7.); self.assertEqual(diff[2], 0.)

    def test_endpoint_only_and_per_layer_conservation(self):
        terms = np.zeros((2, 8, 24, 2)); terms[0, -1, 17, 0] = 16.; terms[0, -1, 17, 1] = 24.
        v, layer = dm.measures(terms)
        np.testing.assert_array_equal(v[0, :4], [2., 16., 2., 0.])
        self.assertEqual(v[0, 6], 16.); self.assertEqual(v[0, 9], 2.)
        self.assertEqual(layer[0, 17], 16.); self.assertEqual(layer[0, 24+17], 2.)
        check, profile = scalar_measures(terms)
        np.testing.assert_array_equal(v, check); np.testing.assert_array_equal(layer, profile)

    def test_all_lags_layers_independent_enumeration(self):
        rng = np.random.default_rng(712)
        ids = np.array([[rng.choice(32, 4, replace=False) for _ in range(30)] for _ in range(24)])
        logits = rng.normal(size=(24, 30, 32)); q = rng.uniform(.001, .15, size=(24, 32))
        rarity = np.where(q < .02, -np.log(q), 0.)
        terms = dm.token_layer_terms(ids, logits, rarity)
        np.testing.assert_allclose(terms, enumerate_experts(ids, logits, q, .02), rtol=0, atol=1e-13)
        windows = np.array([terms[t-7:t+1] for t in range(7, 30)])
        v, layer = dm.measures(windows); expected, p = scalar_measures(windows)
        np.testing.assert_allclose(v, expected, atol=1e-12); np.testing.assert_allclose(layer, p, atol=1e-12)
        for o in (0, 10):
            np.testing.assert_allclose(v[:, o], v[:, o+2]+v[:, o+3])
            np.testing.assert_allclose(v[:, o+1], v[:, o+4:o+7].sum(1))
            np.testing.assert_allclose(v[:, o], v[:, o+7:o+10].sum(1))

    def test_actual_selected_ids_and_translation_invariant_margin(self):
        ids = np.array([[[0, 1]]]); logits = np.array([[[1., 2., 100.]]]); rarity = np.array([[2., 3., 900.]])
        terms = dm.token_layer_terms(ids, logits, rarity)
        np.testing.assert_array_equal(terms, [[[5., 8.]]])
        np.testing.assert_array_equal(terms, dm.token_layer_terms(ids, logits+91, rarity))
        logits[:, :, 1] = 1.
        np.testing.assert_array_equal(dm.token_layer_terms(ids, logits, rarity), [[[5., 5.]]])

    def test_future_does_not_change_prefix(self):
        ids = np.tile(np.arange(4), (24, 25, 1)); logits = np.ones((24, 25, 32)); rarity = np.ones((24, 32))
        first = dm.token_layer_terms(ids, logits, rarity)
        ids[:, 16:, :] += 5; logits[:, 16:, 5] += 100
        altered = dm.token_layer_terms(ids, logits, rarity)
        np.testing.assert_array_equal(first[:16], altered[:16])

    def test_signed_shares_not_clipped_or_called_variance(self):
        mean = np.zeros(20); mean[0] = -1; mean[2] = 2; mean[10] = 1; mean[12] = 2
        self.assertEqual(dm.signed_shares(mean), {"S": -2., "CW": 2.})
        mean[0] = 1e-12
        self.assertIsNone(dm.signed_shares(mean)["S"])
        self.assertEqual(dm.signed_shares(None), {"S": None, "CW": None})

    def test_frozen_donors_and_empty_support(self):
        f = np.array([[10., 20.], [2., 3.], [4., 5.]])
        d = dm.differences(f, np.array([0, 1, 2]), [0, 1], np.array([[1, 2, -1], [-1, -1, -1]]))
        np.testing.assert_array_equal(d[0], [7., 16.]); self.assertTrue(np.isnan(d[1]).all())

    def test_invalid_geometry(self):
        with self.assertRaises(ValueError): dm.measures(np.zeros((3, 7, 24, 2)))
        with self.assertRaises(ValueError): dm.token_layer_terms(np.zeros((24, 5, 4)), np.zeros((23, 5, 32)), np.zeros((24, 32)))


class ContextAndSummaryTest(unittest.TestCase):
    def test_context_never_crosses_step_or_uses_future(self):
        tokens = np.arange(80)
        lo, prefix = dm.causal_context(tokens, 47, [45, 35])
        self.assertEqual(lo, 45); np.testing.assert_array_equal(prefix, [45, 46, 47])
        truncated = dm.causal_context(tokens[:48], 47, [45, 3])
        self.assertEqual(lo, truncated[0]); np.testing.assert_array_equal(prefix, truncated[1])
        lo, prefix = dm.causal_context(tokens, 40, [45, 35])
        self.assertEqual(lo, 9); self.assertEqual(len(prefix), 32)

    def test_unicode_categories_not_domain_rules(self):
        for piece, expected in (("", "other"), (" \n", "whitespace"), (" In", "letter"), ("任务", "letter"),
                                ("3.14", "number"), (";—+", "punctuation_symbol"), ("😀", "punctuation_symbol"), ("x9", "letter")):
            self.assertEqual(dm.category(piece), expected)
        self.assertEqual(dm.category("<|final|>", special=True), "special")

    def test_episode_equal_weighting_and_independent_bootstrap(self):
        a = dict(keys=np.array(["a", "b", "c"]), episode=np.array([0, 0, 1, 2]), ends=np.arange(4),
                 structure=np.zeros((4, 6), int), tokens=np.zeros((4, 8), int), tu=np.zeros(4), valid=np.ones(4, bool))
        metadata = {k: {"family": "f"+str(i % 2), "tier": "T"+str(i)} for i, k in enumerate(a["keys"])}
        values = np.repeat(np.array([0., 0., 10., 20.])[:, None], 20, axis=1)
        f = mm.MatchFeatures(**a); qs = np.arange(4)
        r = mm.effect_summary(f, metadata, qs, values)
        self.assertEqual(r["mean"], [10.]*20)
        verify_summary(r, qs, values, a, metadata)
        empty = mm.effect_summary(f, metadata, np.array([], int), np.empty((0, 20)))
        verify_summary(empty, np.array([], int), np.empty((0, 20)), a, metadata)

    def test_guard_rejects_weights_raw_traces_sealed_and_old_writes(self):
        guard = m9.M9AccessGuard(ROOT, "analysis")
        for p in (ROOT/"artifacts/agent_v2/dataset_g/g_conf/trace.json", ROOT/"artifacts/agent_v2/dataset_g/g_dev/a/trace.json",
                  m9.TOKENIZER.parent/"model.safetensors"):
            with self.assertRaises(PermissionError): guard.check_path(p)
        guard.check_path(m9.TOKENIZER); guard.check_path(m9.OUT/"result.json")
        import os
        with self.assertRaises(PermissionError): guard.audit("open", (str(m9.m8.OUT/"result.json"), "w", os.O_WRONLY))


class GenerationTimingTest(unittest.TestCase):
    def test_routed_input_is_already_sampled_current_token(self):
        events = []
        class FakeTokenizer:
            eos_token_id = 99
            def apply_chat_template(self, messages, **kwargs):
                return "prompt" if kwargs.get("tokenize") is False else torch.tensor([[1, 2]])
            def decode(self, tokens, **kwargs): return " ".join(str(int(t)) for t in tokens)
        class FakeModel:
            device = torch.device("cpu")
            def __call__(self, input_ids, **kwargs):
                tokens = input_ids[0].tolist(); events.append(("forward", tokens))
                logits = torch.full((1, 1, 100), -100.)
                logits[0, 0, {2: 3, 3: 4, 4: 99}[tokens[-1]]] = 10.
                return SimpleNamespace(logits=logits, past_key_values="cache")
        class FakeRecorder:
            @contextmanager
            def record_step(self, *, phase, input_ids, **kwargs):
                events.append(("capture_enter", phase, input_ids[0].tolist()))
                yield
                events.append(("capture_exit", phase))
        original = generation.select_next_token
        def sample(*args, **kwargs):
            value = original(*args, **kwargs); events.append(("sample", int(value.item()))); return value
        annotations = SimpleNamespace(roles=("user",)*2, conversation_turns=(0,)*2, agent_steps=(0,)*2, tool_boundaries=(False,)*2)
        with patch.object(generation, "annotate_chat_prompt", return_value=annotations), patch.object(generation, "select_next_token", side_effect=sample):
            output = generation.generate_routed_turn(model=FakeModel(), tokenizer=FakeTokenizer(), messages=[{"role":"user", "content":"hi"}],
                        recorder=FakeRecorder(), conversation_turn=0, agent_step=0, max_new_tokens=2, assistant_protocol="json_action_or_text")
        self.assertEqual(output.output_token_ids, [3, 4])
        self.assertEqual([x[2] for x in events if x[:2] == ("capture_enter", "decode")], [[3], [4]])
        for token in (3, 4):
            self.assertLess(events.index(("sample", token)), events.index(("capture_enter", "decode", [token])))
            self.assertLess(events.index(("capture_enter", "decode", [token])), events.index(("forward", [token])))
        self.assertLess(events.index(("forward", [3])), events.index(("sample", 4)))


if __name__ == "__main__": unittest.main()
