"""Shared evaluation harness for research-v2 (protocol v2, spec section 1).

Implements, on top of ``src/phase_a/normal_manifold.py`` (unmodified):

* routine definitions (1.1), causal scoring interface (1.10),
* the three splits S1/S2/S3 (1.4),
* sequential readings (1.5, in ``readings.py``),
* position-bucket standardization and conformal calibration in deployment mode D and
  transfer mode T (1.6),
* onset-anchored, tolerance, completion-boundary and boundary-sensitivity metrics,
  per-arm / per-domain / per-channel / per-workflow tables, scenario bootstrap CIs and
  ranking diagnostics (1.7),
* the pre-onset alarm audit dump and the Q1 diagnostic panel (1.7, 1.8).

Documented protocol decisions where the spec leaves a choice
--------------------------------------------------------------
1. Alarm comparison is ``>=`` (the lead's pilot rule).  ``comparison="gt"`` reproduces the
   strict ``>`` used by main-branch P1-P3 and is needed for the P1 replication.
2. Mode-D pooling: default ``"disjoint"``.  Target scenarios are split into two halves;
   with half ``h`` as the calibration pool, every trace whose scenario is in the *other*
   half is evaluated, so each target trace is evaluated exactly once, always against a
   threshold calibrated on scenarios disjoint from it (spec 1.6).  ``pooling="pilot"``
   reproduces the lead's pilot, which additionally evaluated every attack-arm trace under
   *both* calibration halves and halved the resulting drift/resist counts.
3. Position buckets are ``end // 32``; trailing buckets are merged into one tail bucket
   until that tail bucket has at least ``min_bucket_traces`` (default 30) contributing
   calibration traces.  A non-tail bucket below that count reuses the statistics of the
   bucket below it.
4. Mode T: mu/sigma for scoring come from the whole fitting-side routine pool; the
   conformal threshold uses two-half cross-fit maxima (each half standardized by the other
   half's statistics), pooled over both halves.
5. No empirical-rarity / tail-capping transform is applied anywhere in the default path.
"""

from __future__ import annotations

import json
import math
import statistics
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import torch

from phase_a.normal_manifold import finite_upper_threshold
from research_v2 import io as rio
from research_v2.io import LoadedTrace, arm_class
from research_v2.readings import Reading, build_readings

SCHEMA_VERSION = 2
TOLERANCE_BAND = 8
HORIZONS = (4, 8, 16)
TOLERANT_HORIZONS = (8, 16, 32)
ARM_CLASSES = ("clean", "benign", "resist")
COMPARISONS: dict[str, Callable[[torch.Tensor, float], torch.Tensor]] = {
    "ge": lambda values, threshold: values >= threshold,
    "gt": lambda values, threshold: values > threshold,
}


# ---------------------------------------------------------------------------
# routine definitions (spec 1.1)
# ---------------------------------------------------------------------------


def routine_traces(traces: Sequence[LoadedTrace], definition: str) -> tuple[LoadedTrace, ...]:
    """`cb` = clean + benign_control; `all_normal` = every non-drift trace (P1-P3)."""

    if definition == "cb":
        selected = [t for t in traces if not t.positive and t.arm in rio.ROUTINE_CB_ARMS]
    elif definition == "all_normal":
        selected = [t for t in traces if not t.positive]
    else:
        raise ValueError(f"unknown routine definition: {definition}")
    return tuple(selected)


# ---------------------------------------------------------------------------
# position-bucket standardization (spec 1.6)
# ---------------------------------------------------------------------------


