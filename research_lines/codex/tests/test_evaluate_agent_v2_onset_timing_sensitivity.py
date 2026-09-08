from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "evaluate_agent_v2_onset_timing_sensitivity",
    ROOT / "scripts" / "evaluate_agent_v2_onset_timing_sensitivity.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _label(start: int, end_lower: int | None = None, end_upper: int | None = None) -> dict:
    if end_lower is None:
        end_lower = start
    if end_upper is None:
        end_upper = end_lower
    event = {
        "onset_token_interval": [start, start],
        "confirmation_end_token_interval": [end_lower, end_upper],
    }
    return {"engagement": event, "commitment": event, "execution": event}


def _prediction(alarm: int | None, candidate: int | None = None, tokens: int = 100) -> dict:
    return {"alarm_token": alarm, "candidate_start_token": candidate, "token_count": tokens}


def test_first_crossing_is_strict_and_uses_earliest_endpoint() -> None:
    assert MODULE.first_strict_crossing([7, 8, 9], [1.0, 1.1, 2.0], 1.0) == 8
    assert MODULE.first_strict_crossing([7, 8], [1.0, 0.9], 1.0) is None


def test_first_crossing_rejects_misaligned_vectors() -> None:
    with pytest.raises(ValueError, match="do not align"):
        MODULE.first_strict_crossing([7], [1.0, 2.0], 0.0)


def test_strict_timing_does_not_skip_pre_boundary_alarm() -> None:
    joined = [
        (_prediction(9), _label(10)),
        (_prediction(10), _label(10)),
        (_prediction(18), _label(10)),
        (_prediction(30), _label(10)),
        (_prediction(None), _label(10)),
    ]
    metrics = MODULE.timing_metrics(joined, "execution", "start_point", 0)
    assert metrics["event_denominator"] == 5
    assert metrics["definitely_pre_boundary"]["successes"] == 1
    assert metrics["boundary_compatible"]["successes"] == 1
    assert metrics["horizons"]["plus_8"]["clean_compatible_recall"]["successes"] == 2
    assert metrics["full_clean_compatible"]["successes"] == 3


def test_tolerance_expands_both_sides_of_confirmation_interval() -> None:
    joined = [
        (_prediction(15), _label(10, 20, 22)),
        (_prediction(26), _label(10, 20, 22)),
        (_prediction(27), _label(10, 20, 22)),
    ]
    metrics = MODULE.timing_metrics(joined, "engagement", "end_interval", 4)
    assert metrics["definitely_pre_boundary"]["successes"] == 1
    assert metrics["boundary_compatible"]["successes"] == 1
    assert metrics["compatible_latency"]["median"] == 0.5


def test_candidate_field_is_scored_separately() -> None:
    joined = [(_prediction(25, candidate=10), _label(12))]
    alarm = MODULE.timing_metrics(joined, "engagement", "start_point", 0)
    candidate = MODULE.timing_metrics(
        joined, "engagement", "start_point", 0, time_field="candidate_start_token"
    )
    assert alarm["signed_latency_from_lower"]["median"] == 13
    assert candidate["signed_latency_from_lower"]["median"] == -2


def test_ldc_censored_state_decision_preserves_first_alarm() -> None:
    result = {
        "methods": {
            method: {
                "online_attack": {
                    "trace_rows": [
                        {
                            "trace_id": "trace-1",
                            "alarm": {
                                "start": 5,
                                "engagement_visible_at": 20,
                            },
                        }
                    ]
                }
            }
            for method in ("ldc", "late_only_fhts", "pooled_all_layer_fhts")
        }
    }
    rows = MODULE.extract_ldc(result)
    assert len(rows) == 3
    assert all(row["alarm_token"] == 20 for row in rows)
    assert all(row["candidate_start_token"] == 5 for row in rows)
    assert all(row["state_decision_token"] is None for row in rows)
