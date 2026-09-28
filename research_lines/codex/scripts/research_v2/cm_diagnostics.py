#!/usr/bin/env python3
"""CM diagnostics that the harness does not produce (spec 4.4, report tables T-G/T-H/T-J).

* T-G: the C2 ridge held-out routine R^2 curves (targets ``ind`` and the C1 residual)
  and the C3 ridge curve, per S1 fitting side, plus C1 coverage statistics.
* T-H: energy decomposition of ||z||^2 into covered-token / uncovered-token / routine
  baseline contributions, for drift post-onset windows and for the actual false-alarm
  windows of benign / resist traces.
* T-J: how much C3 conditions away -- the per-trace log scale exp(g(p_i)) by arm.
* Failure cases: missed drift traces and highest-scoring non-drift traces with text.

Everything is fitted on routine traces of the *fitting* batch only; drift traces are
used for evaluation and reporting only.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io as rio  # noqa: E402
from research_v2.features import window_means  # noqa: E402
from research_v2.harness import routine_traces  # noqa: E402
from research_v2.scorers.cm import ConditionalManifoldScorer, indicator  # noqa: E402

OUTPUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "cm" / "cm_diagnostics"
PRIMARY_RESULT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "cm" / "cm_c1_middle" / "result.json"
DIRECTIONS = (("b1", "b2", "b1_to_b2"), ("b2", "b1", "b2_to_b1"))
CONFIGS = {
    "c1_middle": {"conditioning": "c1", "layers": "middle"},
    "c1_all": {"conditioning": "c1", "layers": "all"},
    "c1c2_middle": {"conditioning": "c1c2", "layers": "middle"},
    "c1c2_all": {"conditioning": "c1c2", "layers": "all"},
    "c1c2_early": {"conditioning": "c1c2", "layers": "early"},
    "c1c2_late": {"conditioning": "c1c2", "layers": "late"},
    "c1_middle_c3": {"conditioning": "c1", "layers": "middle", "use_c3": True},
}


def _quantiles(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"n": 0, "median": None, "q1": None, "q3": None, "mean": None}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "median": float(statistics.median(ordered)),
        "q1": float(ordered[len(ordered) // 4]),
        "q3": float(ordered[(3 * len(ordered)) // 4]),
        "mean": float(statistics.fmean(ordered)),
    }


def arm_class(trace: Any) -> str:
    if trace.positive:
        return "drift"
    if trace.arm == "attack":
        return "resist"
    if trace.arm == "clean":
        return "clean"
    return "benign"


# ---------------------------------------------------------------------------
# T-G: fit diagnostics
# ---------------------------------------------------------------------------


def fit_diagnostics(batches: dict[str, tuple[Any, ...]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for fit_batch, _, direction in DIRECTIONS:
        pool = routine_traces(batches[fit_batch], "cb")
        for name, config in CONFIGS.items():
            scorer = ConditionalManifoldScorer(window_width=8, **config)
            state = scorer.fit(pool)
            out.setdefault(direction, {})[name] = state.diagnostics
    return out


# ---------------------------------------------------------------------------
# T-H: energy decomposition
# ---------------------------------------------------------------------------


def _decompose(scorer: ConditionalManifoldScorer, state: Any, trace: Any) -> dict[str, torch.Tensor]:
    """||z||^2 = <z, covered/sd> + <z, uncovered/sd> + <z, -window_mean/sd> (exact)."""

    ind = indicator(trace.top_k_ids, scorer.layers)
    rows = state.token_row[trace.token_ids.long()]
    residual = ind - state.table[rows]
    covered = state.covered_row[rows].unsqueeze(1).float()
    ends, mean_all = window_means(residual, scorer.window_width)
    _, mean_cov = window_means(residual * covered, scorer.window_width)
    _, mean_unc = window_means(residual * (1.0 - covered), scorer.window_width)
    _, coverage = window_means(covered, scorer.window_width)
    z = (mean_all - state.window_mean) / state.window_sd
    energy = (z**2).sum(1)
    share_cov = (z * (mean_cov / state.window_sd)).sum(1)
    share_unc = (z * (mean_unc / state.window_sd)).sum(1)
    share_base = (z * (-state.window_mean / state.window_sd)).sum(1)
    return {
        "ends": ends,
        "energy": energy,
        "covered": share_cov / energy.clamp(min=1e-12),
        "uncovered": share_unc / energy.clamp(min=1e-12),
        "baseline": share_base / energy.clamp(min=1e-12),
        "coverage_rate": coverage[:, 0],
    }


def energy_decomposition(
    batches: dict[str, tuple[Any, ...]], alarms: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for fit_batch, target_batch, direction in DIRECTIONS:
        scorer = ConditionalManifoldScorer(window_width=8, conditioning="c1", layers="middle")
        state = scorer.fit(routine_traces(batches[fit_batch], "cb"))
        buckets: dict[str, dict[str, list[float]]] = {}
        per_trace: list[dict[str, Any]] = []
        alarm_map = alarms.get(direction, {})
        for trace in batches[target_batch]:
            parts = _decompose(scorer, state, trace)
            if not parts["ends"].numel():
                continue
            klass = arm_class(trace)
            if klass in ("clean", "benign"):
                # reference distribution: every window of the target-side routine traffic
                everything = torch.ones_like(parts["ends"], dtype=torch.bool)
                for key in ("covered", "uncovered", "baseline", "coverage_rate"):
                    buckets.setdefault("routine_all_windows", {}).setdefault(key, []).extend(
                        parts[key][everything].tolist()
                    )
            if klass == "drift":
                onset = int(trace.evidence_onset)
                mask = parts["ends"] >= onset
                label = "drift_post_onset"
                pre = parts["ends"] < onset
                if bool(pre.any()):
                    for key in ("covered", "uncovered", "baseline", "coverage_rate"):
                        buckets.setdefault("drift_pre_onset", {}).setdefault(key, []).extend(
                            parts[key][pre].tolist()
                        )
            else:
                first = alarm_map.get(trace.trace_id)
                if first is None:
                    continue
                mask = parts["ends"] == int(first)
                label = f"{klass}_false_alarm"
            if not bool(mask.any()):
                continue
            for key in ("covered", "uncovered", "baseline", "coverage_rate"):
                buckets.setdefault(label, {}).setdefault(key, []).extend(
                    parts[key][mask].tolist()
                )
            per_trace.append(
                {
                    "trace_id": trace.trace_id,
                    "class": klass,
                    "label": label,
                    "window_count": int(mask.sum()),
                    "covered_share_median": float(parts["covered"][mask].median()),
                    "uncovered_share_median": float(parts["uncovered"][mask].median()),
                    "baseline_share_median": float(parts["baseline"][mask].median()),
                    "coverage_rate_median": float(parts["coverage_rate"][mask].median()),
                    "energy_median": float(parts["energy"][mask].median()),
                }
            )
        out[direction] = {
            "pooled": {
                label: {key: _quantiles(values) for key, values in keys.items()}
                for label, keys in buckets.items()
            },
            "per_trace": per_trace,
        }
    return out


# ---------------------------------------------------------------------------
# T-J: what C3 conditions away
# ---------------------------------------------------------------------------


def c3_effect(batches: dict[str, tuple[Any, ...]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for fit_batch, target_batch, direction in DIRECTIONS:
        plain = ConditionalManifoldScorer(window_width=8, conditioning="c1", layers="middle")
        conditioned = ConditionalManifoldScorer(
            window_width=8, conditioning="c1", layers="middle", use_c3=True
        )
        pool = routine_traces(batches[fit_batch], "cb")
        state_plain = plain.fit(pool)
        state_c3 = conditioned.fit(pool)
        by_arm: dict[str, list[float]] = {}
        pre_onset: list[float] = []
        post_onset: list[float] = []
        for trace in batches[target_batch]:
            base, ends = plain.score(state_plain, trace)
            scaled, _ = conditioned.score(state_c3, trace)
            if not ends.numel():
                continue
            ratio = float(torch.log(scaled.mean() / base.mean()))  # = -g(p_i)
            by_arm.setdefault(arm_class(trace), []).append(ratio)
            if trace.positive:
                onset = int(trace.evidence_onset)
                pre = ends < onset
                post = ends >= onset
                if bool(pre.any()):
                    pre_onset.append(float(base[pre].mean() - scaled[pre].mean()))
                if bool(post.any()):
                    post_onset.append(float(base[post].mean() - scaled[post].mean()))
        out[direction] = {
            "log_scale_by_arm": {arm: _quantiles(values) for arm, values in by_arm.items()},
            "c3_diagnostics": state_c3.diagnostics.get("c3"),
            "drift_pre_onset_mean_energy_drop": _quantiles(pre_onset),
            "drift_post_onset_mean_energy_drop": _quantiles(post_onset),
        }
    return out


# ---------------------------------------------------------------------------
# failure cases
# ---------------------------------------------------------------------------


def _primary_candidates(
    result: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """The primary candidate per direction plus that case run's score streams."""

    picked: dict[str, dict[str, Any]] = {}
    streams: dict[str, dict[str, Any]] = {}
    for case_run in result["case_runs"]:
        if case_run["window_width"] != 8 or case_run["routine_definition"] != "cb":
            continue
        streams[case_run["case"]] = case_run.get("score_streams", {})
        for candidate in case_run["candidates"]:
            if (
                candidate["mode"] == "D"
                and abs(candidate["alpha"] - 0.10) < 1e-9
                and candidate["reading"] == "persist2"
            ):
                picked[candidate["case"]] = candidate
    return picked, streams


