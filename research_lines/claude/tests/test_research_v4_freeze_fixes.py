"""The freeze-review fixes of 2026-09-07 (``docs/research_v4/freeze_review_code_fixes.md``).

One class per item of the lead's work list:

* ARM IDENTITY -- ``io_g`` recovers the five-way variant from the SUBSET CONFIG, the
  runner asserts the G-dev census, the matched-FAR pool excludes ``legitimate_refusal``;
* D5 DENOMINATOR -- the yield gate divides by attack-BEARING episodes;
* SUPPLEMENTARY BATCH -- deleted, replaced by a scope statement (see
  ``tests/test_research_v4_data_gates.py``);
* PREREG_PATH -- the freeze guard hashes the frozen file, not the draft;
* H ASSERTION -- an untabled cell fails outside a smoke run;
* POWER SIMULATOR -- the machinery on a tiny, hand-checkable case;
* MATCHED-FAR DENOMINATOR -- filtered, ``None`` never counted, recorded in ``result.json``;
* F7 -- the session gate threshold is the union bound of the turns actually run;
* ``--outputs all`` writes the per-token JSONL including ``p_inst``.

Everything is synthetic or metadata-only: no routing shard of any sealed batch is opened.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from research_v2 import io_g, trm3, trm3_g  # noqa: E402

from test_research_v4_detectors_g import make_episode, routine_pool  # noqa: E402
from test_research_v4_prereg_v3_1 import RUNNER, fitted, score_pool  # noqa: E402

torch.set_num_threads(4)


def _module(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


POWER = _module("prereg_power_sim_test", "scripts/research_v4/prereg_power_sim.py")
VALIDATE_CLI = _module("packets_validate_test", "scripts/research_v4/packets_validate.py")

G_DEV_CONFIG = ROOT / "configs" / "dataset_g" / "g_dev.json"


# ---------------------------------------------------------------------------
# ARM IDENTITY
# ---------------------------------------------------------------------------


def _write_trace(
    root: Path,
    *,
    scenario: str,
    arm: str,
    episodes: int = 1,
    channel: str = "",
) -> Path:
    directory = root / scenario / arm
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "trace.json").write_text(
        json.dumps(
            {
                "trace_id": f"{scenario}--{arm}",
                "base_task_id": scenario,
                "pair_group_id": scenario,
                "perturbation": {"arm": arm, "channel": channel},
                "episodes": [{"episode_index": i} for i in range(episodes)],
                "router": {"num_moe_layers": 24, "num_experts": 32, "top_k": 4},
            }
        ),
        encoding="utf-8",
    )
    return directory


def _write_config(path: Path, roles: dict[str, str]) -> None:
    path.write_text(
        json.dumps(
            {
                "experiment_id": "dataset_g_tiny",
                "scenarios": [
                    {
                        "base_task_id": scenario,
                        "pair_group_id": scenario,
                        "factory": {"normal_variant": role},
                    }
                    for scenario, role in roles.items()
                ],
            }
        ),
        encoding="utf-8",
    )


class ArmIdentityConfigJoinTest(unittest.TestCase):
    def test_the_real_g_dev_config_names_the_two_hard_normal_groups(self) -> None:
        overrides = io_g.variant_overrides_from_config(G_DEV_CONFIG)
        counts: dict[str, int] = {}
        for role in overrides.values():
            counts[role] = counts.get(role, 0) + 1
        # base_task_id == pair_group_id in G-dev, so 24 + 24 scenarios = 48 keys
        self.assertEqual(counts, {"benign_lexical": 24, "legitimate_refusal": 24})
        self.assertEqual(len(overrides), 48)

    def test_the_normal_pools_have_no_overrides_at_all(self) -> None:
        """G-fit / G-cal / G-session / G-medium / G-conf load byte-identically."""

        for subset in ("g_fit", "g_cal", "g_session", "g_medium", "g_conf"):
            path = ROOT / "configs" / "dataset_g" / f"{subset}.json"
            if not path.exists():  # pragma: no cover - all six are committed
                continue
            self.assertEqual(io_g.variant_overrides_from_config(path), {}, subset)

    def test_the_join_is_by_scenario_id_not_by_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "tiny.json"
            _write_config(
                config,
                {
                    "s-001": "attack_cell",
                    "s-002": "benign_lexical",
                    "s-003": "legitimate_refusal",
                },
            )
            run = root / "run"
            # s-003 sits in a RESUME directory, exactly as G-dev's last 15 refusals do
            _write_trace(run / "core", scenario="s-001", arm="clean")
            _write_trace(run / "core", scenario="s-001", arm="attack")
            _write_trace(run / "lexical", scenario="s-002", arm="clean")
            _write_trace(run / "resume_1_1", scenario="s-003", arm="clean")
            census = io_g.variant_census(run, variant_overrides=config)
            self.assertEqual(
                census["episodes_by_variant"],
                {
                    "attack": 1,
                    "benign_lexical": 1,
                    "clean": 1,
                    "legitimate_refusal": 1,
                },
            )
            self.assertEqual(census["variant_overridden_traces"], 2)

    def test_only_the_clean_arm_is_ever_overridden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "tiny.json"
            _write_config(config, {"s-002": "benign_lexical"})
            run = root / "run"
            _write_trace(run, scenario="s-002", arm="clean")
            _write_trace(run, scenario="s-002", arm="benign_control")
            _write_trace(run, scenario="s-002", arm="attack")
            census = io_g.variant_census(run, variant_overrides=config)
            self.assertEqual(
                census["episodes_by_variant"],
                {"attack": 1, "benign_control": 1, "benign_lexical": 1},
            )

    def test_overrides_can_be_disabled_and_the_old_reading_comes_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "tiny.json"
            _write_config(config, {"s-002": "legitimate_refusal"})
            run = root / "run"
            _write_trace(run, scenario="s-002", arm="clean")
            self.assertEqual(
                io_g.variant_census(run, variant_overrides=None)["episodes_by_variant"],
                {"clean": 1},
            )

    def test_quarantined_traces_are_never_swept_into_a_pool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            _write_trace(run, scenario="s-001", arm="clean")
            _write_trace(run / "quarantine", scenario="s-001", arm="attack")
            _write_trace(run / "_quarantine" / "partial", scenario="s-002", arm="clean")
            self.assertEqual(len(io_g.iter_trace_paths(run)), 1)
            census = io_g.variant_census(run, variant_overrides=None)
            self.assertEqual(census["episodes_by_variant"], {"clean": 1})
            self.assertEqual(census["skipped_quarantine"], 2)

    def test_the_multi_turn_pre_injection_turn_leaves_the_bearing_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            _write_trace(
                run, scenario="s-001", arm="attack", episodes=2, channel="multi_turn_user"
            )
            _write_trace(run, scenario="s-002", arm="attack", channel="direct_user")
            census = io_g.variant_census(run, variant_overrides=None)
            self.assertEqual(census["episodes_by_variant"], {"attack": 3})
            self.assertEqual(census["attack_bearing_episodes"], 2)
            self.assertEqual(census["attack_pre_injection_episodes"], 1)


class LoadGOverrideWiringTest(unittest.TestCase):
    """``load_g`` resolves the same join the census does, and can be turned off."""

    def test_the_resolution_order_of_the_variant_overrides_argument(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "tiny.json"
            _write_config(config, {"s-002": "benign_lexical"})
            run = root / "run"
            run.mkdir()
            explicit_map = {"s-002": "legitimate_refusal"}
            self.assertEqual(
                io_g._resolve_variant_overrides(explicit_map, run),
                (explicit_map, "explicit_mapping", None),
            )
            overrides, source, path = io_g._resolve_variant_overrides(config, run)
            self.assertEqual(overrides, {"s-002": "benign_lexical"})
            self.assertEqual(source, "explicit_config")
            self.assertEqual(path, str(config))
            self.assertEqual(
                io_g._resolve_variant_overrides(None, run), ({}, "disabled", None)
            )
            self.assertEqual(
                io_g._resolve_variant_overrides(io_g.VARIANT_OVERRIDES_AUTO, run),
                ({}, "auto_no_config_found", None),
            )

    def test_a_run_summary_config_path_is_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "tiny.json"
            _write_config(config, {"s-002": "legitimate_refusal"})
            run = root / "run"
            run.mkdir()
            (run / "run_summary.json").write_text(
                json.dumps({"experiment_id": "dataset_g_tiny", "config_path": str(config)}),
                encoding="utf-8",
            )
            self.assertEqual(io_g.subset_config_for_run(run), config)
            overrides, source, _ = io_g._resolve_variant_overrides(
                io_g.VARIANT_OVERRIDES_AUTO, run
            )
            self.assertEqual(source, "auto_subset_config")
            self.assertEqual(overrides, {"s-002": "legitimate_refusal"})

    def test_a_group_directory_without_a_summary_is_still_discovered(self) -> None:
        """G-dev's core_72_cells group has no run_summary.json, only the resolved config."""

        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run" / "group"
            run.mkdir(parents=True)
            (run / "resolved_experiment_config.json").write_text(
                json.dumps({"experiment_id": "dataset_g_dev"}), encoding="utf-8"
            )
            self.assertEqual(io_g.subset_config_for_run(run.parent), G_DEV_CONFIG)

    @unittest.skipUnless(
        (ROOT / "artifacts" / "agent_v2" / "agent_v3_p0" / "batch" / "run_summary.json").exists(),
        "agent_v3 P0 artifacts absent",
    )
    def test_a_pool_with_no_special_variants_loads_identically(self) -> None:
        batch = ROOT / "artifacts" / "agent_v2" / "agent_v3_p0" / "batch"
        auto: dict = {}
        off: dict = {}
        with_auto = io_g.load_g(
            batch, variants=io_g.NORMAL_VARIANTS, manifest=auto, cache_dir=None
        )
        with_off = io_g.load_g(
            batch,
            variants=io_g.NORMAL_VARIANTS,
            manifest=off,
            cache_dir=None,
            variant_overrides=None,
        )
        self.assertEqual(
            [(e.trace_id, e.variant) for e in with_auto],
            [(e.trace_id, e.variant) for e in with_off],
        )
        self.assertEqual(auto["variant_overridden_traces"], 0)
        self.assertEqual(auto["skipped_quarantine"], 0)
        self.assertIn(auto["variant_override_source"], ("auto_subset_config", "auto_no_config_found"))


