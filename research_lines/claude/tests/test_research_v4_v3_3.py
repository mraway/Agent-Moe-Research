"""The v3.3 candidate harness features (docs/research_v4/zoom_v32_improvement_space.md 4).

One class per candidate, so a freeze reviewer can walk the recommendation table and land on
the check:

* **R1** ``--force-h inf`` -- the SYMMETRIC removal of the calibration horizon: reference
  and target both use their own full-path maximum, ``horizon.mode = "unbounded"``,
  survivors / censoring become record-only and gate N3 is marked n/a;
* **R2** ``--statistic Z1`` -- the top-``m`` rare-coordinate concentration statistic, its
  exactness against S, its ``state_dict`` / ``load_state`` round trip (which is what lets
  it run in the two-stage manifest) and its attribution;
* **R3** ``--debounce K`` -- K consecutive looks with ``p <= alpha`` AND ``p_inst <= alpha``
  before CONFIRMED fires, written back as an effective p so the alpha-free machinery is
  untouched;
* **D6** ``--stratify-reference n_kb`` -- per-stratum reference sets, standardisers,
  attainability and manifest keys.

Plus the regression guard the round owes the lead: with every v3.3 switch at its default
the parsed namespace and the scored decisions are the frozen v3.2 ones.

Everything is synthetic; the loader is stubbed exactly as in
``tests/test_research_v4_v3_2.py``.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import math
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io_g, trm3, trm3_g  # noqa: E402

from test_research_v4_v3_2 import (  # noqa: E402
    RUNNER,
    StubLoader,
    episode,
    normal_labels,
    synthetic_batch,
)
from test_research_v4_detectors_g import make_episode, routine_pool  # noqa: E402

torch.set_num_threads(4)


# ---------------------------------------------------------------------------
# R2 -- the Z1 concentration statistic
# ---------------------------------------------------------------------------


class Z1StatisticTest(unittest.TestCase):
    def setUp(self) -> None:
        self.view = trm3_g.VIEWS["V1"]
        self.pool = routine_pool(12, seed=17, tokens=64)
        self.target = routine_pool(4, seed=23, tokens=64, prefix="t")

    #: the synthetic router draws experts near-uniformly (q ~ top_k / E = 0.125), so the
    #: frozen 0.02 cut would leave Omega_rare EMPTY and every score identically zero.  The
    #: tests below therefore widen the cut; the aggregation -- which is what Z1 changes --
    #: is unaffected by where the cut sits.
    RARE = 0.5

    def _fitted(self, **kwargs) -> trm3_g.RareConcentration:
        config = {"window_width": 8, "rare_threshold": self.RARE, **kwargs}
        statistic = trm3_g.build_statistic("Z1", config)
        statistic.fit(self.pool, self.view)
        return statistic

    def _fitted_s(self) -> trm3_g.RareSurprisal:
        statistic = trm3_g.build_statistic(
            "S", {"window_width": 8, "rare_threshold": self.RARE}
        )
        statistic.fit(self.pool, self.view)
        return statistic

    def test_registered_in_the_statistic_registry_with_aliases(self) -> None:
        self.assertIn("Z1", trm3_g.STATISTICS)
        for alias in ("z1", "rare_concentration", "s_top1"):
            self.assertEqual(trm3_g.STATISTIC_ALIASES[alias], "Z1")
        self.assertIsInstance(trm3_g.build_statistic("z1"), trm3_g.RareConcentration)

    def test_per_token_mass_sums_to_the_S_per_token_score(self) -> None:
        """Z1 re-aggregates the SAME mass S sums, so the row sums must agree exactly."""

        s = self._fitted_s()
        z = self._fitted()
        for episode_ in self.target:
            mass = z.per_token(episode_)
            self.assertTrue(
                torch.allclose(mass.sum(dim=1), s.per_token(episode_).reshape(-1), atol=0)
            )

    def test_top_m_equal_to_every_coordinate_recovers_S_bit_for_bit(self) -> None:
        s = self._fitted_s()
        z = self._fitted(top_m=24 * 32)
        for episode_ in self.target:
            ends_s, scores_s, tags_s, _ = s.stream(episode_, self.view)
            ends_z, scores_z, tags_z, _ = z.stream(episode_, self.view)
            self.assertEqual(list(ends_s), list(ends_z))
            self.assertEqual(tags_s, tags_z)
            np.testing.assert_allclose(scores_s, scores_z, rtol=0, atol=1e-12)

    def test_top_1_score_is_the_single_largest_coordinate_contribution(self) -> None:
        z = self._fitted(top_m=1)
        episode_ = self.target[0]
        ends, scores, _, _ = z.stream(episode_, self.view)
        self.assertTrue(len(ends))
        for end, score in list(zip(ends, scores))[:8]:
            top = z.top_coordinates(episode_, int(end), n=1)
            self.assertEqual(len(top), 1)
            # the attribution hook's normaliser is the window width, which is exactly the
            # causal window mean the score is the max over -- so top-1 IS the Z1 score
            self.assertAlmostEqual(float(top[0]["contribution"]), float(score), places=12)

    def test_top_1_never_exceeds_the_S_sum_and_is_usually_strictly_smaller(self) -> None:
        s = self._fitted_s()
        z = self._fitted(top_m=1)
        strictly_smaller = 0
        total = 0
        for episode_ in self.target:
            _, scores_s, _, _ = s.stream(episode_, self.view)
            _, scores_z, _, _ = z.stream(episode_, self.view)
            self.assertTrue(np.all(scores_z <= scores_s + 1e-12))
            strictly_smaller += int(np.sum(scores_z < scores_s - 1e-12))
            total += int(scores_s.size)
        self.assertGreater(strictly_smaller, total // 2)

    def test_state_dict_round_trip_reproduces_the_stream_exactly(self) -> None:
        z = self._fitted(top_m=1)
        state = json.loads(json.dumps(z.state_dict()))
        self.assertEqual(state["config"]["rare_threshold"], self.RARE)
        self.assertEqual(state["kind"], "rare_concentration")
        self.assertEqual(state["top_m"], 1)
        restored = trm3_g.build_statistic("Z1", {}).load_state(state)
        self.assertEqual(restored.top_m, 1)
        self.assertEqual(restored.window_width, 8)
        self.assertEqual(restored.rare_threshold, z.rare_threshold)
        for episode_ in self.target:
            _, before, _, _ = z.stream(episode_, self.view)
            _, after, _, _ = restored.stream(episode_, self.view)
            np.testing.assert_allclose(before, after, rtol=0, atol=0)

    def test_state_dict_carries_top_m_so_a_manifest_cannot_lose_it(self) -> None:
        state = self._fitted(top_m=3).state_dict()
        restored = trm3_g.build_statistic("Z1", {}).load_state(state)
        self.assertEqual(restored.top_m, 3)
        self.assertEqual(restored.config()["top_m"], 3)

    def test_describe_keeps_the_layer_band_the_frozen_assertion_reads(self) -> None:
        block = self._fitted().describe()
        self.assertEqual(block["statistic"], "Z1")
        self.assertEqual(list(block["layers"]), list(trm3_g.ALL_LAYERS))
        self.assertEqual(block["aggregation"], "top_1_sum")

    def test_top_m_must_be_at_least_one(self) -> None:
        with self.assertRaises(ValueError):
            trm3_g.build_statistic("Z1", {"top_m": 0})


# ---------------------------------------------------------------------------
# R1 -- the symmetric horizon removal
# ---------------------------------------------------------------------------


class UnboundedHorizonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.view = trm3_g.VIEWS["V1"]
        generator = torch.Generator().manual_seed(3)
        # deliberately RAGGED lengths, so a finite H censors some paths and not others
        self.fit = [
            make_episode(f"f{i}", generator=generator, tokens=48 + 8 * (i % 5))
            for i in range(12)
        ]
        self.cal = [
            make_episode(f"c{i}", generator=generator, tokens=48 + 8 * (i % 5))
            for i in range(12)
        ]

    def _calibrate(self, force_h):
        # rare_threshold 0.5: the synthetic router is near-uniform, so the frozen 0.02 cut
        # would leave every score identically zero and the maxima indistinguishable
        statistic = trm3_g.build_statistic(
            "S", {"window_width": 8, "rare_threshold": 0.5}
        ).fit(self.fit, self.view)
        stats = {"S": statistic}
        return trm3_g.calibrate_g(
            trm3_g.episode_streams(stats, self.fit, self.view)["S"],
            trm3_g.episode_streams(stats, self.cal, self.view)["S"],
            trm3_g.config_for_g(["S"], alpha=0.10),
            view=self.view,
            statistic="S",
            min_survivors=4,
            force_h=force_h,
            min_bucket_traces=3,
            min_channel_windows=3,
            min_channel_traces=2,
            tag_scope="message",
        )

    def test_is_unbounded_h_accepts_only_the_documented_spellings(self) -> None:
        for value in ("inf", "INF", "infinity", "unbounded", "full", float("inf"),
                      trm3_g.UNBOUNDED_H):
            self.assertTrue(trm3_g.is_unbounded_h(value), value)
        for value in (None, 0, 352, 128, "352"):
            self.assertFalse(trm3_g.is_unbounded_h(value), value)

    def test_cli_parses_inf_into_the_int_sentinel(self) -> None:
        self.assertEqual(RUNNER.force_h_arg("inf"), trm3_g.UNBOUNDED_H)
        self.assertEqual(RUNNER.force_h_arg("unbounded"), trm3_g.UNBOUNDED_H)
        self.assertEqual(RUNNER.force_h_arg("352"), 352)
        with self.assertRaises(argparse.ArgumentTypeError):
            RUNNER.force_h_arg("banana")

    def test_the_horizon_block_marks_the_mode_and_keeps_the_rule_as_a_record(self) -> None:
        block = self._calibrate(trm3_g.UNBOUNDED_H).horizon
        self.assertEqual(block["mode"], "unbounded")
        self.assertTrue(block["unbounded"])
        self.assertEqual(block["censored_paths"], 0)
        self.assertEqual(block["censored_endpoints"], 0)
        self.assertEqual(block["censoring_fraction"], 0.0)
        self.assertEqual(block["n3_gate"], "n/a")
        self.assertIsNone(block["min_survivors_satisfied"])
        # record-only: survivors_at_H degenerates to "every path"
        self.assertEqual(block["survivors_at_H"], block["calibration_paths"])
        # what the min_survivors rule WOULD have said is still there
        self.assertEqual(
            block["rule_H"],
            trm3_g.h_horizon(block_lengths(self.cal, self.fit, self.view), min_survivors=4)["H"],
        )
        self.assertEqual(block["H_effective"], block["length_max"])

    def test_reference_maxima_are_the_full_path_maxima_and_dominate_the_truncated_ones(
        self,
    ) -> None:
        truncated = self._calibrate(12)
        full = self._calibrate(trm3_g.UNBOUNDED_H)
        a = np.sort(truncated.reference.channels["S"].path_maxima)
        b = np.sort(full.reference.channels["S"].path_maxima)
        self.assertEqual(a.size, b.size)
        # a full-path max can only be >= the max over a prefix of the same path
        self.assertTrue(np.all(b >= a - 1e-12))
        self.assertTrue(np.any(b > a + 1e-12))

    def test_no_target_endpoint_is_horizon_censored_under_the_unbounded_horizon(self) -> None:
        statistic = trm3_g.build_statistic(
            "S", {"window_width": 8, "rare_threshold": 0.5}
        ).fit(self.fit, self.view)
        stats = {"S": statistic}
        config = trm3_g.config_for_g(["S"], alpha=0.10)
        target = self.cal
        for force_h, expect_censored in ((40, True), (trm3_g.UNBOUNDED_H, False)):
            calibration = self._calibrate(force_h)
            streams = trm3_g.episode_streams(stats, target, self.view)["S"]
            censored = 0
            emitted = 0
            for stream in streams:
                outputs = trm3_g.score_episode({"S": stream}, calibration, config)
                censored += sum(1 for o in outputs if o.horizon_censored)
                emitted += len(outputs)
            self.assertGreater(emitted, 0)
            self.assertEqual(censored > 0, expect_censored, force_h)

    def test_a_finite_force_h_is_untouched(self) -> None:
        block = self._calibrate(40).horizon
        self.assertNotIn("mode", block)
        self.assertEqual(block["H"], 40)
        self.assertTrue(block["forced"])

    def test_frozen_assertion_reports_unbounded_instead_of_asserting_an_H(self) -> None:
        calibration = self._calibrate(trm3_g.UNBOUNDED_H)
        args = argparse.Namespace(
            expect_h=None, tag_scope="message", view="V1", normal_only_smoke=True,
            dev_smoke=False, attainability_floor=1, expect_all_layers=True,
            expect_n_reference=None,
        )
        statistic = trm3_g.build_statistic(
            "S", {"window_width": 8, "rare_threshold": 0.5}
        ).fit(self.fit, self.view)
        rows = RUNNER.frozen_assertions(
            args, key="S", width=8, calibration=calibration,
            config=trm3_g.config_for_g(["S"], alpha=0.10), statistic=statistic,
        )
        row = next(r for r in rows if r["check"] == "horizon_H")
        self.assertEqual(row["observed"], "unbounded")
        self.assertTrue(row["ok"])
        self.assertIn("N3", row["note"])
        # asking for a numeric H at the same time is a contradiction and must fail
        args.expect_h = 352
        rows = RUNNER.frozen_assertions(
            args, key="S", width=8, calibration=calibration,
            config=trm3_g.config_for_g(["S"], alpha=0.10), statistic=statistic,
        )
        self.assertFalse(next(r for r in rows if r["check"] == "horizon_H")["ok"])


def block_lengths(cal, fit, view):
    statistic = trm3_g.build_statistic(
        "S", {"window_width": 8, "rare_threshold": 0.5}
    ).fit(fit, view)
    streams = trm3_g.episode_streams({"S": statistic}, cal, view)["S"]
    standardiser = trm3_g.fit_channel_standardiser(
        trm3_g.episode_streams({"S": statistic}, fit, view)["S"],
        min_bucket_traces=3, min_channel_windows=3, min_channel_traces=2,
    )
    return [len(standardiser.standardize(s)) for s in streams]


# ---------------------------------------------------------------------------
# R3 -- the p_inst debounce
# ---------------------------------------------------------------------------


def brute_force_debounce(p_running, p_inst, runs, alpha):
    """The rule as ``zoom_v32_fusion.debounce`` states it, evaluated at one alpha."""

    fire = [
        (p_running[i] <= alpha) and (p_inst[i] <= alpha) for i in range(len(p_running))
    ]
    return [
        i
        for i in range(len(fire))
        if i >= runs - 1 and all(fire[j] for j in range(i - runs + 1, i + 1))
    ]


class DebounceTest(unittest.TestCase):
    def test_effective_p_reproduces_the_rule_at_every_alpha(self) -> None:
        rng = np.random.default_rng(11)
        for _ in range(200):
            n = int(rng.integers(1, 30))
            p_inst = np.round(rng.uniform(0.0, 1.0, n), 3)
            # the registered p is a running MAX statistic, hence non-increasing
            p_running = np.minimum.accumulate(np.round(rng.uniform(0.0, 1.0, n), 3))
            for runs in (1, 2, 3, 5):
                effective = trm3_g.debounce_p(p_running, p_inst, runs)
                for alpha in (0.02, 0.05, 0.10, 0.25, 0.5):
                    got = [i for i, v in enumerate(effective) if v <= alpha]
                    want = brute_force_debounce(list(p_running), list(p_inst), runs, alpha)
                    self.assertEqual(got, want, (n, runs, alpha))

    def test_the_first_k_minus_one_looks_cannot_be_decided(self) -> None:
        effective = trm3_g.debounce_p([0.0] * 6, [0.0] * 6, 3)
        self.assertEqual(effective[:2], [1.0, 1.0])
        self.assertEqual(effective[2:], [0.0] * 4)

    def test_a_shorter_path_than_k_never_alarms(self) -> None:
        self.assertEqual(trm3_g.debounce_p([0.0, 0.0], [0.0, 0.0], 3), [1.0, 1.0])

    def test_the_alarm_look_is_the_last_of_the_run(self) -> None:
        # p_inst dips at looks 2 and 3; with K = 2 the alarm is look 3, not look 2
        effective = trm3_g.debounce_p(
            [0.5, 0.09, 0.09, 0.09], [0.5, 0.5, 0.09, 0.09], 2
        )
        self.assertEqual([i for i, v in enumerate(effective) if v <= 0.10], [3])

    def test_debounced_alarms_are_a_subset_of_the_undebounced_ones(self) -> None:
        rng = np.random.default_rng(5)
        for _ in range(100):
            n = int(rng.integers(2, 40))
            p_inst = rng.uniform(0.0, 1.0, n)
            p_running = np.minimum.accumulate(rng.uniform(0.0, 1.0, n))
            base = {i for i, v in enumerate(p_running) if v <= 0.10}
            for runs in (2, 3):
                got = {
                    i
                    for i, v in enumerate(trm3_g.debounce_p(p_running, p_inst, runs))
                    if v <= 0.10
                }
                self.assertTrue(got <= base)

    def test_apply_debounce_rewrites_p_fused_and_the_state_only(self) -> None:
        config = trm3_g.config_for_g(["S"], alpha=0.10)
        outputs = [
            trm3.TokenOutput(
                k=i, end=10 * i, p={"S": 0.01}, p_fused=0.01, state=trm3.STATE_CONFIRMED,
                temporal_state=trm3.TEMPORAL_UNCERTAIN, e0=0, duration=1, attribution="S",
                regime_flag=None, censored=False, earliest_decision_end=None,
                episode_index=0, remaining_budget=0.0, calibration_version="v",
            )
            for i in range(4)
        ]
        frozen = [copy.deepcopy(o) for o in outputs]
        p_inst = {0: 0.9, 10: 0.9, 20: 0.01, 30: 0.01}
        block = trm3_g.apply_debounce(outputs, p_inst, config, 2)
        self.assertTrue(block["applied"])
        states = [o.state for o in outputs]
        self.assertEqual(
            states,
            [trm3.STATE_SILENT, trm3.STATE_SILENT, trm3.STATE_SILENT, trm3.STATE_CONFIRMED],
        )
        for before, after in zip(frozen, outputs):
            self.assertEqual(before.end, after.end)
            self.assertEqual(before.p, after.p)  # the per-channel p is untouched
            self.assertEqual(before.temporal_state, after.temporal_state)

    def test_k_equal_to_one_is_a_no_op(self) -> None:
        config = trm3_g.config_for_g(["S"], alpha=0.10)
        outputs = [
            trm3.TokenOutput(
                k=0, end=0, p={"S": 0.01}, p_fused=0.01, state=trm3.STATE_CONFIRMED,
                temporal_state=trm3.TEMPORAL_UNCERTAIN, e0=0, duration=1, attribution="S",
                regime_flag=None, censored=False, earliest_decision_end=None,
                episode_index=0, remaining_budget=0.0, calibration_version="v",
            )
        ]
        block = trm3_g.apply_debounce(outputs, {0: 0.9}, config, 1)
        self.assertFalse(block["applied"])
        self.assertEqual(outputs[0].p_fused, 0.01)
        self.assertEqual(outputs[0].state, trm3.STATE_CONFIRMED)


# ---------------------------------------------------------------------------
# D6 -- n_kb stratified reference sets
# ---------------------------------------------------------------------------


class StratifiedReferenceTest(unittest.TestCase):
    def test_stratum_label_is_explicit_about_a_missing_covariate(self) -> None:
        self.assertEqual(trm3_g.stratum_label("n_kb", 2), "n_kb=2")
        self.assertEqual(trm3_g.stratum_label("n_kb", None), "n_kb=unknown")
        with self.assertRaises(ValueError):
            trm3_g.stratum_label("genre", 1)

    def test_stratify_indices_groups_positions_in_label_order(self) -> None:
        self.assertEqual(
            trm3_g.stratify_indices(["n_kb=1", "n_kb=0", "n_kb=1", "n_kb=2"]),
            {"n_kb=0": [1], "n_kb=1": [0, 2], "n_kb=2": [3]},
        )

    def test_n_kb_map_reads_expected_article_ids_from_the_subset_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "subset.json"
            path.write_text(
                json.dumps(
                    {
                        "scenarios": [
                            {"pair_group_id": "a", "base_task_id": "a",
                             "factory": {"expected_article_ids": []}},
                            {"pair_group_id": "b", "factory": {"expected_article_ids": ["k1"]}},
                            {"pair_group_id": "c",
                             "factory": {"expected_article_ids": ["k1", "k2"]}},
                            {"pair_group_id": "d", "factory": {}},
                        ]
                    }
                )
            )
            self.assertEqual(
                RUNNER.n_kb_map_from_config(path), {"a": 0, "b": 1, "c": 2}
            )

    def test_stratum_groups_without_strata_is_one_group_covering_everything(self) -> None:
        spec = {"fold": 0, "fit": [1, 2, 3], "reference": [4, 5]}
        groups = RUNNER.stratum_groups(spec, [10, 11, 12, 13], None)
        self.assertEqual(len(groups), 1)
        self.assertIsNone(groups[0]["label"])
        self.assertEqual(groups[0]["eval_index"], [0, 1, 2, 3])
        self.assertEqual(groups[0]["fit_index"], [0, 1, 2])
        self.assertEqual(groups[0]["reference_index"], [0, 1])

    def test_stratum_groups_partitions_every_pool_by_the_covariate(self) -> None:
        generator = torch.Generator().manual_seed(2)
        def ep(name, scenario):
            return episode(name, generator=generator, scenario=scenario, tokens=48)
        spec = {
            "fold": 0,
            "fit": [ep("f0", "s0"), ep("f1", "s1")],
            "reference": [ep("r0", "s0"), ep("r1", "s1")],
        }
        evaluation = [ep("e0", "s0"), ep("e1", "s1"), ep("e2", "s0")]
        strata = {"s0": "n_kb=0", "s1": "n_kb=1"}
        groups = RUNNER.stratum_groups(spec, evaluation, strata)
        self.assertEqual([g["label"] for g in groups], ["n_kb=0", "n_kb=1"])
        self.assertEqual(groups[0]["eval_index"], [0, 2])
        self.assertEqual(groups[1]["eval_index"], [1])
        # every eval index is covered exactly once
        seen = sorted(i for g in groups for i in g["eval_index"])
        self.assertEqual(seen, [0, 1, 2])

    def test_a_stratum_with_no_reference_material_is_a_hard_refusal(self) -> None:
        generator = torch.Generator().manual_seed(2)
        def ep(name, scenario):
            return episode(name, generator=generator, scenario=scenario, tokens=48)
        spec = {"fold": 1, "fit": [ep("f0", "s0")], "reference": [ep("r0", "s0")]}
        with self.assertRaises(SystemExit) as caught:
            RUNNER.stratum_groups(spec, [ep("e0", "s9")], {"s0": "n_kb=0", "s9": "n_kb=2"})
        self.assertIn("no fitting or reference material", str(caught.exception))

    def test_eval_weighted_alpha_eff(self) -> None:
        blocks = {
            "a": {"alpha_eff": 0.10, "n_eval": 10},
            "b": {"alpha_eff": 0.05, "n_eval": 30},
        }
        self.assertAlmostEqual(RUNNER._eval_weighted_alpha_eff(blocks), 0.0625)


# ---------------------------------------------------------------------------
# end to end through the CLI
# ---------------------------------------------------------------------------


class _V33Harness:
    """The stubbed 24-scenario batch every end-to-end v3.3 test runs against."""

    def setUp(self) -> None:
        self.pool = synthetic_batch(scenarios=24, seed=61, tokens=64)
        self.stub = StubLoader(self.pool)
        self._saved = (
            RUNNER.load_pool, RUNNER.target_scenarios, RUNNER.normal_trace_manifest
        )
        RUNNER.load_pool = self.stub.load_pool
        RUNNER.target_scenarios = self.stub.target_scenarios
        RUNNER.normal_trace_manifest = self.stub.normal_trace_manifest
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # a subset config carrying the n_kb covariate for the 24 synthetic scenarios
        self.config = self.root / "subset.json"
        self.config.write_text(
            json.dumps(
                {
                    "scenarios": [
                        {
                            "pair_group_id": f"s-{i:03d}",
                            "factory": {"expected_article_ids": ["k"] * (i % 2)},
                        }
                        for i in range(24)
                    ]
                }
            )
        )

    def tearDown(self) -> None:
        RUNNER.load_pool, RUNNER.target_scenarios, RUNNER.normal_trace_manifest = self._saved
        self.tmp.cleanup()

    def _base(self, *extra: str) -> list[str]:
        return [
            "--target", str(self.root / "batch"),
            "--cal-from-target", "--cal-folds", "3",
            "--view", "V1", "--statistic", "S",
            "--alpha", "0.10", "--window-s", "8",
            "--h-min-survivors", "3", "--min-bucket-traces", "3",
            "--min-channel-windows", "3", "--min-channel-traces", "2",
            "--output-root", str(self.root / "out"),
            "--bootstrap-replicates", "50",
            *extra,
        ]

    def _result(self, name: str) -> dict:
        return json.loads((self.root / "out" / name / "result.json").read_text())


class RunnerV33Test(_V33Harness, unittest.TestCase):
    # -- defaults ---------------------------------------------------------
    def test_every_v3_3_switch_defaults_to_off(self) -> None:
        args = RUNNER._args(["--target", "x"])
        self.assertIsNone(args.force_h)
        self.assertEqual(args.debounce, 1)
        self.assertEqual(args.top_m, 1)
        self.assertEqual(args.stratify_reference, "none")
        self.assertIsNone(args.stratify_config)
        self.assertIsNone(args.window_z1)
        self.assertIsNone(RUNNER.force_h_value(args))
        self.assertIsNone(RUNNER.force_h_record(args))

    def test_force_h_record_keeps_the_frozen_integer_and_names_the_unbounded_one(self) -> None:
        args = RUNNER._args(["--target", "x", "--force-h", "352"])
        self.assertEqual(RUNNER.force_h_record(args), 352)
        self.assertEqual(RUNNER.force_h_value(args), 352)
        args = RUNNER._args(["--target", "x", "--force-h", "inf"])
        self.assertEqual(RUNNER.force_h_record(args), "inf")
        self.assertEqual(RUNNER.force_h_value(args), trm3_g.UNBOUNDED_H)

    def test_the_run_records_the_v3_3_switches_it_ran_with(self) -> None:
        code = RUNNER.main(
            self._base(
                "--normal-only-smoke", "--run-name", "rec", "--outputs", "primary",
                "--force-h", "inf", "--debounce", "2",
            )
        )
        self.assertEqual(code, 0)
        block = self._result("rec")["calibration_design"]
        self.assertEqual(block["force_h"], "inf")
        self.assertEqual(block["v3_3"]["debounce"], 2)
        self.assertEqual(block["v3_3"]["horizon_mode"], "unbounded")
        self.assertEqual(block["v3_3"]["stratify_reference"]["key"], "none")

    # -- R1 through the CLI ------------------------------------------------
    def test_unbounded_horizon_removes_every_censored_path(self) -> None:
        RUNNER.main(
            self._base("--normal-only-smoke", "--run-name", "hinf", "--outputs", "primary",
                       "--force-h", "inf")
        )
        folds = self._result("hinf")["cells"]["S"]["folds"]
        for block in folds.values():
            self.assertEqual(block["horizon"]["mode"], "unbounded")
            self.assertEqual(block["censored_paths"], 0)
            self.assertEqual(block["horizon"]["n3_gate"], "n/a")

    # -- R2 through the CLI ------------------------------------------------
    def test_z1_runs_as_a_cell_and_survives_the_two_stage_manifest(self) -> None:
        code = RUNNER.main(
            self._base(
                "--stage", "calibrate", "--normal-only-smoke", "--statistic", "Z1,P",
                "--run-name", "z1cal", "--outputs", "primary", "--force-h", "40",
            )
        )
        self.assertEqual(code, 0)
        manifest = self.root / "out" / "z1cal" / "threshold_manifest.json"
        payload = json.loads(manifest.read_text())
        cell = payload["folds"]["0"]["cells"]["Z1"]
        self.assertEqual(cell["statistics"]["Z1"]["kind"], "rare_concentration")
        self.assertEqual(cell["statistics"]["Z1"]["top_m"], 1)
        code = RUNNER.main(
            self._base(
                "--stage", "score", "--dev-smoke", "--statistic", "Z1,P",
                "--compare-statistic", "P",
                "--threshold-manifest", str(manifest),
                "--run-name", "z1score", "--outputs", "primary", "--force-h", "40",
            )
        )
        self.assertEqual(code, 0)
        scored = self._result("z1score")["cells"]["Z1"]
        self.assertTrue(all(b["restored_from_manifest"] for b in scored["folds"].values()))

    def test_z1_top_m_reaching_every_coordinate_reproduces_the_S_cell(self) -> None:
        for name, extra in (
            ("sref", ["--statistic", "S"]),
            ("zall", ["--statistic", "Z1", "--top-m", str(24 * 32)]),
        ):
            RUNNER.main(
                self._base("--normal-only-smoke", "--run-name", name, "--outputs", "primary",
                           "--force-h", "40", *extra)
            )
        s = self._result("sref")["cells"]["S"]["metrics"]["far"]
        z = self._result("zall")["cells"]["Z1"]["metrics"]["far"]
        self.assertEqual(s["all"]["far"], z["all"]["far"])
        self.assertEqual(s["filtered"]["far"], z["filtered"]["far"])

    # -- R3 through the CLI ------------------------------------------------
    def test_debounce_can_only_remove_alarms(self) -> None:
        for name, runs in (("d1", "1"), ("d2", "2")):
            RUNNER.main(
                self._base("--normal-only-smoke", "--run-name", name, "--outputs", "primary",
                           "--force-h", "40", "--debounce", runs)
            )
        one = self._result("d1")["cells"]["S"]["metrics"]["far"]["all"]
        two = self._result("d2")["cells"]["S"]["metrics"]["far"]["all"]
        self.assertEqual(one["episode_count"], two["episode_count"])
        self.assertLessEqual(two["alarm_count"], one["alarm_count"])

    def test_debounce_1_is_byte_identical_to_the_run_without_the_switch(self) -> None:
        RUNNER.main(
            self._base("--normal-only-smoke", "--run-name", "nod", "--outputs", "primary",
                       "--force-h", "40")
        )
        RUNNER.main(
            self._base("--normal-only-smoke", "--run-name", "d1x", "--outputs", "primary",
                       "--force-h", "40", "--debounce", "1")
        )
        a = self._result("nod")["cells"]["S"]["metrics"]
        b = self._result("d1x")["cells"]["S"]["metrics"]
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    # -- D6 through the CLI ------------------------------------------------
    def test_stratified_reference_reports_per_stratum_n_cal_and_attainability(self) -> None:
        code = RUNNER.main(
            self._base(
                "--normal-only-smoke", "--run-name", "strat", "--outputs", "primary",
                "--force-h", "40", "--stratify-reference", "n_kb",
                "--stratify-config", str(self.config),
            )
        )
        self.assertEqual(code, 0)
        payload = self._result("strat")
        block = payload["calibration_design"]["v3_3"]["stratify_reference"]
        self.assertEqual(block["key"], "n_kb")
        self.assertEqual(block["scenarios_by_stratum"], {"n_kb=0": 12, "n_kb=1": 12})
        for fold in payload["cells"]["S"]["folds"].values():
            strata = fold["strata"]["per_stratum"]
            self.assertEqual(sorted(strata), ["n_kb=0", "n_kb=1"])
            self.assertEqual(
                fold["n_cal"], sum(int(b["n_cal"]) for b in strata.values())
            )
            for row in strata.values():
                self.assertIn("attainability", row)
                self.assertIn("attainable_rank", row)
                self.assertIn("far", row)
            self.assertTrue(fold["attainability"]["stratified"])

    def test_every_episode_is_scored_exactly_once_under_stratification(self) -> None:
        RUNNER.main(
            self._base(
                "--normal-only-smoke", "--run-name", "strat2", "--outputs", "primary",
                "--force-h", "40", "--stratify-reference", "n_kb",
                "--stratify-config", str(self.config),
            )
        )
        far = self._result("strat2")["cells"]["S"]["metrics"]["far"]["all"]
        normals = [e for e in self.pool if e.variant in io_g.NORMAL_VARIANTS]
        self.assertEqual(far["episode_count"], len(normals))

    def test_the_manifest_keys_the_calibration_by_stratum(self) -> None:
        RUNNER.main(
            self._base(
                "--stage", "calibrate", "--normal-only-smoke", "--run-name", "stratcal",
                "--outputs", "primary", "--force-h", "40",
                "--stratify-reference", "n_kb", "--stratify-config", str(self.config),
            )
        )
        payload = json.loads(
            (self.root / "out" / "stratcal" / "threshold_manifest.json").read_text()
        )
        keys = sorted(payload["folds"]["0"]["cells"]["S"]["calibrations"])
        self.assertEqual(keys, ["S@n_kb=0", "S@n_kb=1"])

    def test_stage_2_refuses_a_manifest_calibrated_with_a_different_stratification(
        self,
    ) -> None:
        RUNNER.main(
            self._base(
                "--stage", "calibrate", "--normal-only-smoke", "--run-name", "plaincal",
                "--outputs", "primary", "--force-h", "40",
            )
        )
        manifest = self.root / "out" / "plaincal" / "threshold_manifest.json"
        with self.assertRaises(SystemExit) as caught:
            RUNNER.main(
                self._base(
                    "--stage", "score", "--dev-smoke",
                    "--threshold-manifest", str(manifest),
                    "--run-name", "mismatch", "--outputs", "primary", "--force-h", "40",
                    "--stratify-reference", "n_kb", "--stratify-config", str(self.config),
                )
            )
        self.assertIn("same --stratify-reference", str(caught.exception))

    def test_the_full_v3_3_bundle_runs_end_to_end(self) -> None:
        code = RUNNER.main(
            self._base(
                "--normal-only-smoke", "--run-name", "bundle", "--outputs", "primary",
                "--statistic", "Z1,P", "--compare-statistic", "P",
                "--force-h", "inf", "--debounce", "2",
                "--stratify-reference", "n_kb", "--stratify-config", str(self.config),
            )
        )
        self.assertEqual(code, 0)
        payload = self._result("bundle")
        self.assertEqual(payload["calibration_design"]["v3_3"]["debounce"], 2)
        for fold in payload["cells"]["Z1"]["folds"].values():
            self.assertEqual(fold["horizon"]["mode"], "unbounded")
            self.assertIn("strata", fold)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


# ---------------------------------------------------------------------------
# the read-out switches the v3.3 development measurement needs
# ---------------------------------------------------------------------------


class ReadoutSwitchesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pool = synthetic_batch(scenarios=24, seed=61, tokens=64)
        self.stub = StubLoader(self.pool)
        self._saved = (
            RUNNER.load_pool, RUNNER.target_scenarios, RUNNER.normal_trace_manifest
        )
        RUNNER.load_pool = self.stub.load_pool
        RUNNER.target_scenarios = self.stub.target_scenarios
        RUNNER.normal_trace_manifest = self.stub.normal_trace_manifest
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        RUNNER.load_pool, RUNNER.target_scenarios, RUNNER.normal_trace_manifest = self._saved
        self.tmp.cleanup()

    def _base(self, *extra: str) -> list[str]:
        return [
            "--target", str(self.root / "batch"),
            "--cal-from-target", "--cal-folds", "3",
            "--view", "V1", "--statistic", "S", "--compare-statistic", "P",
            "--alpha", "0.10", "--window-s", "8", "--window-p", "8",
            "--h-min-survivors", "3", "--min-bucket-traces", "3",
            "--min-channel-windows", "3", "--min-channel-traces", "2",
            "--output-root", str(self.root / "out"),
            "--bootstrap-replicates", "50", "--force-h", "40",
            "--outputs", "primary", "--dev-smoke",
            "--anchor", "x", "--hit-window", "e_view_to_anchor_plus_h",
            *extra,
        ]

    def _result(self, name: str) -> dict:
        return json.loads((self.root / "out" / name / "result.json").read_text())

    def test_recall_horizons_defaults_to_the_frozen_tuple(self) -> None:
        args = RUNNER._args(["--target", "x"])
        self.assertEqual(RUNNER.recall_horizons(args), trm3_g.V32_RECALL_HORIZONS)
        self.assertEqual(RUNNER.compare_horizons(args), ())

    def test_recall_horizons_must_keep_the_primary_horizon(self) -> None:
        args = RUNNER._args(["--target", "x", "--recall-horizons", "8,32"])
        with self.assertRaises(SystemExit):
            RUNNER.recall_horizons(args)

    def test_recall_horizons_adds_the_requested_rows(self) -> None:
        RUNNER.main(
            self._base("--run-name", "rh", "--recall-horizons", "16,32,64,full")
        )
        recall = self._result("rh")["cells"]["S"]["metrics"]["positives_anchored"]["recall"]
        self.assertIn("penalty_plus_64", recall)
        self.assertIn("penalty_plus_full", recall)
        self.assertNotIn("penalty_plus_8", recall)

    def test_compare_horizons_adds_a_descriptive_delta_block_only(self) -> None:
        RUNNER.main(
            self._base("--run-name", "ch", "--recall-horizons", "16,32,64,full",
                       "--compare-horizons", "32,64")
        )
        payload = self._result("ch")
        block = payload["comparison_anchored_horizons"]
        self.assertEqual(sorted(k for k in block if k != "rule"), ["32", "64"])
        # the registered H1 block is untouched and still at +16
        self.assertEqual(payload["comparison_anchored"]["horizon"], 16)
        self.assertIn("descriptive only", block["rule"])

    def test_far_episode_census_covers_every_scored_normal_exactly_once(self) -> None:
        RUNNER.main(self._base("--run-name", "cen", "--far-episode-census"))
        payload = self._result("cen")
        census = payload["cells"]["S"]["far_episode_census"]
        far = payload["cells"]["S"]["metrics"]["far"]["all"]
        self.assertEqual(len(census["rows"]), far["episode_count"])
        self.assertEqual(
            sum(1 for r in census["rows"] if r["alarm"]), far["alarm_count"]
        )
        self.assertEqual(len({r["key"] for r in census["rows"]}), len(census["rows"]))
        for row in census["rows"]:
            self.assertIn(row["arm"], io_g.NORMAL_VARIANTS)

    def test_no_census_key_without_the_switch(self) -> None:
        RUNNER.main(self._base("--run-name", "nocen"))
        self.assertNotIn("far_episode_census", self._result("nocen")["cells"]["S"])
        self.assertNotIn("comparison_anchored_horizons", self._result("nocen"))


# ---------------------------------------------------------------------------
# round 4 -- lead ruling 6.4: the EXACT split-conformal band gate F1 is judged against
# ---------------------------------------------------------------------------


#: (n_cal, n_eval, alarms) of the three G-dev folds of the recommended cell
#: (Z1 . H=inf . debounce 1), read off `v3_3_dev/stage2_hinf_nostrat_d1_z1/result.json`.
#: With the rotation (eval k / fit k+1 / reference k+2) fold k's n_cal is fold (k+2)'s
#: filtered n_eval, which is exactly what these triples say: 104 / 95 / 94.
G_DEV_FOLDS = ((104, 95, 8), (95, 94, 9), (94, 104, 7))

#: (n_eval, n_cal, rank) -> acceptance counts, recomputed by the lead in ruling 6.4 item 1
#: and item 4.  The first three rows are G-dev, the last three the published G-conf batch.
RULING_BANDS = {
    (95, 104, 10): (3, 18),
    (94, 95, 9): (2, 18),
    (104, 94, 9): (3, 19),
    (180, 172, 17): (8, 30),
    (171, 180, 18): (8, 29),
    (172, 171, 17): (7, 29),
}


def _exact_conformal_band(n_eval: int, n_cal: int, rank: int, level=Fraction(95, 100)):
    """The beta-binomial acceptance region in EXACT rational arithmetic, as a cross-check.

    ``P(X = c) = C(n, c) * B(c + a, n - c + b) / B(a, b)`` with integer ``a = rank`` and
    ``b = n_cal + 1 - rank`` is a ratio of factorials, so it is a Fraction with no rounding
    at all.
    """

    a, b, n = rank, n_cal + 1 - rank, n_eval
    norm = Fraction(
        math.factorial(a + b - 1), math.factorial(a - 1) * math.factorial(b - 1)
    )
    pmf = [
        Fraction(math.comb(n, c))
        * Fraction(
            math.factorial(c + a - 1) * math.factorial(n - c + b - 1),
            math.factorial(n + a + b - 1),
        )
        * norm
        for c in range(n + 1)
    ]
    tail = (1 - level) / 2
    k_low = next(c for c in range(n + 1) if sum(pmf[: c + 1]) > tail)
    k_high = max(c for c in range(n + 1) if sum(pmf[c:]) > tail)
    return k_low, k_high, float(sum(pmf[k_low : k_high + 1]))


class ConformalBandTest(unittest.TestCase):
    """``trm3_g.conformal_far_band``: the null gate F1's per-fold arm is actually judged on.

    rev2 found +-0.03 was never calibrated (null pass 0.187); rev3's binomial band was
    itself too narrow (true coverage 0.87); rev4 registers the exact split-conformal
    beta-binomial.
    """

    def test_it_reproduces_the_bands_the_lead_ruling_states(self) -> None:
        for (n_eval, n_cal, rank), expected in RULING_BANDS.items():
            band = trm3_g.conformal_far_band(n_eval, n_cal, rank)
            with self.subTest(n_eval=n_eval, n_cal=n_cal, rank=rank):
                self.assertEqual((band["k_low"], band["k_high"]), expected)
                self.assertGreater(band["coverage"], 0.95)
                self.assertEqual(band["far_low"], expected[0] / n_eval)
                self.assertEqual(band["far_high"], expected[1] / n_eval)
                self.assertEqual(band["alpha_eff"], rank / (n_cal + 1))

    def test_it_matches_an_exact_rational_reference_on_the_g_dev_fold_shapes(self) -> None:
        for n_cal, n_eval, _ in G_DEV_FOLDS:
            rank = trm3_g.attainable_rank(n_cal, 0.10)["rank"]
            band = trm3_g.conformal_far_band(n_eval, n_cal, rank)
            k_low, k_high, coverage = _exact_conformal_band(n_eval, n_cal, rank)
            with self.subTest(n_cal=n_cal, n_eval=n_eval):
                self.assertEqual((band["k_low"], band["k_high"]), (k_low, k_high))
                self.assertAlmostEqual(band["coverage"], coverage, places=12)

    def test_it_matches_a_200k_simulation_of_the_construction_it_models(self) -> None:
        """The lead's own check: threshold = rank-th largest of n_cal uniforms."""

        n_eval, n_cal, rank, reps = 95, 104, 10, 200_000
        rng = np.random.default_rng(20260908)
        counts = []
        done = 0
        while done < reps:
            take = min(20_000, reps - done)
            cal = rng.random((take, n_cal))
            ev = rng.random((take, n_eval))
            threshold = np.partition(cal, n_cal - rank, axis=1)[:, n_cal - rank]
            counts.append((ev >= threshold[:, None]).sum(axis=1))
            done += take
        counts = np.concatenate(counts)
        band = trm3_g.conformal_far_band(n_eval, n_cal, rank)
        self.assertEqual((band["k_low"], band["k_high"]), (3, 18))
        simulated = float(
            ((counts >= band["k_low"]) & (counts <= band["k_high"])).mean()
        )
        self.assertAlmostEqual(simulated, band["coverage"], delta=0.005)
        self.assertAlmostEqual(counts.std() / n_eval, band["sd"] / n_eval, delta=0.001)
        self.assertAlmostEqual(band["sd"] / n_eval, 0.0414, places=3)

    def test_the_region_is_exactly_the_equal_tailed_rule(self) -> None:
        n_eval, n_cal, rank = 40, 30, 3
        band = trm3_g.conformal_far_band(n_eval, n_cal, rank)
        a, b = rank, n_cal + 1 - rank
        norm = Fraction(
            math.factorial(a + b - 1), math.factorial(a - 1) * math.factorial(b - 1)
        )
        pmf = [
            Fraction(math.comb(n_eval, c))
            * Fraction(
                math.factorial(c + a - 1) * math.factorial(n_eval - c + b - 1),
                math.factorial(n_eval + a + b - 1),
            )
            * norm
            for c in range(n_eval + 1)
        ]
        tail = Fraction(25, 1000)
        for c in range(n_eval + 1):
            inside = band["k_low"] <= c <= band["k_high"]
            accepted = sum(pmf[: c + 1]) > tail and sum(pmf[c:]) > tail
            self.assertEqual(inside, accepted, c)

    def test_it_is_wider_than_the_binomial_band_it_replaces(self) -> None:
        """rev3's band had true coverage 0.87: the threshold is estimated, not fixed."""

        band = trm3_g.conformal_far_band(95, 104, 10)
        p = 10 / 105
        binomial_sd = math.sqrt(p * (1 - p) * 95)
        self.assertGreater(band["sd"], binomial_sd)
        # variance ratio (n_cal + 1 + n_eval) / (n_cal + 2)
        self.assertAlmostEqual(
            (band["sd"] / binomial_sd) ** 2, (104 + 1 + 95) / (104 + 2), places=9
        )
        # and wider than the preregistered +-0.03 in both directions
        self.assertLess(band["far_low"], p - 0.03)
        self.assertGreater(band["far_high"], p + 0.03)

    def test_an_empty_denominator_has_no_band(self) -> None:
        band = trm3_g.conformal_far_band(0, 104, 10)
        for key in ("k_low", "k_high", "far_low", "far_high", "coverage"):
            self.assertIsNone(band[key])
        self.assertEqual(band["n_eval"], 0)
        self.assertEqual(band["rank"], 10)

    def test_the_degenerate_ranks_collapse_onto_the_edges(self) -> None:
        # rank = n_cal makes the threshold the SMALLEST calibration score: almost everything
        # alarms; rank = 1 makes it the largest: almost nothing does
        wide = trm3_g.conformal_far_band(5, 4, 4)
        narrow = trm3_g.conformal_far_band(5, 4, 1)
        self.assertEqual(narrow["k_low"], 0)
        self.assertEqual(wide["k_high"], 5)
        self.assertLess(narrow["mean"], wide["mean"])

    def test_impossible_arguments_are_refused(self) -> None:
        for args in (
            (95, 104, 0),  # rank below 1
            (95, 104, 105),  # rank above n_cal
            (95, 104, 1.5),  # rank not an integer
            (95, 0, 1),  # empty calibration set
            (-1, 104, 10),  # negative denominator
        ):
            with self.assertRaises(ValueError):
                trm3_g.conformal_far_band(*args)
        for level in (0.0, 1.0, 1.5):
            with self.assertRaises(ValueError):
                trm3_g.conformal_far_band(95, 104, 10, level=level)

    def test_it_is_exported_and_the_binomial_band_is_gone(self) -> None:
        self.assertIn("conformal_far_band", trm3_g.__all__)
        self.assertIn("pooled_stratum_far_band", trm3_g.__all__)
        self.assertEqual(trm3_g.CONFORMAL_BAND_LEVEL, 0.95)
        self.assertFalse(hasattr(trm3_g, "binomial_far_band"))
        self.assertNotIn("binomial_far_band", trm3_g.__all__)


