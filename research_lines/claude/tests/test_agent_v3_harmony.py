"""Harmony channel segmentation, tool-call parsing and the chat-template patch."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_v3.harmony import (  # noqa: E402
    HarmonySpecials,
    build_tool_call_chat_template,
    channel_token_counts,
    is_tool_call_segment,
    parse_tool_call,
    read_step,
    segment_channels,
    tool_call_message,
)


SPECIAL_PIECES = {
    "<|start|>": 200006,
    "<|message|>": 200008,
    "<|channel|>": 200005,
    "<|constrain|>": 200003,
    "<|end|>": 200007,
    "<|call|>": 200012,
    "<|return|>": 200002,
}
KNOWN_TOOLS = (
    "lookup_order",
    "search_support_kb",
    "escalate_to_human",
    "issue_refund",
)


class FakeHarmonyTokenizer:
    """Character-level tokenizer with the real harmony special-token IDs.

    One token per character keeps every index in these tests literal, so a span
    assertion is a statement about the token axis rather than about a
    tokenizer's merge table.
    """

    def __init__(self) -> None:
        self._id_to_piece: dict[int, str] = {value: key for key, value in SPECIAL_PIECES.items()}
        self._piece_to_id: dict[str, int] = dict(SPECIAL_PIECES)
        self._next_id = 1

    def convert_tokens_to_ids(self, piece: str) -> int | None:
        return SPECIAL_PIECES.get(piece)

    def encode_harmony(self, text: str) -> list[int]:
        tokens: list[int] = []
        index = 0
        while index < len(text):
            for piece, identifier in SPECIAL_PIECES.items():
                if text.startswith(piece, index):
                    tokens.append(identifier)
                    index += len(piece)
                    break
            else:
                character = text[index]
                identifier = self._piece_to_id.get(character)
                if identifier is None:
                    self._next_id += 1
                    identifier = self._next_id
                    self._piece_to_id[character] = identifier
                    self._id_to_piece[identifier] = character
                tokens.append(identifier)
                index += 1
        return tokens

    def decode(self, token_ids, skip_special_tokens: bool = False, **_: object) -> str:
        pieces = []
        for token_id in token_ids:
            piece = self._id_to_piece[int(token_id)]
            if skip_special_tokens and piece in SPECIAL_PIECES:
                continue
            pieces.append(piece)
        return "".join(pieces)


class ChannelSegmentationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tokenizer = FakeHarmonyTokenizer()
        self.specials = HarmonySpecials.from_tokenizer(self.tokenizer)

    def _segments(self, text: str):
        return segment_channels(self.tokenizer, self.tokenizer.encode_harmony(text), self.specials)

    def test_analysis_then_final_is_split_into_two_bodies(self) -> None:
        text = (
            "<|channel|>analysis<|message|>need order status<|end|>"
            "<|start|>assistant<|channel|>final<|message|>Your order is in transit.<|return|>"
        )
        segments = self._segments(text)

        self.assertEqual([segment.channel for segment in segments], ["analysis", "final"])
        self.assertEqual(segments[0].text, "need order status")
        self.assertEqual(segments[1].text, "Your order is in transit.")
        self.assertEqual(segments[0].end_kind, "end")
        self.assertEqual(segments[1].end_kind, "return")

    def test_spans_are_exact_token_indices(self) -> None:
        text = "<|channel|>final<|message|>abc<|return|>"
        tokens = self.tokenizer.encode_harmony(text)
        segment = segment_channels(self.tokenizer, tokens, self.specials)[0]

        # tokens: 0 <|channel|>, 1..5 "final", 6 <|message|>, 7..9 "abc", 10 <|return|>
        self.assertEqual(segment.header_start, 0)
        self.assertEqual(segment.body_start, 7)
        self.assertEqual(segment.body_end, 10)
        self.assertEqual(segment.terminator_index, 10)
        self.assertEqual(segment.token_count, 3)
        self.assertEqual(len(tokens), 11)

    def test_recipient_after_the_channel_name_is_parsed(self) -> None:
        text = (
            "<|channel|>commentary to=functions.lookup_order "
            '<|constrain|>json<|message|>{"order_id": "ORD-1102"}<|call|>'
        )
        segment = self._segments(text)[0]

        self.assertEqual(segment.channel, "commentary")
        self.assertEqual(segment.recipient, "functions.lookup_order")
        self.assertEqual(segment.content_type, "json")
        self.assertTrue(is_tool_call_segment(segment))
        call = parse_tool_call(segment, KNOWN_TOOLS)
        self.assertFalse(call.malformed)
        self.assertEqual(call.name, "lookup_order")
        self.assertEqual(call.arguments, {"order_id": "ORD-1102"})

    def test_recipient_before_the_channel_is_parsed(self) -> None:
        """The shipped template writes ``to=`` right after ``<|start|>assistant``."""

        text = (
            "<|channel|>analysis<|message|>plan<|end|>"
            "<|start|>assistant to=functions.search_support_kb<|channel|>commentary json"
            '<|message|>{"query": "delivery"}<|call|>'
        )
        segments = self._segments(text)

        self.assertEqual(segments[1].recipient, "functions.search_support_kb")
        call = parse_tool_call(segments[1], KNOWN_TOOLS)
        self.assertFalse(call.malformed)
        self.assertEqual(call.name, "search_support_kb")

    def test_restated_channel_header_keeps_the_recipient(self) -> None:
        """gpt-oss-20b was observed restating the header mid tool call.

        ``<|channel|>commentary to=functions.X<|channel|>commentary
        <|constrain|>json<|message|>{...}<|call|>`` puts the recipient on the
        first header and the arguments on the second. The two headers are one
        call, and the span starts at the first of them.
        """

        text = (
            "<|channel|>commentary to=functions.search_support_kb"
            '<|channel|>commentary <|constrain|>json<|message|>{"query":"delivery"}<|call|>'
        )
        segments = self._segments(text)

        self.assertEqual(len(segments), 1)
        segment = segments[0]
        self.assertEqual(segment.channel, "commentary")
        self.assertEqual(segment.recipient, "functions.search_support_kb")
        self.assertEqual(segment.content_type, "json")
        self.assertTrue(segment.header_repeated)
        self.assertEqual(segment.header_start, 0)
        self.assertEqual(segment.end_kind, "call")
        call = parse_tool_call(segment, KNOWN_TOOLS)
        self.assertFalse(call.malformed)
        self.assertEqual(call.arguments, {"query": "delivery"})

    def test_a_single_header_is_not_flagged_as_repeated(self) -> None:
        segments = self._segments("<|channel|>final<|message|>done<|return|>")
        self.assertFalse(segments[0].header_repeated)

    def test_truncated_body_is_reported_not_dropped(self) -> None:
        segments = self._segments("<|channel|>final<|message|>half a sentence")

        self.assertEqual(segments[0].end_kind, "truncated")
        self.assertEqual(segments[0].terminator_index, -1)
        self.assertEqual(segments[0].text, "half a sentence")

    def test_truncated_header_is_reported(self) -> None:
        segments = self._segments("<|channel|>comment")

        self.assertEqual(segments[0].end_kind, "truncated_header")
        self.assertEqual(segments[0].token_count, 0)

    def test_channel_token_counts_sum_bodies(self) -> None:
        text = (
            "<|channel|>analysis<|message|>abcd<|end|>"
            "<|start|>assistant<|channel|>final<|message|>xyz<|return|>"
        )
        counts = channel_token_counts(self._segments(text))

        self.assertEqual(counts, {"analysis": 4, "final": 3})


class ToolCallParsingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tokenizer = FakeHarmonyTokenizer()
        self.specials = HarmonySpecials.from_tokenizer(self.tokenizer)

    def _call(self, text: str):
        segments = segment_channels(
            self.tokenizer, self.tokenizer.encode_harmony(text), self.specials
        )
        return parse_tool_call(segments[-1], KNOWN_TOOLS)

    def test_invalid_json_is_malformed(self) -> None:
        call = self._call(
            "<|channel|>commentary to=functions.lookup_order<|message|>{order_id: ORD}<|call|>"
        )
        self.assertTrue(call.malformed)
        self.assertTrue(call.error.startswith("invalid_json"))
        self.assertEqual(call.name, "lookup_order")

    def test_unknown_tool_is_malformed(self) -> None:
        call = self._call(
            '<|channel|>commentary to=functions.delete_everything<|message|>{}<|call|>'
        )
        self.assertTrue(call.malformed)
        self.assertEqual(call.error, "unknown_tool")

    def test_missing_recipient_is_malformed(self) -> None:
        call = self._call('<|channel|>commentary<|message|>{"order_id": "ORD-1"}<|call|>')
        self.assertTrue(call.malformed)
        self.assertEqual(call.error, "missing_recipient")

    def test_non_object_arguments_are_malformed(self) -> None:
        call = self._call('<|channel|>commentary to=functions.lookup_order<|message|>[1,2]<|call|>')
        self.assertTrue(call.malformed)
        self.assertEqual(call.error, "arguments_not_an_object")

    def test_empty_arguments_are_malformed(self) -> None:
        call = self._call("<|channel|>commentary to=functions.lookup_order<|message|>   <|call|>")
        self.assertTrue(call.malformed)
        self.assertEqual(call.error, "empty_arguments")

    def test_non_function_namespace_is_malformed(self) -> None:
        call = self._call('<|channel|>commentary to=browser.search<|message|>{"q": "x"}<|call|>')
        self.assertTrue(call.malformed)
        self.assertEqual(call.error, "unsupported_namespace")

    def test_empty_object_arguments_are_accepted(self) -> None:
        call = self._call("<|channel|>commentary to=functions.lookup_order<|message|>{}<|call|>")
        self.assertFalse(call.malformed)
        self.assertEqual(call.arguments, {})


class ReadStepTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tokenizer = FakeHarmonyTokenizer()
        self.specials = HarmonySpecials.from_tokenizer(self.tokenizer)

    def _read(self, text: str):
        return read_step(
            self.tokenizer, self.tokenizer.encode_harmony(text), KNOWN_TOOLS, self.specials
        )

    def test_first_tool_call_wins_over_a_later_final(self) -> None:
        reading = self._read(
            "<|channel|>analysis<|message|>plan<|end|>"
            '<|start|>assistant<|channel|>commentary to=functions.lookup_order<|message|>{"order_id": "O"}<|call|>'
            "<|start|>assistant<|channel|>final<|message|>ignored<|return|>"
        )
        self.assertIsNone(reading.final_segment)
        self.assertIsNotNone(reading.tool_call)
        self.assertEqual(reading.tool_call.name, "lookup_order")

    def test_final_before_any_call_ends_the_step(self) -> None:
        reading = self._read(
            "<|channel|>analysis<|message|>plan<|end|>"
            "<|start|>assistant<|channel|>final<|message|>done<|return|>"
        )
        self.assertIsNotNone(reading.final_segment)
        self.assertIsNone(reading.tool_call)
        self.assertEqual(reading.final_text, "done")

    def test_analysis_only_step_has_no_action(self) -> None:
        reading = self._read("<|channel|>analysis<|message|>thinking<|end|>")
        self.assertIsNone(reading.final_segment)
        self.assertIsNone(reading.tool_call)


class ChatTemplatePatchTest(unittest.TestCase):
    def test_patch_requires_the_harmony_tool_branch(self) -> None:
        with self.assertRaises(ValueError):
            build_tool_call_chat_template("{{ messages }}")
        with self.assertRaises(ValueError):
            build_tool_call_chat_template("")

    def test_tool_call_message_shape(self) -> None:
        message = tool_call_message("lookup_order", {"order_id": "ORD-1"})
        self.assertEqual(message["role"], "tool_call")
        self.assertEqual(message["content"], {"name": "lookup_order", "arguments": {"order_id": "ORD-1"}})


SNAPSHOT = (
    ROOT
    / "artifacts/hf_cache/models--openai--gpt-oss-20b/snapshots"
    / "6cee5e81ee83917806bbde320786a8fb61efebee"
)


@unittest.skipUnless(SNAPSHOT.exists(), "gpt-oss-20b tokenizer snapshot is not cached")
class RealHarmonyTemplateTest(unittest.TestCase):
    """The patched template must be the stock template plus one role branch."""

    @classmethod
    def setUpClass(cls) -> None:
        from transformers import AutoTokenizer

        cls.tokenizer = AutoTokenizer.from_pretrained(
            "openai/gpt-oss-20b",
            revision=SNAPSHOT.name,
            cache_dir=ROOT / "artifacts/hf_cache",
            local_files_only=True,
        )
        cls.patched = build_tool_call_chat_template(cls.tokenizer.chat_template)
        cls.tools = [
            {
                "type": "function",
                "function": {
                    "name": "lookup_order",
                    "description": "Look up one order.",
                    "parameters": {
                        "type": "object",
                        "properties": {"order_id": {"type": "string"}},
                        "required": ["order_id"],
                    },
                },
            }
        ]

    def _render(self, messages, **kwargs):
        return self.tokenizer.apply_chat_template(
            messages,
            tools=self.tools,
            add_generation_prompt=True,
            tokenize=False,
            reasoning_effort="low",
            **kwargs,
        )

    def test_tool_call_role_renders_like_the_stock_tool_calls_field(self) -> None:
        base = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "U"}]
        stock = self._render(
            base
            + [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "lookup_order",
                                "arguments": {"order_id": "ORD-1102"},
                            },
                        }
                    ],
                },
                {"role": "tool", "content": {"ok": True}},
            ]
        )
        patched = self._render(
            base
            + [
                tool_call_message("lookup_order", {"order_id": "ORD-1102"}),
                {"role": "tool", "content": {"ok": True}},
            ],
            chat_template=self.patched,
        )
        self.assertEqual(stock, patched)

    def test_tools_reach_the_developer_message(self) -> None:
        rendered = self._render(
            [{"role": "system", "content": "SYS"}, {"role": "user", "content": "U"}],
            chat_template=self.patched,
        )
        self.assertIn("# Tools", rendered)
        self.assertIn("namespace functions", rendered)
        self.assertIn("type lookup_order = (_: {", rendered)
        self.assertIn("Calls to these tools must go to the commentary channel", rendered)

    def test_tool_results_render_as_a_functions_message(self) -> None:
        rendered = self._render(
            [
                {"role": "system", "content": "SYS"},
                {"role": "user", "content": "U"},
                tool_call_message("lookup_order", {"order_id": "ORD-1102"}),
                {"role": "tool", "content": {"ok": True, "record": {"status": "in transit"}}},
            ],
            chat_template=self.patched,
        )
        self.assertIn(
            '<|start|>assistant to=functions.lookup_order<|channel|>commentary json<|message|>'
            '{"order_id": "ORD-1102"}<|call|>',
            rendered,
        )
        self.assertIn(
            "<|start|>functions.lookup_order to=assistant<|channel|>commentary<|message|>"
            '{"ok": true, "record": {"status": "in transit"}}<|end|>',
            rendered,
        )
        self.assertTrue(rendered.endswith("<|start|>assistant"))

    def test_real_tokens_segment_into_channels(self) -> None:
        specials = HarmonySpecials.from_tokenizer(self.tokenizer)
        raw = (
            "<|channel|>analysis<|message|>Need the order.<|end|>"
            "<|start|>assistant<|channel|>commentary to=functions.lookup_order "
            '<|constrain|>json<|message|>{"order_id":"ORD-1102"}<|call|>'
        )
        token_ids = self.tokenizer.encode(raw, add_special_tokens=False)
        segments = segment_channels(self.tokenizer, token_ids, specials)

        self.assertEqual([segment.channel for segment in segments], ["analysis", "commentary"])
        self.assertEqual(segments[1].recipient, "functions.lookup_order")
        call = parse_tool_call(segments[1], KNOWN_TOOLS)
        self.assertFalse(call.malformed)
        self.assertEqual(call.arguments, {"order_id": "ORD-1102"})
        self.assertEqual(
            self.tokenizer.decode(token_ids[segments[0].body_start : segments[0].body_end]),
            "Need the order.",
        )

    def test_prompt_annotation_survives_tool_calls_and_a_second_turn(self) -> None:
        import torch

        from routing import annotate_chat_prompt

        messages = [
            {"role": "system", "content": "SYS", "logical_role": "system", "conversation_turn": 0, "agent_step": 0},
            {"role": "user", "content": "Where is ORD-1102?", "logical_role": "user", "conversation_turn": 1, "agent_step": 0},
            {**tool_call_message("lookup_order", {"order_id": "ORD-1102"}), "logical_role": "assistant", "conversation_turn": 1, "agent_step": 0},
            {"role": "tool", "content": {"ok": True}, "logical_role": "tool", "conversation_turn": 1, "agent_step": 0},
            {"role": "assistant", "content": "In transit.", "logical_role": "assistant", "conversation_turn": 1, "agent_step": 1},
            {"role": "user", "content": "Thanks.", "logical_role": "user", "conversation_turn": 2, "agent_step": 0},
        ]
        kwargs = {"reasoning_effort": "low", "tools": self.tools, "chat_template": self.patched}
        rendered = self.tokenizer.apply_chat_template(
            [{"role": message["role"], "content": message["content"]} for message in messages],
            add_generation_prompt=True,
            return_tensors="pt",
            **kwargs,
        )
        prompt_ids = rendered if isinstance(rendered, torch.Tensor) else rendered["input_ids"]
        annotations = annotate_chat_prompt(
            self.tokenizer,
            messages,
            prompt_ids,
            generation_agent_step=0,
            chat_template_kwargs=kwargs,
        )

        self.assertEqual(len(annotations.roles), prompt_ids.shape[1])
        self.assertEqual(sum(annotations.tool_boundaries), 1)
        self.assertEqual(set(annotations.conversation_turns), {0, 1, 2})
        self.assertIn("tool", annotations.roles)


if __name__ == "__main__":
    unittest.main()
