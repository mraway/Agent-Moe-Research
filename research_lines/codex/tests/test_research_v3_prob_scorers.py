"""Synthetic unit tests for the EXPLORATORY router-probability channels.

Covers ``prob_rare_mass``, ``prob_weighted_surprisal`` (+ its rare-only sibling),
``prob_js`` / ``prob_js_all``, ``prob_concentration`` and ``prob_entropy_drop`` from
``docs/research_v3/explore_prob_weighted.md``.

Everything here is synthetic routing built from hand-set selections and hand-set router
distributions -- no trace, no label, no attack material.  The tests check shapes, causality,
the layer bands, the registry wiring, the additive attribution and the EXACT value of every
statistic on a tiny case whose answer is computable by hand.
"""

from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import trm3  # noqa: E402
from research_v2.scorers import available, build  # noqa: E402
from research_v2.scorers.prob_common import (  # noqa: E402
    check_probabilities,
    resolve_layers,
    routine_probability_stats,
    row_entropy,
    trace_probabilities,
)
from research_v2.scorers.prob_concentration import (  # noqa: E402
    ProbConcentrationScorer,
    ProbEntropyDropScorer,
    routine_top_experts,
)
from research_v2.scorers.prob_js import ProbJSScorer, jensen_shannon  # noqa: E402
from research_v2.scorers.prob_rare_mass import ProbRareMassScorer  # noqa: E402
from research_v2.scorers.prob_weighted_surprisal import (  # noqa: E402
    ProbWeightedSurprisalScorer,
)

LAYERS = 16
EXPERTS = 64
TOP_K = 8
WIDTH = 8

PROB_SCORER_NAMES = (
    "prob_rare_mass",
    "prob_weighted_surprisal",
    "prob_weighted_surprisal_rare",
    "prob_js",
    "prob_js_all",
    "prob_concentration",
    "prob_entropy_drop",
)


class FakeTrace:
    """Minimal stand-in for ``research_v2.io.LoadedTrace`` (top-8 ids + router softmax)."""

    def __init__(self, trace_id: str, top_k_ids: torch.Tensor, probs: torch.Tensor) -> None:
        self.trace_id = trace_id
        self.top_k_ids = top_k_ids
        self._probs = probs
        self.pair_group_id = trace_id
        self.workflow = "wf_a"

    def probabilities(self) -> torch.Tensor:
        return self._probs


def _ids_from_group(tokens: int, experts: list[int]) -> torch.Tensor:
    """Every layer of every token selects exactly ``experts`` (8 distinct ids)."""

    assert len(experts) == TOP_K and len(set(experts)) == TOP_K
    ids = torch.tensor(experts, dtype=torch.long)
    return ids.view(1, 1, TOP_K).expand(LAYERS, tokens, TOP_K).contiguous()


def _two_group_probs(tokens: int, rare_mass: torch.Tensor) -> torch.Tensor:
    """``[16, T, 64]``: mass ``1 - rare_mass[t]`` spread over 0..7, the rest over 8..63."""

    probs = torch.zeros((LAYERS, tokens, EXPERTS), dtype=torch.float64)
    rare = rare_mass.to(torch.float64)
    probs[:, :, :TOP_K] = ((1.0 - rare) / TOP_K)[None, :, None]
    probs[:, :, TOP_K:] = (rare / (EXPERTS - TOP_K))[None, :, None]
    return probs


# Routine: experts 0..7 always selected, 90% of the router mass on them.
ROUTINE_TOKENS = 48
ROUTINE_TRACES = 12
ROUTINE = [
    FakeTrace(
        f"routine-{i}",
        _ids_from_group(ROUTINE_TOKENS, list(range(TOP_K))),
        _two_group_probs(ROUTINE_TOKENS, torch.full((ROUTINE_TOKENS,), 0.10, dtype=torch.float64)),
    )
    for i in range(ROUTINE_TRACES)
]
ROUTINE_N_TOKENS = ROUTINE_TOKENS * ROUTINE_TRACES

