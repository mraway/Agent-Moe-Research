from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from evaluate_agent_v2_sample_batch import (  # noqa: E402
    _gate_result,
    _has_attack_review,
    _has_natural_response_after_tool_flow,
)


class AgentV2SampleEvaluationTest(unittest.TestCase):
    def test_gate_result_supports_minimum_and_maximum(self) -> None:
        self.assertTrue(_gate_result(8, {"minimum": 8})["passed"])
        self.assertFalse(_gate_result(3, {"maximum": 2})["passed"])

    def test_attack_review_requires_goal_plan_review(self) -> None:
        trace = {
            "adjudications": [
                {"review_kind": "routine_task_completion"},
                {"review_kind": "free_text_goal_plan_deviation"},
            ]
        }
        self.assertTrue(_has_attack_review(trace))
        self.assertFalse(_has_attack_review({"adjudications": []}))

    def test_natural_response_must_follow_successful_tool_result(self) -> None:
        trace = {
            "outcome": {"routine_tool_flow_completed": True},
            "events": [
                {"event_index": 3, "kind": "tool_result", "result": {"ok": True}},
                {
                    "event_index": 4,
                    "kind": "assistant_message",
                    "actor": "assistant",
                },
            ],
        }
        self.assertTrue(_has_natural_response_after_tool_flow(trace))
        trace["events"][1]["actor"] = "controller"
        self.assertFalse(_has_natural_response_after_tool_flow(trace))


if __name__ == "__main__":
    unittest.main()
