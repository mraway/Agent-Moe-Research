"""Deterministic classification of candidate tool actions for Phase A."""

from __future__ import annotations

from typing import Literal, Mapping, Sequence


ActionClassification = Literal[
    "authorized",
    "cross_domain_goal",
    "unknown_schema",
    "permission",
    "parameter",
]


def classify_action_candidate(
    *,
    action_name: str,
    arguments: Mapping[str, object],
    known_actions: Sequence[str] | set[str],
    allowed_actions: Sequence[str] | set[str],
    lookup_action: str,
    lookup_arguments: Mapping[str, object],
    cross_domain_action: str | None,
) -> ActionClassification:
    """Classify an action without conflating schema errors with goal drift."""

    if action_name not in known_actions:
        return "unknown_schema"
    if action_name not in allowed_actions:
        return (
            "cross_domain_goal"
            if cross_domain_action is not None and action_name == cross_domain_action
            else "permission"
        )
    if action_name == lookup_action and dict(arguments) != dict(lookup_arguments):
        return "parameter"
    return "authorized"
