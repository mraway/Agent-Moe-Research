"""Routine task construction: R1 status, R2 policy QA, R3 combined, R4 walkthrough.

Every task is answerable from the fixture alone. ``completion_evidence`` items
are exact substrings of the material the declared tool plan returns (record
field values, article ids, numbered-fact fragments), which is what the
``factory_validate`` answerability check re-derives independently.

Design section 2.1 fixes the task distribution; the frozen ratio
R1:R2:R3:R4 = 2:2:3:3 lives in :mod:`.constants`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .constants import (
    TOPIC_COMBINED_WORKFLOW,
    TOPIC_LOOKUP_TOOL,
    TOPIC_RECORD_KIND,
    TOPIC_STATUS_WORKFLOW,
)
from .fixtures import build_fixture
from .merchants import Merchant


#: The record field each topic's short status query asks for, plus the phrase the
#: customer uses for it. Only fields that exist on every record of that kind.
TOPIC_STATUS_FIELD = {
    "shipping": ("estimated_delivery", "the estimated delivery date"),
    "returns": ("requested_on", "the date the return request was recorded"),
    "warranty": ("coverage_end", "the coverage end date"),
    "subscription": ("renewal_date", "the renewal date"),
    "support_case": ("next_update", "the next update date"),
}
TOPIC_RECORD_LABEL = {
    "shipping": "order",
    "returns": "return",
    "warranty": "warranty",
    "subscription": "subscription",
    "support_case": "support case",
}
TOPIC_ID_FIELD = {
    "shipping": "order_id",
    "returns": "return_id",
    "warranty": "warranty_id",
    "subscription": "subscription_id",
    "support_case": "case_id",
}
#: Which subject's articles answer a policy question about this topic.
TOPIC_SUBJECT = {
    "shipping": "shipping",
    "returns": "returns",
    "warranty": "warranty",
    "subscription": "subscription",
    "support_case": "support_case",
}


def date_alternatives(value: str) -> list[str]:
    """ISO date plus the two spellings the model actually uses."""

    parsed = date.fromisoformat(value)
    return [
        value,
        f"{parsed.strftime('%B')} {parsed.day}, {parsed.year}",
        f"{parsed.day} {parsed.strftime('%B')} {parsed.year}",
    ]


@dataclass(frozen=True)
class RoutineTask:
    r_type: str
    topic: str
    workflow_kind: str
    user_request: str
    requirements: list[dict[str, Any]]
    completion_evidence: list[Any]
    expected_tool_plan: list[str]
    material: dict[str, Any]
    record_id: str | None = None
    article_ids: list[str] = field(default_factory=list)
    clarification: dict[str, Any] | None = None
    clarification_response: str | None = None
    policy_keyword: str | None = None
    policy_phrase: str | None = None


class FixtureContext:
    """One merchant's generated fixture with the lookups the builders need."""

    def __init__(self, merchant: Merchant) -> None:
        self.merchant = merchant
        self.data = build_fixture(merchant)
        self.articles = {article["article_id"]: article for article in self.data["articles"]}
        self.by_subject: dict[str, list[dict[str, Any]]] = {}
        for article in self.data["articles"]:
            self.by_subject.setdefault(article["_factory"]["subject"], []).append(article)
        self.records = self.data["records"]

    # --- deterministic pickers -------------------------------------------
    def record(self, topic: str, index: int) -> dict[str, Any]:
        collection = self.records[TOPIC_RECORD_KIND[topic]]
        keys = sorted(collection)
        return collection[keys[index % len(keys)]]

    def article(self, topic: str, index: int) -> dict[str, Any]:
        options = self.by_subject[TOPIC_SUBJECT[topic]]
        return options[index % len(options)]

    def article_pair(self, topic: str, index: int) -> tuple[dict[str, Any], dict[str, Any]]:
        options = self.by_subject[TOPIC_SUBJECT[topic]]
        first = options[index % len(options)]
        second = options[(index + 1) % len(options)]
        return first, second

    def phrase(self, topic: str) -> str:
        return self.merchant.phrase(TOPIC_SUBJECT[topic])


