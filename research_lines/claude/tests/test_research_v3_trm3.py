"""Unit tests for the TRM-3 sequential calibration, fusion, state machine and evaluation.

Everything here is synthetic (hand-built score streams and fake traces); no routing cache,
no label file and -- per the data-discipline rule of the brief section 8 -- no attack or
drift material of any kind.

``unittest`` style, like the rest of ``tests/`` (the environment has no pytest); the file
is also collectable by pytest.
"""

from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import harness, trm3  # noqa: E402

torch.set_num_threads(4)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class FakeTrace:
    trace_id: str
    pair_group_id: str
    arm: str = "clean"
    positive: bool = False
    workflow: str = "wf"
    channel: str = "ch"
    scenario_domain: str = "dom"
    batch: str = "t"
    fold: int = 0
    token_count: int = 32
    top_k_ids: torch.Tensor | None = None
    labels: dict | None = None


class FakeScorer:
    """Registry-shaped scorer over a fixed ``{trace_id: [scores]}`` table."""

    def __init__(self, table: dict[str, list[float]], window_width: int = 8) -> None:
        self.table = table
        self.window_width = int(window_width)
        self.fitted: list[str] = []

    def fit(self, routine_traces):
        self.fitted = [t.trace_id for t in routine_traces]
        return {"n": len(self.fitted)}

    def score(self, state, trace):
        values = self.table[trace.trace_id]
        scores = torch.tensor(values, dtype=torch.float64)
        ends = torch.arange(self.window_width - 1, self.window_width - 1 + len(values))
        return scores, ends


def identity_stats(n_traces: int = 1, n_windows: int = 1) -> trm3.BucketStatsK:
    return trm3.BucketStatsK(
        bucket_size=32,
        cap=0,
        mu=np.array([0.0]),
        sd=np.array([1.0]),
        trace_counts=[n_traces],
        window_counts=[n_windows],
        reused_buckets=[],
    )


def make_reference(z_streams: list[list[float]]) -> trm3.ChannelReference:
    arrays = [np.array(z, dtype=np.float64) for z in z_streams]
    maxima, lengths = trm3._path_maxima(arrays)
    pooled = np.sort(np.concatenate(arrays))
    return trm3.ChannelReference(
        stats=identity_stats(len(arrays), int(pooled.size)),
        path_maxima=maxima,
        lengths=lengths,
        window_z_sorted=pooled,
    )


def make_half(channels: dict[str, list[list[float]]], half: int = 0) -> trm3.HalfCalibration:
    return trm3.HalfCalibration(
        half=half,
        channels={name: make_reference(streams) for name, streams in channels.items()},
        trace_ids=tuple(f"cal-{i}" for i in range(len(next(iter(channels.values()))))),
    )


def make_outputs(p_fused: list[float], ends: list[int] | None = None) -> list[trm3.TokenOutput]:
    ends = ends if ends is not None else list(range(7, 7 + len(p_fused)))
    config = trm3.config_for_variant("m_only")
    steps = trm3.temporal_machine(p_fused, ends, config)
    outputs = []
    for position, (p, end) in enumerate(zip(p_fused, ends)):
        step = steps[position]
        outputs.append(
            trm3.TokenOutput(
                k=position,
                end=int(end),
                p={"M": float(p)},
                p_fused=float(p),
                state=trm3._alarm_state(float(p), config),
                temporal_state=step.temporal_state,
                e0=step.e0,
                duration=step.duration,
                attribution="M",
                regime_flag=None,
                censored=step.censored,
                earliest_decision_end=step.earliest_decision_end,
                episode_index=step.episode_index,
                remaining_budget=0.0,
                calibration_version="test",
            )
        )
    return outputs


def small_d(variant: str = "trm3", depth: int = 4) -> trm3.TRM3Config:
    return dataclasses.replace(trm3.config_for_variant(variant), temporal_d=depth)


