"""Unit tests for the PDM scorer (proposal 3, spec section 5). Synthetic data, CPU, fast."""

from __future__ import annotations

import math
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research_v2.scorers import build  # noqa: E402
from research_v2.scorers.pdm import EXPERTS, TOP_K, PdmScorer  # noqa: E402


@dataclass
class FakeTrace:
    top_k_ids: torch.Tensor

    @property
    def token_count(self) -> int:
        return int(self.top_k_ids.shape[1])


def constant_trace(tokens: int, expert: int = 0, layers: int = 16) -> FakeTrace:
    ids = torch.full((layers, tokens, TOP_K), expert, dtype=torch.long)
    return FakeTrace(ids)


def random_trace(tokens: int, seed: int, layers: int = 16) -> FakeTrace:
    generator = torch.Generator().manual_seed(seed)
    ids = torch.empty((layers, tokens, TOP_K), dtype=torch.long)
    for layer in range(layers):
        for token in range(tokens):
            ids[layer, token] = torch.randperm(EXPERTS, generator=generator)[:TOP_K]
    return FakeTrace(ids)


def degenerate_topk_trace(tokens: int, seed: int, layers: int = 16) -> FakeTrace:
    """Every top-8 slot holds the same expert, so the set model must reduce to top-1."""

    generator = torch.Generator().manual_seed(seed)
    top1 = torch.randint(EXPERTS, (layers, tokens), generator=generator)
    return FakeTrace(top1.unsqueeze(-1).expand(layers, tokens, TOP_K).contiguous())


class RegistryTests(unittest.TestCase):
    def test_registered(self) -> None:
        from research_v2.scorers import available

        self.assertIn("pdm", available())
        scorer = build("pdm", {"model": "d1", "window_width": 4})
        self.assertEqual(scorer.window_width, 4)
        self.assertEqual(scorer.config()["model"], "d1")

    def test_rejects_unknown_model(self) -> None:
        with self.assertRaises(ValueError):
            PdmScorer(model="d9")


class CountModelTests(unittest.TestCase):
    def test_depth_chain_matches_closed_form(self) -> None:
        """All-constant routing: every table has one occupied cell, smoothing alpha=0.5."""

        tokens = 40
        scorer = PdmScorer(window_width=1, model="d1", smoothing=0.5)
        state = scorer.fit([constant_trace(tokens)])
        self.assertEqual(state.token_count, tokens)
        self.assertEqual(state.trace_count, 1)
        raw = scorer._components(state, constant_trace(5).top_k_ids)["u"]
        alpha = 0.5
        p_initial = (tokens + alpha) / (tokens + EXPERTS * alpha)
        p_transition = (tokens + alpha) / (tokens + EXPERTS * alpha)
        expected = -(math.log(p_initial) + 15 * math.log(p_transition))
        self.assertAlmostEqual(float(raw[0]), expected, places=9)
        self.assertTrue(bool(torch.allclose(raw, raw[0].expand_as(raw))))

    def test_unseen_transition_is_more_surprising(self) -> None:
        scorer = PdmScorer(window_width=1, model="d1", smoothing=0.5)
        state = scorer.fit([constant_trace(50, expert=0)])
        seen = scorer._components(state, constant_trace(3, expert=0).top_k_ids)["u"]
        unseen = scorer._components(state, constant_trace(3, expert=7).top_k_ids)["u"]
        self.assertGreater(float(unseen[0]), float(seen[0]) + 10.0)

    def test_time_chain_first_token_uses_marginal(self) -> None:
        scorer = PdmScorer(window_width=1, model="d2", smoothing=0.5)
        state = scorer.fit([constant_trace(30, expert=3)])
        raw = scorer._components(state, constant_trace(4, expert=3).top_k_ids)["v"]
        alpha = 0.5
        # one trace contributes one initial observation per layer
        p_initial = (1 + alpha) / (1 + EXPERTS * alpha)
        p_transition = (29 + alpha) / (29 + EXPERTS * alpha)
        self.assertAlmostEqual(float(raw[0]), -16 * math.log(p_initial), places=9)
        self.assertAlmostEqual(float(raw[1]), -16 * math.log(p_transition), places=9)

    def test_layer_band_changes_parameter_count(self) -> None:
        full = PdmScorer(window_width=1, model="d1", layers="all")
        middle = PdmScorer(window_width=1, model="d1", layers="middle")
        routine = [random_trace(20, seed=1)]
        self.assertEqual(full.fit(routine).log_depth.shape[0], 15)
        self.assertEqual(middle.fit(routine).log_depth.shape[0], 6)
        self.assertEqual(middle.config()["layers"], list(range(5, 12)))

    def test_probability_tables_normalize(self) -> None:
        scorer = PdmScorer(window_width=1, model="d3", smoothing=0.5)
        state = scorer.fit([random_trace(25, seed=2), random_trace(25, seed=3)])
        row_sums = state.set_transition.sum(dim=2)
        self.assertTrue(bool(torch.allclose(row_sums, torch.ones_like(row_sums), atol=1e-9)))
        self.assertTrue(
            bool(
                torch.allclose(
                    state.set_marginal.sum(dim=1),
                    torch.ones(state.set_marginal.shape[0], dtype=torch.float64),
                    atol=1e-9,
                )
            )
        )


