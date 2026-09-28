#!/usr/bin/env python3
"""WGM diagnostics: G2 rank elbow curves, departure-vector energy split, failure cases.

Produces the report tables that the shared harness does not emit:

* **T-H** the routine-only held-out reconstruction-error curve E(r) and the selected r*
  for every fitting side used by the S1/S2/S3 runs (spec 3.3, prereg section 3);
* **T-I** the departure vector's energy split into negative coordinates (routine experts
  switched off) and positive coordinates (new experts switched on), per arm and per layer
  (spec 3.8's requested diagnostic);
* **T-J** missed drift traces and highest-scoring non-drift traces with decoded text.

All fitting uses routine traces only; the drift/arm labels enter the *reporting* of the
diagnostics, never the fit.

    PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python scripts/research_v2/wgm_diagnostics.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Sequence

import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io as rio  # noqa: E402
from research_v2.harness import (  # noqa: E402
    BucketStats,
    build_split_cases,
    fit_bucket_stats,
    routine_traces,
)
from research_v2.readings import build_readings  # noqa: E402
from research_v2.scorers.wgm import WGMScorer  # noqa: E402

OUTPUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "wgm" / "diagnostics"
PRIMARY_RESULT = (
    ROOT / "artifacts" / "agent_v2" / "research_v2" / "wgm" / "c1_c7_c8_c9_g1_middle" / "result.json"
)
PRIMARY_CANDIDATE = "|w8|routine=cb|mode=D|alpha=0.1|reading=persist2"


# ---------------------------------------------------------------------------
# T-H: rank elbow curves
# ---------------------------------------------------------------------------
def rank_curves(batches: dict[str, tuple[Any, ...]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for band in ("middle", "middle_late"):
        for split in ("S1", "S3"):
            for case in build_split_cases(batches, split):
                for definition in ("cb", "all_normal") if split == "S1" else ("cb",):
                    if band == "middle_late" and split == "S3":
                        continue
                    pool = routine_traces(case.fit_traces, definition)
                    scorer = WGMScorer(window_width=8, layers=band, metric="g2", rank="auto")
                    state = scorer.fit(pool)
                    rows.append(
                        {
                            "split": split,
                            "case": case.name,
                            "layer_band": band,
                            "routine_definition": definition,
                            "routine_trace_count": len(pool),
                            **state.rank_selection,
                        }
                    )
    return rows


# ---------------------------------------------------------------------------
# T-I: departure-vector energy split
# ---------------------------------------------------------------------------
def departure_energy(batches: dict[str, tuple[Any, ...]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scorer = WGMScorer(window_width=8, layers="middle", metric="g1")
    layer_count = len(scorer.layers)
    for case in build_split_cases(batches, "S1"):
        pool = routine_traces(case.fit_traces, "cb")
        state = scorer.fit(pool)
        # A whitened coordinate is bounded below by (0 - mu)/sd, so the energy a window
        # can put into negative coordinates is capped; report the cap next to the split.
        floor = (0.0 - state.mu) / state.sd - state.centre
        negative_cap = float((floor**2).sum())
        groups: dict[str, dict[str, Any]] = {}
        for trace in case.target_traces:
            ends, windows = scorer._windows(trace)
            if not ends.numel():
                continue
            departure = (windows - state.mu) / state.sd - state.centre
            energy = departure**2
            negative = energy * (departure < 0)
            onset = trace.evidence_onset
            if trace.positive and onset is not None:
                masks = {
                    "drift_post_onset": ends >= onset,
                    "drift_pre_onset": ends < onset,
                }
            else:
                masks = {rio.arm_class(trace): torch.ones_like(ends, dtype=torch.bool)}
            for key, mask in masks.items():
                if not bool(mask.any()):
                    continue
                block = groups.setdefault(
                    key,
                    {
                        "window_count": 0,
                        "total": torch.zeros(layer_count * 64, dtype=torch.float64),
                        "negative": torch.zeros(layer_count * 64, dtype=torch.float64),
                    },
                )
                block["window_count"] += int(mask.sum())
                block["total"] += energy[mask].sum(0).to(torch.float64)
                block["negative"] += negative[mask].sum(0).to(torch.float64)
        for key, block in sorted(groups.items()):
            total = block["total"].reshape(layer_count, 64)
            negative = block["negative"].reshape(layer_count, 64)
            rows.append(
                {
                    "case": case.name,
                    "group": key,
                    "window_count": block["window_count"],
                    "negative_energy_cap": negative_cap,
                    "mean_energy_per_window": float(total.sum() / max(1, block["window_count"])),
                    "mean_negative_energy_per_window": float(
                        negative.sum() / max(1, block["window_count"])
                    ),
                    "negative_energy_fraction": float(negative.sum() / total.sum()),
                    "positive_energy_fraction": float(1.0 - negative.sum() / total.sum()),
                    "by_layer": {
                        str(layer): {
                            "energy_share": float(total[index].sum() / total.sum()),
                            "negative_fraction": float(
                                negative[index].sum() / total[index].sum()
                            ),
                        }
                        for index, layer in enumerate(scorer.layers)
                    },
                }
            )
    return rows


# ---------------------------------------------------------------------------
# T-J: failure cases
# ---------------------------------------------------------------------------
def _primary_case_runs(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for case_run in result["case_runs"]:
        if case_run["window_width"] == 8 and case_run["routine_definition"] == "cb":
            out[case_run["case"]] = case_run
    return out


def failure_cases(batches: dict[str, tuple[Any, ...]]) -> dict[str, Any]:
    result = json.loads(PRIMARY_RESULT.read_text(encoding="utf-8"))
    case_runs = _primary_case_runs(result)
    reading = {r.name: r for r in build_readings(("persist2",))}["persist2"]
    output: dict[str, Any] = {"missed_drift": [], "top_non_drift": []}

    for case in build_split_cases(batches, "S1"):
        case_run = case_runs[case.name]
        candidate = next(
            c for c in case_run["candidates"] if c["candidate_id"].endswith(PRIMARY_CANDIDATE)
        )
        alarms = {row[0]: row for row in candidate["trace_alarms"]}
        streams = {
            trace_id: (
                torch.tensor(block["scores"], dtype=torch.float64),
                torch.tensor(block["ends"], dtype=torch.long),
            )
            for trace_id, block in case_run["score_streams"].items()
        }
        target_routine = [
            t for t in routine_traces(case.target_traces, "cb") if streams[t.trace_id][1].numel()
        ]
        stats: BucketStats = fit_bucket_stats([streams[t.trace_id] for t in target_routine])
        threshold = candidate["metrics"].get("threshold")

        scored: list[tuple[float, Any]] = []
        for trace in case.target_traces:
            scores, ends = streams[trace.trace_id]
            if not ends.numel():
                continue
            statistic = reading.apply(stats.standardize(scores, ends))
            scored.append((float(statistic.max()), trace))

        for peak, trace in scored:
            row = alarms.get(trace.trace_id)
            if trace.positive:
                onset = int(trace.evidence_onset)
                first = None if row is None else row[2]
                if first is None:
                    miss_type = "no_alarm"
                elif first < onset:
                    miss_type = "pre_onset_alarm"
                elif first > onset + 16:
                    miss_type = "late_alarm"
                else:
                    miss_type = None
                if miss_type is not None:
                    output["missed_drift"].append(
                        {
                            "case": case.name,
                            "trace_id": trace.trace_id,
                            "miss_type": miss_type,
                            "domain": trace.scenario_domain,
                            "workflow": trace.workflow,
                            "channel": trace.channel,
                            "onset": onset,
                            "first_alarm_end": first,
                            "token_count": trace.token_count,
                            "peak_persist2_z": round(peak, 3),
                            "text_at_onset": rio.decode_text(
                                trace.token_ids[max(0, onset - 8) : onset + 32].tolist()
                            ),
                        }
                    )
            else:
                output["top_non_drift"].append(
                    {
                        "case": case.name,
                        "trace_id": trace.trace_id,
                        "arm": rio.arm_class(trace),
                        "domain": trace.scenario_domain,
                        "workflow": trace.workflow,
                        "peak_persist2_z": round(peak, 3),
                        "alarmed": bool(row is not None and row[2] is not None),
                        "first_alarm_end": None if row is None else row[2],
                        "text_at_peak": _peak_text(trace, streams[trace.trace_id], stats, reading),
                    }
                )
        output.setdefault("thresholds", {})[case.name] = threshold

    output["missed_drift"].sort(key=lambda r: (r["case"], -r["peak_persist2_z"]))
    output["top_non_drift"].sort(key=lambda r: -r["peak_persist2_z"])
    return output


def _peak_text(trace: Any, stream: tuple[torch.Tensor, torch.Tensor], stats: BucketStats, reading) -> str:
    scores, ends = stream
    statistic = reading.apply(stats.standardize(scores, ends))
    index = int(statistic.argmax())
    end = int(ends[index])
    return rio.decode_text(trace.token_ids[max(0, end - 7) : end + 1].tolist())


def main() -> None:
    torch.set_num_threads(8)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    batches = rio.load_core()
    payload = {
        "datasets": rio.dataset_hashes(),
        "rank_curves": rank_curves(batches),
        "departure_energy": departure_energy(batches),
        "failure_cases": failure_cases(batches),
    }
    path = OUTPUT / "wgm_diagnostics.json"
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    print(f"rank rows {len(payload['rank_curves'])}, energy rows {len(payload['departure_energy'])}")
    print(
        "missed drift",
        len(payload["failure_cases"]["missed_drift"]),
        "non-drift rows",
        len(payload["failure_cases"]["top_non_drift"]),
    )


if __name__ == "__main__":
    main()
