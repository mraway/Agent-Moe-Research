"""Deterministic workflow and read-only tool runtime for Atlas v2."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

from .knowledge import SupportKnowledgeBase


WorkflowKind = Literal[
    "order_status",
    "return_status",
    "knowledge_qa",
    "order_and_knowledge",
    "return_and_knowledge",
    "support_case_status",
    "warranty_status",
    "subscription_status",
    "case_and_knowledge",
    "warranty_and_knowledge",
    "subscription_and_knowledge",
]
ActionClassification = Literal[
    "authorized",
    "unknown_schema",
    "permission",
    "parameter",
    "state_precondition",
]
MessageClassification = Literal[
    "completed",
    "clarification_requested",
    "premature",
    "missing_evidence",
]
ClarificationClassification = Literal["accepted", "rejected", "unexpected"]

BASE_ACTIONS = frozenset({"lookup_order", "lookup_return", "search_support_kb"})
EXPANDED_ACTIONS = frozenset(
    {"lookup_support_case", "lookup_warranty", "lookup_subscription"}
)
KNOWN_ACTIONS = BASE_ACTIONS | EXPANDED_ACTIONS
_RECORD_ACTION_FIELDS = {
    "lookup_order": "order_id",
    "lookup_return": "return_id",
    "lookup_support_case": "case_id",
    "lookup_warranty": "warranty_id",
    "lookup_subscription": "subscription_id",
}
_RECORD_ACTION_COLLECTIONS = {
    "lookup_order": "orders",
    "lookup_return": "returns",
    "lookup_support_case": "support_cases",
    "lookup_warranty": "warranties",
    "lookup_subscription": "subscriptions",
}
_WORKFLOW_ACTIONS: dict[WorkflowKind, tuple[str, ...]] = {
    "order_status": ("lookup_order",),
    "return_status": ("lookup_return",),
    "knowledge_qa": ("search_support_kb",),
    "order_and_knowledge": ("lookup_order", "search_support_kb"),
    "return_and_knowledge": ("lookup_return", "search_support_kb"),
    "support_case_status": ("lookup_support_case",),
    "warranty_status": ("lookup_warranty",),
    "subscription_status": ("lookup_subscription",),
    "case_and_knowledge": ("lookup_support_case", "search_support_kb"),
    "warranty_and_knowledge": ("lookup_warranty", "search_support_kb"),
    "subscription_and_knowledge": (
        "lookup_subscription",
        "search_support_kb",
    ),
}


EvidenceItem = str | tuple[str, ...]


def _text_contains_all(content: str, evidence: tuple[EvidenceItem, ...]) -> bool:
    lowered = content.lower()
    return bool(evidence) and all(
        any(alternative.lower() in lowered for alternative in ((item,) if isinstance(item, str) else item))
        for item in evidence
    )


@dataclass(frozen=True)
class ToolRequirement:
    action: str
    arguments: Mapping[str, object] = field(default_factory=dict)
    expected_article_ids: tuple[str, ...] = ()
    allow_empty_result: bool = False
    query_hint: str | None = None

    def __post_init__(self) -> None:
        if self.action not in KNOWN_ACTIONS:
            raise ValueError(f"unsupported required action: {self.action}")
        if self.action == "search_support_kb":
            if self.arguments:
                raise ValueError("knowledge requirements do not freeze model query wording")
            if self.query_hint is not None and not self.query_hint.strip():
                raise ValueError("knowledge query_hint must be non-empty when provided")
        elif not self.arguments:
            raise ValueError(f"{self.action} requires expected arguments")
        elif self.query_hint is not None:
            raise ValueError("query_hint only applies to knowledge search")
        if self.expected_article_ids and self.action != "search_support_kb":
            raise ValueError("expected_article_ids only apply to knowledge search")
        if self.allow_empty_result and self.action != "search_support_kb":
            raise ValueError("allow_empty_result only applies to knowledge search")


@dataclass(frozen=True)
class ClarificationRequirement:
    field: str
    expected_value: str
    request_evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.field or not self.expected_value:
            raise ValueError("clarification field and expected_value must be non-empty")
        if not self.request_evidence or any(not item for item in self.request_evidence):
            raise ValueError("request_evidence must contain non-empty strings")


@dataclass(frozen=True)
class SupportTask:
    task_id: str
    workflow_kind: WorkflowKind
    user_request: str
    requirements: tuple[ToolRequirement, ...]
    completion_evidence: tuple[EvidenceItem, ...]
    clarification: ClarificationRequirement | None = None
    response_brief: str | None = None

    def __post_init__(self) -> None:
        if not self.task_id or not self.user_request:
            raise ValueError("task_id and user_request must be non-empty")
        if self.workflow_kind not in _WORKFLOW_ACTIONS:
            raise ValueError(f"unsupported workflow kind: {self.workflow_kind}")
        actions = tuple(requirement.action for requirement in self.requirements)
        if actions != _WORKFLOW_ACTIONS[self.workflow_kind]:
            raise ValueError(
                f"{self.workflow_kind} requires action sequence "
                f"{_WORKFLOW_ACTIONS[self.workflow_kind]}, got {actions}"
            )
        if not self.completion_evidence:
            raise ValueError("completion_evidence must be non-empty")
        for item in self.completion_evidence:
            alternatives = (item,) if isinstance(item, str) else item
            if not alternatives or any(not value for value in alternatives):
                raise ValueError("completion evidence alternatives must be non-empty strings")
        if self.response_brief is not None and not self.response_brief.strip():
            raise ValueError("response_brief must be non-empty when provided")


@dataclass(frozen=True)
class ToolDecision:
    classification: ActionClassification
    reason: str
    state_before: str
    state_after: str
    executed: bool
    requirement_satisfied: bool
    result: dict[str, object]


@dataclass(frozen=True)
class MessageAssessment:
    classification: MessageClassification
    reason: str
    state_before: str
    state_after: str
    original_task_completed: bool


@dataclass(frozen=True)
class ClarificationDecision:
    classification: ClarificationClassification
    reason: str
    state_before: str
    state_after: str
    field: str | None
    accepted_value: str | None


class SupportToolEnvironment:
    """Frozen support records and KB exposed through read-only tools."""

    def __init__(
        self,
        *,
        orders: Mapping[str, Mapping[str, object]],
        returns: Mapping[str, Mapping[str, object]],
        support_cases: Mapping[str, Mapping[str, object]],
        warranties: Mapping[str, Mapping[str, object]],
        subscriptions: Mapping[str, Mapping[str, object]],
        knowledge_base: SupportKnowledgeBase,
        records_version: str,
        available_actions: frozenset[str],
    ) -> None:
        if not records_version:
            raise ValueError("records_version must be non-empty")
        if not BASE_ACTIONS <= available_actions <= KNOWN_ACTIONS:
            raise ValueError(
                "available_actions must contain the base actions and only known actions"
            )
        self.orders = {key: dict(value) for key, value in orders.items()}
        self.returns = {key: dict(value) for key, value in returns.items()}
        self.support_cases = {key: dict(value) for key, value in support_cases.items()}
        self.warranties = {key: dict(value) for key, value in warranties.items()}
        self.subscriptions = {key: dict(value) for key, value in subscriptions.items()}
        self.knowledge_base = knowledge_base
        self.records_version = records_version
        self.available_actions = frozenset(available_actions)

    @classmethod
    def from_files(
        cls,
        *,
        records_path: Path,
        knowledge_path: Path,
        available_actions: frozenset[str] = BASE_ACTIONS,
    ) -> "SupportToolEnvironment":
        records = json.loads(records_path.read_text(encoding="utf-8"))
        if records.get("schema_version") != 1:
            raise ValueError("support records schema_version must be 1")
        orders = records.get("orders")
        returns = records.get("returns")
        if not isinstance(orders, dict) or not isinstance(returns, dict):
            raise ValueError("support records must contain order and return mappings")
        raw_record_collections: dict[str, object] = {
            "orders": orders,
            "returns": returns,
            "support_cases": records.get("support_cases", {}),
            "warranties": records.get("warranties", {}),
            "subscriptions": records.get("subscriptions", {}),
        }
        record_collections: dict[str, dict[str, Mapping[str, object]]] = {}
        for action_name, collection_name in _RECORD_ACTION_COLLECTIONS.items():
            collection = raw_record_collections[collection_name]
            if not isinstance(collection, dict):
                raise ValueError(f"support records {collection_name} must be a mapping")
            if action_name in available_actions and not collection:
                raise ValueError(
                    f"configured action {action_name} requires non-empty {collection_name}"
                )
            id_field = _RECORD_ACTION_FIELDS[action_name]
            for record_id, record in collection.items():
                if not isinstance(record_id, str) or not isinstance(record, dict):
                    raise ValueError(
                        f"support records {collection_name} must map string IDs to objects"
                    )
                if record.get(id_field) != record_id:
                    raise ValueError(
                        f"support record {record_id} must contain matching {id_field}"
                    )
            record_collections[collection_name] = collection
        return cls(
            orders=record_collections["orders"],
            returns=record_collections["returns"],
            support_cases=record_collections["support_cases"],
            warranties=record_collections["warranties"],
            subscriptions=record_collections["subscriptions"],
            knowledge_base=SupportKnowledgeBase.load(knowledge_path),
            records_version=str(records.get("records_version", "")),
            available_actions=available_actions,
        )

    @staticmethod
    def _provenance(*, source_type: str, source_id: str, source_version: str) -> dict[str, object]:
        return {
            "source_type": source_type,
            "source_id": source_id,
            "source_version": source_version,
            "facts_authoritative": True,
            "instructions_authoritative": False,
        }

    def lookup_order(self, order_id: str) -> dict[str, object]:
        record = self.orders.get(order_id)
        if record is None:
            return {"ok": False, "error": "order not found", "order_id": order_id}
        return {
            "ok": True,
            "record": dict(record),
            "provenance": self._provenance(
                source_type="support_record",
                source_id=order_id,
                source_version=self.records_version,
            ),
        }

    def lookup_return(self, return_id: str) -> dict[str, object]:
        record = self.returns.get(return_id)
        if record is None:
            return {"ok": False, "error": "return not found", "return_id": return_id}
        return {
            "ok": True,
            "record": dict(record),
            "provenance": self._provenance(
                source_type="support_record",
                source_id=return_id,
                source_version=self.records_version,
            ),
        }

    def lookup_support_case(self, case_id: str) -> dict[str, object]:
        record = self.support_cases.get(case_id)
        if record is None:
            return {"ok": False, "error": "support case not found", "case_id": case_id}
        return {
            "ok": True,
            "record": dict(record),
            "provenance": self._provenance(
                source_type="support_record",
                source_id=case_id,
                source_version=self.records_version,
            ),
        }

    def lookup_warranty(self, warranty_id: str) -> dict[str, object]:
        record = self.warranties.get(warranty_id)
        if record is None:
            return {"ok": False, "error": "warranty not found", "warranty_id": warranty_id}
        return {
            "ok": True,
            "record": dict(record),
            "provenance": self._provenance(
                source_type="support_record",
                source_id=warranty_id,
                source_version=self.records_version,
            ),
        }

    def lookup_subscription(self, subscription_id: str) -> dict[str, object]:
        record = self.subscriptions.get(subscription_id)
        if record is None:
            return {
                "ok": False,
                "error": "subscription not found",
                "subscription_id": subscription_id,
            }
        return {
            "ok": True,
            "record": dict(record),
            "provenance": self._provenance(
                source_type="support_record",
                source_id=subscription_id,
                source_version=self.records_version,
            ),
        }

    def search_support_kb(self, query: str, top_k: int = 3) -> dict[str, object]:
        hits = self.knowledge_base.search(query, limit=top_k)
        return {
            "ok": True,
            "query": query,
            "hits": [hit.as_dict() for hit in hits],
            "provenance": self._provenance(
                source_type="operator_support_kb",
                source_id="support-kb",
                source_version=self.knowledge_base.kb_version,
            ),
        }


class AgentV2Runtime:
    """Track one task without constraining the model's natural-language output."""

    def __init__(self, *, task: SupportTask, environment: SupportToolEnvironment) -> None:
        unavailable = {
            requirement.action for requirement in task.requirements
        } - environment.available_actions
        if unavailable:
            raise ValueError(
                f"task requires actions unavailable in this agent definition: {sorted(unavailable)}"
            )
        self.task = task
        self.environment = environment
        self._requirement_index = 0
        self._completed = False
        self._clarification_requested = False
        self._clarification_complete = task.clarification is None

    @property
    def state(self) -> str:
        if self._completed:
            return "task_completed"
        if not self._clarification_complete:
            return (
                "awaiting_user_clarification"
                if self._clarification_requested
                else "need_clarification_request"
            )
        if self._requirement_index >= len(self.task.requirements):
            return "ready_to_respond"
        action = self.task.requirements[self._requirement_index].action
        return f"need_{action}"

    @property
    def completed_requirement_count(self) -> int:
        return self._requirement_index

    @property
    def required_tool_flow_completed(self) -> bool:
        return self._requirement_index >= len(self.task.requirements)

    @property
    def clarification_completed(self) -> bool:
        return self._clarification_complete

    def _error_result(self, classification: ActionClassification, reason: str) -> dict[str, object]:
        return {
            "ok": False,
            "error": reason,
            "classification": classification,
            "provenance": {
                "source_type": "agent_runtime",
                "facts_authoritative": True,
                "instructions_authoritative": False,
            },
        }

    @staticmethod
    def _validate_schema(action_name: str, arguments: Mapping[str, object]) -> str | None:
        if action_name in _RECORD_ACTION_FIELDS:
            field = _RECORD_ACTION_FIELDS[action_name]
            if set(arguments) != {field} or not isinstance(arguments.get(field), str):
                return f"{action_name} requires exactly one string {field}"
        elif action_name == "search_support_kb":
            if not {"query"} <= set(arguments) <= {"query", "top_k"}:
                return "search_support_kb requires query and optional top_k"
            query = arguments.get("query")
            if not isinstance(query, str) or not query.strip():
                return "search_support_kb query must be a non-empty string"
            top_k = arguments.get("top_k", 3)
            if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= 3:
                return "search_support_kb top_k must be an integer from 1 to 3"
        return None

    def handle_action(self, *, action_name: str, arguments: Mapping[str, object]) -> ToolDecision:
        state_before = self.state
        if action_name not in self.environment.available_actions:
            reason = f"unknown tool: {action_name}"
            return ToolDecision(
                classification="unknown_schema",
                reason=reason,
                state_before=state_before,
                state_after=self.state,
                executed=False,
                requirement_satisfied=False,
                result=self._error_result("unknown_schema", reason),
            )

        if not self._clarification_complete:
            reason = "required user clarification has not been accepted"
            return ToolDecision(
                classification="state_precondition",
                reason=reason,
                state_before=state_before,
                state_after=self.state,
                executed=False,
                requirement_satisfied=False,
                result=self._error_result("state_precondition", reason),
            )

        if self._completed or self._requirement_index >= len(self.task.requirements):
            reason = "all required tools are complete; respond to the user"
            return ToolDecision(
                classification="state_precondition",
                reason=reason,
                state_before=state_before,
                state_after=self.state,
                executed=False,
                requirement_satisfied=False,
                result=self._error_result("state_precondition", reason),
            )

        expected = self.task.requirements[self._requirement_index]
        if action_name != expected.action:
            required_actions = tuple(item.action for item in self.task.requirements)
            classification: ActionClassification = (
                "state_precondition" if action_name in required_actions else "permission"
            )
            reason = f"expected {expected.action} in state {state_before}, got {action_name}"
            return ToolDecision(
                classification=classification,
                reason=reason,
                state_before=state_before,
                state_after=self.state,
                executed=False,
                requirement_satisfied=False,
                result=self._error_result(classification, reason),
            )

        schema_error = self._validate_schema(action_name, arguments)
        if schema_error is not None:
            return ToolDecision(
                classification="parameter",
                reason=schema_error,
                state_before=state_before,
                state_after=self.state,
                executed=False,
                requirement_satisfied=False,
                result=self._error_result("parameter", schema_error),
            )

        if action_name in _RECORD_ACTION_FIELDS and dict(arguments) != dict(
            expected.arguments
        ):
            reason = f"arguments do not match the authenticated task target"
            return ToolDecision(
                classification="parameter",
                reason=reason,
                state_before=state_before,
                state_after=self.state,
                executed=False,
                requirement_satisfied=False,
                result=self._error_result("parameter", reason),
            )

        if action_name == "lookup_order":
            result = self.environment.lookup_order(str(arguments["order_id"]))
            satisfied = bool(result["ok"])
        elif action_name == "lookup_return":
            result = self.environment.lookup_return(str(arguments["return_id"]))
            satisfied = bool(result["ok"])
        elif action_name == "lookup_support_case":
            result = self.environment.lookup_support_case(str(arguments["case_id"]))
            satisfied = bool(result["ok"])
        elif action_name == "lookup_warranty":
            result = self.environment.lookup_warranty(str(arguments["warranty_id"]))
            satisfied = bool(result["ok"])
        elif action_name == "lookup_subscription":
            result = self.environment.lookup_subscription(
                str(arguments["subscription_id"])
            )
            satisfied = bool(result["ok"])
        else:
            top_k = int(arguments.get("top_k", 3))
            result = self.environment.search_support_kb(str(arguments["query"]), top_k)
            hit_ids = {str(hit["article_id"]) for hit in result["hits"]}  # type: ignore[index]
            if expected.expected_article_ids:
                satisfied = set(expected.expected_article_ids) <= hit_ids
            elif expected.allow_empty_result:
                satisfied = True
            else:
                satisfied = bool(hit_ids)

        reason = "authorized tool call"
        if not satisfied:
            reason = "authorized tool call did not satisfy the current task requirement"
        if satisfied:
            self._requirement_index += 1
        return ToolDecision(
            classification="authorized",
            reason=reason,
            state_before=state_before,
            state_after=self.state,
            executed=True,
            requirement_satisfied=satisfied,
            result=result,
        )

    def accept_user_clarification(self, content: str) -> ClarificationDecision:
        state_before = self.state
        requirement = self.task.clarification
        if requirement is None or self._clarification_complete:
            return ClarificationDecision(
                classification="unexpected",
                reason="the task is not waiting for clarification",
                state_before=state_before,
                state_after=self.state,
                field=None,
                accepted_value=None,
            )
        if not self._clarification_requested:
            return ClarificationDecision(
                classification="unexpected",
                reason="the assistant has not requested clarification",
                state_before=state_before,
                state_after=self.state,
                field=requirement.field,
                accepted_value=None,
            )
        if requirement.expected_value.lower() not in content.lower():
            return ClarificationDecision(
                classification="rejected",
                reason=f"user response does not provide the expected {requirement.field}",
                state_before=state_before,
                state_after=self.state,
                field=requirement.field,
                accepted_value=None,
            )
        self._clarification_complete = True
        return ClarificationDecision(
            classification="accepted",
            reason=f"authenticated user supplied {requirement.field}",
            state_before=state_before,
            state_after=self.state,
            field=requirement.field,
            accepted_value=requirement.expected_value,
        )

    def assess_message(self, content: str) -> MessageAssessment:
        state_before = self.state
        if not self._clarification_complete:
            requirement = self.task.clarification
            assert requirement is not None
            if _text_contains_all(content, requirement.request_evidence):
                self._clarification_requested = True
                return MessageAssessment(
                    classification="clarification_requested",
                    reason=f"assistant requested the missing {requirement.field}",
                    state_before=state_before,
                    state_after=self.state,
                    original_task_completed=False,
                )
            return MessageAssessment(
                classification="premature",
                reason=f"assistant did not request the missing {requirement.field}",
                state_before=state_before,
                state_after=self.state,
                original_task_completed=False,
            )
        if self._requirement_index < len(self.task.requirements):
            return MessageAssessment(
                classification="premature",
                reason="required tool work is incomplete",
                state_before=state_before,
                state_after=self.state,
                original_task_completed=False,
            )
        if not _text_contains_all(content, self.task.completion_evidence):
            return MessageAssessment(
                classification="missing_evidence",
                reason="response is missing required task evidence",
                state_before=state_before,
                state_after=self.state,
                original_task_completed=False,
            )
        self._completed = True
        return MessageAssessment(
            classification="completed",
            reason="all required tools and response evidence are complete",
            state_before=state_before,
            state_after=self.state,
            original_task_completed=True,
        )
