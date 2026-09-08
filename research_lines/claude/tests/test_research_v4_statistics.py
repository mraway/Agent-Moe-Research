"""The G statistic families: geometry generalisation and equivalence with the frozen scorers.

The G harness re-implements S (rare-coordinate surprisal), M (WGM ``g1`` = frozen CAND-A),
P (marginal surprisal) and B (CAND-B depth chain) without the 16 / 64 / 8 constants of the
OLMoE scorers.  The equivalence tests pin that re-implementation to the frozen one: on the
OLMoE geometry, with a single-channel view (so the segmented windows degenerate to the
frozen global windows), the streams must agree exactly.  Everything else here checks the
parts that only exist on G: the 24 / 32 / 4 geometry and the channel-boundary rule.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import trm3_g  # noqa: E402
from research_v2.features import router_geometry, selection_rate_windows  # noqa: E402
from research_v2.scorers.pdm import PdmScorer  # noqa: E402
from research_v2.scorers.surprisal_marginal import SurprisalMarginalScorer  # noqa: E402
from research_v2.scorers.trm3_s import TRM3SScorer  # noqa: E402
from research_v2.scorers.wgm import WGMScorer  # noqa: E402

torch.set_num_threads(4)


@dataclass
class FakeTrace:
    """Minimal duck type: what both the frozen scorers and the G statistics read."""

    trace_id: str
    top_k_ids: torch.Tensor
    channel_tags: tuple[str, ...]
    pair_group_id: str = "group"
    workflow: str = "w"
    batch: str = "syn"
    router: dict | None = None
    labels: dict = field(default_factory=dict)


def skewed_ids(
    tokens: int, *, layers: int, experts: int, top_k: int, generator: torch.Generator
) -> torch.Tensor:
    """Top-k selections drawn from a skewed per-layer distribution.

    A uniform draw would make every coordinate equally common and channel S identically
    zero, so the equivalence test would be vacuous.  The weights decay geometrically,
    which leaves a genuine rare tail below the frozen 0.02 threshold.
    """

    weights = torch.tensor([0.75**index for index in range(experts)])
    rows = []
    for _ in range(layers):
        per_layer = []
        for _ in range(tokens):
            per_layer.append(torch.multinomial(weights, top_k, replacement=False, generator=generator))
        rows.append(torch.stack(per_layer))
    return torch.stack(rows)


def olmoe_traces(count: int = 6, tokens: int = 48) -> list[FakeTrace]:
    generator = torch.Generator().manual_seed(20260907)
    return [
        FakeTrace(
            trace_id=f"olmoe-{index}",
            top_k_ids=skewed_ids(tokens, layers=16, experts=64, top_k=8, generator=generator),
            channel_tags=tuple(["final"] * tokens),
        )
        for index in range(count)
    ]


def gpt_oss_traces(count: int = 6, tokens: int = 48) -> list[FakeTrace]:
    generator = torch.Generator().manual_seed(41041)
    router = {"num_moe_layers": 24, "num_experts": 32, "top_k": 4}
    return [
        FakeTrace(
            trace_id=f"gptoss-{index}",
            top_k_ids=skewed_ids(tokens, layers=24, experts=32, top_k=4, generator=generator),
            channel_tags=tuple(["analysis"] * 12 + ["commentary"] * 12 + ["final"] * (tokens - 24)),
            router=dict(router),
        )
        for index in range(count)
    ]


class FrozenEquivalenceTest(unittest.TestCase):
    """On 16 / 64 / 8 with one channel, the G statistics ARE the frozen scorers."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.traces = olmoe_traces()
        cls.view = trm3_g.VIEWS["V3"]  # a single tag covering every token

    def _compare(self, statistic: trm3_g.GStatistic, frozen, frozen_state) -> None:
        for trace in self.traces:
            ends, scores, _, _ = statistic.stream(trace, self.view)
            frozen_scores, frozen_ends = frozen.score(frozen_state, trace)
            self.assertTrue(np.array_equal(ends, frozen_ends.numpy()))
            self.assertEqual(len(scores), len(frozen_scores))
            np.testing.assert_allclose(
                scores, frozen_scores.numpy().astype(np.float64), rtol=0, atol=0
            )

    def test_channel_s_matches_trm3_s(self) -> None:
        statistic = trm3_g.build_statistic("S").fit(self.traces, self.view)
        frozen = TRM3SScorer()
        state = frozen.fit(self.traces)
        self.assertGreater(int(state.rare_mask.sum()), 0, "the fixture must have a rare tail")
        self._compare(statistic, frozen, state)

    def test_marginal_surprisal_matches_the_frozen_baseline(self) -> None:
        statistic = trm3_g.build_statistic("P").fit(self.traces, self.view)
        frozen = SurprisalMarginalScorer()
        self._compare(statistic, frozen, frozen.fit(self.traces))

    def test_wgm_g1_matches_frozen_cand_a(self) -> None:
        statistic = trm3_g.build_statistic("M", {"layers": tuple(range(5, 16))}).fit(
            self.traces, self.view
        )
        frozen = WGMScorer(window_width=8, layers="middle_late", metric="g1")
        self._compare(statistic, frozen, frozen.fit(self.traces))

    def test_depth_chain_matches_frozen_cand_b(self) -> None:
        statistic = trm3_g.build_statistic(
            "B", {"layers": tuple(range(5, 12)), "window_width": 4}
        ).fit(self.traces, self.view)
        frozen = PdmScorer(window_width=4, model="d1", layers=tuple(range(5, 12)))
        self._compare(statistic, frozen, frozen.fit(self.traces))


