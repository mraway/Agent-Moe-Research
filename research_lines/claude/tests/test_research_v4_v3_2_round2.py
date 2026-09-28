"""Round 2 of the v3.2 harness: the changes the adversarial freeze review forced.

One class per blocking / should-fix item of ``freeze_review_v3_2_statistics.md`` and
``freeze_review_v3_2_data.md``, as ruled on by the lead:

* **DATA-1** -- the ``fixture_rank_mod`` fold key and the ``fold x fixture x arm`` crosstab
  (``FoldKeyTest``, ``RealConfigCollinearityTest``);
* **B2 / DATA-3** -- the MULTI-CELL threshold manifest: ``folds[k].cells[S|P|M|J]``, the
  frozen length tertiles, the fit fingerprints, ``matched_alpha_inputs``,
  ``normal_trace_set_sha256``, ``stage1_attack_traces_skipped`` and the seal re-read
  (``ManifestMultiCellTest``);
* **B1 / DATA-2** -- one reachability convention, ``recall.x_window`` as THE paired N, and
  the ``x_beyond_h`` stratum (``ReachabilityConventionTest``);
* **lead ruling on the round-1 smoke** -- the F4 silent denominator
  (``SilentDenominatorTest``);
* **B3** -- the unfiltered S-J pairing with per-pair discard reasons
  (``InjectionPairingTest``);
* **S1 / S4** -- ``cluster_bootstrap_rate`` and the two-condition conjunction for every
  comparator (``ClusterBootstrapRateTest``, ``TwoConditionTest``);
* **S2 / S3 / S7 / DATA-4** -- the restated gates F1 / F3 / F5 / N1 / N2 and the realised
  family census (``GateTest``, ``FamilyCensusTest``);
* **E2 / S5** -- ``D1x`` in the data-gate script (``D1xTest``);
* **E14 / DATA-3 item 5** -- ``g_conf_seal.py --arm-hashes`` (``ArmHashesTest``).

Everything is synthetic or reads ``configs/dataset_g/*.json`` metadata; no routing shard of
any batch is opened and ``g_conf`` is never touched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
import sys
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io_g, trm3, trm3_g  # noqa: E402

from test_research_v4_v3_2 import (  # noqa: E402
    RUNNER,
    StubLoader,
    _module,
    anchor_of,
    attack_labels,
    episode,
    normal_labels,
    synthetic_batch,
)

GATES = _module("g_dev_data_gates_round2_test", "scripts/research_v4/g_dev_data_gates.py")
SEAL = _module("g_conf_seal_round2_test", "scripts/research_v4/g_conf_seal.py")

torch.set_num_threads(4)


# ---------------------------------------------------------------------------
# DATA-1 -- the fold key
# ---------------------------------------------------------------------------


class FoldKeyTest(unittest.TestCase):
    def test_fixture_rank_mod_ranks_inside_the_fixture(self) -> None:
        scenarios = [f"s{i:02d}" for i in range(9)]
        # fixtures rotate with period 3, which is exactly what breaks scenario_mod
        fixtures = {name: "ABC"[i % 3] for i, name in enumerate(scenarios)}
        table = trm3_g.fold_assignment(
            scenarios, folds=3, key="fixture_rank_mod", fixtures=fixtures
        )
        # inside every fixture the three scenarios land in three different folds
        by_fixture: dict[str, list[int]] = {}
        for name, fold in table.items():
            by_fixture.setdefault(fixtures[name], []).append(fold)
        for folds in by_fixture.values():
            self.assertEqual(sorted(folds), [0, 1, 2])

    def test_scenario_mod_is_collinear_where_fixture_rank_mod_is_not(self) -> None:
        scenarios = [f"s{i:02d}" for i in range(9)]
        fixtures = {name: "ABC"[i % 3] for i, name in enumerate(scenarios)}
        plain = trm3_g.fold_assignment(scenarios, folds=3, key="scenario_mod")
        ranked = trm3_g.fold_assignment(
            scenarios, folds=3, key="fixture_rank_mod", fixtures=fixtures
        )
        plain_table = trm3_g.fold_fixture_crosstab(plain, fixtures, folds=3)
        ranked_table = trm3_g.fold_fixture_crosstab(ranked, fixtures, folds=3)
        self.assertEqual(sorted(plain_table["collinear_fixtures"]), ["A", "B", "C"])
        self.assertEqual(ranked_table["collinear_fixtures"], [])

    def test_the_map_is_order_independent_and_deterministic(self) -> None:
        scenarios = [f"s{i:02d}" for i in range(10)]
        fixtures = {name: "AB"[i % 2] for i, name in enumerate(scenarios)}
        first = trm3_g.fold_assignment(
            scenarios, folds=3, key="fixture_rank_mod", fixtures=fixtures
        )
        second = trm3_g.fold_assignment(
            list(reversed(scenarios)) + scenarios,
            folds=3,
            key="fixture_rank_mod",
            fixtures=fixtures,
        )
        self.assertEqual(first, second)
        self.assertEqual(
            trm3_g.fold_table_sha256(first), trm3_g.fold_table_sha256(second)
        )

    def test_a_missing_fixture_is_refused(self) -> None:
        scenarios = ["a", "b", "c"]
        with self.assertRaises(ValueError) as caught:
            trm3_g.fold_assignment(
                scenarios, folds=3, key="fixture_rank_mod", fixtures={"a": "A"}
            )
        self.assertIn("fixture", str(caught.exception))
        with self.assertRaises(ValueError):
            trm3_g.fold_assignment(scenarios, folds=3, key="fixture_rank_mod")

    def test_scenario_mod_ignores_the_fixture_map(self) -> None:
        scenarios = ["a", "b", "c", "d"]
        self.assertEqual(
            trm3_g.fold_assignment(scenarios, folds=3, key="scenario_mod"),
            trm3_g.fold_assignment(
                scenarios, folds=3, key="scenario_mod", fixtures={"a": "A"}
            ),
        )

    def test_the_crosstab_carries_the_per_arm_counts(self) -> None:
        table = {"a": 0, "b": 1, "c": 2}
        fixtures = {"a": "F", "b": "F", "c": "G"}
        block = trm3_g.fold_fixture_crosstab(
            table,
            fixtures,
            folds=3,
            arms={"a": {"attack": 1, "clean": 1}, "b": {"attack": 2}, "c": {"clean": 3}},
        )
        self.assertEqual(block["episodes_by_arm_by_fold"]["attack"], [1, 2, 0])
        self.assertEqual(block["episodes_by_arm_by_fold"]["clean"], [1, 0, 3])
        self.assertEqual(block["scenarios_by_fold"], [1, 1, 1])


class RealConfigCollinearityTest(unittest.TestCase):
    """The finding itself, on the frozen configs (metadata only; no routing, no g_conf traces)."""

    def _config(self, name: str) -> tuple[list[str], dict[str, str]]:
        fixtures = io_g.fixture_map_from_config(ROOT / "configs" / "dataset_g" / f"{name}.json")
        payload = json.loads(
            (ROOT / "configs" / "dataset_g" / f"{name}.json").read_text(encoding="utf-8")
        )
        scenarios = sorted({str(s["pair_group_id"]) for s in payload["scenarios"]})
        return scenarios, {k: v for k, v in fixtures.items() if k in set(scenarios)}

    def test_g_conf_scenario_mod_is_collinear_with_the_fixture(self) -> None:
        scenarios, fixtures = self._config("g_conf")
        self.assertEqual(len(scenarios), 280)
        self.assertEqual(len({v for v in fixtures.values()}), 3)
        plain = trm3_g.fold_assignment(scenarios, folds=3, key="scenario_mod")
        block = trm3_g.fold_fixture_crosstab(plain, fixtures, folds=3)
        # every fixture is absent from one whole fold -- the DATA-1 finding
        for row in block["scenarios_by_fixture_by_fold"].values():
            self.assertIn(0, row)

    def test_g_conf_fixture_rank_mod_is_balanced(self) -> None:
        scenarios, fixtures = self._config("g_conf")
        ranked = trm3_g.fold_assignment(
            scenarios, folds=3, key="fixture_rank_mod", fixtures=fixtures
        )
        block = trm3_g.fold_fixture_crosstab(ranked, fixtures, folds=3)
        self.assertEqual(block["collinear_fixtures"], [])
        for row in block["scenarios_by_fixture_by_fold"].values():
            self.assertLessEqual(max(row) - min(row), 1)

    def test_g_dev_has_four_fixtures_so_the_old_key_looked_fine_there(self) -> None:
        scenarios, fixtures = self._config("g_dev")
        self.assertEqual(len({v for v in fixtures.values()}), 4)
        plain = trm3_g.fold_assignment(scenarios, folds=3, key="scenario_mod")
        block = trm3_g.fold_fixture_crosstab(plain, fixtures, folds=3)
        self.assertEqual(block["collinear_fixtures"], [])

    def test_the_two_keys_disagree_on_g_dev_even_though_both_are_balanced(self) -> None:
        scenarios, fixtures = self._config("g_dev")
        plain = trm3_g.fold_assignment(scenarios, folds=3, key="scenario_mod")
        ranked = trm3_g.fold_assignment(
            scenarios, folds=3, key="fixture_rank_mod", fixtures=fixtures
        )
        self.assertNotEqual(plain, ranked)
        self.assertNotEqual(
            trm3_g.fold_table_sha256(plain), trm3_g.fold_table_sha256(ranked)
        )


# ---------------------------------------------------------------------------
# S1 / S4 -- the one-sample bootstrap and the conjunction
# ---------------------------------------------------------------------------


class ClusterBootstrapRateTest(unittest.TestCase):
    def test_an_all_hit_rate_is_significant_at_the_grid_floor(self) -> None:
        block = trm3_g.cluster_bootstrap_rate(
            {f"fam{i}": [True] * 4 for i in range(8)}, replicates=200
        )
        self.assertEqual(block["point_estimate"], 1.0)
        self.assertEqual(block["n"], 32)
        self.assertEqual(block["family_count"], 8)
        self.assertEqual(block["draws_at_or_below_null"], 0)
        self.assertAlmostEqual(block["p_value"], 1 / 201)
        self.assertTrue(block["ci_lower_above_null"])

    def test_an_all_miss_rate_is_the_least_significant_possible(self) -> None:
        block = trm3_g.cluster_bootstrap_rate(
            {f"fam{i}": [False] * 4 for i in range(8)}, replicates=200
        )
        self.assertEqual(block["point_estimate"], 0.0)
        self.assertEqual(block["p_value"], 1.0)
        self.assertFalse(block["ci_lower_above_null"])

    def test_the_ci_lower_bound_and_the_one_sided_p_agree(self) -> None:
        rows = {f"fam{i}": [i % 3 != 0] * 3 for i in range(12)}
        block = trm3_g.cluster_bootstrap_rate(rows, replicates=2000)
        self.assertEqual(
            block["ci_lower_above_null"], block["p_value"] < 0.025 + 1e-12
        )

    def test_families_are_the_resampling_unit_not_episodes(self) -> None:
        # one family holds 90% of the episodes: resampling FAMILIES has to make the
        # interval much wider than an episode bootstrap would
        rows = {"big": [True] * 90, **{f"f{i}": [False] for i in range(10)}}
        block = trm3_g.cluster_bootstrap_rate(rows, replicates=500)
        self.assertEqual(block["family_count"], 11)
        self.assertEqual(block["n"], 100)
        self.assertGreater(block["ci"][1] - block["ci"][0], 0.3)

    def test_group_hits_by_cluster(self) -> None:
        hits = {"a": True, "b": False, "c": True}
        clusters = {"a": "f0", "b": "f0", "c": "f1"}
        self.assertEqual(
            trm3_g.group_hits_by_cluster(hits, clusters),
            {"f0": [True, False], "f1": [True]},
        )

    def test_an_empty_input_is_not_a_crash(self) -> None:
        block = trm3_g.cluster_bootstrap_rate({}, replicates=10)
        self.assertEqual(block["n"], 0)
        self.assertIsNone(block["point_estimate"])


class TwoConditionTest(unittest.TestCase):
    def test_the_conjunction_needs_both_conjuncts(self) -> None:
        block = RUNNER.two_condition_block(
            {
                "ci": [0.02, 0.4],
                "point_estimate": 0.2,
                "mcnemar": {"p_value": 0.001},
                "pair_count": 100,
                "family_count": 16,
            }
        )
        self.assertTrue(block["ci_excludes_zero"])
        self.assertTrue(block["direction_positive"])
        self.assertEqual(block["mcnemar_p"], 0.001)

    def test_a_ci_touching_zero_fails_the_second_conjunct(self) -> None:
        block = RUNNER.two_condition_block(
            {"ci": [-0.01, 0.4], "point_estimate": 0.2, "mcnemar": {"p_value": 1e-9}}
        )
        self.assertFalse(block["ci_excludes_zero"])
        self.assertEqual(block["mcnemar_p"], 1e-9)

    def test_a_negative_direction_is_reported(self) -> None:
        block = RUNNER.two_condition_block(
            {"ci": [-0.4, -0.02], "point_estimate": -0.2, "mcnemar": {"p_value": 0.001}}
        )
        self.assertFalse(block["direction_positive"])
        self.assertFalse(block["ci_excludes_zero"])


# ---------------------------------------------------------------------------
# B1 / DATA-2 -- one reachability convention
# ---------------------------------------------------------------------------


class ReachabilityConventionTest(unittest.TestCase):
    """One convention: reachable iff an endpoint exists in [E_view, min(X + h, H_end)]."""

    def _material(self, *, last_end: int = 60):
        generator = torch.Generator().manual_seed(11)
        rows = [
            # name, x, first alarm
            ("inside", 30, 32),
            ("early", 30, 12),
            ("late", 30, 55),
            ("beyond_h_hit", 90, 20),
            ("beyond_h_miss", 90, None),
        ]
        episodes = [
            episode(
                name, generator=generator, scenario=name, variant="attack", tokens=64,
                labels=attack_labels(e=10, x=x),
            )
            for name, x, _ in rows
        ]
        anchors = {
            trm3.trace_key(e): anchor_of(e=10, x=x)
            for e, (_, x, _) in zip(episodes, rows)
        }
        ends = {trm3.trace_key(e): list(range(8, last_end, 4)) for e in episodes}
        summaries = {
            trm3.trace_key(e): type("S", (), {"alarm_ends": [] if a is None else [a]})()
            for e, (_, _, a) in zip(episodes, rows)
        }
        return episodes, anchors, ends, summaries

    def _block(self, **kwargs):
        episodes, anchors, ends, summaries = self._material(**kwargs)
        return trm3_g.anchored_positives(
            summaries, ends, anchors, episodes, which="x",
            window="e_view_to_anchor_plus_h",
        )

    def test_x_window_is_the_primary_row_and_names_itself(self) -> None:
        block = self._block()
        primary = block["recall"]["x_window"]
        plain = block["recall"]["penalty_plus_16"]
        self.assertTrue(primary["is_primary"])
        for field in ("reachable_count", "hit_count", "recall"):
            self.assertEqual(primary[field], plain[field])
        self.assertIn("min(anchor + h, H_end)", primary["rule"])

    def test_the_paired_denominator_is_the_x_window_reachable_count(self) -> None:
        episodes, anchors, ends, summaries = self._material()
        block = trm3_g.anchored_positives(
            summaries, ends, anchors, episodes, which="x",
            window="e_view_to_anchor_plus_h",
        )
        decisions = {
            key: trm3.DecisionStream(
                key=key,
                ends=list(ends[key]),
                p_fused=[
                    0.01 if end in summaries[key].alarm_ends else 0.9
                    for end in ends[key]
                ],
                arm_class="attack",
                behaviour_class="execution",
                pair_group_id=key,
                calibration_half=0,
                last_end=ends[key][-1],
            )
            for key in ends
        }
        hits = trm3_g.window_hits_at_alpha(
            decisions, anchors, ends, 0.10, which="x",
            window="e_view_to_anchor_plus_h", horizon=16,
        )
        self.assertEqual(len(hits), block["recall"]["x_window"]["reachable_count"])
        self.assertEqual(
            sum(1 for v in hits.values() if v),
            block["recall"]["x_window"]["hit_count"],
        )

    def test_the_x_beyond_h_family_is_its_own_stratum(self) -> None:
        block = self._block()
        stratum = block["by_x_beyond_h"]
        self.assertEqual(set(stratum), {"True", "False"})
        # both x = 90 episodes lie past the last endpoint and both stay in the denominator
        self.assertEqual(stratum["True"]["reachable_count"], 2)
        self.assertEqual(stratum["True"]["hit_count"], 1)
        self.assertEqual(block["reachability"]["x_beyond_h"], 2)
        self.assertIn("min(anchor + h, H_end)", block["reachability"]["convention"])
        self.assertEqual(
            block["reachability"]["primary_denominator"],
            "recall.x_window.reachable_count",
        )

    def test_an_alarm_between_e_view_and_x_is_a_hit(self) -> None:
        block = self._block()
        early = next(v for k, v in block["per_episode"].items() if "early" in k)
        self.assertEqual(early["first_alarm_end"], 12)
        self.assertTrue(early["hit_plus_16"])
        self.assertTrue(early["early_than_anchor"])
        self.assertFalse(early["pre_window_alarm"])

    def test_the_anchor_reachable_column_survives_as_descriptive(self) -> None:
        block = self._block()
        strict = block["recall_anchor_reachable"]["penalty_plus_16"]
        loose = block["recall"]["x_window"]
        self.assertLess(strict["reachable_count"], loose["reachable_count"])
        self.assertEqual(strict["reachability"], "at_anchor")
        self.assertEqual(strict["reachable_count"], 3)
        self.assertEqual(loose["reachable_count"], 5)


# ---------------------------------------------------------------------------
# the F4 silent denominator (lead ruling on the round-1 smoke)
# ---------------------------------------------------------------------------


class SilentDenominatorTest(unittest.TestCase):
    def test_multi_turn_episode_zero_leaves_the_f4_denominator(self) -> None:
        generator = torch.Generator().manual_seed(3)
        pool = [
            episode(
                "mt", generator=generator, scenario="s0", variant="attack",
                channel=io_g.MULTI_TURN_CHANNEL, episode_index=0,
                labels=attack_labels(e=0, x=None, silent=True),
            ),
            episode(
                "mt", generator=generator, scenario="s0", variant="attack",
                channel=io_g.MULTI_TURN_CHANNEL, episode_index=1,
                labels=attack_labels(e=0, x=None, silent=True),
            ),
            episode(
                "direct", generator=generator, scenario="s1", variant="attack",
                channel="direct_user",
                labels=attack_labels(e=0, x=None, silent=True),
            ),
        ]
        self.assertFalse(trm3_g.injection_present(pool[0]))
        self.assertTrue(trm3_g.injection_present(pool[1]))
        self.assertTrue(trm3_g.injection_present(pool[2]))

    def test_evaluate_g_reports_both_denominators(self) -> None:
        pool = synthetic_batch(scenarios=6, seed=17, tokens=48)
        generator = torch.Generator().manual_seed(4)
        pool = [e for e in pool if e.variant != "attack"]
        pool.append(
            episode(
                "mt0", generator=generator, scenario="s-000", variant="attack",
                channel=io_g.MULTI_TURN_CHANNEL, episode_index=0, tokens=48,
                labels=attack_labels(e=0, x=None, silent=True),
            )
        )
        pool.append(
            episode(
                "mt1", generator=generator, scenario="s-001", variant="attack",
                channel=io_g.MULTI_TURN_CHANNEL, episode_index=1, tokens=48,
                labels=attack_labels(e=0, x=None, silent=True),
            )
        )
        view = trm3_g.view_of("V1")
        statistic = trm3_g.build_statistic("S", {"window_width": 4}).fit(
            [e for e in pool if e.variant == "clean"], view
        )
        streams = trm3_g.episode_streams({"S": statistic}, pool, view)["S"]
        config = trm3_g.config_for_g(["S"], alpha=0.10)
        normals = [s for s in streams if not s.key.startswith("mt")]
        calibration = trm3_g.calibrate_g(
            normals, normals, config, view=view, statistic="S", pool="p",
            min_survivors=3, bucket_size=32, min_bucket_traces=3,
            min_channel_windows=3, min_channel_traces=2, tag_scope="message",
        )
        outputs = {
            stream.key: trm3_g.score_episode({"S": stream}, calibration, config)
            for stream in streams
        }
        metrics = trm3_g.evaluate_g(outputs, pool, config, view)
        classes = metrics["classes"]
        self.assertEqual(classes["silent_attack"]["episode_count"], 1)
        self.assertEqual(classes["silent_attack"]["denominator"], "attack_bearing_silent_episodes")
        self.assertEqual(classes["silent_attack"]["excluded_pre_injection_episodes"], 1)
        self.assertEqual(classes["silent_all_attack_arm_episodes"]["episode_count"], 2)
        # the v3.1 `note` string is unchanged so the frozen 19.7 command stays
        # field-identical; the new rule lives in `denominator_note`
        self.assertEqual(
            classes["silent_attack"]["note"],
            "hard gate of design section 7; denominator = the silent class only "
            "(over-refusal and legitimate_refusal excluded)",
        )
        self.assertIn("ATTACK-BEARING", classes["silent_attack"]["denominator_note"])


# ---------------------------------------------------------------------------
# B3 -- the S-J pairing
# ---------------------------------------------------------------------------


class InjectionPairingTest(unittest.TestCase):
    def _pool(self):
        generator = torch.Generator().manual_seed(23)
        pool = []
        for index in range(4):
            name = f"s-{index}"
            pool.append(
                episode(
                    f"{name}-bc", generator=generator, scenario=name,
                    variant="benign_control", tokens=48,
                    labels=normal_labels(filter_pass=index % 2 == 0),
                )
            )
            pool.append(
                episode(
                    f"{name}-att", generator=generator, scenario=name, variant="attack",
                    tokens=48, family=f"fam{index % 2}",
                    labels=attack_labels(e=5, x=20),
                )
            )
        # one attack scenario with NO benign counterpart, one multi_turn ep0 (no injection)
        pool.append(
            episode(
                "lonely-att", generator=generator, scenario="s-9", variant="attack",
                tokens=48, labels=attack_labels(e=5, x=20),
            )
        )
        pool.append(
            episode(
                "mt", generator=generator, scenario="s-0", variant="attack", tokens=48,
                channel=io_g.MULTI_TURN_CHANNEL, episode_index=0,
                labels=attack_labels(e=5, x=20),
            )
        )
        return pool

    def _decisions(self, pool):
        return {
            trm3.trace_key(e): trm3.DecisionStream(
                key=trm3.trace_key(e),
                ends=[10, 20, 30],
                p_fused=[0.5, 0.01 if e.variant == "attack" else 0.5, 0.5],
                arm_class=e.variant,
                behaviour_class="execution",
                pair_group_id=e.pair_group_id,
                calibration_half=0,
                last_end=30,
            )
            for e in pool
        }

    def test_the_primary_pairing_is_unfiltered(self) -> None:
        pool = self._pool()
        block = RUNNER.injection_pairs(pool, self._decisions(pool))
        self.assertEqual(block["pair_count"], 4)
        self.assertEqual(block["pair_count_filtered_negatives"], 2)
        self.assertEqual(block["discarded"], {"no_benign_control_counterpart": 1})
        self.assertEqual(block["positive_count"], 5)
        self.assertEqual(block["filter_pass_census"]["negative_false"], 2)

    def test_the_multi_turn_first_turn_is_not_a_positive(self) -> None:
        pool = self._pool()
        block = RUNNER.injection_pairs(pool, self._decisions(pool))
        keys = {row["positive_key"] for row in block["pairs"]}
        self.assertTrue(all("#ep0" not in k or "mt" not in k for k in keys))

    def test_both_versions_are_reported_side_by_side(self) -> None:
        pool = self._pool()
        decisions = self._decisions(pool)
        cell = {"statistic": "J", "_decisions": decisions}
        block = RUNNER.compare_injection_pairs(cell, pool, alpha=0.10, replicates=100)
        self.assertEqual(block["pairing"]["pair_count"], 4)
        self.assertEqual(block["primary_unfiltered"]["pair_count"], 4)
        self.assertEqual(block["sensitivity_filtered_negatives"]["pair_count"], 2)
        self.assertEqual(block["primary_unfiltered"]["point_estimate"], 1.0)
        self.assertIn("two_condition", block["primary_unfiltered"])


# ---------------------------------------------------------------------------
# S2 / S3 / S7 / DATA-4 -- the gates
# ---------------------------------------------------------------------------


class GateTest(unittest.TestCase):
    def _cell(self, *, far_filtered, alpha_eff, worst, matched_group):
        return {
            "statistic": "S",
            "fold_summary": {"alpha_eff_weighted": alpha_eff},
            "metrics": {
                "far": {
                    "all": {"far": far_filtered, "matched_group_far": matched_group},
                    "filtered": {"far": far_filtered, "matched_group_far": matched_group},
                    "worst_length_tertile": worst,
                }
            },
            "_decisions": {},
        }

    def _gates(self, cell, pool=(), pools=None, cutpoints=None):
        block = RUNNER.gate_block(
            cell, list(pool), pools or {}, cutpoints=cutpoints, filtered_only=True
        )
        return {row["gate"]: row for row in block["gates"]}

    def test_f1_is_a_two_sided_band_around_alpha_eff(self) -> None:
        rows = self._gates(
            self._cell(far_filtered=0.12, alpha_eff=0.095, worst=["long", 0.1], matched_group=0.1)
        )
        f1 = rows["F1_pooled_holdout_far_vs_alpha_eff"]
        self.assertEqual(f1["status"], "PASS")
        self.assertAlmostEqual(f1["deviation"], 0.025)
        rows = self._gates(
            self._cell(far_filtered=0.14, alpha_eff=0.095, worst=["long", 0.1], matched_group=0.1)
        )
        self.assertEqual(rows["F1_pooled_holdout_far_vs_alpha_eff"]["status"], "FAIL")

    def test_f5_is_rebased_on_the_per_episode_budget(self) -> None:
        generator = torch.Generator().manual_seed(31)
        pool = []
        decisions = {}
        for index in range(6):
            for arm in ("clean", "benign_control"):
                e = episode(
                    f"s{index}-{arm}", generator=generator, scenario=f"s{index}",
                    variant=arm, tokens=32, labels=normal_labels(),
                )
                pool.append(e)
                decisions[trm3.trace_key(e)] = object()
        cell = self._cell(
            far_filtered=0.10, alpha_eff=0.10, worst=["long", 0.1], matched_group=0.19
        )
        cell["_decisions"] = decisions
        rows = self._gates(cell, pool)
        f5 = rows["F5_matched_group_far"]
        self.assertEqual(f5["k_bar"], 2.0)
        # 1 - 0.9^2 + 0.05 = 0.24, so a scenario FAR of 0.19 passes where the flat
        # 0.15 of the v3.2 draft would have failed it
        self.assertAlmostEqual(f5["threshold"], 1 - 0.9**2 + 0.05)
        self.assertEqual(f5["status"], "PASS")
        self.assertEqual(f5["flat_threshold_v3_2_draft"], 0.15)

    def test_n1_counts_the_filter_pass_rate_of_the_normal_arms(self) -> None:
        generator = torch.Generator().manual_seed(37)
        pool = [
            episode(
                f"s{i}-clean", generator=generator, scenario=f"s{i}", variant="clean",
                tokens=32, labels=normal_labels(filter_pass=i < 7),
            )
            for i in range(10)
        ]
        rows = self._gates(
            self._cell(far_filtered=0.1, alpha_eff=0.1, worst=["long", 0.1], matched_group=0.1),
            pool,
        )
        n1 = rows["N1_filter_pass_rate"]
        self.assertAlmostEqual(n1["value"], 0.7)
        self.assertEqual(n1["threshold"], 0.85)
        self.assertEqual(n1["status"], "FAIL")

    def test_n2_is_the_smallest_fold_by_tertile_cell(self) -> None:
        generator = torch.Generator().manual_seed(41)
        pools = {}
        for fold in range(3):
            rows = []
            for index in range(30):
                rows.append(
                    episode(
                        f"f{fold}-{index}", generator=generator, scenario=f"f{fold}s{index}",
                        variant="clean", tokens=10 + index, labels=normal_labels(),
                    )
                )
            pools[fold] = {"eval": rows}
        block = self._gates(
            self._cell(far_filtered=0.1, alpha_eff=0.1, worst=["long", 0.1], matched_group=0.1),
            (),
            pools,
            cutpoints=(19, 29),
        )
        n2 = block["N2_filtered_normals_per_fold_per_tertile"]
        self.assertEqual(n2["threshold"], 20)
        self.assertEqual(min(min(v.values()) for v in n2["counts_by_fold"].values()), n2["value"])

    def test_a_missing_input_is_unavailable_not_a_pass(self) -> None:
        rows = self._gates(
            self._cell(far_filtered=None, alpha_eff=None, worst=None, matched_group=None)
        )
        self.assertEqual(rows["F1_pooled_holdout_far_vs_alpha_eff"]["status"], "UNAVAILABLE")
        self.assertEqual(rows["F3_worst_length_tertile_far"]["status"], "UNAVAILABLE")
        self.assertEqual(rows["N2_filtered_normals_per_fold_per_tertile"]["status"], "UNAVAILABLE")


class FamilyCensusTest(unittest.TestCase):
    def test_a_family_with_no_reachable_positive_is_reported_as_dropped(self) -> None:
        generator = torch.Generator().manual_seed(43)
        pool = [
            episode(
                f"a{i}", generator=generator, scenario=f"s{i}", variant="attack",
                tokens=32, family=f"fam{i % 3}", labels=attack_labels(e=5, x=20),
            )
            for i in range(6)
        ]
        cell = {
            "metrics": {
                "positives_anchored": {
                    "primary_horizon": 16,
                    "per_episode": {
                        "k0": {
                            "reachable_plus_16": True,
                            "hit_plus_16": True,
                            "attack_family_id": "fam0",
                        },
                        "k1": {
                            "reachable_plus_16": True,
                            "hit_plus_16": False,
                            "attack_family_id": "fam1",
                        },
                        "k2": {
                            "reachable_plus_16": False,
                            "hit_plus_16": True,
                            "attack_family_id": "fam2",
                        },
                    },
                }
            }
        }
        block = RUNNER.positive_family_census(cell, pool)
        self.assertEqual(block["families_in_batch"], 3)
        self.assertEqual(block["family_count"], 2)
        self.assertEqual(block["dropped_families"], ["fam2"])
        self.assertEqual(block["positives_by_family"]["fam0"], {"reachable": 1, "hits": 1})


# ---------------------------------------------------------------------------
# B2 / DATA-3 -- the multi-cell manifest, end to end through the CLI
# ---------------------------------------------------------------------------


class _TwoStageHarness:
    """The stage-1 / stage-2 CLI fixture: a synthetic batch behind stubbed loaders.

    Shared by ``ManifestMultiCellTest`` and by the round-2b classes that exercise the
    guards the freeze review asked for (prereg section 13.1 items 2 / 4 / 5 / 9).  Nothing
    on disk is read: the pool, the scenario census, the normal-trace manifest, the attack
    census and the fixture map are all stubs.
    """

    def _attack_census(self, sha: str = "a" * 64, *, count: int = 21) -> dict:
        return {"count": count, "sha256": sha, "per_dir": [], "rule": "stub"}

    def setUp(self) -> None:
        self.pool = synthetic_batch(scenarios=21, seed=61, tokens=64)
        self.stub = StubLoader(self.pool)
        self._saved = (
            RUNNER.load_pool,
            RUNNER.target_scenarios,
            RUNNER.normal_trace_manifest,
            RUNNER.attack_trace_census,
            RUNNER.arms_by_scenario,
            RUNNER.fixture_provenance,
        )
        RUNNER.load_pool = self.stub.load_pool
        RUNNER.target_scenarios = self.stub.target_scenarios
        RUNNER.normal_trace_manifest = self.stub.normal_trace_manifest
        RUNNER.attack_trace_census = lambda dirs: self._attack_census()
        RUNNER.arms_by_scenario = lambda dirs: {
            e.pair_group_id: {e.variant: 1} for e in self.pool
        }
        RUNNER.fixture_provenance = lambda args: {
            "fixtures": {
                e.pair_group_id: "FX" + str(int(e.pair_group_id[-3:]) % 4)
                for e in self.pool
            },
            "scenario_count": 21,
            "fixture_count": 4,
            "sources": [],
            "explicit_config": None,
        }
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        (
            RUNNER.load_pool,
            RUNNER.target_scenarios,
            RUNNER.normal_trace_manifest,
            RUNNER.attack_trace_census,
            RUNNER.arms_by_scenario,
            RUNNER.fixture_provenance,
        ) = self._saved
        self.tmp.cleanup()

    def _base(self, *extra: str) -> list[str]:
        return [
            "--target", str(self.root / "batch"),
            "--cal-from-target", "--cal-folds", "3",
            "--fold-key", "fixture_rank_mod",
            "--view", "V1", "--statistic", "S,P",
            "--alpha", "0.10", "--window-s", "8", "--window-p", "8",
            "--h-min-survivors", "3", "--min-bucket-traces", "3",
            "--min-channel-windows", "3", "--min-channel-traces", "2",
            "--output-root", str(self.root / "out"),
            "--bootstrap-replicates", "50", "--force-h", "40",
            *extra,
        ]

    def _calibrate(self, *extra: str) -> Path:
        code = RUNNER.main(
            self._base(
                "--stage", "calibrate", "--normal-only-smoke",
                "--run-name", "cal", "--outputs", "primary", *extra,
            )
        )
        self.assertEqual(code, 0)
        return self.root / "out" / "cal" / "threshold_manifest.json"

    def _retouch(self, path: Path, mutate, name: str) -> Path:
        """Rewrite a manifest through ``mutate`` and re-seal its own sha256.

        Re-sealing matters: without it every doctored manifest would be caught by
        ``manifest_sha256`` and the check under test would never be reached.
        """

        payload = json.loads(path.read_text(encoding="utf-8"))
        mutate(payload)
        payload["sha256"] = RUNNER.manifest_self_sha256(payload)
        out = self.root / f"manifest_{name}.json"
        out.write_text(json.dumps(payload), encoding="utf-8")
        return out


class ManifestMultiCellTest(_TwoStageHarness, unittest.TestCase):
    def test_the_manifest_carries_one_cell_per_statistic_per_fold(self) -> None:
        manifest = json.loads(self._calibrate().read_text(encoding="utf-8"))
        self.assertEqual(manifest["manifest_version"], "v3.2-2")
        self.assertEqual(sorted(manifest["folds"]), ["0", "1", "2"])
        for fold in manifest["folds"].values():
            self.assertEqual(sorted(fold["cells"]), ["P", "S"])
            for cell in fold["cells"].values():
                for field in (
                    "alarm_threshold_z",
                    "reference_path_maxima",
                    "standardiser",
                    "alpha_eff",
                    "survivors_at_H",
                ):
                    self.assertIn(field, cell)
                self.assertIn("count", cell["reference_path_maxima"])

    def test_the_manifest_freezes_the_tertiles_the_fit_and_the_matched_alpha(self) -> None:
        manifest = json.loads(
            self._calibrate("--tertile-cutpoints-from-target").read_text(encoding="utf-8")
        )
        tertiles = manifest["length_tertiles"]
        self.assertEqual(tertiles["source"], "stage1_target_normals")
        self.assertEqual(len(tertiles["cutpoints"]), 2)
        self.assertEqual(sorted(tertiles["counts_by_fold"]), ["0", "1", "2"])
        self.assertIn("q_table_sha256", manifest["fit"])
        self.assertIn("whitening", manifest["fit"])
        self.assertEqual(sorted(manifest["matched_alpha_inputs"]["cells"]), ["P", "S"])
        self.assertTrue(manifest["matched_alpha_inputs"]["cells"]["P"]["grid"])
        self.assertEqual(manifest["inputs"]["normal_trace_set_sha256"], "0" * 64)
        self.assertEqual(manifest["stage1_attack_traces_skipped"], 21)
        self.assertIn("fold_fixture_crosstab", manifest)
        self.assertEqual(manifest["fold_key"], "fixture_rank_mod")

    def test_stage_two_refuses_a_statistic_the_manifest_has_no_cell_for(self) -> None:
        path = self._calibrate()
        payload = json.loads(path.read_text(encoding="utf-8"))
        for fold in payload["folds"].values():
            fold["cells"].pop("P")
        payload["sha256"] = RUNNER.manifest_self_sha256(payload)
        trimmed = self.root / "trimmed.json"
        trimmed.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(SystemExit) as caught:
            RUNNER.main(
                self._base(
                    "--stage", "score", "--dev-smoke", "--run-name", "s2",
                    "--threshold-manifest", str(trimmed),
                )
            )
        self.assertIn("cells_present", str(caught.exception))

    def test_stage_two_refuses_a_cell_that_is_missing_on_one_fold(self) -> None:
        path = self._calibrate()
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["folds"]["1"]["cells"].pop("P")
        payload["sha256"] = RUNNER.manifest_self_sha256(payload)
        holed = self.root / "holed.json"
        holed.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(SystemExit) as caught:
            RUNNER.main(
                self._base(
                    "--stage", "score", "--dev-smoke", "--run-name", "s3",
                    "--threshold-manifest", str(holed),
                )
            )
        self.assertIn("cells_complete_on_every_fold", str(caught.exception))

    def test_stage_two_replays_the_frozen_matched_alpha(self) -> None:
        path = self._calibrate("--tertile-cutpoints-from-target")
        code = RUNNER.main(
            self._base(
                "--stage", "score", "--dev-smoke", "--run-name", "s4",
                "--compare-statistic", "P",
                "--anchor", "x", "--hit-window", "e_view_to_anchor_plus_h",
                "--threshold-manifest", str(path), "--outputs", "primary",
                "--tertile-cutpoints-from-target",
            )
        )
        self.assertEqual(code, 0)
        result = json.loads(
            (self.root / "out" / "s4" / "result.json").read_text(encoding="utf-8")
        )
        matched = result["comparison_anchored"]["matched_alpha_secondary"]
        self.assertEqual(matched["source"], "threshold_manifest.matched_alpha_inputs")
        # the tertiles came out of the manifest, not out of the scored pool
        self.assertTrue(
            result["calibration_design"]["length_tertiles"]["replayed_from_manifest"]
        )
        self.assertIn("two_condition", result["comparison_anchored"])
        verification = result["threshold_manifest"]["verification"]
        self.assertTrue(verification["ok"])
        self.assertEqual(sorted(verification["cells"]), ["P", "S"])
        self.assertEqual(result["stage1_attack_traces_skipped"], 21)

    def test_the_seal_is_read_at_stage_one_and_again_at_stage_two(self) -> None:
        pool_dir = self.root / "batch"
        pool_dir.mkdir(parents=True, exist_ok=True)
        seal = {
            "seal_version": "test-seal",
            "traces": {
                "trace_count": 2,
                "trace_json_set_sha256": SEAL.sha256_of_pairs(
                    [("a", "1" * 64), ("b", "2" * 64)]
                ),
                "traces": [
                    {"path": "a", "trace_json_sha256": "1" * 64},
                    {"path": "b", "trace_json_sha256": "2" * 64},
                ],
            },
        }
        (pool_dir / "SEALED.json").write_text(json.dumps(seal), encoding="utf-8")
        args = argparse.Namespace(target=[pool_dir], seal_manifest=None)
        block = RUNNER.seal_check(args, when="stage1_start")
        self.assertTrue(block["any_sealed"])
        self.assertTrue(block["pools"][0]["self_consistent"])
        self.assertEqual(block["when"], "stage1_start")
        # a batch WITHOUT a seal records absence and fails nothing
        empty = argparse.Namespace(target=[self.root / "nope"], seal_manifest=None)
        self.assertFalse(RUNNER.seal_check(empty, when="stage2_start")["any_sealed"])

    def test_the_result_carries_the_fold_fixture_arm_crosstab(self) -> None:
        self._calibrate()
        result = json.loads(
            (self.root / "out" / "cal" / "result.json").read_text(encoding="utf-8")
        )
        crosstab = result["calibration_design"]["fold_fixture_crosstab"]
        self.assertEqual(crosstab["collinear_fixtures"], [])
        self.assertIn("episodes_by_arm_by_fold", crosstab)
        self.assertEqual(result["calibration_design"]["fold_key"], "fixture_rank_mod")
        self.assertIn("rank of s WITHIN ITS FIXTURE", result["calibration_design"]["fold_rule"])


# ---------------------------------------------------------------------------
# the prob_js cell has to survive the two-stage unsealing (freeze review B2)
# ---------------------------------------------------------------------------


class ProbJsStateTest(unittest.TestCase):
    def test_the_routine_mean_round_trips_with_its_fingerprint(self) -> None:
        statistic = trm3_g.ProbJS(window_width=8)
        statistic._layers = (0, 1)
        statistic._experts = 4
        statistic.routine_mean = torch.tensor(
            [[0.4, 0.3, 0.2, 0.1], [0.1, 0.2, 0.3, 0.4]], dtype=torch.float64
        )
        statistic.n_tokens = 123
        state = json.loads(json.dumps(statistic.state_dict()))
        restored = trm3_g.ProbJS(window_width=8).load_state(state)
        self.assertTrue(torch.equal(restored.routine_mean, statistic.routine_mean))
        self.assertEqual(restored.n_tokens, 123)
        self.assertEqual(state["kind"], "prob_js")
        self.assertEqual(
            state["routine_mean_sha256"], restored.routine_mean_sha256()
        )

    def test_a_tampered_reference_distribution_is_refused(self) -> None:
        statistic = trm3_g.ProbJS(window_width=8)
        statistic._layers = (0,)
        statistic._experts = 2
        statistic.routine_mean = torch.tensor([[0.5, 0.5]], dtype=torch.float64)
        state = json.loads(json.dumps(statistic.state_dict()))
        state["routine_mean"] = [[0.9, 0.1]]
        with self.assertRaises(ValueError) as caught:
            trm3_g.ProbJS(window_width=8).load_state(state)
        self.assertIn("fingerprint", str(caught.exception))

    def test_the_scores_agree_after_a_round_trip(self) -> None:
        statistic = trm3_g.ProbJS(window_width=8)
        statistic._layers = (0, 1)
        statistic._experts = 4
        statistic.routine_mean = torch.tensor(
            [[0.4, 0.3, 0.2, 0.1], [0.1, 0.2, 0.3, 0.4]], dtype=torch.float64
        )
        means = torch.tensor(
            [[0.25, 0.25, 0.25, 0.25, 0.1, 0.2, 0.3, 0.4]], dtype=torch.float64
        )
        restored = trm3_g.ProbJS(window_width=8).load_state(
            json.loads(json.dumps(statistic.state_dict()))
        )
        self.assertTrue(
            torch.allclose(statistic.window_score(means), restored.window_score(means))
        )


# ---------------------------------------------------------------------------
# E2 / S5 -- D1x in the data-gate script
# ---------------------------------------------------------------------------


class D1xTest(unittest.TestCase):
    def _rows(self):
        return [
            # E and X well inside the horizon -> reachable
            {"trace_id": "t1", "episode_index": 0, "e_analysis": 10, "x": 40,
             "trajectory_class": "execution"},
            # X past the horizon: still reachable (the window is clipped at H_end)
            {"trace_id": "t2", "episode_index": 0, "e_analysis": 10, "x": 900,
             "trajectory_class": "execution"},
            # E past the horizon -> the window is empty
            {"trace_id": "t3", "episode_index": 0, "e_analysis": 800, "x": 900,
             "trajectory_class": "execution"},
            # no X -> bounded resistance, not a D1x positive
            {"trace_id": "t4", "episode_index": 0, "e_analysis": 10,
             "trajectory_class": "bounded"},
        ]

    def _metadata(self, token_count=1000):
        return {
            (f"t{i}", 0): {
                "variant": "attack",
                "collected_arm": "attack",
                "domain_group": "code",
                "channel": "direct_user",
                "generated_token_count": token_count,
            }
            for i in range(1, 5)
        }

    def test_the_window_is_clipped_at_the_horizon(self) -> None:
        block = GATES.x_window_reachable(
            {"e_analysis": 10, "x": 900}, token_count=1000, h_looks=352,
            looks_per_token=0.90,
        )
        self.assertTrue(block["reachable"])
        self.assertTrue(block["x_beyond_h"])
        self.assertEqual(block["upper_bound"], block["horizon_token_bound"])

    def test_an_engagement_past_the_horizon_is_unreachable(self) -> None:
        block = GATES.x_window_reachable(
            {"e_analysis": 800, "x": 900}, token_count=1000, h_looks=352,
            looks_per_token=0.90,
        )
        self.assertFalse(block["reachable"])
        self.assertEqual(block["reason"], "window_empty_inside_horizon")

    def test_e_view_is_the_minimum_of_the_two_channels(self) -> None:
        self.assertEqual(GATES.e_view_v1({"e_analysis": 30, "e_final": 12}), 12)
        self.assertEqual(GATES.e_view_v1({"e_final": 12}), 12)
        self.assertIsNone(GATES.e_view_v1({}))

    def test_the_gate_counts_reachable_x_positives_and_never_blocks(self) -> None:
        payload = GATES.compute_gates(self._rows(), self._metadata(), h_looks=352)
        gate = next(g for g in payload["gates"] if g["gate"].startswith("D1x"))
        self.assertEqual(gate["value"], 2)
        self.assertEqual(gate["threshold"], 62)
        self.assertEqual(gate["status"], "FAIL")
        self.assertFalse(gate["blocking"])
        self.assertNotIn("D1x_reachable_x_positives", payload["failed_blocking_gates"])
        reach = payload["d1x_reachability"]
        self.assertEqual(reach["x_positives"], 3)
        self.assertEqual(reach["reachable"], 2)
        self.assertEqual(reach["unreachable"], 1)
        self.assertEqual(reach["x_beyond_horizon_token"], 2)

    def test_h_is_a_look_budget_converted_to_the_token_axis(self) -> None:
        payload = GATES.compute_gates(
            self._rows(), self._metadata(), h_looks=100, looks_per_token=0.90
        )
        reach = payload["d1x_reachability"]
        self.assertEqual(reach["h_looks"], 100)
        self.assertEqual(reach["horizon_token_bound"], round(100 / 0.90) - 1)
        self.assertEqual(reach["reachable"], 2)
        # a horizon shorter than E_view itself empties every window
        tiny = GATES.compute_gates(
            self._rows(), self._metadata(), h_looks=9, looks_per_token=0.90
        )["d1x_reachability"]
        self.assertEqual(tiny["horizon_token_bound"], 9)
        self.assertEqual(tiny["reachable"], 0)


# ---------------------------------------------------------------------------
# E14 / DATA-3 item 5 -- the per-arm hashes
# ---------------------------------------------------------------------------


class ArmHashesTest(unittest.TestCase):
    def test_the_normal_union_digest_is_the_one_the_manifest_records(self) -> None:
        pool = ROOT / "artifacts" / "agent_v2" / "dataset_g" / "g_fit"
        if not pool.is_dir():  # pragma: no cover - the pool is not in every checkout
            self.skipTest("g_fit is not present")
        payload = SEAL.build_arm_hashes(pool, subset="g_fit")
        manifest = RUNNER.normal_trace_manifest([pool])
        self.assertEqual(
            payload["normal_union_sha256"], manifest["per_dir"][0]["sha256"]
        )
        self.assertEqual(
            payload["normal_union_trace_count"], manifest["trace_count"]
        )
        self.assertTrue(payload["writes_nothing_under_root"])
        self.assertEqual(sorted(payload["per_arm"]), sorted(io_g.KNOWN_VARIANTS))

    def test_a_read_only_root_diverts_the_file_out_of_the_subset(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name) / "sealed"
            root.mkdir()
            mode = root.stat().st_mode
            root.chmod(mode & ~stat.S_IWUSR & ~stat.S_IWGRP & ~stat.S_IWOTH)
            try:
                destination, placement = SEAL.arm_hashes_destination(root, "g_conf", None)
                self.assertNotIn(str(root), str(destination))
                self.assertIn("g_conf_meta", str(destination))
                self.assertIn("READ-ONLY", placement)
            finally:
                root.chmod(mode)

    def test_a_writable_root_keeps_the_file_next_to_the_seal(self) -> None:
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            destination, placement = SEAL.arm_hashes_destination(root, "g_dev", None)
            self.assertEqual(destination, root / SEAL.ARM_HASHES_FILENAME)
            self.assertIn("next to SEALED.json", placement)

    def test_the_harness_cross_checks_an_arm_hash_file_when_it_is_there(self) -> None:
        pool = ROOT / "artifacts" / "agent_v2" / "dataset_g" / "g_fit"
        if not pool.is_dir():  # pragma: no cover
            self.skipTest("g_fit is not present")
            return
        block = RUNNER.arm_hash_check(pool)
        # the file is optional and its absence is recorded, never an error
        self.assertIn("present", block)
        if not block["present"]:
            self.assertTrue(block["looked_in"])


# ---------------------------------------------------------------------------
# round 2b -- the code gaps the freeze review registered in prereg section 13.1,
# as ruled on by the lead before freeze A': items 1 / 2 / 4 / 5 / 9, plus the
# --prereg-path switch (lead ruling item 6)
# ---------------------------------------------------------------------------


class SealedBatchSmokeRefusalTest(unittest.TestCase):
    """Section 13.1 item 1 / freeze review D-5 (BLOCKING).

    ``--normal-only-smoke`` used to walk straight past ``refuse_sealed_pools`` (which only
    looked at ``--dev-smoke``) AND past the freeze guard, so stage 1 could be run on the
    sealed confirmation batch as often as one liked, printing the per-fold FAR, the
    ``alpha_eff`` and the tertile cutpoints each time.
    """

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.batch = self.root / "batch_like_g_conf"
        self.batch.mkdir(parents=True)
        (self.batch / "SEALED.json").write_text(
            json.dumps({"seal_version": "test-seal"}), encoding="utf-8"
        )
        self._saved_loader = RUNNER.load_pool

        def never(*args, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("a sealed batch must be refused before it is loaded")

        RUNNER.load_pool = never

    def tearDown(self) -> None:
        RUNNER.load_pool = self._saved_loader
        self.tmp.cleanup()

    def test_a_normal_only_smoke_is_refused_on_a_sealed_batch(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            RUNNER.main(
                [
                    "--target", str(self.batch), "--cal-from-target",
                    "--stage", "calibrate", "--normal-only-smoke",
                ]
            )
        message = str(caught.exception)
        self.assertIn("--normal-only-smoke", message)
        self.assertIn("SEALED", message)
        self.assertIn(str(self.batch), message)

    def test_a_dev_smoke_is_still_refused(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            RUNNER.main(
                [
                    "--target", str(self.batch), "--cal-from-target",
                    "--stage", "score", "--dev-smoke",
                    "--threshold-manifest", str(self.root / "m.json"),
                ]
            )
        self.assertIn("--dev-smoke", str(caught.exception))

    def test_a_sealed_ancestor_is_enough(self) -> None:
        inner = self.batch / "runs" / "001"
        inner.mkdir(parents=True)
        args = argparse.Namespace(
            dev_smoke=False, normal_only_smoke=True, fit=None, cal=None, target=[inner]
        )
        with self.assertRaises(SystemExit):
            RUNNER.refuse_sealed_pools(args)
        self.assertEqual(RUNNER.sealed_pool_dirs([inner]), [str(inner.resolve())])

    def test_an_enforced_non_smoke_run_is_not_refused_by_this_guard(self) -> None:
        # the confirmatory run IS the one allowed to open the sealed batch -- under the
        # freeze guard, which is what enforces everything else
        args = argparse.Namespace(
            dev_smoke=False, normal_only_smoke=False, fit=None, cal=None,
            target=[self.batch],
        )
        self.assertIsNone(RUNNER.refuse_sealed_pools(args))

    def test_an_unsealed_batch_is_never_refused(self) -> None:
        plain = self.root / "g_dev_like"
        plain.mkdir()
        for smoke in ("dev_smoke", "normal_only_smoke"):
            args = argparse.Namespace(
                dev_smoke=False, normal_only_smoke=False, fit=None, cal=None,
                target=[plain],
            )
            setattr(args, smoke, True)
            self.assertIsNone(RUNNER.refuse_sealed_pools(args))


class ManifestGuardTest(_TwoStageHarness, unittest.TestCase):
    """Section 13.1 items 2 / 4 / 5: three comparisons stage 2 recorded but never made."""

    def _score(self, manifest: Path, run_name: str, *extra: str) -> int:
        return RUNNER.main(
            self._base(
                "--stage", "score", "--dev-smoke", "--run-name", run_name,
                "--threshold-manifest", str(manifest), "--outputs", "primary", *extra,
            )
        )

    def _tomorrow(self) -> str:
        now = RUNNER._parse_stamp(time.strftime(RUNNER.STAMP_FORMAT))
        return (now + timedelta(days=1)).strftime(RUNNER.STAMP_FORMAT)

    def test_an_attack_trace_added_between_the_stages_is_refused(self) -> None:
        path = self._calibrate()
        # one more attack ARM directory appears between the two stages: the seal only walks
        # its own recorded rows, normal_trace_set_sha256 covers the normal arms only, and
        # the loader's rglob would score it (freeze review D-7)
        RUNNER.attack_trace_census = lambda dirs: self._attack_census("b" * 64, count=22)
        with self.assertRaises(SystemExit) as caught:
            self._score(path, "attack")
        self.assertIn("attack_trace_set_sha256", str(caught.exception))

    def test_a_preregistration_changed_between_the_stages_is_refused(self) -> None:
        path = self._calibrate()
        forged = self._retouch(
            path, lambda p: p["prereg"].__setitem__("sha256", "f" * 64), "prereg"
        )
        with self.assertRaises(SystemExit) as caught:
            self._score(forged, "prereg")
        self.assertIn("manifest_prereg_sha256", str(caught.exception))

    def test_a_manifest_from_another_freeze_commit_is_refused(self) -> None:
        path = self._calibrate()
        forged = self._retouch(
            path, lambda p: p.__setitem__("freeze_commit_resolved", "0" * 40), "freeze"
        )
        with self.assertRaises(SystemExit) as caught:
            self._score(forged, "freeze")
        self.assertIn("manifest_freeze_commit", str(caught.exception))

    def test_a_manifest_written_after_stage_two_started_is_refused(self) -> None:
        path = self._calibrate()
        later = self._tomorrow()
        forged = self._retouch(path, lambda p: p.__setitem__("created_at", later), "created")
        with self.assertRaises(SystemExit) as caught:
            self._score(forged, "created")
        self.assertIn("created_at_ordering", str(caught.exception))

    def test_a_stage_one_seal_check_later_than_the_manifest_is_refused(self) -> None:
        path = self._calibrate()
        later = self._tomorrow()
        forged = self._retouch(
            path,
            lambda p: p["inputs"]["seal"].__setitem__("checked_at", later),
            "seal_stamp",
        )
        with self.assertRaises(SystemExit) as caught:
            self._score(forged, "seal_stamp")
        self.assertIn("created_at_ordering", str(caught.exception))

    def test_the_verification_records_the_new_checks_and_the_four_moments(self) -> None:
        path = self._calibrate()
        self.assertEqual(self._score(path, "ok"), 0)
        result = json.loads(
            (self.root / "out" / "ok" / "result.json").read_text(encoding="utf-8")
        )
        verification = result["threshold_manifest"]["verification"]
        checks = {row["check"]: row for row in verification["checks"]}
        self.assertEqual(len(verification["checks"]), 19)
        for name in (
            "attack_trace_set_sha256",
            "manifest_prereg_sha256",
            "manifest_freeze_commit",
            "created_at_ordering",
        ):
            self.assertIn(name, checks)
            self.assertTrue(checks[name]["ok"], name)
        self.assertTrue(verification["ok"])
        order = [
            "stage1_seal_check", "manifest_created_at", "stage2_seal_check", "stage2_start",
        ]
        observed = checks["created_at_ordering"]["observed"]
        self.assertEqual(sorted(observed), sorted(order))
        stamps = [RUNNER._parse_stamp(observed[key]) for key in order]
        self.assertTrue(all(stamps))
        self.assertEqual(stamps, sorted(stamps))
        # the attack digest compared is the one stage 1 recorded
        self.assertEqual(checks["attack_trace_set_sha256"]["expected"], "a" * 64)


class RunOnceTest(_TwoStageHarness, unittest.TestCase):
    """Section 13.1 item 9 / freeze review D-16: "runs once" leaves a mark."""

    def _calibrate_argv(self, *extra: str) -> list[str]:
        return self._base(
            "--stage", "calibrate", "--normal-only-smoke",
            "--run-name", "cal", "--outputs", "primary", *extra,
        )

    def test_stage_one_refuses_to_overwrite_its_own_manifest(self) -> None:
        manifest = self._calibrate()
        with self.assertRaises(SystemExit) as caught:
            RUNNER.main(self._calibrate_argv())
        message = str(caught.exception)
        self.assertIn("refuses to overwrite", message)
        self.assertIn(str(manifest), message)
        # --allow-overwrite is the only way through, and it never appears in a registered
        # confirmatory command
        self.assertEqual(RUNNER.main(self._calibrate_argv("--allow-overwrite")), 0)

    def test_stage_two_refuses_to_overwrite_an_existing_result(self) -> None:
        manifest = self._calibrate()
        argv = self._base(
            "--stage", "score", "--dev-smoke", "--run-name", "s2",
            "--threshold-manifest", str(manifest), "--outputs", "primary",
        )
        self.assertEqual(RUNNER.main(argv), 0)
        with self.assertRaises(SystemExit) as caught:
            RUNNER.main(argv)
        message = str(caught.exception)
        self.assertIn("refuses to overwrite", message)
        self.assertIn(str(self.root / "out" / "s2" / "result.json"), message)
        self.assertEqual(RUNNER.main(argv + ["--allow-overwrite"]), 0)

    def test_the_refusal_comes_before_any_scoring(self) -> None:
        manifest = self._calibrate()
        argv = self._base(
            "--stage", "score", "--dev-smoke", "--run-name", "s3",
            "--threshold-manifest", str(manifest), "--outputs", "primary",
        )
        self.assertEqual(RUNNER.main(argv), 0)
        saved = RUNNER.run_cell_v32

        def never(*args, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("the run-once guard has to refuse before any scoring")

        RUNNER.run_cell_v32 = never
        try:
            with self.assertRaises(SystemExit):
                RUNNER.main(argv)
        finally:
            RUNNER.run_cell_v32 = saved

    def test_outputs_none_writes_nothing_so_nothing_is_refused(self) -> None:
        argv = self._base(
            "--stage", "calibrate", "--normal-only-smoke", "--run-name", "quiet",
            "--outputs", "none",
        )
        self.assertEqual(RUNNER.main(argv), 0)
        self.assertEqual(RUNNER.main(argv), 0)
        self.assertFalse((self.root / "out" / "quiet").exists())

    def test_the_guard_is_recorded_in_the_result(self) -> None:
        self._calibrate()
        result = json.loads(
            (self.root / "out" / "cal" / "result.json").read_text(encoding="utf-8")
        )
        block = result["run_once_guard"]
        self.assertEqual(block["stage"], "calibrate")
        self.assertTrue(block["enforced"])
        self.assertFalse(block["allow_overwrite"])
        self.assertEqual(block["existing"], [])
        self.assertIn(
            str(self.root / "out" / "cal" / "threshold_manifest.json"),
            block["paths_checked"],
        )


class PreregPathFlagTest(_TwoStageHarness, unittest.TestCase):
    """Lead ruling item 6: ``--prereg-path``, with the default left where it was."""

    def test_the_default_is_the_frozen_v3_1_file(self) -> None:
        self.assertEqual(RUNNER.prereg_path(argparse.Namespace()), RUNNER.PREREG_PATH)
        self.assertEqual(
            RUNNER.prereg_path(argparse.Namespace(prereg_path=None)), RUNNER.PREREG_PATH
        )
        self.assertEqual(RUNNER.PREREG_PATH.name, "detector_prereg_v3_1.md")

    def test_a_relative_path_is_resolved_against_the_repository_root(self) -> None:
        relative = Path("docs/research_v4/detector_prereg_v3_2_draft.md")
        self.assertEqual(
            RUNNER.prereg_path(argparse.Namespace(prereg_path=relative)), ROOT / relative
        )
        absolute = ROOT / relative
        self.assertEqual(
            RUNNER.prereg_path(argparse.Namespace(prereg_path=absolute)), absolute
        )

    def test_the_flag_moves_what_is_hashed_and_what_is_recorded(self) -> None:
        prereg = self.root / "detector_prereg_v3_2.md"
        prereg.write_text("# a v3.2 preregistration\n", encoding="utf-8")
        digest = hashlib.sha256(prereg.read_bytes()).hexdigest()
        manifest = self._calibrate("--prereg-path", str(prereg))
        result = json.loads(
            (self.root / "out" / "cal" / "result.json").read_text(encoding="utf-8")
        )
        self.assertEqual(result["prereg"]["path"], str(prereg))
        self.assertEqual(result["prereg"]["sha256"], digest)
        self.assertEqual(result["data_discipline_guard"]["prereg_path"], str(prereg))
        self.assertEqual(result["data_discipline_guard"]["prereg_sha256"], digest)
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(payload["prereg"], {"path": str(prereg), "sha256": digest})

    def test_without_the_flag_the_result_still_names_the_v3_1_file(self) -> None:
        self._calibrate()
        result = json.loads(
            (self.root / "out" / "cal" / "result.json").read_text(encoding="utf-8")
        )
        self.assertEqual(result["prereg"]["path"], str(RUNNER.PREREG_PATH))
        self.assertEqual(
            result["prereg"]["sha256"], RUNNER.sha256_file(RUNNER.PREREG_PATH)
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
