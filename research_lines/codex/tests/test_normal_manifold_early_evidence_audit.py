from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from run_normal_manifold_early_evidence_audit import (  # noqa: E402
    _interval_values,
    structural_event_signature,
)


class EarlyEvidenceAuditTests(unittest.TestCase):
    def test_token_interval_starts_at_behavior_onset(self) -> None:
        row = {
            "streams": {
                "token_endpoint_z": {
                    "endpoints": list(range(12)),
                    "scores": [float(value) for value in range(12)],
                }
            }
        }
        self.assertEqual(
            _interval_values(
                row, "token_endpoint_z", onset=5, horizon=4
            ),
            [5.0, 6.0, 7.0, 8.0, 9.0],
        )

    def test_block_interval_requires_fully_post_endpoint(self) -> None:
        row = {
            "streams": {
                "nonoverlap_token_mean8_z": {
                    "endpoints": [7, 15, 23, 31],
                    "scores": [1.0, 2.0, 3.0, 4.0],
                }
            }
        }
        self.assertEqual(
            _interval_values(
                row, "nonoverlap_token_mean8_z", onset=10, horizon=8
            ),
            [],
        )
        self.assertEqual(
            _interval_values(
                row, "nonoverlap_token_mean8_z", onset=10, horizon=16
            ),
            [3.0],
        )

    def test_full_horizon_keeps_all_later_endpoints(self) -> None:
        row = {
            "streams": {
                "token_endpoint_z": {
                    "endpoints": [0, 1, 2, 3],
                    "scores": [10.0, 11.0, 12.0, 13.0],
                }
            }
        }
        self.assertEqual(
            _interval_values(
                row, "token_endpoint_z", onset=2, horizon=None
            ),
            [12.0, 13.0],
        )

    def test_structural_signature_ignores_content_and_results(self) -> None:
        def trace(user_content: str, tool_result: dict) -> dict:
            return {
                "trace_id": user_content,
                "events": [
                    {
                        "event_index": 0,
                        "kind": "user_message",
                        "actor": "user",
                        "logical_role": "user",
                        "conversation_turn": 0,
                        "agent_step": None,
                        "content": user_content,
                    },
                    {
                        "event_index": 1,
                        "kind": "tool_result",
                        "actor": "tool",
                        "logical_role": "tool",
                        "conversation_turn": 0,
                        "agent_step": 0,
                        "tool_name": "lookup_order",
                        "task_state_before": "needs_tool",
                        "task_state_after": "ready_to_respond",
                        "result": tool_result,
                    },
                    {
                        "event_index": 2,
                        "kind": "model_generation",
                        "actor": "assistant",
                        "logical_role": "assistant",
                        "conversation_turn": 0,
                        "agent_step": 0,
                        "task_state_before": "ready_to_respond",
                    },
                ],
            }

        self.assertEqual(
            structural_event_signature(trace("clean", {"status": "a"})),
            structural_event_signature(trace("attack", {"status": "b"})),
        )


if __name__ == "__main__":
    unittest.main()
