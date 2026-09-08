"""Synthetic unit tests for the TRM-3 channel scorers and their two ablations.

Covers ``trm3_s`` (rare-coordinate support surprisal), ``trm3_j`` (adjacent-layer top-1
coupling), ``unseen_only`` (B-U) and ``surprisal_marginal`` (B-S) from
``docs/research_v3/trm3_prereg.md`` sections 2, 5 and 6.

Everything here is synthetic routing built from hand-set counts -- no trace, no label, no
attack material -- so these tests are legal before the freeze commit (prereg section 10).
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research_v2.scorers import available, build  # noqa: E402
from research_v2.scorers.surprisal_marginal import SurprisalMarginalScorer  # noqa: E402
from research_v2.scorers.trm3_j import PAIR_LAYERS, TRM3JScorer  # noqa: E402
from research_v2.scorers.trm3_s import (  # noqa: E402
    RARE_THRESHOLD,
    SMOOTHING,
    TRM3SScorer,
    selection_counts,
)
from research_v2.scorers.unseen_only import UnseenOnlyScorer, window_maxima  # noqa: E402

LAYERS = 16
EXPERTS = 64
TOP_K = 8


class FakeTrace:
    """Minimal stand-in for ``research_v2.io.LoadedTrace`` (top-8 routing only)."""

    def __init__(self, trace_id: str, top_k_ids: torch.Tensor) -> None:
        self.trace_id = trace_id
        self.top_k_ids = top_k_ids
        self.pair_group_id = trace_id
        self.workflow = "wf_a"


def _constant_trace(trace_id: str, tokens: int, experts: list[int]) -> FakeTrace:
    """Every layer of every token selects exactly ``experts`` (8 distinct ids)."""

    assert len(experts) == TOP_K and len(set(experts)) == TOP_K
    ids = torch.tensor(experts, dtype=torch.long)
    return FakeTrace(trace_id, ids.view(1, 1, TOP_K).expand(LAYERS, tokens, TOP_K).contiguous())


def _random_trace(trace_id: str, tokens: int, seed: int, pool: int = 40) -> FakeTrace:
    generator = torch.Generator().manual_seed(seed)
    weights = torch.ones(pool).expand(LAYERS * tokens, pool)
    picks = torch.multinomial(weights, TOP_K, replacement=False, generator=generator)
    return FakeTrace(trace_id, picks.view(LAYERS, tokens, TOP_K).contiguous())


def _routine_pool() -> list[FakeTrace]:
    return [_random_trace(f"r{i:02d}", 48, seed=100 + i) for i in range(8)]


class SelectionCountTests(unittest.TestCase):
    def test_counts_are_exact_token_counts(self) -> None:
        traces = [
            _constant_trace("a", 10, list(range(8))),
            _constant_trace("b", 5, [0, 1, 2, 3, 60, 61, 62, 63]),
        ]
        counts, n_tokens, n_traces = selection_counts(traces)
        self.assertEqual(n_tokens, 15)
        self.assertEqual(n_traces, 2)
        for layer in range(LAYERS):
            self.assertEqual(float(counts[layer, 0]), 15.0)  # in both traces
            self.assertEqual(float(counts[layer, 4]), 10.0)  # first trace only
            self.assertEqual(float(counts[layer, 63]), 5.0)  # second trace only
            self.assertEqual(float(counts[layer, 20]), 0.0)  # never selected
        self.assertEqual(float(counts.sum()), 15.0 * LAYERS * TOP_K)

    def test_empty_pool_rejected(self) -> None:
        with self.assertRaises(ValueError):
            selection_counts([])
        with self.assertRaises(ValueError):
            selection_counts([FakeTrace("z", torch.zeros((LAYERS, 0, TOP_K), dtype=torch.long))])

    def test_shape_validation(self) -> None:
        with self.assertRaises(ValueError):
            selection_counts([FakeTrace("bad", torch.zeros((8, 4, TOP_K), dtype=torch.long))])
        with self.assertRaises(ValueError):
            selection_counts(
                [FakeTrace("bad", torch.full((LAYERS, 4, TOP_K), 64, dtype=torch.long))]
            )


class TRM3SScorerTests(unittest.TestCase):
    def setUp(self) -> None:
        torch.set_num_threads(2)

    # -- registry ------------------------------------------------------------
    def test_registered_and_buildable(self) -> None:
        self.assertIn("trm3_s", available())
        scorer = build("trm3_s", {"window_width": 4, "rare_threshold": 0.05})
        self.assertIsInstance(scorer, TRM3SScorer)
        self.assertEqual(scorer.window_width, 4)
        self.assertEqual(scorer.config()["rare_threshold"], 0.05)

    def test_frozen_defaults(self) -> None:
        scorer = build("trm3_s")
        self.assertEqual(scorer.window_width, 8)
        self.assertEqual(scorer.rare_threshold, RARE_THRESHOLD)
        self.assertEqual(scorer.rare_threshold, 0.02)
        self.assertEqual(scorer.smoothing, SMOOTHING)
        self.assertEqual(scorer.config()["layers"], list(range(16)))

    # -- exact arithmetic ----------------------------------------------------
    def test_q_and_rare_mask_are_exact(self) -> None:
        # 100 routine tokens: experts 0-7 selected in every token, everything else never.
        routine = [_constant_trace("a", 100, list(range(8)))]
        state = TRM3SScorer().fit(routine)
        q_seen = (100 + 0.5) / (100 + 32)
        q_unseen = 0.5 / (100 + 32)
        self.assertAlmostEqual(float(state.q[3, 0]), q_seen, places=12)
        self.assertAlmostEqual(float(state.q[3, 40]), q_unseen, places=12)
        # q_seen = 0.761 > 0.02 (not rare); q_unseen = 0.00379 < 0.02 (rare)
        self.assertFalse(bool(state.rare_mask[3, 0]))
        self.assertTrue(bool(state.rare_mask[3, 40]))
        self.assertEqual(int(state.rare_mask.sum()), LAYERS * (EXPERTS - 8))
        self.assertEqual(state.rare_per_layer, tuple([EXPERTS - 8] * LAYERS))
        self.assertEqual(float(state.surprisal[3, 0]), 0.0)
        self.assertAlmostEqual(float(state.surprisal[3, 40]), -math.log(q_unseen), places=12)

    def test_token_and_window_scores_are_exact(self) -> None:
        routine = [_constant_trace("a", 100, list(range(8)))]
        scorer = TRM3SScorer(window_width=4)
        state = scorer.fit(routine)
        q_unseen = 0.5 / (100 + 32)
        surprisal = -math.log(q_unseen)

        # A routine-shaped trace scores exactly 0 everywhere (no rare coordinate).
        inside = _constant_trace("inside", 12, list(range(8)))
        scores, ends = scorer.score(state, inside)
        self.assertTrue(torch.equal(ends, torch.arange(3, 12)))
        self.assertTrue(torch.allclose(scores, torch.zeros(9, dtype=torch.float64)))

        # A trace that swaps in two never-seen experts per layer: 2 rare coords x 16 layers.
        outside = _constant_trace("outside", 12, [0, 1, 2, 3, 4, 5, 40, 41])
        scores, ends = scorer.score(state, outside)
        expected = 2 * LAYERS * surprisal
        self.assertTrue(torch.allclose(scores, torch.full((9,), expected, dtype=torch.float64)))

        # Mixed trace: the causal window mean of a step is exact.
        mixed = torch.cat(
            (inside.top_k_ids[:, :6, :], outside.top_k_ids[:, :6, :]), dim=1
        )
        scores, ends = scorer.score(state, FakeTrace("mixed", mixed))
        self.assertEqual(int(ends[0]), 3)
        self.assertAlmostEqual(float(scores[0]), 0.0, places=12)  # window [0,3] all routine
        self.assertAlmostEqual(float(scores[3]), expected / 4.0, places=10)  # [3,6]: one rare token
        self.assertAlmostEqual(float(scores[6]), expected, places=10)  # [6,9]: all rare

    def test_rare_threshold_is_configurable_and_monotone(self) -> None:
        routine = _routine_pool()
        loose = TRM3SScorer(rare_threshold=0.5).fit(routine)
        tight = TRM3SScorer(rare_threshold=0.001).fit(routine)
        self.assertGreaterEqual(int(loose.rare_mask.sum()), int(tight.rare_mask.sum()))
        self.assertTrue(bool((tight.rare_mask <= loose.rare_mask).all()))

    # -- interface -----------------------------------------------------------
    def test_ends_are_causal_window_ends(self) -> None:
        state = TRM3SScorer().fit(_routine_pool())
        trace = _random_trace("t", 30, seed=9)
        scores, ends = TRM3SScorer().score(state, trace)
        self.assertEqual(scores.shape, ends.shape)
        self.assertTrue(torch.equal(ends, torch.arange(7, 30)))
        self.assertTrue(torch.isfinite(scores).all())

    def test_scores_are_causal(self) -> None:
        """Rewriting tokens after t must not change the score at endpoint t."""

        scorer = TRM3SScorer()
        state = scorer.fit(_routine_pool())
        trace = _random_trace("t", 40, seed=11)
        full_scores, full_ends = scorer.score(state, trace)

        cut = 24
        edited = trace.top_k_ids.clone()
        edited[:, cut:, :] = torch.arange(56, 64, dtype=torch.long).view(1, 1, TOP_K)
        edit_scores, edit_ends = scorer.score(state, FakeTrace("edit", edited))
        keep = full_ends < cut
        self.assertTrue(torch.equal(full_ends[keep], edit_ends[keep]))
        self.assertTrue(torch.allclose(full_scores[keep], edit_scores[keep]))

        truncated = FakeTrace("cut", trace.top_k_ids[:, :cut, :].contiguous())
        part_scores, part_ends = scorer.score(state, truncated)
        self.assertTrue(torch.equal(full_ends[keep], part_ends))
        self.assertTrue(torch.allclose(full_scores[keep], part_scores))

    def test_short_trace_returns_empty(self) -> None:
        scorer = TRM3SScorer(window_width=8)
        state = scorer.fit(_routine_pool())
        for tokens in (0, 1, 7):
            scores, ends = scorer.score(state, _random_trace("s", tokens, seed=3) if tokens else
                                        FakeTrace("s0", torch.zeros((LAYERS, 0, TOP_K), dtype=torch.long)))
            self.assertEqual(scores.numel(), 0)
            self.assertEqual(ends.numel(), 0)
            self.assertEqual(ends.dtype, torch.long)
        exact = _random_trace("exact", 8, seed=4)
        scores, ends = scorer.score(state, exact)
        self.assertEqual(scores.numel(), 1)
        self.assertEqual(int(ends[0]), 7)

    def test_determinism(self) -> None:
        routine = _routine_pool()
        trace = _random_trace("t", 33, seed=17)
        first = TRM3SScorer().fit(routine)
        second = TRM3SScorer().fit(list(routine))
        self.assertTrue(torch.equal(first.q, second.q))
        a_scores, a_ends = TRM3SScorer().score(first, trace)
        b_scores, b_ends = TRM3SScorer().score(second, trace)
        self.assertTrue(torch.equal(a_ends, b_ends))
        self.assertTrue(torch.equal(a_scores, b_scores))

    def test_describe(self) -> None:
        state = TRM3SScorer().fit([_constant_trace("a", 100, list(range(8)))])
        described = state.describe()
        self.assertEqual(described["channel"], "trm3_s")
        self.assertEqual(described["n_routine_tokens"], 100)
        self.assertEqual(described["n_routine_traces"], 1)
        self.assertEqual(described["rare_threshold"], 0.02)
        self.assertEqual(described["coordinates"], LAYERS * EXPERTS)
        self.assertEqual(described["rare_coordinates"], LAYERS * (EXPERTS - 8))
        self.assertEqual(described["unseen_coordinates"], LAYERS * (EXPERTS - 8))
        self.assertEqual(described, state.to_json())


class TRM3JScorerTests(unittest.TestCase):
    def setUp(self) -> None:
        torch.set_num_threads(2)

    def test_registered_and_frozen_defaults(self) -> None:
        self.assertIn("trm3_j", available())
        scorer = build("trm3_j")
        self.assertIsInstance(scorer, TRM3JScorer)
        self.assertEqual(scorer.window_width, 4)
        self.assertEqual(scorer.layers, PAIR_LAYERS)
        self.assertEqual(list(scorer.layers), [5, 6, 7, 8, 9, 10])
        self.assertEqual(scorer.smoothing, 0.5)
        configured = build("trm3_j", {"layers": [2, 3], "window_width": 2})
        self.assertEqual(configured.layers, (2, 3))
        self.assertEqual(configured.window_width, 2)

    def test_rejects_pair_outside_the_stack(self) -> None:
        with self.assertRaises(ValueError):
            TRM3JScorer(layers=[15])
        with self.assertRaises(ValueError):
            TRM3JScorer(layers=[])

    def test_pair_and_marginal_tables_are_exact(self) -> None:
        # 60 tokens: top-1 is expert 3 in every layer, so pair (3,3) has all the mass.
        routine = [_constant_trace("a", 60, [3, 9, 11, 12, 20, 21, 22, 23])]
        scorer = TRM3JScorer()
        state = scorer.fit(routine)
        self.assertEqual(state.n_tokens, 60)
        expected_pair = (60 + 0.5) / (60 + 0.5 * 4096)
        expected_absent_pair = 0.5 / (60 + 0.5 * 4096)
        expected_marg = (60 + 0.5) / (60 + 32)
        self.assertAlmostEqual(float(state.log_pair[0, 3, 3].exp()), expected_pair, places=12)
        self.assertAlmostEqual(float(state.log_pair[0, 7, 8].exp()), expected_absent_pair, places=12)
        self.assertAlmostEqual(float(state.log_marg[5, 3].exp()), expected_marg, places=12)
        self.assertAlmostEqual(float(state.log_marg[5, 7].exp()), 0.5 / (60 + 32), places=12)

        # j_t on the routine-shaped trace: 6 pairs, each -log P_pair + 2 log P_marg.
        per_pair = -math.log(expected_pair) + 2 * math.log(expected_marg)
        trace = _constant_trace("t", 8, [3, 9, 11, 12, 20, 21, 22, 23])
        tokens = scorer.token_scores(state, trace)
        self.assertTrue(
            torch.allclose(tokens, torch.full((8,), 6 * per_pair, dtype=torch.float64))
        )
        scores, ends = scorer.score(state, trace)
        self.assertTrue(torch.equal(ends, torch.arange(3, 8)))
        self.assertTrue(
            torch.allclose(scores, torch.full((5,), 6 * per_pair, dtype=torch.float64))
        )

    def test_recombination_of_common_experts_scores_higher(self) -> None:
        """Individually common experts in a routine-rare adjacent pairing must fire."""

        # Routine: half the tokens use top-1 = 3 in every layer, half use top-1 = 7.
        # Both experts are individually common; the pair (3, 7) never occurs.
        first = _constant_trace("a", 50, [3, 9, 11, 12, 20, 21, 22, 23]).top_k_ids
        second = _constant_trace("b", 50, [7, 9, 11, 12, 20, 21, 22, 23]).top_k_ids
        routine = [FakeTrace("a", first), FakeTrace("b", second)]
        scorer = TRM3JScorer()
        state = scorer.fit(routine)

        seen = scorer.token_scores(state, FakeTrace("seen", first[:, :4, :]))
        mixed = first[:, :4, :].clone()
        mixed[6, :, 0] = 7  # layer 6 top-1 flips: pairs (5,6) and (6,7) become unseen
        recombined = scorer.token_scores(state, FakeTrace("mix", mixed))
        self.assertTrue(bool((recombined > seen).all()))

        # Only the two pairs touching layer 6 change, by -log P_pair(unseen) + log P_pair(seen).
        seen_pair = float(state.log_pair[0, 3, 3])
        unseen_pair = float(state.log_pair[0, 3, 7])
        self.assertAlmostEqual(
            float(recombined[0] - seen[0]), 2 * (seen_pair - unseen_pair), places=8
        )

    def test_marginal_correction_cancels_pure_rarity(self) -> None:
        """A pair as rare as its independent ends contributes ~0 (PMI reading).

        Under independent per-layer top-1 draws every pair is rare (1/400 of the mass),
        so the *uncorrected* -log P_pair is large; the marginal correction removes almost
        all of it.  The residual is the additive-smoothing bias (0.5 x 4096 pseudo-counts
        against N_tok real ones) and shrinks with N_tok.
        """

        generator = torch.Generator().manual_seed(5)
        tokens_n = 20000
        picks = torch.randint(0, 20, (LAYERS, tokens_n, 1), generator=generator)
        rest = torch.arange(40, 47, dtype=torch.long).view(1, 1, 7).expand(LAYERS, tokens_n, 7)
        ids = torch.cat((picks, rest), dim=2).contiguous()
        scorer = TRM3JScorer()
        state = scorer.fit([FakeTrace("iid", ids)])
        sample = FakeTrace("iid", ids[:, :500, :].contiguous())
        corrected = scorer.token_scores(state, sample)

        top1 = ids[:, :500, 0]
        flat_pair = state.log_pair.view(len(state.layers), EXPERTS * EXPERTS)
        uncorrected = torch.zeros(500, dtype=torch.float64)
        for position, layer in enumerate(state.layers):
            uncorrected += -flat_pair[position].index_select(
                0, top1[layer] * EXPERTS + top1[layer + 1]
            )
        self.assertGreater(float(uncorrected.mean()), 30.0)  # ~6 x log 400
        self.assertLess(abs(float(corrected.mean())), 1.0)

    def test_scores_are_causal(self) -> None:
        scorer = TRM3JScorer()
        state = scorer.fit(_routine_pool())
        trace = _random_trace("t", 40, seed=21)
        full_scores, full_ends = scorer.score(state, trace)
        cut = 20
        edited = trace.top_k_ids.clone()
        edited[:, cut:, :] = torch.arange(50, 58, dtype=torch.long).view(1, 1, TOP_K)
        edit_scores, edit_ends = scorer.score(state, FakeTrace("edit", edited))
        keep = full_ends < cut
        self.assertTrue(torch.equal(full_ends[keep], edit_ends[keep]))
        self.assertTrue(torch.allclose(full_scores[keep], edit_scores[keep]))

    def test_short_trace_and_determinism(self) -> None:
        scorer = TRM3JScorer()
        state = scorer.fit(_routine_pool())
        scores, ends = scorer.score(state, _random_trace("short", 3, seed=2))
        self.assertEqual(scores.numel(), 0)
        self.assertEqual(ends.numel(), 0)
        trace = _random_trace("t", 25, seed=31)
        a, ae = scorer.score(state, trace)
        b, be = scorer.score(scorer.fit(_routine_pool()), trace)
        self.assertTrue(torch.equal(ae, be))
        self.assertTrue(torch.equal(a, b))

    def test_describe(self) -> None:
        state = TRM3JScorer().fit([_constant_trace("a", 60, [3, 9, 11, 12, 20, 21, 22, 23])])
        described = state.describe()
        self.assertEqual(described["channel"], "trm3_j")
        self.assertEqual(described["layers"], [5, 6, 7, 8, 9, 10])
        self.assertEqual(described["pairs"][0], [5, 6])
        self.assertEqual(described["n_routine_tokens"], 60)
        self.assertEqual(described["unseen_pairs_per_layer"], [EXPERTS * EXPERTS - 1] * 6)
        self.assertEqual(described["unseen_top1_experts_per_layer"], [EXPERTS - 1] * LAYERS)
        self.assertEqual(described, state.to_json())


class UnseenOnlyTests(unittest.TestCase):
    def test_registered_and_defaults(self) -> None:
        self.assertIn("unseen_only", available())
        scorer = build("unseen_only")
        self.assertIsInstance(scorer, UnseenOnlyScorer)
        self.assertEqual(scorer.window_width, 8)
        self.assertEqual(build("unseen_only", {"window_width": 4}).window_width, 4)

    def test_window_maxima_helper(self) -> None:
        values = torch.tensor([0.0, 0.0, 3.0, 0.0, 0.0, 0.0], dtype=torch.float64)
        ends, maxima = window_maxima(values, 3)
        self.assertTrue(torch.equal(ends, torch.tensor([2, 3, 4, 5])))
        self.assertTrue(torch.equal(maxima, torch.tensor([3.0, 3.0, 3.0, 0.0], dtype=torch.float64)))
        ends, maxima = window_maxima(values[:2], 3)
        self.assertEqual(ends.numel(), 0)
        self.assertEqual(maxima.numel(), 0)

    def test_exact_counts_and_window_max(self) -> None:
        routine = [_constant_trace("a", 40, list(range(8)))]
        scorer = UnseenOnlyScorer(window_width=4)
        state = scorer.fit(routine)
        self.assertEqual(int(state.unseen_mask.sum()), LAYERS * (EXPERTS - 8))

        inside = _constant_trace("inside", 10, list(range(8)))
        scores, ends = scorer.score(state, inside)
        self.assertTrue(torch.equal(ends, torch.arange(3, 10)))
        self.assertTrue(torch.equal(scores, torch.zeros(7, dtype=torch.float64)))

        # One token with three unseen coordinates per layer -> 3 * 16 = 48.
        ids = inside.top_k_ids.clone()
        ids[:, 5, 5:] = torch.tensor([50, 51, 52], dtype=torch.long)
        scores, ends = scorer.score(state, FakeTrace("spike", ids))
        expected = torch.zeros(7, dtype=torch.float64)
        expected[(ends >= 5) & (ends <= 8)] = 3.0 * LAYERS
        self.assertTrue(torch.equal(scores, expected))
        self.assertGreaterEqual(float(scores.max()), 1.0)

    def test_causal_and_short(self) -> None:
        scorer = UnseenOnlyScorer()
        state = scorer.fit(_routine_pool())
        trace = _random_trace("t", 36, seed=41)
        full_scores, full_ends = scorer.score(state, trace)
        cut = 20
        edited = trace.top_k_ids.clone()
        edited[:, cut:, :] = torch.arange(44, 52, dtype=torch.long).view(1, 1, TOP_K)
        edit_scores, edit_ends = scorer.score(state, FakeTrace("edit", edited))
        keep = full_ends < cut
        self.assertTrue(torch.equal(full_ends[keep], edit_ends[keep]))
        self.assertTrue(torch.equal(full_scores[keep], edit_scores[keep]))
        scores, ends = scorer.score(state, _random_trace("short", 5, seed=6))
        self.assertEqual(scores.numel(), 0)
        self.assertEqual(ends.numel(), 0)

    def test_describe(self) -> None:
        state = UnseenOnlyScorer().fit([_constant_trace("a", 40, list(range(8)))])
        described = state.describe()
        self.assertEqual(described["channel"], "unseen_only")
        self.assertEqual(described["n_routine_tokens"], 40)
        self.assertEqual(described["unseen_coordinates"], LAYERS * (EXPERTS - 8))
        self.assertEqual(described["unseen_coordinates_per_layer"], [EXPERTS - 8] * LAYERS)
        self.assertEqual(described, state.to_json())


class SurprisalMarginalTests(unittest.TestCase):
    def test_registered_and_defaults(self) -> None:
        self.assertIn("surprisal_marginal", available())
        scorer = build("surprisal_marginal")
        self.assertIsInstance(scorer, SurprisalMarginalScorer)
        self.assertEqual(scorer.window_width, 8)
        self.assertEqual(scorer.smoothing, 0.5)

    def test_exact_mean_surprisal(self) -> None:
        routine = [_constant_trace("a", 100, list(range(8)))]
        scorer = SurprisalMarginalScorer(window_width=4)
        state = scorer.fit(routine)
        seen = -math.log((100 + 0.5) / (100 + 32))
        unseen = -math.log(0.5 / (100 + 32))

        inside = _constant_trace("inside", 8, list(range(8)))
        scores, ends = scorer.score(state, inside)
        self.assertTrue(torch.equal(ends, torch.arange(3, 8)))
        self.assertTrue(torch.allclose(scores, torch.full((5,), seen, dtype=torch.float64)))

        # 6 routine + 2 unseen selections per layer -> mean over the 128 selections.
        outside = _constant_trace("outside", 8, [0, 1, 2, 3, 4, 5, 40, 41])
        expected = (6 * seen + 2 * unseen) / 8.0
        scores, _ = scorer.score(state, outside)
        self.assertTrue(torch.allclose(scores, torch.full((5,), expected, dtype=torch.float64)))
        self.assertGreater(expected, seen)

    def test_unlike_trm3_s_every_coordinate_contributes(self) -> None:
        """B-S is non-zero on a purely routine window; channel S is exactly zero there."""

        routine = [_constant_trace("a", 100, list(range(8)))]
        inside = _constant_trace("inside", 12, list(range(8)))
        b_s, _ = SurprisalMarginalScorer().score(SurprisalMarginalScorer().fit(routine), inside)
        s, _ = TRM3SScorer().score(TRM3SScorer().fit(routine), inside)
        self.assertTrue(bool((b_s > 0).all()))
        self.assertTrue(bool((s == 0).all()))

    def test_causal_short_and_determinism(self) -> None:
        scorer = SurprisalMarginalScorer()
        state = scorer.fit(_routine_pool())
        trace = _random_trace("t", 36, seed=51)
        full_scores, full_ends = scorer.score(state, trace)
        cut = 16
        truncated = FakeTrace("cut", trace.top_k_ids[:, :cut, :].contiguous())
        part_scores, part_ends = scorer.score(state, truncated)
        keep = full_ends < cut
        self.assertTrue(torch.equal(full_ends[keep], part_ends))
        self.assertTrue(torch.allclose(full_scores[keep], part_scores))
        scores, ends = scorer.score(state, _random_trace("short", 7, seed=7))
        self.assertEqual(scores.numel(), 0)
        self.assertEqual(ends.numel(), 0)
        again, again_ends = scorer.score(scorer.fit(_routine_pool()), trace)
        self.assertTrue(torch.equal(again_ends, full_ends))
        self.assertTrue(torch.equal(again, full_scores))

    def test_describe(self) -> None:
        state = SurprisalMarginalScorer().fit([_constant_trace("a", 100, list(range(8)))])
        described = state.describe()
        self.assertEqual(described["channel"], "surprisal_marginal")
        self.assertEqual(described["selections_per_token"], LAYERS * TOP_K)
        self.assertEqual(described["n_routine_tokens"], 100)
        self.assertEqual(described, state.to_json())


class SharedInterfaceTests(unittest.TestCase):
    def test_all_four_share_the_registry_contract(self) -> None:
        routine = _routine_pool()
        trace = _random_trace("t", 40, seed=61)
        for name, width in (
            ("trm3_s", 8),
            ("trm3_j", 4),
            ("unseen_only", 8),
            ("surprisal_marginal", 8),
        ):
            with self.subTest(name=name):
                scorer = build(name)
                self.assertEqual(scorer.window_width, width)
                self.assertFalse(getattr(scorer, "requires_positives", False))
                state = scorer.fit(routine)
                self.assertIsInstance(state.describe(), dict)
                scores, ends = scorer.score(state, trace)
                self.assertEqual(scores.shape, ends.shape)
                self.assertEqual(ends.dtype, torch.long)
                self.assertTrue(torch.equal(ends, torch.arange(width - 1, 40)))
                self.assertTrue(torch.isfinite(scores).all())
                self.assertTrue(bool((scores >= 0).all()) or name == "trm3_j")

    def test_frozen_scorers_still_registered(self) -> None:
        for name in ("wgm", "pdm", "cm", "fcm"):
            self.assertIn(name, available())


if __name__ == "__main__":
    unittest.main()
