"""Harmony channel segmentation and function-call parsing for Agent v3.

gpt-oss emits every assistant turn as a sequence of harmony channel messages:

``<|channel|>analysis<|message|>...<|end|>``
``<|start|>assistant<|channel|>commentary to=functions.NAME <|constrain|>json<|message|>{...}<|call|>``
``<|start|>assistant<|channel|>final<|message|>...<|return|>``

The recipient (``to=functions.NAME``) may appear either inside the channel
header, as the model writes it, or right after ``<|start|>assistant``, as the
shipped chat template writes it. Both forms are accepted here.

Segmentation runs on token IDs rather than on decoded text so that the token
indices recorded in a trace are exact: every span this module reports is a
half-open interval over the generated-token axis of one agent step.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


TOOL_CALL_ROLE = "tool_call"
FUNCTION_PREFIX = "functions."

#: Anchor the ``tool_call`` role is spliced in front of inside the stock
#: harmony template. The patched template renders a ``tool_call`` message
#: byte-for-byte like the stock template renders an assistant message that
#: carries ``tool_calls`` (asserted in tests/test_agent_v3_harmony.py).
_TOOL_BRANCH_ANCHOR = "    {%- elif message.role == 'tool' -%}"
_TOOL_CALL_BRANCH = """    {%- elif message.role == 'tool_call' -%}
        {{- "<|start|>assistant to=" }}
        {{- "functions." + message.content['name'] + "<|channel|>commentary " }}
        {{- (message.content['content_type'] if message.content['content_type'] is defined else "json") + "<|message|>" }}
        {{- message.content['arguments']|tojson }}
        {{- "<|call|>" }}
        {%- set last_tool_call.name = message.content['name'] %}
"""


def build_tool_call_chat_template(chat_template: str) -> str:
    """Add a ``tool_call`` role branch to the shipped harmony template.

    ``phase_a.generate_routed_turn`` and ``routing.annotate_chat_prompt`` render
    messages as ``{"role": ..., "content": ...}`` pairs, so an assistant tool
    call cannot be expressed through the stock template's ``tool_calls`` field.
    The added branch reads the same information out of a mapping ``content``.
    Nothing else in the template changes.
    """

    if not isinstance(chat_template, str) or not chat_template.strip():
        raise ValueError("chat_template must be a non-empty string")
    if _TOOL_BRANCH_ANCHOR not in chat_template:
        raise ValueError(
            "chat template does not contain the harmony tool-role branch; "
            "the tool_call patch cannot be applied safely"
        )
    if chat_template.count(_TOOL_BRANCH_ANCHOR) != 1:
        raise ValueError("harmony tool-role branch is not unique in the chat template")
    return chat_template.replace(
        _TOOL_BRANCH_ANCHOR, _TOOL_CALL_BRANCH + _TOOL_BRANCH_ANCHOR, 1
    )


def tool_call_message(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Build the assistant tool-call message the patched template renders."""

    return {
        "role": TOOL_CALL_ROLE,
        "content": {"name": str(name), "arguments": dict(arguments)},
    }


@dataclass(frozen=True)
class HarmonySpecials:
    start: int
    message: int
    channel: int
    constrain: int
    end: int
    call: int
    return_: int

    @classmethod
    def from_tokenizer(cls, tokenizer: Any) -> "HarmonySpecials":
        def token_id(piece: str) -> int:
            value = tokenizer.convert_tokens_to_ids(piece)
            if value is None or int(value) < 0:
                raise ValueError(f"tokenizer does not define the harmony token {piece}")
            return int(value)

        return cls(
            start=token_id("<|start|>"),
            message=token_id("<|message|>"),
            channel=token_id("<|channel|>"),
            constrain=token_id("<|constrain|>"),
            end=token_id("<|end|>"),
            call=token_id("<|call|>"),
            return_=token_id("<|return|>"),
        )

    @property
    def terminators(self) -> frozenset[int]:
        return frozenset({self.end, self.call, self.return_})

    @property
    def all_ids(self) -> frozenset[int]:
        return frozenset(
            {
                self.start,
                self.message,
                self.channel,
                self.constrain,
                self.end,
                self.call,
                self.return_,
            }
        )


