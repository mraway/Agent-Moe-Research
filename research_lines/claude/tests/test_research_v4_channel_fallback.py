"""The deterministic sparse-channel fallback of the dataset-G standardisation.

Prereg note 3 of 2026-09-07 (``docs/research_v4/detector_prereg_notes.md``): when a
harmony channel is too thin in the FITTING pool -- G-bridge's ``commentary`` rested on
3 episodes / 12 windows -- its position buckets must not stand on that handful of windows,
and a channel that is absent from the fitting pool but present in a calibration or target
episode must not raise (``gbridge_harness_smoke.md`` open issue 3: with another split seed
``ChannelStandardiser.standardize`` raised ``KeyError``).  Both cases fall back to the
pooled all-channel buckets of the same fitting pool, and the fallback is recorded.

Everything here is synthetic except the two CLI cases, which reuse the P0 probe batch
(routine arms only) to pin ``tag_scope`` as a first-class option in ``result.json``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io_g, trm3, trm3_g  # noqa: E402

from test_research_v4_detectors_g import make_episode, routine_pool  # noqa: E402

torch.set_num_threads(4)


def streams_of(
    episodes, view: trm3_g.View, statistic: str = "M", fit_on=None
) -> tuple[trm3_g.GStatistic, list[trm3_g.EpisodeStream]]:
    stat = trm3_g.build_statistic(statistic).fit(list(fit_on or episodes), view)
    return stat, trm3_g.episode_streams({statistic: stat}, list(episodes), view)[statistic]


def sparse_fit_pool(commentary_traces: int = 3, count: int = 12, seed: int = 7):
    """A fitting pool where only ``commentary_traces`` episodes carry a commentary run.

    Mirrors the G-bridge shape: analysis and final are everywhere, commentary is rare.
    """

    generator = torch.Generator().manual_seed(seed)
    pool = []
    for index in range(count):
        commentary = 12 if index < commentary_traces else 0
        pool.append(
            make_episode(
                f"f{index}",
                generator=generator,
                tokens=90,
                analysis=24,
                commentary=commentary,
            )
        )
    return pool


class SparseChannelFallbackTest(unittest.TestCase):
    """A rare channel borrows the pooled buckets instead of its own 12 windows."""

    def setUp(self) -> None:
        self.view = trm3_g.VIEWS["V1"]
        self.fit = sparse_fit_pool()
        _, self.fit_streams = streams_of(self.fit, self.view)

    def test_rare_channel_is_dropped_and_recorded(self) -> None:
        standardiser = trm3_g.fit_channel_standardiser(
            self.fit_streams, min_bucket_traces=3, pooled_fallback=True
        )
        self.assertEqual(standardiser.fallback_channels, ("commentary",))
        self.assertEqual(sorted(standardiser.stats), ["analysis", "final"])
        self.assertIsNotNone(standardiser.pooled)
        block = standardiser.sparse_fallback_json()
        self.assertTrue(block["enabled"])
        self.assertEqual(block["min_channel_windows"], trm3_g.MIN_CHANNEL_WINDOWS)
        self.assertEqual(block["min_channel_traces"], trm3_g.MIN_CHANNEL_TRACES)
        self.assertEqual(block["channels"], ["commentary"])
        # counts of the rejected channel are kept: 3 episodes, 3 * (12 - 8 + 1) windows
        self.assertEqual(block["fit_support"]["commentary"]["traces"], 3)
        self.assertEqual(block["fit_support"]["commentary"]["windows"], 15)
        self.assertEqual(block["fit_support"]["commentary"]["fallback"], 1)
        self.assertEqual(block["fit_support"]["analysis"]["fallback"], 0)
        self.assertGreaterEqual(block["fit_support"]["analysis"]["traces"], 10)

    def test_a_thin_channel_alone_is_enough_to_trigger_the_fallback(self) -> None:
        # 10 commentary episodes (>= min_channel_traces) but only 30 windows short of the
        # window floor: either counter firing is enough.
        pool = sparse_fit_pool(commentary_traces=10, count=12, seed=11)
        _, streams = streams_of(pool, self.view)
        loose = trm3_g.fit_channel_standardiser(
            streams, min_bucket_traces=3, pooled_fallback=True, min_channel_windows=1
        )
        self.assertEqual(loose.fallback_channels, ())
        tight = trm3_g.fit_channel_standardiser(
            streams, min_bucket_traces=3, pooled_fallback=True, min_channel_windows=10_000
        )
        self.assertEqual(sorted(tight.fallback_channels), ["analysis", "commentary", "final"])
        self.assertEqual(tight.stats, {})

    def test_dense_channels_are_unchanged_bit_for_bit(self) -> None:
        strict = trm3_g.fit_channel_standardiser(self.fit_streams, min_bucket_traces=3)
        relaxed = trm3_g.fit_channel_standardiser(
            self.fit_streams, min_bucket_traces=3, pooled_fallback=True
        )
        for tag in ("analysis", "final"):
            np.testing.assert_array_equal(strict.stats[tag].mu, relaxed.stats[tag].mu)
            np.testing.assert_array_equal(strict.stats[tag].sd, relaxed.stats[tag].sd)
            self.assertEqual(strict.stats[tag].cap, relaxed.stats[tag].cap)
            self.assertEqual(strict.stats[tag].trace_counts, relaxed.stats[tag].trace_counts)
            self.assertEqual(strict.stats[tag].window_counts, relaxed.stats[tag].window_counts)
        # and the z of every dense window is identical, bit for bit
        for stream in self.fit_streams:
            z_strict = strict.standardize(stream)
            z_relaxed = relaxed.standardize(stream)
            dense = np.array([tag != "commentary" for tag in stream.tags], dtype=bool)
            np.testing.assert_array_equal(z_strict[dense], z_relaxed[dense])

    def test_the_fallback_uses_the_pooled_episode_wide_buckets(self) -> None:
        standardiser = trm3_g.fit_channel_standardiser(
            self.fit_streams, min_bucket_traces=3, pooled_fallback=True
        )
        stream = next(s for s in self.fit_streams if "commentary" in s.tags)
        z = standardiser.standardize(stream)
        mask = np.array([tag == "commentary" for tag in stream.tags], dtype=bool)
        ordinals = np.arange(int(stream.scores.size), dtype=np.int64)[mask]
        expected = standardiser.pooled.standardize(stream.scores[mask], ordinals)
        np.testing.assert_array_equal(z[mask], expected)
        # the pooled buckets are NOT the commentary buckets: they are the all-channel ones
        pooled_direct = trm3.fit_bucket_stats_k(
            [s.scores for s in self.fit_streams if s.scores.size], min_bucket_traces=3
        )
        np.testing.assert_array_equal(standardiser.pooled.mu, pooled_direct.mu)

    def test_determinism(self) -> None:
        first = trm3_g.fit_channel_standardiser(
            self.fit_streams, min_bucket_traces=3, pooled_fallback=True
        )
        second = trm3_g.fit_channel_standardiser(
            list(reversed(self.fit_streams)), min_bucket_traces=3, pooled_fallback=True
        )
        self.assertEqual(first.fallback_channels, second.fallback_channels)
        np.testing.assert_array_equal(first.pooled.mu, second.pooled.mu)
        np.testing.assert_array_equal(first.pooled.sd, second.pooled.sd)


class AbsentChannelTest(unittest.TestCase):
    """The G-bridge open issue: the channel is missing from fit and present in target."""

    def setUp(self) -> None:
        self.view = trm3_g.VIEWS["V1"]
        generator = torch.Generator().manual_seed(23)
        # no commentary anywhere in the fitting pool
        self.fit = [
            make_episode(f"f{index}", generator=generator, tokens=90, analysis=24, commentary=0)
            for index in range(12)
        ]
        self.stat, self.fit_streams = streams_of(self.fit, self.view)
        self.target = make_episode(
            "t", generator=generator, tokens=90, analysis=24, commentary=12
        )
        self.target_stream = trm3_g.episode_streams(
            {"M": self.stat}, [self.target], self.view
        )["M"][0]

    def test_strict_mode_still_raises(self) -> None:
        strict = trm3_g.fit_channel_standardiser(self.fit_streams, min_bucket_traces=3)
        with self.assertRaises(KeyError):
            strict.standardize(self.target_stream)

    def test_the_fallback_never_raises_and_records_the_unseen_channel(self) -> None:
        standardiser = trm3_g.fit_channel_standardiser(
            self.fit_streams, min_bucket_traces=3, pooled_fallback=True
        )
        self.assertNotIn("commentary", standardiser.stats)
        self.assertEqual(standardiser.fallback_channels, ())  # nothing thin, just absent
        z = standardiser.standardize(self.target_stream)
        self.assertEqual(z.shape, self.target_stream.scores.shape)
        self.assertTrue(np.isfinite(z).all())
        block = standardiser.sparse_fallback_json()
        self.assertEqual(block["applied_channels_absent_from_fit"], ["commentary"])
        commentary_windows = sum(1 for tag in self.target_stream.tags if tag == "commentary")
        self.assertEqual(block["applied_windows"]["commentary"], commentary_windows)
        self.assertNotIn("analysis", block["applied_windows"])

    def test_calibration_and_scoring_survive_an_unseen_channel(self) -> None:
        generator = torch.Generator().manual_seed(24)
        cal = [
            make_episode(
                f"c{index}",
                generator=generator,
                tokens=90,
                analysis=24,
                commentary=12 if index < 3 else 0,
            )
            for index in range(10)
        ]
        config = trm3_g.config_for_g(["M"], alpha=0.10)
        cal_streams = trm3_g.episode_streams({"M": self.stat}, cal, self.view)["M"]
        calibration = trm3_g.calibrate_g(
            self.fit_streams,
            cal_streams,
            config,
            view=self.view,
            statistic="M",
            min_survivors=5,
            min_bucket_traces=3,
            tag_scope="message",
        )
        outputs = trm3_g.score_episode({"M": self.target_stream}, calibration, config)
        self.assertEqual(len(outputs), len(self.target_stream.ends))
        payload = calibration.to_json()
        self.assertEqual(payload["tag_scope"], "message")
        block = payload["standardiser"]["sparse_fallback"]
        self.assertTrue(block["enabled"])
        self.assertEqual(block["applied_channels_absent_from_fit"], ["commentary"])
        self.assertGreater(block["applied_windows"]["commentary"], 0)
        self.assertIsNotNone(block["pooled"])

    def test_strict_calibration_reproduces_the_reported_keyerror(self) -> None:
        generator = torch.Generator().manual_seed(25)
        cal = [
            make_episode(
                f"c{index}",
                generator=generator,
                tokens=90,
                analysis=24,
                commentary=12 if index < 3 else 0,
            )
            for index in range(10)
        ]
        config = trm3_g.config_for_g(["M"], alpha=0.10)
        cal_streams = trm3_g.episode_streams({"M": self.stat}, cal, self.view)["M"]
        with self.assertRaises(KeyError):
            trm3_g.calibrate_g(
                self.fit_streams,
                cal_streams,
                config,
                view=self.view,
                statistic="M",
                min_survivors=5,
                min_bucket_traces=3,
                pooled_fallback=False,
            )


class FallbackDoesNotDisturbDenseCellsTest(unittest.TestCase):
    """A pool with no thin channel scores identically with and without the fallback."""

    def test_scores_are_identical_when_nothing_falls_back(self) -> None:
        view = trm3_g.VIEWS["V1"]
        fit = routine_pool(14, seed=41)
        cal = routine_pool(12, seed=42, prefix="c")
        stat, fit_streams = streams_of(fit, view)
        cal_streams = trm3_g.episode_streams({"M": stat}, cal, view)["M"]
        config = trm3_g.config_for_g(["M"], alpha=0.10)
        common = dict(view=view, statistic="M", min_survivors=6, min_bucket_traces=3)
        with_fallback = trm3_g.calibrate_g(
            fit_streams, cal_streams, config, pooled_fallback=True, **common
        )
        without = trm3_g.calibrate_g(
            fit_streams, cal_streams, config, pooled_fallback=False, **common
        )
        self.assertEqual(with_fallback.standardiser.fallback_channels, ())
        self.assertEqual(sorted(with_fallback.standardiser.stats), sorted(without.standardiser.stats))
        np.testing.assert_array_equal(
            with_fallback.reference.channels["M"].path_maxima,
            without.reference.channels["M"].path_maxima,
        )
        for stream in cal_streams:
            np.testing.assert_array_equal(
                with_fallback.standardiser.standardize(stream),
                without.standardiser.standardize(stream),
            )
        self.assertEqual(with_fallback.standardiser.fallback_applied, {})


P0_BATCH = ROOT / "artifacts" / "agent_v2" / "agent_v3_p0" / "batch"
P0_AVAILABLE = (P0_BATCH / "run_summary.json").exists()


def _runner_module():
    import importlib.util

    path = ROOT / "scripts" / "research_v4" / "run_detectors_g.py"
    spec = importlib.util.spec_from_file_location("run_detectors_g_fallback_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(P0_AVAILABLE, "agent_v3 P0 artifacts absent")
class TagScopeCliTest(unittest.TestCase):
    """``tag_scope`` is a first-class option and lands in ``result.json`` under both scopes."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = _runner_module()

    def _run(self, out_root: Path, scope: str) -> dict:
        argv = [
            "--fit", str(P0_BATCH), "--fit-scenarios", "001,011,016,021",
            "--cal", str(P0_BATCH), "--cal-scenarios", "036,041,066,071",
            "--target", str(P0_BATCH), "--target-scenarios", "036,041,066,071",
            "--view", "V1", "--statistic", "M", "--alpha", "0.10",
            "--h-min-survivors", "6", "--min-bucket-traces", "3",
            "--tag-scope", scope, "--normal-only-smoke",
            "--outputs", "primary", "--output-root", str(out_root),
            "--run-name", f"scope_{scope}",
        ]
        self.assertEqual(self.runner.main(argv), 0)
        return json.loads((out_root / f"scope_{scope}" / "result.json").read_text())

    def test_both_scopes_are_recorded_and_differ(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            message = self._run(root, "message")
            body = self._run(root, "body")
        for scope, payload in (("message", message), ("body", body)):
            self.assertEqual(payload["tag_scope"], scope)
            self.assertEqual(payload["tag_scope_detail"]["scope"], scope)
            self.assertEqual(payload["tag_scope_detail"]["load_reports_agree"], [scope])
            self.assertEqual(payload["args"]["tag_scope"], scope)
            self.assertEqual(payload["cells"]["M"]["calibration"]["tag_scope"], scope)
            self.assertEqual(payload["cells"]["M"]["standardisation"]["tag_scope"], scope)
        # the body scope drops the channel-header tokens, so it retains strictly fewer
        message_tagged = sum(
            count
            for tag, count in message["pools"]["target"]["channel_tokens"].items()
            if tag in io_g.HARMONY_CHANNELS
        )
        body_tagged = sum(
            count
            for tag, count in body["pools"]["target"]["channel_tokens"].items()
            if tag in io_g.HARMONY_CHANNELS
        )
        self.assertLess(body_tagged, message_tagged)
        self.assertGreater(body["pools"]["target"]["channel_tokens"]["other"], 0)

    def test_the_fallback_block_is_written_for_every_cell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            payload = self._run(Path(tmp), "message")
        block = payload["cells"]["M"]["standardisation"]
        self.assertTrue(block["enabled"])
        self.assertEqual(block["min_channel_windows"], trm3_g.MIN_CHANNEL_WINDOWS)
        self.assertEqual(block["min_channel_traces"], trm3_g.MIN_CHANNEL_TRACES)
        self.assertIn("fit_support", block)
        self.assertIsNotNone(block["pooled"])
        self.assertEqual(
            block,
            {
                "tag_scope": "message",
                **payload["cells"]["M"]["calibration"]["standardiser"]["sparse_fallback"],
            },
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