@unittest.skipUnless(
    (ROOT / "artifacts" / "agent_v2" / "dataset_g" / "g_dev").exists(),
    "G-dev run directory absent",
)
class GDevCensusTest(unittest.TestCase):
    """METADATA ONLY: ``trace.json`` plus the subset config; no routing is opened."""

    def test_the_five_arms_have_the_preregistered_episode_counts(self) -> None:
        census = io_g.variant_census(ROOT / "artifacts" / "agent_v2" / "dataset_g" / "g_dev")
        self.assertEqual(census["episodes_by_variant"], dict(io_g.G_DEV_VARIANT_COUNTS))
        self.assertEqual(census["attack_bearing_episodes"], 264)
        self.assertEqual(census["attack_pre_injection_episodes"], 88)
        self.assertEqual(census["variant_override_source"], "auto_subset_config")
        self.assertEqual(census["skipped_quarantine"], 5)

    def test_the_expected_table_is_the_one_the_runner_asserts(self) -> None:
        self.assertEqual(sum(io_g.G_DEV_VARIANT_COUNTS.values()), 784)
        self.assertEqual(set(io_g.G_DEV_VARIANT_COUNTS), set(io_g.KNOWN_VARIANTS))


class TargetCensusAssertionTest(unittest.TestCase):
    def _args(self, target: Path, *extra: str):
        return RUNNER._args(
            ["--fit", "x", "--cal", "x", "--target", str(target), *extra]
        )

    def test_a_non_g_dev_target_records_the_row_without_enforcing_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pool = routine_pool(3, seed=71)
            rows = RUNNER.target_pool_assertions(self._args(Path(tmp)), pool)
            self.assertEqual(len(rows), 1)
            self.assertFalse(rows[0]["enforced"])
            self.assertTrue(rows[0]["ok"])
            self.assertIn("not enforced", rows[0]["note"])

    @unittest.skipUnless(
        (ROOT / "artifacts" / "agent_v2" / "dataset_g" / "g_dev").exists(),
        "G-dev run directory absent",
    )
    def test_a_g_dev_target_with_a_wrong_census_fails(self) -> None:
        target = ROOT / "artifacts" / "agent_v2" / "dataset_g" / "g_dev"
        pool = routine_pool(3, seed=72)  # three clean episodes, nothing else
        rows = RUNNER.target_pool_assertions(self._args(target), pool)
        self.assertTrue(rows[0]["enforced"])
        self.assertFalse(rows[0]["ok"])
        self.assertEqual(rows[0]["expected"], dict(io_g.G_DEV_VARIANT_COUNTS))
        # a scenario filter or a smoke run turns the assertion back off
        for extra in (("--target-scenarios", "g-dev-001"), ("--normal-only-smoke",)):
            relaxed = RUNNER.target_pool_assertions(self._args(target, *extra), pool)
            self.assertFalse(relaxed[0]["enforced"])
            self.assertTrue(relaxed[0]["ok"])


