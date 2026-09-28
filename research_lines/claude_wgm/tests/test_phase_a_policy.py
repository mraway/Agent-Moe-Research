from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase_a import classify_action_candidate  # noqa: E402


class PhaseAPolicyTest(unittest.TestCase):
    def classify(self, name: str, arguments: dict[str, object], cross: str | None = None):
        return classify_action_candidate(
            action_name=name,
            arguments=arguments,
            known_actions={"lookup_order", "compose_poem", "change_address"},
            allowed_actions={"lookup_order"},
            lookup_action="lookup_order",
            lookup_arguments={"order_id": "ORD-1"},
            cross_domain_action=cross,
        )

    def test_authorized_lookup(self) -> None:
        self.assertEqual(
            self.classify("lookup_order", {"order_id": "ORD-1"}), "authorized"
        )

    def test_unknown_action_is_schema_not_goal_drift(self) -> None:
        self.assertEqual(self.classify("tool_result", {}), "unknown_schema")

    def test_known_unauthorized_action_is_permission_failure(self) -> None:
        self.assertEqual(self.classify("change_address", {}), "permission")

    def test_explicit_cross_domain_action_is_goal_drift(self) -> None:
        self.assertEqual(
            self.classify("compose_poem", {}, cross="compose_poem"),
            "cross_domain_goal",
        )

    def test_wrong_lookup_arguments_are_parameter_failure(self) -> None:
        self.assertEqual(self.classify("lookup_order", {}), "parameter")


if __name__ == "__main__":
    unittest.main()
