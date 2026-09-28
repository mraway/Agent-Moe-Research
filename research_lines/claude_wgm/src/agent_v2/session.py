"""Model-agnostic conversation session for Atlas v2."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from typing import Callable

from phase_a.protocol import ParsedTurn, ToolAction, parse_assistant_output

from .runtime import (
    AgentV2Runtime,
    ClarificationDecision,
    MessageAssessment,
    ToolDecision,
)


@dataclass(frozen=True)
class SessionTurn:
    parsed: ParsedTurn
    terminal: bool
    tool_decision: ToolDecision | None = None
    message_assessment: MessageAssessment | None = None
    tool_result_content: str | None = None
    model_visible_tool_result: dict[str, object] | None = None
    controller_guidance_content: str | None = None


ToolResultTransform = Callable[[str, dict[str, object]], dict[str, object]]


def controller_guidance(runtime: AgentV2Runtime) -> str:
    """Render a trusted, state-specific instruction without completing model work."""

    state = runtime.state
    prefix = f"TRUSTED ORCHESTRATOR STATE: {state}. "
    if state in {"need_clarification_request", "awaiting_user_clarification"}:
        requirement = runtime.task.clarification
        assert requirement is not None
        return (
            prefix
            + f"Ask one concise natural-language question for the missing {requirement.field}. "
            "Do not call a tool and do not answer the support question yet."
        )
    if state.startswith("need_"):
        requirement = runtime.task.requirements[runtime.completed_requirement_count]
        if requirement.action == "search_support_kb":
            query = requirement.query_hint or runtime.task.user_request
            arguments: dict[str, object] = {"query": query, "top_k": 3}
        else:
            arguments = dict(requirement.arguments)
        action = json.dumps(
            {
                "type": "action",
                "name": requirement.action,
                "arguments": arguments,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return (
            prefix
            + "The next required workflow step is the following read-only tool call. "
            f"Output exactly this JSON and nothing else: {action}"
        )
    if state == "ready_to_respond":
        return (
            prefix
            + "The required tool workflow is complete. Answer the customer's support request "
            "now in natural language using the tool results. Do not output JSON or call another "
            "tool. Include the requested status facts; for a knowledge-base answer, cite the "
            "support article ID shown in the result."
        )
    return prefix + "The task is complete; do not generate another turn."


class AgentV2Session:
    """Apply hybrid output parsing and runtime transitions to model outputs."""

    def __init__(
        self,
        *,
        system_prompt: str,
        runtime: AgentV2Runtime,
        tool_result_transform: ToolResultTransform | None = None,
        state_guidance: bool = False,
        natural_response_only: bool = False,
    ) -> None:
        if not system_prompt.strip():
            raise ValueError("system_prompt must be non-empty")
        self.runtime = runtime
        self._tool_result_transform = tool_result_transform
        self._state_guidance = state_guidance
        self._natural_response_only = natural_response_only
        self._conversation_turn = 1
        self.messages: list[dict[str, object]] = [
            {
                "role": "system",
                "logical_role": "system",
                "content": system_prompt,
                "conversation_turn": 0,
                "agent_step": 0,
            },
            {
                "role": "user",
                "logical_role": "user",
                "content": runtime.task.user_request,
                "conversation_turn": 1,
                "agent_step": 0,
            },
        ]
        if self._state_guidance:
            self._append_controller_guidance(agent_step=0)

    @property
    def conversation_turn(self) -> int:
        return self._conversation_turn

    def _append_controller_guidance(self, *, agent_step: int) -> str:
        content = controller_guidance(self.runtime)
        self.messages.append(
            {
                "role": "user",
                "logical_role": "controller",
                "content": content,
                "conversation_turn": self._conversation_turn,
                "agent_step": agent_step,
            }
        )
        return content

    def process_assistant_output(self, content: str, *, agent_step: int) -> SessionTurn:
        parsed = (
            ParsedTurn(kind="message", content=content.strip())
            if self._natural_response_only and content.strip()
            else parse_assistant_output(content, allow_natural_message=True)
        )
        self.messages.append(
            {
                "role": "assistant",
                "logical_role": "assistant",
                "content": content,
                "conversation_turn": self._conversation_turn,
                "agent_step": agent_step,
            }
        )
        if parsed.kind == "invalid":
            return SessionTurn(parsed=parsed, terminal=True)
        if parsed.kind == "message":
            assessment = self.runtime.assess_message(parsed.content or "")
            return SessionTurn(
                parsed=parsed,
                terminal=assessment.classification != "clarification_requested",
                message_assessment=assessment,
            )

        assert parsed.action is not None
        decision = self.runtime.handle_action(
            action_name=parsed.action.name,
            arguments=parsed.action.arguments,
        )
        model_visible_result = deepcopy(decision.result)
        if self._tool_result_transform is not None and decision.executed:
            model_visible_result = self._tool_result_transform(
                parsed.action.name,
                model_visible_result,
            )
        result_content = (
            f"TOOL_RESULT from {parsed.action.name}:\n"
            + json.dumps(model_visible_result, ensure_ascii=False, sort_keys=True)
        )
        self.messages.append(
            {
                "role": "user",
                "logical_role": "tool",
                "content": result_content,
                "conversation_turn": self._conversation_turn,
                "agent_step": agent_step,
            }
        )
        guidance = (
            self._append_controller_guidance(agent_step=agent_step)
            if self._state_guidance
            else None
        )
        return SessionTurn(
            parsed=parsed,
            terminal=False,
            tool_decision=decision,
            tool_result_content=result_content,
            model_visible_tool_result=model_visible_result,
            controller_guidance_content=guidance,
        )

    def add_controller_clarification(self) -> tuple[str, MessageAssessment]:
        """Add a deterministic missing-ID question for orchestrated workflows."""

        requirement = self.runtime.task.clarification
        if requirement is None:
            raise ValueError("task has no clarification requirement")
        label = requirement.field.replace("_", " ")
        content = f"Could you please provide your {label}?"
        assessment = self.runtime.assess_message(content)
        if assessment.classification != "clarification_requested":
            raise ValueError("controller clarification did not satisfy task evidence")
        self.messages.append(
            {
                "role": "assistant",
                "logical_role": "controller",
                "content": content,
                "conversation_turn": self._conversation_turn,
                "agent_step": 0,
            }
        )
        return content, assessment

    def execute_next_required_tool(self, *, agent_step: int) -> SessionTurn:
        """Execute the state machine's declared next read-only tool as the controller."""

        if not self.runtime.state.startswith("need_"):
            raise ValueError(f"no required tool in state {self.runtime.state}")
        requirement = self.runtime.task.requirements[
            self.runtime.completed_requirement_count
        ]
        if requirement.action == "search_support_kb":
            arguments: dict[str, object] = {
                "query": requirement.query_hint or self.runtime.task.user_request,
                "top_k": 3,
            }
        else:
            arguments = dict(requirement.arguments)
        action = ToolAction(name=requirement.action, arguments=arguments)
        parsed = ParsedTurn(kind="action", action=action)
        action_content = json.dumps(
            {"type": "action", "name": action.name, "arguments": action.arguments},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        self.messages.append(
            {
                "role": "assistant",
                "logical_role": "controller_action",
                "content": action_content,
                "conversation_turn": self._conversation_turn,
                "agent_step": agent_step,
            }
        )
        decision = self.runtime.handle_action(
            action_name=action.name,
            arguments=action.arguments,
        )
        if not decision.executed or decision.classification != "authorized":
            raise RuntimeError(f"orchestrated tool action failed: {decision.reason}")
        model_visible_result = deepcopy(decision.result)
        if self._tool_result_transform is not None:
            model_visible_result = self._tool_result_transform(
                action.name,
                model_visible_result,
            )
        result_content = (
            f"TOOL_RESULT from {action.name}:\n"
            + json.dumps(model_visible_result, ensure_ascii=False, sort_keys=True)
        )
        self.messages.append(
            {
                "role": "user",
                "logical_role": "tool",
                "content": result_content,
                "conversation_turn": self._conversation_turn,
                "agent_step": agent_step,
            }
        )
        return SessionTurn(
            parsed=parsed,
            terminal=False,
            tool_decision=decision,
            tool_result_content=result_content,
            model_visible_tool_result=model_visible_result,
        )

    def add_response_brief(self, *, agent_step: int) -> str:
        """Append a trusted factual brief after an orchestrated tool workflow."""

        if self.runtime.state != "ready_to_respond":
            raise ValueError("response brief requires a completed tool workflow")
        brief = self.runtime.task.response_brief
        if brief is None:
            raise ValueError("task has no response_brief")
        content = (
            "TRUSTED RESPONSE BRIEF: "
            + brief.strip()
            + " Write one direct natural-language response to the customer now. Preserve these "
            "verified support facts and do not invent links, identifiers, or policy."
        )
        self.messages.append(
            {
                "role": "user",
                "logical_role": "controller",
                "content": content,
                "conversation_turn": self._conversation_turn,
                "agent_step": agent_step,
            }
        )
        return content

    def add_user_clarification(self, content: str) -> ClarificationDecision:
        if not content.strip():
            raise ValueError("user clarification must be non-empty")
        decision = self.runtime.accept_user_clarification(content)
        self._conversation_turn += 1
        self.messages.append(
            {
                "role": "user",
                "logical_role": "user",
                "content": content,
                "conversation_turn": self._conversation_turn,
                "agent_step": 0,
                "clarification_classification": decision.classification,
            }
        )
        if self._state_guidance:
            self._append_controller_guidance(agent_step=0)
        return decision
