from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_agent_v2_horizon384_replay import compare_replay_trace  # noqa: E402


def _trace(ids: list[int], stop: str) -> dict:
    return {
        "trace_id": "group--attack",
        "base_task_id": "group",
        "condition_id": "group--attack",
        "seed": 7,
        "model_id": "model",
        "model_revision": "rev",
        "agent_id": "agent",
        "agent_definition_version": "v",
        "system_prompt_hash": "system",
        "tool_schema_hash": "tools",
        "task_mandate": {"goal": "support"},
        "perturbation": {"arm": "attack"},
        "pair_group_id": "group",
        "events": [
            {
                "kind": "model_generation",
                "rendered_prompt": "prompt",
                "output_token_ids": ids,
                "stop_reason": stop,
            }
        ],
    }


class Horizon384ReplayAuditTests(unittest.TestCase):
    def test_eos_replay_requires_complete_identity(self) -> None:
        parent = _trace([1, 2, 3], "eos")
        replay = copy.deepcopy(parent)
        row = compare_replay_trace(parent, replay)
        self.assertTrue(row["passed"])
        self.assertEqual(row["extension_token_count"], 0)

        replay["events"][0]["output_token_ids"].append(4)
        self.assertFalse(compare_replay_trace(parent, replay)["passed"])

    def test_length_replay_requires_192_token_prefix(self) -> None:
        parent = _trace(list(range(192)), "length")
        replay = _trace(list(range(240)), "eos")
        row = compare_replay_trace(parent, replay)
        self.assertTrue(row["passed"])
        self.assertEqual(row["extension_token_count"], 48)

        replay["events"][0]["output_token_ids"][100] = 999
        self.assertFalse(compare_replay_trace(parent, replay)["passed"])

    def test_input_change_is_rejected(self) -> None:
        parent = _trace(list(range(192)), "length")
        replay = _trace(list(range(220)), "eos")
        replay["seed"] = 8
        with self.assertRaisesRegex(ValueError, "sampling seed changed"):
            compare_replay_trace(parent, replay)


if __name__ == "__main__":
    unittest.main()
