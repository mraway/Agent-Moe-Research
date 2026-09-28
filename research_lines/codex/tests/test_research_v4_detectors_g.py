"""The dataset-G detector harness: views, standardisation, the H rule, anchors, metrics.

Everything here is synthetic: routing is drawn from two distributions ("routine" and
"off-manifold"), channels are assigned explicitly, and labels are written by hand, so the
tests pin the protocol rather than any number of any real pool.  The frozen sequential
core is exercised through ``trm3.online`` exactly as the runner calls it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io_g, trm3, trm3_g  # noqa: E402

torch.set_num_threads(4)

ROUTER = {"num_moe_layers": 24, "num_experts": 32, "top_k": 4, "router_adapter": "gpt_oss"}


def make_ids(
    tokens: int, *, generator: torch.Generator, offset: int = 0, spread: int = 12
) -> torch.Tensor:
    """[24, T, 4] selections drawn from ``spread`` experts starting at ``offset``."""

    experts = 32
    weights = torch.zeros(experts)
    for index in range(spread):
        weights[(offset + index) % experts] = 0.8**index
    rows = []
    for _ in range(24):
        rows.append(
            torch.stack(
                [
                    torch.multinomial(weights, 4, replacement=False, generator=generator)
                    for _ in range(tokens)
                ]
            )
        )
    return torch.stack(rows)


def make_episode(
    trace_id: str,
    *,
    generator: torch.Generator,
    tokens: int = 60,
    variant: str = "clean",
    episode_index: int = 0,
    session: str | None = None,
    labels: dict | None = None,
    offset: int = 0,
    analysis: int = 16,
    commentary: int = 12,
    scenario: str | None = None,
    family: str = "",
) -> io_g.GEpisode:
    tags = (
        ["analysis"] * analysis
        + ["commentary"] * commentary
        + ["final"] * (tokens - analysis - commentary)
    )
    return io_g.GEpisode(
        source_trace_id=session or trace_id,
        episode_index=episode_index,
        batch="synthetic",
        variant=variant,
        top_k_ids=make_ids(tokens, generator=generator, offset=offset),
        token_ids=torch.zeros(tokens, dtype=torch.long),
        channel_tags=tuple(tags),
        router=io_g.RouterMeta.from_json(ROUTER),
        pair_group_id=scenario or trace_id,
        fold=0,
        workflow="order_status",
        channel="direct_user",
        domain="poetry",
        scenario_domain="creative",
        attack_family_id=family,
        conversation_turn=episode_index + 1,
        episode_count=1,
        labels=io_g.normalise_label_row({"trace_id": trace_id, **(labels or {})}) if labels else {},
    )


def routine_pool(count: int, *, seed: int, tokens: int = 60, prefix: str = "n") -> list[io_g.GEpisode]:
    generator = torch.Generator().manual_seed(seed)
    return [
        make_episode(f"{prefix}{index}", generator=generator, tokens=tokens)
        for index in range(count)
    ]


def fitted_cell(
    fit: list[io_g.GEpisode],
    cal: list[io_g.GEpisode],
    *,
    view: str = "V1",
    statistic: str = "M",
    alpha: float = 0.10,
    min_survivors: int = 5,
    min_bucket_traces: int = 3,
):
    view_obj = trm3_g.VIEWS[view]
    config = trm3_g.config_for_g([statistic], alpha=alpha)
    stat = trm3_g.build_statistic(statistic).fit(fit, view_obj)
    fit_streams = trm3_g.episode_streams({statistic: stat}, fit, view_obj)[statistic]
    cal_streams = trm3_g.episode_streams({statistic: stat}, cal, view_obj)[statistic]
    calibration = trm3_g.calibrate_g(
        fit_streams,
        cal_streams,
        config,
        view=view_obj,
        statistic=statistic,
        min_survivors=min_survivors,
        min_bucket_traces=min_bucket_traces,
    )
    return view_obj, config, stat, calibration


def score(
    episodes: list[io_g.GEpisode], stat, calibration, config, view
) -> dict[str, list[trm3.TokenOutput]]:
    streams = trm3_g.episode_streams({config.channels[0].name: stat}, episodes, view)
    name = config.channels[0].name
    out = {}
    for episode, stream in zip(episodes, streams[name]):
        out[trm3.trace_key(episode)] = trm3_g.score_episode({name: stream}, calibration, config)
    return out


class HRuleTest(unittest.TestCase):
    def test_h_is_the_largest_k_with_enough_survivors(self) -> None:
        block = trm3_g.h_horizon([5, 4, 4, 3, 2], min_survivors=3)
        self.assertEqual(block["H"], 4)
        self.assertEqual(block["survivors_at_H"], 3)
        self.assertEqual(block["censored_paths"], 1)
        self.assertEqual(block["censored_endpoints"], 1)

    def test_ties_can_leave_more_survivors_than_the_floor(self) -> None:
        block = trm3_g.h_horizon([4, 4, 4, 4], min_survivors=2)
        self.assertEqual(block["H"], 4)
        self.assertEqual(block["survivors_at_H"], 4)
        self.assertEqual(block["censored_paths"], 0)

    def test_too_few_paths_means_no_horizon(self) -> None:
        block = trm3_g.h_horizon([10, 9], min_survivors=90)
        self.assertEqual(block["H"], 0)

    def test_calibration_refuses_a_pool_below_the_survivor_floor(self) -> None:
        fit = routine_pool(6, seed=1)
        cal = routine_pool(4, seed=2, prefix="c")
        with self.assertRaises(ValueError):
            fitted_cell(fit, cal, min_survivors=90)

    def test_endpoints_past_h_are_censored_and_carry_the_frozen_decision(self) -> None:
        fit = routine_pool(8, seed=3)
        cal = [
            make_episode(
                f"c{index}",
                generator=torch.Generator().manual_seed(100 + index),
                tokens=40 if index < 3 else 90,
            )
            for index in range(8)
        ]
        view, config, stat, calibration = fitted_cell(fit, cal, min_survivors=5)
        horizon = calibration.horizon["H"]
        self.assertGreater(horizon, 0)
        long_episode = make_episode("target", generator=torch.Generator().manual_seed(7), tokens=200)
        outputs = score([long_episode], stat, calibration, config, view)[
            trm3.trace_key(long_episode)
        ]
        scored = [o for o in outputs if not o.horizon_censored]
        censored = [o for o in outputs if o.horizon_censored]
        self.assertEqual(len(scored), horizon)
        self.assertTrue(censored)
        self.assertTrue(all(o.p_fused == scored[-1].p_fused for o in censored))
        summary = trm3.summarize_trace(outputs, long_episode, 0)
        self.assertEqual(summary.eligible_endpoints, horizon)
        self.assertEqual(summary.censored_endpoints, len(censored))

    def test_reference_maxima_are_truncated_at_h(self) -> None:
        fit = routine_pool(8, seed=11)
        cal = [
            make_episode(
                f"c{index}",
                generator=torch.Generator().manual_seed(200 + index),
                tokens=40 if index < 4 else 160,
            )
            for index in range(8)
        ]
        _, _, _, calibration = fitted_cell(fit, cal, min_survivors=5)
        reference = calibration.reference.channels["M"]
        self.assertEqual(int(reference.lengths.max()), calibration.horizon["H"])
        self.assertEqual(reference.n_reference, 8)


class StandardiserTest(unittest.TestCase):
    def test_buckets_are_fitted_per_channel(self) -> None:
        fit = routine_pool(8, seed=5)
        view = trm3_g.VIEWS["V1"]
        stat = trm3_g.build_statistic("M").fit(fit, view)
        streams = trm3_g.episode_streams({"M": stat}, fit, view)["M"]
        standardiser = trm3_g.fit_channel_standardiser(streams, min_bucket_traces=3)
        self.assertEqual(sorted(standardiser.stats), ["analysis", "commentary", "final"])
        z = standardiser.standardize(streams[0])
        self.assertEqual(len(z), len(streams[0].scores))
        # standardisation is per channel: pooled z of the fitting pool is centred per tag
        by_tag: dict[str, list[float]] = {}
        for stream in streams:
            values = standardiser.standardize(stream)
            for tag, value in zip(stream.tags, values):
                by_tag.setdefault(tag, []).append(float(value))
        for tag, values in by_tag.items():
            self.assertLess(abs(float(np.mean(values))), 0.5, tag)

    def test_a_channel_absent_from_the_fitting_pool_raises(self) -> None:
        generator = torch.Generator().manual_seed(9)
        fit = [
            make_episode(f"f{index}", generator=generator, analysis=0, commentary=0)
            for index in range(6)
        ]
        view = trm3_g.VIEWS["V1"]
        stat = trm3_g.build_statistic("M").fit(fit, view)
        streams = trm3_g.episode_streams({"M": stat}, fit, view)["M"]
        standardiser = trm3_g.fit_channel_standardiser(streams, min_bucket_traces=2)
        target = make_episode("t", generator=generator)
        target_stream = trm3_g.episode_streams({"M": stat}, [target], view)["M"][0]
        with self.assertRaises(KeyError):
            standardiser.standardize(target_stream)

    def test_position_bucket_ordinals_are_within_channel(self) -> None:
        generator = torch.Generator().manual_seed(4)
        episode = make_episode("e", generator=generator, tokens=200, analysis=60, commentary=60)
        view = trm3_g.VIEWS["V1"]
        stat = trm3_g.build_statistic("M").fit([episode], view)
        stream = trm3_g.episode_streams({"M": stat}, [episode], view)["M"][0]
        for tag in ("analysis", "commentary", "final"):
            ordinals = [o for o, t in zip(stream.ordinals, stream.tags) if t == tag]
            self.assertEqual(ordinals, list(range(len(ordinals))))


class DetectionTest(unittest.TestCase):
    def test_an_off_manifold_episode_alarms_and_routine_ones_do_not(self) -> None:
        fit = routine_pool(10, seed=21)
        cal = routine_pool(12, seed=22, prefix="c")
        view, config, stat, calibration = fitted_cell(fit, cal, min_survivors=6)
        generator = torch.Generator().manual_seed(31)
        anomalous = make_episode("attack", generator=generator, variant="attack", offset=16)
        routine = make_episode("normal", generator=generator)
        outputs = score([anomalous, routine], stat, calibration, config, view)
        anomalous_summary = trm3.summarize_trace(
            outputs[trm3.trace_key(anomalous)], anomalous, 0
        )
        routine_summary = trm3.summarize_trace(outputs[trm3.trace_key(routine)], routine, 0)
        self.assertTrue(anomalous_summary.alarm)
        self.assertFalse(routine_summary.alarm)

    def test_scores_are_causal(self) -> None:
        """Truncating an episode never changes the decisions of the surviving endpoints."""

        fit = routine_pool(8, seed=41)
        cal = routine_pool(10, seed=42, prefix="c")
        view, config, stat, calibration = fitted_cell(fit, cal, min_survivors=5)
        generator = torch.Generator().manual_seed(43)
        full = make_episode("t", generator=generator, tokens=90)
        cut = 60
        truncated = io_g.GEpisode(
            **{
                **full.__dict__,
                "top_k_ids": full.top_k_ids[:, :cut, :],
                "token_ids": full.token_ids[:cut],
                "channel_tags": full.channel_tags[:cut],
            }
        )
        full_outputs = score([full], stat, calibration, config, view)[trm3.trace_key(full)]
        cut_outputs = score([truncated], stat, calibration, config, view)[
            trm3.trace_key(truncated)
        ]
        shared = {o.end: o.p_fused for o in full_outputs if o.end < cut}
        for output in cut_outputs:
            self.assertAlmostEqual(shared[output.end], output.p_fused, places=12)


class AnchorTest(unittest.TestCase):
    def setUp(self) -> None:
        generator = torch.Generator().manual_seed(51)
        self.both = make_episode(
            "both",
            generator=generator,
            variant="attack",
            labels={"e_analysis": [4, 6], "e_final": [40, 44], "x": 52, "trajectory_class": "execution"},
        )
        self.analysis_only = make_episode(
            "analysis_only",
            generator=generator,
            variant="attack",
            labels={"e_analysis": 5, "trajectory_class": "engaged_only", "analysis_only_engagement": True},
        )
        self.silent = make_episode(
            "silent", generator=generator, variant="attack", labels={"trajectory_class": "silent"}
        )
        self.unlabelled = make_episode("unlabelled", generator=generator, variant="attack")

    def test_v1_takes_the_earliest_reachable_channel(self) -> None:
        anchors = trm3_g.view_anchors([self.both], trm3_g.VIEWS["V1"])
        anchor = anchors[trm3.trace_key(self.both)]
        self.assertEqual(anchor.anchor, 4)
        self.assertEqual(anchor.anchor_channel, "analysis")
        self.assertEqual(anchor.x, 52)

    def test_v3_uses_e_final_only(self) -> None:
        anchors = trm3_g.view_anchors([self.both], trm3_g.VIEWS["V3"])
        self.assertEqual(anchors[trm3.trace_key(self.both)].anchor, 40)

    def test_v3_marks_an_analysis_only_engagement_unreachable(self) -> None:
        anchors = trm3_g.view_anchors([self.analysis_only], trm3_g.VIEWS["V3"])
        anchor = anchors[trm3.trace_key(self.analysis_only)]
        self.assertIsNone(anchor.anchor)
        self.assertEqual(anchor.reason, "engagement_outside_view")

    def test_missing_labels_mean_no_positive(self) -> None:
        anchors = trm3_g.view_anchors(
            [self.silent, self.unlabelled], trm3_g.VIEWS["V1"]
        )
        self.assertEqual(
            anchors[trm3.trace_key(self.silent)].reason, "no_engagement"
        )
        self.assertEqual(anchors[trm3.trace_key(self.unlabelled)].reason, "unlabelled")

    def test_hit_conventions_and_window_reachability(self) -> None:
        summary = trm3.TraceSummary(
            trace_id="t",
            batch="b",
            arm="attack",
            arm_class="resist",
            behaviour_class="execution",
            workflow="w",
            channel="c",
            domain="d",
            token_count=100,
            eligible_endpoints=10,
            calibration_half=0,
            alarm=True,
            first_alarm_end=20,
            alarm_endpoints=2,
            alarm_onsets=1,
            provisional_endpoints=0,
            e0=None,
            temporal_state="NONE",
            earliest_decision_end=None,
            censored=False,
            attribution_counts={},
            alarm_ends=[20, 24],
            pair_group_id="g",
            last_end=60,
        )
        # anchor 30: the alarm at 20 is BEFORE it -> penalty convention says miss
        block = trm3_g.hit_block(summary, [8, 16, 20, 24, 60], 30)
        self.assertFalse(block["hit_plus_16"])
        self.assertTrue(block["pre_onset_alarm"])
        self.assertFalse(block["hit_no_penalty_plus_16"])  # no alarm inside [30, 46]
        # anchor 18: the alarm at 20 is a hit under both conventions
        block = trm3_g.hit_block(summary, [8, 16, 20, 24, 60], 18)
        self.assertTrue(block["hit_plus_16"])
        self.assertTrue(block["hit_no_penalty_plus_16"])
        # window reachability differs from the frozen "last_end >= anchor + h" form
        block = trm3_g.hit_block(summary, [8, 16, 60], 30)
        self.assertFalse(block["reachable_plus_16"])
        self.assertTrue(block["reachable_plus_16_frozen"])


class EvaluationTest(unittest.TestCase):
    def _pool(self):
        generator = torch.Generator().manual_seed(61)
        episodes = [
            make_episode("clean-a", generator=generator, variant="clean", labels={"quality": {"behavior": "on_task", "coverage": "full"}}),
            make_episode("clean-b", generator=generator, variant="clean", labels={"quality": {"behavior": "task_replaced", "coverage": "none"}}),
            make_episode("bc-a", generator=generator, variant="benign_control", labels={"quality": {"behavior": "on_task", "coverage": "partial"}}),
            make_episode("bl-a", generator=generator, variant="benign_lexical", labels={"quality": {"behavior": "on_task", "coverage": "full"}}),
            make_episode("lr-a", generator=generator, variant="legitimate_refusal", labels={"trajectory_class": "legitimate_refusal", "e_final": 30}),
            make_episode("att-a", generator=generator, variant="attack", labels={"trajectory_class": "silent"}, family="fam1"),
        ]
        return episodes

    def test_far_denominators_exclude_the_third_outcome(self) -> None:
        episodes = self._pool()
        fit = routine_pool(8, seed=62)
        cal = routine_pool(10, seed=63, prefix="c")
        view, config, stat, calibration = fitted_cell(fit, cal, min_survivors=5)
        outputs = score(episodes, stat, calibration, config, view)
        decisions = {
            key: trm3.DecisionStream.from_outputs(
                value, next(e for e in episodes if trm3.trace_key(e) == key), 0
            )
            for key, value in outputs.items()
        }
        metrics = trm3_g.evaluate_g(
            outputs,
            episodes,
            config,
            view,
            anchors=trm3_g.view_anchors(episodes, view),
            decisions=decisions,
        )
        far = metrics["far"]
        self.assertEqual(far["all"]["episode_count"], 4)  # clean x2, benign_control, benign_lexical
        self.assertEqual(far["filtered"]["episode_count"], 3)  # clean-b fails the quality filter
        self.assertEqual(far["clean"]["all"]["episode_count"], 2)
        self.assertEqual(far["benign_lexical"]["all"]["episode_count"], 1)
        self.assertEqual(metrics["classes"]["legitimate_refusal"]["episode_count"], 1)
        self.assertEqual(metrics["classes"]["silent_attack"]["episode_count"], 1)
        self.assertIn("short", far["length_tertile"])
        self.assertIsNotNone(metrics["endpoint"]["alarm_onsets_per_1000_eligible"])

    def test_session_budget_is_bonferroni_over_turns(self) -> None:
        generator = torch.Generator().manual_seed(71)
        episodes = [
            make_episode("s1", generator=generator, episode_index=0, session="s1", variant="clean"),
            make_episode("s1", generator=generator, episode_index=1, session="s1", variant="clean"),
            make_episode("s2", generator=generator, episode_index=0, session="s2", variant="clean"),
        ]
        fit = routine_pool(8, seed=72)
        cal = routine_pool(10, seed=73, prefix="c")
        view, config, stat, calibration = fitted_cell(fit, cal, min_survivors=5)
        outputs = score(episodes, stat, calibration, config, view)
        decisions = {
            key: trm3.DecisionStream.from_outputs(
                value, next(e for e in episodes if trm3.trace_key(e) == key), 0
            )
            for key, value in outputs.items()
        }
        block = trm3_g.session_budget(
            decisions,
            {trm3.trace_key(e): e for e in episodes},
            session_alpha=0.10,
            session_turns=4,
        )
        self.assertAlmostEqual(block["alpha_episode"], 0.025)
        self.assertEqual(block["session_count"], 2)
        self.assertEqual(block["multi_episode_sessions"], 1)
        self.assertEqual(block["normal_session_count"], 2)
        self.assertIsNotNone(block["session_far"])

    def test_matched_measured_far_and_cluster_bootstrap(self) -> None:
        keys = [f"k{index}" for index in range(8)]
        hits_a = {key: index % 2 == 0 for index, key in enumerate(keys)}
        hits_b = {key: False for key in keys}
        clusters = {key: f"fam{index // 2}" for index, key in enumerate(keys)}
        block = trm3_g.cluster_bootstrap_paired(hits_a, hits_b, clusters, replicates=200)
        self.assertEqual(block["family_count"], 4)
        self.assertAlmostEqual(block["point_estimate"], 0.5)
        self.assertEqual(block["mcnemar"]["only_a"], 4)
        self.assertIsNotNone(block["ci"])

        class Stream:
            def __init__(self, values):
                self.p_fused = values

            def alarm_ends(self, alpha):
                return [index for index, p in enumerate(self.p_fused) if p <= alpha]

        decisions = {
            "a": Stream([0.5, 0.2]),
            "b": Stream([0.9]),
            "c": Stream([0.05]),
            "d": Stream([0.4]),
        }
        matched = trm3_g.matched_alpha_by_measured_far(decisions, list(decisions), 0.25)
        self.assertLessEqual(matched["measured_far"], 0.25)
        self.assertAlmostEqual(matched["alpha"], 0.05)


class ViewConstructionTest(unittest.TestCase):
    def test_view_endpoint_counts_are_ordered_v1_v2_v3(self) -> None:
        generator = torch.Generator().manual_seed(81)
        episode = make_episode("e", generator=generator, tokens=120, analysis=30, commentary=30)
        fit = routine_pool(6, seed=82, tokens=120)
        counts = {}
        for name in ("V1", "V2", "V3"):
            view = trm3_g.VIEWS[name]
            stat = trm3_g.build_statistic("M").fit(fit, view)
            ends, _, _, _ = stat.stream(episode, view)
            counts[name] = len(ends)
        self.assertGreater(counts["V1"], counts["V2"])
        self.assertGreater(counts["V2"], counts["V3"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


def _runner_module():
    import importlib.util

    path = ROOT / "scripts" / "research_v4" / "run_detectors_g.py"
    spec = importlib.util.spec_from_file_location("run_detectors_g_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


P0_BATCH = ROOT / "artifacts" / "agent_v2" / "agent_v3_p0" / "batch"
P0_AVAILABLE = (P0_BATCH / "run_summary.json").exists()


@unittest.skipUnless(P0_AVAILABLE, "agent_v3 P0 artifacts absent")
class RunnerSmokeTest(unittest.TestCase):
    """The CLI on the P0 probe: routine arms only, and the probe guard has teeth."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = _runner_module()

    def _argv(self, *extra: str) -> list[str]:
        return [
            "--fit", str(P0_BATCH), "--fit-scenarios", "001,011,016,021",
            "--cal", str(P0_BATCH), "--cal-scenarios", "036,041,066,071",
            "--target", str(P0_BATCH), "--target-scenarios", "001,011,016,021",
            "--view", "V1", "--statistic", "M", "--alpha", "0.10",
            "--h-min-survivors", "6", "--min-bucket-traces", "3",
            "--outputs", "none", *extra,
        ]

    def test_probe_material_requires_the_smoke_flag(self) -> None:
        with self.assertRaises(SystemExit):
            self.runner.main(self._argv())

    def test_smoke_runs_end_to_end(self) -> None:
        self.assertEqual(self.runner.main(self._argv("--normal-only-smoke")), 0)

    def test_overlapping_fit_and_calibration_scenarios_are_refused(self) -> None:
        argv = [
            "--fit", str(P0_BATCH), "--fit-scenarios", "001,011",
            "--cal", str(P0_BATCH), "--cal-scenarios", "001,041",
            "--target", str(P0_BATCH), "--target-scenarios", "016",
            "--h-min-survivors", "2", "--min-bucket-traces", "2",
            "--outputs", "none", "--normal-only-smoke",
        ]
        with self.assertRaises(SystemExit):
            self.runner.main(argv)


