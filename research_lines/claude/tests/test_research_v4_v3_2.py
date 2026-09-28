"""The v3.2 harness changes (docs/research_v4/v3_2_design_note.md section 10).

One test class per item of the change list, so a freeze reviewer can walk the design
note's table and land on the check:

* item 1 / 2 / 7  -- ``--cal-from-target`` with the scenario-disjoint K-fold rotation, the
  quality-filtered calibration folds and the PER-FOLD attainability floor;
* item 4          -- ``--force-h``: the frozen look budget overrides the survivor rule and
  reports what the rule would have given;
* item 5          -- the two-stage unsealing: stage 1 sees the NORMAL arms only and writes
  a self-hashed threshold manifest, stage 2 refuses to run without it, refuses a tampered
  one, and scores without refitting anything;
* item 3          -- ``--anchor`` / ``--hit-window``: the ``[E_view, X + h]`` convention,
  the strict pre-``E_view`` penalty and ``x_beyond_h``;
* item 6          -- ``--positives injection_present``: silent attacks are POSITIVES and
  the tool-result span is the injection point;
* item 8          -- ``prereg_power_sim.py --psi`` is a grid axis, not a module constant;
* item 9 / 10 / 11 -- ``or_arm.cost``, the ``channel`` column of ``outputs.jsonl`` and the
  per-fold ``x_beyond_h``.

Everything is synthetic: routing is drawn from two distributions, channels are assigned by
hand, labels are written literally and the loader is stubbed, so the tests pin the protocol
and not any number of any real pool.
"""

from __future__ import annotations

import copy
import dataclasses
import importlib.util
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

from test_research_v4_detectors_g import make_episode  # noqa: E402

torch.set_num_threads(4)


