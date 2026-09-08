"""Strict loader for the Atlas v2 agent definition."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runtime import BASE_ACTIONS, KNOWN_ACTIONS, SupportToolEnvironment


@dataclass(frozen=True)
class AgentV2Definition:
    agent_id: str
    definition_version: str
    authorized_domain: str
    assistant_protocol: str
    system_prompt: str
    max_agent_steps: int
    control_mode: str
    tool_names: tuple[str, ...]
    tool_schemas: dict[str, dict[str, Any]]
    environment: SupportToolEnvironment
    config_path: Path


def _required_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def load_agent_v2_definition(path: Path, *, workspace_root: Path | None = None) -> AgentV2Definition:
    path = path.resolve()
    config: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema_version") != 1:
        raise ValueError("agent v2 schema_version must be 1")
    if config.get("assistant_protocol") != "json_action_or_text":
        raise ValueError("agent v2 requires json_action_or_text protocol")

    tools = config.get("tools")
    if not isinstance(tools, dict):
        raise ValueError("agent v2 tools must be an object")
    tool_names = frozenset(tools)
    if not BASE_ACTIONS <= tool_names <= KNOWN_ACTIONS:
        raise ValueError(
            "agent v2 tools must contain the base tools and only known expanded tools"
        )
    for name, schema in tools.items():
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError(f"{name} must define an object argument schema")

    runtime = config.get("runtime")
    if not isinstance(runtime, dict):
        raise ValueError("agent v2 runtime configuration is required")
    max_agent_steps = runtime.get("max_agent_steps")
    if not isinstance(max_agent_steps, int) or isinstance(max_agent_steps, bool) or max_agent_steps < 2:
        raise ValueError("max_agent_steps must be an integer of at least 2")
    if runtime.get("tool_execution") != "sandboxed_read_only":
        raise ValueError("agent v2 tools must use sandboxed_read_only execution")
    control_mode = str(runtime.get("control_mode", "model_planned_tools"))
    if control_mode not in {
        "model_planned_tools",
        "state_guided_tools",
        "orchestrated_tools",
    }:
        raise ValueError("unsupported agent v2 control_mode")

    root = workspace_root.resolve() if workspace_root is not None else path.parents[1]
    knowledge_path = root / _required_string(config.get("knowledge_base"), "knowledge_base")
    records_path = root / _required_string(config.get("support_records"), "support_records")
    environment = SupportToolEnvironment.from_files(
        records_path=records_path,
        knowledge_path=knowledge_path,
        available_actions=tool_names,
    )
    return AgentV2Definition(
        agent_id=_required_string(config.get("agent_id"), "agent_id"),
        definition_version=_required_string(
            config.get("definition_version"), "definition_version"
        ),
        authorized_domain=_required_string(
            config.get("authorized_domain"), "authorized_domain"
        ),
        assistant_protocol=str(config["assistant_protocol"]),
        system_prompt=_required_string(config.get("system_prompt"), "system_prompt"),
        max_agent_steps=max_agent_steps,
        control_mode=control_mode,
        tool_names=tuple(sorted(tool_names)),
        tool_schemas={str(name): dict(schema) for name, schema in tools.items()},
        environment=environment,
        config_path=path,
    )