class UnblindPassthroughTest(unittest.TestCase):
    def test_the_private_mapping_normal_variant_reaches_the_unblinded_row(self) -> None:
        rows = [{"case_id": "a"}, {"case_id": "b"}]
        mapping = [
            {
                "case_id": "a",
                "normal_variant": "legitimate_refusal",
                "scenario_role": "legitimate_refusal",
                "arm_name": "clean",
            },
            {"case_id": "b", "normal_variant": "attack_cell", "arm_name": "attack"},
        ]
        out = VALIDATE_CLI.enrich_with_mapping(rows, mapping)
        self.assertEqual(out[0]["normal_variant"], "legitimate_refusal")
        self.assertEqual(out[0]["scenario_role"], "legitimate_refusal")
        self.assertEqual(out[1]["arm_name"], "attack")
        self.assertNotIn("scenario_role", out[1])

    def test_without_a_mapping_nothing_is_added(self) -> None:
        rows = [{"case_id": "a"}]
        self.assertEqual(VALIDATE_CLI.enrich_with_mapping(rows, None), [{"case_id": "a"}])


# ---------------------------------------------------------------------------
# PREREG_PATH and the H assertion
# ---------------------------------------------------------------------------


class PreregPathTest(unittest.TestCase):
    def test_the_guard_hashes_the_frozen_file_not_the_draft(self) -> None:
        self.assertEqual(RUNNER.PREREG_PATH.name, "detector_prereg_v3_1.md")
        self.assertNotIn("draft", RUNNER.PREREG_PATH.name)
        self.assertEqual(
            RUNNER.PREREG_PATH,
            ROOT / "docs" / "research_v4" / "detector_prereg_v3_1.md",
        )

    @unittest.skipUnless(
        (ROOT / "docs" / "research_v4" / "detector_prereg_v3_1.md").exists(),
        "preregistration absent",
    )
    def test_the_reproduce_command_of_section_19_7_would_now_pass_the_guard(self) -> None:
        digest = RUNNER.sha256_file(RUNNER.PREREG_PATH)
        args = RUNNER._args(
            [
                "--fit", "x", "--cal", "x", "--target", "x",
                "--normal-only-smoke",
                "--prereg-sha256", digest,
            ]
        )
        block = RUNNER.freeze_guard(args, {})
        self.assertTrue(block["prereg_sha256_matches"])
        self.assertTrue(block["prereg_path"].endswith("detector_prereg_v3_1.md"))


