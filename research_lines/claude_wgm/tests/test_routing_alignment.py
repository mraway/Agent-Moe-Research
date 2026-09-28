from __future__ import annotations

import sys
import unittest
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from routing import annotate_chat_prompt  # noqa: E402


class _StableTemplateTokenizer:
    """Tiny tokenizer whose rendered token IDs expose message boundaries."""

    role_ids = {"system": 10, "user": 20, "assistant": 30}

    def apply_chat_template(
        self,
        messages,
        *,
        add_generation_prompt: bool,
        return_tensors: str,
    ) -> torch.Tensor:
        del return_tensors
        values: list[int] = []
        for message in messages:
            values.extend((self.role_ids[message["role"]], len(message["content"])))
        if add_generation_prompt:
            values.append(31)
        return torch.tensor([values], dtype=torch.long)


class RoutingAlignmentTest(unittest.TestCase):
    def test_logical_tool_role_and_boundary_survive_rendered_user_role(self) -> None:
        tokenizer = _StableTemplateTokenizer()
        messages = [
            {
                "role": "system",
                "logical_role": "system",
                "content": "policy",
                "conversation_turn": 0,
                "agent_step": 0,
            },
            {
                "role": "user",
                "logical_role": "user",
                "content": "request",
                "conversation_turn": 1,
                "agent_step": 0,
            },
            {
                "role": "assistant",
                "logical_role": "assistant",
                "content": "tool call",
                "conversation_turn": 1,
                "agent_step": 0,
            },
            {
                "role": "user",
                "logical_role": "tool",
                "content": "tool result",
                "conversation_turn": 1,
                "agent_step": 0,
            },
        ]
        prompt = tokenizer.apply_chat_template(
            [{"role": row["role"], "content": row["content"]} for row in messages],
            add_generation_prompt=True,
            return_tensors="pt",
        )

        annotations = annotate_chat_prompt(
            tokenizer,
            messages,
            prompt,
            generation_agent_step=1,
        )

        self.assertEqual(len(annotations.roles), prompt.numel())
        self.assertEqual(annotations.roles, (
            "system", "system", "user", "user", "assistant", "assistant",
            "tool", "tool", "assistant",
        ))
        self.assertEqual(annotations.agent_steps[-1], 1)
        self.assertTrue(annotations.tool_boundaries[6])
        self.assertEqual(sum(annotations.tool_boundaries), 1)

    def test_rejects_context_dependent_non_prefix_template(self) -> None:
        tokenizer = _StableTemplateTokenizer()
        messages = [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "y"},
        ]
        bad_prompt = torch.tensor([[20, 99, 30, 1, 31]])
        with self.assertRaisesRegex(ValueError, "cannot locate a stable token span"):
            annotate_chat_prompt(tokenizer, messages, bad_prompt, generation_agent_step=0)


if __name__ == "__main__":
    unittest.main()