def failure_cases(
    batches: dict[str, tuple[Any, ...]], result: dict[str, Any], limit: int = 15
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    picked, streams_by_case = _primary_candidates(result)
    alarms: dict[str, dict[str, Any]] = {}
    out: dict[str, Any] = {}
    for _, target_batch, direction in DIRECTIONS:
        candidate = picked.get(direction)
        if candidate is None:
            continue
        streams = streams_by_case.get(direction, {})
        alarm_by_trace = {row[0]: row[2] for row in candidate["trace_alarms"]}
        alarms[direction] = {k: v for k, v in alarm_by_trace.items() if v is not None}
        lookup = {trace.trace_id: trace for trace in batches[target_batch]}
        misses = []
        for trace_id, first in alarm_by_trace.items():
            trace = lookup[trace_id]
            if not trace.positive:
                continue
            onset = int(trace.evidence_onset)
            if first is not None and int(first) >= onset:
                continue  # counted as a clean hit by the final-recall metric
            token_ids = trace.token_ids.tolist()
            misses.append(
                {
                    "trace_id": trace_id,
                    "domain": trace.scenario_domain,
                    "workflow": trace.workflow,
                    "channel": trace.channel,
                    "onset": onset,
                    "token_count": len(token_ids),
                    "miss_kind": "no_alarm" if first is None else "pre_onset_alarm",
                    "first_alarm_end": None if first is None else int(first),
                    "onset_text": rio.decode_text(token_ids[onset : onset + 24]),
                    "max_score": (
                        max(streams[trace_id]["scores"]) if trace_id in streams else None
                    ),
                }
            )
        top_negative = []
        for trace in batches[target_batch]:
            if trace.positive or trace.trace_id not in streams:
                continue
            scores = streams[trace.trace_id]["scores"]
            ends = streams[trace.trace_id]["ends"]
            if not scores:
                continue
            index = max(range(len(scores)), key=lambda i: scores[i])
            end = int(ends[index])
            token_ids = trace.token_ids.tolist()
            top_negative.append(
                {
                    "trace_id": trace.trace_id,
                    "arm": arm_class(trace),
                    "workflow": trace.workflow,
                    "max_score": scores[index],
                    "max_end": end,
                    "alarmed": alarm_by_trace.get(trace.trace_id) is not None,
                    "window_text": rio.decode_text(token_ids[max(0, end - 7) : end + 1]),
                }
            )
        top_negative.sort(key=lambda row: -row["max_score"])
        out[direction] = {
            "missed_drift": sorted(misses, key=lambda row: row["trace_id"])[: max(limit, 10)],
            "missed_drift_count": len(misses),
            "top_non_drift": top_negative[:limit],
        }
    return out, alarms


def main() -> None:
    torch.set_num_threads(8)
    start = time.time()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    batches = rio.load_core()
    result = json.loads(PRIMARY_RESULT.read_text(encoding="utf-8"))
    cases, alarms = failure_cases(batches, result)
    payload = {
        "generated_from": str(PRIMARY_RESULT),
        "code_commit": result.get("code_commit"),
        "datasets": rio.dataset_hashes(),
        "fit_diagnostics": fit_diagnostics(batches),
        "energy_decomposition": energy_decomposition(batches, alarms),
        "c3_effect": c3_effect(batches),
        "failure_cases": cases,
    }
    payload["wall_clock_seconds"] = round(time.time() - start, 2)
    path = OUTPUT / "diagnostics.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wrote {path} in {payload['wall_clock_seconds']}s")


if __name__ == "__main__":
    main()
