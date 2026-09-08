"""The weight-aware G statistic families (in-set residual mass, prob-JS, prob-rare-mass).

These three families are the only ones on dataset G that read the FULL router softmax
rather than the 0/1 top-k view.  What is pinned here:

* **shapes / geometry** -- per-token feature widths and the 24 x 32 x 4 gpt-oss geometry;
* **causality** -- the window score at endpoint ``t`` is a function of tokens
  ``t - w + 1 .. t`` and of nothing after ``t`` (mutating a later token cannot move it);
* **exact small cases** -- Jensen-Shannon on hand-computable rows, the rare-mass sum on a
  hand-built rare set, and the two properties the in-set residual's definition rests on:
  the streaming normal equations equal a direct least squares, and an exactly linear
  in-set mass produces an exactly zero residual;
* **channel boundaries and the standardisation / calibration path** -- the same
  ``segmented_windows`` rule and the same ``calibrate_g`` entry point as S / M / B;
* **the data contract** -- ``io_g.episode_logits`` reads the FULL logits of a real trace
  shard and the softmax mass on the STORED top-k equals the mass on a recomputed top-k
  (bfloat16 differences are exact ties).
"""

from __future__ import annotations

import math
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io_g, trm3, trm3_g  # noqa: E402
from research_v2.features import selection_counts_per_token  # noqa: E402

torch.set_num_threads(4)

LAYERS = 24
EXPERTS = 32
TOP_K = 4
ROUTER = {"num_moe_layers": LAYERS, "num_experts": EXPERTS, "top_k": TOP_K}


@dataclass
class FakeEpisode:
    """Minimal duck type of :class:`io_g.GEpisode` for the weight-aware families."""

    trace_id: str
    top_k_ids: torch.Tensor
    logits: torch.Tensor
    channel_tags: tuple[str, ...]
    pair_group_id: str = "group"
    workflow: str = "w"
    batch: str = "syn"
    variant: str = "clean"
    session_id: str = "s"
    router: dict | None = field(default_factory=lambda: dict(ROUTER))
    labels: dict = field(default_factory=dict)

    @property
    def token_count(self) -> int:
        return int(self.top_k_ids.shape[1])

    def probabilities(self, **_: object) -> torch.Tensor:
        return torch.softmax(self.logits.to(torch.float32), dim=-1)


