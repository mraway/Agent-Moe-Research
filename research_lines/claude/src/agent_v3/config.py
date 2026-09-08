"""Loader for the Agent v3 definition (system prompt + harmony tool schemas)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_v2.runtime import SupportToolEnvironment

from .tools import (
    ESCALATION_TOOLS,
    READ_ONLY_TOOLS,
    RESTRICTED_TOOLS,
    TOOL_CLASSES,
)


@dataclass(frozen=True)
class AgentV3Definition:
    agent_id: str
    definition_version: str
    authorized_domain: str
    assistant_protocol: str
    system_prompt: str
    max_agent_steps: int
    tool_specs: tuple[dict[str, Any], ...]
    tool_classes: dict[str, str]
    environment: SupportToolEnvironment
    config_path: Path
    knowledge_base_path: Path
    support_records_path: Path

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(spec["function"]["name"] for spec in self.tool_specs)

    @property
    def restricted_tool_names(self) -> tuple[str, ...]:
        return tuple(
            name for name in self.tool_names if self.tool_classes[name] == "restricted"
        )

    def harmony_tools(self) -> list[dict[str, Any]]:
        """Tool list in the shape the harmony chat template expects."""

        return [json.loads(json.dumps(spec)) for spec in self.tool_specs]


def _required_string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def load_agent_v3_definition(
    path: Path, *, workspace_root: Path | None = None
) -> AgentV3Definition:
    path = Path(path).resolve()
    config: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if config.get("schema_version") != 1:
        raise ValueError("agent v3 schema_version must be 1")
    if config.get("phase") != "agent_v3":
        raise ValueError("agent v3 definition must declare phase agent_v3")
    if config.get("assistant_protocol") != "harmony_tool_calls":
        raise ValueError("agent v3 requires the harmony_tool_calls protocol")

    tools = config.get("tools")
    if not isinstance(tools, dict) or not tools:
        raise ValueError("agent v3 tools must be a non-empty object")

    tool_specs: list[dict[str, Any]] = []
    tool_classes: dict[str, str] = {}
    for name, spec in tools.items():
        if not isinstance(spec, dict):
            raise ValueError(f"tool {name} must be an object")
        tool_class = spec.get("tool_class")
        if tool_class not in TOOL_CLASSES:
            raise ValueError(f"tool {name} must declare a supported tool_class")
        description = _required_string(spec.get("description"), f"{name} description")
        parameters = spec.get("parameters")
        if not isinstance(parameters, dict) or parameters.get("type") != "object":
            raise ValueError(f"tool {name} must define an object parameter schema")
        tool_classes[str(name)] = str(tool_class)
        tool_specs.append(
            {
                "type": "function",
                "function": {
                    "name": str(name),
                    "description": description,
                    "parameters": json.loads(json.dumps(parameters)),
                },
            }
        )

    declared_read_only = {n for n, c in tool_classes.items() if c == "read_only"}
    declared_restricted = {n for n, c in tool_classes.items() if c == "restricted"}
    declared_escalation = {n for n, c in tool_classes.items() if c == "escalation"}
    if declared_read_only != set(READ_ONLY_TOOLS):
        raise ValueError(
            f"agent v3 must expose exactly the read-only tools {sorted(READ_ONLY_TOOLS)}"
        )
    if declared_restricted != set(RESTRICTED_TOOLS):
        raise ValueError(
            f"agent v3 must expose exactly the restricted stubs {sorted(RESTRICTED_TOOLS)}"
        )
    if declared_escalation != set(ESCALATION_TOOLS):
        raise ValueError(
            f"agent v3 must expose exactly the escalation tools {sorted(ESCALATION_TOOLS)}"
        )

    runtime = config.get("runtime")
    if not isinstance(runtime, dict):
        raise ValueError("agent v3 runtime configuration is required")
    max_agent_steps = runtime.get("max_agent_steps")
    if (
        not isinstance(max_agent_steps, int)
        or isinstance(max_agent_steps, bool)
        or max_agent_steps < 2
    ):
        raise ValueError("max_agent_steps must be an integer of at least 2")
    if runtime.get("tool_execution") != "sandboxed_read_only":
        raise ValueError("agent v3 tools must use sandboxed_read_only execution")
    if runtime.get("control_mode") != "model_planned_tools":
        raise ValueError("agent v3 requires control_mode model_planned_tools")

    root = Path(workspace_root).resolve() if workspace_root is not None else path.parents[1]
    knowledge_path = root / _required_string(config.get("knowledge_base"), "knowledge_base")
    records_path = root / _required_string(config.get("support_records"), "support_records")
    environment = SupportToolEnvironment.from_files(
        records_path=records_path,
        knowledge_path=knowledge_path,
        available_actions=frozenset(declared_read_only),
    )
    return AgentV3Definition(
        agent_id=_required_string(config.get("agent_id"), "agent_id"),
        definition_version=_required_string(
            config.get("definition_version"), "definition_version"
        ),
        authorized_domain=_required_string(
            config.get("authorized_domain"), "authorized_domain"
        ),
        assistant_protocol=str(config["assistant_protocol"]),
        system_prompt=_required_string(config.get("system_prompt"), "system_prompt"),
        max_agent_steps=int(max_agent_steps),
        tool_specs=tuple(tool_specs),
        tool_classes=tool_classes,
        environment=environment,
        config_path=path,
        knowledge_base_path=knowledge_path.resolve(),
        support_records_path=records_path.resolve(),
    )
