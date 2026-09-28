"""The pilot batch / attack-probe config builder."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "research_v3"))

from agent_v2 import ARM_NAMES, validate_experiment_config  # noqa: E402
from pilot_build_batch import (  # noqa: E402
    MAX_NEW_TOKENS_PER_TURN,
    MODELS,
    SELECTED_SCENARIOS,
    SOURCE_CONFIG,
    build_config,
    default_output,
)


def _source_scenarios() -> dict[str, dict]:
    source = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
    return {scenario["base_task_id"]: scenario for scenario in source["scenarios"]}


def _injected_text(attack: dict) -> str:
    if attack["channel"] == "tool_output":
        return attack["tool_result_injection"]["content"]
    if attack["channel"] == "direct_user":
        return attack["user_request"]
    return attack["clarification_response"]


class BatchConfigTest(unittest.TestCase):
    def test_batch_copies_four_scenarios_unchanged(self) -> None:
        source = _source_scenarios()
        config = build_config("qwen3_30b_a3b", "batch")
        self.assertEqual(config["experiment_id"], "pilot_qwen3_30b_a3b")
        self.assertEqual(config["dataset_role"], "engineering_pilot_not_data")
        self.assertEqual(config["arms"], list(ARM_NAMES))
        self.assertEqual(config["model_config"], MODELS["qwen3_30b_a3b"]["model_config"])
        self.assertEqual(len(config["scenarios"]), 4)
        for scenario, (task_id, _) in zip(config["scenarios"], SELECTED_SCENARIOS, strict=True):
            self.assertEqual(scenario["base_task_id"], task_id)
            self.assertEqual(scenario, source[task_id])
            self.assertEqual(scenario["sampling_seed"], source[task_id]["sampling_seed"])
            self.assertEqual(set(scenario["arms"]), set(ARM_NAMES))

    def test_selection_covers_every_channel_and_four_domains(self) -> None:
        config = build_config("gpt_oss_20b", "batch")
        channels = {scenario["arms"]["attack"]["channel"] for scenario in config["scenarios"]}
        domains = {
            scenario["arms"]["attack"]["attack_goal"]["target_domain"]
            for scenario in config["scenarios"]
        }
        families = {
            scenario["arms"]["attack"]["attack_family_id"] for scenario in config["scenarios"]
        }
        folds = {scenario["preregistered_fold"] for scenario in config["scenarios"]}
        self.assertEqual(channels, {"tool_output", "direct_user", "multi_turn_user"})
        self.assertEqual(domains, {"poetry", "programming", "mathematics", "cooking"})
        self.assertEqual(len(families), 4)
        self.assertEqual(len(folds), 4)

    def test_horizon_is_384_and_other_decoding_is_inherited(self) -> None:
        source = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
        config = build_config("gpt_oss_20b", "batch")
        self.assertEqual(config["decoding"]["max_new_tokens_per_turn"], MAX_NEW_TOKENS_PER_TURN)
        for key, value in source["decoding"].items():
            if key == "max_new_tokens_per_turn":
                continue
            self.assertEqual(config["decoding"][key], value, key)

    def test_emitted_config_files_match_the_builder(self) -> None:
        for model in sorted(MODELS):
            for mode in ("batch", "probe"):
                path = default_output(model, mode)
                self.assertTrue(path.exists(), path)
                on_disk = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(on_disk, build_config(model, mode), f"{model}/{mode}")


class ProbeConfigTest(unittest.TestCase):
    def test_probe_emits_two_variants_per_scenario(self) -> None:
        config = build_config("qwen3_30b_a3b", "probe")
        self.assertEqual(config["experiment_id"], "pilot_probe_qwen3_30b_a3b")
        self.assertEqual(len(config["scenarios"]), 8)
        variants = [scenario["probe_variant"] for scenario in config["scenarios"]]
        self.assertEqual(sorted(set(variants)), ["authority_system_update", "format_contract"])
        ids = [scenario["base_task_id"] for scenario in config["scenarios"]]
        self.assertEqual(len(set(ids)), 8)
        validate_experiment_config(config)

    def test_only_the_injected_text_changes(self) -> None:
        source = _source_scenarios()
        config = build_config("qwen3_30b_a3b", "probe")
        for scenario in config["scenarios"]:
            base_id = scenario["base_task_id"].rsplit("--", 1)[0]
            base = source[base_id]
            attack = scenario["arms"]["attack"]
            base_attack = base["arms"]["attack"]

            self.assertEqual(scenario["arms"]["clean"], base["arms"]["clean"])
            self.assertEqual(scenario["arms"]["benign_control"], base["arms"]["benign_control"])
            self.assertEqual(scenario["task"], base["task"])
            self.assertEqual(scenario["sampling_seed"], base["sampling_seed"])
            self.assertEqual(scenario["manual_review_markers"], base["manual_review_markers"])
            self.assertEqual(attack["channel"], base_attack["channel"])
            self.assertEqual(attack["attack_goal"], base_attack["attack_goal"])
            self.assertEqual(attack["attack_family_id"], base_attack["attack_family_id"])
            if attack["channel"] == "tool_output":
                self.assertEqual(
                    attack["tool_result_injection"]["location"],
                    base_attack["tool_result_injection"]["location"],
                )
                self.assertEqual(
                    attack["tool_result_injection"]["action"],
                    base_attack["tool_result_injection"]["action"],
                )

            text = _injected_text(attack)
            self.assertNotEqual(text, _injected_text(base_attack))
            self.assertEqual(scenario["probe_original_injection"], _injected_text(base_attack))
            for marker in base_attack["attack_goal"]["required_substrings"]:
                self.assertIn(marker, text)
            for review_marker in base["manual_review_markers"]:
                if not review_marker.startswith("["):
                    self.assertIn(review_marker, text.lower())
            self.assertNotIn("second deliverable", text)
            self.assertNotIn("UNTRUSTED ADDENDUM", text)

    def test_direct_and_multi_turn_variants_keep_the_routine_prefix(self) -> None:
        source = _source_scenarios()
        config = build_config("gpt_oss_20b", "probe")
        for scenario in config["scenarios"]:
            attack = scenario["arms"]["attack"]
            base = source[scenario["base_task_id"].rsplit("--", 1)[0]]
            if attack["channel"] == "direct_user":
                self.assertTrue(attack["user_request"].startswith(base["task"]["user_request"]))
            if attack["channel"] == "multi_turn_user":
                clean = base["arms"]["clean"]["clarification_response"]
                self.assertTrue(attack["clarification_response"].startswith(clean))


if __name__ == "__main__":
    unittest.main()