class UntabledHCellTest(unittest.TestCase):
    def _rows(self, *extra: str):
        fit = routine_pool(8, seed=81)
        cal = routine_pool(10, seed=82, prefix="c")
        _, config, stat, calibration = fitted(fit, cal, statistic="M")
        args = RUNNER._args(
            ["--fit", "x", "--cal", "x", "--target", "x", *extra]
        )
        rows = RUNNER.frozen_assertions(
            args, key="M", width=6, calibration=calibration, config=config, statistic=stat
        )
        return {row["check"]: row for row in rows}, args

    def test_a_cell_outside_the_frozen_table_fails_a_non_smoke_run(self) -> None:
        self.assertIsNone(trm3_g.frozen_h("message", "V1", 6))
        by_check, _ = self._rows()
        row = by_check["horizon_H"]
        self.assertIsNone(row["expected"])
        self.assertFalse(row["ok"])
        self.assertIn("non-smoke run is refused", row["note"])

    def test_a_smoke_run_still_records_it_and_passes(self) -> None:
        by_check, _ = self._rows("--normal-only-smoke")
        self.assertTrue(by_check["horizon_H"]["ok"])

    def test_an_explicit_expect_h_is_the_documented_escape(self) -> None:
        fit = routine_pool(8, seed=83)
        cal = routine_pool(10, seed=84, prefix="c")
        _, config, stat, calibration = fitted(fit, cal, statistic="M")
        args = RUNNER._args(
            [
                "--fit", "x", "--cal", "x", "--target", "x",
                "--expect-h", str(calibration.horizon["H"]),
            ]
        )
        rows = RUNNER.frozen_assertions(
            args, key="M", width=6, calibration=calibration, config=config, statistic=stat
        )
        self.assertTrue({row["check"]: row for row in rows}["horizon_H"]["ok"])