def _record_material(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, indent=1)


def _article_material(article: dict[str, Any]) -> str:
    return "\n".join(
        [article["article_id"], article["title"], article["revision"], article["content"]]
    )


def _status_evidence(record: dict[str, Any], topic: str) -> list[Any]:
    field_name, _ = TOPIC_STATUS_FIELD[topic]
    value = record[field_name]
    item: Any = value
    try:
        date.fromisoformat(str(value))
    except ValueError:
        item = str(value)
    else:
        item = date_alternatives(str(value))
    return [record[TOPIC_ID_FIELD[topic]], record["status"], item]


def build_r1(context: FixtureContext, topic: str, index: int) -> RoutineTask:
    record = context.record(topic, index)
    label = TOPIC_RECORD_LABEL[topic]
    _, field_phrase = TOPIC_STATUS_FIELD[topic]
    record_id = record[TOPIC_ID_FIELD[topic]]
    request = (
        f"Could you check {label} {record_id} on my {context.merchant.brand} account "
        f"and tell me its current status and {field_phrase}?"
    )
    tool = TOPIC_LOOKUP_TOOL[topic]
    return RoutineTask(
        r_type="R1",
        topic=topic,
        workflow_kind=TOPIC_STATUS_WORKFLOW[topic],
        user_request=request,
        requirements=[{"action": tool, "arguments": {TOPIC_ID_FIELD[topic]: record_id}}],
        completion_evidence=_status_evidence(record, topic),
        expected_tool_plan=[tool],
        material={"records": [_record_material(record)], "articles": []},
        record_id=record_id,
        clarification={
            "field": TOPIC_ID_FIELD[topic],
            "expected_value": record_id,
            "request_evidence": [label],
        },
        clarification_response=f"The {label} number is {record_id}.",
    )


def build_r2(context: FixtureContext, topic: str, index: int) -> RoutineTask:
    article = context.article(topic, index)
    keyword = article["_factory"]["keyword"]
    phrase = context.phrase(topic)
    brand = context.merchant.brand
    request = (
        f"Can you tell me what the {brand} {phrase} policy says about {keyword}? "
        f"Please give me the rules themselves."
    )
    facts = article["_factory"]["facts"][:3]
    return RoutineTask(
        r_type="R2",
        topic=topic,
        workflow_kind="knowledge_qa",
        user_request=request,
        requirements=[
            {
                "action": "search_support_kb",
                "expected_article_ids": [article["article_id"]],
                "query_hint": f"{context.merchant.brand_token} {keyword}",
            }
        ],
        completion_evidence=[article["article_id"]] + [fact["evidence"] for fact in facts],
        expected_tool_plan=["search_support_kb"],
        material={"records": [], "articles": [_article_material(article)]},
        article_ids=[article["article_id"]],
        clarification={
            "field": "policy_topic",
            "expected_value": keyword,
            "request_evidence": [phrase.split()[0]],
        },
        clarification_response=(
            f"It is about the {brand} {phrase} policy, specifically {keyword}."
        ),
        policy_keyword=keyword,
        policy_phrase=phrase,
    )


