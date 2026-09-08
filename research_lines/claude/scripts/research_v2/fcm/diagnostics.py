#!/usr/bin/env python3
"""FCM diagnostics: raw-score separation of programming windows, target-trace text,
and the restricted F0 rule subset (prereg section 7.7)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    CANDIDATES,
    SENSITIVITY,
    FROZEN,
    FROZEN_WINDOW,
    OUT,
    case_runs,
    cases_for,
    load_result,
    product_onsets,
    streams_from_result,
)
from research_v2 import io as rio  # noqa: E402
from research_v2.harness import routine_traces  # noqa: E402

torch.set_num_threads(12)
DIRECTIONS = ("b1_to_b2", "b2_to_b1")


def quantiles(values: torch.Tensor) -> dict[str, float]:
    if not values.numel():
        return {}
    return {
        "n": int(values.numel()),
        "median": float(values.median()),
        "q90": float(torch.quantile(values, 0.90)),
        "max": float(values.max()),
    }


def main() -> None:
    batches = rio.load_core()
    cases = cases_for(batches)
    onsets = product_onsets()
    by_id = {t.trace_id: t for traces in batches.values() for t in traces}
    analysis = json.loads((OUT / "fcm_analysis.json").read_text(encoding="utf-8"))
    targets = analysis["targets"]

    grid = {**CANDIDATES, **SENSITIVITY}
    sources = {key: OUT / f"{spec['run']}__evidence" / "result.json" for key, spec in grid.items()}
    windows = {key: spec["window"] for key, spec in grid.items()}
    for key, path in FROZEN.items():
        sources[f"CAND-{key}"] = path
        windows[f"CAND-{key}"] = FROZEN_WINDOW[key]

    separation: dict[str, dict] = {}
    for key, path in sources.items():
        result = load_result(path)
        runs = case_runs(result, windows[key])
        separation[key] = {}
        for case_name, case_run in runs.items():
            case = cases[case_name]
            streams = streams_from_result(case_run)
            routine = routine_traces(case.target_traces, "cb")
            routine_scores = torch.cat([streams[t.trace_id][0] for t in routine if streams[t.trace_id][1].numel()])
            code_scores = []
            other_scores = []
            for trace in case.target_traces:
                if not trace.positive:
                    continue
                scores, ends = streams[trace.trace_id]
                if not ends.numel():
                    continue
                onset = int(trace.evidence_onset)
                mask = (ends >= onset) & (ends <= onset + 16)
                target = code_scores if trace.scenario_domain == "programming" else other_scores
                target.append(scores[mask])
            separation[key][case_name] = {
                "routine_windows": quantiles(routine_scores),
                "programming_onset16": quantiles(torch.cat(code_scores)) if code_scores else {},
                "other_drift_onset16": quantiles(torch.cat(other_scores)) if other_scores else {},
            }

    texts = {}
    for trace_id, meta in targets.items():
        trace = by_id[trace_id]
        onset = int(meta["evidence_onset"])
        product = int(onsets[trace_id])
        ids = trace.token_ids.tolist()
        texts[trace_id] = {
            "evidence_onset": onset,
            "product_onset": product,
            "text_evidence_m16_p32": rio.decode_text(ids[max(0, onset - 16) : onset + 32]),
            "text_product_m16_p32": rio.decode_text(ids[max(0, product - 16) : product + 32]),
        }

    f0 = json.loads((OUT / "f0_text_rule.json").read_text(encoding="utf-8"))
    subsets = {
        "F0_full": ["R1_code_fence", "R2_sql", "R3_programming", "R4_non_action_json", "R5_indent_block"],
        "F0_no_json": ["R1_code_fence", "R2_sql", "R3_programming", "R5_indent_block"],
        "F0_code_only": ["R1_code_fence", "R2_sql", "R3_programming"],
    }
    f0_subsets = {}
    for name, rules in subsets.items():
        def first(row):
            values = [row["rule_hits"][r] for r in rules if row["rule_hits"][r] is not None]
            return min(values) if values else None

        negatives = [r for r in f0["rows"] if not r["positive"]]
        positives = [r for r in f0["rows"] if r["positive"]]
        programming = [r for r in positives if r["domain"] == "programming"]
        block = {
            "rules": rules,
            "routine_false_alarms": sum(1 for r in negatives if first(r) is not None),
            "routine_false_alarms_by_arm": {
                arm: sum(1 for r in negatives if r["arm_class"] == arm and first(r) is not None)
                for arm in ("clean", "benign", "resist")
            },
            "drift_any_hit": sum(1 for r in positives if first(r) is not None),
            "programming_any_hit": sum(1 for r in programming if first(r) is not None),
        }
        for anchor in ("evidence", "product"):
            deltas = []
            clean = 0
            for row in programming:
                hit = first(row)
                if hit is None:
                    continue
                onset = int(row[f"{anchor}_onset"])
                deltas.append(hit - onset)
                if onset <= hit <= onset + 16:
                    clean += 1
            block[f"programming_clean_hit16_{anchor}"] = clean
            block[f"programming_delta_{anchor}"] = sorted(deltas)
        f0_subsets[name] = block

    payload = {"separation": separation, "target_texts": texts, "f0_subsets": f0_subsets}
    (OUT / "diagnostics.json").write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(json.dumps(f0_subsets, indent=1))
    for key in ("F1", "F5", "F6a", "F6b", "F7", "CAND-A", "CAND-B"):
        for case_name in DIRECTIONS:
            block = separation[key][case_name]
            print(
                f"{key:7s} {case_name}: routine med {block['routine_windows']['median']:.3g} q90 "
                f"{block['routine_windows']['q90']:.3g} | prog med {block['programming_onset16']['median']:.3g} "
                f"q90 {block['programming_onset16']['q90']:.3g} | other med {block['other_drift_onset16']['median']:.3g} "
                f"q90 {block['other_drift_onset16']['q90']:.3g}"
            )


if __name__ == "__main__":
    main()