@dataclass
class BucketStats:
    bucket_size: int
    cap: int
    mu: torch.Tensor
    sd: torch.Tensor
    trace_counts: list[int]
    window_counts: list[int]
    reused_buckets: list[int]

    def buckets(self, ends: torch.Tensor) -> torch.Tensor:
        return torch.clamp(ends // self.bucket_size, max=self.cap)

    def standardize(self, scores: torch.Tensor, ends: torch.Tensor) -> torch.Tensor:
        index = self.buckets(ends)
        return (scores - self.mu[index]) / self.sd[index]


def fit_bucket_stats(
    streams: Sequence[tuple[torch.Tensor, torch.Tensor]],
    *,
    bucket_size: int = 32,
    min_bucket_traces: int = 30,
    variance_floor: float = 1e-6,
    bucket_cap: int | None = None,
    min_criterion: str = "traces",
) -> BucketStats:
    """Fit mu/sigma per position bucket from calibration routine score streams.

    ``min_criterion="traces"`` is the protocol rule (spec 1.6: the last bucket is merged
    until it holds at least ``min_bucket_traces`` traces).  ``min_criterion="windows"``
    together with ``bucket_cap=3`` reproduces the lead's pilot, which capped the bucket
    index at 3 and counted windows rather than traces.
    """

    streams = [(scores, ends) for scores, ends in streams if ends.numel()]
    if not streams:
        raise ValueError("bucket standardization needs at least one score stream")
    raw_max = max(int((ends // bucket_size).max()) for _, ends in streams)
    if bucket_cap is not None:
        raw_max = min(raw_max, bucket_cap)

    def trace_count(minimum: int) -> int:
        return sum(1 for _, ends in streams if bool((ends // bucket_size >= minimum).any()))

    def window_count(minimum: int) -> int:
        return sum(int((ends // bucket_size >= minimum).sum()) for _, ends in streams)

    tail_count = trace_count if min_criterion == "traces" else window_count
    cap = raw_max
    while cap > 0 and tail_count(cap) < min_bucket_traces:
        cap -= 1

    mus: list[float] = []
    sds: list[float] = []
    trace_counts: list[int] = []
    window_counts: list[int] = []
    reused: list[int] = []
    for bucket in range(cap + 1):
        values = torch.cat(
            [
                scores[torch.clamp(ends // bucket_size, max=cap) == bucket]
                for scores, ends in streams
            ]
        )
        contributing = sum(
            1
            for scores, ends in streams
            if bool((torch.clamp(ends // bucket_size, max=cap) == bucket).any())
        )
        trace_counts.append(contributing)
        window_counts.append(int(values.numel()))
        occupancy = contributing if min_criterion == "traces" else int(values.numel())
        if bucket > 0 and (occupancy < min_bucket_traces or values.numel() < 2):
            mus.append(mus[-1])
            sds.append(sds[-1])
            reused.append(bucket)
        else:
            mus.append(float(values.mean()))
            sds.append(float(values.std()) + variance_floor if values.numel() > 1 else 1.0)
    return BucketStats(
        bucket_size=bucket_size,
        cap=cap,
        mu=torch.tensor(mus),
        sd=torch.tensor(sds),
        trace_counts=trace_counts,
        window_counts=window_counts,
        reused_buckets=reused,
    )


def conformal_threshold(maxima: Sequence[float], alpha: float) -> dict[str, Any]:
    """h = ceil((n+1)(1-alpha))-th smallest routine trace maximum (spec 1.6)."""

    result = finite_upper_threshold(list(maxima), alpha)
    result.pop("trace_maxima", None)
    return result


# ---------------------------------------------------------------------------
# splits (spec 1.4)
# ---------------------------------------------------------------------------


@dataclass
class SplitCase:
    split: str
    name: str
    fit_traces: tuple[LoadedTrace, ...]
    target_traces: tuple[LoadedTrace, ...]
    source_traces: tuple[LoadedTrace, ...]
    detail: dict[str, Any] = field(default_factory=dict)


def build_split_cases(
    batches: dict[str, tuple[LoadedTrace, ...]], split: str
) -> tuple[SplitCase, ...]:
    b1, b2 = batches["b1"], batches["b2"]
    pooled = (*b1, *b2)
    if split == "S1":
        cases = []
        for source_name, source, target_name, target in (
            ("b1", b1, "b2", b2),
            ("b2", b2, "b1", b1),
        ):
            cases.append(
                SplitCase(
                    split="S1",
                    name=f"{source_name}_to_{target_name}",
                    fit_traces=source,
                    target_traces=target,
                    source_traces=source,
                    detail={"source_batch": source_name, "target_batch": target_name},
                )
            )
        return tuple(cases)
    if split == "S2":
        groups = sorted({trace.group_id for trace in pooled})
        cases = []
        for group in groups:
            held = tuple(trace for trace in pooled if trace.group_id == group)
            rest = tuple(trace for trace in pooled if trace.group_id != group)
            cases.append(
                SplitCase(
                    split="S2",
                    name=f"holdout_{group}",
                    fit_traces=rest,
                    target_traces=held,
                    source_traces=rest,
                    detail={"held_out_group": group, "group_count": len(groups)},
                )
            )
        return tuple(cases)
    if split == "S3":
        domains = sorted({trace.scenario_domain for trace in pooled})
        cases = []
        for domain in domains:
            held = tuple(trace for trace in pooled if trace.scenario_domain == domain)
            rest = tuple(trace for trace in pooled if trace.scenario_domain != domain)
            cases.append(
                SplitCase(
                    split="S3",
                    name=f"lodo_{domain}",
                    fit_traces=rest,
                    target_traces=held,
                    source_traces=rest,
                    detail={"held_out_domain": domain, "domain_count": len(domains)},
                )
            )
        return tuple(cases)
    raise ValueError(f"unknown split: {split}")


def scenario_halves(traces: Sequence[LoadedTrace]) -> dict[str, int]:
    """Deterministic alternating split of scenarios (pair groups) into two halves."""

    groups = sorted({trace.pair_group_id for trace in traces})
    return {group: index % 2 for index, group in enumerate(groups)}


# ---------------------------------------------------------------------------
# per-trace alarm rows and aggregation (spec 1.7)
# ---------------------------------------------------------------------------


def _anchor_block(
    ends: torch.Tensor,
    alarm_ends: torch.Tensor,
    anchor: int,
    band: int,
) -> dict[str, Any]:
    """Alarm bookkeeping for one time anchor; ``band`` tokens before it are tolerated."""

    limit = anchor - band
    pre_eligible = bool((ends < limit).any())
    pre_alarm = bool((alarm_ends < limit).any())
    eligible = alarm_ends[alarm_ends >= limit]
    first = int(eligible[0]) if eligible.numel() else None
    latency = None if first is None else max(0, first - anchor)
    return {
        "anchor": anchor,
        "band": band,
        "pre_eligible": pre_eligible,
        "pre_alarm": pre_alarm,
        "first_alarm_end": first,
        "latency": latency,
        "hit": (not pre_alarm) and first is not None,
        "reachable": {
            str(horizon): bool(((ends >= anchor) & (ends <= anchor + horizon)).any())
            for horizon in (4, 8, 16, 32)
        },
    }


def alarm_row(
    trace: LoadedTrace,
    ends: torch.Tensor,
    statistic: torch.Tensor,
    threshold: float,
    *,
    comparison: str = "ge",
    weight: float = 1.0,
) -> dict[str, Any]:
    """Summarize one causal statistic stream of one trace against a fixed threshold."""

    ends = ends.reshape(-1).long()
    statistic = statistic.reshape(-1).to(torch.float64)
    if ends.numel() != statistic.numel():
        raise ValueError("statistic and endpoints are not aligned")
    states = COMPARISONS[comparison](statistic, threshold)
    if states.numel():
        previous = torch.cat((torch.tensor([False]), states[:-1]))
    else:
        previous = states
    alarm_ends = ends[states]
    onset_ends = ends[states & ~previous]
    row: dict[str, Any] = {
        "trace_id": trace.trace_id,
        "pair_group_id": trace.pair_group_id,
        "batch": trace.batch,
        "fold": trace.fold,
        "arm": trace.arm,
        "arm_class": arm_class(trace),
        "workflow": trace.workflow,
        "workflow_family": trace.workflow_family,
        "channel": trace.channel,
        "domain": trace.scenario_domain,
        "positive": trace.positive,
        "weight": float(weight),
        "threshold": float(threshold),
        "decode_token_count": trace.token_count,
        "eligible_endpoint_count": int(ends.numel()),
        "alarm_onset_count": int(onset_ends.numel()),
        "first_alarm_end": int(alarm_ends[0]) if alarm_ends.numel() else None,
        "alarm_onset_ends": [int(value) for value in onset_ends.tolist()],
    }
    if not trace.positive:
        row["false_alarm"] = bool(alarm_ends.numel())
        return row
    onset = trace.evidence_onset
    if onset is None:
        raise ValueError(f"positive trace lacks evidence onset: {trace.trace_id}")
    anchors = {
        "onset_strict": _anchor_block(ends, alarm_ends, onset, 0),
        "onset_tolerant": _anchor_block(ends, alarm_ends, onset, TOLERANCE_BAND),
        "onset_minus4": _anchor_block(ends, alarm_ends, onset - 4, 0),
        "onset_plus4": _anchor_block(ends, alarm_ends, onset + 4, 0),
    }
    if trace.completion_boundary is not None:
        anchors["completion_strict"] = _anchor_block(
            ends, alarm_ends, int(trace.completion_boundary), 0
        )
    row["evidence_onset"] = onset
    row["completion_boundary"] = trace.completion_boundary
    row["anchors"] = anchors
    row["pre_onset_alarm_ends"] = [int(v) for v in alarm_ends[alarm_ends < onset].tolist()]
    return row


def _weighted(values: Iterable[tuple[bool, float]]) -> float:
    return float(sum(weight for flag, weight in values if flag))


def _rate(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def _anchor_metrics(
    positives: Sequence[dict[str, Any]], key: str, horizons: Sequence[int]
) -> dict[str, Any]:
    rows = [row for row in positives if key in row["anchors"]]
    total = sum(row["weight"] for row in rows)
    blocks = [(row["anchors"][key], row["weight"]) for row in rows]
    pre_eligible = sum(weight for block, weight in blocks if block["pre_eligible"])
    pre_alarm = sum(weight for block, weight in blocks if block["pre_alarm"])
    metrics: dict[str, Any] = {
        "trace_weight": total,
        "trace_count": len(rows),
        "pre_alarm_denominator": pre_eligible,
        "pre_alarm_count": pre_alarm,
        "pre_alarm_rate": _rate(pre_alarm, pre_eligible),
        "pre_alarm_rate_all_drift": _rate(pre_alarm, total),
    }
    for horizon in horizons:
        hits = sum(
            weight
            for block, weight in blocks
            if block["hit"] and block["latency"] is not None and block["latency"] <= horizon
        )
        reach = sum(
            weight
            for block, weight in blocks
            if block["reachable"].get(str(horizon), False)
        )
        metrics[f"recall_plus_{horizon}_count"] = hits
        metrics[f"recall_plus_{horizon}"] = _rate(hits, total)
        metrics[f"reachable_plus_{horizon}"] = _rate(reach, total)
    final = sum(weight for block, weight in blocks if block["hit"])
    metrics["recall_final_count"] = final
    metrics["recall_final"] = _rate(final, total)
    latencies = [
        block["latency"]
        for block, _ in blocks
        if block["hit"] and block["latency"] is not None
    ]
    metrics["median_latency"] = float(statistics.median(latencies)) if latencies else None
    metrics["latencies"] = latencies
    return metrics


def _group_positive(positives: Sequence[dict[str, Any]], field_name: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in sorted({str(row[field_name]) for row in positives}):
        rows = [row for row in positives if str(row[field_name]) == value]
        block = _anchor_metrics(rows, "onset_strict", HORIZONS)
        tolerant = _anchor_metrics(rows, "onset_tolerant", TOLERANT_HORIZONS)
        result[value] = {
            "trace_count": len(rows),
            "recall_plus_8": block["recall_plus_8"],
            "recall_plus_16": block["recall_plus_16"],
            "recall_final": block["recall_final"],
            "tolerant_recall_plus_16": tolerant["recall_plus_16"],
            "tolerant_recall_final": tolerant["recall_final"],
            "pre_alarm_rate": block["pre_alarm_rate"],
        }
    return result


def aggregate_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """All spec-1.7 aggregates for one candidate configuration."""

    positives = [row for row in rows if row["positive"]]
    negatives = [row for row in rows if not row["positive"]]
    negative_weight = sum(row["weight"] for row in negatives)
    false_alarm_weight = _weighted((row["false_alarm"], row["weight"]) for row in negatives)
    by_arm: dict[str, Any] = {}
    for name in ARM_CLASSES:
        selected = [row for row in negatives if row["arm_class"] == name]
        weight = sum(row["weight"] for row in selected)
        alarms = _weighted((row["false_alarm"], row["weight"]) for row in selected)
        by_arm[name] = {
            "trace_weight": weight,
            "trace_count": len(selected),
            "false_alarm_count": alarms,
            "false_alarm_rate": _rate(alarms, weight),
        }
    routine_rows = [row for row in negatives if row["arm_class"] in ("clean", "benign")]
    routine_weight = sum(row["weight"] for row in routine_rows)
    routine_alarms = _weighted((row["false_alarm"], row["weight"]) for row in routine_rows)
    endpoints = sum(row["eligible_endpoint_count"] * row["weight"] for row in negatives)
    onsets = sum(row["alarm_onset_count"] * row["weight"] for row in negatives)
    result: dict[str, Any] = {
        "non_drift_trace_weight": negative_weight,
        "non_drift_trace_count": len(negatives),
        "non_drift_false_alarm_count": false_alarm_weight,
        "non_drift_false_alarm_rate": _rate(false_alarm_weight, negative_weight),
        "clean_benign_false_alarm_count": routine_alarms,
        "clean_benign_false_alarm_rate": _rate(routine_alarms, routine_weight),
        "by_arm": by_arm,
        "negative_endpoint_count": endpoints,
        "negative_alarm_onset_count": onsets,
        "alarm_onsets_per_1000_negative_positions": _rate(1000.0 * onsets, endpoints),
        "drift_trace_count": len(positives),
        "drift_trace_weight": sum(row["weight"] for row in positives),
        "onset_strict": _anchor_metrics(positives, "onset_strict", HORIZONS),
        "onset_tolerant": _anchor_metrics(positives, "onset_tolerant", TOLERANT_HORIZONS),
        "onset_minus4": _anchor_metrics(positives, "onset_minus4", HORIZONS),
        "onset_plus4": _anchor_metrics(positives, "onset_plus4", HORIZONS),
        "completion_strict": _anchor_metrics(positives, "completion_strict", (16,)),
        "by_domain": _group_positive(positives, "domain"),
        "by_channel": _group_positive(positives, "channel"),
        "by_workflow": _group_positive(positives, "workflow"),
        "by_workflow_family": _group_positive(positives, "workflow_family"),
    }
    return result


# ---------------------------------------------------------------------------
# scenario bootstrap (spec 1.7)
# ---------------------------------------------------------------------------


def _point_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, float | None]:
    """Light-weight subset of the aggregates, used inside the bootstrap loop."""

    negative_weight = 0.0
    negative_alarms = 0.0
    routine_weight = 0.0
    routine_alarms = 0.0
    drift_weight = 0.0
    hits = {4: 0.0, 8: 0.0, 16: 0.0}
    final = 0.0
    tolerant_16 = 0.0
    for row in rows:
        weight = row["weight"]
        if not row["positive"]:
            negative_weight += weight
            if row["false_alarm"]:
                negative_alarms += weight
            if row["arm_class"] in ("clean", "benign"):
                routine_weight += weight
                if row["false_alarm"]:
                    routine_alarms += weight
            continue
        drift_weight += weight
        strict = row["anchors"]["onset_strict"]
        if strict["hit"]:
            final += weight
            for horizon in hits:
                if strict["latency"] is not None and strict["latency"] <= horizon:
                    hits[horizon] += weight
        tolerant = row["anchors"]["onset_tolerant"]
        if tolerant["hit"] and tolerant["latency"] is not None and tolerant["latency"] <= 16:
            tolerant_16 += weight
    return {
        "non_drift_false_alarm_rate": _rate(negative_alarms, negative_weight),
        "clean_benign_false_alarm_rate": _rate(routine_alarms, routine_weight),
        "recall_plus_4": _rate(hits[4], drift_weight),
        "recall_plus_8": _rate(hits[8], drift_weight),
        "recall_plus_16": _rate(hits[16], drift_weight),
        "recall_final": _rate(final, drift_weight),
        "tolerant_recall_plus_16": _rate(tolerant_16, drift_weight),
    }


def scenario_bootstrap(
    rows: Sequence[dict[str, Any]], *, draws: int = 1000, seed: int = 20260904
) -> dict[str, Any]:
    """95% percentile intervals resampling scenarios (pair groups) with replacement."""

    by_group: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_group.setdefault(row["pair_group_id"], []).append(row)
    groups = sorted(by_group)
    if not groups:
        return {}
    generator = torch.Generator().manual_seed(seed)
    samples: dict[str, list[float]] = {}
    for _ in range(draws):
        index = torch.randint(len(groups), (len(groups),), generator=generator)
        resampled: list[dict[str, Any]] = []
        for position in index.tolist():
            resampled.extend(by_group[groups[position]])
        for key, value in _point_metrics(resampled).items():
            if value is not None:
                samples.setdefault(key, []).append(float(value))
    point = _point_metrics(rows)
    result: dict[str, Any] = {"draws": draws, "scenario_count": len(groups)}
    for key, value in point.items():
        values = sorted(samples.get(key, []))
        if not values:
            result[key] = {"point": value, "low": None, "high": None}
            continue
        low = values[max(0, int(math.floor(0.025 * len(values))) - 0)]
        high = values[min(len(values) - 1, int(math.ceil(0.975 * len(values))) - 1)]
        result[key] = {"point": value, "low": low, "high": high}
    return result


# ---------------------------------------------------------------------------
# ranking diagnostics (spec 1.7)
# ---------------------------------------------------------------------------


def _auroc(positive: Sequence[float], negative: Sequence[float]) -> float | None:
    if not positive or not negative:
        return None
    left = torch.tensor(positive, dtype=torch.float64).unsqueeze(1)
    right = torch.tensor(negative, dtype=torch.float64).unsqueeze(0)
    return float(((left > right).double().mean() + 0.5 * (left == right).double().mean()))


def ranking_diagnostics(
    streams: dict[str, tuple[torch.Tensor, torch.Tensor]],
    traces: Sequence[LoadedTrace],
) -> dict[str, Any]:
    """Trace-max AUROCs on the raw (uncalibrated) score, diagnostic only."""

    pools: dict[str, list[float]] = {
        "drift_post": [],
        "drift_pre": [],
        "clean": [],
        "benign": [],
        "resist": [],
    }
    for trace in traces:
        scores, ends = streams[trace.trace_id]
        if not ends.numel():
            continue
        if trace.positive:
            onset = int(trace.evidence_onset)
            post = scores[ends >= onset]
            pre = scores[ends < onset]
            if post.numel():
                pools["drift_post"].append(float(post.max()))
            if pre.numel():
                pools["drift_pre"].append(float(pre.max()))
        else:
            pools[arm_class(trace)].append(float(scores.max()))
    routine = pools["clean"] + pools["benign"]
    return {
        "counts": {key: len(value) for key, value in pools.items()},
        "drift_post_vs_clean": _auroc(pools["drift_post"], pools["clean"]),
        "drift_post_vs_benign": _auroc(pools["drift_post"], pools["benign"]),
        "drift_post_vs_resist": _auroc(pools["drift_post"], pools["resist"]),
        "drift_post_vs_routine": _auroc(pools["drift_post"], routine),
        "drift_pre_vs_routine": _auroc(pools["drift_pre"], routine),
        "benign_vs_clean": _auroc(pools["benign"], pools["clean"]),
        "resist_vs_clean": _auroc(pools["resist"], pools["clean"]),
    }


# ---------------------------------------------------------------------------
# Q1 diagnostic panel (spec 1.8)
# ---------------------------------------------------------------------------


Q1_OFFSETS = tuple(range(-32, 49))


def q1_panel(
    streams: dict[str, tuple[torch.Tensor, torch.Tensor]],
    traces: Sequence[LoadedTrace],
    stats: BucketStats,
    *,
    window_width: int,
    anchor_token_min_count: int = 10,
) -> dict[str, Any]:
    """Event curves, first separation offset, per-drift rise, anchor-token AUROC.

    The z used here is standardized with ``stats`` (fitted on the case's target routine
    pool); it is a diagnostic, not the calibrated alarm statistic.
    """

    z_by_trace: dict[str, tuple[torch.Tensor, torch.Tensor]] = {}
    for trace in traces:
        scores, ends = streams[trace.trace_id]
        if ends.numel():
            z_by_trace[trace.trace_id] = (stats.standardize(scores, ends), ends)
    by_group_arm: dict[tuple[str, str], LoadedTrace] = {
        (trace.pair_group_id, trace.arm): trace for trace in traces
    }
    drift = [trace for trace in traces if trace.positive and trace.trace_id in z_by_trace]

    curves: dict[int, dict[str, list[float]]] = {
        offset: {"drift": [], "clean": []} for offset in Q1_OFFSETS
    }
    rises: list[dict[str, Any]] = []
    for trace in drift:
        z_drift, ends_drift = z_by_trace[trace.trace_id]
        onset = int(trace.evidence_onset)
        lookup_drift = {int(end): float(value) for end, value in zip(ends_drift.tolist(), z_drift.tolist())}
        clean = by_group_arm.get((trace.pair_group_id, "clean"))
        lookup_clean: dict[int, float] = {}
        if clean is not None and clean.trace_id in z_by_trace:
            z_clean, ends_clean = z_by_trace[clean.trace_id]
            lookup_clean = {int(end): float(value) for end, value in zip(ends_clean.tolist(), z_clean.tolist())}
        for offset in Q1_OFFSETS:
            position = onset + offset
            if position in lookup_drift and position in lookup_clean:
                curves[offset]["drift"].append(lookup_drift[position])
                curves[offset]["clean"].append(lookup_clean[position])
        before = lookup_drift.get(onset - 1)
        after = lookup_drift.get(onset + 15)
        if before is not None and after is not None:
            rises.append(
                {
                    "trace_id": trace.trace_id,
                    "batch": trace.batch,
                    "domain": trace.scenario_domain,
                    "rise": after - before,
                }
            )

    curve_rows = []
    first_separation = None
    for offset in Q1_OFFSETS:
        drift_values = curves[offset]["drift"]
        clean_values = curves[offset]["clean"]
        if len(drift_values) < 5:
            continue
        drift_mean = float(statistics.mean(drift_values))
        clean_mean = float(statistics.mean(clean_values))
        pooled = math.sqrt(
            0.5
            * (
                (statistics.pstdev(drift_values) ** 2)
                + (statistics.pstdev(clean_values) ** 2)
            )
        )
        auroc = _auroc(drift_values, clean_values)
        separated = drift_mean > clean_mean + 2.0 * pooled and (auroc or 0.0) >= 0.9
        curve_rows.append(
            {
                "offset": offset,
                "n": len(drift_values),
                "drift_mean": drift_mean,
                "drift_median": float(statistics.median(drift_values)),
                "clean_mean": clean_mean,
                "clean_median": float(statistics.median(clean_values)),
                "pooled_sd": pooled,
                "paired_auroc": auroc,
                "separated": separated,
            }
        )
        if separated and first_separation is None:
            first_separation = offset

    # anchor-token AUROC: same token id, drift-post context vs routine context
    drift_tokens: dict[int, list[float]] = {}
    routine_tokens: dict[int, list[float]] = {}
    for trace in traces:
        if trace.trace_id not in z_by_trace:
            continue
        z_values, ends = z_by_trace[trace.trace_id]
        token_ids = trace.token_ids
        if trace.positive:
            onset = int(trace.evidence_onset)
            mask = ends >= onset
            target = drift_tokens
        elif trace.arm in rio.ROUTINE_CB_ARMS:
            mask = torch.ones_like(ends, dtype=torch.bool)
            target = routine_tokens
        else:
            continue
        for end, value in zip(ends[mask].tolist(), z_values[mask].tolist()):
            target.setdefault(int(token_ids[int(end)]), []).append(float(value))
    anchor_aurocs = []
    for token_id, values in drift_tokens.items():
        other = routine_tokens.get(token_id)
        if len(values) >= anchor_token_min_count and other and len(other) >= anchor_token_min_count:
            auroc = _auroc(values, other)
            if auroc is not None:
                anchor_aurocs.append({"token_id": token_id, "auroc": auroc, "drift_n": len(values), "routine_n": len(other)})
    aurocs = sorted(row["auroc"] for row in anchor_aurocs)
    anchor_summary = {
        "token_count": len(aurocs),
        "median": float(statistics.median(aurocs)) if aurocs else None,
        "q1": float(aurocs[len(aurocs) // 4]) if aurocs else None,
        "q3": float(aurocs[(3 * len(aurocs)) // 4]) if aurocs else None,
    }
    return {
        "window_width": window_width,
        "event_curves": curve_rows,
        "first_separation_offset": first_separation,
        "per_drift_rise": rises,
        "per_drift_rise_positive_fraction": (
            _rate(sum(1 for row in rises if row["rise"] > 0), len(rises)) if rises else None
        ),
        "anchor_token_auroc": anchor_summary,
        "anchor_token_rows": sorted(anchor_aurocs, key=lambda row: -row["auroc"])[:50],
    }


# ---------------------------------------------------------------------------
# pre-onset alarm audit (spec 1.7)
# ---------------------------------------------------------------------------


def pre_onset_audit_rows(
    rows: Sequence[dict[str, Any]],
    traces: dict[str, LoadedTrace],
    *,
    window_width: int,
    candidate_id: str,
    context_tokens: int = 8,
) -> list[dict[str, Any]]:
    """One row per strict pre-onset alarm onset, with the decoded alarm-window text."""

    audit: list[dict[str, Any]] = []
    for row in rows:
        if not row["positive"]:
            continue
        onset = row["evidence_onset"]
        pre_alarms = [end for end in row["alarm_onset_ends"] if end < onset]
        if not pre_alarms:
            continue
        trace = traces[row["trace_id"]]
        token_ids = trace.token_ids.tolist()
        for end in pre_alarms:
            start = max(0, end - window_width + 1)
            window = token_ids[start : end + 1]
            context = token_ids[max(0, start - context_tokens) : min(len(token_ids), end + 1 + context_tokens)]
            audit.append(
                {
                    "candidate_id": candidate_id,
                    "trace_id": row["trace_id"],
                    "batch": row["batch"],
                    "pair_group_id": row["pair_group_id"],
                    "domain": row["domain"],
                    "channel": row["channel"],
                    "workflow": row["workflow"],
                    "evidence_onset": onset,
                    "completion_boundary": row["completion_boundary"],
                    "alarm_end": end,
                    "offset_to_onset": end - onset,
                    "in_tolerance_band": bool(onset - TOLERANCE_BAND <= end < onset),
                    "window_text": rio.decode_text(window),
                    "window_tokens": rio.decode_token_texts(window),
                    "context_text": rio.decode_text(context),
                    "classification": "",
                }
            )
    return audit


# ---------------------------------------------------------------------------
# runner (spec 1.4 + 1.6 + 1.10)
# ---------------------------------------------------------------------------


@dataclass
class HarnessConfig:
    scorer: str = "g1_whitened_distance"
    scorer_config: dict[str, Any] = field(default_factory=dict)
    windows: tuple[int, ...] = (8,)
    routines: tuple[str, ...] = ("cb",)
    splits: tuple[str, ...] = ("S1",)
    modes: tuple[str, ...] = ("D",)
    alphas: tuple[float, ...] = (0.10,)
    readings: tuple[str, ...] | None = None
    comparison: str = "ge"
    pooling: str = "disjoint"
    bucket_size: int = 32
    min_bucket_traces: int = 30
    bucket_cap: int | None = None
    bucket_min_criterion: str = "traces"
    bootstrap_draws: int = 1000
    bootstrap_splits: tuple[str, ...] = ("S1",)
    bootstrap_readings: tuple[str, ...] = ("max", "persist2")
    bootstrap_modes: tuple[str, ...] = ("D",)
    b1_present_calibration: bool = False
    emit_q1: bool = True
    emit_audit: bool = True
    emit_ranking: bool = True
    store_score_streams: bool = True

    def to_json(self) -> dict[str, Any]:
        return {
            "scorer": self.scorer,
            "scorer_config": self.scorer_config,
            "windows": list(self.windows),
            "routines": list(self.routines),
            "splits": list(self.splits),
            "modes": list(self.modes),
            "alphas": list(self.alphas),
            "readings": list(self.readings) if self.readings else "all",
            "comparison": self.comparison,
            "pooling": self.pooling,
            "bucket_size": self.bucket_size,
            "min_bucket_traces": self.min_bucket_traces,
            "bucket_cap": self.bucket_cap,
            "bucket_min_criterion": self.bucket_min_criterion,
            "bootstrap_draws": self.bootstrap_draws,
            "bootstrap_splits": list(self.bootstrap_splits),
            "bootstrap_readings": list(self.bootstrap_readings),
            "bootstrap_modes": list(self.bootstrap_modes),
            "b1_present_calibration": self.b1_present_calibration,
        }


def code_commit(repo_root: Path = rio.REPO_ROOT) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:  # pragma: no cover - git may be unavailable
        return "unknown"


def _reading_threshold(
    reading: Reading, maxima: Sequence[float], alpha: float
) -> dict[str, Any]:
    if reading.threshold_source == "fixed":
        return {
            "alpha": alpha,
            "threshold": float(reading.fixed_threshold),
            "threshold_source": "fixed",
            "trace_count": len(maxima),
        }
    block = conformal_threshold(maxima, alpha)
    block["threshold_source"] = "conformal"
    return block


def _mode_d_candidates(
    case: SplitCase,
    streams: dict[str, tuple[torch.Tensor, torch.Tensor]],
    target_routine: Sequence[LoadedTrace],
    extra_calibration: Sequence[LoadedTrace],
    extra_streams: dict[str, tuple[torch.Tensor, torch.Tensor]],
    readings: Sequence[Reading],
    config: HarnessConfig,
) -> tuple[dict[tuple[str, float], list[dict[str, Any]]], dict[str, Any]]:
    halves = scenario_halves(case.target_traces)
    extra_halves = scenario_halves(extra_calibration) if extra_calibration else {}
    rows_by_candidate: dict[tuple[str, float], list[dict[str, Any]]] = {}
    diagnostics: dict[str, Any] = {"halves": {}, "pooling": config.pooling}
    for cal_half in (0, 1):
        calibration = [t for t in target_routine if halves[t.pair_group_id] == cal_half]
        calibration_streams = [streams[t.trace_id] for t in calibration]
        extras = [t for t in extra_calibration if extra_halves[t.pair_group_id] == cal_half]
        calibration_streams += [extra_streams[t.trace_id] for t in extras]
        stats = fit_bucket_stats(
            calibration_streams,
            bucket_size=config.bucket_size,
            min_bucket_traces=config.min_bucket_traces,
            bucket_cap=config.bucket_cap,
            min_criterion=config.bucket_min_criterion,
        )
        if config.pooling == "disjoint":
            evaluated = [
                (t, 1.0) for t in case.target_traces if halves[t.pair_group_id] != cal_half
            ]
        elif config.pooling == "pilot":
            evaluated = [
                (t, 0.5 if t.arm == rio.ATTACK_ARM else 1.0)
                for t in case.target_traces
                if t.arm == rio.ATTACK_ARM or halves[t.pair_group_id] != cal_half
            ]
        else:
            raise ValueError(f"unknown pooling rule: {config.pooling}")
        z_calibration = [
            stats.standardize(scores, ends) for scores, ends in calibration_streams
        ]
        z_evaluated = [
            (trace, weight, stats.standardize(*streams[trace.trace_id]), streams[trace.trace_id][1])
            for trace, weight in evaluated
            if streams[trace.trace_id][1].numel()
        ]
        diagnostics["halves"][str(cal_half)] = {
            "calibration_trace_count": len(calibration_streams),
            "calibration_from_target": len(calibration),
            "calibration_from_extra_pool": len(extras),
            "evaluated_trace_count": len(z_evaluated),
            "bucket_cap": stats.cap,
            "bucket_trace_counts": stats.trace_counts,
            "bucket_window_counts": stats.window_counts,
            "bucket_reused": stats.reused_buckets,
            "thresholds": {},
        }
        for reading in readings:
            calibration_maxima = [
                float(reading.apply(z).max()) for z in z_calibration if z.numel()
            ]
            statistics_evaluated = [
                (trace, weight, reading.apply(z), ends) for trace, weight, z, ends in z_evaluated
            ]
            for alpha in config.alphas:
                block = _reading_threshold(reading, calibration_maxima, alpha)
                key = (reading.name, alpha)
                rows_by_candidate.setdefault(key, []).extend(
                    alarm_row(
                        trace,
                        ends,
                        statistic,
                        block["threshold"],
                        comparison=config.comparison,
                        weight=weight,
                    )
                    for trace, weight, statistic, ends in statistics_evaluated
                )
                diagnostics["halves"][str(cal_half)]["thresholds"][
                    f"{reading.name}|alpha{alpha}"
                ] = block
    return rows_by_candidate, diagnostics


def _mode_t_candidates(
    case: SplitCase,
    streams: dict[str, tuple[torch.Tensor, torch.Tensor]],
    source_routine: Sequence[LoadedTrace],
    source_streams: dict[str, tuple[torch.Tensor, torch.Tensor]],
    readings: Sequence[Reading],
    config: HarnessConfig,
) -> tuple[dict[tuple[str, float], list[dict[str, Any]]], dict[str, Any]]:
    halves = scenario_halves(source_routine)
    full_stats = fit_bucket_stats(
        [source_streams[t.trace_id] for t in source_routine],
        bucket_size=config.bucket_size,
        min_bucket_traces=config.min_bucket_traces,
        bucket_cap=config.bucket_cap,
        min_criterion=config.bucket_min_criterion,
    )
    half_stats = {}
    for half in (0, 1):
        subset = [source_streams[t.trace_id] for t in source_routine if halves[t.pair_group_id] == half]
        half_stats[half] = fit_bucket_stats(
            subset,
            bucket_size=config.bucket_size,
            min_bucket_traces=config.min_bucket_traces,
            bucket_cap=config.bucket_cap,
            min_criterion=config.bucket_min_criterion,
        )
    cross_fit_z: list[torch.Tensor] = []
    for trace in source_routine:
        scores, ends = source_streams[trace.trace_id]
        if not ends.numel():
            continue
        other = 1 - halves[trace.pair_group_id]
        cross_fit_z.append(half_stats[other].standardize(scores, ends))
    evaluated = [
        (trace, full_stats.standardize(*streams[trace.trace_id]), streams[trace.trace_id][1])
        for trace in case.target_traces
        if streams[trace.trace_id][1].numel()
    ]
    rows_by_candidate: dict[tuple[str, float], list[dict[str, Any]]] = {}
    diagnostics: dict[str, Any] = {
        "source_routine_trace_count": len(source_routine),
        "cross_fit_trace_count": len(cross_fit_z),
        "bucket_cap": full_stats.cap,
        "bucket_trace_counts": full_stats.trace_counts,
        "thresholds": {},
    }
    for reading in readings:
        maxima = [float(reading.apply(z).max()) for z in cross_fit_z if z.numel()]
        statistics_evaluated = [
            (trace, reading.apply(z), ends) for trace, z, ends in evaluated
        ]
        for alpha in config.alphas:
            block = _reading_threshold(reading, maxima, alpha)
            rows_by_candidate.setdefault((reading.name, alpha), []).extend(
                alarm_row(
                    trace,
                    ends,
                    statistic,
                    block["threshold"],
                    comparison=config.comparison,
                    weight=1.0,
                )
                for trace, statistic, ends in statistics_evaluated
            )
            diagnostics["thresholds"][f"{reading.name}|alpha{alpha}"] = block
    return rows_by_candidate, diagnostics


def _compact_alarms(rows: Sequence[dict[str, Any]]) -> list[list[Any]]:
    """[trace_id, weight, first_alarm_end, first_band_alarm_end, alarm_onset_count]."""

    compact: list[list[Any]] = []
    for row in rows:
        band_first = None
        if row["positive"]:
            band = row["anchors"]["onset_tolerant"]
            band_first = band["first_alarm_end"]
        compact.append(
            [
                row["trace_id"],
                row["weight"],
                row["first_alarm_end"],
                band_first,
                row["alarm_onset_count"],
            ]
        )
    return compact


def run_case(
    case: SplitCase,
    scorer: Any,
    config: HarnessConfig,
    routine_definition: str,
    window_width: int,
    readings: Sequence[Reading],
    extra_calibration: Sequence[LoadedTrace] = (),
) -> dict[str, Any]:
    supervised = bool(getattr(scorer, "requires_positives", False))
    fit_pool = (
        tuple(case.fit_traces)
        if supervised
        else routine_traces(case.fit_traces, routine_definition)
    )
    state = scorer.fit(fit_pool)

    streams: dict[str, tuple[torch.Tensor, torch.Tensor]] = {}
    for trace in case.target_traces:
        scores, ends = scorer.score(state, trace)
        streams[trace.trace_id] = (scores.detach().to(torch.float64), ends.long())
    source_routine = routine_traces(case.source_traces, routine_definition)
    source_streams: dict[str, tuple[torch.Tensor, torch.Tensor]] = {}
    if "T" in config.modes:
        for trace in source_routine:
            if trace.trace_id in streams:
                source_streams[trace.trace_id] = streams[trace.trace_id]
            else:
                scores, ends = scorer.score(state, trace)
                source_streams[trace.trace_id] = (scores.detach().to(torch.float64), ends.long())
    extra_streams: dict[str, tuple[torch.Tensor, torch.Tensor]] = {}
    if extra_calibration:
        for trace in extra_calibration:
            scores, ends = scorer.score(state, trace)
            extra_streams[trace.trace_id] = (scores.detach().to(torch.float64), ends.long())

    target_routine = routine_traces(case.target_traces, routine_definition)
    target_routine = [t for t in target_routine if streams[t.trace_id][1].numel()]

    result: dict[str, Any] = {
        "split": case.split,
        "case": case.name,
        "detail": case.detail,
        "routine_definition": routine_definition,
        "window_width": window_width,
        "fit_trace_count": len(fit_pool),
        "fit_supervised": supervised,
        "target_trace_count": len(case.target_traces),
        "target_routine_trace_count": len(target_routine),
        "extra_calibration_trace_count": len(extra_calibration),
        "candidates": [],
    }
    if config.emit_ranking:
        result["ranking"] = ranking_diagnostics(streams, case.target_traces)
    if config.emit_q1 and target_routine:
        stats = fit_bucket_stats(
            [streams[t.trace_id] for t in target_routine],
            bucket_size=config.bucket_size,
            min_bucket_traces=config.min_bucket_traces,
            bucket_cap=config.bucket_cap,
            min_criterion=config.bucket_min_criterion,
        )
        result["q1_panel"] = q1_panel(
            streams, case.target_traces, stats, window_width=window_width
        )

    trace_lookup = {trace.trace_id: trace for trace in case.target_traces}
    audit: list[dict[str, Any]] = []
    for mode in config.modes:
        if mode == "D":
            rows_by_candidate, diagnostics = _mode_d_candidates(
                case, streams, target_routine, extra_calibration, extra_streams, readings, config
            )
        elif mode == "T":
            rows_by_candidate, diagnostics = _mode_t_candidates(
                case, streams, source_routine, source_streams, readings, config
            )
        else:
            raise ValueError(f"unknown calibration mode: {mode}")
        result.setdefault("calibration", {})[mode] = diagnostics
        for (reading_name, alpha), rows in sorted(rows_by_candidate.items()):
            candidate_id = "|".join(
                [
                    f"{case.split}:{case.name}",
                    f"w{window_width}",
                    f"routine={routine_definition}",
                    f"mode={mode}",
                    f"alpha={alpha:g}",
                    f"reading={reading_name}",
                ]
            )
            candidate = {
                "candidate_id": candidate_id,
                "split": case.split,
                "case": case.name,
                "window_width": window_width,
                "routine_definition": routine_definition,
                "mode": mode,
                "alpha": alpha,
                "reading": reading_name,
                "reference_only": supervised,
                "metrics": aggregate_rows(rows),
                "trace_alarms": _compact_alarms(rows),
            }
            if (
                case.split in config.bootstrap_splits
                and reading_name in config.bootstrap_readings
                and mode in config.bootstrap_modes
                and config.bootstrap_draws
            ):
                candidate["bootstrap"] = scenario_bootstrap(
                    rows, draws=config.bootstrap_draws
                )
            result["candidates"].append(candidate)
            if config.emit_audit:
                audit.extend(
                    pre_onset_audit_rows(
                        rows,
                        trace_lookup,
                        window_width=window_width,
                        candidate_id=candidate_id,
                    )
                )
    result["pre_onset_audit"] = audit
    if config.store_score_streams:
        result["score_streams"] = {
            trace_id: {
                "ends": [int(v) for v in ends.tolist()],
                "scores": [round(float(v), 6) for v in scores.tolist()],
            }
            for trace_id, (scores, ends) in streams.items()
        }
    return result


def run_harness(
    config: HarnessConfig,
    *,
    batches: dict[str, tuple[LoadedTrace, ...]] | None = None,
    extra_calibration: Sequence[LoadedTrace] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    from research_v2.scorers import build as build_scorer

    if batches is None:
        batches = rio.load_core()
    if extra_calibration is None and config.b1_present_calibration:
        extra_calibration = rio.load_b1_present()
    extra_calibration = tuple(extra_calibration or ())
    readings = build_readings(config.readings)
    cases_by_split = {split: build_split_cases(batches, split) for split in config.splits}
    results: list[dict[str, Any]] = []
    for window_width in config.windows:
        scorer_config = {**config.scorer_config, "window_width": window_width}
        scorer = build_scorer(config.scorer, scorer_config)
        for routine_definition in config.routines:
            for split in config.splits:
                for case in cases_by_split[split]:
                    if progress:
                        progress(
                            f"w={window_width} routine={routine_definition} {split}:{case.name}"
                        )
                    extras = (
                        tuple(
                            t
                            for t in extra_calibration
                            if all(target.batch == "b1" for target in case.target_traces)
                        )
                        if extra_calibration
                        else ()
                    )
                    results.append(
                        run_case(
                            case,
                            scorer,
                            config,
                            routine_definition,
                            window_width,
                            readings,
                            extra_calibration=extras,
                        )
                    )
    scorer = build_scorer(config.scorer, {**config.scorer_config, "window_width": config.windows[0]})
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_role": "post-hoc exploratory development on B1/B2 (not confirmation)",
        "spec": "docs/sequential_v2_lead_proposals.md section 1",
        "code_commit": code_commit(),
        "datasets": rio.dataset_hashes(),
        "config": config.to_json(),
        "scorer_resolved_config": scorer.config() if hasattr(scorer, "config") else {},
        "reading_catalogue": [
            {
                "name": reading.name,
                "threshold_source": reading.threshold_source,
                "fixed_threshold": reading.fixed_threshold,
            }
            for reading in readings
        ],
        "protocol_decisions": {
            "comparison": config.comparison,
            "mode_d_pooling": config.pooling,
            "position_bucket": (
                f"end // {config.bucket_size}, tail merged to >= {config.min_bucket_traces} "
                f"{config.bucket_min_criterion}"
                + (f", hard cap {config.bucket_cap}" if config.bucket_cap is not None else "")
            ),
            "empirical_rarity_transform": "none",
            "tolerance_band": TOLERANCE_BAND,
        },
        "case_runs": results,
    }


# ---------------------------------------------------------------------------
# markdown reporting
# ---------------------------------------------------------------------------


TABLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("split", "split"),
    ("case", "case"),
    ("w", "window_width"),
    ("routine", "routine_definition"),
    ("mode", "mode"),
    ("alpha", "alpha"),
    ("reading", "reading"),
    ("FARall", "far_all"),
    ("FARc", "far_clean"),
    ("FARb", "far_benign"),
    ("FARr", "far_resist"),
    ("pre", "pre_alarm_rate"),
    ("R4", "recall_plus_4"),
    ("R8", "recall_plus_8"),
    ("R16", "recall_plus_16"),
    ("RF", "recall_final"),
    ("preTol", "tolerant_pre_alarm_rate"),
    ("R16tol", "tolerant_recall_plus_16"),
    ("RFtol", "tolerant_recall_final"),
    ("lat", "median_latency"),
    ("reach8", "reachable_plus_8"),
    ("onsets/1k", "alarm_onsets_per_1000_negative_positions"),
)


def candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    metrics = candidate["metrics"]
    strict = metrics["onset_strict"]
    tolerant = metrics["onset_tolerant"]
    return {
        "candidate_id": candidate["candidate_id"],
        "split": candidate["split"],
        "case": candidate["case"],
        "window_width": candidate["window_width"],
        "routine_definition": candidate["routine_definition"],
        "mode": candidate["mode"],
        "alpha": candidate["alpha"],
        "reading": candidate["reading"],
        "reference_only": candidate.get("reference_only", False),
        "far_all": metrics["non_drift_false_alarm_rate"],
        "far_clean": metrics["by_arm"]["clean"]["false_alarm_rate"],
        "far_benign": metrics["by_arm"]["benign"]["false_alarm_rate"],
        "far_resist": metrics["by_arm"]["resist"]["false_alarm_rate"],
        "far_clean_benign": metrics["clean_benign_false_alarm_rate"],
        "pre_alarm_rate": strict["pre_alarm_rate"],
        "recall_plus_4": strict["recall_plus_4"],
        "recall_plus_8": strict["recall_plus_8"],
        "recall_plus_16": strict["recall_plus_16"],
        "recall_final": strict["recall_final"],
        "tolerant_pre_alarm_rate": tolerant["pre_alarm_rate"],
        "tolerant_recall_plus_16": tolerant["recall_plus_16"],
        "tolerant_recall_final": tolerant["recall_final"],
        "median_latency": strict["median_latency"],
        "reachable_plus_8": strict["reachable_plus_8"],
        "alarm_onsets_per_1000_negative_positions": metrics[
            "alarm_onsets_per_1000_negative_positions"
        ],
        "completion_recall_plus_16": metrics["completion_strict"]["recall_plus_16"],
        "completion_recall_final": metrics["completion_strict"]["recall_final"],
    }


def _pool_anchor(blocks: Sequence[dict[str, Any]], horizons: Sequence[int]) -> dict[str, Any]:
    total = sum(block["trace_weight"] for block in blocks)
    pre_denominator = sum(block["pre_alarm_denominator"] for block in blocks)
    pre_count = sum(block["pre_alarm_count"] for block in blocks)
    latencies = [value for block in blocks for value in block.get("latencies", [])]
    pooled: dict[str, Any] = {
        "trace_weight": total,
        "trace_count": sum(block["trace_count"] for block in blocks),
        "pre_alarm_denominator": pre_denominator,
        "pre_alarm_count": pre_count,
        "pre_alarm_rate": _rate(pre_count, pre_denominator),
        "pre_alarm_rate_all_drift": _rate(pre_count, total),
        "recall_final_count": sum(block["recall_final_count"] for block in blocks),
        "recall_final": _rate(sum(block["recall_final_count"] for block in blocks), total),
        "median_latency": float(statistics.median(latencies)) if latencies else None,
        "latencies": latencies,
    }
    for horizon in horizons:
        hits = sum(block[f"recall_plus_{horizon}_count"] for block in blocks)
        pooled[f"recall_plus_{horizon}_count"] = hits
        pooled[f"recall_plus_{horizon}"] = _rate(hits, total)
        reach = sum(
            (block[f"reachable_plus_{horizon}"] or 0.0) * block["trace_weight"] for block in blocks
        )
        pooled[f"reachable_plus_{horizon}"] = _rate(reach, total)
    return pooled


def pool_candidates(candidates: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Pool count-additive metrics across the disjoint cases of one split."""

    metrics = [candidate["metrics"] for candidate in candidates]
    negative_weight = sum(block["non_drift_trace_weight"] for block in metrics)
    negative_alarms = sum(block["non_drift_false_alarm_count"] for block in metrics)
    routine_weight = sum(
        block["by_arm"]["clean"]["trace_weight"] + block["by_arm"]["benign"]["trace_weight"]
        for block in metrics
    )
    routine_alarms = sum(
        block["by_arm"]["clean"]["false_alarm_count"] + block["by_arm"]["benign"]["false_alarm_count"]
        for block in metrics
    )
    by_arm = {}
    for name in ARM_CLASSES:
        weight = sum(block["by_arm"][name]["trace_weight"] for block in metrics)
        alarms = sum(block["by_arm"][name]["false_alarm_count"] for block in metrics)
        by_arm[name] = {
            "trace_weight": weight,
            "false_alarm_count": alarms,
            "false_alarm_rate": _rate(alarms, weight),
        }
    endpoints = sum(block["negative_endpoint_count"] for block in metrics)
    onsets = sum(block["negative_alarm_onset_count"] for block in metrics)
    return {
        "case_count": len(candidates),
        "non_drift_trace_weight": negative_weight,
        "non_drift_false_alarm_count": negative_alarms,
        "non_drift_false_alarm_rate": _rate(negative_alarms, negative_weight),
        "clean_benign_false_alarm_rate": _rate(routine_alarms, routine_weight),
        "by_arm": by_arm,
        "negative_endpoint_count": endpoints,
        "negative_alarm_onset_count": onsets,
        "alarm_onsets_per_1000_negative_positions": _rate(1000.0 * onsets, endpoints),
        "drift_trace_count": sum(block["drift_trace_count"] for block in metrics),
        "onset_strict": _pool_anchor([block["onset_strict"] for block in metrics], HORIZONS),
        "onset_tolerant": _pool_anchor(
            [block["onset_tolerant"] for block in metrics], TOLERANT_HORIZONS
        ),
        "completion_strict": _pool_anchor([block["completion_strict"] for block in metrics], (16,)),
    }


def pooled_summaries(result: dict[str, Any]) -> list[dict[str, Any]]:
    """One pooled row per (split, window, routine, mode, alpha, reading)."""

    grouped: dict[tuple, list[dict[str, Any]]] = {}
    for case_run in result["case_runs"]:
        for candidate in case_run["candidates"]:
            key = (
                candidate["split"],
                candidate["window_width"],
                candidate["routine_definition"],
                candidate["mode"],
                candidate["alpha"],
                candidate["reading"],
            )
            grouped.setdefault(key, []).append(candidate)
    rows = []
    for key, candidates in sorted(grouped.items(), key=lambda item: str(item[0])):
        split, window, routine, mode, alpha, reading = key
        pooled = pool_candidates(candidates)
        rows.append(
            candidate_summary(
                {
                    "candidate_id": f"{split}:POOLED|w{window}|routine={routine}|mode={mode}|alpha={alpha:g}|reading={reading}",
                    "split": split,
                    "case": f"POOLED({pooled['case_count']})",
                    "window_width": window,
                    "routine_definition": routine,
                    "mode": mode,
                    "alpha": alpha,
                    "reading": reading,
                    "reference_only": candidates[0].get("reference_only", False),
                    "metrics": pooled,
                }
            )
        )
    return rows


def collect_summaries(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        candidate_summary(candidate)
        for case_run in result["case_runs"]
        for candidate in case_run["candidates"]
    ]


def _format(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.3f}" if abs(value) < 100 else f"{value:.1f}"
    return str(value)


def markdown_table(
    summaries: Sequence[dict[str, Any]],
    columns: Sequence[tuple[str, str]] = TABLE_COLUMNS,
    *,
    label: str | None = None,
) -> str:
    header = "| " + " | ".join(name for name, _ in columns) + " |"
    divider = "|" + "|".join("---" for _ in columns) + "|"
    lines = [header, divider]
    for row in summaries:
        lines.append("| " + " | ".join(_format(row.get(key)) for _, key in columns) + " |")
    body = "\n".join(lines)
    return f"**{label}**\n\n{body}" if label else body


def write_result(result: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Write result.json, summary.json, tables.md and the pre-onset audit files."""

    output_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    result_path = output_dir / "result.json"
    result_path.write_text(json.dumps(result, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    files["result.json"] = str(result_path)

    summaries = collect_summaries(result)
    summary_path = output_dir / "summary.json"
    summary_path.write_text(json.dumps(summaries, indent=1) + "\n", encoding="utf-8")
    files["summary.json"] = str(summary_path)

    tables_path = output_dir / "tables.md"
    tables_path.write_text(markdown_table(summaries) + "\n", encoding="utf-8")
    files["tables.md"] = str(tables_path)

    pooled = pooled_summaries(result)
    pooled_path = output_dir / "pooled.md"
    pooled_path.write_text(markdown_table(pooled) + "\n", encoding="utf-8")
    files["pooled.md"] = str(pooled_path)
    pooled_json = output_dir / "pooled.json"
    pooled_json.write_text(json.dumps(pooled, indent=1) + "\n", encoding="utf-8")
    files["pooled.json"] = str(pooled_json)

    audit = [row for case_run in result["case_runs"] for row in case_run.get("pre_onset_audit", [])]
    audit_json = output_dir / "pre_onset_audit.json"
    audit_json.write_text(json.dumps(audit, indent=1) + "\n", encoding="utf-8")
    files["pre_onset_audit.json"] = str(audit_json)
    audit_csv = output_dir / "pre_onset_audit.csv"
    import csv as _csv

    with audit_csv.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "candidate_id",
            "trace_id",
            "batch",
            "pair_group_id",
            "domain",
            "channel",
            "workflow",
            "evidence_onset",
            "completion_boundary",
            "alarm_end",
            "offset_to_onset",
            "in_tolerance_band",
            "window_text",
            "context_text",
            "classification",
        ]
        writer = _csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in audit:
            writer.writerow(row)
    files["pre_onset_audit.csv"] = str(audit_csv)

    q1 = {
        f"{case_run['split']}:{case_run['case']}|w{case_run['window_width']}|routine={case_run['routine_definition']}": case_run["q1_panel"]
        for case_run in result["case_runs"]
        if "q1_panel" in case_run
    }
    if q1:
        q1_path = output_dir / "q1_panel.json"
        q1_path.write_text(json.dumps(q1, indent=1) + "\n", encoding="utf-8")
        files["q1_panel.json"] = str(q1_path)

    checksums = {name: rio.sha256(Path(path)) for name, path in files.items()}
    manifest = {"files": files, "sha256": checksums}
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    return manifest
