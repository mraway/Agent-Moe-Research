"""Unit tests for the FCM scorers (docs/research_v2/fcm_prereg.md).

CPU-only, synthetic routing.  The tests that need the local OLMoE tokenizer (the form
source ``T`` is defined on decoded token surfaces) skip themselves when it is absent.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase_a.normal_manifold import ManifoldTrace  # noqa: E402
from research_v2 import harness as H  # noqa: E402
from research_v2.features import MIDDLE_LATE_LAYERS, selection_rate_windows  # noqa: E402
from research_v2.io import HF_SNAPSHOT, LoadedTrace  # noqa: E402
from research_v2.scorers import build  # noqa: E402
from research_v2.scorers import fcm  # noqa: E402

torch.set_num_threads(4)
HAS_TOKENIZER = (HF_SNAPSHOT / "tokenizer.json").exists()


def make_trace(
    trace_id: str,
    *,
    batch: str = "b1",
    arm: str = "clean",
    positive: bool = False,
    onset: int | None = None,
    tokens: int = 64,
    fold: int = 0,
    pair_group_id: str | None = None,
    seed: int = 0,
    expert_offset: int = 0,
    drift_from: int | None = None,
    token_lo: int = 300,
    token_hi: int = 900,
) -> LoadedTrace:
    generator = torch.Generator().manual_seed(seed)
    top_k = torch.zeros(16, tokens, 8, dtype=torch.long)
    for layer in range(16):
        for position in range(tokens):
            offset = 40 if (drift_from is not None and position >= drift_from) else expert_offset
            top_k[layer, position] = torch.randperm(16, generator=generator)[:8] + offset
    token_ids = torch.randint(token_lo, token_hi, (tokens,), generator=generator, dtype=torch.long)
    record = ManifoldTrace(
        batch=batch,
        trace_id=trace_id,
        pair_group_id=pair_group_id or trace_id,
        fold=fold,
        arm=arm,
        workflow="order_status",
        workflow_family="status",
        channel="direct_user",
        domain="poetry",
        positive=positive,
        completion_boundary=None if onset is None else onset + 3,
        evidence_onset=onset,
        trace_dir=Path("/nonexistent"),
        cache_file=Path("/nonexistent"),
    )
    return LoadedTrace(
        record=record, top_k_ids=top_k, token_ids=token_ids, scenario_domain="poetry"
    )


def fixed_expert_trace(trace_id: str, *, experts: list[int], tokens: int = 32) -> LoadedTrace:
    """Every token, every layer selects the same eight experts."""

    trace = make_trace(trace_id, tokens=tokens, seed=1)
    top_k = torch.tensor(experts, dtype=torch.long).reshape(1, 1, 8).expand(16, tokens, 8)
    return LoadedTrace(
        record=trace.record,
        top_k_ids=top_k.contiguous(),
        token_ids=trace.token_ids,
        scenario_domain=trace.scenario_domain,
    )


def full_coverage_trace(trace_id: str, *, seed: int = 0, tokens: int = 128) -> LoadedTrace:
    """Routine trace where all 64 experts appear but 58-63 are the rarest.

    The rare group must be smaller than 10% of the 64 experts, otherwise the per-layer q10
    threshold falls *inside* it and the strict ``<`` comparison flags nothing.
    """

    trace = make_trace(trace_id, tokens=tokens, seed=seed)
    top_k = torch.zeros(16, tokens, 8, dtype=torch.long)
    for position in range(tokens):
        if position % 16 == 0:
            experts = torch.cat((torch.arange(58, 64), torch.tensor([0, 1])))
        else:
            experts = (torch.arange(8) * 7 + position) % 58
        top_k[:, position, :] = experts
    return LoadedTrace(
        record=trace.record,
        top_k_ids=top_k.contiguous(),
        token_ids=trace.token_ids,
        scenario_domain=trace.scenario_domain,
    )


def routine_pool(count: int = 6, tokens: int = 64) -> list[LoadedTrace]:
    return [
        make_trace(f"r{index}", pair_group_id=f"g{index}", seed=index, tokens=tokens)
        for index in range(count)
    ]


@unittest.skipUnless(HAS_TOKENIZER, "local OLMoE tokenizer required")
class TokenClassTests(unittest.TestCase):
    def test_hard_characters_are_structured(self) -> None:
        for piece in ('{"', '":', "});", " [", "']", "=", "<"):
            self.assertTrue(fcm.structured_surface(piece), piece)

    def test_soft_only_characters(self) -> None:
        self.assertTrue(fcm.structured_surface(" 123"))
        self.assertTrue(fcm.structured_surface("::"))
        self.assertTrue(fcm.structured_surface(" ,"))

    def test_pure_newline_is_prose_under_the_preregistered_rule(self) -> None:
        # Documented defect of prereg 2.1: ``core = norm.strip(" ")`` leaves "\n" in the
        # core, so the ``s0`` branch is unreachable for pure newline / indent tokens.  The
        # ``strip_whitespace`` sensitivity variant restores the intended behaviour.
        self.assertFalse(fcm.structured_surface("\n"))
        self.assertFalse(fcm.structured_surface("\n    "))
        self.assertTrue(fcm.structured_surface("\n", strip_whitespace=True))
        self.assertTrue(fcm.structured_surface("\n    ", strip_whitespace=True))
        self.assertFalse(fcm.structured_surface("  ", strip_whitespace=True))

    def test_identifier_fragments(self) -> None:
        self.assertTrue(fcm.structured_surface(" lookup_order"))
        self.assertTrue(fcm.structured_surface("getOrder"))

    def test_prose_is_not_structured(self) -> None:
        for piece in (" the", " warranty", "Hello", " tortoise"):
            self.assertFalse(fcm.structured_surface(piece), piece)

    def test_vocab_mask_matches_the_rule(self) -> None:
        mask = fcm.structured_vocab_mask()
        self.assertGreater(int(mask.sum()), 1000)
        self.assertLess(float(mask.double().mean()), 0.9)

    def test_window_fraction_alignment(self) -> None:
        trace = make_trace("t", tokens=40)
        ends_a, _ = selection_rate_windows(trace.top_k_ids, 8, MIDDLE_LATE_LAYERS)
        ends_b, fractions = fcm.structured_fraction_windows(trace.token_ids, 8)
        self.assertTrue(torch.equal(ends_a, ends_b))
        mask = fcm.structured_token_mask(trace.token_ids).double()
        self.assertAlmostEqual(float(fractions[0]), float(mask[:8].mean()), places=9)
        self.assertAlmostEqual(float(fractions[-1]), float(mask[-8:].mean()), places=9)


@unittest.skipUnless(HAS_TOKENIZER, "local OLMoE tokenizer required")
class FormThresholdTests(unittest.TestCase):
    def test_tau_is_the_routine_q80(self) -> None:
        scorer = fcm.FcmScorer(variant="run_length")
        pool = routine_pool()
        state = scorer.fit(pool)
        pooled = torch.cat([fcm.structured_fraction_windows(t.token_ids, 8)[1] for t in pool])
        self.assertAlmostEqual(state.tau, float(torch.quantile(pooled, 0.80)), places=9)
        share = float((pooled >= state.tau).double().mean())
        self.assertLessEqual(share, 0.5)
        self.assertGreater(state.form_counts["prose"], 0)

    def test_fit_uses_only_the_given_routine_pool(self) -> None:
        scorer = fcm.FcmScorer(variant="run_length")
        small = scorer.fit(routine_pool(2))
        large = scorer.fit(routine_pool(6))
        self.assertEqual(small.trace_count, 2)
        self.assertEqual(large.trace_count, 6)


@unittest.skipUnless(HAS_TOKENIZER, "local OLMoE tokenizer required")
class ScorerBehaviourTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pool = routine_pool()

    def _causal(self, scorer) -> None:
        state = scorer.fit(self.pool)
        trace = make_trace("eval", seed=99, tokens=64)
        full, ends = scorer.score(state, trace)
        cut = 40
        truncated = LoadedTrace(
            record=trace.record,
            top_k_ids=trace.top_k_ids[:, :cut, :].contiguous(),
            token_ids=trace.token_ids[:cut].contiguous(),
            scenario_domain=trace.scenario_domain,
        )
        prefix, prefix_ends = scorer.score(state, truncated)
        self.assertTrue(torch.equal(ends[: prefix_ends.numel()], prefix_ends))
        self.assertTrue(torch.allclose(full[: prefix.numel()], prefix, atol=1e-9))

    def test_form_whitened_is_causal(self) -> None:
        self._causal(fcm.FcmScorer(variant="form_whitened"))

    def test_run_length_is_causal(self) -> None:
        self._causal(fcm.FcmScorer(variant="run_length"))

    def test_unseen_expert_is_causal(self) -> None:
        self._causal(fcm.FcmScorer(variant="unseen_expert"))

    def test_unseen_transition_is_causal(self) -> None:
        self._causal(fcm.FcmScorer(variant="unseen_transition", window_width=4, layers="middle"))

    def test_run_length_counts_consecutive_structured_windows(self) -> None:
        scorer = fcm.FcmScorer(variant="run_length")
        state = scorer.fit(self.pool)
        trace = make_trace("eval", seed=7)
        scores, ends = scorer.score(state, trace)
        _, fractions = fcm.structured_fraction_windows(trace.token_ids, 8)
        flags = fractions >= state.tau
        expected = 0.0
        for index in range(flags.numel()):
            expected = expected + 1 if bool(flags[index]) else 0.0
            self.assertEqual(float(scores[index]), expected)
        self.assertEqual(int(ends.numel()), int(flags.numel()))

    def test_soft_score_is_at_most_the_assigned_score(self) -> None:
        hard = fcm.FcmScorer(variant="form_whitened", score_mode="assigned")
        soft = fcm.FcmScorer(variant="form_whitened", score_mode="soft")
        state = hard.fit(self.pool)
        trace = make_trace("eval", seed=11)
        a, _ = hard.score(state, trace)
        b, _ = soft.score(soft.fit(self.pool), trace)
        self.assertTrue(bool((b <= a + 1e-9).all()))

    def test_unseen_expert_flags_the_rarest_experts(self) -> None:
        # every expert appears in the routine pool, but experts 56-63 only rarely; the
        # per-layer q10 rule must flag them and only them.
        pool = [full_coverage_trace(f"c{i}", seed=i) for i in range(4)]
        scorer = fcm.FcmScorer(variant="unseen_expert")
        state = scorer.fit(pool)
        self.assertIsNotNone(state.rare_mask)
        self.assertTrue(bool(state.rare_mask[:, 58:].all()))
        self.assertFalse(bool(state.rare_mask[:, :58].any()))
        common = fixed_expert_trace("common", experts=list(range(8)))
        rare = fixed_expert_trace("rare", experts=[58, 59, 60, 61, 62, 63, 0, 1])
        common_score, _ = scorer.score(state, common)
        rare_score, _ = scorer.score(state, rare)
        self.assertEqual(float(common_score.max()), 0.0)
        self.assertEqual(float(rare_score.min()), float(6 * len(scorer.layers)))

    def test_unseen_transition_counts_empty_cells(self) -> None:
        scorer = fcm.FcmScorer(variant="unseen_transition", window_width=4, layers="middle")
        state = scorer.fit(self.pool)
        foreign = make_trace("foreign", seed=5, expert_offset=40)
        scores, _ = scorer.score(state, foreign)
        self.assertGreaterEqual(float(scores.min()), 0.0)
        self.assertLessEqual(float(scores.max()), float(len(scorer.layers)))
        self.assertGreater(float(scores.mean()), 0.0)

    def test_filtered_wgm_is_the_whitened_distance_of_the_kept_windows(self) -> None:
        filtered = fcm.FcmScorer(variant="filtered_wgm")
        state = filtered.fit(self.pool)
        kept = []
        for trace in self.pool:
            ends, windows = selection_rate_windows(trace.top_k_ids, 8, MIDDLE_LATE_LAYERS)
            _, fractions = fcm.structured_fraction_windows(trace.token_ids, 8)
            kept.append(windows[fractions[: ends.numel()] < state.tau])
        matrix = torch.cat(kept)
        mu = matrix.mean(0)
        sd = matrix.std(0) + 1e-3
        trace = make_trace("eval", seed=13)
        scores, _ = filtered.score(state, trace)
        _, windows = selection_rate_windows(trace.top_k_ids, 8, MIDDLE_LATE_LAYERS)
        expected = (((windows - mu) / sd) ** 2).sum(1).double()
        self.assertTrue(torch.allclose(scores, expected, rtol=1e-6, atol=1e-6))

    def test_g1_equivalence_of_the_unfiltered_whitened_distance(self) -> None:
        trace = make_trace("eval", seed=13)
        reference = build("wgm", {"metric": "g1", "layers": "middle_late", "window_width": 8})
        reference_scores, _ = reference.score(reference.fit(self.pool), trace)
        _, windows = selection_rate_windows(trace.top_k_ids, 8, MIDDLE_LATE_LAYERS)
        blocks = torch.cat(
            [selection_rate_windows(t.top_k_ids, 8, MIDDLE_LATE_LAYERS)[1] for t in self.pool]
        )
        mu = blocks.mean(0)
        sd = blocks.std(0) + 1e-3
        expected = (((windows - mu) / sd) ** 2).sum(1)
        self.assertTrue(torch.allclose(reference_scores, expected, rtol=1e-5, atol=1e-4))

    def test_filtered_wgm_drops_structured_windows(self) -> None:
        filtered = fcm.FcmScorer(variant="filtered_wgm")
        state = filtered.fit(self.pool)
        self.assertGreater(state.form_counts["dropped_windows"], 0)
        self.assertEqual(
            state.form_counts["dropped_windows"] + state.form_counts["kept_windows"],
            sum(
                int(fcm.structured_fraction_windows(t.token_ids, 8)[1].numel()) for t in self.pool
            ),
        )

    def test_filtered_pdm_drops_structured_tokens(self) -> None:
        scorer = fcm.FcmScorer(variant="filtered_pdm", window_width=4, layers="middle")
        state = scorer.fit(self.pool)
        self.assertGreater(state.form_counts["dropped_tokens"], 0)
        self.assertEqual(
            state.form_counts["dropped_tokens"] + state.form_counts["kept_tokens"],
            sum(int(t.token_ids.numel()) for t in self.pool),
        )
        scores, ends = scorer.score(state, make_trace("eval", seed=17))
        self.assertEqual(int(scores.numel()), int(ends.numel()))


class KmeansTests(unittest.TestCase):
    def test_kmeans_separates_two_blobs_and_is_deterministic(self) -> None:
        generator = torch.Generator().manual_seed(0)
        block_a = torch.randn(100, 4, generator=generator)
        block_b = torch.randn(100, 4, generator=generator) + 10.0
        matrix = torch.cat((block_a, block_b))
        centres = fcm.kmeans(matrix, 2, seed=0)
        again = fcm.kmeans(matrix, 2, seed=0)
        self.assertTrue(torch.allclose(centres, again))
        assignment = torch.cdist(matrix, centres).argmin(dim=1)
        self.assertEqual(len(set(assignment[:100].tolist())), 1)
        self.assertEqual(len(set(assignment[100:].tolist())), 1)
        self.assertNotEqual(int(assignment[0]), int(assignment[100]))


@unittest.skipUnless(HAS_TOKENIZER, "local OLMoE tokenizer required")
class HarnessIntegrationTests(unittest.TestCase):
    def test_registry_and_end_to_end_run(self) -> None:
        self.assertIsNotNone(build("fcm", {"variant": "form_whitened"}))
        b1 = [
            make_trace(f"b1-{i}", batch="b1", pair_group_id=f"p{i}", seed=i, fold=i % 2)
            for i in range(8)
        ] + [
            make_trace(
                f"b1-d{i}", batch="b1", arm="attack", positive=True, onset=24,
                pair_group_id=f"pd{i}", seed=50 + i, drift_from=24, fold=i % 2,
            )
            for i in range(2)
        ]
        b2 = [
            make_trace(f"b2-{i}", batch="b2", pair_group_id=f"q{i}", seed=100 + i, fold=i % 2)
            for i in range(8)
        ] + [
            make_trace(
                f"b2-d{i}", batch="b2", arm="attack", positive=True, onset=24,
                pair_group_id=f"qd{i}", seed=150 + i, drift_from=24, fold=i % 2,
            )
            for i in range(2)
        ]
        config = H.HarnessConfig(
            scorer="fcm",
            scorer_config={"variant": "form_whitened"},
            windows=(8,),
            splits=("S1",),
            modes=("D",),
            alphas=(0.10,),
            readings=("persist2",),
            min_bucket_traces=2,
            bootstrap_draws=0,
            emit_q1=False,
            emit_audit=False,
            emit_ranking=False,
        )
        result = H.run_harness(config, batches={"b1": tuple(b1), "b2": tuple(b2)})
        self.assertEqual(len(result["case_runs"]), 2)
        for case_run in result["case_runs"]:
            self.assertTrue(case_run["candidates"])
            self.assertLessEqual(case_run["fit_trace_count"], 8)


if __name__ == "__main__":
    unittest.main()