@dataclass(frozen=True)
class ChannelSegment:
    """One harmony channel message inside a single agent step.

    All indices are indices into that step's generated-token list. ``body_end``
    is exclusive and ``terminator_index`` is the index of the ``<|end|>`` /
    ``<|call|>`` / ``<|return|>`` token that closed the body (``-1`` when the
    step hit the token budget mid-message).
    """

    channel: str
    header_start: int
    body_start: int
    body_end: int
    terminator_index: int
    end_kind: str
    text: str
    recipient: str | None = None
    content_type: str | None = None
    header_text: str = ""
    header_repeated: bool = False

    @property
    def token_count(self) -> int:
        return max(0, self.body_end - self.body_start)

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "recipient": self.recipient,
            "content_type": self.content_type,
            "header_start": self.header_start,
            "body_start": self.body_start,
            "body_end": self.body_end,
            "terminator_index": self.terminator_index,
            "end_kind": self.end_kind,
            "token_count": self.token_count,
            "header_repeated": self.header_repeated,
            "text": self.text,
        }


@dataclass(frozen=True)
class ParsedToolCall:
    """A commentary-channel function call, well formed or not."""

    name: str | None
    arguments: dict[str, Any]
    raw_arguments: str
    malformed: bool
    error: str | None
    segment: ChannelSegment


def _decode(tokenizer: Any, token_ids: Sequence[int]) -> str:
    if not token_ids:
        return ""
    return tokenizer.decode(list(token_ids), skip_special_tokens=False, clean_up_tokenization_spaces=False)


def _parse_header(header_text: str) -> tuple[str, str | None]:
    """Split ``analysis`` / ``commentary to=functions.foo`` into name + recipient."""

    recipient: str | None = None
    words = []
    for word in header_text.split():
        if word.startswith("to="):
            recipient = word[3:].strip() or None
        else:
            words.append(word)
    channel = words[0].strip() if words else ""
    return channel, recipient


def segment_channels(
    tokenizer: Any,
    output_token_ids: Sequence[int],
    specials: HarmonySpecials | None = None,
) -> list[ChannelSegment]:
    """Split one step's generated tokens into harmony channel messages."""

    specials = specials or HarmonySpecials.from_tokenizer(tokenizer)
    ids = [int(value) for value in output_token_ids]
    count = len(ids)
    segments: list[ChannelSegment] = []
    pending_recipient: str | None = None
    carried_header_start: int | None = None
    carried_channel: str = ""
    carried_content_type: str | None = None
    header_repeated = False
    index = 0
    while index < count:
        token = ids[index]
        if token == specials.start:
            cursor = index + 1
            while cursor < count and ids[cursor] not in specials.all_ids:
                cursor += 1
            _, recipient = _parse_header(_decode(tokenizer, ids[index + 1 : cursor]))
            pending_recipient = recipient
            index = cursor
            continue
        if token != specials.channel:
            index += 1
            continue

        header_start = index
        cursor = index + 1
        while cursor < count and ids[cursor] not in specials.all_ids:
            cursor += 1
        header_text = _decode(tokenizer, ids[index + 1 : cursor])
        channel, recipient = _parse_header(header_text)
        content_type: str | None = None
        while cursor < count and ids[cursor] == specials.constrain:
            constrain_end = cursor + 1
            while constrain_end < count and ids[constrain_end] not in specials.all_ids:
                constrain_end += 1
            content_type = _decode(tokenizer, ids[cursor + 1 : constrain_end]).strip() or None
            cursor = constrain_end
        if recipient is None:
            recipient = pending_recipient
        pending_recipient = None
        if not channel:
            channel = carried_channel
        if content_type is None:
            content_type = carried_content_type

        if cursor < count and ids[cursor] == specials.channel:
            # The model restated the channel header instead of opening a body.
            # Observed on gpt-oss-20b as
            # "<|channel|>commentary to=functions.X<|channel|>commentary <|constrain|>json<|message|>{...}<|call|>":
            # the recipient sits on the first header and the arguments on the
            # second. Carry the recipient, the channel name and the opening
            # token index forward so the call is one span, and flag it.
            pending_recipient = recipient
            carried_channel = channel
            carried_content_type = content_type
            if carried_header_start is None:
                carried_header_start = header_start
            header_repeated = True
            index = cursor
            continue

        if carried_header_start is not None:
            header_start = carried_header_start

        if cursor >= count or ids[cursor] != specials.message:
            segments.append(
                ChannelSegment(
                    channel=channel,
                    header_start=header_start,
                    body_start=cursor,
                    body_end=cursor,
                    terminator_index=-1,
                    end_kind="truncated_header",
                    text="",
                    recipient=recipient,
                    content_type=content_type,
                    header_text=header_text,
                    header_repeated=header_repeated,
                )
            )
            carried_header_start = None
            carried_channel = ""
            carried_content_type = None
            header_repeated = False
            index = cursor
            continue

        body_start = cursor + 1
        body_end = body_start
        while body_end < count and ids[body_end] not in specials.terminators:
            body_end += 1
        if body_end < count:
            terminator = ids[body_end]
            end_kind = (
                "call"
                if terminator == specials.call
                else "return"
                if terminator == specials.return_
                else "end"
            )
            terminator_index = body_end
        else:
            end_kind = "truncated"
            terminator_index = -1
        segments.append(
            ChannelSegment(
                channel=channel,
                header_start=header_start,
                body_start=body_start,
                body_end=body_end,
                terminator_index=terminator_index,
                end_kind=end_kind,
                text=_decode(tokenizer, ids[body_start:body_end]),
                recipient=recipient,
                content_type=content_type,
                header_text=header_text,
                header_repeated=header_repeated,
            )
        )
        carried_header_start = None
        carried_channel = ""
        carried_content_type = None
        header_repeated = False
        index = body_end + 1 if terminator_index >= 0 else count
    return segments