TARGET_TOKENS = 20
# rare mass ramps 0.01, 0.02, ... so every window mean is a distinct rational number
TARGET_RARE = torch.arange(1, TARGET_TOKENS + 1, dtype=torch.float64) / 100.0
TARGET = FakeTrace(
    "target",
    _ids_from_group(TARGET_TOKENS, list(range(TOP_K))),
    _two_group_probs(TARGET_TOKENS, TARGET_RARE),
)


def _q_tables() -> tuple[float, float]:
    """The smoothed routine selection rates of the two expert groups (channel S's ``q``)."""

    common = (ROUTINE_N_TOKENS + 0.5) / (ROUTINE_N_TOKENS + EXPERTS * 0.5)
    rare = 0.5 / (ROUTINE_N_TOKENS + EXPERTS * 0.5)
    return common, rare


class ProbCommonTests(unittest.TestCase):
    def test_check_probabilities_rejects_wrong_shapes(self) -> None:
        with self.assertRaises(ValueError):
            check_probabilities(torch.zeros((8, 5, 64)))
        with self.assertRaises(ValueError):
            check_probabilities(torch.zeros((16, 5, 32)))
        with self.assertRaises(ValueError):
            check_probabilities(torch.zeros((16, 5, 64)), tokens=6)
        self.assertEqual(
            check_probabilities(torch.zeros((16, 5, 64)), tokens=5).dtype, torch.float64
        )

    def test_trace_probabilities_requires_the_tensor(self) -> None:
        class NoProbs:
            top_k_ids = torch.zeros((LAYERS, 4, TOP_K), dtype=torch.long)

        with self.assertRaises(ValueError):
            trace_probabilities(NoProbs())

    def test_trace_probabilities_accepts_a_plain_attribute(self) -> None:
        class Attr:
            top_k_ids = torch.zeros((LAYERS, 4, TOP_K), dtype=torch.long)
            probabilities = torch.full((LAYERS, 4, EXPERTS), 1.0 / EXPERTS)

        self.assertEqual(tuple(trace_probabilities(Attr()).shape), (LAYERS, 4, EXPERTS))

    def test_row_entropy_matches_closed_forms(self) -> None:
        uniform = torch.full((3, EXPERTS), 1.0 / EXPERTS, dtype=torch.float64)
        self.assertAlmostEqual(float(row_entropy(uniform)[0]), math.log(EXPERTS), places=12)
        one_hot = torch.zeros((1, EXPERTS), dtype=torch.float64)
        one_hot[0, 3] = 1.0
        self.assertAlmostEqual(float(row_entropy(one_hot)[0]), 0.0, places=12)

    def test_routine_stats_are_token_weighted(self) -> None:
        stats = routine_probability_stats(ROUTINE)
        self.assertEqual(stats.n_tokens, ROUTINE_N_TOKENS)
        self.assertEqual(stats.n_traces, ROUTINE_TRACES)
        self.assertAlmostEqual(float(stats.mean_prob[0, 0]), 0.90 / TOP_K, places=12)
        self.assertAlmostEqual(
            float(stats.mean_prob[0, TOP_K]), 0.10 / (EXPERTS - TOP_K), places=12
        )
        for row_sum in stats.mean_prob.sum(1).tolist():
            self.assertAlmostEqual(row_sum, 1.0, places=12)
        self.assertLess(stats.simplex_max_deviation, 1e-12)

    def test_resolve_layers(self) -> None:
        self.assertEqual(resolve_layers("all"), tuple(range(16)))
        self.assertEqual(resolve_layers("middle_late"), tuple(range(5, 16)))
        self.assertEqual(resolve_layers("concentration"), (11, 12, 13, 14))
        self.assertEqual(resolve_layers([3, 4]), (3, 4))
        with self.assertRaises(ValueError):
            resolve_layers("nope")
        with self.assertRaises(ValueError):
            resolve_layers([16])
        with self.assertRaises(ValueError):
            resolve_layers([])


class ProbRareMassTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scorer = ProbRareMassScorer()
        self.state = self.scorer.fit(ROUTINE)

    def test_rare_set_is_channel_s_rare_set(self) -> None:
        # experts 0-7 are selected by every routine token, 8-63 never
        self.assertEqual(int(self.state.rare_mask.sum()), LAYERS * (EXPERTS - TOP_K))
        self.assertFalse(bool(self.state.rare_mask[:, :TOP_K].any()))
        self.assertTrue(bool(self.state.rare_mask[:, TOP_K:].all()))
        described = self.state.describe()
        self.assertEqual(described["status"], "EXPLORATORY_POST_HOC")
        self.assertEqual(described["n_routine_tokens"], ROUTINE_N_TOKENS)
        self.assertEqual(described["rare_coordinates"], LAYERS * (EXPERTS - TOP_K))

    def test_exact_window_values(self) -> None:
        scores, ends = self.scorer.score(self.state, TARGET)
        self.assertEqual(len(ends), TARGET_TOKENS - WIDTH + 1)
        self.assertEqual(int(ends[0]), WIDTH - 1)
        self.assertEqual(int(ends[-1]), TARGET_TOKENS - 1)
        for index, end in enumerate(ends.tolist()):
            expected = LAYERS * float(TARGET_RARE[end - WIDTH + 1 : end + 1].mean())
            self.assertAlmostEqual(float(scores[index]), expected, places=12)

    def test_layer_band_restricts_the_sum(self) -> None:
        narrow = ProbRareMassScorer(layers=[0, 1])
        state = narrow.fit(ROUTINE)
        scores, _ = narrow.score(state, TARGET)
        wide, _ = self.scorer.score(self.state, TARGET)
        for a, b in zip(scores.tolist(), wide.tolist()):
            self.assertAlmostEqual(a, b * 2.0 / LAYERS, places=12)

    def test_causality(self) -> None:
        probs = TARGET.probabilities().clone()
        probs[:, -1, :] = torch.full((LAYERS, EXPERTS), 1.0 / EXPERTS, dtype=torch.float64)
        mutated = FakeTrace("mutated", TARGET.top_k_ids, probs)
        base_scores, base_ends = self.scorer.score(self.state, TARGET)
        new_scores, new_ends = self.scorer.score(self.state, mutated)
        self.assertEqual(base_ends.tolist(), new_ends.tolist())
        # only the eight windows that contain the final token may move
        for index, end in enumerate(base_ends.tolist()):
            if end < TARGET_TOKENS - 1:
                self.assertAlmostEqual(
                    float(base_scores[index]), float(new_scores[index]), places=12
                )
        self.assertNotAlmostEqual(float(base_scores[-1]), float(new_scores[-1]), places=6)

    def test_short_trace_returns_an_empty_stream(self) -> None:
        short = FakeTrace(
            "short",
            _ids_from_group(WIDTH - 1, list(range(TOP_K))),
            _two_group_probs(WIDTH - 1, torch.full((WIDTH - 1,), 0.1, dtype=torch.float64)),
        )
        scores, ends = self.scorer.score(self.state, short)
        self.assertEqual(len(scores), 0)
        self.assertEqual(len(ends), 0)

    def test_attribution_sums_to_the_window_score(self) -> None:
        scores, ends = self.scorer.score(self.state, TARGET)
        end = int(ends[3])
        everything = self.scorer.top_coordinates(self.state, TARGET, end, n=LAYERS * EXPERTS)
        self.assertAlmostEqual(
            sum(item["contribution"] for item in everything), float(scores[3]), places=12
        )
        for item in everything:
            self.assertTrue(bool(self.state.rare_mask[item["layer"], item["expert"]]))
        self.assertEqual(self.scorer.top_coordinates(self.state, TARGET, 0, n=3), [])
        self.assertEqual(self.scorer.top_coordinates(self.state, TARGET, 10_000, n=3), [])


class ProbWeightedSurprisalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.full = ProbWeightedSurprisalScorer()
        self.rare = ProbWeightedSurprisalScorer(rare_only=True)
        self.full_state = self.full.fit(ROUTINE)
        self.rare_state = self.rare.fit(ROUTINE)

    def test_exact_window_values(self) -> None:
        q_common, q_rare = _q_tables()
        s_common, s_rare = -math.log(q_common), -math.log(q_rare)
        scores, ends = self.full.score(self.full_state, TARGET)
        rare_scores, _ = self.rare.score(self.rare_state, TARGET)
        for index, end in enumerate(ends.tolist()):
            mass = float(TARGET_RARE[end - WIDTH + 1 : end + 1].mean())
            expected_full = LAYERS * ((1.0 - mass) * s_common + mass * s_rare)
            expected_rare = LAYERS * mass * s_rare
            self.assertAlmostEqual(float(scores[index]), expected_full, places=10)
            self.assertAlmostEqual(float(rare_scores[index]), expected_rare, places=10)

    def test_rare_variant_is_bounded_by_the_full_variant(self) -> None:
        full, _ = self.full.score(self.full_state, TARGET)
        rare, _ = self.rare.score(self.rare_state, TARGET)
        for a, b in zip(rare.tolist(), full.tolist()):
            self.assertLessEqual(a, b + 1e-12)

    def test_registry_wires_the_two_variants(self) -> None:
        self.assertFalse(build("prob_weighted_surprisal", {}).rare_only)
        self.assertTrue(build("prob_weighted_surprisal_rare", {}).rare_only)
        described = self.rare_state.describe()
        self.assertEqual(described["channel"], "prob_weighted_surprisal_rare")
        self.assertTrue(described["rare_only"])
        self.assertEqual(described["status"], "EXPLORATORY_POST_HOC")

    def test_attribution_sums_to_the_window_score(self) -> None:
        scores, ends = self.full.score(self.full_state, TARGET)
        end = int(ends[2])
        everything = self.full.top_coordinates(
            self.full_state, TARGET, end, n=LAYERS * EXPERTS
        )
        self.assertAlmostEqual(
            sum(item["contribution"] for item in everything), float(scores[2]), places=10
        )