class MultiStepEpisodeTest(unittest.TestCase):
    """An episode that alternates channels across agent steps, as Agent v3 traces do."""

    @staticmethod
    def _alternating() -> io_g.GEpisode:
        generator = torch.Generator().manual_seed(91)
        runs = [("analysis", 14), ("commentary", 12), ("analysis", 13), ("commentary", 12), ("final", 60)]
        tags: list[str] = []
        for channel, length in runs:
            tags.extend([channel] * length)
        episode = make_episode("multi", generator=generator, tokens=len(tags))
        episode.channel_tags = tuple(tags)
        return episode

    def test_ordinals_accumulate_across_runs_of_the_same_channel(self) -> None:
        episode = self._alternating()
        fit = routine_pool(6, seed=92, tokens=len(episode.channel_tags))
        view = trm3_g.VIEWS["V1"]
        stat = trm3_g.build_statistic("M").fit(fit, view)
        stream = trm3_g.episode_streams({"M": stat}, [episode], view)["M"][0]
        analysis_ends = [e for e, t in zip(stream.ends, stream.tags) if t == "analysis"]
        analysis_ordinals = [o for o, t in zip(stream.ordinals, stream.tags) if t == "analysis"]
        # two analysis runs of 14 and 13 tokens at w=8 -> 7 + 6 endpoints, one counter
        self.assertEqual(len(analysis_ends), 13)
        self.assertEqual(analysis_ordinals, list(range(13)))
        # no window crosses a run boundary
        for end, tag in zip(stream.ends, stream.tags):
            self.assertTrue(all(episode.channel_tags[end - offset] == tag for offset in range(8)))

    def test_v3_keeps_only_the_final_run(self) -> None:
        episode = self._alternating()
        fit = routine_pool(6, seed=93, tokens=len(episode.channel_tags))
        view = trm3_g.VIEWS["V3"]
        stat = trm3_g.build_statistic("S").fit(fit, view)
        ends, _, tags, _ = stat.stream(episode, view)
        self.assertEqual(set(tags), {"final"})
        self.assertEqual(int(ends.min()), 51 + 7)


class TemporalBlockTest(unittest.TestCase):
    def test_temporal_states_are_grouped_by_trajectory_class(self) -> None:
        generator = torch.Generator().manual_seed(101)
        episodes = [
            make_episode("ex", generator=generator, variant="attack", offset=16,
                         labels={"e_analysis": 20, "trajectory_class": "execution"}, family="fam1"),
            make_episode("cl", generator=generator, variant="clean"),
        ]
        fit = routine_pool(8, seed=102)
        cal = routine_pool(12, seed=103, prefix="c")
        view, config, stat, calibration = fitted_cell(fit, cal, min_survivors=6)
        outputs = score(episodes, stat, calibration, config, view)
        metrics = trm3_g.evaluate_g(
            outputs,
            episodes,
            config,
            view,
            anchors=trm3_g.view_anchors(episodes, view),
            decisions={},
        )
        block = metrics["temporal"]["by_trajectory_class"]
        self.assertIn("execution", block)
        self.assertIn("unlabelled_clean", block)
        self.assertEqual(block["execution"]["episode_count"], 1)
        self.assertIsNotNone(block["execution"]["abstention_rate"])
