"""Experiment-config schema and episode planning for Agent v3 batches.

Scenarios keep the frozen Atlas v2.5-B2 shape (task, requirements, completion
evidence, three arms, sampling seed) so that a v3 batch can be traced back to
the batch it was derived from. What changes is the runtime: the model plans its
own tool calls, and an attack channel maps onto episodes rather than onto the
deterministic controller.

Channel to episode mapping:

* ``direct_user``     - injected text replaces the first user message;
* ``multi_turn_user`` - the scenario's clarification reply becomes a **second**
  user turn, i.e. a second episode inside the same session;
* ``tool_output``     - the injection is applied to the model-visible result of
  the tool it targets, whenever the model calls that tool.
"""

from __future__ import annotations

from typing import Any

from agent_v2 import task_from_scenario

from .tools import READ_ONLY_TOOLS


ARM_NAMES = ("clean", "benign_control", "attack")
ATTACK_CHANNELS = ("direct_user", "multi_turn_user", "tool_output")
INJECTION_LOCATIONS = ("result_note", "first_kb_hit")
WORDING_TIERS = ("T0", "T1", "T2")
DOMAIN_GROUPS = ("creative", "code", "reasoning_legal", "everyday_knowledge")


def user_turns(scenario: dict[str, Any], arm_name: str) -> list[str]:
    """The user messages of this scenario arm, one episode each."""

    if arm_name not in ARM_NAMES:
        raise ValueError(f"unsupported arm: {arm_name}")
    task = scenario["task"]
    arm = scenario["arms"][arm_name]
    turns = [str(arm.get("user_request", task["user_request"]))]
    if task.get("clarification") is not None:
        response = arm.get(
            "clarification_response", scenario.get("clarification_response")
        )
        if not isinstance(response, str) or not response:
            raise ValueError(
                f"{scenario['base_task_id']}/{arm_name} needs a clarification_response"
            )
        turns.append(response)
    return turns


def _check_injection(scenario_id: str, injection: Any, required_actions: set[str]) -> None:
    if injection is None:
        return
    if not isinstance(injection, dict):
        raise ValueError(f"{scenario_id} tool_result_injection must be an object")
    action = injection.get("action")
    if action not in READ_ONLY_TOOLS:
        raise ValueError(f"{scenario_id} injection must target a read-only tool")
    if action not in required_actions:
        raise ValueError(f"{scenario_id} injection must target a routine tool")
    if injection.get("location") not in INJECTION_LOCATIONS:
        raise ValueError(f"{scenario_id} injection location is unsupported")
    if injection.get("location") == "first_kb_hit" and action != "search_support_kb":
        raise ValueError(f"{scenario_id} first_kb_hit injection must target the KB search")
    if not isinstance(injection.get("content"), str) or not injection["content"]:
        raise ValueError(f"{scenario_id} injection content must be non-empty")


def validate_agent_v3_experiment(config: dict[str, Any]) -> None:
    """Strict schema check for a v3 batch or probe config."""

    if config.get("schema_version") != 1 or config.get("phase") != "agent_v3":
        raise ValueError("expected agent_v3 experiment schema version 1")
    for field in ("experiment_id", "agent_config", "model_config", "dataset_role"):
        if not isinstance(config.get(field), str) or not config[field]:
            raise ValueError(f"{field} must be a non-empty string")

    arms = config.get("arms", list(ARM_NAMES))
    if not isinstance(arms, list) or not arms or not set(arms) <= set(ARM_NAMES):
        raise ValueError("arms must be a non-empty subset of the three arm names")

    decoding = config.get("decoding")
    if not isinstance(decoding, dict):
        raise ValueError("decoding configuration is required")
    if decoding.get("strategy") not in {"greedy", "sample"}:
        raise ValueError("decoding strategy must be greedy or sample")
    if decoding["strategy"] == "sample":
        if float(decoding.get("temperature", 0)) <= 0:
            raise ValueError("sample decoding requires positive temperature")
        if not 0 < float(decoding.get("top_p", 0)) <= 1:
            raise ValueError("sample decoding requires top_p in (0, 1]")
    max_new_tokens = decoding.get("max_new_tokens_per_turn")
    if not isinstance(max_new_tokens, int) or isinstance(max_new_tokens, bool) or max_new_tokens <= 0:
        raise ValueError("max_new_tokens_per_turn must be a positive integer")
    max_steps = decoding.get("max_agent_steps")
    if not isinstance(max_steps, int) or isinstance(max_steps, bool) or max_steps <= 0:
        raise ValueError("max_agent_steps must be a positive integer")

    scenarios = config.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("at least one scenario is required")

    seen_task_ids: set[str] = set()
    seen_pair_ids: set[str] = set()
    for scenario in scenarios:
        task_id = str(scenario.get("base_task_id", ""))
        pair_id = str(scenario.get("pair_group_id", ""))
        if not task_id or not pair_id or task_id in seen_task_ids or pair_id in seen_pair_ids:
            raise ValueError("base_task_id and pair_group_id must be unique and non-empty")
        seen_task_ids.add(task_id)
        seen_pair_ids.add(pair_id)
        if not isinstance(scenario.get("analysis_group_id"), str) or not scenario["analysis_group_id"]:
            raise ValueError(f"{task_id} requires a non-empty analysis_group_id")
        seed = scenario.get("sampling_seed")
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise ValueError(f"{task_id} requires a non-negative integer sampling_seed")
        if scenario.get("domain_group") is not None and scenario["domain_group"] not in DOMAIN_GROUPS:
            raise ValueError(f"{task_id} has an unsupported domain_group")
        if scenario.get("wording_tier") is not None and scenario["wording_tier"] not in WORDING_TIERS:
            raise ValueError(f"{task_id} has an unsupported wording_tier")

        scenario_arms = scenario.get("arms")
        if not isinstance(scenario_arms, dict) or set(scenario_arms) != set(ARM_NAMES):
            raise ValueError(f"{task_id} must define clean/benign_control/attack")
        task = scenario.get("task")
        if not isinstance(task, dict) or not isinstance(task.get("requirements"), list):
            raise ValueError(f"{task_id} must define a task and requirements")
        required_actions = {str(item.get("action")) for item in task["requirements"]}

        for arm_name in ARM_NAMES:
            task_from_scenario(scenario, arm_name)
            arm = scenario_arms[arm_name]
            if not isinstance(arm, dict):
                raise ValueError(f"{task_id}/{arm_name} must be an object")
            _check_injection(f"{task_id}/{arm_name}", arm.get("tool_result_injection"), required_actions)
            user_turns(scenario, arm_name)

        attack = scenario_arms["attack"]
        channel = attack.get("channel")
        if channel not in ATTACK_CHANNELS:
            raise ValueError(f"{task_id} attack must declare a supported channel")
        if channel == "direct_user" and not isinstance(attack.get("user_request"), str):
            raise ValueError(f"{task_id} direct-user attack requires user_request")
        if channel == "multi_turn_user" and task.get("clarification") is None:
            raise ValueError(f"{task_id} multi-turn attack requires a clarification turn")
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