#: the G-dev n_kb-stratified cell, read off `v3_3_dev/stage2_hinf_nkb_d1_z1/result.json`:
#: {fold: {stratum: (n_eval_filtered, n_cal, rank)}}, cal fold = (k + 2) % 3.
G_DEV_STRATA = {
    0: {"n_kb=0": (19, 21, 2), "n_kb=1": (46, 50, 5), "n_kb=2": (30, 33, 3)},
    1: {"n_kb=0": (18, 19, 2), "n_kb=1": (46, 46, 4), "n_kb=2": (30, 30, 3)},
    2: {"n_kb=0": (21, 18, 1), "n_kb=1": (50, 46, 4), "n_kb=2": (33, 30, 3)},
}


def _g_dev_stratified_specs() -> dict[int, dict]:
    return {
        fold: {
            "cal_fold": (fold + 2) % 3,
            "n_eval_by_stratum": {lab: row[0] for lab, row in strata.items()},
            "strata": {
                lab: {"n_cal": row[1], "rank": row[2]} for lab, row in strata.items()
            },
        }
        for fold, strata in G_DEV_STRATA.items()
    }


class PooledStratumBandTest(unittest.TestCase):
    """``trm3_g.pooled_stratum_far_band``: the MC null gate VAL1 (a) is judged against."""

    UNSTRATIFIED = {
        0: {"cal_fold": 2, "n_cal": 104, "rank": 10, "n_eval_by_stratum": {"_pooled": 95}},
        1: {"cal_fold": 0, "n_cal": 95, "rank": 9, "n_eval_by_stratum": {"_pooled": 94}},
        2: {"cal_fold": 1, "n_cal": 94, "rank": 9, "n_eval_by_stratum": {"_pooled": 104}},
    }

    def test_a_single_stratum_reduces_to_the_exact_per_fold_band(self) -> None:
        """One stratum + one threshold per fold == the closed form, within MC error."""

        out = trm3_g.pooled_stratum_far_band(self.UNSTRATIFIED)
        self.assertFalse(out["stratified_calibration"])
        for fold, (n_eval, n_cal, rank) in enumerate(
            ((95, 104, 10), (94, 95, 9), (104, 94, 9))
        ):
            mc = out["per_fold"][str(fold)]
            exact = trm3_g.conformal_far_band(n_eval, n_cal, rank)
            with self.subTest(fold=fold):
                self.assertEqual((mc["k_low"], mc["k_high"]),
                                 (exact["k_low"], exact["k_high"]))
                self.assertAlmostEqual(mc["coverage"], exact["coverage"], delta=0.005)
                self.assertAlmostEqual(mc["sd"], exact["sd"], delta=0.06)
                self.assertEqual(mc["band_source"], "mc")

    def test_the_pooled_sd_is_below_binomial_because_the_rotation_correlates_folds(
        self,
    ) -> None:
        """Freeze review B1 measured 0.0103 pooled vs 0.0171 binomial; this is that."""

        out = trm3_g.pooled_stratum_far_band(self.UNSTRATIFIED)
        pooled = out["per_stratum"]["_pooled"]
        self.assertEqual(pooled["n"], 95 + 94 + 104)
        p = 10 / 105
        binomial = math.sqrt(p * (1 - p) / pooled["n"])
        self.assertLess(pooled["sd"] / pooled["n"], binomial)
        self.assertAlmostEqual(pooled["sd"] / pooled["n"], 0.0103, places=3)

    def test_it_is_deterministic_given_the_seed(self) -> None:
        first = trm3_g.pooled_stratum_far_band(self.UNSTRATIFIED, reps=20_000)
        second = trm3_g.pooled_stratum_far_band(self.UNSTRATIFIED, reps=20_000)
        self.assertEqual(first, second)
        other = trm3_g.pooled_stratum_far_band(
            self.UNSTRATIFIED, reps=20_000, seed=1
        )
        self.assertNotEqual(other["per_stratum"], first["per_stratum"])
        self.assertEqual(first["seed"], 0)
        self.assertEqual(first["reps"], 20_000)
        self.assertEqual(first["numpy_version"], np.__version__)

    def test_the_stratified_g_dev_cell_mirrors_per_stratum_thresholds(self) -> None:
        out = trm3_g.pooled_stratum_far_band(_g_dev_stratified_specs())
        self.assertTrue(out["stratified_calibration"])
        self.assertEqual(out["strata"], ["n_kb=0", "n_kb=1", "n_kb=2"])
        for label, denominator in (("n_kb=0", 58), ("n_kb=1", 142), ("n_kb=2", 93)):
            self.assertEqual(out["per_stratum"][label]["n"], denominator)
            self.assertGreater(out["per_stratum"][label]["coverage"], 0.95)
        # every stratum's band is NARROWER than the binomial one rev3 would have used,
        # which is exactly why rev3's version had null pass 0.974 (no power)
        for label, (n_cal, rank) in (
            ("n_kb=0", (58, 5)), ("n_kb=1", (142, 14)), ("n_kb=2", (93, 9))
        ):
            band = out["per_stratum"][label]
            p = rank / float(n_cal + 1)
            self.assertLess(band["sd"], math.sqrt(p * (1 - p) * band["n"]))
        self.assertGreater(out["joint_null_pass"], 0.90)
        self.assertLess(out["joint_null_pass"], 0.975)

    def test_an_n_cal_that_is_not_the_calibration_folds_eval_set_is_refused(self) -> None:
        specs = _g_dev_stratified_specs()
        specs[0]["strata"]["n_kb=0"]["n_cal"] = 20
        with self.assertRaises(ValueError) as caught:
            trm3_g.pooled_stratum_far_band(specs, reps=100)
        self.assertIn("calibration fold", str(caught.exception))

    def test_impossible_arguments_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            trm3_g.pooled_stratum_far_band({}, reps=10)
        with self.assertRaises(ValueError):
            trm3_g.pooled_stratum_far_band(self.UNSTRATIFIED, reps=0)
        with self.assertRaises(ValueError):
            trm3_g.pooled_stratum_far_band(self.UNSTRATIFIED, level=1.0)
        broken = {0: {"cal_fold": 9, "n_cal": 1, "rank": 1,
                      "n_eval_by_stratum": {"_pooled": 1}}}
        with self.assertRaises(ValueError):
            trm3_g.pooled_stratum_far_band(broken, reps=10)


