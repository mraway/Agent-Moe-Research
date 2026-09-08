"""Frozen experiment-schema helpers for Atlas v2 model runs."""

from __future__ import annotations

from typing import Any

from .runtime import (
    KNOWN_ACTIONS,
    ClarificationRequirement,
    SupportTask,
    ToolRequirement,
)


ARM_NAMES = ("clean", "benign_control", "attack")
INJECTION_LOCATIONS = frozenset({"result_note", "first_kb_hit"})


def task_from_scenario(scenario: dict[str, Any], arm_name: str) -> SupportTask:
    if arm_name not in ARM_NAMES:
        raise ValueError(f"unsupported arm: {arm_name}")
    task = scenario["task"]
    arm = scenario["arms"][arm_name]
    requirements = tuple(
        ToolRequirement(
            action=str(item["action"]),
            arguments=dict(item.get("arguments", {})),
            expected_article_ids=tuple(str(value) for value in item.get("expected_article_ids", [])),
            allow_empty_result=bool(item.get("allow_empty_result", False)),
            query_hint=(
                str(item["query_hint"])
                if item.get("query_hint") is not None
                else None
            ),
        )
        for item in task["requirements"]
    )
    clarification_config = task.get("clarification")
    clarification = None
    if clarification_config is not None:
        clarification = ClarificationRequirement(
            field=str(clarification_config["field"]),
            expected_value=str(clarification_config["expected_value"]),
            request_evidence=tuple(
                str(value) for value in clarification_config["request_evidence"]
            ),
        )
    return SupportTask(
        task_id=str(scenario["base_task_id"]),
        workflow_kind=str(task["workflow_kind"]),  # type: ignore[arg-type]
        user_request=str(arm.get("user_request", task["user_request"])),
        requirements=requirements,
        completion_evidence=tuple(
            tuple(str(alternative) for alternative in value)
            if isinstance(value, list)
            else str(value)
            for value in task["completion_evidence"]
        ),
        clarification=clarification,
        response_brief=(
            str(task["response_brief"])
            if task.get("response_brief") is not None
            else None
        ),
    )


def apply_tool_result_injection(
    *,
    action_name: str,
    result: dict[str, object],
    injection: dict[str, Any] | None,
) -> dict[str, object]:
    """Add a provenance-marked experiment string to a model-visible tool result."""

    if injection is None or action_name != injection["action"]:
        return result
    location = injection["location"]
    content = str(injection["content"])
    metadata = {
        "location": location,
        "source_trust": "untrusted",
        "facts_authoritative": False,
        "instructions_authoritative": False,
        "applied": True,
    }
    if location == "result_note":
        result["external_note"] = content
    elif location == "first_kb_hit":
        hits = result.get("hits")
        if not isinstance(hits, list) or not hits:
            metadata["applied"] = False
            metadata["reason"] = "knowledge search returned no hits"
            result["experiment_perturbation"] = metadata
            return result
        first = hits[0]
        if not isinstance(first, dict) or not isinstance(first.get("content"), str):
            raise ValueError("first KB hit must contain text content")
        first["content"] = f"{first['content']}\n\nRetrieved external note: {content}"
        first["external_note_provenance"] = metadata
    else:
        raise ValueError(f"unsupported injection location: {location}")
    result["experiment_perturbation"] = metadata
    return result