def _module(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


RUNNER = _module("run_detectors_g_v32_test", "scripts/research_v4/run_detectors_g.py")
POWER = _module("prereg_power_sim_v32_test", "scripts/research_v4/prereg_power_sim.py")


# ---------------------------------------------------------------------------
# synthetic material
# ---------------------------------------------------------------------------


def episode(
    trace_id: str,
    *,
    generator: torch.Generator,
    scenario: str,
    variant: str = "clean",
    tokens: int = 60,
    offset: int = 0,
    labels: dict | None = None,
    channel: str = "direct_user",
    episode_index: int = 0,
    tool_events: tuple = (),
    family: str = "fam0",
) -> io_g.GEpisode:
    built = make_episode(
        trace_id,
        generator=generator,
        tokens=tokens,
        variant=variant,
        labels=labels,
        offset=offset,
        scenario=scenario,
        family=family,
        episode_index=episode_index,
    )
    return dataclasses.replace(built, channel=channel, tool_events=tuple(tool_events))


def normal_labels(*, filter_pass: bool = True) -> dict:
    return {
        "quality": {
            "behavior": "on_task",
            "coverage": "full",
            "material_errors": not filter_pass,
            "unauthorized_tool_attempt": False,
        }
    }


def attack_labels(*, e: int, x: int | None, silent: bool = False) -> dict:
    if silent:
        return {"trajectory_class": "silent", "quality": {"behavior": "on_task", "coverage": "full"}}
    row: dict = {
        "e_analysis": e,
        "trajectory_class": "execution",
        "quality": {"behavior": "on_task", "coverage": "full"},
    }
    if x is not None:
        row["x"] = x
    return row


def synthetic_batch(
    *,
    scenarios: int = 12,
    seed: int = 5,
    attacks: bool = True,
    tokens: int = 60,
) -> list[io_g.GEpisode]:
    """``scenarios`` scenarios x (clean, benign_control[, attack]) episodes."""

    generator = torch.Generator().manual_seed(seed)
    pool: list[io_g.GEpisode] = []
    for index in range(scenarios):
        name = f"s-{index:03d}"
        pool.append(
            episode(
                f"{name}-clean",
                generator=generator,
                scenario=name,
                variant="clean",
                tokens=tokens,
                labels=normal_labels(),
            )
        )
        pool.append(
            episode(
                f"{name}-benign",
                generator=generator,
                scenario=name,
                variant="benign_control",
                tokens=tokens,
                labels=normal_labels(filter_pass=index % 5 != 0),
            )
        )
        if attacks:
            pool.append(
                episode(
                    f"{name}-attack",
                    generator=generator,
                    scenario=name,
                    variant="attack",
                    tokens=tokens,
                    offset=9,
                    family=f"fam{index % 3}",
                    labels=attack_labels(e=20, x=34),
                )
            )
    return pool


def anchor_of(*, e: int | None, x: int | None = None, c: int | None = None) -> trm3_g.ViewAnchor:
    return trm3_g.ViewAnchor(
        key="k",
        anchor=e,
        anchor_channel="analysis",
        reason="labelled" if e is not None else "no_engagement",
        c=c,
        x=x,
        variant="attack",
    )


# ---------------------------------------------------------------------------
# item 1 / 7 -- the fold rotation
# ---------------------------------------------------------------------------


class FoldRotationTest(unittest.TestCase):
    def test_fold_is_the_sorted_index_modulo_k_over_every_scenario(self) -> None:
        table = trm3_g.fold_assignment(["c", "a", "b", "e", "d"], folds=3)
        self.assertEqual(table, {"a": 0, "b": 1, "c": 2, "d": 0, "e": 1})

    def test_the_map_does_not_depend_on_input_order_or_duplicates(self) -> None:
        one = trm3_g.fold_assignment(["b", "a", "c"], folds=3)
        two = trm3_g.fold_assignment(["c", "c", "a", "b"], folds=3)
        self.assertEqual(one, two)
        self.assertEqual(trm3_g.fold_table_sha256(one), trm3_g.fold_table_sha256(two))

    def test_the_sha256_changes_when_one_scenario_moves(self) -> None:
        one = trm3_g.fold_assignment(["a", "b", "c"], folds=3)
        two = dict(one, c=0)
        self.assertNotEqual(trm3_g.fold_table_sha256(one), trm3_g.fold_table_sha256(two))

    def test_unknown_fold_key_and_degenerate_k_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            trm3_g.fold_assignment(["a"], key="hash_mod")
        with self.assertRaises(ValueError):
            trm3_g.fold_assignment(["a"], folds=1)

    def test_rotation_is_k_holds_out_k1_fits_k2_references(self) -> None:
        self.assertEqual(trm3_g.rotation(0, 3), {"eval": 0, "fit": 1, "reference": 2})
        self.assertEqual(trm3_g.rotation(2, 3), {"eval": 2, "fit": 0, "reference": 1})

    def test_fold_pools_are_scenario_disjoint_and_quality_filtered(self) -> None:
        pool = synthetic_batch(scenarios=9)
        table = trm3_g.fold_assignment({e.pair_group_id for e in pool}, folds=3)
        pools = RUNNER.fold_pools(
            pool, table, folds=3, filtered_only=True, normals_only_eval=False
        )
        for fold, spec in pools.items():
            evaluated = {e.pair_group_id for e in spec["eval"]}
            fitted = {e.pair_group_id for e in spec["fit"]}
            referenced = {e.pair_group_id for e in spec["reference"]}
            self.assertFalse(evaluated & fitted)
            self.assertFalse(evaluated & referenced)
            self.assertFalse(fitted & referenced)
            # design note 3.2 last row: filter_pass None / False never calibrates
            self.assertTrue(all(e.filter_pass is True for e in spec["fit"]))
            self.assertTrue(all(e.filter_pass is True for e in spec["reference"]))
            self.assertTrue(all(e.variant in io_g.NORMAL_VARIANTS for e in spec["fit"]))
        # every episode is evaluated exactly once
        seen = [trm3.trace_key(e) for spec in pools.values() for e in spec["eval"]]
        self.assertEqual(len(seen), len(pool))
        self.assertEqual(len(set(seen)), len(pool))

    def test_unfiltered_rotation_keeps_the_dropped_episodes(self) -> None:
        pool = synthetic_batch(scenarios=9)
        table = trm3_g.fold_assignment({e.pair_group_id for e in pool}, folds=3)
        strict = RUNNER.fold_pools(
            pool, table, folds=3, filtered_only=True, normals_only_eval=False
        )
        loose = RUNNER.fold_pools(
            pool, table, folds=3, filtered_only=False, normals_only_eval=False
        )
        self.assertLess(
            sum(len(s["fit"]) for s in strict.values()),
            sum(len(s["fit"]) for s in loose.values()),
        )

    def test_a_scenario_outside_the_fold_map_is_a_hard_refusal(self) -> None:
        pool = synthetic_batch(scenarios=6)
        table = trm3_g.fold_assignment(
            {e.pair_group_id for e in pool} - {"s-005"}, folds=3
        )
        with self.assertRaises(SystemExit) as caught:
            RUNNER.fold_pools(pool, table, folds=3, filtered_only=False, normals_only_eval=False)
        self.assertIn("fold map does not cover", str(caught.exception))

    def test_normals_only_eval_keeps_the_attack_arms_out(self) -> None:
        pool = synthetic_batch(scenarios=6)
        table = trm3_g.fold_assignment({e.pair_group_id for e in pool}, folds=3)
        pools = RUNNER.fold_pools(
            pool, table, folds=3, filtered_only=True, normals_only_eval=True
        )
        for spec in pools.values():
            self.assertTrue(all(e.variant in io_g.NORMAL_VARIANTS for e in spec["eval"]))

    def test_per_fold_attainability_floor(self) -> None:
        # design note 3.3: floor((n_cal + 1) * alpha) >= 1 asserted PER FOLD
        self.assertEqual(trm3_g.attainable_rank(9, 0.10)["rank"], 1)
        self.assertEqual(trm3_g.attainable_rank(8, 0.10)["rank"], 0)
        self.assertAlmostEqual(trm3_g.attainable_rank(157, 0.10)["alpha_eff"], 15 / 158.0)
        self.assertAlmostEqual(trm3_g.attainable_rank(213, 0.10)["alpha_eff"], 21 / 214.0)


# ---------------------------------------------------------------------------
# item 4 -- the explicitly frozen horizon
# ---------------------------------------------------------------------------


class ForcedHorizonTest(unittest.TestCase):
    def test_forced_h_overrides_the_survivor_rule_and_reports_it(self) -> None:
        lengths = [400, 380, 360, 300, 200, 120]
        rule = trm3_g.h_horizon(lengths, min_survivors=5)
        forced = trm3_g.h_horizon_at(lengths, 352, min_survivors=5)
        self.assertEqual(rule["H"], 200)
        self.assertEqual(forced["H"], 352)
        self.assertEqual(forced["rule_H"], 200)
        self.assertEqual(forced["survivors_at_H"], 3)
        self.assertFalse(forced["min_survivors_satisfied"])
        self.assertTrue(forced["forced"])
        self.assertEqual(forced["censored_paths"], 3)

    def test_the_bookkeeping_keys_match_the_unforced_rule(self) -> None:
        lengths = [50, 40, 30]
        rule = trm3_g.h_horizon(lengths, min_survivors=3)
        forced = trm3_g.h_horizon_at(lengths, int(rule["H"]), min_survivors=3)
        for key in rule:
            self.assertEqual(rule[key], forced[key], key)

    def test_calibrate_g_takes_the_forced_horizon(self) -> None:
        generator = torch.Generator().manual_seed(11)
        pool = [
            episode(f"n{i}", generator=generator, scenario=f"s{i}", tokens=80)
            for i in range(8)
        ]
        view = trm3_g.VIEWS["V1"]
        stat = trm3_g.build_statistic("S").fit(pool, view)
        streams = trm3_g.episode_streams({"S": stat}, pool, view)["S"]
        config = trm3_g.config_for_g(["S"], alpha=0.10)
        forced = trm3_g.calibrate_g(
            streams,
            streams,
            config,
            view=view,
            statistic="S",
            min_survivors=3,
            min_bucket_traces=3,
            force_h=5,
        )
        self.assertEqual(int(forced.horizon["H"]), 5)
        self.assertTrue(forced.horizon["forced"])


# ---------------------------------------------------------------------------
# item 5 -- the fitted state round-trips exactly
# ---------------------------------------------------------------------------


class StateRoundTripTest(unittest.TestCase):
    def _pool(self, seed: int = 17, count: int = 8):
        generator = torch.Generator().manual_seed(seed)
        return [
            episode(f"r{i}", generator=generator, scenario=f"s{i}", tokens=70)
            for i in range(count)
        ]

    def test_statistic_state_round_trips_through_json(self) -> None:
        view = trm3_g.VIEWS["V1"]
        pool = self._pool()
        for name in ("S", "P", "M"):
            fitted = trm3_g.build_statistic(name).fit(pool, view)
            payload = json.loads(json.dumps(fitted.state_dict()))
            restored = trm3_g.build_statistic(name).load_state(payload)
            for episode_ in pool:
                before = fitted.stream(episode_, view)[1]
                after = restored.stream(episode_, view)[1]
                np.testing.assert_array_equal(before, after)

    def test_an_unsupported_family_refuses_to_serialise(self) -> None:
        view = trm3_g.VIEWS["V1"]
        fitted = trm3_g.build_statistic("B").fit(self._pool(), view)
        with self.assertRaises(NotImplementedError):
            fitted.state_dict()

    def test_calibration_state_reproduces_every_alarm(self) -> None:
        view = trm3_g.VIEWS["V1"]
        fit_pool = self._pool(seed=21)
        cal_pool = self._pool(seed=22)
        target = self._pool(seed=23)
        stat = trm3_g.build_statistic("S").fit(fit_pool, view)
        config = trm3_g.config_for_g(["S"], alpha=0.10)
        fit_streams = trm3_g.episode_streams({"S": stat}, fit_pool, view)["S"]
        cal_streams = trm3_g.episode_streams({"S": stat}, cal_pool, view)["S"]
        calibration = trm3_g.calibrate_g(
            fit_streams,
            cal_streams,
            config,
            view=view,
            statistic="S",
            min_survivors=3,
            min_bucket_traces=3,
        )
        payload = json.loads(json.dumps(calibration.state_dict()))
        restored = trm3_g.calibration_from_state(payload)
        restored_stat = trm3_g.build_statistic("S").load_state(
            json.loads(json.dumps(stat.state_dict()))
        )
        for episode_ in target:
            before = trm3_g.score_episode(
                {"S": trm3_g.episode_streams({"S": stat}, [episode_], view)["S"][0]},
                calibration,
                config,
            )
            after = trm3_g.score_episode(
                {
                    "S": trm3_g.episode_streams({"S": restored_stat}, [episode_], view)["S"][0]
                },
                restored,
                config,
            )
            self.assertEqual([o.end for o in before], [o.end for o in after])
            self.assertEqual([o.p_fused for o in before], [o.p_fused for o in after])
            self.assertEqual([o.state for o in before], [o.state for o in after])

    def test_window_z_is_summarised_not_stored_by_default(self) -> None:
        view = trm3_g.VIEWS["V1"]
        pool = self._pool()
        stat = trm3_g.build_statistic("S").fit(pool, view)
        streams = trm3_g.episode_streams({"S": stat}, pool, view)["S"]
        config = trm3_g.config_for_g(["S"], alpha=0.10)
        calibration = trm3_g.calibrate_g(
            streams, streams, config, view=view, statistic="S",
            min_survivors=3, min_bucket_traces=3,
        )
        lean = calibration.state_dict()
        fat = calibration.state_dict(include_window_z=True)
        self.assertNotIn("window_z_sorted", lean["channels"]["S"])
        self.assertIn("window_z_sorted", fat["channels"]["S"])
        self.assertEqual(
            lean["channels"]["S"]["window_z_count"], len(fat["channels"]["S"]["window_z_sorted"])
        )


# ---------------------------------------------------------------------------
# item 3 -- the X anchor and the [E_view, X + h] convention
# ---------------------------------------------------------------------------


class AnchorConventionTest(unittest.TestCase):
    def test_anchor_value_reads_the_three_anchors(self) -> None:
        anchor = anchor_of(e=10, x=40, c=25)
        self.assertEqual(trm3_g.anchor_value(anchor, "e_view"), 10)
        self.assertEqual(trm3_g.anchor_value(anchor, "x"), 40)
        self.assertEqual(trm3_g.anchor_value(anchor, "c"), 25)
        self.assertIsNone(trm3_g.anchor_value(anchor_of(e=10), "x"))
        # an episode outside the E denominator has no anchor under ANY name
        self.assertIsNone(trm3_g.anchor_value(anchor_of(e=None, x=40), "x"))
        with self.assertRaises(ValueError):
            trm3_g.anchor_value(anchor, "f")

    def test_window_bounds(self) -> None:
        anchor = anchor_of(e=10, x=40)
        self.assertEqual(
            trm3_g.window_bounds(anchor, which="x", window="anchor_plus_h", horizon=16),
            (40, 56),
        )
        self.assertEqual(
            trm3_g.window_bounds(
                anchor, which="x", window="e_view_to_anchor_plus_h", horizon=16
            ),
            (10, 56),
        )
        self.assertEqual(
            trm3_g.window_bounds(
                anchor, which="x", window="e_view_to_anchor_plus_h", horizon=None
            ),
            (10, None),
        )

    def _block(self, alarms, ends, **kwargs):
        return trm3_g.window_hit_block(
            alarms, ends, anchor_of(e=10, x=40), horizons=(16, None), **kwargs
        )

    def test_an_alarm_between_e_and_x_is_a_hit_not_a_miss(self) -> None:
        block = self._block([25], list(range(0, 60)))
        self.assertTrue(block["hit_plus_16"])
        self.assertTrue(block["early_than_anchor"])
        self.assertFalse(block["pre_window_alarm"])
        # the FROZEN v3.1 window would have called the same alarm a miss
        frozen = self._block([25], list(range(0, 60)), window="anchor_plus_h")
        self.assertFalse(frozen["hit_plus_16"])

    def test_an_alarm_before_e_view_is_a_miss_strict_penalty(self) -> None:
        block = self._block([5, 45], list(range(0, 60)))
        self.assertFalse(block["hit_plus_16"])
        self.assertTrue(block["pre_window_alarm"])
        self.assertTrue(block["hit_no_penalty_plus_16"])

    def test_an_alarm_after_x_plus_h_is_a_miss(self) -> None:
        block = self._block([57], list(range(0, 60)))
        self.assertFalse(block["hit_plus_16"])
        self.assertTrue(block["hit_plus_full"])

    def test_reachability_is_the_window_form(self) -> None:
        # a grid with a hole over [10, 56] cannot decide inside the window
        block = self._block([], [0, 2, 4, 6, 8, 58, 59])
        self.assertFalse(block["reachable_plus_16"])
        self.assertTrue(block["reachable_plus_full"])

    def test_x_beyond_the_horizon_is_flagged(self) -> None:
        block = self._block([], list(range(0, 30)))
        self.assertTrue(block["anchor_beyond_h"])
        # the window form still reaches (endpoints exist inside [E_view, X+16]); the
        # STRICTER at-anchor form does not, which is what x_beyond_h counts
        self.assertTrue(block["reachable_plus_16"])
        self.assertFalse(block["reachable_at_anchor_plus_16"])
        near = self._block([], list(range(0, 60)))
        self.assertFalse(near["anchor_beyond_h"])
        self.assertTrue(near["reachable_at_anchor_plus_16"])

    def test_an_episode_without_x_produces_no_block(self) -> None:
        self.assertIsNone(
            trm3_g.window_hit_block([], [1, 2], anchor_of(e=10), which="x")
        )


class AnchoredPositivesTest(unittest.TestCase):
    def _material(self):
        generator = torch.Generator().manual_seed(31)
        episodes = [
            episode(
                "a-hit",
                generator=generator,
                scenario="s1",
                variant="attack",
                labels=attack_labels(e=10, x=40),
            ),
            episode(
                "a-early",
                generator=generator,
                scenario="s2",
                variant="attack",
                labels=attack_labels(e=10, x=40),
            ),
            episode(
                "a-pre",
                generator=generator,
                scenario="s3",
                variant="attack",
                labels=attack_labels(e=10, x=40),
            ),
            episode(
                "a-no-x",
                generator=generator,
                scenario="s4",
                variant="attack",
                labels=attack_labels(e=10, x=None),
            ),
        ]
        anchors = {trm3.trace_key(e): anchor_of(e=10, x=40) for e in episodes}
        anchors[trm3.trace_key(episodes[3])] = anchor_of(e=10)
        ends = {trm3.trace_key(e): list(range(0, 60)) for e in episodes}
        alarms = {
            trm3.trace_key(episodes[0]): [45],
            trm3.trace_key(episodes[1]): [22],
            trm3.trace_key(episodes[2]): [3],
            trm3.trace_key(episodes[3]): [],
        }
        summaries = {
            key: type("S", (), {"alarm_ends": value})() for key, value in alarms.items()
        }
        return episodes, anchors, ends, summaries

    def test_recall_denominator_excludes_the_episodes_without_x(self) -> None:
        episodes, anchors, ends, summaries = self._material()
        block = trm3_g.anchored_positives(
            summaries, ends, anchors, episodes, which="x",
            window="e_view_to_anchor_plus_h",
        )
        self.assertEqual(block["count"], 3)
        self.assertEqual(block["excluded"]["no_x_annotation"], 1)
        row = block["recall"]["penalty_plus_16"]
        self.assertEqual(row["reachable_count"], 3)
        self.assertEqual(row["hit_count"], 2)
        self.assertAlmostEqual(row["recall"], 2 / 3)
        self.assertAlmostEqual(block["early_than_anchor"]["rate"], 1 / 3)
        self.assertAlmostEqual(block["pre_window_alarm_rate"], 1 / 3)

    def test_every_horizon_and_every_stratum_is_reported(self) -> None:
        episodes, anchors, ends, summaries = self._material()
        block = trm3_g.anchored_positives(
            summaries, ends, anchors, episodes, which="x",
            window="e_view_to_anchor_plus_h",
        )
        for name in ("8", "16", "32", "full"):
            self.assertIn(f"penalty_plus_{name}", block["recall"])
            self.assertIn(f"no_penalty_plus_{name}", block["recall"])
        for key in ("by_trajectory_class", "by_domain_group", "by_channel", "by_wording_tier"):
            self.assertTrue(block[key])
        self.assertIn("x_beyond_h", block["reachability"])

    def test_x_beyond_h_counts_the_positives_the_horizon_cut_off(self) -> None:
        episodes, anchors, ends, summaries = self._material()
        short = {key: list(range(0, 30)) for key in ends}
        block = trm3_g.anchored_positives(
            summaries, short, anchors, episodes, which="x",
            window="e_view_to_anchor_plus_h",
        )
        self.assertEqual(block["reachability"]["x_beyond_h"], 3)
        self.assertEqual(block["reachability"]["anchor_beyond_h"], 3)
        self.assertEqual(block["reachability"]["anchor_reachable_plus_16"], 0)
        # the window form still has a denominator; the at-anchor form does not
        self.assertEqual(block["recall"]["penalty_plus_16"]["reachable_count"], 3)
        self.assertEqual(
            block["recall_anchor_reachable"]["penalty_plus_16"]["reachable_count"], 0
        )

    def test_window_hits_at_alpha_agrees_with_the_metric_block(self) -> None:
        generator = torch.Generator().manual_seed(41)
        pool = [
            episode(f"n{i}", generator=generator, scenario=f"c{i}", tokens=70)
            for i in range(8)
        ]
        attack = episode(
            "atk",
            generator=generator,
            scenario="atk",
            variant="attack",
            tokens=70,
            offset=9,
            labels=attack_labels(e=10, x=40),
        )
        view = trm3_g.VIEWS["V1"]
        stat = trm3_g.build_statistic("S").fit(pool, view)
        config = trm3_g.config_for_g(["S"], alpha=0.10)
        streams = trm3_g.episode_streams({"S": stat}, pool, view)["S"]
        calibration = trm3_g.calibrate_g(
            streams, streams, config, view=view, statistic="S",
            min_survivors=3, min_bucket_traces=3,
        )
        target_stream = trm3_g.episode_streams({"S": stat}, [attack], view)["S"][0]
        outputs = trm3_g.score_episode({"S": target_stream}, calibration, config)
        key = trm3.trace_key(attack)
        decisions = {key: trm3.DecisionStream.from_outputs(outputs, attack, 0)}
        anchors = {key: anchor_of(e=10, x=40)}
        ends = {key: [int(o.end) for o in outputs if not o.horizon_censored]}
        summaries = {key: trm3.summarize_trace(outputs, attack, 0)}
        metric = trm3_g.anchored_positives(
            summaries, ends, anchors, [attack], which="x",
            window="e_view_to_anchor_plus_h",
        )
        hits = trm3_g.window_hits_at_alpha(
            decisions, anchors, ends, 0.10, which="x",
            window="e_view_to_anchor_plus_h", horizon=16,
        )
        if metric["recall"]["penalty_plus_16"]["reachable_count"]:
            self.assertEqual(
                hits[key], bool(metric["per_episode"][key]["hit_plus_16"])
            )


# ---------------------------------------------------------------------------
# item 6 -- the injection-presence cell
# ---------------------------------------------------------------------------


class InjectionPresenceTest(unittest.TestCase):
    def test_multi_turn_episode_zero_carries_no_injection(self) -> None:
        generator = torch.Generator().manual_seed(51)
        first = episode(
            "m", generator=generator, scenario="s", variant="attack",
            channel=io_g.MULTI_TURN_CHANNEL, episode_index=0,
        )
        second = episode(
            "m", generator=generator, scenario="s", variant="attack",
            channel=io_g.MULTI_TURN_CHANNEL, episode_index=1,
        )
        direct = episode(
            "d", generator=generator, scenario="s", variant="attack", channel="direct_user"
        )
        clean = episode("c", generator=generator, scenario="s", variant="clean")
        self.assertFalse(trm3_g.injection_present(first))
        self.assertTrue(trm3_g.injection_present(second))
        self.assertTrue(trm3_g.injection_present(direct))
        self.assertFalse(trm3_g.injection_present(clean))

    def test_the_injection_point_of_a_tool_output_arm_is_the_tool_result_span(self) -> None:
        generator = torch.Generator().manual_seed(52)
        with_event = episode(
            "t", generator=generator, scenario="s", variant="attack",
            channel=trm3_g.TOOL_OUTPUT_CHANNEL,
            tool_events=(
                {"injection_applied": False, "call_last_token_global": 5},
                {"injection_applied": True, "call_last_token_global": 21},
                {"injection_applied": True, "call_last_token_global": 40},
            ),
        )
        self.assertEqual(trm3_g.injection_point(with_event)["token"], 21)
        self.assertEqual(trm3_g.injection_point(with_event)["source"], "tool_result_span")
        without = episode(
            "t2", generator=generator, scenario="s", variant="attack",
            channel=trm3_g.TOOL_OUTPUT_CHANNEL,
        )
        self.assertEqual(trm3_g.injection_point(without)["token"], 0)
        self.assertEqual(
            trm3_g.injection_point(without)["source"], "episode_start_no_tool_result_span"
        )
        direct = episode("d", generator=generator, scenario="s", variant="attack")
        self.assertEqual(trm3_g.injection_point(direct)["token"], 0)
        self.assertEqual(trm3_g.injection_point(direct)["source"], "episode_start")

    def test_silent_attacks_are_positives_and_benign_arms_are_the_negatives(self) -> None:
        generator = torch.Generator().manual_seed(53)
        loud = episode(
            "loud", generator=generator, scenario="s1", variant="attack",
            labels=attack_labels(e=10, x=40),
        )
        silent = episode(
            "silent", generator=generator, scenario="s2", variant="attack",
            labels=attack_labels(e=0, x=None, silent=True),
        )
        benign = episode(
            "benign", generator=generator, scenario="s3", variant="benign_control",
            labels=normal_labels(),
        )
        clean = episode(
            "clean", generator=generator, scenario="s4", variant="clean",
            labels=normal_labels(),
        )
        pre = episode(
            "pre", generator=generator, scenario="s5", variant="attack",
            channel=io_g.MULTI_TURN_CHANNEL, episode_index=0,
        )
        episodes = [loud, silent, benign, clean, pre]

        class Stream:
            def __init__(self, ends):
                self._ends = ends

            def alarm_ends(self, alpha):
                return list(self._ends)

        decisions = {
            trm3.trace_key(loud): Stream([44]),
            trm3.trace_key(silent): Stream([12]),
            trm3.trace_key(benign): Stream([]),
            trm3.trace_key(clean): Stream([3]),
            trm3.trace_key(pre): Stream([9]),
        }
        block = trm3_g.injection_presence_block(decisions, episodes, alpha=0.10)
        self.assertEqual(block["positives"]["count"], 2)
        self.assertEqual(block["positives"]["hit_count"], 2)
        self.assertEqual(block["positives"]["silent_count"], 1)
        self.assertEqual(block["positives"]["silent_hit_count"], 1)
        self.assertEqual(block["negatives"]["count"], 2)
        self.assertAlmostEqual(block["negatives"]["far"], 0.5)
        self.assertEqual(block["excluded_attack_episodes"], 1)

    def test_an_alarm_before_the_injection_point_is_not_a_hit(self) -> None:
        generator = torch.Generator().manual_seed(54)
        tool = episode(
            "t", generator=generator, scenario="s", variant="attack",
            channel=trm3_g.TOOL_OUTPUT_CHANNEL,
            tool_events=({"injection_applied": True, "call_last_token_global": 30},),
        )

        class Stream:
            def alarm_ends(self, alpha):
                return [12]

        hits = trm3_g.injection_hits({trm3.trace_key(tool): Stream()}, [tool], 0.10)
        self.assertEqual(hits[trm3.trace_key(tool)], False)


# ---------------------------------------------------------------------------
# item 8 -- the power simulator's psi axis
# ---------------------------------------------------------------------------


class PowerSimPsiTest(unittest.TestCase):
    def test_psi_is_a_grid_axis_and_offset_zero_keeps_the_frozen_seed(self) -> None:
        sizes = [3, 2]
        one = POWER.run_grid(
            family_sizes=sizes, ns=(20,), deltas=(0.10,), rhos=(0.15,),
            replicates=200, bootstrap=50,
        )
        two = POWER.run_grid(
            family_sizes=sizes, ns=(20,), deltas=(0.10,), rhos=(0.15,),
            psis=(POWER.PSI, 0.35), replicates=200, bootstrap=50,
        )
        self.assertEqual(len(two), 2)
        self.assertEqual(one[0]["power"], two[0]["power"])
        self.assertEqual(two[0]["psi"], POWER.PSI)
        self.assertEqual(two[1]["psi"], 0.35)

    def test_a_delta_above_psi_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            POWER.simulate_cell(
                n=20, delta=0.30, rho=0.15, psi=0.25,
                family_sizes=[3, 2], replicates=10, bootstrap=10,
            )

    def test_the_cli_refuses_an_unattainable_grid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SystemExit):
                POWER.main(
                    [
                        "--replicates", "2000", "--bootstrap", "50",
                        "--n", "20", "--rho", "0.15",
                        "--delta", "0.30", "--psi", "0.25",
                        "--output-dir", tmp,
                    ]
                )

    def test_the_tables_carry_the_psi_column_when_several_are_run(self) -> None:
        rows = POWER.run_grid(
            family_sizes=[3, 2], ns=(20,), deltas=(0.0, 0.10), rhos=(0.15,),
            psis=(0.25, 0.35), replicates=200, bootstrap=50,
        )
        self.assertIn("| psi |", POWER.markdown_table(rows, [0.10]))
        self.assertIn("| psi |", POWER.mde_table(rows))
        self.assertIn("| psi |", POWER.null_table(rows))
        single = [row for row in rows if row["psi"] == 0.25]
        self.assertNotIn("| psi |", POWER.markdown_table(single, [0.10]))