class ProbJSTests(unittest.TestCase):
    def test_jensen_shannon_endpoints(self) -> None:
        p = torch.zeros(4, dtype=torch.float64)
        p[0] = 1.0
        q = torch.zeros(4, dtype=torch.float64)
        q[1] = 1.0
        self.assertAlmostEqual(float(jensen_shannon(p, p)), 0.0, places=12)
        self.assertAlmostEqual(float(jensen_shannon(p, q)), math.log(2.0), places=12)

    def _one_hot_trace(self, expert: int, tokens: int = 12) -> FakeTrace:
        probs = torch.zeros((LAYERS, tokens, EXPERTS), dtype=torch.float64)
        probs[:, :, expert] = 1.0
        return FakeTrace(f"onehot-{expert}", _ids_from_group(tokens, list(range(TOP_K))), probs)

    def test_identical_distribution_scores_zero_and_disjoint_scores_log2_per_layer(self) -> None:
        routine = [self._one_hot_trace(0, tokens=16) for _ in range(4)]
        for scorer, band in (
            (ProbJSScorer(), 11),
            (ProbJSScorer(layers="all"), 16),
        ):
            state = scorer.fit(routine)
            same, _ = scorer.score(state, self._one_hot_trace(0))
            disjoint, _ = scorer.score(state, self._one_hot_trace(1))
            with self.subTest(band=band):
                self.assertEqual(len(state.layers), band)
                for value in same.tolist():
                    self.assertAlmostEqual(value, 0.0, places=12)
                for value in disjoint.tolist():
                    self.assertAlmostEqual(value, band * math.log(2.0), places=12)

    def test_score_uses_the_window_mean_distribution(self) -> None:
        """A window that mixes two one-hot tokens is a mixture, not a per-token average."""

        routine = [self._one_hot_trace(0, tokens=16) for _ in range(4)]
        scorer = ProbJSScorer(window_width=2, layers=[0])
        state = scorer.fit(routine)
        probs = torch.zeros((LAYERS, 2, EXPERTS), dtype=torch.float64)
        probs[:, 0, 0] = 1.0
        probs[:, 1, 1] = 1.0
        trace = FakeTrace("mix", _ids_from_group(2, list(range(TOP_K))), probs)
        scores, ends = scorer.score(state, trace)
        self.assertEqual(ends.tolist(), [1])
        # pbar = (0.5, 0.5), qbar = (1, 0), m = (0.75, 0.25)
        expected = 0.5 * (
            0.5 * math.log(0.5 / 0.75) + 0.5 * math.log(0.5 / 0.25)
        ) + 0.5 * math.log(1.0 / 0.75)
        self.assertAlmostEqual(float(scores[0]), expected, places=12)

    def test_shapes_and_causality(self) -> None:
        scorer = ProbJSScorer()
        state = scorer.fit(ROUTINE)
        scores, ends = scorer.score(state, TARGET)
        self.assertEqual(len(scores), TARGET_TOKENS - WIDTH + 1)
        self.assertEqual(int(ends[0]), WIDTH - 1)
        self.assertEqual(int(ends[-1]), TARGET_TOKENS - 1)
        self.assertTrue(all(v >= 0.0 for v in scores.tolist()))
        self.assertTrue(all(v <= len(state.layers) * math.log(2.0) + 1e-12 for v in scores.tolist()))
        probs = TARGET.probabilities().clone()
        probs[:, -1, :] = torch.full((LAYERS, EXPERTS), 1.0 / EXPERTS, dtype=torch.float64)
        mutated, _ = scorer.score(state, FakeTrace("m", TARGET.top_k_ids, probs))
        for index, end in enumerate(ends.tolist()):
            if end < TARGET_TOKENS - 1:
                self.assertAlmostEqual(float(scores[index]), float(mutated[index]), places=12)

    def test_channel_m_band_is_the_default(self) -> None:
        self.assertEqual(build("prob_js", {}).layers, tuple(range(5, 16)))
        self.assertEqual(build("prob_js_all", {}).layers, tuple(range(16)))


class ProbConcentrationTests(unittest.TestCase):
    def test_routine_top_experts_ties_break_on_the_lower_id(self) -> None:
        counts = torch.zeros((LAYERS, EXPERTS), dtype=torch.float64)
        counts[0, :16] = 5.0  # a 16-way tie
        mask = routine_top_experts(counts)
        self.assertEqual(sorted(mask[0].nonzero().flatten().tolist()), list(range(TOP_K)))

    def test_exact_window_values_and_band(self) -> None:
        scorer = ProbConcentrationScorer()
        state = scorer.fit(ROUTINE)
        self.assertEqual(state.layers, (11, 12, 13, 14))
        self.assertTrue(bool(state.top_mask[:, :TOP_K].all()))
        self.assertFalse(bool(state.top_mask[:, TOP_K:].any()))
        scores, ends = scorer.score(state, TARGET)
        for index, end in enumerate(ends.tolist()):
            mass = float(TARGET_RARE[end - WIDTH + 1 : end + 1].mean())
            self.assertAlmostEqual(float(scores[index]), 1.0 - mass, places=12)

    def test_out_of_band_layers_are_ignored(self) -> None:
        scorer = ProbConcentrationScorer()
        state = scorer.fit(ROUTINE)
        base, _ = scorer.score(state, TARGET)
        probs = TARGET.probabilities().clone()
        probs[0] = torch.full((TARGET_TOKENS, EXPERTS), 1.0 / EXPERTS, dtype=torch.float64)
        probs[15] = torch.full((TARGET_TOKENS, EXPERTS), 1.0 / EXPERTS, dtype=torch.float64)
        moved, _ = scorer.score(state, FakeTrace("m", TARGET.top_k_ids, probs))
        self.assertEqual(base.tolist(), moved.tolist())

    def test_sign_is_inverted_high_concentration_scores_high(self) -> None:
        scorer = ProbConcentrationScorer()
        state = scorer.fit(ROUTINE)
        tokens = 10
        sharp = torch.zeros((LAYERS, tokens, EXPERTS), dtype=torch.float64)
        sharp[:, :, 0] = 1.0
        spread = torch.full((LAYERS, tokens, EXPERTS), 1.0 / EXPERTS, dtype=torch.float64)
        ids = _ids_from_group(tokens, list(range(TOP_K)))
        sharp_score, _ = scorer.score(state, FakeTrace("sharp", ids, sharp))
        spread_score, _ = scorer.score(state, FakeTrace("spread", ids, spread))
        self.assertAlmostEqual(float(sharp_score[0]), 1.0, places=12)
        self.assertAlmostEqual(float(spread_score[0]), TOP_K / EXPERTS, places=12)
        self.assertGreater(float(sharp_score[0]), float(spread_score[0]))
        self.assertIn("inverted", state.describe()["sign"])

    def test_attribution_sums_to_the_window_score(self) -> None:
        scorer = ProbConcentrationScorer()
        state = scorer.fit(ROUTINE)
        scores, ends = scorer.score(state, TARGET)
        end = int(ends[1])
        everything = scorer.top_coordinates(state, TARGET, end, n=LAYERS * EXPERTS)
        self.assertAlmostEqual(
            sum(item["contribution"] for item in everything), float(scores[1]), places=12
        )
        for item in everything:
            self.assertIn(item["layer"], (11, 12, 13, 14))