# ---------------------------------------------------------------------------
# MATCHED-FAR DENOMINATOR
# ---------------------------------------------------------------------------


class MatchedFarDenominatorTest(unittest.TestCase):
    def _pool(self):
        generator = torch.Generator().manual_seed(91)
        episodes = []
        for index in range(4):
            episodes.append(
                make_episode(
                    f"c{index}",
                    generator=generator,
                    variant="clean",
                    labels={"quality": {"behavior": "on_task", "coverage": "full"}},
                )
            )
        for index in range(2):
            episodes.append(
                make_episode(
                    f"b{index}",
                    generator=generator,
                    variant="benign_control",
                    labels={"quality": {"behavior": "on_task", "coverage": "full"}},
                )
            )
        # a clean episode that FAILED the quality filter, and one with no annotation
        episodes.append(
            make_episode(
                "cfail",
                generator=generator,
                variant="clean",
                labels={"quality": {"behavior": "off_task", "coverage": "none"}},
            )
        )
        episodes.append(make_episode("cnone", generator=generator, variant="clean"))
        # the third outcome and the attack arm, neither of which is a FAR denominator
        episodes.append(
            make_episode(
                "r0",
                generator=generator,
                variant="legitimate_refusal",
                labels={"quality": {"behavior": "on_task", "coverage": "full"}},
            )
        )
        episodes.append(make_episode("a0", generator=generator, variant="attack"))
        return episodes

    def test_the_denominator_is_filtered_and_excludes_legitimate_refusal(self) -> None:
        pool = self._pool()
        keys, block = RUNNER.matching_normal_keys(pool)
        self.assertEqual(block["denominator"], RUNNER.MATCHED_FAR_DENOMINATOR)
        self.assertEqual(block["normal_count"], 6)  # 4 clean + 2 benign_control
        self.assertEqual(len(keys), 6)
        self.assertEqual(block["by_variant"], {"clean": 4, "benign_control": 2, "benign_lexical": 0})
        self.assertEqual(block["unlabelled_normal_episodes"], 1)
        self.assertEqual(block["excluded_legitimate_refusal"], 1)
        self.assertNotIn(trm3.trace_key(pool[-2]), keys)  # legitimate_refusal
        self.assertNotIn(trm3.trace_key(pool[-1]), keys)  # attack

    def test_an_unlabelled_pool_falls_back_and_says_so(self) -> None:
        pool = routine_pool(4, seed=92)
        keys, block = RUNNER.matching_normal_keys(pool)
        self.assertEqual(block["denominator"], RUNNER.MATCHED_FAR_DENOMINATOR_FALLBACK)
        self.assertEqual(len(keys), 4)
        self.assertEqual(block["filtered_count"], 0)

    def test_compare_cells_records_the_denominator_in_result_json(self) -> None:
        fit = routine_pool(10, seed=93)
        cal = routine_pool(12, seed=94, prefix="c")
        view, config, stat, calibration = fitted(fit, cal, statistic="S")
        target = self._pool()
        _, outputs, decisions = score_pool(target, stat, calibration, config, view)
        anchors = trm3_g.view_anchors(target, view, e_denominator_arms=None)
        ends = {k: [int(o.end) for o in v if not o.horizon_censored] for k, v in outputs.items()}
        cell = {
            "statistic": "S",
            "_decisions": decisions,
            "_anchors": anchors,
            "_ends": ends,
        }
        comparison = RUNNER.compare_cells(
            cell, dict(cell, statistic="P"), target, alpha=0.10, replicates=50
        )
        block = comparison["normal_denominator"]
        self.assertEqual(block["denominator"], RUNNER.MATCHED_FAR_DENOMINATOR)
        self.assertEqual(block["normal_count"], 6)
        matched = comparison["matched_alpha_secondary"]
        if matched is not None:
            self.assertEqual(matched["normal_count"], 6)
            self.assertEqual(matched["denominator"], RUNNER.MATCHED_FAR_DENOMINATOR)