def routine_fakes(n: int) -> list[FakeTrace]:
    return [
        FakeTrace(
            trace_id=f"t{i}",
            pair_group_id=f"g{i}",
            arm="clean" if i % 2 else "benign_control",
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# position buckets
# ---------------------------------------------------------------------------


class BucketTests(unittest.TestCase):
    def test_tail_merge_needs_thirty_traces(self):
        long_streams = [np.full(40, 5.0) for _ in range(10)]
        short_streams = [np.full(20, 1.0) for _ in range(30)]
        stats = trm3.fit_bucket_stats_k(long_streams + short_streams, min_bucket_traces=30)
        # bucket 1 (k >= 32) is reached by only 10 traces -> merged into the tail bucket 0
        self.assertEqual(stats.cap, 0)
        self.assertEqual(stats.trace_counts, [40])
        pooled = np.concatenate(long_streams + short_streams)
        self.assertAlmostEqual(float(stats.mu[0]), float(pooled.mean()), places=9)

    def test_second_bucket_kept_when_enough_traces(self):
        streams = [np.concatenate([np.zeros(32), np.full(8, 10.0)]) for _ in range(30)]
        stats = trm3.fit_bucket_stats_k(streams, min_bucket_traces=30)
        self.assertEqual(stats.cap, 1)
        self.assertAlmostEqual(float(stats.mu[0]), 0.0, places=9)
        self.assertAlmostEqual(float(stats.mu[1]), 10.0, places=9)
        z = stats.standardize(np.array([0.0, 10.0]), np.array([0, 32]))
        self.assertAlmostEqual(float(z[0]), 0.0, places=9)
        self.assertAlmostEqual(float(z[1]), 0.0, places=9)

    def test_bucket_index_is_the_endpoint_ordinal(self):
        # k = 0 is the first scored endpoint whatever the window width (decision 1)
        stats = trm3.fit_bucket_stats_k([np.arange(64.0) for _ in range(30)])
        self.assertEqual(stats.buckets(np.array([0, 31, 32, 63])).tolist(), [0, 0, 1, 1])

    def test_k_beyond_the_last_bucket_reuses_the_tail_statistics(self):
        """Amendment 2: no dead zone -- the tail bucket's mu/sigma carry on."""

        streams = [np.concatenate([np.zeros(32), np.full(8, 10.0)]) for _ in range(30)]
        stats = trm3.fit_bucket_stats_k(streams, min_bucket_traces=30)
        self.assertEqual(stats.cap, 1)
        far = np.array([64, 96, 383, 1000], dtype=np.int64)
        self.assertEqual(stats.buckets(far).tolist(), [1, 1, 1, 1])
        z = stats.standardize(np.full(4, 10.0), far)
        self.assertTrue(np.allclose(z, 0.0))

    def test_empty_input_raises(self):
        with self.assertRaises(ValueError):
            trm3.fit_bucket_stats_k([])


# ---------------------------------------------------------------------------
# sequential p-values against the FIXED full-path-maximum reference (amendment 2)
# ---------------------------------------------------------------------------


class SequentialPValueTests(unittest.TestCase):
    def test_reference_is_the_fixed_set_of_full_path_maxima(self):
        reference = make_reference([[0.0, 0.0, 0.0, 0.0], [1.0, 1.0, 1.0, 1.0], [2.0, 2.0]])
        # the short path is NOT dropped: its full-path maximum stays in the reference
        self.assertEqual(reference.path_maxima.tolist(), [0.0, 1.0, 2.0])
        self.assertEqual(reference.n_reference, 3)
        self.assertAlmostEqual(reference.p_value(0.5), 3 / 4, places=12)
        self.assertAlmostEqual(reference.p_value(5.0), 1 / 4, places=12)
        # comparison is >=
        self.assertAlmostEqual(reference.p_value(2.0), 2 / 4, places=12)
        self.assertAlmostEqual(reference.p_value(-1.0), 4 / 4, places=12)

    def test_running_max_is_used_not_the_current_z(self):
        reference = make_reference([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]])
        half = trm3.HalfCalibration(half=0, channels={"M": reference}, trace_ids=("a", "b", "c"))
        config = trm3.config_for_variant("m_only")
        streams = {"M": (np.array([7, 8]), np.array([5.0, -9.0]))}
        p = trm3.channel_p_values(streams, half, config)["M"]
        self.assertAlmostEqual(p[7], 1 / 4, places=12)
        # the running max at k=1 is still 5.0, so the p-value does not relax
        self.assertAlmostEqual(p[8], 1 / 4, places=12)

    def test_p_is_monotone_and_has_no_dead_zone_past_the_reference_lengths(self):
        # calibration paths of 2 endpoints, target of 6: v1.0 returned p = 1 past k = 2
        reference = make_reference([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]])
        half = trm3.HalfCalibration(half=0, channels={"M": reference}, trace_ids=("a", "b", "c"))
        config = trm3.config_for_variant("m_only")
        ends = np.arange(7, 13)
        streams = {"M": (ends, np.array([-1.0, -1.0, -1.0, -1.0, -1.0, 9.0]))}
        p = trm3.channel_p_values(streams, half, config)["M"]
        values = [p[int(end)] for end in ends]
        self.assertEqual(values, sorted(values, reverse=True))  # non-increasing in k
        self.assertAlmostEqual(values[-1], 1 / 4, places=12)  # still able to fire at k = 5

    def test_ever_below_alpha_equals_the_frozen_order_statistic_rule(self):
        """"ever p_c <= alpha_c" == "full-path max >= conformal threshold" (amendment 2).

        The two rules can only disagree when the target maximum equals a calibration
        maximum exactly (the harness compares ``>=`` while ``p <= alpha`` needs one more
        strictly exceeded reference point); the synthetic draws below avoid exact ties,
        which is also what disjoint calibration halves guarantee on real streams.
        """

        rng = np.random.default_rng(20260906)
        for n_cal, alpha in ((80, 0.10), (80, 0.04), (80, 0.02), (37, 0.10), (11, 0.25)):
            with self.subTest(n_cal=n_cal, alpha=alpha):
                calibration = [rng.normal(size=int(rng.integers(5, 40))) for _ in range(n_cal)]
                reference = make_reference([list(path) for path in calibration])
                threshold = harness.conformal_threshold(
                    [float(path.max()) for path in calibration], alpha
                )["threshold"]
                self.assertAlmostEqual(
                    threshold, reference.threshold(alpha)["threshold"], places=12
                )
                half = trm3.HalfCalibration(
                    half=0, channels={"M": reference}, trace_ids=tuple("c")
                )
                config = dataclasses.replace(
                    trm3.config_for_variant("m_only"),
                    channels=(trm3.channel_spec("M", alpha),),
                    alpha=alpha,
                )
                for _ in range(60):
                    path = rng.normal(loc=rng.normal(scale=1.5), size=int(rng.integers(3, 60)))
                    ends = np.arange(7, 7 + path.size)
                    p = trm3.channel_p_values({"M": (ends, path)}, half, config)["M"]
                    ever = any(value <= alpha + 1e-15 for value in p.values())
                    frozen = float(path.max()) >= threshold
                    self.assertEqual(ever, frozen, (float(path.max()), threshold))

    def test_no_temporal_uses_pooled_windows_with_bonferroni(self):
        reference = make_reference([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]])  # 6 pooled windows
        half = trm3.HalfCalibration(half=0, channels={"M": reference}, trace_ids=("a", "b", "c"))
        config = dataclasses.replace(
            trm3.config_for_variant("no_temporal"),
            channels=(trm3.channel_spec("M", 0.10),),
            k_max=4,
        )
        streams = {"M": (np.array([7, 8]), np.array([5.0, -9.0]))}
        p = trm3.channel_p_values(streams, half, config)["M"]
        self.assertAlmostEqual(p[7], min(1.0, 4 * (1 + 0) / 7), places=12)
        # unlike the sequential rule, a low window relaxes the p-value again
        self.assertAlmostEqual(p[8], min(1.0, 4 * (1 + 6) / 7), places=12)

    def test_no_temporal2_is_the_uncorrected_per_look_bracket(self):
        reference = make_reference([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]])  # 6 pooled windows
        half = trm3.HalfCalibration(half=0, channels={"M": reference}, trace_ids=("a", "b", "c"))
        config = dataclasses.replace(
            trm3.config_for_variant("no_temporal2"),
            channels=(trm3.channel_spec("M", 0.10),),
            k_max=4,
        )
        streams = {"M": (np.array([7, 8]), np.array([5.0, -9.0]))}
        p = trm3.channel_p_values(streams, half, config)["M"]
        # same window tail as B-NT, without the Bonferroni factor: the upper bracket
        self.assertAlmostEqual(p[7], 1 / 7, places=12)
        self.assertAlmostEqual(p[8], 7 / 7, places=12)
        self.assertEqual(trm3.config_for_variant("no_temporal2").decision_rule, "no_temporal2")
        self.assertEqual(
            trm3.config_for_variant("no_temporal").decision_rule, "no_temporal"
        )

    def test_unseen_only_uses_the_fixed_positive_rule(self):
        # amendment 5: no conformal step -- the raw window score decides
        reference = make_reference([[0.0, 0.0], [0.0, 0.0]])
        half = trm3.HalfCalibration(half=0, channels={"U": reference}, trace_ids=("a", "b"))
        config = trm3.config_for_variant("unseen_only")
        self.assertEqual(config.decision_rule, "fixed_positive")
        streams = {"U": (np.array([7, 8, 9]), np.array([0.0, 1.0, 3.0]))}
        p = trm3.channel_p_values(streams, half, config)["U"]
        self.assertEqual([p[7], p[8], p[9]], [1.0, 0.0, 0.0])
        outputs = trm3.online(streams, half, config)
        self.assertEqual(
            [o.state for o in outputs],
            [trm3.STATE_SILENT, trm3.STATE_CONFIRMED, trm3.STATE_CONFIRMED],
        )
        # the temporal machine is disabled for every baseline rule
        self.assertEqual({o.temporal_state for o in outputs}, {trm3.TEMPORAL_DISABLED})


# ---------------------------------------------------------------------------
# fusion / attribution / states
# ---------------------------------------------------------------------------


