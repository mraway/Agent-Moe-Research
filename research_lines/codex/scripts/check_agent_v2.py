#!/usr/bin/env python3
"""Validate Atlas v2.5 and exercise every deterministic routine workflow."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_v2 import (  # noqa: E402
    AgentV2Runtime,
    SupportTask,
    ToolRequirement,
    load_agent_v2_definition,
)


@dataclass(frozen=True)
class WorkflowCheck:
    workflow_kind: str
    record_action: str | None
    record_arguments: dict[str, object]
    query: str | None
    expected_article_id: str | None
    response: str


CHECKS = (
    WorkflowCheck(
        "order_status",
        "lookup_order",
        {"order_id": "ORD-210"},
        None,
        None,
        "ORD-210 is in transit.",
    ),
    WorkflowCheck(
        "return_status",
        "lookup_return",
        {"return_id": "RET-205"},
        None,
        None,
        "RET-205 has completed inspection and its refund is approved.",
    ),
    WorkflowCheck(
        "knowledge_qa",
        None,
        {},
        "refund processing time",
        "KB-REFUND-001",
        "KB-REFUND-001 says approved refunds take two to six business days.",
    ),
    WorkflowCheck(
        "order_and_knowledge",
        "lookup_order",
        {"order_id": "ORD-223"},
        "proof of delivery signature reception",
        "KB-PROOF-001",
        "ORD-223 was delivered to reception; KB-PROOF-001 explains proof of delivery.",
    ),
    WorkflowCheck(
        "return_and_knowledge",
        "lookup_return",
        {"return_id": "RET-112"},
        "return label prepaid label",
        "KB-LABEL-001",
        "RET-112 has a return label issued; KB-LABEL-001 explains how to use it.",
    ),
    WorkflowCheck(
        "support_case_status",
        "lookup_support_case",
        {"case_id": "CASE-1001"},
        None,
        None,
        "CASE-1001 is investigating with its next update planned for 2026-09-06.",
    ),
    WorkflowCheck(
        "warranty_status",
        "lookup_warranty",
        {"warranty_id": "WAR-1001"},
        None,
        None,
        "WAR-1001 is active through 2027-03-14.",
    ),
    WorkflowCheck(
        "subscription_status",
        "lookup_subscription",
        {"subscription_id": "SUB-1001"},
        None,
        None,
        "SUB-1001 is active and renews on 2026-09-18.",
    ),
    WorkflowCheck(
        "case_and_knowledge",
        "lookup_support_case",
        {"case_id": "CASE-1003"},
        "case escalation repeated billing error",
        "KB-CASE-ESCALATE-001",
        "CASE-1003 is escalated; KB-CASE-ESCALATE-001 lists repeated billing errors.",
    ),
    WorkflowCheck(
        "warranty_and_knowledge",
        "lookup_warranty",
        {"warranty_id": "WAR-1007"},
        "warranty exclusion accidental damage",
        "KB-WARRANTY-EXCLUSION-001",
        "WAR-1007 was declined; KB-WARRANTY-EXCLUSION-001 covers accidental damage.",
    ),
    WorkflowCheck(
        "subscription_and_knowledge",
        "lookup_subscription",
        {"subscription_id": "SUB-1002"},
        "cancel subscription final access",
        "KB-SUB-CANCEL-001",
        "SUB-1002 has cancellation scheduled; KB-SUB-CANCEL-001 explains final access.",
    ),
)


def _task(check: WorkflowCheck) -> SupportTask:
    requirements: list[ToolRequirement] = []
    if check.record_action is not None:
        requirements.append(
            ToolRequirement(
                action=check.record_action,
                arguments=check.record_arguments,
            )
        )
    if check.query is not None:
        requirements.append(
            ToolRequirement(
                action="search_support_kb",
                expected_article_ids=(str(check.expected_article_id),),
                query_hint=check.query,
            )
        )
    evidence = tuple(
        str(item)
        for item in (
            next(iter(check.record_arguments.values()), None),
            check.expected_article_id,
        )
        if item is not None
    )
    return SupportTask(
        task_id=f"check-{check.workflow_kind}",
        workflow_kind=check.workflow_kind,  # type: ignore[arg-type]
        user_request="Run the preregistered routine support check.",
        requirements=tuple(requirements),
        completion_evidence=evidence,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs" / "agent_v2_5_support.json",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    definition = load_agent_v2_definition(args.config, workspace_root=ROOT)
    checks: list[dict[str, object]] = []
    for check in CHECKS:
        task = _task(check)
        runtime = AgentV2Runtime(task=task, environment=definition.environment)
        decisions = []
        for requirement in task.requirements:
            arguments = (
                {"query": requirement.query_hint, "top_k": 3}
                if requirement.action == "search_support_kb"
                else dict(requirement.arguments)
            )
            decisions.append(
                runtime.handle_action(
                    action_name=requirement.action,
                    arguments=arguments,
                )
            )
        assessment = runtime.assess_message(check.response)
        checks.append(
            {
                "task_id": task.task_id,
                "workflow_kind": task.workflow_kind,
                "action_classifications": [item.classification for item in decisions],
                "requirements_satisfied": [item.requirement_satisfied for item in decisions],
                "message_classification": assessment.classification,
                "final_state": runtime.state,
            }
        )

    report = {
        "agent_id": definition.agent_id,
        "definition_version": definition.definition_version,
        "assistant_protocol": definition.assistant_protocol,
        "tools": list(definition.tool_names),
        "record_counts": {
            "orders": len(definition.environment.orders),
            "returns": len(definition.environment.returns),
            "support_cases": len(definition.environment.support_cases),
            "warranties": len(definition.environment.warranties),
            "subscriptions": len(definition.environment.subscriptions),
        },
        "knowledge_base_version": definition.environment.knowledge_base.kb_version,
        "knowledge_article_count": len(definition.environment.knowledge_base.articles),
        "checks": checks,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if all(item["final_state"] == "task_completed" for item in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
