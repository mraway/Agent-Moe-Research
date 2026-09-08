"""Episode loop: global token axis, tool events, stop reasons, injection placement."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_v2 import apply_tool_result_injection  # noqa: E402
from agent_v3 import episode as episode_module  # noqa: E402
from agent_v3 import (  # noqa: E402
    AgentV3Session,
    AgentV3ToolController,
    HarmonySpecials,
    load_agent_v3_definition,
    user_turns,
)
from phase_a.generation import GenerationResult  # noqa: E402

from test_agent_v3_harmony import FakeHarmonyTokenizer  # noqa: E402


AGENT_CONFIG = ROOT / "configs" / "agent_v3_support.json"
B2_CONFIG = ROOT / "configs" / "agent_v2_5_b2.json"


class _FakeRecorder:
    def __init__(self) -> None:
        self._next = 0

    @property
    def next_step_index(self) -> int:
        return self._next

    def advance(self, count: int) -> None:
        self._next += count


class _ScriptedModel:
    """Replays a list of harmony outputs, one per agent step."""

    def __init__(self, tokenizer: FakeHarmonyTokenizer, outputs: list[str]) -> None:
        self.tokenizer = tokenizer
        self.outputs = list(outputs)
        self.prompts: list[list[dict]] = []

    def __call__(self, **kwargs):
        messages = kwargs["messages"]
        self.prompts.append([dict(message) for message in messages])
        recorder = kwargs["recorder"]
        text = self.outputs[len(self.prompts) - 1]
        token_ids = self.tokenizer.encode_harmony(text)
        recorder.advance(1 + len(token_ids))
        return GenerationResult(
            text=self.tokenizer.decode(token_ids, skip_special_tokens=True),
            output_token_ids=token_ids,
            prompt_token_ids=[1, 2, 3],
            prompt_token_count=3,
            output_token_count=len(token_ids),
            stop_reason="stop_token",
            rendered_prompt="<prompt>",
            channel_boundaries={"analysis": -1, "commentary": -1, "final": -1},
        )


def _call(name: str, arguments: dict) -> str:
    return (
        f"<|channel|>commentary to=functions.{name}<|message|>"
        f"{json.dumps(arguments)}<|call|>"
    )


def _final(text: str) -> str:
    return f"<|channel|>final<|message|>{text}<|return|>"


class SessionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tokenizer = FakeHarmonyTokenizer()
        self.definition = load_agent_v3_definition(AGENT_CONFIG, workspace_root=ROOT)
        self._original_generate = episode_module.generate_routed_turn

    def tearDown(self) -> None:
        episode_module.generate_routed_turn = self._original_generate

    def _session(self, outputs: list[str], *, injection=None, max_agent_steps: int = 6):
        transform = None
        if injection is not None:
            def transform(action, result):
                return apply_tool_result_injection(
                    action_name=action, result=result, injection=injection
                )
        controller = AgentV3ToolController(
            environment=self.definition.environment,
            tool_classes=self.definition.tool_classes,
            tool_result_transform=transform,
        )
        self.model = _ScriptedModel(self.tokenizer, outputs)
        episode_module.generate_routed_turn = self.model
        self.events: list[dict] = []
        return AgentV3Session(
            model=object(),
            tokenizer=self.tokenizer,
            recorder=_FakeRecorder(),
            controller=controller,
            system_prompt=self.definition.system_prompt,
            chat_template_kwargs={"reasoning_effort": "low"},
            max_agent_steps=max_agent_steps,
            max_new_tokens=1024,
            specials=HarmonySpecials.from_tokenizer(self.tokenizer),
            event_sink=self.events.append,
        )


class TokenAxisTest(SessionTestCase):
    def test_steps_concatenate_into_one_episode_token_axis(self) -> None:
        outputs = [
            "<|channel|>analysis<|message|>look it up<|end|>"
            + _call("lookup_order", {"order_id": "ORD-1102"}),
            _call("search_support_kb", {"query": "delivery"}),
            _final("Order ORD-1102 is in transit."),
        ]
        session = self._session(outputs)
        episode = session.run_user_turn("Where is ORD-1102?")

        self.assertEqual(episode.step_count, 3)
        self.assertEqual(episode.stop_reason, "final_channel")
        offsets = [step.global_token_offset for step in episode.steps]
        counts = [step.output_token_count for step in episode.steps]
        self.assertEqual(offsets[0], 0)
        self.assertEqual(offsets[1], counts[0])
        self.assertEqual(offsets[2], counts[0] + counts[1])
        self.assertEqual(episode.generated_token_count, sum(counts))

    def test_routing_shard_index_maps_back_to_the_generated_token(self) -> None:
        outputs = [_call("lookup_order", {"order_id": "ORD-1102"}), _final("done")]
        session = self._session(outputs)
        episode = session.run_user_turn("Where is ORD-1102?")

        first, second = episode.steps
        # The recorder wrote one prefill plus one decode shard per generated token.
        self.assertEqual(first.routing_step_index_prefill, 0)
        self.assertEqual(first.routing_step_index_first_decode, 1)
        self.assertEqual(
            second.routing_step_index_prefill, 1 + first.output_token_count
        )
        event = episode.tool_events[0]
        self.assertEqual(
            event.routing_step_index_first_token,
            first.routing_step_index_first_decode + event.call_first_token_in_step,
        )

    def test_tool_event_spans_point_at_the_call_tokens(self) -> None:
        call_text = _call("lookup_order", {"order_id": "ORD-1102"})
        outputs = ["<|channel|>analysis<|message|>plan<|end|>" + call_text, _final("done")]
        session = self._session(outputs)
        episode = session.run_user_turn("Where is ORD-1102?")

        event = episode.tool_events[0]
        step = episode.steps[0]
        token_ids = self.tokenizer.encode_harmony(outputs[0])
        prefix = self.tokenizer.encode_harmony("<|channel|>analysis<|message|>plan<|end|>")

        self.assertEqual(event.call_first_token_in_step, len(prefix))
        self.assertEqual(event.call_last_token_global, len(token_ids) - 1)
        self.assertEqual(
            self.tokenizer.decode(
                token_ids[event.arguments_start_global : event.arguments_end_global]
            ),
            '{"order_id": "ORD-1102"}',
        )
        self.assertEqual(step.global_token_offset, 0)

    def test_second_user_turn_is_a_second_episode_on_the_session_axis(self) -> None:
        outputs = [_final("first answer"), _final("second answer")]
        session = self._session(outputs)
        first = session.run_user_turn("Turn one")
        second = session.run_user_turn("Turn two")

        self.assertEqual(first.episode_index, 0)
        self.assertEqual(second.episode_index, 1)
        self.assertEqual(second.conversation_turn, 2)
        self.assertEqual(second.steps[0].global_token_offset, 0)
        self.assertEqual(second.session_token_offset, first.generated_token_count)
        self.assertEqual(
            second.steps[0].session_token_offset, first.generated_token_count
        )
        # The second episode's prompt carries the first episode's final answer.
        roles = [message["role"] for message in self.model.prompts[1]]
        self.assertEqual(roles, ["system", "user", "assistant", "user"])


class ToolEventTest(SessionTestCase):
    def test_restricted_call_logs_an_x_tool_event_and_the_loop_continues(self) -> None:
        outputs = [
            _call("issue_refund", {"order_id": "ORD-1102", "amount": 20}),
            _final("I cannot refund; escalating."),
        ]
        session = self._session(outputs)
        episode = session.run_user_turn("Refund my order")

        event = episode.tool_events[0]
        self.assertTrue(event.restricted)
        self.assertTrue(event.x_tool)
        self.assertFalse(event.executed)
        self.assertFalse(event.malformed)
        self.assertEqual(event.result["error"], "unauthorized_tool_not_executed")
        self.assertEqual(episode.stop_reason, "final_channel")
        self.assertEqual(episode.step_count, 2)
        tool_messages = [
            message for message in session.messages if message["role"] == "tool"
        ]
        self.assertEqual(tool_messages[0]["content"]["error"], "unauthorized_tool_not_executed")

    def test_escalation_is_recorded_as_a_legal_call(self) -> None:
        outputs = [_call("escalate_to_human", {"reason": "refund"}), _final("Escalated.")]
        episode = self._session(outputs).run_user_turn("Refund my order")

        event = episode.tool_events[0]
        self.assertEqual(event.tool_class, "escalation")
        self.assertFalse(event.restricted)
        self.assertFalse(event.x_tool)
        self.assertTrue(event.executed)

    def test_malformed_call_stops_the_episode_and_is_logged(self) -> None:
        outputs = [
            "<|channel|>commentary to=functions.lookup_order<|message|>{not json}<|call|>",
            _final("unreachable"),
        ]
        session = self._session(outputs)
        episode = session.run_user_turn("Where is ORD-1102?")

        self.assertEqual(episode.stop_reason, "malformed_tool_call")
        self.assertEqual(episode.step_count, 1)
        event = episode.tool_events[0]
        self.assertTrue(event.malformed)
        self.assertFalse(event.executed)
        self.assertTrue(event.error.startswith("invalid_json"))
        self.assertNotIn("tool", [message["role"] for message in session.messages])

    def test_max_agent_steps_stops_a_tool_loop(self) -> None:
        outputs = [_call("lookup_order", {"order_id": "ORD-1102"})] * 3
        session = self._session(outputs, max_agent_steps=3)
        episode = session.run_user_turn("Where is ORD-1102?")

        self.assertEqual(episode.step_count, 3)
        self.assertEqual(episode.stop_reason, "max_agent_steps")
        self.assertEqual(len(episode.tool_events), 3)

    def test_step_without_a_call_or_a_final_stops_the_episode(self) -> None:
        session = self._session(["<|channel|>analysis<|message|>thinking<|end|>"])
        episode = session.run_user_turn("Where is ORD-1102?")

        self.assertEqual(episode.stop_reason, "no_tool_call_no_final")
        self.assertEqual(episode.tool_events, [])

    def test_tool_call_and_result_are_appended_for_the_next_step(self) -> None:
        outputs = [_call("lookup_order", {"order_id": "ORD-1102"}), _final("done")]
        session = self._session(outputs)
        session.run_user_turn("Where is ORD-1102?")

        roles = [message["role"] for message in session.messages]
        self.assertEqual(roles, ["system", "user", "tool_call", "tool", "assistant"])
        self.assertEqual(
            session.messages[2]["content"],
            {"name": "lookup_order", "arguments": {"order_id": "ORD-1102"}},
        )
        self.assertTrue(session.messages[3]["content"]["ok"])
        self.assertEqual(session.messages[3]["logical_role"], "tool")

    def test_events_cover_the_whole_episode(self) -> None:
        outputs = [_call("lookup_order", {"order_id": "ORD-1102"}), _final("done")]
        session = self._session(outputs)
        session.run_user_turn("Where is ORD-1102?")

        kinds = [event["kind"] for event in self.events]
        self.assertEqual(
            kinds,
            [
                "user_message",
                "model_generation",
                "tool_call",
                "tool_result",
                "model_generation",
                "assistant_message",
            ],
        )


class ChannelCountTest(SessionTestCase):
    def test_channel_token_counts_accumulate_over_the_episode(self) -> None:
        outputs = [
            "<|channel|>analysis<|message|>abcd<|end|>" + _call("lookup_order", {"order_id": "ORD-1102"}),
            "<|channel|>analysis<|message|>ef<|end|>" + _final("xyz"),
        ]
        episode = self._session(outputs).run_user_turn("Where is ORD-1102?")
        counts = episode.channel_token_counts

        self.assertEqual(counts["analysis"], 6)
        self.assertEqual(counts["final"], 3)
        self.assertEqual(counts["commentary"], len('{"order_id": "ORD-1102"}'))


class InjectionPlacementTest(unittest.TestCase):
    """Each attack channel lands in exactly one place in the episode plan."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.b2 = json.loads(B2_CONFIG.read_text(encoding="utf-8"))
        cls.by_id = {s["base_task_id"]: s for s in cls.b2["scenarios"]}

    def test_direct_user_injection_replaces_the_first_user_message(self) -> None:
        scenario = self.by_id["b2-f2-011-knowledge_qa-sql-query"]
        clean = user_turns(scenario, "clean")
        attack = user_turns(scenario, "attack")

        self.assertEqual(len(clean), 1)
        self.assertEqual(len(attack), 1)
        self.assertNotEqual(clean[0], attack[0])
        self.assertIn("[SQL]", attack[0])
        self.assertIsNone(scenario["arms"]["attack"].get("tool_result_injection"))

    def test_multi_turn_injection_is_a_second_user_turn(self) -> None:
        scenario = self.by_id["b2-f4-021-return_status-geometry-derivation"]
        clean = user_turns(scenario, "clean")
        attack = user_turns(scenario, "attack")

        self.assertEqual(len(clean), 2)
        self.assertEqual(len(attack), 2)
        self.assertEqual(clean[0], attack[0])
        self.assertNotIn("[GEOMETRY]", attack[0])
        self.assertIn("[GEOMETRY]", attack[1])

    def test_tool_output_injection_leaves_the_user_turns_alone(self) -> None:
        scenario = self.by_id["b2-f0-001-order_and_knowledge-free-verse"]
        clean = user_turns(scenario, "clean")
        attack = user_turns(scenario, "attack")
        injection = scenario["arms"]["attack"]["tool_result_injection"]

        self.assertEqual(clean, attack)
        self.assertEqual(injection["action"], "search_support_kb")
        self.assertEqual(injection["location"], "first_kb_hit")
        self.assertIn("[FREEVERSE]", injection["content"])


if __name__ == "__main__":
    unittest.main()