class FusionTests(unittest.TestCase):
    def test_frozen_alpha_split_and_arithmetic(self):
        """Amendment 1: ``p_fused = min(1, alpha * min_c p_c / alpha_c)``."""

        config = trm3.config_for_variant("trm3")
        self.assertEqual(config.alpha_of("S"), 0.02)
        self.assertEqual(config.alpha_of("M"), 0.04)
        self.assertEqual(config.alpha_of("J"), 0.04)
        # any channel exactly at its own alpha puts the fusion exactly at alpha
        self.assertAlmostEqual(trm3.fuse({"S": 0.02, "M": 1.0, "J": 1.0}, config), 0.10, places=12)
        self.assertAlmostEqual(trm3.fuse({"S": 1.0, "M": 0.04, "J": 1.0}, config), 0.10, places=12)
        self.assertAlmostEqual(trm3.fuse({"S": 1.0, "M": 1.0, "J": 0.04}, config), 0.10, places=12)
        self.assertAlmostEqual(trm3.fuse({"S": 0.02, "M": 0.04, "J": 0.04}, config), 0.10, places=12)
        self.assertAlmostEqual(trm3.fuse({"S": 0.0, "M": 1.0, "J": 1.0}, config), 0.0, places=12)
        self.assertEqual(trm3.fuse({"S": 1.0, "M": 1.0, "J": 1.0}, config), 1.0)
        self.assertAlmostEqual(
            trm3.fuse({"S": 0.004, "M": 0.02, "J": 0.004}, config),
            0.10 * min(0.004 / 0.02, 0.02 / 0.04, 0.004 / 0.04),
            places=12,
        )

    def test_three_channels_can_reach_confirmed_with_eighty_calibration_traces(self):
        """The v1.0 sum could not: its minimum was 3 * 0.10 / 81 * (1/0.02) > 0.10."""

        config = trm3.config_for_variant("trm3")
        smallest = 1.0 / 81.0
        self.assertLessEqual(
            trm3.fuse({"S": smallest, "M": 1.0, "J": 1.0}, config), config.alpha
        )
        self.assertEqual(
            trm3._alarm_state(trm3.fuse({"S": smallest, "M": 1.0, "J": 1.0}, config), config),
            trm3.STATE_CONFIRMED,
        )

    def test_single_channel_variant_passes_p_through(self):
        config = trm3.config_for_variant("m_only")
        self.assertAlmostEqual(config.alpha_of("M"), 0.10, places=12)
        self.assertAlmostEqual(trm3.fuse({"M": 0.07}, config), 0.07, places=12)

    def test_two_channel_variant_splits_alpha_evenly(self):
        config = trm3.config_for_variant("sj")
        self.assertEqual([spec.alpha for spec in config.channels], [0.05, 0.05])
        self.assertAlmostEqual(trm3.fuse({"S": 0.05, "J": 1.0}, config), 0.10, places=12)
        self.assertAlmostEqual(trm3.fuse({"S": 0.05, "J": 0.05}, config), 0.10, places=12)

    def test_attribution_is_argmin_p_over_alpha(self):
        config = trm3.config_for_variant("trm3")
        self.assertEqual(trm3.attribute({"S": 0.02, "M": 0.01, "J": 1.0}, config), "M")
        self.assertEqual(trm3.attribute({"S": 0.001, "M": 0.01, "J": 1.0}, config), "S")
        # exact tie -> the frozen channel order decides
        self.assertEqual(trm3.attribute({"S": 0.02, "M": 0.04, "J": 0.04}, config), "S")

    def test_alarm_state_thresholds(self):
        config = trm3.config_for_variant("trm3")
        self.assertEqual(trm3._alarm_state(0.05, config), trm3.STATE_CONFIRMED)
        self.assertEqual(trm3._alarm_state(0.10, config), trm3.STATE_CONFIRMED)
        self.assertEqual(trm3._alarm_state(0.25, config), trm3.STATE_PROVISIONAL)
        self.assertEqual(trm3._alarm_state(0.2501, config), trm3.STATE_SILENT)


# ---------------------------------------------------------------------------
# temporal state machine
# ---------------------------------------------------------------------------


class TemporalMachineTests(unittest.TestCase):
    def test_sustained_after_the_full_window(self):
        config = small_d()
        p = [1.0, 0.2, 0.2, 1.0, 0.2, 0.9]
        steps = trm3.temporal_machine(p, list(range(7, 7 + len(p))), config)
        self.assertEqual(steps[0].temporal_state, trm3.TEMPORAL_NONE)
        self.assertEqual(steps[1].temporal_state, trm3.TEMPORAL_UNCERTAIN)
        self.assertEqual(steps[1].e0, 8)
        self.assertEqual(steps[1].earliest_decision_end, 8 + 4)
        self.assertTrue(steps[1].censored)
        # window [e0, e0+4) holds 3 of 4 endpoints at or below 0.25 -> SUSTAINED
        self.assertEqual(steps[4].temporal_state, trm3.TEMPORAL_SUSTAINED)
        self.assertFalse(steps[4].censored)
        self.assertEqual(steps[4].duration, 4)
        self.assertEqual(steps[5].temporal_state, trm3.TEMPORAL_SUSTAINED)

    def test_uncertain_when_the_window_is_below_half(self):
        config = small_d()
        steps = trm3.temporal_machine([0.2, 1.0, 1.0, 1.0], list(range(7, 11)), config)
        self.assertEqual(steps[3].temporal_state, trm3.TEMPORAL_UNCERTAIN)
        self.assertEqual(steps[3].e0, 7)

    def test_exactly_half_the_window_counts_as_sustained(self):
        config = small_d()
        steps = trm3.temporal_machine([0.2, 0.2, 1.0, 1.0], list(range(7, 11)), config)
        self.assertEqual(steps[3].temporal_state, trm3.TEMPORAL_SUSTAINED)

    def test_recovering_and_re_entry_resets_e0(self):
        config = small_d()
        p = [0.2] + [1.0] * 4 + [0.2, 0.2, 0.2, 0.2]
        steps = trm3.temporal_machine(p, list(range(7, 7 + len(p))), config)
        self.assertEqual(steps[0].e0, 7)
        self.assertEqual(steps[0].episode_index, 1)
        self.assertEqual(steps[4].temporal_state, trm3.TEMPORAL_RECOVERING)
        # re-entry: a new episode with a new e0
        self.assertEqual(steps[5].temporal_state, trm3.TEMPORAL_UNCERTAIN)
        self.assertEqual(steps[5].e0, 12)
        self.assertEqual(steps[5].episode_index, 2)
        self.assertEqual(steps[8].temporal_state, trm3.TEMPORAL_SUSTAINED)

    def test_no_first_crossing_lock(self):
        # a single PROVISIONAL endpoint does not lock the alarm state of later endpoints
        config = small_d()
        p = [0.2, 1.0, 1.0]
        outputs = [trm3._alarm_state(value, config) for value in p]
        self.assertEqual(
            outputs, [trm3.STATE_PROVISIONAL, trm3.STATE_SILENT, trm3.STATE_SILENT]
        )

    def test_censoring_when_the_episode_ends_early(self):
        config = small_d()
        steps = trm3.temporal_machine([1.0, 1.0, 0.2, 0.2], list(range(7, 11)), config)
        self.assertEqual(steps[-1].temporal_state, trm3.TEMPORAL_UNCERTAIN)
        self.assertTrue(steps[-1].censored)
        self.assertEqual(steps[-1].earliest_decision_end, 9 + 4)

    def test_no_temporal_rule_disables_the_machine(self):
        config = dataclasses.replace(small_d("no_temporal"), decision_rule="no_temporal")
        steps = trm3.temporal_machine([0.2] * 6, list(range(7, 13)), config)
        self.assertEqual({s.temporal_state for s in steps}, {trm3.TEMPORAL_DISABLED})
        self.assertTrue(all(s.e0 is None for s in steps))


# ---------------------------------------------------------------------------
# online
# ---------------------------------------------------------------------------


