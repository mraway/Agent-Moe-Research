"""Shared definitions for the FCM evaluation (docs/research_v2/fcm_prereg.md).

Holds the preregistered candidate grid, the two time anchors (evidence_onset and
product_onset) and a mode-D replay that rebuilds the harness decision from a stored
score stream, so that per-trace margins and the second anchor can be produced without
re-running any scorer.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Any, Sequence

import torch

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from research_v2 import io as rio  # noqa: E402
from research_v2.harness import (  # noqa: E402
    HarnessConfig,
    SplitCase,
    aggregate_rows,
    alarm_row,
    build_split_cases,
    conformal_threshold,
    fit_bucket_stats,
    routine_traces,
    scenario_halves,
)
from research_v2.readings import build_readings  # noqa: E402

ART = ROOT / "artifacts" / "agent_v2" / "research_v2"
OUT = ART / "fcm"
LABELS = ROOT / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"
MISSES = ART / "zoom" / "missed_drift" / "misses.json"

FROZEN = {
    "A": ART / "wgm" / "c2_g1_middle_late" / "result.json",
    "B": ART / "pdm_d1_middle_s1" / "result.json",
}
FROZEN_WINDOW = {"A": 8, "B": 4}

# preregistered grid (fcm_prereg.md section 4); F4 is a combiner over F1 and F3
CANDIDATES: dict[str, dict[str, Any]] = {
    "F1": {
        "run": "f1_form_whitened_middle_late",
        "config": {"variant": "form_whitened", "layers": "middle_late", "form_source": "T"},
        "window": 8, "reference": "A", "text_derived": True,
        "label": "F1 form-conditional whitened distance (PRIMARY)",
    },
    "F1R": {
        "run": "f1r_form_whitened_routing_forms",
        "config": {"variant": "form_whitened", "layers": "middle_late", "form_source": "R", "form_k": 2},
        "window": 8, "reference": "A", "text_derived": False,
        "label": "F1R form-conditional whitened distance, routing forms (k-means k=2)",
    },
    "F1soft": {
        "run": "f1soft_form_whitened_min",
        "config": {"variant": "form_whitened", "layers": "middle_late", "form_source": "T", "score_mode": "soft"},
        "window": 8, "reference": "A", "text_derived": False,
        "label": "F1-soft min over forms",
    },
    "F2": {
        "run": "f2_form_whitened_all_layers",
        "config": {"variant": "form_whitened", "layers": "all", "form_source": "T"},
        "window": 8, "reference": "A", "text_derived": True,
        "label": "F2 form-conditional whitened distance, layers 0-15",
    },
    "F3": {
        "run": "f3_structured_runlength",
        "config": {"variant": "run_length"},
        "window": 8, "reference": "A", "text_derived": True,
        "label": "F3 structured-form run length",
    },
    "F5": {
        "run": "f5_unseen_expert",
        "config": {"variant": "unseen_expert", "layers": "middle_late"},
        "window": 8, "reference": "A", "text_derived": False,
        "label": "F5 unseen-expert rate (layers 5-15)",
    },
    "F6a": {
        "run": "f6a_unseen_transition_middle",
        "config": {"variant": "unseen_transition", "layers": "middle"},
        "window": 4, "reference": "B", "text_derived": False,
        "label": "F6a unseen depth-transition rate (layers 5-11)",
    },
    "F6b": {
        "run": "f6b_unseen_transition_all",
        "config": {"variant": "unseen_transition", "layers": "all"},
        "window": 4, "reference": "B", "text_derived": False,
        "label": "F6b unseen depth-transition rate (layers 0-15)",
    },
    "F7": {
        "run": "f7_probability_form_whitened",
        "config": {"variant": "form_whitened", "layers": "middle_late", "form_source": "T", "feature": "probability"},
        "window": 8, "reference": "A", "text_derived": True,
        "label": "F7 probability-feature form-conditional whitened distance",
    },
    "F8a": {
        "run": "f8a_filtered_wgm",
        "config": {"variant": "filtered_wgm", "layers": "middle_late"},
        "window": 8, "reference": "A", "text_derived": True,
        "label": "F8a CAND-A refitted on prose-only routine windows",
    },
    "F8b": {
        "run": "f8b_filtered_pdm",
        "config": {"variant": "filtered_pdm", "layers": "middle", "pdm_model": "d1"},
        "window": 4, "reference": "B", "text_derived": True,
        "label": "F8b CAND-B refitted on prose-only routine tokens",
    },
}
# post-hoc sensitivity (NOT part of the preregistered grid): the corrected whitespace
# rule, which labels pure newline / indent tokens structured (see the report's rule-defect
# section).
SENSITIVITY: dict[str, dict[str, Any]] = {
    "F1-ws": {
        "run": "sens_f1_strip_whitespace",
        "config": {"variant": "form_whitened", "layers": "middle_late", "form_source": "T", "strip_whitespace": True},
        "window": 8, "reference": "A", "text_derived": True,
        "label": "F1 with the corrected whitespace rule (sensitivity)",
    },
    "F3-ws": {
        "run": "sens_f3_strip_whitespace",
        "config": {"variant": "run_length", "strip_whitespace": True},
        "window": 8, "reference": "A", "text_derived": True,
        "label": "F3 with the corrected whitespace rule (sensitivity)",
    },
    "F8a-ws": {
        "run": "sens_f8a_strip_whitespace",
        "config": {"variant": "filtered_wgm", "layers": "middle_late", "strip_whitespace": True},
        "window": 8, "reference": "A", "text_derived": True,
        "label": "F8a with the corrected whitespace rule (sensitivity)",
    },
}

COMBINER = {
    "F4": {
        "parts": ("F1", "F3"), "alpha": 0.05, "reference": "A", "text_derived": True,
        "label": "F4 = F1 OR F3, alpha 0.05 each",
    }
}

ANCHORS = ("evidence", "product")


# ---------------------------------------------------------------------------
# anchors
# ---------------------------------------------------------------------------


def product_onsets() -> dict[str, int]:
    rows = [json.loads(line) for line in LABELS.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {row["trace_id"]: int(row["product_onset"]) for row in rows}


def anchored_traces(traces: Sequence[Any], onsets: dict[str, int]) -> tuple[Any, ...]:
    """Copy of ``traces`` whose drift records carry ``product_onset`` as evidence_onset."""

    out = []
    for trace in traces:
        if not trace.positive:
            out.append(trace)
            continue
        onset = onsets.get(trace.trace_id)
        if onset is None:
            raise KeyError(f"no product_onset label for {trace.trace_id}")
        record = dataclasses.replace(trace.record, evidence_onset=int(onset))
        out.append(dataclasses.replace(trace, record=record))
    return tuple(out)


def anchored_case(case: SplitCase, onsets: dict[str, int]) -> SplitCase:
    return SplitCase(
        split=case.split,
        name=case.name,
        fit_traces=case.fit_traces,
        target_traces=anchored_traces(case.target_traces, onsets),
        source_traces=case.source_traces,
        detail=dict(case.detail),
    )


# ---------------------------------------------------------------------------
# stored streams
# ---------------------------------------------------------------------------


def load_result(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def streams_from_result(case_run: dict[str, Any]) -> dict[str, tuple[torch.Tensor, torch.Tensor]]:
    return {
        trace_id: (
            torch.tensor(block["scores"], dtype=torch.float64),
            torch.tensor(block["ends"], dtype=torch.long),
        )
        for trace_id, block in case_run["score_streams"].items()
    }


def case_runs(result: dict[str, Any], window: int, routine: str = "cb") -> dict[str, dict[str, Any]]:
    out = {}
    for case_run in result["case_runs"]:
        if case_run["split"] != "S1":
            continue
        if case_run["window_width"] != window or case_run["routine_definition"] != routine:
            continue
        out[case_run["case"]] = case_run
    return out


def cases_for(batches, split: str = "S1") -> dict[str, SplitCase]:
    return {case.name: case for case in build_split_cases(batches, split)}


# ---------------------------------------------------------------------------
# mode-D replay with per-trace detail
# ---------------------------------------------------------------------------


def replay_mode_d(
    case: SplitCase,
    streams: dict[str, tuple[torch.Tensor, torch.Tensor]],
    *,
    routine_def: str = "cb",
    reading_name: str = "persist2",
    alpha: float = 0.10,
    config: HarnessConfig | None = None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Rebuild the harness mode-D disjoint decision; returns (alarm rows, per-trace detail)."""

    config = config or HarnessConfig(scorer="x", scorer_config={})
    reading = {r.name: r for r in build_readings()}[reading_name]
    target_routine = [
        t for t in routine_traces(case.target_traces, routine_def) if streams[t.trace_id][1].numel()
    ]
    halves = scenario_halves(case.target_traces)
    rows: list[dict[str, Any]] = []
    detail: dict[str, dict[str, Any]] = {}
    for cal_half in (0, 1):
        calibration = [t for t in target_routine if halves[t.pair_group_id] == cal_half]
        cal_streams = [streams[t.trace_id] for t in calibration]
        stats = fit_bucket_stats(
            cal_streams,
            bucket_size=config.bucket_size,
            min_bucket_traces=config.min_bucket_traces,
            bucket_cap=config.bucket_cap,
            min_criterion=config.bucket_min_criterion,
        )
        maxima = [float(reading.apply(stats.standardize(s, e)).max()) for s, e in cal_streams]
        threshold = float(conformal_threshold(maxima, alpha)["threshold"])
        for trace in case.target_traces:
            if halves[trace.pair_group_id] == cal_half:
                continue
            scores, ends = streams[trace.trace_id]
            if not ends.numel():
                continue
            z = stats.standardize(scores, ends)
            statistic = reading.apply(z)
            rows.append(alarm_row(trace, ends, statistic, threshold, comparison=config.comparison))
            detail[trace.trace_id] = {
                "z": z,
                "statistic": statistic,
                "ends": ends,
                "threshold": threshold,
                "calibration_half": cal_half,
            }
    return rows, detail
