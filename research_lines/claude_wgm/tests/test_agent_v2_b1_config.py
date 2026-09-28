from __future__ import annotations

import sys
import unittest
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_agent_v2_b1_config import audit_config, build_config  # noqa: E402


class AgentV2B1ConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = build_config()

    def test_frozen_matrix_has_80_matched_triplets(self) -> None:
        scenarios = self.config["scenarios"]
        self.assertEqual(len(scenarios), 80)
        self.assertTrue(
            all(set(scenario["arms"]) == {"clean", "benign_control", "attack"}
                for scenario in scenarios)
        )
        self.assertEqual(audit_config(self.config)["trace_count"], 240)

    def test_attack_families_do_not_cross_preregistered_folds(self) -> None:
        family_folds: defaultdict[str, set[int]] = defaultdict(set)
        for scenario in self.config["scenarios"]:
            attack = scenario["arms"]["attack"]
            family_folds[attack["attack_family_id"]].add(
                scenario["preregistered_fold"]
            )
        self.assertEqual(len(family_folds), 16)
        self.assertTrue(all(len(folds) == 1 for folds in family_folds.values()))

    def test_brief_and_attack_channels_are_balanced(self) -> None:
        scenarios = self.config["scenarios"]
        briefs = Counter(scenario["response_brief_condition"] for scenario in scenarios)
        channels = Counter(
            scenario["arms"]["attack"]["channel"] for scenario in scenarios
        )
        self.assertEqual(briefs, {"present": 40, "absent": 40})
        self.assertEqual(
            channels,
            {"direct_user": 27, "tool_output": 27, "multi_turn_user": 26},
        )


if __name__ == "__main__":
    unittest.main()
