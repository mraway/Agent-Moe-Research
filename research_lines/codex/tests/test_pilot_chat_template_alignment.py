"""Prompt-token annotation under templates whose preamble depends on options.

The harmony template writes ``Reasoning: <effort>`` into the built-in system
message, so every incremental render diverges from a ``reasoning_effort="low"``
prompt at the same preamble token. Without repeating the render options the
longest-common-prefix walk finds no stable span for the second message.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from routing import annotate_chat_prompt  # noqa: E402


class _OptionSensitiveTokenizer:
    """Renders one token per word, with an option-dependent preamble word."""

    def __init__(self) -> None:
        self.vocabulary: dict[str, int] = {}

    def _identifier(self, word: str) -> int:
        return self.vocabulary.setdefault(word, len(self.vocabulary) + 1)

    def apply_chat_template(
        self,
        messages,
        *,
        add_generation_prompt: bool = False,
        return_tensors: str | None = None,
        reasoning_effort: str = "medium",
        **_: object,
    ):
        words = ["<sys>", f"reasoning:{reasoning_effort}", "</sys>"]
        for message in messages:
            words.append(f"<{message['role']}>")
            words.extend(str(message["content"]).split())
        if add_generation_prompt:
            words.append("<assistant>")
        identifiers = [self._identifier(word) for word in words]
        if return_tensors == "pt":
            return torch.tensor([identifiers], dtype=torch.long)
        return identifiers


MESSAGES = [
    {"role": "system", "content": "atlas policy"},
    {"role": "user", "content": "order status please"},
    {"role": "user", "content": "tool result payload"},
]


class ChatTemplateKwargsAlignmentTest(unittest.TestCase):
    def _prompt(self, tokenizer, **kwargs) -> torch.Tensor:
        return tokenizer.apply_chat_template(
            MESSAGES, add_generation_prompt=True, return_tensors="pt", **kwargs
        )

    def test_option_dependent_preamble_breaks_without_the_kwargs(self) -> None:
        tokenizer = _OptionSensitiveTokenizer()
        prompt = self._prompt(tokenizer, reasoning_effort="low")
        with self.assertRaises(ValueError):
            annotate_chat_prompt(tokenizer, MESSAGES, prompt, generation_agent_step=0)

    def test_repeating_the_kwargs_recovers_every_message_span(self) -> None:
        tokenizer = _OptionSensitiveTokenizer()
        prompt = self._prompt(tokenizer, reasoning_effort="low")
        annotations = annotate_chat_prompt(
            tokenizer,
            MESSAGES,
            prompt,
            generation_agent_step=0,
            chat_template_kwargs={"reasoning_effort": "low"},
        )
        self.assertEqual(len(annotations.roles), prompt.shape[1])
        self.assertEqual(annotations.roles[-1], "assistant")
        self.assertIn("system", annotations.roles)
        self.assertIn("user", annotations.roles)
        self.assertEqual(annotations.conversation_turns[-1], 2)

    def test_absent_kwargs_matches_the_historical_render(self) -> None:
        tokenizer = _OptionSensitiveTokenizer()
        prompt = self._prompt(tokenizer)
        without = annotate_chat_prompt(
            tokenizer, MESSAGES, prompt, generation_agent_step=0
        )
        explicit_none = annotate_chat_prompt(
            tokenizer,
            MESSAGES,
            prompt,
            generation_agent_step=0,
            chat_template_kwargs=None,
        )
        empty = annotate_chat_prompt(
            tokenizer, MESSAGES, prompt, generation_agent_step=0, chat_template_kwargs={}
        )
        self.assertEqual(without, explicit_none)
        self.assertEqual(without, empty)


if __name__ == "__main__":
    unittest.main()