class GeometryTest(unittest.TestCase):
    def test_router_geometry_defaults_to_olmoe(self) -> None:
        trace = olmoe_traces(1)[0]
        self.assertEqual(
            router_geometry(trace),
            {"num_moe_layers": 16, "num_experts": 64, "top_k": 8},
        )

    def test_router_geometry_reads_trace_metadata(self) -> None:
        trace = gpt_oss_traces(1)[0]
        self.assertEqual(
            router_geometry(trace),
            {"num_moe_layers": 24, "num_experts": 32, "top_k": 4},
        )

    def test_router_geometry_rejects_a_mismatch(self) -> None:
        trace = gpt_oss_traces(1)[0]
        trace.router = {"num_moe_layers": 16, "num_experts": 32, "top_k": 4}
        with self.assertRaises(ValueError):
            router_geometry(trace)

    def test_selection_rate_windows_on_32_experts(self) -> None:
        trace = gpt_oss_traces(1)[0]
        ends, windows = selection_rate_windows(
            trace.top_k_ids, 8, tuple(range(24)), num_experts=32
        )
        self.assertEqual(windows.shape[1], 24 * 32)
        # every window is a selection rate: each layer sums to top_k
        np.testing.assert_allclose(
            windows.reshape(windows.shape[0], 24, 32).sum(2).numpy(),
            np.full((windows.shape[0], 24), 4.0),
            atol=1e-5,
        )

    def test_every_statistic_fits_the_gpt_oss_geometry(self) -> None:
        traces = gpt_oss_traces()
        view = trm3_g.VIEWS["V1"]
        for name in ("S", "M", "P", "B"):
            statistic = trm3_g.build_statistic(name).fit(traces, view)
            ends, scores, tags, ordinals = statistic.stream(traces[0], view)
            self.assertEqual(len(ends), len(scores))
            self.assertEqual(len(ends), len(tags))
            self.assertEqual(len(ends), len(ordinals))
            self.assertTrue(np.all(np.diff(ends) > 0))
            self.assertEqual(
                statistic.describe()["layers"], list(range(24)), "default = all MoE layers"
            )

    def test_rare_threshold_is_exposed(self) -> None:
        traces = gpt_oss_traces()
        loose = trm3_g.build_statistic("S", {"rare_threshold": 0.5}).fit(
            traces, trm3_g.VIEWS["V1"]
        )
        tight = trm3_g.build_statistic("S", {"rare_threshold": 0.0}).fit(
            traces, trm3_g.VIEWS["V1"]
        )
        self.assertGreater(loose.describe()["rare_coordinates"], tight.describe()["rare_coordinates"])
        self.assertEqual(tight.describe()["rare_coordinates"], 0)


class ChannelBoundaryTest(unittest.TestCase):
    def test_windows_never_straddle_a_channel_switch(self) -> None:
        tags = tuple(["analysis"] * 10 + ["commentary"] * 10 + ["final"] * 10)
        features = torch.arange(30, dtype=torch.float64)[:, None]
        ends, means, out_tags, ordinals = trm3_g.segmented_windows(
            features, tags, trm3_g.VIEWS["V1"], 8
        )
        # a window ending at 7 is inside analysis; the first commentary endpoint is 17
        self.assertEqual(list(ends[:5]), [7, 8, 9, 17, 18])
        self.assertEqual(out_tags[:5], ["analysis", "analysis", "analysis", "commentary", "commentary"])
        # ordinals restart per channel
        self.assertEqual(list(ordinals[:5]), [0, 1, 2, 0, 1])
        # the window ending at 17 averages tokens 10..17 only (no analysis token)
        self.assertAlmostEqual(float(means[3, 0]), float(np.mean(np.arange(10, 18))))

    def test_short_runs_produce_no_endpoint(self) -> None:
        tags = tuple(["analysis"] * 5 + ["final"] * 20)
        features = torch.ones((25, 1), dtype=torch.float64)
        ends, _, out_tags, _ = trm3_g.segmented_windows(features, tags, trm3_g.VIEWS["V1"], 8)
        self.assertTrue(all(tag == "final" for tag in out_tags))
        self.assertEqual(int(ends.min()), 12)

    def test_views_restrict_the_retained_channels(self) -> None:
        tags = tuple(["analysis"] * 12 + ["commentary"] * 12 + ["final"] * 12)
        features = torch.ones((36, 1), dtype=torch.float64)
        counts = {}
        for name in ("V1", "V2", "V3"):
            ends, _, out_tags, _ = trm3_g.segmented_windows(
                features, tags, trm3_g.VIEWS[name], 8
            )
            counts[name] = sorted(set(out_tags))
        self.assertEqual(counts["V1"], ["analysis", "commentary", "final"])
        self.assertEqual(counts["V2"], ["analysis", "final"])
        self.assertEqual(counts["V3"], ["final"])

    def test_dropping_commentary_does_not_change_the_other_windows(self) -> None:
        traces = gpt_oss_traces(2)
        statistic = trm3_g.build_statistic("S").fit(traces, trm3_g.VIEWS["V1"])
        ends1, scores1, tags1, _ = statistic.stream(traces[0], trm3_g.VIEWS["V1"])
        ends2, scores2, tags2, _ = statistic.stream(traces[0], trm3_g.VIEWS["V2"])
        keep = [index for index, tag in enumerate(tags1) if tag != "commentary"]
        np.testing.assert_array_equal(ends1[keep], ends2)
        np.testing.assert_allclose(scores1[keep], scores2, rtol=0, atol=0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