class ProbEntropyDropTests(unittest.TestCase):
    def test_exact_value_against_closed_form_entropies(self) -> None:
        tokens = 12
        uniform = torch.full((LAYERS, tokens, EXPERTS), 1.0 / EXPERTS, dtype=torch.float64)
        ids = _ids_from_group(tokens, list(range(TOP_K)))
        routine = [FakeTrace(f"r{i}", ids, uniform) for i in range(3)]
        scorer = ProbEntropyDropScorer()
        state = scorer.fit(routine)
        for value in state.routine_entropy.tolist():
            self.assertAlmostEqual(value, math.log(EXPERTS), places=12)
        two = torch.zeros((LAYERS, tokens, EXPERTS), dtype=torch.float64)
        two[:, :, 0] = 0.5
        two[:, :, 1] = 0.5
        scores, ends = scorer.score(state, FakeTrace("two", ids, two))
        self.assertEqual(int(ends[0]), WIDTH - 1)
        for value in scores.tolist():
            self.assertAlmostEqual(value, math.log(EXPERTS) - math.log(2.0), places=12)

    def test_routine_like_traffic_scores_about_zero(self) -> None:
        scorer = ProbEntropyDropScorer()
        state = scorer.fit(ROUTINE)
        scores, _ = scorer.score(state, ROUTINE[0])
        for value in scores.tolist():
            self.assertAlmostEqual(value, 0.0, places=12)

    def test_band_restriction(self) -> None:
        scorer = ProbEntropyDropScorer(layers=[0, 1])
        state = scorer.fit(ROUTINE)
        self.assertEqual(state.layers, (0, 1))
        self.assertEqual(len(scorer.score(state, TARGET)[0]), TARGET_TOKENS - WIDTH + 1)


