"""Shared plumbing for the TIMING ANATOMY OF HITS zoom audit.

Everything here is a *diagnostic re-scoring* of two frozen candidates; it never selects a
configuration.  Calibration always follows the harness mode-D rule (scenario half
cross-fit, position-bucket standardization, conformal threshold on routine trace maxima).

CAND-A : scorer `wgm`, metric g1 (whitened squared distance), layers 5-15, w=8, routine=cb
CAND-B : scorer `pdm`, model d1 (depth-chain surprisal), layers 5-11, w=4, routine=cb
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import torch

torch.set_num_threads(6)

from research_v2 import io as rio  # noqa: E402
from research_v2.features import selection_rate_windows  # noqa: E402
from research_v2.harness import (  # noqa: E402
    HarnessConfig,
    aggregate_rows,
    alarm_row,
    build_split_cases,
    conformal_threshold,
    fit_bucket_stats,
    routine_traces,
    scenario_halves,
)
from research_v2.readings import build_readings  # noqa: E402
from research_v2.scorers.pdm import PdmScorer  # noqa: E402
from research_v2.scorers.wgm import WGMScorer  # noqa: E402

ALPHA = 0.10
CAND_A_LAYERS = tuple(range(5, 16))
CAND_B_LAYERS = tuple(range(5, 12))
BUCKET_KW = dict(bucket_size=32, min_bucket_traces=30, bucket_cap=None, min_criterion="traces")

Stream = tuple[torch.Tensor, torch.Tensor]  # (scores[n], ends[n])


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------


def load_cases() -> tuple[dict[str, tuple], tuple]:
    batches = rio.load_core()
    cases = build_split_cases(batches, "S1")
    return batches, cases


_TEXT_CACHE: dict[str, list[str]] = {}


def token_texts(trace) -> list[str]:
    if trace.trace_id not in _TEXT_CACHE:
        _TEXT_CACHE[trace.trace_id] = rio.decode_token_texts(trace.token_ids.tolist())
    return _TEXT_CACHE[trace.trace_id]


def snippet(trace, start: int, stop: int) -> str:
    ids = trace.token_ids.tolist()
    start = max(0, start)
    stop = min(len(ids), stop)
    if start >= stop:
        return ""
    return rio.decode_text(ids[start:stop]).replace("\n", "\\n")


# ---------------------------------------------------------------------------
# scorers and their per-layer decompositions
# ---------------------------------------------------------------------------


class CandA:
    """g1 whitened squared distance, layers 5-15.  Per-layer parts sum to the total."""

    key = "CAND-A"
    layers = CAND_A_LAYERS

    def __init__(self, window_width: int = 8) -> None:
        self.window_width = window_width
        self.scorer = WGMScorer(window_width=window_width, layers="middle_late", metric="g1")

    def fit(self, routine):
        self.state = self.scorer.fit(routine)
        return self.state

    def stream(self, trace) -> Stream:
        scores, ends = self.scorer.score(self.state, trace)
        return scores.to(torch.float64), ends.long()

    def layer_stream(self, trace) -> tuple[torch.Tensor, torch.Tensor]:
        """[L, n] per-layer squared whitened distance, same ends as `stream`."""
        ends, windows = self.scorer._windows(trace)
        if not ends.numel():
            return torch.empty((len(self.layers), 0), dtype=torch.float64), ends
        z = (windows - self.state.mu) / self.state.sd - self.state.centre
        per = (z**2).reshape(z.shape[0], len(self.layers), 64).sum(2)
        return per.T.to(torch.float64), ends.long()

    def layer_names(self) -> list[str]:
        return [f"L{l}" for l in self.layers]


class CandB:
    """d1 depth-chain surprisal over layers 5-11.

    Per-layer parts: the initial marginal at layer 5 and the six depth steps 5->6 ... 10->11.
    Each part is the per-token surprisal contribution, averaged over the same causal window.
    """

    key = "CAND-B"
    layers = CAND_B_LAYERS

    def __init__(self, window_width: int = 4) -> None:
        self.window_width = window_width
        self.scorer = PdmScorer(window_width=window_width, model="d1", layers="middle")

    def fit(self, routine):
        self.state = self.scorer.fit(routine)
        # routine mean/sd of each per-layer part, for a comparable per-layer stream
        parts: list[torch.Tensor] = []
        for trace in routine:
            if trace.top_k_ids.shape[1]:
                parts.append(self._parts(trace))
        pooled = torch.cat(parts, dim=1)
        self.part_mean = pooled.mean(dim=1, keepdim=True)
        self.part_sd = pooled.std(dim=1, keepdim=True) + 1e-6
        return self.state

    def _parts(self, trace) -> torch.Tensor:
        """[P, T] raw per-token surprisal contributions (P = 1 + len(layers)-1)."""
        top1 = self.scorer._top1(trace.top_k_ids)
        rows = [-self.state.log_depth_initial[top1[0]]]
        for index in range(top1.shape[0] - 1):
            rows.append(-self.state.log_depth[index][top1[index], top1[index + 1]])
        return torch.stack(rows).to(torch.float64)

    def stream(self, trace) -> Stream:
        scores, ends = self.scorer.score(self.state, trace)
        return scores.to(torch.float64), ends.long()

    def layer_stream(self, trace) -> tuple[torch.Tensor, torch.Tensor]:
        parts = (self._parts(trace) - self.part_mean) / self.part_sd  # [P, T]
        tokens = parts.shape[1]
        w = self.window_width
        if tokens < w:
            return torch.empty((parts.shape[0], 0), dtype=torch.float64), torch.empty(0, dtype=torch.long)
        prefix = torch.cat((torch.zeros((parts.shape[0], 1), dtype=torch.float64), parts.cumsum(1)), dim=1)
        means = (prefix[:, w:] - prefix[:, :-w]) / float(w)
        ends = torch.arange(w - 1, tokens, dtype=torch.long)
        return means, ends

    def layer_names(self) -> list[str]:
        names = [f"init@L{self.layers[0]}"]
        names += [f"L{a}->L{b}" for a, b in zip(self.layers[:-1], self.layers[1:])]
        return names


# ---------------------------------------------------------------------------
# mode-D machinery (mirrors harness._mode_d_candidates)
# ---------------------------------------------------------------------------


@dataclass
class HalfContext:
    cal_half: int
    calibration: list
    cal_streams: list[Stream]
    stats: Any
    evaluated: list
    routine_median_z: float


def mode_d_halves(case, target_routine, raw: dict[str, Stream]) -> list[HalfContext]:
    halves = scenario_halves(case.target_traces)
    out: list[HalfContext] = []
    for cal_half in (0, 1):
        calibration = [t for t in target_routine if halves[t.pair_group_id] == cal_half]
        cal_streams = [raw[t.trace_id] for t in calibration]
        stats = fit_bucket_stats(cal_streams, **BUCKET_KW)
        z_cal = torch.cat([stats.standardize(s, e) for s, e in cal_streams])
        evaluated = [t for t in case.target_traces if halves[t.pair_group_id] != cal_half]
        evaluated = [t for t in evaluated if raw[t.trace_id][1].numel()]
        out.append(
            HalfContext(
                cal_half=cal_half,
                calibration=calibration,
                cal_streams=cal_streams,
                stats=stats,
                evaluated=evaluated,
                routine_median_z=float(z_cal.median()),
            )
        )
    return out


def evaluate_stat(
    case,
    target_routine,
    raw: dict[str, Stream],
    reading_name: str = "persist2",
    alpha: float = ALPHA,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Standard path: bucket-standardize, apply reading, conformal threshold, alarm rows."""
    reading = build_readings((reading_name,))[0]
    rows: list[dict[str, Any]] = []
    detail: dict[str, Any] = {}
    for ctx in mode_d_halves(case, target_routine, raw):
        maxima = []
        for s, e in ctx.cal_streams:
            z = ctx.stats.standardize(s, e)
            if z.numel():
                maxima.append(float(reading.apply(z).max()))
        block = conformal_threshold(maxima, alpha)
        h = float(block["threshold"])
        detail[str(ctx.cal_half)] = {
            "threshold": h,
            "routine_median_z": ctx.routine_median_z,
            "calibration_trace_count": len(ctx.calibration),
            "bucket_cap": ctx.stats.cap,
        }
        for trace in ctx.evaluated:
            s, e = raw[trace.trace_id]
            z = ctx.stats.standardize(s, e)
            rows.append(alarm_row(trace, e, reading.apply(z), h, comparison="ge", weight=1.0))
    return rows, detail