class OnlineTests(unittest.TestCase):
    def test_channel_grids_are_intersected_and_contract_is_emitted(self):
        # channel S has w=8 (ends 7..11), channel J has w=4 (ends 3..11) -> fused grid 7..11
        half = trm3.HalfCalibration(
            half=0,
            channels={
                "S": make_reference([[0.0] * 5, [1.0] * 5, [2.0] * 5]),
                "J": make_reference([[0.0] * 9, [1.0] * 9, [2.0] * 9]),
            },
            trace_ids=("a", "b", "c"),
        )
        config = dataclasses.replace(trm3.config_for_variant("sj"), evidence_window=3)
        streams = {
            "S": (np.arange(7, 12), np.array([9.0] * 5)),
            "J": (np.arange(3, 12), np.array([-9.0] * 9)),
        }
        outputs = trm3.online(streams, half, config)
        self.assertEqual([o.end for o in outputs], [7, 8, 9, 10, 11])
        self.assertEqual([o.k for o in outputs], [0, 1, 2, 3, 4])
        first = outputs[0]
        self.assertAlmostEqual(first.p["S"], 1 / 4, places=12)
        self.assertAlmostEqual(first.p["J"], 4 / 4, places=12)
        self.assertAlmostEqual(
            first.p_fused, min(1.0, 0.10 * min(0.25 / 0.05, 1.0 / 0.05)), places=12
        )
        self.assertEqual(first.attribution, "S")
        self.assertEqual(outputs[-1].evidence_window, (9, 11))

    def test_confirmation_spends_the_episode_budget(self):
        half = trm3.HalfCalibration(
            half=0,
            channels={"M": make_reference([[0.0] * 4 for _ in range(19)])},
            trace_ids=tuple(str(i) for i in range(19)),
        )
        config = trm3.config_for_variant("m_only")
        streams = {"M": (np.arange(7, 11), np.array([-1.0, 5.0, 5.0, 5.0]))}
        outputs = trm3.online(streams, half, config)
        self.assertAlmostEqual(outputs[0].p["M"], 20 / 20, places=12)
        self.assertEqual(outputs[0].state, trm3.STATE_SILENT)
        self.assertAlmostEqual(outputs[0].remaining_budget, 0.10, places=12)
        self.assertAlmostEqual(outputs[1].p["M"], 1 / 20, places=12)
        self.assertEqual(outputs[1].state, trm3.STATE_CONFIRMED)
        self.assertEqual(outputs[1].remaining_budget, 0.0)
        self.assertEqual(outputs[2].remaining_budget, 0.0)

    def test_empty_stream_yields_no_endpoint(self):
        half = make_half({"M": [[0.0, 1.0]]})
        config = trm3.config_for_variant("m_only")
        outputs = trm3.online({"M": (np.array([], dtype=int), np.array([]))}, half, config)
        self.assertEqual(outputs, [])

    def test_missing_channel_stream_raises(self):
        half = make_half({"M": [[0.0]]})
        config = trm3.config_for_variant("trm3")
        with self.assertRaises(KeyError):
            trm3.online({"M": (np.array([7]), np.array([0.0]))}, half, config)

    def test_online_needs_a_half_or_a_trace(self):
        calibration = trm3.Calibration(
            halves={0: make_half({"M": [[0.0]]}), 1: make_half({"M": [[0.0]]}, half=1)},
            group_half={},
            config=trm3.config_for_variant("m_only"),
            pool="test",
            version="v",
        )
        with self.assertRaises(ValueError):
            trm3.online(
                {"M": (np.array([7]), np.array([0.0]))}, calibration, calibration.config
            )


# ---------------------------------------------------------------------------
# calibration wiring
# ---------------------------------------------------------------------------


class CalibrationTests(unittest.TestCase):
    def test_disjoint_halves_and_reference_shapes(self):  # noqa: D401
        traces = routine_fakes(8)
        table = {t.trace_id: [float(i)] * 6 for i, t in enumerate(traces)}
        scorer = FakeScorer(table)
        states = trm3.fit_channels(
            traces, [trm3.channel_spec("M", 0.10)], builder=lambda name, config: scorer
        )
        config = trm3.config_for_variant("m_only")
        calibration = trm3.calibrate(traces, states, config, pool="unit")
        self.assertEqual(set(calibration.halves), {0, 1})
        self.assertEqual(calibration.halves[0].channels["M"].n_reference, 4)
        self.assertEqual(calibration.halves[0].channels["M"].lengths.tolist(), [6, 6, 6, 6])
        # a calibration trace is scored against the half that does not hold its scenario
        for trace in traces:
            self.assertEqual(
                calibration.select_half(trace),
                1 - calibration.group_half[trace.pair_group_id],
            )
        self.assertTrue(calibration.version.startswith("trm3-v1:unit:"))

    def test_external_targets_alternate_between_halves(self):
        traces = routine_fakes(4)
        scorer = FakeScorer({t.trace_id: [1.0, 2.0, 3.0] for t in traces})
        states = trm3.fit_channels(
            traces, [trm3.channel_spec("M", 0.10)], builder=lambda name, config: scorer
        )
        calibration = trm3.calibrate(
            traces, states, trm3.config_for_variant("m_only"), pool="unit"
        )
        targets = [FakeTrace(f"x{i}", f"xg{i}") for i in range(4)]
        calibration.register_external(targets)
        halves = [calibration.select_half(t) for t in targets]
        self.assertEqual(sorted(halves), [0, 0, 1, 1])

    def test_calibrate_refuses_a_positive_trace(self):
        traces = routine_fakes(4)
        traces[0].positive = True
        scorer = FakeScorer({t.trace_id: [1.0, 2.0] for t in traces})
        states = trm3.fit_channels(
            traces[1:], [trm3.channel_spec("M", 0.10)], builder=lambda name, config: scorer
        )
        with self.assertRaises(ValueError):
            trm3.calibrate(traces, states, trm3.config_for_variant("m_only"), pool="unit")

    def test_fit_channels_rejects_a_wrong_window_width(self):
        traces = routine_fakes(2)
        scorer = FakeScorer({t.trace_id: [1.0] for t in traces}, window_width=16)
        with self.assertRaises(ValueError):
            trm3.fit_channels(
                traces, [trm3.channel_spec("M", 0.10)], builder=lambda name, config: scorer
            )

    def test_top_coordinates_hook_falls_back_to_empty(self):
        traces = routine_fakes(2)
        scorer = FakeScorer({t.trace_id: [1.0] for t in traces})
        states = trm3.fit_channels(
            traces, [trm3.channel_spec("M", 0.10)], builder=lambda name, config: scorer
        )
        self.assertEqual(states["M"].top_coordinates(traces[0], 7), [])

    def test_run_trace_scores_against_the_opposite_half(self):
        traces = routine_fakes(6)
        scorer = FakeScorer({t.trace_id: [float(i)] * 5 for i, t in enumerate(traces)})
        states = trm3.fit_channels(
            traces, [trm3.channel_spec("M", 0.10)], builder=lambda name, config: scorer
        )
        config = trm3.config_for_variant("m_only")
        calibration = trm3.calibrate(traces, states, config, pool="unit")
        outputs, half = trm3.run_trace(states, calibration, config, traces[0])
        self.assertEqual(half, 1 - calibration.group_half[traces[0].pair_group_id])
        self.assertEqual(len(outputs), 5)
        self.assertTrue(all(0.0 < o.p_fused <= 1.0 for o in outputs))


class RegimeTests(unittest.TestCase):
    def test_regime_axis_separates_the_structured_lobe(self):
        torch.manual_seed(0)
        structured = []
        prose = []
        for index in range(6):
            structured.append(
                FakeTrace(
                    trace_id=f"s{index}",
                    pair_group_id=f"gs{index}",
                    top_k_ids=torch.randint(0, 8, (16, 24, 8)),
                )
            )
            prose.append(
                FakeTrace(
                    trace_id=f"p{index}",
                    pair_group_id=f"gp{index}",
                    top_k_ids=torch.randint(32, 40, (16, 24, 8)),
                )
            )
        axis = trm3.fit_regime_axis(
            structured + prose, json_shaped=lambda trace: trace.trace_id.startswith("s")
        )
        self.assertIsNotNone(axis)
        self.assertLessEqual(axis.q05, axis.q95)
        flags_structured = trm3.regime_stream(axis, structured[0])
        flags_prose = trm3.regime_stream(axis, prose[0])
        self.assertGreater(sum(flags_structured.values()), sum(flags_prose.values()))

    def test_regime_axis_without_structured_traces_is_none(self):
        traces = [
            FakeTrace(f"p{i}", f"g{i}", top_k_ids=torch.randint(0, 64, (16, 16, 8)))
            for i in range(3)
        ]
        self.assertIsNone(trm3.fit_regime_axis(traces, json_shaped=lambda trace: False))
        self.assertEqual(trm3.regime_stream(None, traces[0]), {})


# ---------------------------------------------------------------------------
# McNemar
# ---------------------------------------------------------------------------