def is_tool_call_segment(segment: ChannelSegment) -> bool:
    """A commentary message addressed to a function, or closed by ``<|call|>``."""

    if segment.end_kind == "call":
        return True
    return bool(segment.recipient and segment.recipient.startswith(FUNCTION_PREFIX))


def parse_tool_call(segment: ChannelSegment, known_tools: Sequence[str]) -> ParsedToolCall:
    """Turn one call segment into a name and arguments, or flag it malformed."""

    raw = segment.text
    recipient = segment.recipient or ""
    if not recipient:
        return ParsedToolCall(None, {}, raw, True, "missing_recipient", segment)
    if not recipient.startswith(FUNCTION_PREFIX):
        return ParsedToolCall(None, {}, raw, True, "unsupported_namespace", segment)
    name = recipient[len(FUNCTION_PREFIX) :].strip().rstrip(".,")
    if not name:
        return ParsedToolCall(None, {}, raw, True, "empty_tool_name", segment)
    if name not in set(known_tools):
        return ParsedToolCall(name, {}, raw, True, "unknown_tool", segment)
    stripped = raw.strip()
    if not stripped:
        return ParsedToolCall(name, {}, raw, True, "empty_arguments", segment)
    try:
        arguments = json.loads(stripped)
    except json.JSONDecodeError as error:
        return ParsedToolCall(name, {}, raw, True, f"invalid_json: {error.msg}", segment)
    if not isinstance(arguments, dict):
        return ParsedToolCall(name, {}, raw, True, "arguments_not_an_object", segment)
    if any(not isinstance(key, str) for key in arguments):
        return ParsedToolCall(name, {}, raw, True, "argument_keys_not_strings", segment)
    return ParsedToolCall(name, arguments, raw, False, None, segment)


@dataclass(frozen=True)
class StepReading:
    """What one agent step decided: a tool call, a final answer, or neither."""

    segments: list[ChannelSegment] = field(default_factory=list)
    tool_call: ParsedToolCall | None = None
    final_segment: ChannelSegment | None = None

    @property
    def final_text(self) -> str:
        return self.final_segment.text if self.final_segment is not None else ""


def read_step(
    tokenizer: Any,
    output_token_ids: Sequence[int],
    known_tools: Sequence[str],
    specials: HarmonySpecials | None = None,
) -> StepReading:
    """Segment a step and return its first actionable event.

    The first final-channel message ends the episode; the first commentary
    function call is the one the controller executes. Whichever comes first in
    the token stream wins, so a step that reasons, calls a tool and then keeps
    going is still one call per step.
    """

    segments = segment_channels(tokenizer, output_token_ids, specials)
    for segment in segments:
        if segment.channel == "final" and segment.end_kind != "truncated_header":
            return StepReading(segments=segments, final_segment=segment)
        if is_tool_call_segment(segment):
            return StepReading(
                segments=segments, tool_call=parse_tool_call(segment, known_tools)
            )
    return StepReading(segments=segments)


def channel_token_counts(segments: Sequence[ChannelSegment]) -> dict[str, int]:
    """Body tokens per channel across one step (headers excluded)."""

    counts: dict[str, int] = {}
    for segment in segments:
        key = segment.channel or "unlabelled"
        counts[key] = counts.get(key, 0) + segment.token_count
    return counts