# ---------------------------------------------------------------------------
# F7
# ---------------------------------------------------------------------------


def _decision(key: str, episode, *, alarm: bool) -> trm3.DecisionStream:
    """One in-horizon look whose fused p either alarms at every alpha or at none."""

    return trm3.DecisionStream(
        key=key,
        ends=[10],
        p_fused=[0.0 if alarm else 1.0],
        arm_class=str(episode.variant),
        behaviour_class="",
        pair_group_id=str(episode.pair_group_id),
        calibration_half=0,
        last_end=10,
    )


class SessionGateF7Test(unittest.TestCase):
    def _sessions(self, turns_per_session: int, sessions: int, *, alarm: bool = False):
        generator = torch.Generator().manual_seed(95)
        episodes = []
        for s in range(sessions):
            for t in range(turns_per_session):
                episodes.append(
                    make_episode(
                        f"s{s}",
                        generator=generator,
                        variant="clean",
                        episode_index=t,
                        session=f"s{s}",
                        scenario=f"scn{s}",
                    )
                )
        by_key = {trm3.trace_key(e): e for e in episodes}
        decisions = {
            key: _decision(key, episode, alarm=alarm) for key, episode in by_key.items()
        }
        return decisions, by_key

    def test_the_threshold_is_the_union_bound_of_the_turns_actually_run(self) -> None:
        decisions, by_key = self._sessions(2, 10)
        block = trm3_g.session_budget(
            decisions, by_key, session_alpha=0.10, session_turns=4
        )
        gate = block["gate_f7"]
        # 2 turns run x alpha_ep 0.025 = 0.05, well under the flat 0.10 the prereg gated on
        self.assertAlmostEqual(gate["threshold"], 0.05)
        self.assertEqual(gate["flat_threshold"], 0.10)
        self.assertEqual(gate["n_turns_run"], {"2": 10})
        self.assertEqual(gate["observed_session_far"], 0.0)
        self.assertTrue(gate["ok"])
        for session in block["per_session"].values():
            self.assertEqual(session["n_turns_run"], 2)
            self.assertAlmostEqual(session["session_bound"], 0.05)

    def test_the_bound_is_capped_at_alpha_session(self) -> None:
        decisions, by_key = self._sessions(6, 4)
        block = trm3_g.session_budget(
            decisions, by_key, session_alpha=0.10, session_turns=4
        )
        # 6 x 0.025 = 0.15 > alpha_session, so the cap bites
        self.assertAlmostEqual(block["gate_f7"]["threshold"], 0.10)
        self.assertAlmostEqual(block["gate_f7"]["max_session_bound"], 0.10)

    def test_a_configured_turn_count_tightens_the_bound(self) -> None:
        decisions, by_key = self._sessions(2, 10)
        block = trm3_g.session_budget(
            decisions,
            by_key,
            session_alpha=0.10,
            session_turns=4,
            turns_by_scenario={f"scn{i}": 5 for i in range(10)},
        )
        # alpha_ep = 0.10 / 5 = 0.02, two turns run -> 0.04
        self.assertAlmostEqual(block["gate_f7"]["threshold"], 0.04)
        self.assertEqual(block["configured_turn_sessions"], 10)

    def test_an_alarming_pool_fails_the_tight_gate_and_passes_the_flat_one(self) -> None:
        decisions, by_key = self._sessions(2, 10, alarm=True)
        gate = trm3_g.session_budget(
            decisions, by_key, session_alpha=0.10, session_turns=4
        )["gate_f7"]
        self.assertEqual(gate["observed_session_far"], 1.0)
        self.assertFalse(gate["ok"])
        self.assertFalse(gate["ok_flat_threshold"])

    def test_a_borderline_pool_separates_the_two_thresholds(self) -> None:
        decisions, by_key = self._sessions(2, 20)
        # make BOTH turns of 2 of the 20 sessions alarm -> session FAR 0.10 exactly
        for session in ("s0", "s1"):
            for key, episode in by_key.items():
                if episode.session_id == session:
                    decisions[key] = _decision(key, episode, alarm=True)
        gate = trm3_g.session_budget(
            decisions, by_key, session_alpha=0.10, session_turns=4
        )["gate_f7"]
        self.assertAlmostEqual(gate["observed_session_far"], 0.10)
        self.assertTrue(gate["ok_flat_threshold"])  # 0.10 <= 0.10, the pass-by-construction
        self.assertFalse(gate["ok"])  # 0.10 > 0.05, the union bound of 2 turns