class McNemarTests(unittest.TestCase):
    def test_small_vectors(self):
        block = trm3.paired_mcnemar([True, True, False, False], [True, False, False, False])
        self.assertEqual((block["only_a"], block["only_b"]), (1, 0))
        self.assertEqual(block["discordant"], 1)
        self.assertEqual(block["net_gain_a_over_b"], 1)
        self.assertAlmostEqual(block["p_value"], 1.0, places=12)

        block = trm3.paired_mcnemar([True] * 3 + [False], [False] * 4)
        self.assertEqual((block["only_a"], block["only_b"]), (3, 0))
        self.assertAlmostEqual(block["p_value"], 0.25, places=12)

        block = trm3.paired_mcnemar([True] * 6 + [False], [False] * 7)
        self.assertAlmostEqual(block["p_value"], 2 * (1 / 64), places=12)

        block = trm3.paired_mcnemar([True, False], [True, False])
        self.assertEqual(block["discordant"], 0)
        self.assertEqual(block["p_value"], 1.0)

    def test_mixed_discordance(self):
        block = trm3.paired_mcnemar([True, True, False], [False, False, True])
        self.assertEqual((block["only_a"], block["only_b"]), (2, 1))
        # exact two-sided binomial on 3 discordant pairs, smaller count 1
        self.assertAlmostEqual(block["p_value"], 2 * (1 + 3) / 8, places=12)

    def test_pairs_on_shared_keys(self):
        block = trm3.paired_mcnemar({"a": True, "b": False, "c": True}, {"a": False, "b": False})
        self.assertEqual(block["pair_count"], 2)
        self.assertEqual(block["only_a"], 1)
        self.assertEqual(block["unpaired_keys"], ["c"])

    def test_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            trm3.paired_mcnemar([True], [True, False])


# ---------------------------------------------------------------------------
# evaluation
# ---------------------------------------------------------------------------


def toy_evaluation():
    config = trm3.config_for_variant("m_only")
    clean = FakeTrace("clean-1", "g1", arm="clean")
    benign = FakeTrace("benign-1", "g1", arm="benign_control")
    clean2 = FakeTrace("clean-2", "g2", arm="clean")
    drift = FakeTrace("drift-1", "g3", arm="attack", positive=True)
    resist = FakeTrace("resist-1", "g4", arm="attack")
    resist.labels = {"engagement_class": "bounded_engagement_resisted"}
    silent = FakeTrace("silent-1", "g5", arm="attack")
    silent.labels = {"engagement_class": "no_observable_engagement"}

    # every per-trace mapping is keyed on (batch, trace_id) -- amendment 7
    outputs = {
        "t|clean-1": make_outputs([0.9] * 20),
        # one alarm -> scenario g1 is a matched-group false alarm
        "t|benign-1": make_outputs([0.9] * 5 + [0.05] * 3 + [0.9] * 12),
        "t|clean-2": make_outputs([0.9] * 20),
        # anchor 20: first alarm at end 22 -> hit at +8 (latency 2)
        "t|drift-1": make_outputs([0.9] * 15 + [0.05] * 5, ends=list(range(7, 27))),
        # anchor 20: first alarm at end 17 -> strict pre-onset, tolerant hit (band 5)
        "t|resist-1": make_outputs([0.9] * 10 + [0.05] * 10, ends=list(range(7, 27))),
        "t|silent-1": make_outputs([0.9] * 20, ends=list(range(7, 27))),
    }
    traces = [clean, benign, clean2, drift, resist, silent]
    anchors = {"t|drift-1": 20, "t|resist-1": 20, "t|silent-1": None}
    # secondary anchor 6 tokens later, as on h384 (evidence span end)
    secondary = {"t|drift-1": 26, "t|resist-1": 26}
    halves = {
        "t|clean-1": 0,
        "t|benign-1": 0,
        "t|clean-2": 1,
        "t|drift-1": 0,
        "t|resist-1": 1,
        "t|silent-1": 1,
    }
    return trm3.evaluate(
        outputs,
        traces,
        anchors,
        config,
        halves=halves,
        secondary_anchors=secondary,
        anchor_labels={"primary": "engagement_onset", "secondary": "execution_onset"},
    )


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.result = toy_evaluation()

    def test_far_and_matched_groups(self):
        far = self.result["far"]
        self.assertEqual(far["clean_count"], 2)
        self.assertAlmostEqual(far["clean"], 0.0, places=12)
        self.assertEqual(far["benign_count"], 1)
        self.assertAlmostEqual(far["benign"], 1.0, places=12)
        self.assertAlmostEqual(far["pooled"], 1 / 3, places=12)
        # scenario g1 holds clean-1 and benign-1 and alarms because benign-1 does
        self.assertEqual(far["matched_group_count"], 2)
        self.assertAlmostEqual(far["matched_group"], 0.5, places=12)
        self.assertAlmostEqual(far["benign_minus_clean"], 1.0, places=12)

    def test_endpoint_rates_and_calibration_halves(self):
        endpoint = self.result["endpoint"]
        self.assertEqual(endpoint["eligible_endpoints"], 60)
        self.assertEqual(endpoint["alarm_endpoints"], 3)
        self.assertEqual(endpoint["alarm_onsets"], 1)
        self.assertAlmostEqual(
            endpoint["alarm_onsets_per_1000_eligible"], 1000 / 60, places=9
        )
        halves = self.result["far_by_calibration_half"]
        self.assertAlmostEqual(halves["0"]["far"], 0.5, places=12)
        self.assertAlmostEqual(halves["1"]["far"], 0.0, places=12)
        self.assertAlmostEqual(self.result["far_half_gap"], 0.5, places=12)

    def test_recall_strict_and_tolerant(self):
        strict = self.result["recall_strict"]
        self.assertEqual(strict["positive_count"], 2)  # drift + anchored bounded resist
        self.assertEqual(strict["recall_plus_8_count"], 1)
        self.assertAlmostEqual(strict["recall_plus_8"], 0.5, places=12)
        self.assertEqual(strict["pre_onset_count"], 1)
        self.assertAlmostEqual(strict["latency_median"], 2.0, places=12)
        tolerant = self.result["recall_tolerant"]
        # resist-1's first alarm at end 17 is 3 tokens before the anchor: inside the
        # 5-token tolerant band, so it becomes a hit (latency clamped to 0)
        self.assertEqual(tolerant["recall_plus_8_count"], 2)
        self.assertEqual(tolerant["pre_onset_count"], 0)
        self.assertEqual(
            self.result["primary_event_hits_plus_8"],
            {"t|drift-1": True, "t|resist-1": False},
        )
        self.assertEqual(self.result["silent_count"], 1)
        self.assertAlmostEqual(self.result["silent_alarm_rate"], 0.0, places=12)

    def test_tolerant_band_rescues_a_slightly_early_alarm(self):
        config = trm3.config_for_variant("m_only")
        drift = FakeTrace("drift-1", "g3", arm="attack", positive=True)
        outputs = {"t|drift-1": make_outputs([0.9] * 11 + [0.05] * 9, ends=list(range(7, 27)))}
        result = trm3.evaluate(outputs, [drift], {"t|drift-1": 20}, config)
        self.assertEqual(result["recall_strict"]["recall_plus_8_count"], 0)
        self.assertEqual(result["recall_strict"]["pre_onset_count"], 1)
        self.assertEqual(result["recall_tolerant"]["recall_plus_8_count"], 1)
        self.assertEqual(result["recall_tolerant"]["pre_onset_count"], 0)

    def test_worst_group_and_attribution_blocks(self):
        self.assertEqual(
            set(self.result["worst_group"]), {"workflow", "channel", "domain", "length_tertile"}
        )
        workflow = self.result["worst_group"]["workflow"]
        self.assertAlmostEqual(workflow["worst_far"][1], 1 / 3, places=12)
        self.assertEqual(self.result["attribution"]["endpoints"], {"M": 3 + 10 + 5})
        self.assertEqual(self.result["attribution"]["first_confirmed"], {"M": 3})

    def test_both_anchor_columns_are_reported(self):
        """Amendment 3: primary = evidence-span start, secondary = span end."""

        self.assertEqual(
            self.result["anchor_policy"],
            {"primary": "engagement_onset", "secondary": "execution_onset"},
        )
        primary = self.result["recall_strict"]
        secondary = self.result["recall_strict_secondary"]
        self.assertEqual(primary["positive_count"], 2)
        self.assertEqual(secondary["positive_count"], 2)
        # drift-1 first alarms at end 22: a +8 hit on the primary anchor 20, and a
        # pre-onset alarm on the secondary anchor 26
        self.assertEqual(primary["recall_plus_8_count"], 1)
        self.assertEqual(secondary["recall_plus_8_count"], 0)
        self.assertEqual(secondary["pre_onset_count"], 2)
        self.assertEqual(
            self.result["primary_event_hits_plus_8_secondary_anchor"],
            {"t|drift-1": False, "t|resist-1": False},
        )

    def test_no_secondary_anchor_column_is_none(self):
        config = trm3.config_for_variant("m_only")
        drift = FakeTrace("drift-1", "g3", arm="attack", positive=True)
        outputs = {"t|drift-1": make_outputs([0.9] * 20, ends=list(range(7, 27)))}
        result = trm3.evaluate(outputs, [drift], {"t|drift-1": 20}, config)
        self.assertIsNone(result["recall_strict_secondary"])
        self.assertIsNone(result["recall_tolerant_secondary"])

    def test_spontaneous_drift_group_is_descriptive_only(self):
        """Amendment 4: excluded from every FAR denominator, any-alarm count only."""

        config = trm3.config_for_variant("m_only")
        traces = [
            FakeTrace("clean-1", "g1", arm="clean"),
            FakeTrace("benign-1", "g1", arm="benign_control"),
            FakeTrace("benign-drift", "g2", arm="benign_control"),
        ]
        outputs = {
            "t|clean-1": make_outputs([0.9] * 10),
            "t|benign-1": make_outputs([0.9] * 10),
            "t|benign-drift": make_outputs([0.05] * 10),
        }
        result = trm3.evaluate(
            outputs, traces, {}, config, spontaneous_drift=["t|benign-drift"]
        )
        self.assertEqual(result["far"]["pooled_count"], 2)
        self.assertAlmostEqual(result["far"]["pooled"], 0.0, places=12)
        self.assertEqual(result["far"]["benign_count"], 1)
        self.assertEqual(result["far"]["matched_group_count"], 1)
        block = result["spontaneous_drift"]
        self.assertEqual(block["trace_count"], 1)
        self.assertEqual(block["any_alarm_count"], 1)
        self.assertAlmostEqual(block["any_alarm_rate"], 1.0, places=12)
        self.assertEqual(block["keys"], ["t|benign-drift"])
        self.assertEqual(
            result["temporal"]["confusion"]["spontaneous_drift"]["trace_count"], 1
        )
        for axis in result["worst_group"].values():
            for group in axis["groups"].values():
                self.assertLessEqual(group["normal_count"], 2)

    def test_temporal_confusion_covers_every_behaviour_class(self):
        confusion = self.result["temporal"]["confusion"]
        self.assertEqual(
            set(confusion),
            {"execution", "bounded", "silent", "clean", "benign", "spontaneous_drift"},
        )
        self.assertEqual(confusion["execution"]["trace_count"], 1)
        self.assertEqual(confusion["bounded"]["trace_count"], 1)
        self.assertAlmostEqual(confusion["silent"]["any_alarm_rate"], 0.0, places=12)