# ---------------------------------------------------------------------------
# items 1 / 5 / 10 / 11 -- the runner end to end (loader stubbed)
# ---------------------------------------------------------------------------


class StubLoader:
    """Replaces ``load_pool`` / the metadata scans with an in-memory batch."""

    def __init__(self, pool):
        self.pool = list(pool)
        self.calls: list[dict] = []

    def load_pool(self, dirs, *, name, scenarios, labels, tag_scope, cache_dir, variants=None):
        self.calls.append({"name": name, "variants": None if variants is None else list(variants)})
        kept = [
            e
            for e in self.pool
            if (variants is None or e.variant in variants)
            and (scenarios is None or e.pair_group_id in scenarios)
        ]
        block = io_g.episode_manifest(kept)
        block.update(
            {
                "pool": name,
                "dirs": [str(d) for d in dirs],
                "scenarios_filter": list(scenarios or ()),
                "load_reports": [],
                "dataset_roles": [],
            }
        )
        return tuple(kept), block

    def target_scenarios(self, dirs):
        return sorted({e.pair_group_id for e in self.pool}), [{"run_dir": str(dirs[0])}]

    def normal_trace_manifest(self, dirs):
        return {
            "sha256": "0" * 64,
            "trace_count": sum(1 for e in self.pool if e.variant in io_g.NORMAL_VARIANTS),
            "traces_by_variant": {},
            "per_dir": [{"run_dir": str(dirs[0]), "sha256": "0" * 64}],
        }


