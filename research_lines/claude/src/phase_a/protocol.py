"""Assistant-output protocol and deterministic structured-action boundary logic."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ToolAction:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ParsedTurn:
    kind: Literal["action", "message", "invalid"]
    action: ToolAction | None = None
    content: str | None = None
    error: str | None = None
    protocol_warning: str | None = None


def _first_json_object(text: str) -> tuple[dict[str, Any], int, int] | None:
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, consumed = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value, index, index + consumed
    return None


def parse_assistant_output(
    text: str, *, allow_natural_message: bool = False
) -> ParsedTurn:
    """Parse a tool action or message under the selected assistant protocol.

    The legacy protocol requires a JSON object for both actions and messages.  The
    hybrid protocol still requires JSON actions, but treats ordinary non-JSON text
    as a user-facing message.  A malformed leading JSON object remains invalid so
    that broken tool calls cannot silently become dialogue.
    """

    original = text.strip()
    candidate = original
    if candidate.startswith("```"):
        newline = candidate.find("\n")
        if newline >= 0:
            candidate = candidate[newline + 1 :]
        if candidate.rstrip().endswith("```"):
            candidate = candidate.rstrip()[:-3]
    decoded = _first_json_object(candidate)
    if decoded is None:
        if allow_natural_message and original and not (
            original.startswith("{") or original.lower().startswith("```json")
        ):
            return ParsedTurn(kind="message", content=original)
        return ParsedTurn(kind="invalid", error="no JSON object found")
    payload, start, end = decoded
    extra = (candidate[:start] + candidate[end:]).strip()
    warning = "content outside the first JSON object was ignored" if extra else None
    kind = payload.get("type")
    if kind == "action":
        name = payload.get("name")
        arguments = payload.get("arguments")
        if not isinstance(name, str) or not name:
            return ParsedTurn(kind="invalid", error="action name must be a non-empty string")
        if not isinstance(arguments, dict):
            return ParsedTurn(kind="invalid", error="action arguments must be an object")
        return ParsedTurn(
            kind="action",
            action=ToolAction(name=name, arguments=arguments),
            protocol_warning=warning,
        )
    if kind == "message":
        content = payload.get("content")
        if not isinstance(content, str):
            return ParsedTurn(kind="invalid", error="message content must be a string")
        return ParsedTurn(kind="message", content=content, protocol_warning=warning)
    if allow_natural_message and start > 0:
        return ParsedTurn(kind="message", content=original)
    return ParsedTurn(kind="invalid", error="type must be action or message")


def action_decision_token(tokenizer: Any, token_ids: list[int], action_name: str) -> int | None:
    """Return the zero-based output token that first completes an action name."""

    for token_index in range(len(token_ids)):
        prefix = tokenizer.decode(
            token_ids[: token_index + 1],
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )
        if action_name in prefix:
            return token_index
    return None


def text_contains_all(
    content: str,
    required_substrings: list[str] | tuple[str, ...],
) -> bool:
    """Return whether every non-empty required substring occurs case-insensitively."""

    if not required_substrings or any(not value for value in required_substrings):
        raise ValueError("required_substrings must contain only non-empty strings")
    lowered = content.lower()
    return all(value.lower() in lowered for value in required_substrings)
