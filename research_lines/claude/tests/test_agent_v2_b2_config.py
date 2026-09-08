from __future__ import annotations

import sys
import unittest
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_agent_v2_b2_config import audit_config, build_config  # noqa: E402


class AgentV2B2ConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = build_config()

    def test_confirmation_matrix_has_80_new_triplets(self) -> None:
        scenarios = self.config["scenarios"]
        self.assertEqual(len(scenarios), 80)
        self.assertTrue(
            all(
                set(scenario["arms"]) == {"clean", "benign_control", "attack"}
                for scenario in scenarios
            )
        )
        audit = audit_config(self.config)
        self.assertEqual(audit["trace_count"], 240)
        self.assertFalse(audit["b1_record_ids_reused"])
        self.assertFalse(audit["b1_attack_families_reused"])
        self.assertFalse(audit["b1_attack_topics_reused"])
        self.assertFalse(audit["b1_sampling_seeds_reused"])

    def test_all_scenarios_omit_response_briefs(self) -> None:
        scenarios = self.config["scenarios"]
        self.assertEqual(
            Counter(scenario["response_brief_condition"] for scenario in scenarios),
            {"absent": 80},
        )
        self.assertTrue(
            all("response_brief" not in scenario["task"] for scenario in scenarios)
        )

    def test_families_are_fold_exclusive_and_channels_balanced(self) -> None:
        scenarios = self.config["scenarios"]
        family_folds: defaultdict[str, set[int]] = defaultdict(set)
        for scenario in scenarios:
            family_folds[scenario["analysis_group_id"]].add(
                scenario["preregistered_fold"]
            )
        self.assertEqual(len(family_folds), 16)
        self.assertTrue(all(len(folds) == 1 for folds in family_folds.values()))
        self.assertEqual(
            Counter(
                scenario["arms"]["attack"]["channel"] for scenario in scenarios
            ),
            {"direct_user": 26, "tool_output": 27, "multi_turn_user": 27},
        )

    def test_primary_model_and_no_refit_rule_are_frozen(self) -> None:
        self.assertEqual(
            self.config["frozen_confirmation_model"]["primary"],
            "decode_first16_route_selection",
        )
        self.assertTrue(
            self.config["primary_confirmatory_criteria"]["no_b2_refit_for_primary"]
        )
        self.assertTrue(
            self.config["stopping_rules"]["no_classifier_refit_or_threshold_change_on_b2"]
        )


if __name__ == "__main__":
    unittest.main()