class RegistryAndVariantTests(unittest.TestCase):
    def test_every_probability_scorer_is_registered_at_width_8(self) -> None:
        for name in PROB_SCORER_NAMES:
            with self.subTest(scorer=name):
                self.assertIn(name, available())
                built = build(name, {})
                self.assertEqual(int(built.window_width), WIDTH)
                self.assertFalse(getattr(built, "requires_positives", False))
                self.assertIn("window_width", built.config())

    def test_frozen_variant_table_is_untouched(self) -> None:
        self.assertEqual(
            set(trm3.VARIANT_CHANNELS),
            {
                "trm3",
                "no_temporal",
                "no_temporal2",
                "s_only",
                "m_only",
                "j_only",
                "sm",
                "mj",
                "sj",
                "unseen_only",
                "surprisal_marginal",
            },
        )
        self.assertFalse(set(trm3.VARIANT_CHANNELS) & set(trm3.EXPLORATORY_VARIANT_CHANNELS))
        self.assertEqual(
            set(trm3.ALL_VARIANT_CHANNELS),
            set(trm3.VARIANT_CHANNELS) | set(trm3.EXPLORATORY_VARIANT_CHANNELS),
        )
        self.assertEqual(set(trm3.PROBABILITY_VARIANTS), set(trm3.EXPLORATORY_VARIANT_CHANNELS))

    def test_exploratory_variants_build_single_channel_configs(self) -> None:
        for variant in sorted(trm3.EXPLORATORY_VARIANT_CHANNELS):
            with self.subTest(variant=variant):
                self.assertTrue(trm3.is_exploratory_variant(variant))
                config = trm3.config_for_variant(variant)
                self.assertEqual(len(config.channels), 1)
                spec = config.channels[0]
                self.assertEqual(spec.weight, 1.0)
                self.assertAlmostEqual(spec.alpha, trm3.ALPHA, places=12)
                self.assertEqual(spec.window_width, WIDTH)
                self.assertEqual(spec.scorer, variant)
                self.assertEqual(config.decision_rule, trm3.DECISION_RULE_SEQUENTIAL)

    def test_no_preregistered_variant_is_marked_exploratory(self) -> None:
        for variant in trm3.VARIANT_CHANNELS:
            self.assertFalse(trm3.is_exploratory_variant(variant))

    def test_fitted_channels_score_through_the_trm3_interface(self) -> None:
        for variant in sorted(trm3.EXPLORATORY_VARIANT_CHANNELS):
            with self.subTest(variant=variant):
                config = trm3.config_for_variant(variant)
                state = trm3.fit_channels(ROUTINE, list(config.channels))[
                    config.channels[0].name
                ]
                ends, values = state.score(TARGET)
                self.assertEqual(ends.shape, values.shape)
                self.assertEqual(int(ends[0]), WIDTH - 1)
                self.assertEqual(int(ends[-1]), TARGET_TOKENS - 1)
                self.assertIn("status", state.state.describe())


class ExploratoryGuardTests(unittest.TestCase):
    """The ``--exploratory`` escape of the data-discipline guard (documented, default off)."""

    def setUp(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts" / "research_v3"))
        import run_trm3  # noqa: PLC0415

        self.run_trm3 = run_trm3

    def test_exploratory_marks_the_run_instead_of_refusing(self) -> None:
        block = self.run_trm3.data_discipline_guard(None, smoke=False, exploratory=True)
        self.assertTrue(block["exploratory"])
        self.assertEqual(block["label"], "EXPLORATORY_POST_HOC")
        self.assertTrue(block["dirty"])  # forced, whatever the tree looks like
        self.assertIn("dirty_entries_observed", block)
        self.assertIn("EXPLORATORY_POST_HOC", block["exploratory_rule"])

    def test_default_off_keeps_the_frozen_guard(self) -> None:
        block = self.run_trm3.data_discipline_guard(None, smoke=True)
        self.assertNotIn("exploratory", block)
        self.assertNotIn("label", block)
        with self.assertRaises(SystemExit):
            # no --freeze-commit and not a smoke run -> the frozen guard still refuses
            self.run_trm3.data_discipline_guard(None, smoke=False)

    def test_probability_variants_need_the_router_softmax_flag(self) -> None:
        self.assertTrue(trm3.PROBABILITY_VARIANTS)
        self.assertFalse(self.run_trm3.LOAD_WITH_PROBABILITIES)

    def test_decision_sink_is_off_by_default(self) -> None:
        self.assertIsNone(self.run_trm3.DECISION_SINK)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