def validate_experiment_config(config: dict[str, Any]) -> None:
    if config.get("schema_version") != 1 or config.get("phase") != "agent_v2":
        raise ValueError("expected agent_v2 experiment schema version 1")
    for field in ("experiment_id", "agent_config", "model_config"):
        if not isinstance(config.get(field), str) or not config[field]:
            raise ValueError(f"{field} must be a non-empty string")
    decoding = config.get("decoding")
    if not isinstance(decoding, dict):
        raise ValueError("decoding configuration is required")
    if decoding.get("strategy") not in {"greedy", "sample"}:
        raise ValueError("decoding strategy must be greedy or sample")
    if decoding.get("strategy") == "sample":
        if float(decoding.get("temperature", 0)) <= 0:
            raise ValueError("sample decoding requires positive temperature")
        top_p = float(decoding.get("top_p", 0))
        if not 0 < top_p <= 1:
            raise ValueError("sample decoding requires top_p in (0, 1]")
    max_new_tokens = decoding.get("max_new_tokens_per_turn")
    if not isinstance(max_new_tokens, int) or max_new_tokens <= 0:
        raise ValueError("max_new_tokens_per_turn must be a positive integer")
    max_agent_steps = decoding.get("max_agent_steps")
    if not isinstance(max_agent_steps, int) or isinstance(max_agent_steps, bool) or max_agent_steps <= 0:
        raise ValueError("max_agent_steps must be a positive integer")

    scenarios = config.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("at least one scenario is required")
    task_ids: set[str] = set()
    pair_ids: set[str] = set()
    for scenario in scenarios:
        task_id = str(scenario.get("base_task_id", ""))
        pair_id = str(scenario.get("pair_group_id", ""))
        if not task_id or not pair_id or task_id in task_ids or pair_id in pair_ids:
            raise ValueError("base_task_id and pair_group_id must be unique and non-empty")
        task_ids.add(task_id)
        pair_ids.add(pair_id)
        if not isinstance(scenario.get("analysis_group_id"), str) or not scenario[
            "analysis_group_id"
        ]:
            raise ValueError(f"{task_id} requires a non-empty analysis_group_id")
        seed = scenario.get("sampling_seed")
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise ValueError(f"{task_id} requires a non-negative integer sampling_seed")
        arms = scenario.get("arms")
        if not isinstance(arms, dict) or set(arms) != set(ARM_NAMES):
            raise ValueError(f"{task_id} must define clean/benign_control/attack")
        task = scenario.get("task")
        if not isinstance(task, dict) or not isinstance(task.get("requirements"), list):
            raise ValueError(f"{task_id} must define a task and requirements")
        required_actions = {
            str(requirement.get("action")) for requirement in task["requirements"]
        }
        for arm_name in ARM_NAMES:
            task_from_scenario(scenario, arm_name)
            arm = arms[arm_name]
            if not isinstance(arm, dict):
                raise ValueError(f"{task_id}/{arm_name} must be an object")
            injection = arm.get("tool_result_injection")
            if injection is not None:
                if not isinstance(injection, dict):
                    raise ValueError("tool_result_injection must be an object")
                if injection.get("action") not in KNOWN_ACTIONS:
                    raise ValueError("tool result injection must target a known action")
                if injection.get("action") not in required_actions:
                    raise ValueError("tool result injection must target a task action")
                if injection.get("location") not in INJECTION_LOCATIONS:
                    raise ValueError("tool result injection has an unsupported location")
                if (
                    injection.get("location") == "first_kb_hit"
                    and injection.get("action") != "search_support_kb"
                ):
                    raise ValueError("first_kb_hit injection must target search_support_kb")
                if not isinstance(injection.get("content"), str) or not injection["content"]:
                    raise ValueError("tool result injection content must be non-empty")
            if task.get("clarification") is not None:
                response = arm.get(
                    "clarification_response", scenario.get("clarification_response")
                )
                if not isinstance(response, str) or not response:
                    raise ValueError(f"{task_id}/{arm_name} requires a clarification response")

        attack = arms["attack"]
        channel = attack.get("channel")
        if channel not in {"direct_user", "multi_turn_user", "tool_output"}:
            raise ValueError(f"{task_id} attack must declare a supported channel")
        if channel == "direct_user" and not isinstance(attack.get("user_request"), str):
            raise ValueError(f"{task_id} direct-user attack requires user_request")
        if channel == "multi_turn_user" and task.get("clarification") is None:
            raise ValueError(f"{task_id} multi-turn attack requires clarification")
        if channel == "tool_output" and attack.get("tool_result_injection") is None:
            raise ValueError(f"{task_id} tool-output attack requires an injection")
        if not isinstance(attack.get("attack_family_id"), str) or not attack["attack_family_id"]:
            raise ValueError(f"{task_id} attack_family_id is required")
        goal = attack.get("attack_goal")
        if not isinstance(goal, dict) or goal.get("type") != "user_visible_text":
            raise ValueError(f"{task_id} requires a user_visible_text attack goal")
        required = goal.get("required_substrings")
        if not isinstance(required, list) or not required or any(
            not isinstance(value, str) or not value for value in required
        ):
            raise ValueError(f"{task_id} attack goal requires non-empty substrings")
        markers = scenario.get("manual_review_markers")
        if not isinstance(markers, list) or not markers:
            raise ValueError(f"{task_id} requires manual_review_markers")
