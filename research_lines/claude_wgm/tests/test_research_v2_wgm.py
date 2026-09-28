"""Fast synthetic unit tests for the WGM scorer (proposal 1, spec section 3)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research_v2.features import MIDDLE_LAYERS, selection_rate_windows  # noqa: E402
from research_v2.scorers import build  # noqa: E402
from research_v2.scorers.geometry import WhitenedDistanceScorer  # noqa: E402
from research_v2.scorers.wgm import RANK_GRID, WGMScorer  # noqa: E402

LAYERS = 16
EXPERTS = 64
TOP_K = 8


class FakeTrace:
    """Minimal stand-in for ``research_v2.io.LoadedTrace``."""

    def __init__(
        self,
        trace_id: str,
        top_k_ids: torch.Tensor,
        *,
        workflow: str = "wf_a",
        pair_group_id: str | None = None,
    ) -> None:
        self.trace_id = trace_id
        self.top_k_ids = top_k_ids
        self.workflow = workflow
        self.pair_group_id = pair_group_id or trace_id


def _routing(tokens: int, generator: torch.Generator, *, favoured: int = 0) -> torch.Tensor:
    """[16, tokens, 8] top-8 expert ids drawn from a low-dimensional preference."""

    weights = torch.full((LAYERS, EXPERTS), 0.05)
    weights[:, favoured : favoured + 12] = 1.0
    ids = torch.empty((LAYERS, tokens, TOP_K), dtype=torch.long)
    for layer in range(LAYERS):
        probabilities = weights[layer].expand(tokens, EXPERTS)
        ids[layer] = torch.multinomial(probabilities, TOP_K, replacement=False, generator=generator)
    return ids


def _routine_traces(count: int = 12, tokens: int = 60) -> list[FakeTrace]:
    generator = torch.Generator().manual_seed(7)
    return [
        FakeTrace(
            f"r{index:02d}",
            _routing(tokens, generator),
            workflow="wf_a" if index % 2 == 0 else "wf_b",
            pair_group_id=f"g{index:02d}",
        )
        for index in range(count)
    ]


class WGMScorerTests(unittest.TestCase):
    def setUp(self) -> None:
        torch.set_num_threads(2)
        self.routine = _routine_traces()

    # -- registry / interface -------------------------------------------------
    def test_registered_and_buildable(self) -> None:
        from research_v2.scorers import available

        self.assertIn("wgm", available())
        scorer = build("wgm", {"window_width": 4, "metric": "g2", "rank": 8})
        self.assertIsInstance(scorer, WGMScorer)
        self.assertEqual(scorer.window_width, 4)
        self.assertEqual(scorer.config()["metric"], "g2")

    def test_score_shape_and_causal_ends(self) -> None:
        scorer = WGMScorer(window_width=8)
        state = scorer.fit(self.routine)
        ends, scores = None, None
        trace = self.routine[0]
        scores, ends = scorer.score(state, trace)
        expected_ends, _ = selection_rate_windows(trace.top_k_ids, 8, MIDDLE_LAYERS)
        self.assertEqual(scores.shape[0], ends.shape[0])
        self.assertTrue(torch.equal(ends, expected_ends))
        self.assertEqual(int(ends[0]), 7)
        self.assertTrue(torch.isfinite(scores).all())

    def test_scores_are_causal_prefix_stable(self) -> None:
        """Truncating a trace must not change the scores of the surviving windows."""

        scorer = WGMScorer(window_width=8)
        state = scorer.fit(self.routine)
        trace = self.routine[0]
        full_scores, full_ends = scorer.score(state, trace)
        cut = 40
        truncated = FakeTrace("cut", trace.top_k_ids[:, :cut, :], workflow=trace.workflow)
        part_scores, part_ends = scorer.score(state, truncated)
        keep = full_ends < cut
        self.assertTrue(torch.equal(full_ends[keep], part_ends))
        self.assertTrue(torch.allclose(full_scores[keep], part_scores, atol=1e-5))

    def test_short_trace_returns_empty(self) -> None:
        scorer = WGMScorer(window_width=8)
        state = scorer.fit(self.routine)
        generator = torch.Generator().manual_seed(1)
        short = FakeTrace("short", _routing(4, generator))
        scores, ends = scorer.score(state, short)
        self.assertEqual(scores.numel(), 0)
        self.assertEqual(ends.numel(), 0)

    def test_fit_rejects_empty_routine(self) -> None:
        with self.assertRaises(ValueError):
            WGMScorer(window_width=8).fit([])

    # -- G1 ------------------------------------------------------------------
    def test_g1_matches_harness_reference_scorer(self) -> None:
        """C1 must be numerically identical to the harness's g1_whitened_distance."""

        mine = WGMScorer(window_width=8, layers="middle", metric="g1")
        reference = WhitenedDistanceScorer(window_width=8, layers="middle")
        state_a = mine.fit(self.routine)
        state_b = reference.fit(self.routine)
        for trace in self.routine[:3]:
            scores_a, ends_a = mine.score(state_a, trace)
            scores_b, ends_b = reference.score(state_b, trace)
            self.assertTrue(torch.equal(ends_a, ends_b))
            self.assertTrue(torch.allclose(scores_a, scores_b, atol=1e-5))

    def test_g1_flags_off_manifold_traffic(self) -> None:
        scorer = WGMScorer(window_width=8)
        state = scorer.fit(self.routine)
        generator = torch.Generator().manual_seed(99)
        drift = FakeTrace("d", _routing(60, generator, favoured=40))
        routine_max = max(float(scorer.score(state, t)[0].max()) for t in self.routine)
        drift_max = float(scorer.score(state, drift)[0].max())
        self.assertGreater(drift_max, routine_max)

    def test_sqrt_transform_changes_scores(self) -> None:
        plain = WGMScorer(window_width=8, transform="identity")
        hell = WGMScorer(window_width=8, transform="sqrt")
        trace = self.routine[0]
        a, _ = plain.score(plain.fit(self.routine), trace)
        b, _ = hell.score(hell.fit(self.routine), trace)
        self.assertEqual(a.shape, b.shape)
        self.assertFalse(torch.allclose(a, b, atol=1e-3))

    def test_trace_equal_weight_option(self) -> None:
        pooled = WGMScorer(window_width=8, trace_equal_weight=False)
        equal = WGMScorer(window_width=8, trace_equal_weight=True)
        trace = self.routine[0]
        a, _ = pooled.score(pooled.fit(self.routine), trace)
        b, _ = equal.score(equal.fit(self.routine), trace)
        self.assertEqual(a.shape, b.shape)
        self.assertTrue(torch.isfinite(b).all())

    # -- G2 ------------------------------------------------------------------
    def test_g2_residual_is_bounded_by_g1(self) -> None:
        g1 = WGMScorer(window_width=8, metric="g1")
        g2 = WGMScorer(window_width=8, metric="g2", rank=8)
        trace = self.routine[0]
        total, _ = g1.score(g1.fit(self.routine), trace)
        residual, _ = g2.score(g2.fit(self.routine), trace)
        self.assertTrue(torch.all(residual <= total + 1e-4))
        self.assertTrue(torch.all(residual >= -1e-6))

    def test_g2_rank_selection_is_routine_only_and_recorded(self) -> None:
        scorer = WGMScorer(window_width=8, metric="g2", rank="auto")
        state = scorer.fit(self.routine)
        selection = state.rank_selection
        self.assertIn(state.rank, RANK_GRID)
        self.assertEqual(selection["grid"], list(RANK_GRID))
        self.assertGreater(selection["holdout_traces"], 0)
        self.assertGreater(selection["fit_traces"], 0)
        self.assertEqual(
            selection["holdout_traces"] + selection["fit_traces"], len(self.routine)
        )
        curve = {int(k): v for k, v in selection["reconstruction_error"].items()}
        for low, high in zip(sorted(curve)[:-1], sorted(curve)[1:]):
            self.assertLessEqual(curve[high], curve[low] + 1e-6)

    def test_g2_elbow_rule_picks_smallest_rank_with_small_gain(self) -> None:
        """The preregistered rule: first r whose next grid point gains < 10%."""

        scorer = WGMScorer(window_width=8, metric="g2", rank="auto")
        state = scorer.fit(self.routine)
        reductions = {int(k): v for k, v in state.rank_selection["relative_reduction"].items()}
        expected = max(RANK_GRID)
        for rank in RANK_GRID[:-1]:
            if reductions[rank] < 0.10:
                expected = rank
                break
        self.assertEqual(state.rank, expected)

    def test_g2_fixed_rank_32_is_honoured(self) -> None:
        scorer = WGMScorer(window_width=8, metric="g2", rank=32)
        state = scorer.fit(self.routine)
        self.assertEqual(state.rank, 32)
        self.assertEqual(state.components.shape[1], 32)

    # -- G3 ------------------------------------------------------------------
    def test_g3_reference_set_is_capped_and_deterministic(self) -> None:
        scorer = WGMScorer(window_width=8, metric="g3", knn_reference_cap=50)
        state_a = scorer.fit(self.routine)
        state_b = scorer.fit(self.routine)
        self.assertLessEqual(state_a.reference.shape[0], 50)
        self.assertEqual(state_a.reference.shape[1], 32)
        self.assertTrue(torch.allclose(state_a.reference, state_b.reference))
        trace = self.routine[0]
        self.assertTrue(
            torch.allclose(scorer.score(state_a, trace)[0], scorer.score(state_b, trace)[0])
        )

    def test_g3_scores_are_non_negative(self) -> None:
        scorer = WGMScorer(window_width=8, metric="g3")
        state = scorer.fit(self.routine)
        scores, _ = scorer.score(state, self.routine[0])
        self.assertTrue(torch.all(scores >= 0))

    # -- workflow centre ------------------------------------------------------
    def test_workflow_centre_shrinks_towards_global(self) -> None:
        scorer = WGMScorer(window_width=8, centre="workflow", workflow_shrinkage=20.0)
        state = scorer.fit(self.routine)
        self.assertEqual(set(state.workflow_centres), {"wf_a", "wf_b"})
        # 6 traces per workflow, kappa = 20 -> weight 6/26 on the local mean.
        for centre in state.workflow_centres.values():
            self.assertLess(float((centre - state.centre).abs().max()), 1.0)

    def test_unknown_workflow_falls_back_to_global_centre(self) -> None:
        scorer = WGMScorer(window_width=8, centre="workflow")
        state = scorer.fit(self.routine)
        trace = self.routine[0]
        unknown = FakeTrace("u", trace.top_k_ids, workflow="wf_unseen")
        global_scorer = WGMScorer(window_width=8, centre="global")
        global_state = global_scorer.fit(self.routine)
        a, _ = scorer.score(state, unknown)
        b, _ = global_scorer.score(global_state, trace)
        self.assertTrue(torch.allclose(a, b, atol=1e-5))

    # -- configuration guards --------------------------------------------------
    def test_invalid_configuration_raises(self) -> None:
        for kwargs in ({"metric": "g9"}, {"transform": "log"}, {"centre": "domain"}):
            with self.assertRaises(ValueError):
                WGMScorer(**kwargs)

    def test_layer_bands(self) -> None:
        self.assertEqual(len(WGMScorer(layers="middle").layers), 7)
        self.assertEqual(len(WGMScorer(layers="middle_late").layers), 11)
        self.assertEqual(len(WGMScorer(layers="all").layers), 16)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