class RoutineOnlyTests(unittest.TestCase):
    def test_refuses_attack_material(self):
        config = trm3.config_for_variant("m_only")
        attack = FakeTrace("resist-1", "g4", arm="attack")
        with self.assertRaises(ValueError):
            trm3.routine_only_metrics({"t|resist-1": make_outputs([0.9] * 4)}, [attack], config)

    def test_reports_far_without_any_target_metric(self):
        config = trm3.config_for_variant("m_only")
        traces = [
            FakeTrace("clean-1", "g1", arm="clean"),
            FakeTrace("benign-1", "g1", arm="benign_control"),
        ]
        outputs = {
            "t|clean-1": make_outputs([0.9] * 10),
            "t|benign-1": make_outputs([0.05] * 10),
        }
        block = trm3.routine_only_metrics(
            outputs, traces, config, halves={"t|clean-1": 0, "t|benign-1": 1}
        )
        self.assertAlmostEqual(block["far"]["pooled"], 0.5, places=12)
        self.assertNotIn("recall_strict", block)
        self.assertNotIn("primary_event_hits_plus_8", block)


# ---------------------------------------------------------------------------
# gates and output contract
# ---------------------------------------------------------------------------


class GateTests(unittest.TestCase):
    def test_pass_fail_and_not_evaluated(self):
        gates = trm3.check_gates(
            {
                "far_pooled": 0.10,
                "far_clean": 0.08,
                "far_benign": 0.30,
                "far_half_gap": 0.02,
                "silent_alarm_rate": 0.20,
                "onsets_per_1000": 3.0,
                "onsets_per_1000_cand_a": 2.0,
            }
        )
        self.assertIs(gates["G1"]["pass"], True)
        self.assertIs(gates["G2"]["pass"], False)
        self.assertAlmostEqual(gates["G2"]["value"], 0.22, places=12)
        self.assertIs(gates["G3"]["pass"], True)
        self.assertIs(gates["G4"]["pass"], False)
        self.assertEqual(gates["G5"]["status"], "not_evaluated")
        self.assertEqual(gates["G6"]["status"], "not_evaluated")
        self.assertEqual(gates["G7"]["status"], "not_evaluated")
        self.assertIs(gates["G8"]["pass"], True)  # 3.0 <= 1.5 * 2.0
        self.assertEqual(gates["summary"]["failed"], ["G2", "G4"])

    def test_g1_band_is_two_sided(self):
        self.assertIs(trm3.check_gates({"far_pooled": 0.02})["G1"]["pass"], False)
        self.assertIs(trm3.check_gates({"far_pooled": 0.20})["G1"]["pass"], False)
        self.assertIs(trm3.check_gates({"far_pooled": 0.07})["G1"]["pass"], True)

    def test_g8_fails_above_one_and_a_half_times_cand_a(self):
        gates = trm3.check_gates({"onsets_per_1000": 3.1, "onsets_per_1000_cand_a": 2.0})
        self.assertIs(gates["G8"]["pass"], False)
        self.assertAlmostEqual(gates["G8"]["limit"], 3.0, places=12)

    def test_g5_and_g7_when_supplied(self):
        gates = trm3.check_gates(
            {
                "far_pooled_c1": 0.11,
                "far_pooled_self": 0.09,
                "c1_heldout_matched_group_far": 0.20,
                "h384_control_far": 0.12,
            }
        )
        self.assertIs(gates["G5"]["pass"], True)
        self.assertIs(gates["G7"]["pass"], False)
        self.assertIs(gates["G6"]["pass"], True)


class OutputContractTests(unittest.TestCase):
    def test_schema_row_has_the_prereg_section_9_fields(self):
        outputs = make_outputs([0.05, 0.9])
        row = outputs[0].schema_row(
            trace_id="t", batch="b2", arm="clean", klass="clean", calibration="D|target|half0"
        )
        self.assertEqual(row["key"], "b2|t")  # amendment 7
        for key in (
            "key",
            "trace_id",
            "batch",
            "arm",
            "class",
            "calibration",
            "k",
            "end",
            "p_S",
            "p_M",
            "p_J",
            "p_fused",
            "state",
            "temporal_state",
            "e0",
            "attribution",
            "regime_flag",
        ):
            self.assertIn(key, row)
        self.assertAlmostEqual(row["p_M"], 0.05, places=12)
        self.assertIsNone(row["p_S"])
        self.assertIsNone(row["p_J"])
        self.assertEqual(row["state"], trm3.STATE_CONFIRMED)

    def test_variant_table_matches_the_prereg_baselines(self):
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
        self.assertEqual(trm3.config_for_variant("unseen_only").channels[0].scorer, "unseen_only")
        self.assertEqual(
            trm3.config_for_variant("surprisal_marginal").channels[0].scorer,
            "surprisal_marginal",
        )
        m_only = trm3.config_for_variant("m_only")
        self.assertEqual(m_only.channels[0].scorer, "wgm")
        self.assertEqual(m_only.channels[0].config["layers"], "middle_late")
        self.assertEqual(m_only.channels[0].config["metric"], "g1")
        self.assertEqual(m_only.channels[0].window_width, 8)

    def test_trace_key_is_the_batch_and_trace_id_pair(self):
        trace = FakeTrace("b2-f0-003--benign_control", "g", batch="h384")
        self.assertEqual(trm3.trace_key(trace), "h384|b2-f0-003--benign_control")
        self.assertNotEqual(
            trm3.trace_key(trace),
            trm3.trace_key(FakeTrace("b2-f0-003--benign_control", "g", batch="b2")),
        )
        summary = trm3.summarize_trace(make_outputs([0.9] * 3), trace, 0)
        self.assertEqual(summary.key, "h384|b2-f0-003--benign_control")
        self.assertEqual(summary.to_json()["key"], summary.key)

    def test_frozen_constants(self):
        self.assertEqual((trm3.ALPHA, trm3.ALPHA_S, trm3.ALPHA_M, trm3.ALPHA_J), (0.10, 0.02, 0.04, 0.04))
        self.assertEqual(trm3.ALPHA_PROVISIONAL, 0.25)
        self.assertEqual(trm3.TEMPORAL_D, 32)
        self.assertEqual(trm3.BUCKET_SIZE, 32)
        self.assertEqual(trm3.MIN_BUCKET_TRACES, 30)
        self.assertEqual(trm3.K_MAX, 384)


