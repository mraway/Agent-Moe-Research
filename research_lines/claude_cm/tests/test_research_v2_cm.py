"""Unit tests for the CM (conditional manifold) scorer, spec section 4.

Synthetic data only; no routing cache, no embedding matrix, no model.  The OLMoE
static embedding lookup is monkeypatched with a small deterministic matrix so the
C2 path is exercised without touching the local weights.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io as rio  # noqa: E402
from research_v2.scorers import available, build  # noqa: E402
from research_v2.scorers import cm as cm_module  # noqa: E402

VOCAB = 64
HIDDEN = 24


def _fake_embeddings() -> torch.Tensor:
    generator = torch.Generator().manual_seed(7)
    return torch.randn(cm_module.VOCAB_SIZE, HIDDEN, generator=generator)


class FakeTrace:
    """Minimal stand-in for ``LoadedTrace`` (only what the scorer may read)."""

    def __init__(
        self,
        trace_id: str,
        token_ids: torch.Tensor,
        top_k_ids: torch.Tensor,
        *,
        pair_group_id: str = "g0",
        workflow: str = "wf",
        batch: str = "b1",
    ) -> None:
        self.trace_id = trace_id
        self.token_ids = token_ids
        self.top_k_ids = top_k_ids
        self.pair_group_id = pair_group_id
        self.workflow = workflow
        self.batch = batch
        self.record = SimpleNamespace(trace_dir=Path("/nonexistent"))


def _routing(token_ids: torch.Tensor, *, layers: int = 16, shift: int = 0, seed: int = 0) -> torch.Tensor:
    """Deterministic top-8 sets driven by the token id (so C1 has something to learn)."""

    generator = torch.Generator().manual_seed(seed)
    tokens = token_ids.numel()
    out = torch.zeros(layers, tokens, 8, dtype=torch.long)
    for index, token in enumerate(token_ids.tolist()):
        for layer in range(layers):
            base = (int(token) * 7 + layer * 3 + shift) % 64
            experts = [(base + offset) % 64 for offset in range(8)]
            if torch.rand(1, generator=generator).item() < 0.15:  # a little routine noise
                replacement = int(torch.randint(0, 64, (1,), generator=generator).item())
                while replacement in experts:
                    replacement = (replacement + 1) % 64
                experts[0] = replacement
            out[layer, index] = torch.tensor(experts)
    return out


def _make_traces(count: int, *, shift: int = 0, seed: int = 0, prefix: str = "r", length: int = 60):
    generator = torch.Generator().manual_seed(seed + 1000)
    traces = []
    for index in range(count):
        token_ids = torch.randint(0, VOCAB, (length,), generator=generator)
        traces.append(
            FakeTrace(
                f"{prefix}{index}",
                token_ids,
                _routing(token_ids, shift=shift, seed=seed + index),
                pair_group_id=f"g{index // 2}",
            )
        )
    return traces


class IndicatorTests(unittest.TestCase):
    def test_indicator_shape_and_row_sums(self) -> None:
        token_ids = torch.arange(10)
        top_k = _routing(token_ids)
        ind = cm_module.indicator(top_k, cm_module.MIDDLE_LAYERS)
        self.assertEqual(tuple(ind.shape), (10, 7 * 64))
        per_layer = ind.reshape(10, 7, 64).sum(2)
        self.assertTrue(torch.all(per_layer <= 8))
        self.assertTrue(torch.all(per_layer >= 1))

    def test_indicator_selects_requested_layers(self) -> None:
        token_ids = torch.arange(4)
        top_k = _routing(token_ids)
        ind = cm_module.indicator(top_k, (0,))
        for position in range(4):
            chosen = torch.nonzero(ind[position]).flatten().tolist()
            self.assertEqual(set(chosen), set(top_k[0, position].tolist()))


class C1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.routine = _make_traces(12, seed=1)

    def test_fit_uses_only_routine_and_reports_coverage(self) -> None:
        scorer = cm_module.ConditionalManifoldScorer(window_width=8)
        state = scorer.fit(self.routine)
        self.assertEqual(state.diagnostics["routine_trace_count"], 12)
        self.assertGreater(state.diagnostics["covered_token_type_count"], 0)
        self.assertGreater(state.diagnostics["routine_covered_token_fraction"], 0.5)
        self.assertFalse(bool(state.covered_row[0]))

    def test_shrinkage_matches_closed_form(self) -> None:
        scorer = cm_module.ConditionalManifoldScorer(window_width=4, layers=(0,), shrinkage_m=5.0)
        state = scorer.fit(self.routine)
        counts = torch.zeros(state.table.shape[0])
        sums = torch.zeros(state.table.shape[0], 64)
        for trace in self.routine:
            rows = state.token_row[trace.token_ids.long()]
            ind = cm_module.indicator(trace.top_k_ids, (0,))
            counts.index_add_(0, rows, torch.ones(rows.numel()))
            sums.index_add_(0, rows, ind)
        row = int(torch.argmax(counts))
        expected = (sums[row] + 5.0 * state.mu) / (counts[row] + 5.0)
        self.assertTrue(torch.allclose(state.table[row], expected, atol=1e-5))

    def test_score_is_causal_prefix_stable(self) -> None:
        scorer = cm_module.ConditionalManifoldScorer(window_width=8)
        state = scorer.fit(self.routine)
        trace = self.routine[0]
        full, ends = scorer.score(state, trace)
        cut = 40
        prefix = FakeTrace("cut", trace.token_ids[:cut], trace.top_k_ids[:, :cut, :])
        partial, prefix_ends = scorer.score(state, prefix)
        self.assertTrue(torch.equal(prefix_ends, ends[: prefix_ends.numel()]))
        self.assertTrue(torch.allclose(partial, full[: partial.numel()], atol=1e-6))

    def test_windows_and_ends_follow_the_interface(self) -> None:
        scorer = cm_module.ConditionalManifoldScorer(window_width=8)
        state = scorer.fit(self.routine)
        scores, ends = scorer.score(state, self.routine[0])
        self.assertEqual(scores.numel(), ends.numel())
        self.assertEqual(int(ends[0]), 7)
        self.assertEqual(int(ends[-1]), self.routine[0].token_ids.numel() - 1)
        self.assertTrue(torch.all(scores >= 0))

    def test_off_manifold_routing_scores_higher(self) -> None:
        scorer = cm_module.ConditionalManifoldScorer(window_width=8)
        state = scorer.fit(self.routine)
        normal, _ = scorer.score(state, self.routine[0])
        drifted_ids = self.routine[0].token_ids
        drifted = FakeTrace("drift", drifted_ids, _routing(drifted_ids, shift=17, seed=99))
        shifted, _ = scorer.score(state, drifted)
        self.assertGreater(float(shifted.mean()), float(normal.mean()))

    def test_uncovered_zero_variant_zeroes_novel_tokens(self) -> None:
        scorer = cm_module.ConditionalManifoldScorer(window_width=4, uncovered="zero")
        state = scorer.fit(self.routine)
        novel_ids = torch.full((12,), VOCAB + 5, dtype=torch.long)
        novel = FakeTrace("novel", novel_ids, _routing(novel_ids, shift=23, seed=5))
        residual = scorer._residual(state, novel)
        self.assertTrue(torch.allclose(residual, torch.zeros_like(residual)))

    def test_uncovered_mu_variant_keeps_novel_tokens(self) -> None:
        scorer = cm_module.ConditionalManifoldScorer(window_width=4, uncovered="mu")
        state = scorer.fit(self.routine)
        novel_ids = torch.full((12,), VOCAB + 5, dtype=torch.long)
        novel = FakeTrace("novel", novel_ids, _routing(novel_ids, shift=23, seed=5))
        residual = scorer._residual(state, novel)
        self.assertGreater(float(residual.abs().sum()), 0.0)


class C2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._original = rio.load_input_embeddings
        matrix = _fake_embeddings()
        rio.load_input_embeddings = lambda *args, **kwargs: matrix  # type: ignore[assignment]
        self.routine = _make_traces(12, seed=3)

    def tearDown(self) -> None:
        rio.load_input_embeddings = self._original  # type: ignore[assignment]

    def test_text_features_have_expected_width_and_are_causal(self) -> None:
        scorer = cm_module.ConditionalManifoldScorer(window_width=8, conditioning="c1c2", pca_components=8)
        state = scorer.fit(self.routine)
        features = scorer._text_features(state, self.routine[0])
        self.assertEqual(features.shape[1], 8 * 3 + 1)
        cut = 30
        prefix = FakeTrace("cut", self.routine[0].token_ids[:cut], self.routine[0].top_k_ids[:, :cut, :])
        prefix_features = scorer._text_features(state, prefix)
        self.assertTrue(torch.allclose(prefix_features, features[:cut], atol=1e-5))

    def test_ridge_reports_holdout_r2_for_both_targets(self) -> None:
        scorer = cm_module.ConditionalManifoldScorer(window_width=8, conditioning="c1c2", pca_components=8)
        state = scorer.fit(self.routine)
        report = state.diagnostics["c2_ridge"]
        for label in ("ind", "c1_residual"):
            self.assertIn(label, report)
            self.assertEqual(len(report[label]["curve"]), len(scorer.ridge_lambdas))
            self.assertIn(report[label]["selected_lambda"], scorer.ridge_lambdas)
            best = max(row["holdout_r2"] for row in report[label]["curve"])
            self.assertAlmostEqual(report[label]["holdout_r2"], best, places=9)

    def test_c1c2_scores_run_end_to_end(self) -> None:
        scorer = cm_module.ConditionalManifoldScorer(window_width=8, conditioning="c1c2", pca_components=8)
        state = scorer.fit(self.routine)
        scores, ends = scorer.score(state, self.routine[0])
        self.assertEqual(scores.numel(), ends.numel())
        self.assertTrue(torch.all(torch.isfinite(scores)))

    def test_x_knn_control_scores_novel_text_higher(self) -> None:
        scorer = cm_module.XFeatureKnnScorer(window_width=8, pca_components=8, reference_cap=500)
        state = scorer.fit(self.routine)
        near, _ = scorer.score(state, self.routine[0])
        generator = torch.Generator().manual_seed(5)
        far_ids = torch.randint(VOCAB + 100, VOCAB + 200, (60,), generator=generator)
        far = FakeTrace("far", far_ids, _routing(far_ids))
        distant, _ = scorer.score(state, far)
        self.assertGreater(float(distant.mean()), float(near.mean()))


class C3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.routine = _make_traces(16, seed=11)
        self._original = cm_module.prefill_summary
        generator = torch.Generator().manual_seed(21)
        self.summaries = {
            trace.trace_id: torch.rand(1024, generator=generator) for trace in self.routine
        }
        cm_module.prefill_summary = lambda trace, *args, **kwargs: self.summaries.get(  # type: ignore[assignment]
            trace.trace_id, torch.zeros(1024)
        )

    def tearDown(self) -> None:
        cm_module.prefill_summary = self._original  # type: ignore[assignment]

    def test_c3_falls_back_to_constant_when_holdout_r2_is_not_positive(self) -> None:
        scorer = cm_module.ConditionalManifoldScorer(window_width=8, use_c3=True, c3_components=4)
        state = scorer.fit(self.routine)
        self.assertIn("c3", state.diagnostics)
        self.assertIn(state.diagnostics["c3"]["status"], ("fitted", "constant"))
        scores, ends = scorer.score(state, self.routine[0])
        self.assertEqual(scores.numel(), ends.numel())
        self.assertTrue(torch.all(torch.isfinite(scores)))

    def test_c3_rescales_but_keeps_within_trace_ordering(self) -> None:
        plain = cm_module.ConditionalManifoldScorer(window_width=8)
        conditioned = cm_module.ConditionalManifoldScorer(window_width=8, use_c3=True, c3_components=4)
        state_plain = plain.fit(self.routine)
        state_c3 = conditioned.fit(self.routine)
        a, _ = plain.score(state_plain, self.routine[0])
        b, _ = conditioned.score(state_c3, self.routine[0])
        self.assertTrue(torch.equal(torch.argsort(a), torch.argsort(b)))


class ExtraCalibrationFilterTests(unittest.TestCase):
    """Regression test for the harness fix this proposal needed (spec 1.6).

    The extra B1 brief=present calibration pool must obey the routine definition in
    force; with ``routine="cb"`` its resisted-attack traces must not reach the
    conformal threshold.
    """

    def test_extra_pool_obeys_routine_definition(self) -> None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_research_v2_harness import make_trace, synthetic_batches  # noqa: E402

        from research_v2.harness import HarnessConfig, build_split_cases, run_case  # noqa: E402
        from research_v2.readings import build_readings  # noqa: E402

        batches = synthetic_batches()
        case = [c for c in build_split_cases(batches, "S1") if c.name == "b1_to_b2"][0]
        extras = (
            make_trace("extra-clean", batch="b1", arm="clean", pair_group_id="extra-pg0", seed=41),
            make_trace("extra-benign", batch="b1", arm="benign_control", pair_group_id="extra-pg1", seed=42),
            make_trace("extra-resist", batch="b1", arm="attack", pair_group_id="extra-pg2", seed=43),
        )
        config = HarnessConfig(
            scorer="cm",
            windows=(8,),
            modes=("D",),
            alphas=(0.10,),
            readings=("max",),
            bootstrap_draws=0,
            emit_q1=False,
            emit_audit=False,
            store_score_streams=False,
        )
        scorer = build("cm", {"window_width": 8})
        readings = build_readings(("max",))
        for definition, expected in (("cb", 2), ("all_normal", 3)):
            result = run_case(case, scorer, config, definition, 8, readings, extra_calibration=extras)
            self.assertEqual(result["extra_calibration_trace_count"], expected)
            used = sum(
                half["calibration_from_extra_pool"]
                for half in result["calibration"]["D"]["halves"].values()
            )
            self.assertEqual(used, expected)


class RegistryTests(unittest.TestCase):
    def test_scorers_are_registered(self) -> None:
        self.assertIn("cm", available())
        self.assertIn("cm_x_knn", available())

    def test_build_passes_config(self) -> None:
        scorer = build("cm", {"window_width": 4, "layers": "all", "conditioning": "c1"})
        self.assertEqual(scorer.window_width, 4)
        self.assertEqual(len(scorer.layers), 16)
        self.assertEqual(scorer.config()["layers"], "all")

    def test_unknown_options_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build("cm", {"conditioning": "c9"})
        with self.assertRaises(ValueError):
            build("cm", {"uncovered": "nan"})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
