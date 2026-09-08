from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_agent_v2_horizon384_config import (  # noqa: E402
    PARENT_CONFIG_SHA256,
    audit_config,
    build_config,
    canonical_hash,
)


class AgentV2Horizon384ConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = build_config()
        cls.parent = json.loads(
            (ROOT / "configs" / "agent_v2_5_b2.json").read_text(
                encoding="utf-8"
            )
        )

    def test_parent_is_the_frozen_b2_matrix(self) -> None:
        self.assertEqual(canonical_hash(self.parent), PARENT_CONFIG_SHA256)
        self.assertEqual(self.config["scenarios"], self.parent["scenarios"])
        self.assertEqual(self.config["agent_config"], self.parent["agent_config"])
        self.assertEqual(self.config["model_config"], self.parent["model_config"])

    def test_only_decoding_ceiling_changes(self) -> None:
        self.assertEqual(
            self.parent["decoding"]["max_new_tokens_per_turn"], 192
        )
        self.assertEqual(
            self.config["decoding"]["max_new_tokens_per_turn"], 384
        )
        for field in ("strategy", "temperature", "top_p", "max_agent_steps"):
            self.assertEqual(
                self.config["decoding"][field], self.parent["decoding"][field]
            )

    def test_audit_freezes_full_replay(self) -> None:
        audit = audit_config(self.config)
        self.assertEqual(audit["scenario_count"], 80)
        self.assertEqual(audit["trace_count"], 240)
        self.assertTrue(audit["scenario_matrix_identical"])
        self.assertTrue(audit["seed_matrix_identical"])
        self.assertFalse(audit["b3_used"])

    def test_engagement_label_does_not_relabel_all_resistance(self) -> None:
        labels = self.config["engagement_labels"]
        self.assertTrue(labels["behavior_only_before_routing_analysis"])
        self.assertTrue(labels["resisted_is_not_automatically_engaged"])
        self.assertIn("no_observable_engagement", labels["classes"])
        self.assertIn("bounded_engagement_resisted", labels["classes"])
        self.assertIn("cross_domain_execution", labels["classes"])


if __name__ == "__main__":
    unittest.main()