def evaluate_vote(
    case,
    target_routine,
    layer_raw: dict[str, tuple[torch.Tensor, torch.Tensor]],
    layer_alpha: float,
    reading_name: str = "persist2",
    alpha: float = ALPHA,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Per-layer early vote: S(e) = #{layers whose own cross-fitted tail is exceeded}.

    Per-layer tails and the vote threshold are both fitted on the same calibration half,
    so the statistic stays cross-fitted exactly like the primary reading.
    """
    reading = build_readings((reading_name,))[0]
    halves = scenario_halves(case.target_traces)
    rows: list[dict[str, Any]] = []
    detail: dict[str, Any] = {}
    n_layers = next(iter(layer_raw.values()))[0].shape[0]
    for cal_half in (0, 1):
        calibration = [t for t in target_routine if halves[t.pair_group_id] == cal_half]
        per_layer_stats = []
        for li in range(n_layers):
            streams = [(layer_raw[t.trace_id][0][li], layer_raw[t.trace_id][1]) for t in calibration]
            per_layer_stats.append(fit_bucket_stats(streams, **BUCKET_KW))
        quantiles = []
        for li in range(n_layers):
            maxima = []
            for t in calibration:
                s, e = layer_raw[t.trace_id][0][li], layer_raw[t.trace_id][1]
                if e.numel():
                    maxima.append(float(per_layer_stats[li].standardize(s, e).max()))
            quantiles.append(float(conformal_threshold(maxima, layer_alpha)["threshold"]))

        def vote(trace_id: str) -> tuple[torch.Tensor, torch.Tensor]:
            per, e = layer_raw[trace_id]
            if not e.numel():
                return torch.empty(0, dtype=torch.float64), e
            flags = torch.stack(
                [
                    (per_layer_stats[li].standardize(per[li], e) >= quantiles[li]).to(torch.float64)
                    for li in range(n_layers)
                ]
            )
            return flags.sum(0), e

        maxima = []
        for t in calibration:
            s, e = vote(t.trace_id)
            if e.numel():
                maxima.append(float(reading.apply(s).max()))
        block = conformal_threshold(maxima, alpha)
        h = float(block["threshold"])
        detail[str(cal_half)] = {
            "vote_threshold_k": h,
            "layer_alpha": layer_alpha,
            "layer_quantiles": quantiles,
        }
        for trace in case.target_traces:
            if halves[trace.pair_group_id] == cal_half:
                continue
            s, e = vote(trace.trace_id)
            if not e.numel():
                continue
            rows.append(alarm_row(trace, e, reading.apply(s), h, comparison="ge", weight=1.0))
    return rows, detail


def block_subsample(raw: dict[str, Stream], width: int) -> dict[str, Stream]:
    """Keep only non-overlapping block ends: w-1, 2w-1, 3w-1, ..."""
    out: dict[str, Stream] = {}
    for trace_id, (s, e) in raw.items():
        if not e.numel():
            out[trace_id] = (s, e)
            continue
        keep = ((e + 1) % width == 0)
        out[trace_id] = (s[keep], e[keep])
    return out


# ---------------------------------------------------------------------------
# metric helpers
# ---------------------------------------------------------------------------


def headline(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    agg = aggregate_rows(rows)
    strict = agg["onset_strict"]
    lat = strict["latencies"]
    return {
        "far_all": agg["non_drift_false_alarm_rate"],
        "far_count": agg["non_drift_false_alarm_count"],
        "far_denom": agg["non_drift_trace_weight"],
        "far_clean": agg["by_arm"]["clean"]["false_alarm_rate"],
        "far_benign": agg["by_arm"]["benign"]["false_alarm_rate"],
        "far_resist": agg["by_arm"]["resist"]["false_alarm_rate"],
        "resist_count": agg["by_arm"]["resist"]["false_alarm_count"],
        "resist_denom": agg["by_arm"]["resist"]["trace_weight"],
        "drift_n": strict["trace_count"],
        "r4": strict["recall_plus_4"],
        "r8": strict["recall_plus_8"],
        "r16": strict["recall_plus_16"],
        "rf": strict["recall_final"],
        "r4_count": strict["recall_plus_4_count"],
        "r8_count": strict["recall_plus_8_count"],
        "r16_count": strict["recall_plus_16_count"],
        "rf_count": strict["recall_final_count"],
        "pre_alarm_rate": strict["pre_alarm_rate"],
        "median_latency": strict["median_latency"],
        "latency_iqr": iqr(lat),
        "latencies": lat,
    }


def iqr(values: Sequence[float]) -> tuple[float | None, float | None]:
    values = sorted(values)
    if not values:
        return (None, None)
    if len(values) == 1:
        return (float(values[0]), float(values[0]))
    return (quantile(values, 0.25), quantile(values, 0.75))


def quantile(sorted_values: Sequence[float], q: float) -> float:
    n = len(sorted_values)
    pos = (n - 1) * q
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return float(sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac)


def median_or_none(values: Sequence[float]) -> float | None:
    return float(statistics.median(values)) if values else None