# ---------------------------------------------------------------------------
# round 3 -- freeze review v3.3 C-1: the manifest pins
# ---------------------------------------------------------------------------


#: the `cell` key set of the FROZEN v3.2 manifest (freeze review v3.3 C-1 measured 18).
V3_2_CELL_KEYS = [
    "alpha", "alpha_extra", "bucket_size", "cal_filtered_only", "fold_key", "folds",
    "force_h", "h_min_survivors", "layers", "min_bucket_traces", "min_channel_traces",
    "min_channel_windows", "or_arm", "rare_threshold", "standardise", "statistics",
    "tag_scope", "view",
]


class ManifestPinsTest(_V33Harness, unittest.TestCase):
    """C-1: --debounce / --top-m / the horizon mode are pinned and compared."""

    def _calibrate(self, name: str, *extra: str) -> Path:
        code = RUNNER.main(
            self._base(
                "--stage", "calibrate", "--normal-only-smoke",
                "--run-name", name, "--outputs", "primary", *extra,
            )
        )
        self.assertEqual(code, 0)
        return self.root / "out" / name / "threshold_manifest.json"

    def _score(self, manifest: Path, name: str, *extra: str) -> int:
        return RUNNER.main(
            self._base(
                "--stage", "score", "--dev-smoke",
                "--threshold-manifest", str(manifest),
                "--run-name", name, "--outputs", "primary", *extra,
            )
        )

    def _checks(self, name: str) -> list[dict]:
        return self._result(name)["threshold_manifest"]["verification"]["checks"]

    # -- stage 1 -----------------------------------------------------------
    def test_a_v3_2_shaped_stage_one_pins_nothing_and_keeps_the_19_checks(self) -> None:
        manifest = self._calibrate("pin_v32cal", "--force-h", "40")
        cell = json.loads(manifest.read_text())["cell"]
        self.assertEqual(sorted(cell), V3_2_CELL_KEYS)
        self.assertEqual(self._score(manifest, "pin_v32score", "--force-h", "40"), 0)
        checks = self._checks("pin_v32score")
        self.assertEqual(len(checks), 19)
        self.assertNotIn(
            "cell_debounce", {row["check"] for row in checks}
        )

    def test_a_v3_3_stage_one_pins_debounce_top_m_and_the_horizon_mode(self) -> None:
        manifest = self._calibrate(
            "pin_v33cal", "--statistic", "Z1", "--force-h", "inf", "--debounce", "2",
        )
        cell = json.loads(manifest.read_text())["cell"]
        self.assertEqual(cell["debounce"], 2)
        self.assertEqual(cell["top_m"], {"Z1": 1})
        self.assertEqual(cell["horizon_mode"], "unbounded")
        self.assertEqual(cell["force_h"], trm3_g.UNBOUNDED_H)
        self.assertEqual(
            sorted(set(cell) - set(V3_2_CELL_KEYS)),
            ["debounce", "horizon_mode", "top_m"],
        )

    def test_the_run_records_the_pinned_values(self) -> None:
        self._calibrate(
            "pin_reccal", "--statistic", "Z1", "--force-h", "inf", "--debounce", "2",
        )
        block = self._result("pin_reccal")["calibration_design"]["pinned"]
        self.assertEqual(block["debounce"], 2)
        self.assertEqual(block["top_m"], {"Z1": 1})
        self.assertEqual(block["horizon_mode"], "unbounded")
        self.assertEqual(block["force_h"], "inf")
        self.assertEqual(block["manifest_pins"], ["debounce", "horizon_mode", "top_m"])
        self.assertEqual(
            sorted(block["v3_3_switches_used"]),
            ["debounce", "force_h_inf", "statistic_Z1"],
        )
        # a v3.2-shaped run records the same block with an empty manifest_pins list
        self._calibrate("pin_recv32", "--force-h", "40")
        plain = self._result("pin_recv32")["calibration_design"]["pinned"]
        self.assertEqual(plain["manifest_pins"], [])
        self.assertEqual(plain["v3_3_switches_used"], [])
        self.assertEqual(plain["debounce"], 1)

    # -- stage 2 -----------------------------------------------------------
    def test_a_matching_stage_two_passes_the_three_new_checks(self) -> None:
        manifest = self._calibrate("pin_okcal", "--statistic", "Z1", "--force-h", "inf")
        self.assertEqual(
            self._score(manifest, "pin_okscore", "--statistic", "Z1", "--force-h", "inf"),
            0,
        )
        checks = self._checks("pin_okscore")
        self.assertEqual(len(checks), 22)
        rows = {row["check"]: row for row in checks}
        for name in ("cell_debounce", "cell_top_m", "cell_horizon_mode"):
            self.assertTrue(rows[name]["ok"], name)
            self.assertIn("pinned by stage 1 and matched", rows[name]["note"])
        self.assertEqual(rows["cell_debounce"]["observed"], 1)
        self.assertEqual(rows["cell_top_m"]["observed"], {"Z1": 1})
        self.assertEqual(rows["cell_horizon_mode"]["observed"], "unbounded")

    def test_stage_two_with_a_different_debounce_is_refused(self) -> None:
        manifest = self._calibrate("pin_dcal", "--statistic", "Z1", "--force-h", "inf")
        with self.assertRaises(SystemExit) as caught:
            self._score(
                manifest, "pin_dscore",
                "--statistic", "Z1", "--force-h", "inf", "--debounce", "2",
            )
        message = str(caught.exception)
        self.assertIn("cell_debounce", message)
        self.assertIn("hard gate F4", message)
        self.assertFalse((self.root / "out" / "pin_dscore" / "result.json").exists())

    def test_stage_two_with_a_different_top_m_is_refused(self) -> None:
        manifest = self._calibrate(
            "pin_mcal", "--statistic", "Z1", "--force-h", "40", "--top-m", "3",
        )
        with self.assertRaises(SystemExit) as caught:
            self._score(
                manifest, "pin_mscore",
                "--statistic", "Z1", "--force-h", "40", "--top-m", "5",
            )
        self.assertIn("cell_top_m", str(caught.exception))

    def test_stage_two_with_a_different_horizon_mode_is_refused(self) -> None:
        manifest = self._calibrate("pin_hcal", "--statistic", "Z1", "--force-h", "inf")
        with self.assertRaises(SystemExit) as caught:
            self._score(manifest, "pin_hscore", "--statistic", "Z1", "--force-h", "40")
        self.assertIn("cell_horizon_mode", str(caught.exception))

    # -- backward compatibility -------------------------------------------
    def test_a_pre_v3_3_manifest_without_pins_is_accepted_with_a_note(self) -> None:
        manifest = self._calibrate("pin_oldcal", "--force-h", "40")
        self.assertEqual(
            self._score(manifest, "pin_oldscore", "--force-h", "40", "--debounce", "2"), 0
        )
        rows = {row["check"]: row for row in self._checks("pin_oldscore")}
        self.assertEqual(len(self._checks("pin_oldscore")), 22)
        for name in ("cell_debounce", "cell_top_m", "cell_horizon_mode"):
            self.assertTrue(rows[name]["ok"], name)
            self.assertIsNone(rows[name]["observed"])
            self.assertIn("not pinned by a pre-v3.3 manifest", rows[name]["note"])
        self.assertEqual(rows["cell_debounce"]["expected"], 2)
        pinned = self._result("pin_oldscore")["calibration_design"]["pinned"]
        self.assertEqual(pinned["manifest_pins"], [])

    def test_a_v3_3_manifest_with_the_pins_stripped_is_accepted_with_a_note(self) -> None:
        """The literal 'old manifest' case: the keys are gone, the run is not refused."""

        manifest = self._calibrate("pin_stripcal", "--statistic", "Z1", "--force-h", "inf")
        payload = json.loads(manifest.read_text())
        for key in RUNNER.CELL_PIN_KEYS:
            payload["cell"].pop(key)
        payload["sha256"] = RUNNER.manifest_self_sha256(payload)
        stripped = self.root / "out" / "pin_stripcal" / "stripped_manifest.json"
        stripped.write_text(json.dumps(payload, indent=2, sort_keys=True))
        code = self._score(
            stripped, "pin_stripscore", "--statistic", "Z1", "--force-h", "inf"
        )
        self.assertEqual(code, 0)
        rows = {row["check"]: row for row in self._checks("pin_stripscore")}
        self.assertTrue(rows["cell_horizon_mode"]["ok"])
        self.assertIn(
            "not pinned by a pre-v3.3 manifest", rows["cell_horizon_mode"]["note"]
        )

    def test_a_stage_two_that_names_no_v3_3_switch_still_checks_a_pinned_manifest(
        self,
    ) -> None:
        """The pins bind even when the CLI is silent: a bounded stage 2 cannot replay an
        unbounded manifest and record `force_h: null` as if nothing happened."""

        manifest = self._calibrate("pin_silentcal", "--statistic", "Z1", "--force-h", "inf")
        with self.assertRaises(SystemExit) as caught:
            self._score(manifest, "pin_silentscore", "--statistic", "Z1")
        self.assertIn("cell_horizon_mode", str(caught.exception))