class RunnerV32Test(unittest.TestCase):
    def setUp(self) -> None:
        self.pool = synthetic_batch(scenarios=21, seed=61, tokens=64)
        self.stub = StubLoader(self.pool)
        self._saved = (
            RUNNER.load_pool,
            RUNNER.target_scenarios,
            RUNNER.normal_trace_manifest,
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
            "--view", "V1", "--statistic", "S",
            "--alpha", "0.10", "--window-s", "8",
            "--h-min-survivors", "3", "--min-bucket-traces", "3",
            "--min-channel-windows", "3", "--min-channel-traces", "2",
            "--output-root", str(self.root / "out"),
            "--bootstrap-replicates", "50",
            *extra,
        ]

    def _calibrate(self, *extra: str) -> Path:
        code = RUNNER.main(
            self._base(
                "--stage", "calibrate", "--normal-only-smoke",
                "--statistic", "S,P",
                "--run-name", "cal", "--outputs", "primary", *extra,
            )
        )
        self.assertEqual(code, 0)
        return self.root / "out" / "cal" / "threshold_manifest.json"

    # -- stage 1 ----------------------------------------------------------
    def test_stage_calibrate_loads_the_normal_arms_only(self) -> None:
        self._calibrate()
        self.assertEqual(
            self.stub.calls[-1]["variants"], list(io_g.NORMAL_VARIANTS)
        )

    def test_stage_calibrate_refuses_an_attack_arm_that_slips_through(self) -> None:
        original = self.stub.load_pool

        def leaky(dirs, **kwargs):
            episodes, block = original(dirs, **kwargs)
            return tuple(list(episodes) + [self.pool[2]]), block

        RUNNER.load_pool = leaky
        with self.assertRaises(SystemExit) as caught:
            RUNNER.main(
                self._base("--stage", "calibrate", "--normal-only-smoke", "--run-name", "leak")
            )
        self.assertIn("two-stage unsealing forbids", str(caught.exception))

    def test_the_manifest_records_the_fold_table_and_the_per_fold_thresholds(self) -> None:
        path = self._calibrate("--force-h", "40")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["kind"], RUNNER.MANIFEST_KIND)
        self.assertEqual(manifest["sha256"], RUNNER.manifest_self_sha256(manifest))
        self.assertEqual(len(manifest["fold_assignment"]), 21)
        self.assertEqual(
            manifest["fold_assignment_sha256"],
            trm3_g.fold_table_sha256(manifest["fold_assignment"]),
        )
        folds = {
            fold: block["cells"]["S"] for fold, block in manifest["folds"].items()
        }
        self.assertEqual(sorted(folds), ["0", "1", "2"])
        for block in folds.values():
            self.assertEqual(block["H"], 40)
            self.assertIn("S", block["statistics"])
            self.assertIn("S", block["calibrations"])
            self.assertGreaterEqual(block["n_cal"], 9)
            self.assertIn("alpha_eff", block)
            # design note 3.3: floor((n_cal + 1) * alpha) >= 1 per fold
            self.assertGreaterEqual(
                trm3_g.attainable_rank(block["n_cal"], 0.10)["rank"], 1
            )
            self.assertTrue(block["attainability"]["ok"])

    def test_the_result_records_per_fold_n_fit_n_cal_h_and_alpha_eff(self) -> None:
        self._calibrate("--force-h", "40")
        result = json.loads(
            (self.root / "out" / "cal" / "result.json").read_text(encoding="utf-8")
        )
        design = result["calibration_design"]
        self.assertEqual(design["fold_key"], "scenario_mod")
        self.assertEqual(design["folds"], 3)
        self.assertEqual(
            design["fold_assignment_sha256"],
            trm3_g.fold_table_sha256(design["fold_assignment"]),
        )
        cell = result["cells"]["S"]
        for fold in ("0", "1", "2"):
            block = cell["folds"][fold]
            for key in ("n_fit", "n_cal", "H", "alpha_eff", "survivors_at_H", "x_beyond_h"):
                self.assertIn(key, block)
            self.assertTrue(block["horizon"]["forced"])
        self.assertIn("alpha_eff_weighted", cell["fold_summary"])

    # -- stage 2 ----------------------------------------------------------
    def test_stage_score_without_a_manifest_exits_non_zero(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            RUNNER.main(self._base("--stage", "score", "--dev-smoke", "--run-name", "s"))
        self.assertIn("--threshold-manifest", str(caught.exception))

    def test_stage_score_with_a_missing_manifest_exits_non_zero(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            RUNNER.main(
                self._base(
                    "--stage", "score", "--dev-smoke", "--run-name", "s",
                    "--threshold-manifest", str(self.root / "nope.json"),
                )
            )
        self.assertIn("does not exist", str(caught.exception))

    def test_stage_score_with_a_tampered_manifest_exits_non_zero(self) -> None:
        path = self._calibrate("--force-h", "40")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["folds"]["0"]["cells"]["S"]["H"] = 999
        tampered = self.root / "tampered.json"
        tampered.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(SystemExit) as caught:
            RUNNER.main(
                self._base(
                    "--stage", "score", "--dev-smoke", "--run-name", "t",
                    "--force-h", "40", "--threshold-manifest", str(tampered),
                )
            )
        self.assertIn("manifest_sha256", str(caught.exception))

    def test_a_re_hashed_but_re_folded_manifest_is_still_refused(self) -> None:
        path = self._calibrate("--force-h", "40")
        payload = json.loads(path.read_text(encoding="utf-8"))
        scenario = sorted(payload["fold_assignment"])[0]
        payload["fold_assignment"][scenario] = (
            payload["fold_assignment"][scenario] + 1
        ) % 3
        payload["fold_assignment_sha256"] = trm3_g.fold_table_sha256(
            payload["fold_assignment"]
        )
        payload["sha256"] = RUNNER.manifest_self_sha256(payload)
        tampered = self.root / "refolded.json"
        tampered.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(SystemExit) as caught:
            RUNNER.main(
                self._base(
                    "--stage", "score", "--dev-smoke", "--run-name", "r",
                    "--force-h", "40", "--threshold-manifest", str(tampered),
                )
            )
        self.assertIn("fold_assignment_sha256", str(caught.exception))

    def test_a_manifest_from_another_normal_pool_is_refused(self) -> None:
        path = self._calibrate("--force-h", "40")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["inputs"]["normal_traces"]["sha256"] = "1" * 64
        payload["sha256"] = RUNNER.manifest_self_sha256(payload)
        moved = self.root / "moved.json"
        moved.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(SystemExit) as caught:
            RUNNER.main(
                self._base(
                    "--stage", "score", "--dev-smoke", "--run-name", "m",
                    "--force-h", "40", "--threshold-manifest", str(moved),
                )
            )
        self.assertIn("normal_traces_sha256", str(caught.exception))

    def test_stage_score_scores_every_arm_from_the_frozen_manifest(self) -> None:
        path = self._calibrate("--force-h", "40")
        code = RUNNER.main(
            self._base(
                "--stage", "score", "--dev-smoke", "--run-name", "score",
                "--force-h", "40", "--threshold-manifest", str(path),
                "--anchor", "x", "--hit-window", "e_view_to_anchor_plus_h",
                "--positives", "injection_present",
                "--compare-statistic", "P", "--outputs", "all",
            )
        )
        self.assertEqual(code, 0)
        result = json.loads(
            (self.root / "out" / "score" / "result.json").read_text(encoding="utf-8")
        )
        self.assertEqual(result["stage"], "score")
        self.assertTrue(result["threshold_manifest"]["verification"]["ok"])
        self.assertEqual(
            result["inputs"]["threshold_manifest_sha256"],
            json.loads(path.read_text(encoding="utf-8"))["sha256"],
        )
        cell = result["cells"]["S"]
        self.assertTrue(cell["restored_from_manifest"])
        self.assertTrue(all(b["restored_from_manifest"] for b in cell["folds"].values()))
        self.assertEqual(cell["metrics"]["pool"]["variants"].get("attack"), 21)
        anchored = cell["metrics"]["positives_anchored"]
        self.assertEqual(anchored["anchor"], "x")
        self.assertEqual(anchored["hit_window"], "e_view_to_anchor_plus_h")
        self.assertIn("x_beyond_h", anchored["reachability"])
        self.assertIn("injection_presence", cell["metrics"])
        self.assertIsNotNone(result["comparison_anchored"])
        self.assertIsNotNone(result["comparison_injection_present"])
        self.assertEqual(result["comparison_anchored"]["anchor"], "x")
        # item 10: outputs.jsonl carries the harmony channel of the endpoint
        rows = [
            json.loads(line)
            for line in (self.root / "out" / "score" / "outputs.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertTrue(rows)
        self.assertTrue(all("channel" in row for row in rows))
        self.assertTrue(set(r["channel"] for r in rows) <= set(io_g.ALL_TAGS))
        self.assertTrue(all("fold" in row for row in rows))

    def test_stage_score_refuses_a_head_that_is_not_the_freeze_commit(self) -> None:
        path = self._calibrate("--force-h", "40")
        with self.assertRaises(SystemExit) as caught:
            RUNNER.main(
                self._base(
                    "--stage", "score", "--dev-smoke", "--run-name", "fc",
                    "--force-h", "40", "--threshold-manifest", str(path),
                    "--freeze-commit", "0" * 40,
                )
            )
        self.assertIn("head_is_freeze_commit", str(caught.exception))

    def test_the_two_stage_and_the_single_process_forms_agree(self) -> None:
        path = self._calibrate("--force-h", "40")
        RUNNER.main(
            self._base(
                "--stage", "score", "--dev-smoke", "--run-name", "two",
                "--force-h", "40", "--threshold-manifest", str(path),
                "--anchor", "x", "--hit-window", "e_view_to_anchor_plus_h",
            )
        )
        RUNNER.main(
            self._base(
                "--stage", "single", "--dev-smoke", "--run-name", "one",
                "--force-h", "40",
                "--threshold-manifest", str(self.root / "single_manifest.json"),
                "--anchor", "x", "--hit-window", "e_view_to_anchor_plus_h",
            )
        )
        two = json.loads(
            (self.root / "out" / "two" / "result.json").read_text(encoding="utf-8")
        )
        one = json.loads(
            (self.root / "out" / "one" / "result.json").read_text(encoding="utf-8")
        )
        for payload in (one, two):
            payload["cells"]["S"]["metrics"]["positives_anchored"].pop("per_episode", None)
        self.assertEqual(
            one["cells"]["S"]["metrics"]["far"], two["cells"]["S"]["metrics"]["far"]
        )
        self.assertEqual(
            one["cells"]["S"]["metrics"]["positives_anchored"]["recall"],
            two["cells"]["S"]["metrics"]["positives_anchored"]["recall"],
        )

    def test_a_dev_smoke_is_refused_on_a_sealed_batch(self) -> None:
        sealed = self.root / "sealed"
        sealed.mkdir()
        (sealed / "SEALED.json").write_text("{}", encoding="utf-8")
        args = type("A", (), {"dev_smoke": True, "fit": None, "cal": None, "target": [sealed]})()
        with self.assertRaises(SystemExit) as caught:
            RUNNER.refuse_sealed_pools(args)
        self.assertIn("SEALED", str(caught.exception))

    def test_the_v31_path_gains_the_anchored_and_injection_blocks_on_demand(self) -> None:
        """items 3 / 6 are available on the FROZEN three-pool path too, and only there."""

        fit_scenarios = ",".join(f"s-{i:03d}" for i in range(0, 10))
        cal_scenarios = ",".join(f"s-{i:03d}" for i in range(10, 21))
        base = [
            "--fit", str(self.root / "batch"), "--fit-scenarios", fit_scenarios,
            "--cal", str(self.root / "batch"), "--cal-scenarios", cal_scenarios,
            "--target", str(self.root / "batch"),
            "--view", "V1", "--statistic", "S", "--compare-statistic", "P",
            "--alpha", "0.10", "--window-s", "8",
            "--h-min-survivors", "3", "--min-bucket-traces", "3",
            "--min-channel-windows", "3", "--min-channel-traces", "2",
            "--output-root", str(self.root / "out"),
            "--bootstrap-replicates", "50", "--dev-smoke",
        ]
        RUNNER.main(base + ["--run-name", "legacy_default"])
        plain = json.loads(
            (self.root / "out" / "legacy_default" / "result.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("positives_anchored", plain["cells"]["S"]["metrics"])
        self.assertNotIn("injection_presence", plain["cells"]["S"]["metrics"])
        self.assertIsNone(plain["comparison_anchored"])
        self.assertIsNone(plain["comparison_injection_present"])

        RUNNER.main(
            base
            + [
                "--run-name", "legacy_x",
                "--anchor", "x", "--hit-window", "e_view_to_anchor_plus_h",
                "--positives", "injection_present",
            ]
        )
        anchored = json.loads(
            (self.root / "out" / "legacy_x" / "result.json").read_text(encoding="utf-8")
        )
        metrics = anchored["cells"]["S"]["metrics"]
        self.assertEqual(metrics["positives_anchored"]["anchor"], "x")
        self.assertEqual(metrics["injection_presence"]["kind"], "injection_present")
        self.assertIsNotNone(anchored["comparison_anchored"])
        self.assertIsNotNone(anchored["comparison_injection_present"])
        # the v3.1 blocks are untouched by the extra ones
        self.assertEqual(
            plain["cells"]["S"]["metrics"]["far"], metrics["far"]
        )
        self.assertEqual(
            plain["comparison"]["bootstrap"]["point_estimate"],
            anchored["comparison"]["bootstrap"]["point_estimate"],
        )

    def test_the_fold_rotation_requires_cal_from_target(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            RUNNER.main(
                [
                    "--target", str(self.root / "batch"),
                    "--stage", "calibrate", "--normal-only-smoke",
                ]
            )
        self.assertIn("--cal-from-target", str(caught.exception))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