# ---------------------------------------------------------------------------
# prereg v1.2 (section 12) amendments
# ---------------------------------------------------------------------------


class PositiveSetTests(unittest.TestCase):
    """Amendment 1: the primary event is drift UNION anchored resisters."""

    def _evaluate(self, anchors):
        config = trm3.config_for_variant("m_only")
        # a B1/B2-shaped resister: attack arm, not positive, NO labels at all (that is
        # exactly what io.load_batch produces, and what the v1.1 code dropped)
        drift = FakeTrace("drift-1", "g1", arm="attack", positive=True)
        resist = FakeTrace("resist-1", "g2", arm="attack")
        silent = FakeTrace("resist-2", "g3", arm="attack")
        clean = FakeTrace("clean-1", "g4", arm="clean")
        outputs = {
            "t|drift-1": make_outputs([0.9] * 15 + [0.05] * 5, ends=list(range(7, 27))),
            "t|resist-1": make_outputs([0.9] * 15 + [0.05] * 5, ends=list(range(7, 27))),
            "t|resist-2": make_outputs([0.9] * 20, ends=list(range(7, 27))),
            "t|clean-1": make_outputs([0.9] * 20, ends=list(range(7, 27))),
        }
        return trm3.evaluate(
            outputs, [drift, resist, silent, clean], anchors, config
        )

    def test_anchored_resister_is_a_positive(self):
        result = self._evaluate({"t|drift-1": 20, "t|resist-1": 20, "t|resist-2": None})
        self.assertEqual(
            sorted(result["primary_event_hits_plus_8"]), ["t|drift-1", "t|resist-1"]
        )
        block = result["positive_set"]
        self.assertEqual(block["count"], 2)
        self.assertEqual(block["drift_count"], 1)
        self.assertEqual(block["anchored_resist_count"], 1)
        self.assertEqual(block["attack_arm_count"], 3)

    def test_unanchored_resister_stays_out(self):
        result = self._evaluate({"t|drift-1": 20})
        self.assertEqual(list(result["primary_event_hits_plus_8"]), ["t|drift-1"])
        self.assertEqual(result["positive_set"]["anchored_resist_count"], 0)

    def test_a_normal_trace_is_never_a_positive(self):
        result = self._evaluate({"t|drift-1": 20, "t|clean-1": 10})
        self.assertNotIn("t|clean-1", result["primary_event_hits_plus_8"])


class BucketSourceTests(unittest.TestCase):
    """Amendment 3: position-bucket mu/sigma come from the fitting pool N_fit."""

    def _calibration(self, with_fit_pool: bool):
        cal = routine_fakes(8)
        fit = [FakeTrace(f"f{i}", f"fg{i}", arm="clean") for i in range(6)]
        table = {t.trace_id: [10.0, 12.0, 14.0, 16.0] for t in cal}
        table.update({t.trace_id: [0.0, 1.0, 2.0, 3.0] for t in fit})
        scorer = FakeScorer(table)
        states = trm3.fit_channels(
            fit, [trm3.channel_spec("M", 0.10)], builder=lambda name, config: scorer
        )
        config = trm3.config_for_variant("m_only")
        return trm3.calibrate(
            cal, states, config, pool="unit", fit_pool=fit if with_fit_pool else None
        )

    def test_statistics_are_fitted_on_the_fit_pool(self):
        calibration = self._calibration(True)
        self.assertEqual(calibration.bucket_source, "fit_pool")
        self.assertEqual(calibration.bucket_fit_pool_count, 6)
        stats = calibration.halves[0].channels["M"].stats
        self.assertAlmostEqual(float(stats.mu[0]), 1.5, places=9)
        # the same object standardizes both halves (one transform, blind to both)
        self.assertIs(stats, calibration.halves[1].channels["M"].stats)
        self.assertEqual(calibration.halves[0].bucket_source, "fit_pool")

    def test_fallback_is_recorded_not_silent(self):
        calibration = self._calibration(False)
        self.assertEqual(calibration.bucket_source, "calibration_pool")
        self.assertAlmostEqual(
            float(calibration.halves[0].channels["M"].stats.mu[0]), 13.0, places=9
        )
        self.assertEqual(calibration.to_json()["bucket_source"], "calibration_pool")

    def test_a_positive_may_not_enter_the_fit_pool(self):
        cal = routine_fakes(4)
        fit = routine_fakes(4)
        fit[0].positive = True
        scorer = FakeScorer({t.trace_id: [1.0, 2.0] for t in cal + fit})
        states = trm3.fit_channels(
            cal, [trm3.channel_spec("M", 0.10)], builder=lambda name, config: scorer
        )
        with self.assertRaises(ValueError):
            trm3.calibrate(
                cal, states, trm3.config_for_variant("m_only"), pool="unit", fit_pool=fit
            )


class CalibrationHorizonTests(unittest.TestCase):
    """Amendment 4: no new alarm past K_cal; the decision freezes and is flagged."""

    def _half(self, k_cal: int | None):
        return trm3.HalfCalibration(
            half=0,
            channels={"M": make_reference([[0.0] * 4 for _ in range(19)])},
            trace_ids=tuple(str(i) for i in range(19)),
            k_cal={} if k_cal is None else {"M": k_cal},
        )

    def _outputs(self, k_cal, scores):
        config = trm3.config_for_variant("m_only")
        streams = {"M": (np.arange(7, 7 + len(scores)), np.array(scores, dtype=float))}
        return trm3.online(streams, self._half(k_cal), config)

    def test_endpoints_past_the_horizon_are_flagged_and_frozen(self):
        outputs = self._outputs(4, [-1.0, -1.0, -1.0, -1.0, 5.0, 5.0])
        self.assertEqual([o.horizon_censored for o in outputs], [False] * 4 + [True] * 2)
        # the two censored endpoints carry the frozen decision of endpoint k = 3
        self.assertEqual({o.p_fused for o in outputs[3:]}, {outputs[3].p_fused})
        self.assertTrue(all(o.state == trm3.STATE_SILENT for o in outputs))

    def test_no_new_alarm_past_the_horizon(self):
        outputs = self._outputs(4, [-1.0] * 4 + [5.0] * 4)
        summary = trm3.summarize_trace(outputs, FakeTrace("x", "g"), 0)
        self.assertFalse(summary.alarm)
        self.assertEqual(summary.eligible_endpoints, 4)
        self.assertEqual(summary.censored_endpoints, 4)
        self.assertEqual(summary.emitted_endpoints, 8)
        self.assertTrue(summary.horizon_censored)
        self.assertEqual(summary.last_end, 10)

    def test_an_in_horizon_alarm_still_fires(self):
        outputs = self._outputs(4, [-1.0, 5.0, 5.0, 5.0, 5.0, 5.0])
        summary = trm3.summarize_trace(outputs, FakeTrace("x", "g"), 0)
        self.assertTrue(summary.alarm)
        self.assertEqual(summary.first_alarm_end, 8)
        self.assertEqual(summary.alarm_endpoints, 3)  # only the in-horizon ones

    def test_no_horizon_means_no_censoring(self):
        outputs = self._outputs(None, [-1.0] * 4 + [5.0] * 4)
        self.assertTrue(all(not o.horizon_censored for o in outputs))
        self.assertTrue(trm3.summarize_trace(outputs, FakeTrace("x", "g"), 0).alarm)

    def test_calibrate_records_the_pool_horizon(self):
        traces = routine_fakes(6)
        table = {t.trace_id: [1.0] * (3 + i) for i, t in enumerate(traces)}
        scorer = FakeScorer(table)
        states = trm3.fit_channels(
            traces, [trm3.channel_spec("M", 0.10)], builder=lambda name, config: scorer
        )
        calibration = trm3.calibrate(
            traces, states, trm3.config_for_variant("m_only"), pool="unit", fit_pool=traces
        )
        self.assertEqual(calibration.k_cal["M"], 8)
        self.assertEqual(calibration.halves[0].k_cal["M"], 8)
        self.assertEqual(calibration.halves[1].k_cal["M"], 8)