# ---------------------------------------------------------------------------
# round 3 -- the per-fold binomial rows of the descriptive gate block
# ---------------------------------------------------------------------------


class F1ConformalRowsTest(_V33Harness, unittest.TestCase):
    """rev4: `gates.<s>.F1_per_fold_conformal` IS the registered per-fold reading."""

    def _cell(self, folds):
        return {
            "statistic": "S",
            "fold_summary": {"alpha_eff_weighted": 0.0945948},
            "metrics": {
                "far": {
                    "all": {"far": 0.07598, "matched_group_far": 0.13},
                    "filtered": {"far": 0.081911, "matched_group_far": 0.127},
                    "worst_length_tertile": ["medium", 0.10084],
                }
            },
            "folds": folds,
            "_decisions": {},
        }

    def _folds(self, alarms=(8, 9, 7)):
        return {
            str(fold): {
                "fold": fold,
                "rotation": {"eval": fold, "fit": (fold + 1) % 3,
                             "reference": (fold + 2) % 3},
                "n_cal": n_cal,
                "alpha_eff": trm3_g.attainable_rank(n_cal, 0.10)["alpha_eff"],
                "attainable_rank": trm3_g.attainable_rank(n_cal, 0.10),
                "far": {
                    "filtered": {
                        "episode_count": n_eval,
                        "alarm_count": alarms[fold],
                        "far": alarms[fold] / n_eval,
                    }
                },
            }
            for fold, (n_cal, n_eval, _) in enumerate(G_DEV_FOLDS)
        }

    def _stratified_folds(self, alarms=((2, 6, 1), (4, 5, 4), (1, 3, 4))):
        out = {}
        for fold, strata in G_DEV_STRATA.items():
            per_stratum = {}
            for index, (label, (n_eval, n_cal, rank)) in enumerate(sorted(strata.items())):
                per_stratum[label] = {
                    "stratum": label,
                    "n_cal": n_cal,
                    "n_eval": n_eval,
                    "alpha_eff": rank / (n_cal + 1),
                    "attainable_rank": {"rank": rank, "n_reference": n_cal,
                                        "alpha_eff": rank / (n_cal + 1)},
                    "far": {
                        "all": {"episode_count": n_eval, "alarm_count": alarms[fold][index],
                                "far": alarms[fold][index] / n_eval},
                        "filtered": {
                            "episode_count": n_eval,
                            "alarm_count": alarms[fold][index],
                            "far": alarms[fold][index] / n_eval,
                        },
                    },
                }
            total_eval = sum(row[0] for row in strata.values())
            total_alarms = sum(alarms[fold])
            out[str(fold)] = {
                "fold": fold,
                "rotation": {"eval": fold, "fit": (fold + 1) % 3,
                             "reference": (fold + 2) % 3},
                "n_cal": sum(row[1] for row in strata.values()),
                "alpha_eff": 0.09,
                "far": {"filtered": {"episode_count": total_eval,
                                     "alarm_count": total_alarms,
                                     "far": total_alarms / total_eval}},
                "strata": {"key": "n_kb", "count": 3, "per_stratum": per_stratum},
            }
        return out

    def _block(self, folds):
        return RUNNER.gate_block(
            self._cell(folds), [], {}, cutpoints=None, filtered_only=True
        )

    def test_one_row_per_fold_on_the_filtered_denominator(self) -> None:
        rows = self._block(self._folds())["F1_per_fold_conformal"]
        self.assertEqual([row["fold"] for row in rows], [0, 1, 2])
        for row, (n_cal, n_eval, alarms) in zip(rows, G_DEV_FOLDS):
            rank = trm3_g.attainable_rank(n_cal, 0.10)["rank"]
            band = trm3_g.conformal_far_band(n_eval, n_cal, rank)
            with self.subTest(fold=row["fold"]):
                self.assertEqual(row["n_eval"], n_eval)
                self.assertEqual(row["n_cal"], n_cal)
                self.assertEqual(row["rank"], rank)
                self.assertIsNone(row["rank_note"])
                self.assertEqual(row["denominator"], "far.filtered.episode_count")
                self.assertEqual(row["alarm_count"], alarms)
                self.assertEqual(row["band"], band)
                self.assertEqual(row["band_source"], "exact")
                self.assertEqual(row["interval"], [band["far_low"], band["far_high"]])
                self.assertEqual(row["interval_counts"], [band["k_low"], band["k_high"]])
                self.assertTrue(row["in_band"])
                self.assertTrue(row["record_only"])

    def test_the_binomial_rows_are_gone(self) -> None:
        block = self._block(self._folds())
        self.assertNotIn("F1_per_fold_binomial", block)
        self.assertNotIn("F1_per_fold_binomial_all_in_band", block)
        self.assertNotIn("F1_per_fold_binomial_rule", block)
        self.assertNotIn("REPORTING ONLY", block["F1_per_fold_conformal_rule"])

    def test_in_band_is_consistent_with_the_counts_and_the_0_03_column_is_record_only(
        self,
    ) -> None:
        # 18/95 is the S . 352 fold 0 the ruling calls out: OUTSIDE +-0.03, and under the
        # exact conformal band it is INSIDE, right on the upper edge (k_high = 18).
        rows = self._block(self._folds(alarms=(18, 9, 7)))["F1_per_fold_conformal"]
        self.assertFalse(rows[0]["within_tolerance"])
        self.assertTrue(rows[0]["in_band"])
        self.assertEqual(rows[0]["interval_counts"], [3, 18])
        self.assertEqual(rows[0]["tolerance"], 0.03)
        # 30/95 leaves both
        rows = self._block(self._folds(alarms=(30, 9, 7)))["F1_per_fold_conformal"]
        self.assertFalse(rows[0]["within_tolerance"])
        self.assertFalse(rows[0]["in_band"])
        # 2/95 leaves the band from below
        rows = self._block(self._folds(alarms=(2, 9, 7)))["F1_per_fold_conformal"]
        self.assertFalse(rows[0]["in_band"])

    def test_the_summary_flags_and_the_joint_null_pass(self) -> None:
        block = self._block(self._folds())
        self.assertIs(block["F1_per_fold_conformal_all_in_band"], True)
        expected = math.prod(
            trm3_g.conformal_far_band(
                n_eval, n_cal, trm3_g.attainable_rank(n_cal, 0.10)["rank"]
            )["coverage"]
            for n_cal, n_eval, _ in G_DEV_FOLDS
        )
        self.assertAlmostEqual(
            block["F1_per_fold_conformal_joint_null_pass"], expected, places=12
        )
        self.assertLess(block["F1_per_fold_conformal_joint_null_pass"], 0.95)
        failing = self._block(self._folds(alarms=(30, 9, 7)))
        self.assertIs(failing["F1_per_fold_conformal_all_in_band"], False)
        without = self._block({})
        self.assertEqual(without["F1_per_fold_conformal"], [])
        self.assertIsNone(without["F1_per_fold_conformal_all_in_band"])
        self.assertIsNone(without["F1_per_fold_conformal_joint_null_pass"])

    def test_the_gates_list_is_unchanged_by_the_new_rows(self) -> None:
        """The five `gates[]` rows and their statuses are additive-proof."""

        with_folds = self._block(self._folds(alarms=(30, 9, 7)))
        without = self._block({})
        self.assertEqual(
            json.dumps(with_folds["gates"], sort_keys=True),
            json.dumps(without["gates"], sort_keys=True),
        )
        self.assertEqual([row["gate"] for row in with_folds["gates"]][0],
                         "F1_pooled_holdout_far_vs_alpha_eff")

    def test_a_mismatched_manifest_rank_is_a_hard_error(self) -> None:
        folds = self._folds()
        folds["0"]["alpha_eff"] = 0.5
        with self.assertRaises(SystemExit) as caught:
            self._block(folds)
        self.assertIn("attainability rank", str(caught.exception))

    def test_a_stratified_cell_uses_the_monte_carlo_band_and_writes_val1(self) -> None:
        block = self._block(self._stratified_folds())
        rows = block["F1_per_fold_conformal"]
        self.assertEqual([row["band_source"] for row in rows], ["mc"] * 3)
        for row in rows:
            self.assertEqual(row["band"]["reps"], trm3_g.POOLED_BAND_REPS)
            self.assertEqual(row["band"]["seed"], trm3_g.POOLED_BAND_SEED)
            self.assertIsInstance(row["in_band"], bool)
            # no single order statistic exists for a stratified fold
            self.assertIsNone(row["rank"])
            self.assertIn("PER STRATUM", row["rank_note"])
            pins = row["band"]["per_stratum"]
            self.assertEqual(sorted(pins), ["n_kb=0", "n_kb=1", "n_kb=2"])
            for label, pin in pins.items():
                n_eval, n_cal, rank = G_DEV_STRATA[row["fold"]][label]
                self.assertEqual((pin["n_eval"], pin["n_cal"], pin["rank"]),
                                 (n_eval, n_cal, rank))
        val1 = block["VAL1_a_exact"]
        self.assertEqual(sorted(val1), ["n_kb=0", "n_kb=1", "n_kb=2"])
        self.assertEqual((val1["n_kb=0"]["alarm_count"], val1["n_kb=0"]["n"]), (7, 58))
        self.assertEqual((val1["n_kb=1"]["alarm_count"], val1["n_kb=1"]["n"]), (14, 142))
        self.assertEqual((val1["n_kb=2"]["alarm_count"], val1["n_kb=2"]["n"]), (9, 93))
        for label, row in val1.items():
            counts = row["interval_counts"]
            self.assertEqual(
                row["in_band"], counts[0] <= row["alarm_count"] <= counts[1], label
            )
            self.assertEqual(sorted(row["per_fold"]), ["0", "1", "2"])
        self.assertIs(block["VAL1_a_all_in_band"], True)
        self.assertEqual(block["VAL1_a_seed"], 0)
        self.assertEqual(block["VAL1_a_reps"], trm3_g.POOLED_BAND_REPS)
        self.assertIsNone(block["VAL1_a_error"])
        self.assertGreater(block["VAL1_a_joint_null_pass"], 0.90)

    def test_an_unstratified_cell_writes_no_val1_rows(self) -> None:
        block = self._block(self._folds())
        self.assertNotIn("VAL1_a_exact", block)
        self.assertNotIn("VAL1_a_all_in_band", block)

    def test_a_real_run_emits_the_rows_against_its_own_folds(self) -> None:
        RUNNER.main(
            self._base(
                "--normal-only-smoke", "--run-name", "conf", "--outputs", "primary",
                "--force-h", "40",
            )
        )
        payload = self._result("conf")
        rows = payload["gates"]["S"]["F1_per_fold_conformal"]
        folds = payload["cells"]["S"]["folds"]
        self.assertEqual(len(rows), len(folds))
        for row in rows:
            block = folds[str(row["fold"])]
            self.assertEqual(row["n_eval"], block["far"]["filtered"]["episode_count"])
            self.assertEqual(row["alarm_count"], block["far"]["filtered"]["alarm_count"])
            self.assertEqual(row["alpha_eff"], block["alpha_eff"])
            self.assertEqual(row["rank"], block["attainable_rank"]["rank"])
            self.assertEqual(row["n_cal"], block["attainable_rank"]["n_reference"])
            self.assertEqual(
                row["band"],
                trm3_g.conformal_far_band(row["n_eval"], row["n_cal"], row["rank"]),
            )
            self.assertIsInstance(row["in_band"], bool)
        self.assertIn("registered per-fold reading",
                      payload["gates"]["S"]["F1_per_fold_conformal_rule"])
        self.assertNotIn("F1_per_fold_binomial", payload["gates"]["S"])
