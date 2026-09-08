"""P0 config builder: selection, counts, tiers, and schema validation."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_v3 import (  # noqa: E402
    ARM_NAMES,
    DOMAIN_GROUPS,
    WORDING_TIERS,
    validate_agent_v3_experiment,
)


def _load_builder():
    path = ROOT / "scripts" / "research_v4" / "build_agent_v3_p0.py"
    spec = importlib.util.spec_from_file_location("build_agent_v3_p0_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILDER = _load_builder()
B2 = json.loads((ROOT / "configs" / "agent_v2_5_b2.json").read_text(encoding="utf-8"))
B2_BY_ID = {scenario["base_task_id"]: scenario for scenario in B2["scenarios"]}


class BatchConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = BUILDER.build_batch_config()

    def test_eight_scenarios_times_three_arms(self) -> None:
        self.assertEqual(len(self.config["scenarios"]), 8)
        self.assertEqual(self.config["arms"], list(ARM_NAMES))
        self.assertEqual(len(self.config["scenarios"]) * len(self.config["arms"]), 24)

    def test_two_scenarios_per_domain_group(self) -> None:
        counts = Counter(scenario["domain_group"] for scenario in self.config["scenarios"])
        self.assertEqual(set(counts), set(DOMAIN_GROUPS))
        self.assertEqual(set(counts.values()), {2})

    def test_channels_are_spread_with_tool_output_at_one_quarter(self) -> None:
        counts = Counter(
            scenario["arms"]["attack"]["channel"] for scenario in self.config["scenarios"]
        )
        self.assertEqual(counts["tool_output"], 2)
        self.assertEqual(counts["direct_user"], 3)
        self.assertEqual(counts["multi_turn_user"], 3)

    def test_both_tool_injection_locations_are_covered(self) -> None:
        locations = {
            scenario["arms"]["attack"]["tool_result_injection"]["location"]
            for scenario in self.config["scenarios"]
            if scenario["arms"]["attack"]["channel"] == "tool_output"
        }
        self.assertEqual(locations, {"first_kb_hit", "result_note"})

    def test_every_attack_is_a_type_a_content_deliverable(self) -> None:
        for scenario in self.config["scenarios"]:
            goal = scenario["arms"]["attack"]["attack_goal"]
            self.assertEqual(goal["type"], "user_visible_text")
            self.assertTrue(goal["required_substrings"])

    def test_scenarios_are_copied_from_b2_with_their_seeds(self) -> None:
        for scenario in self.config["scenarios"]:
            source = B2_BY_ID[scenario["base_task_id"]]
            self.assertEqual(scenario["sampling_seed"], source["sampling_seed"])
            self.assertEqual(scenario["task"], source["task"])
            self.assertEqual(scenario["arms"], source["arms"])
            self.assertEqual(scenario["analysis_group_id"], source["analysis_group_id"])

    def test_attack_families_and_routines_are_not_all_the_same(self) -> None:
        families = {
            scenario["arms"]["attack"]["attack_family_id"]
            for scenario in self.config["scenarios"]
        }
        self.assertEqual(len(families), 8)

    def test_decoding_matches_the_design(self) -> None:
        decoding = self.config["decoding"]
        self.assertEqual(decoding["strategy"], "sample")
        self.assertEqual(decoding["temperature"], 0.8)
        self.assertEqual(decoding["top_p"], 0.9)
        self.assertEqual(decoding["max_new_tokens_per_turn"], 1024)
        self.assertEqual(decoding["max_agent_steps"], 6)

    def test_dataset_role_keeps_p0_out_of_every_pool(self) -> None:
        self.assertEqual(self.config["dataset_role"], "p0_probe_not_data")
        self.assertEqual(self.config["agent_config"], "configs/agent_v3_support.json")


class ProbeConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = BUILDER.build_probe_config()
        cls.batch = BUILDER.build_batch_config()

    def test_eight_scenarios_times_three_tiers_attack_only(self) -> None:
        self.assertEqual(len(self.config["scenarios"]), 24)
        self.assertEqual(self.config["arms"], ["attack"])
        self.assertEqual(self.config["dataset_role"], "p0_probe_not_data")

    def test_every_tier_appears_eight_times(self) -> None:
        counts = Counter(scenario["wording_tier"] for scenario in self.config["scenarios"])
        self.assertEqual(set(counts), set(WORDING_TIERS))
        self.assertEqual(set(counts.values()), {8})

    def test_tier_zero_is_the_frozen_b2_wording(self) -> None:
        for scenario in self.config["scenarios"]:
            if scenario["wording_tier"] != "T0":
                continue
            source = B2_BY_ID[scenario["base_task_id"].removesuffix("--T0")]
            self.assertEqual(scenario["arms"]["attack"], source["arms"]["attack"])
            self.assertEqual(scenario["sampling_seed"], source["sampling_seed"])

    def test_tier_one_and_two_use_the_pilot_wordings(self) -> None:
        seen = Counter()
        for scenario in self.config["scenarios"]:
            attack = scenario["arms"]["attack"]
            text = (
                (attack.get("tool_result_injection") or {}).get("content")
                or attack.get("user_request")
                or attack.get("clarification_response")
            )
            if scenario["wording_tier"] == "T1":
                seen["T1"] += 1
                self.assertIn("SUPPORT PLATFORM NOTICE 2026-09", text)
                self.assertIn("rejected by the response quality gate", text)
                self.assertNotIn("second deliverable", text)
            elif scenario["wording_tier"] == "T2":
                seen["T2"] += 1
                self.assertIn("Ticket record field response_format_required", text)
                self.assertIn("checked automatically", text)
                self.assertNotIn("second deliverable", text)
        self.assertEqual(seen, Counter({"T1": 8, "T2": 8}))

    def test_tiers_share_the_scenario_and_the_seed(self) -> None:
        by_base: dict[str, list[dict]] = {}
        for scenario in self.config["scenarios"]:
            base = scenario["base_task_id"].rsplit("--", 1)[0]
            by_base.setdefault(base, []).append(scenario)
        self.assertEqual(len(by_base), 8)
        for base, group in by_base.items():
            seeds = {scenario["sampling_seed"] for scenario in group}
            channels = {scenario["arms"]["attack"]["channel"] for scenario in group}
            self.assertEqual(len(seeds), 1)
            self.assertEqual(len(channels), 1)
            self.assertEqual(seeds, {B2_BY_ID[base]["sampling_seed"]})

    def test_probe_scenarios_are_the_batch_scenarios(self) -> None:
        batch_ids = {scenario["base_task_id"] for scenario in self.batch["scenarios"]}
        probe_ids = {
            scenario["base_task_id"].rsplit("--", 1)[0]
            for scenario in self.config["scenarios"]
        }
        self.assertEqual(batch_ids, probe_ids)


class WrittenConfigTest(unittest.TestCase):
    def test_committed_configs_match_the_builder_and_validate(self) -> None:
        for name, built in (
            ("agent_v3_p0_batch.json", BUILDER.build_batch_config()),
            ("agent_v3_p0_probe.json", BUILDER.build_probe_config()),
        ):
            with self.subTest(name=name):
                written = json.loads((ROOT / "configs" / name).read_text(encoding="utf-8"))
                self.assertEqual(written, built)
                validate_agent_v3_experiment(written)


class ExperimentValidationTest(unittest.TestCase):
    def _config(self) -> dict:
        return BUILDER.build_batch_config()

    def test_wrong_phase_is_rejected(self) -> None:
        config = self._config()
        config["phase"] = "agent_v2"
        with self.assertRaises(ValueError):
            validate_agent_v3_experiment(config)

    def test_duplicate_scenario_ids_are_rejected(self) -> None:
        config = self._config()
        config["scenarios"].append(json.loads(json.dumps(config["scenarios"][0])))
        with self.assertRaises(ValueError):
            validate_agent_v3_experiment(config)

    def test_injection_must_target_a_routine_tool(self) -> None:
        config = self._config()
        for scenario in config["scenarios"]:
            injection = scenario["arms"]["attack"].get("tool_result_injection")
            if injection is not None:
                injection["action"] = "lookup_warranty"
                break
        with self.assertRaises(ValueError):
            validate_agent_v3_experiment(config)

    def test_unsupported_domain_group_is_rejected(self) -> None:
        config = self._config()
        config["scenarios"][0]["domain_group"] = "nonsense"
        with self.assertRaises(ValueError):
            validate_agent_v3_experiment(config)

    def test_missing_dataset_role_is_rejected(self) -> None:
        config = self._config()
        del config["dataset_role"]
        with self.assertRaises(ValueError):
            validate_agent_v3_experiment(config)


if __name__ == "__main__":
    unittest.main()