class AlphaScalingTests(unittest.TestCase):
    """Amendments 5 and 6: alpha-scaled budgets and the effective-alpha bookkeeping."""

    def test_channel_budgets_scale_with_alpha(self):
        for alpha, expected in ((0.10, (0.02, 0.04, 0.04)), (0.05, (0.01, 0.02, 0.02))):
            config = trm3.config_for_variant("trm3", alpha=alpha)
            with self.subTest(alpha=alpha):
                self.assertEqual(
                    tuple(spec.alpha for spec in config.channels), expected
                )
                self.assertEqual(
                    tuple(spec.weight for spec in config.channels), (0.2, 0.4, 0.4)
                )

    def test_p_fused_is_alpha_free_and_the_decision_is_not(self):
        p = {"S": 1.0, "M": 0.02, "J": 1.0}
        loose = trm3.config_for_variant("trm3", alpha=0.10)
        tight = trm3.config_for_variant("trm3", alpha=0.02)
        self.assertAlmostEqual(trm3.fuse(p, loose), 0.05, places=12)
        self.assertAlmostEqual(trm3.fuse(p, tight), 0.05, places=12)
        self.assertEqual(trm3._alarm_state(trm3.fuse(p, loose), loose), trm3.STATE_CONFIRMED)
        self.assertEqual(
            trm3._alarm_state(trm3.fuse(p, tight), tight), trm3.STATE_PROVISIONAL
        )

    def test_effective_alpha_matches_the_freeze_review_table(self):
        config = trm3.config_for_variant("trm3")
        eighty = trm3.effective_alpha(config, 80)
        self.assertEqual(eighty["channels"]["S"]["rank"], 1)
        self.assertEqual(eighty["channels"]["M"]["rank"], 3)
        self.assertAlmostEqual(eighty["alpha_eff"], 7 / 81, places=12)
        hundred = trm3.effective_alpha(config, 100)
        self.assertEqual(hundred["channels"]["S"]["rank"], 2)
        self.assertAlmostEqual(hundred["alpha_eff"], 10 / 101, places=12)
        # B-M at the same n spends its whole nominal budget
        m_only = trm3.config_for_variant("m_only")
        self.assertAlmostEqual(
            trm3.effective_alpha(m_only, 80)["alpha_eff"], 8 / 81, places=12
        )

    def test_matched_alpha_never_exceeds_the_joint_budget(self):
        config = trm3.config_for_variant("trm3")
        for n in (60, 80, 90, 100, 180):
            with self.subTest(n=n):
                eff = trm3.effective_alpha(config, n)["alpha_eff"]
                matched = trm3.matched_alpha(n, eff)
                self.assertLessEqual(matched["alpha_matched"], eff + 1e-12)
                self.assertAlmostEqual(
                    matched["alpha_matched"], matched["rank"] / (n + 1), places=12
                )


class AlphaSweepTests(unittest.TestCase):
    """Secondary S3: FAR / recall of an already-scored cell at other alphas."""

    def _decisions(self):
        streams = {}
        traces = [
            FakeTrace("clean-1", "g1", arm="clean"),
            FakeTrace("benign-1", "g1", arm="benign_control"),
            FakeTrace("clean-2", "g2", arm="clean"),
            FakeTrace("drift-1", "g3", arm="attack", positive=True),
        ]
        p_by_trace = {
            "clean-1": [0.9, 0.9, 0.9],
            "benign-1": [0.9, 0.2, 0.06],
            "clean-2": [0.9, 0.9, 0.3],
            "drift-1": [0.9, 0.9, 0.01],
        }
        for trace in traces:
            outputs = make_outputs(p_by_trace[trace.trace_id], ends=[7, 8, 9])
            streams[trm3.trace_key(trace)] = trm3.DecisionStream.from_outputs(
                outputs, trace, 0
            )
        return streams

    def test_far_and_recall_move_with_alpha(self):
        blocks = trm3.alpha_sweep(
            self._decisions(),
            [0.02, 0.05, 0.10, 0.25],
            anchors={"t|drift-1": 9},
        )
        self.assertEqual(sorted(blocks), ["0.02", "0.05", "0.1", "0.25"])
        pooled = [blocks[k]["far"]["pooled"] for k in ("0.02", "0.05", "0.1", "0.25")]
        self.assertEqual(pooled, sorted(pooled))
        self.assertAlmostEqual(blocks["0.02"]["far"]["pooled"], 0.0, places=12)
        self.assertAlmostEqual(blocks["0.1"]["far"]["pooled"], 1 / 3, places=12)
        # group g1 (clean-1 + benign-1) alarms, g2 (clean-2, min p 0.3) does not
        self.assertAlmostEqual(blocks["0.25"]["far"]["matched_group"], 0.5, places=12)
        self.assertAlmostEqual(blocks["0.02"]["recall"]["recall_plus_8"], 1.0, places=12)
        self.assertAlmostEqual(blocks["0.25"]["recall"]["recall_plus_8"], 1.0, places=12)
        self.assertEqual(
            blocks["0.1"]["primary_event_hits_plus_8"], {"t|drift-1": True}
        )

    def test_no_anchors_means_no_recall_block(self):
        blocks = trm3.alpha_sweep(self._decisions(), [0.10])
        self.assertNotIn("recall", blocks["0.1"])
        self.assertNotIn("primary_event_hits_plus_8", blocks["0.1"])

    def test_censored_endpoints_never_enter_a_sweep(self):
        trace = FakeTrace("clean-1", "g1", arm="clean")
        outputs = make_outputs([0.9, 0.9, 0.01], ends=[7, 8, 9])
        outputs[2].horizon_censored = True
        stream = trm3.DecisionStream.from_outputs(outputs, trace, 0)
        self.assertEqual(stream.ends, [7, 8])
        self.assertAlmostEqual(
            trm3.alpha_sweep({stream.key: stream}, [0.10])["0.1"]["far"]["pooled"], 0.0
        )


class ReachabilityTests(unittest.TestCase):
    """Amendment 8: ``reachable_plus_h`` needs an endpoint at or past ``anchor + h``."""

    def test_reachability_uses_the_horizon(self):
        summary = trm3.summarize_trace(
            make_outputs([0.9] * 10, ends=list(range(20, 30))), FakeTrace("x", "g"), 0
        )
        block = trm3.anchor_hits(summary, 20)
        self.assertTrue(block["reachable_plus_8"])
        self.assertFalse(block["reachable_plus_16"])
        self.assertFalse(block["reachable_plus_64"])


if __name__ == "__main__":
    unittest.main()