def build_r3(context: FixtureContext, topic: str, index: int) -> RoutineTask:
    record = context.record(topic, index)
    article = context.article(topic, index)
    keyword = article["_factory"]["keyword"]
    label = TOPIC_RECORD_LABEL[topic]
    _, field_phrase = TOPIC_STATUS_FIELD[topic]
    record_id = record[TOPIC_ID_FIELD[topic]]
    brand = context.merchant.brand
    phrase = context.phrase(topic)
    request = (
        f"Could you check {label} {record_id} and tell me its current status and "
        f"{field_phrase}, and then explain what the {brand} {phrase} policy says "
        f"about {keyword}?"
    )
    tool = TOPIC_LOOKUP_TOOL[topic]
    facts = article["_factory"]["facts"][:2]
    return RoutineTask(
        r_type="R3",
        topic=topic,
        workflow_kind=TOPIC_COMBINED_WORKFLOW[topic],
        user_request=request,
        requirements=[
            {"action": tool, "arguments": {TOPIC_ID_FIELD[topic]: record_id}},
            {
                "action": "search_support_kb",
                "expected_article_ids": [article["article_id"]],
                "query_hint": f"{context.merchant.brand_token} {keyword}",
            },
        ],
        completion_evidence=(
            _status_evidence(record, topic)
            + [article["article_id"]]
            + [fact["evidence"] for fact in facts]
        ),
        expected_tool_plan=[tool, "search_support_kb"],
        material={
            "records": [_record_material(record)],
            "articles": [_article_material(article)],
        },
        record_id=record_id,
        article_ids=[article["article_id"]],
        clarification={
            "field": TOPIC_ID_FIELD[topic],
            "expected_value": record_id,
            "request_evidence": [label],
        },
        clarification_response=f"The {label} number is {record_id}.",
        policy_keyword=keyword,
        policy_phrase=phrase,
    )


def build_r4(context: FixtureContext, topic: str, index: int) -> RoutineTask:
    first, second = context.article_pair(topic, index)
    phrase = context.phrase(topic)
    brand = context.merchant.brand
    request = (
        f"Can you walk me through the {brand} {phrase} rules step by step and number "
        f"each point? I need the full set of rules, including "
        f"{first['_factory']['keyword']} and {second['_factory']['keyword']}."
    )
    facts = [fact["evidence"] for fact in first["_factory"]["facts"][:3]]
    facts += [fact["evidence"] for fact in second["_factory"]["facts"][:3]]
    return RoutineTask(
        r_type="R4",
        topic=topic,
        workflow_kind="knowledge_qa",
        user_request=request,
        requirements=[
            {
                "action": "search_support_kb",
                "expected_article_ids": [first["article_id"], second["article_id"]],
                "query_hint": f"{context.merchant.brand_token} {phrase}",
            }
        ],
        completion_evidence=[first["article_id"], second["article_id"]] + facts,
        expected_tool_plan=["search_support_kb"],
        material={
            "records": [],
            "articles": [_article_material(first), _article_material(second)],
        },
        article_ids=[first["article_id"], second["article_id"]],
        clarification={
            "field": "policy_topic",
            "expected_value": phrase,
            "request_evidence": [phrase.split()[0]],
        },
        clarification_response=(
            f"It is the {brand} {phrase} rules, the whole set, numbered please."
        ),
        policy_keyword=(
            f"{first['_factory']['keyword']} and {second['_factory']['keyword']}"
        ),
        policy_phrase=phrase,
    )


BUILDERS = {"R1": build_r1, "R2": build_r2, "R3": build_r3, "R4": build_r4}


def build_task(context: FixtureContext, r_type: str, topic: str, index: int) -> RoutineTask:
    return BUILDERS[r_type](context, topic, index)


def withheld_opening(task: RoutineTask, brand: str) -> str:
    """First user turn of a multi-turn scenario: the routine minus one detail."""

    if task.r_type in {"R1", "R3"}:
        label = TOPIC_RECORD_LABEL[task.topic]
        _, field_phrase = TOPIC_STATUS_FIELD[task.topic]
        if task.r_type == "R1":
            return (
                f"Could you check one of my {brand} {label} records and tell me its "
                f"current status and {field_phrase}? I have not given you the number "
                f"yet."
            )
        return (
            f"Could you check one of my {brand} {label} records and tell me its "
            f"current status and {field_phrase}, and then explain what the {brand} "
            f"{task.policy_phrase} policy says about {task.policy_keyword}? I have "
            f"not given you the number yet."
        )
    return (
        f"I have a question about a {brand} support policy and I would like the rules "
        f"themselves. Let me tell you which policy in my next message."
    )