def synthetic_episode(
    key: str,
    tokens: int,
    *,
    generator: torch.Generator,
    tags: tuple[str, ...] | None = None,
    scale: float = 1.0,
) -> FakeEpisode:
    """One episode whose stored top-k is the argtop-k of its own logits (no ties)."""

    logits = torch.randn((LAYERS, tokens, EXPERTS), generator=generator) * scale
    # a skewed offset so some experts are genuinely rare and channel S / RM are not vacuous
    offset = torch.tensor([4.5 * (0.82**index) for index in range(EXPERTS)])
    logits = logits + offset[None, None, :]
    ids = torch.topk(logits, TOP_K, dim=-1).indices
    if tags is None:
        tags = tuple(["analysis"] * (tokens // 3) + ["final"] * (tokens - tokens // 3))
    return FakeEpisode(trace_id=key, top_k_ids=ids, logits=logits, channel_tags=tags)


def synthetic_pool(count: int, tokens: int = 60, seed: int = 20260907) -> list[FakeEpisode]:
    generator = torch.Generator().manual_seed(seed)
    return [synthetic_episode(f"ep-{i}", tokens, generator=generator) for i in range(count)]


# ---------------------------------------------------------------------------
# shapes and geometry
# ---------------------------------------------------------------------------


class ShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pool = synthetic_pool(6)
        self.view = trm3_g.VIEWS["V1"]

    def test_registry_exposes_the_three_families(self) -> None:
        for name in ("in_set_residual_mass", "prob_js", "prob_rare_mass"):
            statistic = trm3_g.build_statistic(name)
            self.assertIn(statistic.name, trm3_g.PROB_STATISTICS)
            self.assertEqual(statistic.window_width, 8)

    def test_in_set_residual_feature_width(self) -> None:
        statistic = trm3_g.build_statistic("in_set_residual_mass").fit(self.pool, self.view)
        features = statistic.per_token(self.pool[0])
        self.assertEqual(tuple(features.shape), (self.pool[0].token_count, LAYERS + LAYERS * EXPERTS))
        # the first L columns are the in-set mass, in (0, 1]
        mass = features[:, :LAYERS]
        self.assertTrue(bool((mass > 0).all()) and bool((mass <= 1).all()))
        # the rest is exactly the 0/1 indicator of the frozen helper
        indicator = selection_counts_per_token(self.pool[0].top_k_ids, range(LAYERS), EXPERTS)
        self.assertTrue(torch.equal(features[:, LAYERS:], indicator))

    def test_prob_js_feature_width_and_range(self) -> None:
        statistic = trm3_g.build_statistic("prob_js").fit(self.pool, self.view)
        features = statistic.per_token(self.pool[0])
        self.assertEqual(tuple(features.shape), (self.pool[0].token_count, LAYERS * EXPERTS))
        _, scores, _, _ = statistic.stream(self.pool[0], self.view)
        self.assertTrue(bool(np.all(scores >= 0.0)))
        self.assertTrue(bool(np.all(scores <= LAYERS * math.log(2.0) + 1e-9)))

    def test_prob_rare_mass_feature_width(self) -> None:
        statistic = trm3_g.build_statistic("prob_rare_mass").fit(self.pool, self.view)
        features = statistic.per_token(self.pool[0])
        self.assertEqual(tuple(features.shape), (self.pool[0].token_count, 1))
        self.assertTrue(bool((features >= 0).all()))
        self.assertGreater(int(statistic.rare_mask.sum()), 0, "the fixture must have a rare tail")

    def test_describe_blocks_are_json_shaped(self) -> None:
        for name in ("in_set_residual_mass", "prob_js", "prob_rare_mass"):
            statistic = trm3_g.build_statistic(name).fit(self.pool, self.view)
            block = statistic.describe()
            self.assertEqual(block["statistic"], statistic.name)
            self.assertEqual(block["window_width"], 8)
            self.assertEqual(block["layers"], list(range(LAYERS)))

    def test_missing_probabilities_is_an_error_not_a_silent_zero(self) -> None:
        class NoProbs:
            trace_id = "x"
            top_k_ids = self.pool[0].top_k_ids
            channel_tags = self.pool[0].channel_tags
            router = dict(ROUTER)

        statistic = trm3_g.build_statistic("prob_js").fit(self.pool, self.view)
        with self.assertRaises(ValueError):
            statistic.per_token(NoProbs())


# ---------------------------------------------------------------------------
# causality
# ---------------------------------------------------------------------------


class CausalityTest(unittest.TestCase):
    """A window ending at ``t`` may not move when a token after ``t`` changes."""

    def setUp(self) -> None:
        self.pool = synthetic_pool(6)
        self.view = trm3_g.VIEWS["V3"]  # single channel: one run, frozen global windows

    def _mutated(self, episode: FakeEpisode, index: int) -> FakeEpisode:
        logits = episode.logits.clone()
        logits[:, index, :] = logits[:, index, :].flip(-1) * 3.0 + 5.0
        ids = torch.topk(logits, TOP_K, dim=-1).indices
        top_k = episode.top_k_ids.clone()
        top_k[:, index, :] = ids[:, index, :]
        return FakeEpisode(
            trace_id=episode.trace_id,
            top_k_ids=top_k,
            logits=logits,
            channel_tags=episode.channel_tags,
        )

    def test_future_tokens_cannot_move_an_earlier_window(self) -> None:
        for name in ("in_set_residual_mass", "prob_js", "prob_rare_mass"):
            with self.subTest(statistic=name):
                statistic = trm3_g.build_statistic(name).fit(self.pool, self.view)
                episode = self.pool[0]
                cut = 30
                base_ends, base_scores, _, _ = statistic.stream(episode, self.view)
                other_ends, other_scores, _, _ = statistic.stream(
                    self._mutated(episode, cut), self.view
                )
                self.assertTrue(np.array_equal(base_ends, other_ends))
                keep = base_ends < cut
                np.testing.assert_allclose(
                    base_scores[keep], other_scores[keep], rtol=0.0, atol=0.0
                )
                self.assertTrue(
                    bool(np.any(base_scores[~keep] != other_scores[~keep])),
                    "the mutation must move at least the windows that contain it",
                )

    def test_tokens_before_the_window_cannot_move_it(self) -> None:
        for name in ("in_set_residual_mass", "prob_js", "prob_rare_mass"):
            with self.subTest(statistic=name):
                statistic = trm3_g.build_statistic(name).fit(self.pool, self.view)
                episode = self.pool[0]
                width = statistic.window_width
                cut = 20
                base_ends, base_scores, _, _ = statistic.stream(episode, self.view)
                other_ends, other_scores, _, _ = statistic.stream(
                    self._mutated(episode, cut), self.view
                )
                keep = base_ends >= cut + width
                self.assertTrue(bool(keep.any()))
                # exact in real arithmetic; ``window_means`` uses a float32 cumulative
                # sum, so the shared prefix cancels only to float32 precision
                np.testing.assert_allclose(
                    base_scores[keep], other_scores[keep], rtol=1e-5, atol=1e-7
                )

    def test_windows_never_straddle_a_channel_boundary(self) -> None:
        generator = torch.Generator().manual_seed(5)
        tags = tuple(["analysis"] * 20 + ["commentary"] * 6 + ["final"] * 34)
        episode = synthetic_episode("mixed", 60, generator=generator, tags=tags)
        pool = synthetic_pool(6) + [episode]
        view = trm3_g.VIEWS["V1"]
        for name in ("in_set_residual_mass", "prob_js", "prob_rare_mass"):
            with self.subTest(statistic=name):
                statistic = trm3_g.build_statistic(name).fit(pool, view)
                ends, _, stream_tags, _ = statistic.stream(episode, view)
                width = statistic.window_width
                for end, tag in zip(ends.tolist(), stream_tags):
                    window = tags[end - width + 1 : end + 1]
                    self.assertEqual(set(window), {tag})
                # commentary is 6 tokens < w = 8, so it can produce no endpoint
                self.assertNotIn("commentary", set(stream_tags))


# ---------------------------------------------------------------------------
# exact small cases
# ---------------------------------------------------------------------------


class JensenShannonTest(unittest.TestCase):
    def test_identical_rows_are_zero(self) -> None:
        p = torch.tensor([[0.1, 0.2, 0.7]], dtype=torch.float64)
        self.assertAlmostEqual(float(trm3_g.jensen_shannon_rows(p, p)), 0.0, places=15)

    def test_disjoint_point_masses_are_log_two(self) -> None:
        p = torch.tensor([[1.0, 0.0, 0.0]], dtype=torch.float64)
        q = torch.tensor([[0.0, 1.0, 0.0]], dtype=torch.float64)
        self.assertAlmostEqual(float(trm3_g.jensen_shannon_rows(p, q)), math.log(2.0), places=12)

    def test_hand_computed_two_point_case(self) -> None:
        p = torch.tensor([[1.0, 0.0]], dtype=torch.float64)
        q = torch.tensor([[0.5, 0.5]], dtype=torch.float64)
        # m = (0.75, 0.25); JS = 0.5 * KL(p||m) + 0.5 * KL(q||m)
        expected = 0.5 * math.log(1 / 0.75) + 0.5 * (
            0.5 * math.log(0.5 / 0.75) + 0.5 * math.log(0.5 / 0.25)
        )
        self.assertAlmostEqual(float(trm3_g.jensen_shannon_rows(p, q)), expected, places=12)

    def test_symmetric(self) -> None:
        generator = torch.Generator().manual_seed(3)
        p = torch.rand((5, 7), generator=generator, dtype=torch.float64)
        q = torch.rand((5, 7), generator=generator, dtype=torch.float64)
        p = p / p.sum(-1, keepdim=True)
        q = q / q.sum(-1, keepdim=True)
        np.testing.assert_allclose(
            trm3_g.jensen_shannon_rows(p, q).numpy(),
            trm3_g.jensen_shannon_rows(q, p).numpy(),
            rtol=0,
            atol=1e-15,
        )


class ProbJSExactTest(unittest.TestCase):
    def test_window_score_equals_the_definition(self) -> None:
        pool = synthetic_pool(5)
        view = trm3_g.VIEWS["V3"]
        statistic = trm3_g.build_statistic("prob_js").fit(pool, view)
        episode = pool[0]
        probs = episode.probabilities().to(torch.float64)
        ends, scores, _, _ = statistic.stream(episode, view)
        width = statistic.window_width
        for end, score in list(zip(ends.tolist(), scores.tolist()))[:6]:
            window = probs[:, end - width + 1 : end + 1, :].mean(dim=1)  # [L, E]
            expected = float(
                trm3_g.jensen_shannon_rows(window, statistic.routine_mean).sum()
            )
            self.assertAlmostEqual(score, expected, places=9)

    def test_fit_reference_is_the_token_weighted_mean(self) -> None:
        pool = synthetic_pool(4)
        view = trm3_g.VIEWS["V3"]  # keeps only the `final` tokens
        statistic = trm3_g.build_statistic("prob_js").fit(pool, view)
        total = torch.zeros((LAYERS, EXPERTS), dtype=torch.float64)
        tokens = 0
        for episode in pool:
            mask = torch.tensor([tag == "final" for tag in episode.channel_tags])
            probs = episode.probabilities().to(torch.float64)[:, mask, :]
            total += probs.sum(dim=1)
            tokens += int(mask.sum())
        np.testing.assert_allclose(
            statistic.routine_mean.numpy(), (total / tokens).numpy(), rtol=0, atol=1e-15
        )
        self.assertEqual(statistic.n_tokens, tokens)
        # the softmax is taken in float32, so a row sums to 1 only to float32 precision;
        # the observed deviation is recorded on the statistic as provenance
        np.testing.assert_allclose(
            statistic.routine_mean.sum(1).numpy(), np.ones(LAYERS), rtol=0, atol=1e-6
        )
        self.assertLess(statistic.simplex_max_deviation, 1e-6)


class ProbRareMassExactTest(unittest.TestCase):
    def test_score_is_the_probability_sum_over_the_rare_set(self) -> None:
        pool = synthetic_pool(5)
        view = trm3_g.VIEWS["V3"]
        statistic = trm3_g.build_statistic("prob_rare_mass").fit(pool, view)
        episode = pool[0]
        probs = episode.probabilities().to(torch.float64)
        mask = statistic.rare_mask
        per_token = (probs * mask.to(probs.dtype)[:, None, :]).sum(dim=(0, 2))
        ends, scores, _, _ = statistic.stream(episode, view)
        width = statistic.window_width
        for end, score in list(zip(ends.tolist(), scores.tolist()))[:6]:
            expected = float(per_token[end - width + 1 : end + 1].mean())
            self.assertAlmostEqual(score, expected, places=12)

    def test_rare_set_is_channel_s_rare_set(self) -> None:
        pool = synthetic_pool(5)
        view = trm3_g.VIEWS["V1"]
        rare_mass = trm3_g.build_statistic("prob_rare_mass").fit(pool, view)
        channel_s = trm3_g.build_statistic("S").fit(pool, view)
        np.testing.assert_allclose(rare_mass.q.numpy(), channel_s.q.numpy(), rtol=0, atol=0)
        self.assertTrue(
            torch.equal(rare_mass.rare_mask, channel_s.q < channel_s.rare_threshold)
        )

    def test_empty_rare_set_gives_an_identically_zero_channel(self) -> None:
        pool = synthetic_pool(4)
        view = trm3_g.VIEWS["V3"]
        statistic = trm3_g.build_statistic(
            "prob_rare_mass", {"rare_threshold": 0.0}
        ).fit(pool, view)
        self.assertEqual(int(statistic.rare_mask.sum()), 0)
        _, scores, _, _ = statistic.stream(pool[0], view)
        np.testing.assert_allclose(scores, np.zeros_like(scores), rtol=0, atol=0)


class InSetResidualExactTest(unittest.TestCase):
    def test_streaming_normal_equations_equal_a_direct_least_squares(self) -> None:
        pool = synthetic_pool(6)
        view = trm3_g.VIEWS["V1"]
        statistic = trm3_g.build_statistic("in_set_residual_mass").fit(pool, view)
        blocks = [
            trm3_g.segmented_windows(
                statistic.per_token(episode), episode.channel_tags, view, 8
            )[1]
            for episode in pool
        ]
        matrix = torch.cat([block for block in blocks if block.shape[0]]).to(torch.float64)
        mass = matrix[:, :LAYERS]
        indicator = matrix[:, LAYERS:].reshape(-1, LAYERS, EXPERTS)
        # the design is EXACTLY rank deficient (every window has sum_e Sbar[l, e] = top_k),
        # so the comparison is against a rank-revealing SVD driver, and the invariant is the
        # fitted value / residual sum of squares, not the coefficient vector
        for layer in range(LAYERS):
            design = indicator[:, layer, :]
            target = mass[:, layer]
            reference = torch.linalg.lstsq(
                design, target[:, None], driver="gelsd"
            ).solution[:, 0]
            self.assertLess(int(torch.linalg.matrix_rank(design)), EXPERTS + 1)
            np.testing.assert_allclose(
                (design @ statistic.beta[layer]).numpy(),
                (design @ reference).numpy(),
                rtol=1e-8,
                atol=1e-10,
            )
            self.assertAlmostEqual(
                float(((target - design @ statistic.beta[layer]) ** 2).sum()),
                float(((target - design @ reference) ** 2).sum()),
                places=12,
            )
        # and the residual moments are the moments of those residuals
        residual = statistic._residual(matrix)
        np.testing.assert_allclose(
            statistic.mu_r.numpy(), residual.mean(0).numpy(), rtol=1e-8, atol=1e-12
        )
        np.testing.assert_allclose(
            (statistic.sd_r - statistic.variance_floor).numpy(),
            residual.std(0, unbiased=False).numpy(),
            rtol=1e-6,
            atol=1e-12,
        )
        self.assertEqual(statistic.window_count, int(matrix.shape[0]))

    def test_an_exactly_linear_in_set_mass_leaves_no_residual(self) -> None:
        """If ``m`` IS a linear function of the indicator, the residual carries nothing.

        The exact small case for the least squares: build the in-set mass as
        ``m[t, l] = sum_e c[e] * s[t, l, e]`` with known coefficients and check that the
        fitted per-layer read-out reproduces it (R2 = 1, residual sd at the float32 noise
        floor of the feature tensor).
        """

        pool = synthetic_pool(6)
        view = trm3_g.VIEWS["V1"]
        statistic = trm3_g.build_statistic("in_set_residual_mass")
        coefficients = torch.tensor(
            [0.01 * (index % 7) for index in range(EXPERTS)], dtype=torch.float64
        )[None, :].repeat(LAYERS, 1)

        def linear_features(episode: FakeEpisode) -> torch.Tensor:
            indicator = selection_counts_per_token(episode.top_k_ids, range(LAYERS), EXPERTS)
            grid = indicator.reshape(-1, LAYERS, EXPERTS).to(torch.float64)
            mass = (grid * coefficients[None, :, :]).sum(-1)
            return torch.cat((mass.to(indicator.dtype), indicator), dim=1).contiguous()

        original = type(statistic).per_token
        try:
            type(statistic).per_token = staticmethod(linear_features)  # type: ignore[assignment]
            statistic.fit(pool, view)
            _, means, _, _ = trm3_g.segmented_windows(
                linear_features(pool[0]), pool[0].channel_tags, view, 8
            )
            residual = statistic._residual(means)
        finally:
            type(statistic).per_token = original  # type: ignore[assignment]
        self.assertLess(float(residual.abs().max()), 1e-6)
        self.assertGreater(min(statistic.r2), 1.0 - 1e-6)

    def test_window_mean_of_the_per_token_residual_equals_the_residual_of_the_means(self) -> None:
        """The linearity that licenses fitting on window means rather than on tokens."""

        pool = synthetic_pool(5)
        view = trm3_g.VIEWS["V3"]
        statistic = trm3_g.build_statistic("in_set_residual_mass").fit(pool, view)
        episode = pool[0]
        features = statistic.per_token(episode).to(torch.float64)
        mass = features[:, :LAYERS]
        indicator = features[:, LAYERS:].reshape(-1, LAYERS, EXPERTS)
        per_token_residual = mass - (indicator * statistic.beta[None, :, :]).sum(-1)
        ends, means, _, _ = trm3_g.segmented_windows(
            statistic.per_token(episode), episode.channel_tags, view, 8
        )
        window_residual = statistic._residual(means)
        for row, end in enumerate(ends.tolist()):
            np.testing.assert_allclose(
                window_residual[row].numpy(),
                per_token_residual[end - 7 : end + 1].mean(0).numpy(),
                rtol=1e-4,
                atol=1e-7,
            )

    def test_score_is_the_summed_standardized_residual(self) -> None:
        pool = synthetic_pool(5)
        view = trm3_g.VIEWS["V3"]
        statistic = trm3_g.build_statistic("in_set_residual_mass").fit(pool, view)
        episode = pool[0]
        _, means, _, _ = trm3_g.segmented_windows(
            statistic.per_token(episode), episode.channel_tags, view, 8
        )
        residual = statistic._residual(means)
        expected = ((residual - statistic.mu_r) / statistic.sd_r).sum(1)
        _, scores, _, _ = statistic.stream(episode, view)
        np.testing.assert_allclose(scores, expected.numpy(), rtol=0, atol=1e-12)

    def test_the_fit_is_position_free(self) -> None:
        """Reversing the token order of an episode permutes the residuals, nothing else."""

        pool = synthetic_pool(5)
        view = trm3_g.VIEWS["V3"]
        statistic = trm3_g.build_statistic("in_set_residual_mass").fit(pool, view)
        episode = pool[0]
        features = statistic.per_token(episode).to(torch.float64)
        forward = statistic._residual(features)  # per-token residuals (w = 1 view)
        reversed_episode = FakeEpisode(
            trace_id="rev",
            top_k_ids=episode.top_k_ids.flip(1),
            logits=episode.logits.flip(1),
            channel_tags=episode.channel_tags,
        )
        backward = statistic._residual(statistic.per_token(reversed_episode).to(torch.float64))
        np.testing.assert_allclose(
            forward.numpy(), backward.flip(0).numpy(), rtol=1e-6, atol=1e-9
        )


# ---------------------------------------------------------------------------
# the harness path: standardisation, sparse fallback, calibration
# ---------------------------------------------------------------------------


class HarnessPathTest(unittest.TestCase):
    """The weight-aware families go through exactly the S / M / B calibration path."""

    def setUp(self) -> None:
        generator = torch.Generator().manual_seed(77)
        tags_dense = tuple(["analysis"] * 20 + ["final"] * 40)
        tags_thin = tuple(["analysis"] * 20 + ["commentary"] * 10 + ["final"] * 30)
        self.fit_pool = [
            synthetic_episode(f"fit-{i}", 60, generator=generator, tags=tags_dense)
            for i in range(10)
        ] + [synthetic_episode("fit-thin", 60, generator=generator, tags=tags_thin)]
        self.cal_pool = [
            synthetic_episode(f"cal-{i}", 60, generator=generator, tags=tags_thin)
            for i in range(12)
        ]
        self.view = trm3_g.VIEWS["V1"]

    def _calibrate(self, name: str) -> trm3_g.GCalibration:
        key = trm3_g.STATISTIC_ALIASES[name]
        statistic = trm3_g.build_statistic(name).fit(self.fit_pool, self.view)
        config = trm3_g.config_for_g([key], alpha=0.10)
        fit_streams = trm3_g.episode_streams({key: statistic}, self.fit_pool, self.view)[key]
        cal_streams = trm3_g.episode_streams({key: statistic}, self.cal_pool, self.view)[key]
        return trm3_g.calibrate_g(
            fit_streams,
            cal_streams,
            config,
            view=self.view,
            statistic=key,
            min_survivors=4,
            min_bucket_traces=3,
        )

    def test_calibration_runs_and_records_the_sparse_fallback(self) -> None:
        for name in ("in_set_residual_mass", "prob_js", "prob_rare_mass"):
            with self.subTest(statistic=name):
                calibration = self._calibrate(name)
                block = calibration.to_json()
                self.assertGreater(block["horizon"]["H"], 0)
                self.assertEqual(block["calibration_mode"], "whole_pool_no_halves")
                fallback = block["standardiser"]["sparse_fallback"]
                self.assertTrue(fallback["enabled"])
                # commentary appears in one fit episode only -> below min_channel_traces
                self.assertIn("commentary", fallback["channels"])
                self.assertGreater(fallback["applied_windows"].get("commentary", 0), 0)

    def test_scores_are_finite_and_the_decision_stream_is_monotone(self) -> None:
        for name in ("in_set_residual_mass", "prob_js", "prob_rare_mass"):
            with self.subTest(statistic=name):
                key = trm3_g.STATISTIC_ALIASES[name]
                calibration = self._calibrate(name)
                statistic = trm3_g.build_statistic(name).fit(self.fit_pool, self.view)
                config = trm3_g.config_for_g([key], alpha=0.10)
                streams = trm3_g.episode_streams({key: statistic}, self.cal_pool, self.view)[key]
                outputs = trm3_g.score_episode({key: streams[0]}, calibration, config)
                self.assertTrue(outputs)
                values = [float(output.p_fused) for output in outputs]
                self.assertTrue(all(math.isfinite(v) for v in values))
                self.assertTrue(all(b <= a + 1e-12 for a, b in zip(values, values[1:])))

    def test_alpha_is_attainable_on_the_reference_size(self) -> None:
        calibration = self._calibrate("prob_js")
        config = trm3_g.config_for_g(["J"], alpha=0.10)
        block = trm3.effective_alpha(config, calibration.n_reference)
        self.assertLessEqual(block["alpha_eff"], 0.10 + 1e-12)
        self.assertGreater(block["alpha_eff"], 0.0)


# ---------------------------------------------------------------------------
# the data contract on a real trace shard
# ---------------------------------------------------------------------------


REAL_TRACE = (
    ROOT / "artifacts" / "agent_v2" / "dataset_g" / "g_fit" / "g-fit-001" / "clean"
)


@unittest.skipUnless(REAL_TRACE.exists(), "dataset G fit traces are not present")
class DataContractTest(unittest.TestCase):
    """What the gpt-oss shards actually store, asserted rather than assumed."""

    def test_shards_carry_full_logits_over_every_expert(self) -> None:
        from safetensors import safe_open

        shard = sorted((REAL_TRACE / "steps").glob("*_decode.safetensors"))[0]
        with safe_open(shard, "pt") as handle:
            keys = set(handle.keys())
            self.assertIn("router_logits", keys)
            logits = handle.get_slice("router_logits")
            self.assertEqual(logits.get_dtype(), "BF16")
            shape = logits.get_shape()
            self.assertEqual(shape[0], LAYERS)
            self.assertEqual(shape[2], EXPERTS)
            self.assertEqual(handle.get_slice("top_k_ids").get_shape()[2], TOP_K)

    def test_episode_logits_match_the_top_k_axis(self) -> None:
        episodes = io_g.load_g(
            REAL_TRACE.parent.parent,
            scenarios=[REAL_TRACE.parent.name],
            variants=[io_g.CLEAN],
            cache_dir=None,
        )
        self.assertTrue(episodes)
        episode = episodes[0]
        logits = episode.router_logits(cache_dir=None)
        self.assertEqual(
            tuple(logits.shape), (LAYERS, episode.token_count, EXPERTS)
        )
        self.assertIs(logits.dtype, torch.bfloat16)
        probs = episode.probabilities(cache_dir=None)
        self.assertLess(float((probs.sum(-1) - 1.0).abs().max()), 1e-5)

    def test_stored_top_k_is_a_valid_top_k_of_the_stored_logits(self) -> None:
        """bfloat16 differences are exact ties: the in-set MASS is identical."""

        episodes = io_g.load_g(
            REAL_TRACE.parent.parent,
            scenarios=[REAL_TRACE.parent.name],
            variants=[io_g.CLEAN],
            cache_dir=None,
        )
        block = io_g.load_episode_probabilities(episodes, cache_dir=None)
        self.assertEqual(block["episodes"], len(episodes))
        self.assertLess(block["simplex_max_deviation"], 1e-5)
        self.assertEqual(block["tie_in_set_mass_max_abs_difference"], 0.0)
        self.assertGreater(block["in_set_mass_mean"], 0.0)
        self.assertLess(block["in_set_mass_mean"], 1.0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