class SetModelTests(unittest.TestCase):
    def test_d3_reduces_to_d2_when_the_set_is_one_expert(self) -> None:
        routine = [degenerate_topk_trace(30, seed=11), degenerate_topk_trace(30, seed=12)]
        probe = degenerate_topk_trace(15, seed=13)
        d2 = PdmScorer(window_width=1, model="d2", smoothing=0.5)
        d3 = PdmScorer(window_width=1, model="d3", smoothing=0.5)
        raw_v = d2._components(d2.fit(routine), probe.top_k_ids)["v"]
        raw_d = d3._components(d3.fit(routine), probe.top_k_ids)["d"]
        self.assertTrue(bool(torch.allclose(raw_v, raw_d, atol=1e-9)))

    def test_d3_uses_the_whole_top_k_set(self) -> None:
        routine = [random_trace(40, seed=21)]
        scorer = PdmScorer(window_width=1, model="d3")
        state = scorer.fit(routine)
        probe = random_trace(10, seed=22)
        base = scorer._components(state, probe.top_k_ids)["d"].clone()
        perturbed = probe.top_k_ids.clone()
        perturbed[:, :, 4:] = (perturbed[:, :, 4:] + 1) % EXPERTS  # keep top-1, change the tail
        moved = scorer._components(state, perturbed)["d"]
        self.assertFalse(bool(torch.allclose(base, moved)))


class StreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.routine = [random_trace(40, seed=100 + index) for index in range(4)]
        self.probe = random_trace(37, seed=999)

    def test_window_shapes_and_ends(self) -> None:
        for width in (1, 4, 16):
            scorer = PdmScorer(window_width=width, model="d1d2")
            state = scorer.fit(self.routine)
            scores, ends = scorer.score(state, self.probe)
            self.assertEqual(scores.shape[0], 37 - width + 1)
            self.assertEqual(int(ends[0]), width - 1)
            self.assertEqual(int(ends[-1]), 36)
            self.assertEqual(scores.shape, ends.shape)

    def test_short_trace_yields_no_window(self) -> None:
        scorer = PdmScorer(window_width=16, model="d1")
        state = scorer.fit(self.routine)
        scores, ends = scorer.score(state, random_trace(5, seed=7))
        self.assertEqual(scores.numel(), 0)
        self.assertEqual(ends.numel(), 0)

    def test_scores_are_causal(self) -> None:
        scorer = PdmScorer(window_width=4, model="d1d2")
        state = scorer.fit(self.routine)
        base_scores, base_ends = scorer.score(state, self.probe)
        tampered = self.probe.top_k_ids.clone()
        tampered[:, 20:, :] = (tampered[:, 20:, :] + 5) % EXPERTS
        new_scores, new_ends = scorer.score(state, FakeTrace(tampered))
        self.assertTrue(bool(torch.equal(base_ends, new_ends)))
        keep = base_ends < 20
        self.assertTrue(bool(torch.allclose(base_scores[keep], new_scores[keep], atol=1e-12)))
        self.assertFalse(bool(torch.allclose(base_scores, new_scores, atol=1e-12)))

    def test_d1d2_is_the_sum_of_the_standardized_parts(self) -> None:
        d1 = PdmScorer(window_width=1, model="d1")
        d2 = PdmScorer(window_width=1, model="d2")
        both = PdmScorer(window_width=1, model="d1d2")
        s1, s2, sb = d1.fit(self.routine), d2.fit(self.routine), both.fit(self.routine)
        a, _ = d1.score(s1, self.probe)
        b, _ = d2.score(s2, self.probe)
        c, _ = both.score(sb, self.probe)
        self.assertTrue(bool(torch.allclose(a + b, c, atol=1e-9)))

    def test_window_mean_equals_manual_average(self) -> None:
        scorer_1 = PdmScorer(window_width=1, model="d1")
        scorer_4 = PdmScorer(window_width=4, model="d1")
        state = scorer_1.fit(self.routine)
        per_token, _ = scorer_1.score(state, self.probe)
        windowed, ends = scorer_4.score(scorer_4.fit(self.routine), self.probe)
        for index in (0, 5, len(ends) - 1):
            end = int(ends[index])
            expected = float(per_token[end - 3 : end + 1].mean())
            self.assertAlmostEqual(float(windowed[index]), expected, places=9)

    def test_routine_stream_is_standardized(self) -> None:
        scorer = PdmScorer(window_width=1, model="d1")
        state = scorer.fit(self.routine)
        pooled = torch.cat([scorer.score(state, trace)[0] for trace in self.routine])
        self.assertAlmostEqual(float(pooled.mean()), 0.0, places=6)
        self.assertAlmostEqual(float(pooled.std()), 1.0, places=4)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
