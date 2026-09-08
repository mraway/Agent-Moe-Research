"""Model-driven tool-calling episodes with a continuous routing token axis.

One *episode* is one user turn: every token the model generates for that turn,
across all agent steps, on a single causal token axis. Step ``s`` contributes
``output_token_count[s]`` generated tokens starting at
``global_token_offset[s]``, and the routing shard for episode token ``g`` is
``routing_step_index_first_decode[s] + (g - global_token_offset[s])``, so a
detector can walk the whole episode without resetting between steps.

Sessions (a second user turn, i.e. the ``multi_turn_user`` channel) keep the
same message list and the same recorder, and additionally carry a session-level
token index.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from phase_a import generate_routed_turn
from phase_a.generation import find_channel_boundaries

from .harmony import (
    HarmonySpecials,
    channel_token_counts,
    read_step,
    tool_call_message,
)
from .tools import AgentV3ToolController


HARMONY_CHANNEL_MARKERS = {
    "analysis": "<|channel|>analysis",
    "commentary": "<|channel|>commentary",
    "final": "<|channel|>final",
}
EPISODE_STOP_REASONS = (
    "final_channel",
    "malformed_tool_call",
    "max_agent_steps",
    "no_tool_call_no_final",
)


@dataclass
class ToolEventRecord:
    """An automatic event: one model-issued call, executed or refused."""

    event_id: str
    episode_index: int
    conversation_turn: int
    agent_step: int
    tool_name: str | None
    tool_class: str
    restricted: bool
    malformed: bool
    executed: bool
    error: str | None
    arguments: dict[str, Any]
    raw_arguments: str
    injection_applied: bool
    x_tool: bool
    header_repeated: bool
    call_first_token_global: int
    call_last_token_global: int
    arguments_start_global: int
    arguments_end_global: int
    call_first_token_session: int
    call_last_token_session: int
    call_first_token_in_step: int
    call_last_token_in_step: int
    routing_step_index_first_token: int
    routing_step_index_last_token: int
    result: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class StepRecord:
    episode_index: int
    conversation_turn: int
    agent_step: int
    prompt_token_count: int
    output_token_count: int
    global_token_offset: int
    session_token_offset: int
    routing_step_index_prefill: int
    routing_step_index_first_decode: int
    generation_stop_reason: str
    action: str
    channel_boundaries: dict[str, int]
    channel_segments: list[dict[str, Any]]
    channel_token_counts: dict[str, int]
    wall_seconds: float
    text: str
    output_token_ids: list[int]

    def as_dict(self, *, include_token_ids: bool = True) -> dict[str, Any]:
        payload = dict(self.__dict__)
        if not include_token_ids:
            payload.pop("output_token_ids")
        return payload


@dataclass
class EpisodeRecord:
    episode_index: int
    conversation_turn: int
    user_request: str
    steps: list[StepRecord] = field(default_factory=list)
    tool_events: list[ToolEventRecord] = field(default_factory=list)
    stop_reason: str = "max_agent_steps"
    final_text: str = ""
    generated_token_count: int = 0
    session_token_offset: int = 0
    wall_seconds: float = 0.0

    @property
    def channel_token_counts(self) -> dict[str, int]:
        totals: dict[str, int] = {}
        for step in self.steps:
            for channel, count in step.channel_token_counts.items():
                totals[channel] = totals.get(channel, 0) + count
        return totals

    @property
    def step_count(self) -> int:
        return len(self.steps)


class AgentV3Session:
    """A conversation with the model-driven tool loop, one episode per user turn."""

    def __init__(
        self,
        *,
        model: Any,
        tokenizer: Any,
        recorder: Any,
        controller: AgentV3ToolController,
        system_prompt: str,
        chat_template_kwargs: Mapping[str, Any],
        max_agent_steps: int,
        max_new_tokens: int,
        decoding_strategy: str = "sample",
        temperature: float = 0.8,
        top_p: float = 0.9,
        stop_token_ids: Sequence[int] = (),
        specials: HarmonySpecials | None = None,
        event_sink: Any = None,
    ) -> None:
        if max_agent_steps < 1:
            raise ValueError("max_agent_steps must be positive")
        self.model = model
        self.tokenizer = tokenizer
        self.recorder = recorder
        self.controller = controller
        self.chat_template_kwargs = dict(chat_template_kwargs)
        self.max_agent_steps = int(max_agent_steps)
        self.max_new_tokens = int(max_new_tokens)
        self.decoding_strategy = decoding_strategy
        self.temperature = float(temperature)
        self.top_p = float(top_p)
        self.stop_token_ids = [int(value) for value in stop_token_ids]
        self.specials = specials or HarmonySpecials.from_tokenizer(tokenizer)
        self.event_sink = event_sink
        self.messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": system_prompt,
                "logical_role": "system",
                "conversation_turn": 0,
                "agent_step": 0,
            }
        ]
        self.episodes: list[EpisodeRecord] = []
        self.conversation_turn = 0
        self.session_token_count = 0

    def _emit(self, row: dict[str, Any]) -> None:
        if self.event_sink is not None:
            self.event_sink(row)

    def run_user_turn(self, user_request: str) -> EpisodeRecord:
        """Generate one full episode for ``user_request`` and return its record."""

        self.conversation_turn += 1
        episode_index = len(self.episodes)
        episode = EpisodeRecord(
            episode_index=episode_index,
            conversation_turn=self.conversation_turn,
            user_request=user_request,
            session_token_offset=self.session_token_count,
        )
        self.episodes.append(episode)
        self.messages.append(
            {
                "role": "user",
                "content": user_request,
                "logical_role": "user",
                "conversation_turn": self.conversation_turn,
                "agent_step": 0,
            }
        )
        self._emit(
            {
                "kind": "user_message",
                "actor": "user",
                "logical_role": "user",
                "source_trust": "authenticated_limited",
                "model_visible": True,
                "user_visible": True,
                "episode_index": episode_index,
                "conversation_turn": self.conversation_turn,
                "agent_step": 0,
                "content": user_request,
            }
        )

        episode_started = time.perf_counter()
        for agent_step in range(self.max_agent_steps):
            step, reading = self._generate_step(episode, agent_step)
            episode.steps.append(step)
            episode.generated_token_count += step.output_token_count
            self.session_token_count += step.output_token_count

            if reading.final_segment is not None:
                episode.stop_reason = "final_channel"
                episode.final_text = reading.final_text
                self.messages.append(
                    {
                        "role": "assistant",
                        "content": reading.final_text,
                        "logical_role": "assistant",
                        "conversation_turn": self.conversation_turn,
                        "agent_step": agent_step,
                    }
                )
                self._emit(
                    {
                        "kind": "assistant_message",
                        "actor": "assistant",
                        "logical_role": "assistant",
                        "source_trust": "model",
                        "model_visible": True,
                        "user_visible": True,
                        "episode_index": episode_index,
                        "conversation_turn": self.conversation_turn,
                        "agent_step": agent_step,
                        "content": reading.final_text,
                    }
                )
                break

            if reading.tool_call is None:
                episode.stop_reason = "no_tool_call_no_final"
                break

            event = self._handle_tool_call(episode, step, agent_step, reading.tool_call)
            if event.malformed:
                episode.stop_reason = "malformed_tool_call"
                break
        else:
            episode.stop_reason = "max_agent_steps"

        episode.wall_seconds = round(time.perf_counter() - episode_started, 6)
        return episode

    def _generate_step(
        self, episode: EpisodeRecord, agent_step: int
    ) -> tuple[StepRecord, Any]:
        prefill_index = int(self.recorder.next_step_index)
        started = time.perf_counter()
        generated = generate_routed_turn(
            model=self.model,
            tokenizer=self.tokenizer,
            messages=self.messages,
            recorder=self.recorder,
            conversation_turn=self.conversation_turn,
            agent_step=agent_step,
            max_new_tokens=self.max_new_tokens,
            stop_on_complete_protocol_object=False,
            decoding_strategy=self.decoding_strategy,
            temperature=self.temperature,
            top_p=self.top_p,
            assistant_protocol="json_action_or_text",
            chat_template_kwargs=self.chat_template_kwargs,
            channel_markers=HARMONY_CHANNEL_MARKERS,
            extra_stop_token_ids=self.stop_token_ids,
        )
        wall_seconds = time.perf_counter() - started
        reading = read_step(
            self.tokenizer,
            generated.output_token_ids,
            self.controller.tool_names,
            self.specials,
        )
        global_offset = episode.generated_token_count
        session_offset = self.session_token_count
        segments = []
        for segment in reading.segments:
            row = segment.as_dict()
            row["global_header_start"] = global_offset + segment.header_start
            row["global_body_start"] = global_offset + segment.body_start
            row["global_body_end"] = global_offset + segment.body_end
            segments.append(row)
        action = (
            "final"
            if reading.final_segment is not None
            else "tool_call"
            if reading.tool_call is not None
            else "none"
        )
        step = StepRecord(
            episode_index=episode.episode_index,
            conversation_turn=self.conversation_turn,
            agent_step=agent_step,
            prompt_token_count=generated.prompt_token_count,
            output_token_count=generated.output_token_count,
            global_token_offset=global_offset,
            session_token_offset=session_offset,
            routing_step_index_prefill=prefill_index,
            routing_step_index_first_decode=prefill_index + 1,
            generation_stop_reason=generated.stop_reason,
            action=action,
            channel_boundaries=dict(generated.channel_boundaries),
            channel_segments=segments,
            channel_token_counts=channel_token_counts(reading.segments),
            wall_seconds=round(wall_seconds, 6),
            text=generated.text,
            output_token_ids=list(generated.output_token_ids),
        )
        self._emit(
            {
                "kind": "model_generation",
                "actor": "assistant",
                "logical_role": "assistant",
                "source_trust": "model",
                "model_visible": True,
                "user_visible": False,
                "episode_index": episode.episode_index,
                "conversation_turn": self.conversation_turn,
                "agent_step": agent_step,
                "content": generated.text,
                "raw_text": self.tokenizer.decode(
                    generated.output_token_ids,
                    skip_special_tokens=False,
                    clean_up_tokenization_spaces=False,
                ),
                "prompt_token_count": generated.prompt_token_count,
                "output_token_count": generated.output_token_count,
                "output_token_ids": list(generated.output_token_ids),
                "stop_reason": generated.stop_reason,
                "rendered_prompt": generated.rendered_prompt,
                "channel_boundaries": dict(generated.channel_boundaries),
                "channel_segments": segments,
                "routing_step_index_prefill": prefill_index,
                "routing_step_index_first_decode": prefill_index + 1,
                "global_token_offset": global_offset,
            }
        )
        return step, reading

    def _handle_tool_call(
        self,
        episode: EpisodeRecord,
        step: StepRecord,
        agent_step: int,
        parsed: Any,
    ) -> ToolEventRecord:
        assert parsed is not None
        segment = parsed.segment
        first_in_step = segment.header_start
        last_in_step = (
            segment.terminator_index
            if segment.terminator_index >= 0
            else max(segment.body_end - 1, segment.header_start)
        )
        global_offset = step.global_token_offset
        session_offset = step.session_token_offset
        first_decode = step.routing_step_index_first_decode

        if parsed.malformed:
            tool_class = (
                self.controller.tool_class(parsed.name) if parsed.name else "unknown"
            )
            execution_result: dict[str, Any] = {
                "ok": False,
                "error": f"malformed_tool_call: {parsed.error}",
            }
            executed = False
            injection_applied = False
        else:
            assert parsed.name is not None
            execution = self.controller.execute(
                name=parsed.name, arguments=parsed.arguments
            )
            tool_class = execution.tool_class
            execution_result = execution.result
            executed = execution.executed
            injection_applied = execution.injection_applied

        restricted = tool_class == "restricted"
        event = ToolEventRecord(
            event_id=f"e{episode.episode_index}-s{agent_step}-{len(episode.tool_events)}",
            episode_index=episode.episode_index,
            conversation_turn=self.conversation_turn,
            agent_step=agent_step,
            tool_name=parsed.name,
            tool_class=tool_class,
            restricted=restricted,
            malformed=parsed.malformed,
            executed=executed,
            error=parsed.error if parsed.malformed else None,
            arguments=dict(parsed.arguments),
            raw_arguments=parsed.raw_arguments,
            injection_applied=injection_applied,
            x_tool=restricted and not parsed.malformed,
            header_repeated=segment.header_repeated,
            call_first_token_global=global_offset + first_in_step,
            call_last_token_global=global_offset + last_in_step,
            arguments_start_global=global_offset + segment.body_start,
            arguments_end_global=global_offset + segment.body_end,
            call_first_token_session=session_offset + first_in_step,
            call_last_token_session=session_offset + last_in_step,
            call_first_token_in_step=first_in_step,
            call_last_token_in_step=last_in_step,
            routing_step_index_first_token=first_decode + first_in_step,
            routing_step_index_last_token=first_decode + last_in_step,
            result=execution_result,
        )
        episode.tool_events.append(event)
        self._emit(
            {
                "kind": "tool_call",
                "actor": "assistant",
                "logical_role": "assistant",
                "source_trust": "model",
                "model_visible": True,
                "user_visible": False,
                "episode_index": episode.episode_index,
                "conversation_turn": self.conversation_turn,
                "agent_step": agent_step,
                "content": parsed.raw_arguments,
                "tool_name": parsed.name,
                "tool_class": tool_class,
                "restricted": restricted,
                "malformed": parsed.malformed,
                "executed": executed,
                "error": event.error,
                "call_first_token_global": event.call_first_token_global,
                "call_last_token_global": event.call_last_token_global,
            }
        )
        if parsed.malformed:
            return event

        assert parsed.name is not None
        self.messages.append(
            {
                **tool_call_message(parsed.name, parsed.arguments),
                "logical_role": "assistant",
                "conversation_turn": self.conversation_turn,
                "agent_step": agent_step,
            }
        )
        self.messages.append(
            {
                "role": "tool",
                "content": execution_result,
                "logical_role": "tool",
                "conversation_turn": self.conversation_turn,
                "agent_step": agent_step,
            }
        )
        self._emit(
            {
                "kind": "tool_result",
                "actor": "tool",
                "logical_role": "tool",
                "source_trust": (
                    "untrusted"
                    if injection_applied
                    else "trusted_data_untrusted_instructions"
                ),
                "model_visible": True,
                "user_visible": False,
                "episode_index": episode.episode_index,
                "conversation_turn": self.conversation_turn,
                "agent_step": agent_step,
                "content": execution_result,
                "tool_name": parsed.name,
                "tool_class": tool_class,
                "executed": executed,
                "injection_applied": injection_applied,
            }
        )
        return event
