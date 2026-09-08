from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "build_agent_v2_onset_consensus",
    ROOT / "scripts" / "build_agent_v2_onset_consensus.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _event(start: int, end: int, evidence: str = "x") -> dict:
    return {
        "evidence": evidence,
        "rationale": "source rationale",
        "confidence": "high",
        "span": {"char_start": 0, "char_end": len(evidence), "token_start": start, "token_end": end},
    }


def test_automatic_exact_start_preserves_confirmation_end_interval() -> None:
    value, resolution, note = MODULE._automatic_event(_event(4, 6), _event(4, 9))
    assert value["onset_token_interval"] == [4, 4]
    assert value["confirmation_end_token_interval"] == [6, 9]
    assert value["selected_evidence"] is None
    assert resolution == "exact_start_agreement"
    assert note is None


def test_source_absence_is_explicitly_resolved() -> None:
    packet = {"final_output": "answer", "output_tokens": [{"text": "answer"}]}
    value, resolution, note = MODULE._adjudicated_event(
        packet,
        _event(0, 0, "answer"),
        None,
        {"selection": "b", "rationale": "No substantive execution."},
    )
    assert value is None
    assert resolution == "reviewer_b_absence"
    assert "No substantive execution" in note


def test_third_evidence_is_token_aligned_not_manually_indexed() -> None:
    packet = {
        "final_output": "alpha beta gamma",
        "output_tokens": [{"text": "alpha"}, {"text": " beta"}, {"text": " gamma"}],
    }
    value, resolution, note = MODULE._adjudicated_event(
        packet,
        _event(0, 0, "alpha"),
        _event(2, 2, " gamma"),
        {"selection": "third", "evidence": "beta", "rationale": "Middle token is semantic onset."},
    )
    assert value["onset_token_interval"] == [1, 1]
    assert value["confirmation_end_token_interval"] == [1, 1]
    assert resolution == "third_evidence"
    assert note is None


def test_nonautomatic_event_requires_a_decision() -> None:
    with pytest.raises(ValueError, match="non-automatic"):
        MODULE._automatic_event(_event(1, 1), _event(2, 2))


def test_class_map_rejects_execution_without_commitment() -> None:
    assert (True, False, True) not in MODULE.CLASSES