# ---------------------------------------------------------------------------
# --outputs all
# ---------------------------------------------------------------------------


class OutputsAllTest(unittest.TestCase):
    """Prereg 2.8 / section 14 item 4 need the per-look JSONL, which only ``all`` writes."""

    def _cell(self, outputs: str):
        fit = routine_pool(8, seed=61)
        cal = routine_pool(10, seed=62, prefix="c")
        generator = torch.Generator().manual_seed(63)
        target = [make_episode("t0", generator=generator, variant="clean")]
        view = trm3_g.VIEWS["V1"]
        args = RUNNER._args(
            [
                "--fit", "x", "--cal", "x", "--target", "x",
                "--statistic", "M", "--outputs", outputs,
                "--h-min-survivors", "5", "--min-bucket-traces", "3",
                "--normal-only-smoke",
            ]
        )
        return RUNNER.run_cell(
            "M", args=args, view=view, fit_pool=fit, cal_pool=cal, target_pool=target
        )

    def test_outputs_all_carries_p_inst_and_the_hysteresis_columns(self) -> None:
        cell = self._cell("all")
        rows = cell["_rows"]
        self.assertTrue(rows)
        for column in (
            "p_inst",
            "hysteresis_state",
            "hysteresis_e0",
            "hysteresis_segment",
            "view",
            "statistic",
            "episode_index",
            "session_id",
        ):
            self.assertIn(column, rows[0], column)
        scored = [row for row in rows if row["p_inst"] is not None]
        self.assertTrue(scored)
        self.assertTrue(all(0.0 < row["p_inst"] <= 1.0 for row in scored))
        # every row is JSON-serialisable, which is what main writes to outputs.jsonl
        for row in rows:
            json.loads(json.dumps(row, sort_keys=True))

    def test_outputs_primary_writes_no_per_look_rows(self) -> None:
        self.assertEqual(self._cell("primary")["_rows"], [])

    def test_the_help_text_documents_the_session_target_run(self) -> None:
        text = RUNNER.__doc__ or ""
        self.assertIn("--session-turns-config", text)
        self.assertIn("g_session", text)
        self.assertIn("--outputs all", text)
        self.assertIn("p_inst", text)


# ---------------------------------------------------------------------------
# POWER SIMULATOR
# ---------------------------------------------------------------------------


