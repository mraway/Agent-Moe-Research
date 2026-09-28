"""Token-level role and execution annotations for chat-template prompts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import torch


@dataclass(frozen=True)
class TokenAnnotations:
    roles: tuple[str, ...]
    conversation_turns: tuple[int, ...]
    agent_steps: tuple[int, ...]
    tool_boundaries: tuple[bool, ...]

    def validate(self, token_count: int) -> None:
        for name, values in (
            ("roles", self.roles),
            ("conversation_turns", self.conversation_turns),
            ("agent_steps", self.agent_steps),
            ("tool_boundaries", self.tool_boundaries),
        ):
            if len(values) != token_count:
                raise ValueError(f"{name} has {len(values)} entries for {token_count} tokens")


def annotate_chat_prompt(
    tokenizer: Any,
    messages: list[dict[str, Any]],
    final_prompt_ids: torch.Tensor,
    *,
    generation_agent_step: int,
) -> TokenAnnotations:
    """Assign each rendered token to the message that introduced it.

    A template may replace the terminal marker of a message once another
    message follows it. The longest common prefix of each incremental render
    and the final prompt therefore defines a conservative, monotonic boundary.
    Wrapper/separator tokens at a boundary belong to the following message.
    """

    final_ids = final_prompt_ids.detach().cpu().reshape(-1).tolist()
    roles: list[str] = []
    turns: list[int] = []
    agent_steps: list[int] = []
    tool_boundaries: list[bool] = []
    previous_length = 0
    current_turn = 0

    template_messages = [
        {"role": message["role"], "content": message["content"]} for message in messages
    ]
    for message_index, message in enumerate(messages):
        prefix = tokenizer.apply_chat_template(
            template_messages[: message_index + 1],
            add_generation_prompt=False,
            return_tensors="pt",
        )
        prefix_ids = prefix if isinstance(prefix, torch.Tensor) else prefix["input_ids"]
        prefix_values = prefix_ids.detach().cpu().reshape(-1).tolist()
        stable_length = 0
        for final_token, prefix_token in zip(final_ids, prefix_values, strict=False):
            if final_token != prefix_token:
                break
            stable_length += 1
        if stable_length <= previous_length:
            raise ValueError(
                f"cannot locate a stable token span for message {message_index}"
            )
        logical_role = str(message.get("logical_role", message["role"]))
        if logical_role == "user":
            current_turn = int(message.get("conversation_turn", current_turn + 1))
        else:
            current_turn = int(message.get("conversation_turn", current_turn))
        message_step = int(message.get("agent_step", generation_agent_step))
        added = stable_length - previous_length
        roles.extend([logical_role] * added)
        turns.extend([current_turn] * added)
        agent_steps.extend([message_step] * added)
        boundaries = [False] * added
        if logical_role == "tool" and added:
            boundaries[0] = True
        tool_boundaries.extend(boundaries)
        previous_length = stable_length

    if previous_length > len(final_ids):
        raise ValueError("message template is longer than the final generation prompt")
    generation_prefix = len(final_ids) - previous_length
    roles.extend(["assistant"] * generation_prefix)
    turns.extend([current_turn] * generation_prefix)
    agent_steps.extend([generation_agent_step] * generation_prefix)
    tool_boundaries.extend([False] * generation_prefix)
    annotations = TokenAnnotations(
        roles=tuple(roles),
        conversation_turns=tuple(turns),
        agent_steps=tuple(agent_steps),
        tool_boundaries=tuple(tool_boundaries),
    )
    annotations.validate(len(final_ids))
    return annotations