class PowerSimulatorTest(unittest.TestCase):
    def test_the_family_sizes_come_from_the_config_and_are_unequal(self) -> None:
        sizes = POWER.attack_family_sizes(G_DEV_CONFIG)
        self.assertEqual(len(sizes), 16)
        self.assertEqual(sum(sizes.values()), 264)  # attack-BEARING, not 352
        self.assertEqual(sorted(set(sizes.values())), [14, 19])
        self.assertEqual(sum(1 for v in sizes.values() if v == 19), 8)

    def test_allocation_is_proportional_exact_and_never_empties_a_family(self) -> None:
        weights = [19] * 8 + [14] * 8
        for total in (16, 107, 150, 177, 264):
            counts = POWER.allocate(total, weights)
            self.assertEqual(sum(counts), total)
            self.assertEqual(len(counts), 16)
            self.assertTrue(all(c >= 1 for c in counts))
        self.assertEqual(POWER.allocate(264, weights), weights)
        with self.assertRaises(ValueError):
            POWER.allocate(3, weights)

    def test_mcnemar_matches_the_frozen_exact_test(self) -> None:
        for only_a, only_b in ((0, 0), (6, 0), (5, 0), (10, 2), (3, 3), (12, 4)):
            self.assertAlmostEqual(
                POWER.mcnemar_p(only_a, only_b),
                trm3.paired_mcnemar([True] * only_a + [False] * only_b,
                                    [False] * only_a + [True] * only_b)["p_value"],
            )

    def test_a_tiny_deterministic_case_reproduces_by_hand(self) -> None:
        """Two families, no correlation, Delta = psi: every discordant pair favours S."""

        cell = POWER.simulate_cell(
            n=20,
            delta=0.25,
            rho=0.0,
            family_sizes=[1, 1],
            replicates=2000,
            bootstrap=200,
            psi=0.25,
            seed=7,
        )
        self.assertEqual(cell["family_sizes"], [10, 10])
        self.assertEqual(cell["families"], 2)
        # q = (1 + delta/psi)/2 = 1: P never wins a discordant pair, so Delta_hat >= 0 always
        self.assertGreaterEqual(cell["mean_point_estimate"], 0.0)
        self.assertAlmostEqual(cell["mean_point_estimate"], 0.25, places=1)
        self.assertAlmostEqual(cell["mean_discordant_pairs"], 5.0, places=0)
        self.assertGreater(cell["power"], 0.0)
        self.assertLessEqual(cell["power"], cell["power_ci_only"])
        self.assertLessEqual(cell["power"], cell["power_mcnemar_only"])

    def test_the_null_cell_holds_the_rule_near_its_nominal_level(self) -> None:
        cell = POWER.simulate_cell(
            n=60,
            delta=0.0,
            rho=0.0,
            family_sizes=[19] * 8 + [14] * 8,
            replicates=2000,
            bootstrap=200,
            seed=11,
        )
        self.assertAlmostEqual(cell["mean_point_estimate"], 0.0, places=1)
        self.assertLess(cell["power"], 0.05)
        # the conjunction is never larger than either condition alone
        self.assertLessEqual(cell["power"], cell["power_ci_only"])
        self.assertLessEqual(cell["power"], cell["power_mcnemar_only"])
        self.assertLessEqual(cell["power_mcnemar_one_sided"], cell["power_mcnemar_only"])

    def test_power_rises_with_delta_and_falls_with_rho(self) -> None:
        common = dict(
            n=60,
            family_sizes=[19] * 8 + [14] * 8,
            replicates=2000,
            bootstrap=200,
            seed=13,
        )
        weak = POWER.simulate_cell(delta=0.10, rho=0.15, **common)
        strong = POWER.simulate_cell(delta=0.20, rho=0.15, **common)
        correlated = POWER.simulate_cell(delta=0.20, rho=0.30, **common)
        self.assertGreater(strong["power"], weak["power"])
        self.assertGreater(strong["power"], correlated["power"])

    def test_the_grid_is_deterministic_and_the_cli_refuses_a_thin_run(self) -> None:
        common = dict(
            family_sizes=[19] * 8 + [14] * 8,
            ns=(60,),
            deltas=(0.15,),
            rhos=(0.15,),
            replicates=2000,
            bootstrap=200,
        )
        first = POWER.run_grid(**common)
        second = POWER.run_grid(**common)
        self.assertEqual(first, second)
        with self.assertRaises(SystemExit):
            POWER.main(["--replicates", "100"])

    def test_an_unattainable_delta_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            POWER.simulate_cell(
                n=20, delta=0.50, rho=0.0, family_sizes=[1, 1],
                replicates=2000, bootstrap=100, psi=0.25,
            )

    def test_the_markdown_tables_render(self) -> None:
        rows = POWER.run_grid(
            family_sizes=[19] * 8 + [14] * 8,
            ns=(60,),
            deltas=(0.0, 0.15),
            rhos=(0.15,),
            replicates=2000,
            bootstrap=200,
        )
        table = POWER.markdown_table(rows, [0.15])
        self.assertIn("Delta = 0.15", table)
        self.assertIn("| 60 | 0.15 |", table)
        null = POWER.null_table(rows)
        self.assertIn("conjunction", null)
        self.assertIn("one-sided", null)
        self.assertIn("Delta at power 0.8", POWER.mde_table(rows))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
