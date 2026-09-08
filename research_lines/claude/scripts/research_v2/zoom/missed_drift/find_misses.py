#!/usr/bin/env python3
"""Identify and verify every drift trace not cleanly detected within onset+16."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    ART,
    OUT,
    cases_for,
    hit_within,
    load_result,
    miss_class,
    mode_d_rows,
    snippet,
    streams_from_result,
)
from research_v2 import io as rio  # noqa: E402

torch.set_num_threads(6)

CANDIDATES = {
    "CAND-A": ART / "wgm" / "c2_g1_middle_late" / "result.json",
    "CAND-B": ART / "pdm_d1_middle_s1" / "result.json",
}
WINDOW = {"CAND-A": 8, "CAND-B": 4}


def main() -> None:
    batches = rio.load_core()
    cases = cases_for(batches)
    by_id = {t.trace_id: t for b in batches.values() for t in b}
    report: dict[str, dict] = {}
    for key, path in CANDIDATES.items():
        result = load_result(path)
        report[key] = {}
        for case_run in result["case_runs"]:
            if case_run["split"] != "S1" or case_run["window_width"] != WINDOW[key]:
                continue
            if case_run["routine_definition"] != "cb":
                continue
            case = cases[case_run["case"]]
            streams = streams_from_result(case_run)
            rows, detail = mode_d_rows(case, streams)
            # verification against the stored harness alarms
            stored = {
                r[0]: r for r in next(
                    c for c in case_run["candidates"]
                    if c["candidate_id"].endswith("mode=D|alpha=0.1|reading=persist2")
                )["trace_alarms"]
            }
            mismatch = []
            for row in rows:
                s = stored.get(row["trace_id"])
                if s is None:
                    mismatch.append((row["trace_id"], "missing"))
                elif s[2] != row["first_alarm_end"]:
                    mismatch.append((row["trace_id"], s[2], row["first_alarm_end"]))
            drift = [r for r in rows if r["positive"]]
            misses = [r for r in drift if not hit_within(r, 16)]
            entries = []
            for row in misses:
                trace = by_id[row["trace_id"]]
                d = detail[row["trace_id"]]
                onset = row["evidence_onset"]
                ends = d["ends"].tolist()
                z = d["z"].tolist()
                stat = d["stat"].tolist()
                thr = d["threshold"]
                idx = {e: i for i, e in enumerate(ends)}
                post = [(e, z[i], stat[i]) for e, i in idx.items() if e >= onset]
                post_max_stat = max((s for _, _, s in post), default=float("-inf"))
                post_max_z = max((zz for _, zz, _ in post), default=float("-inf"))
                in16 = [(e, zz, s) for e, zz, s in post if e <= onset + 16]
                max_stat_in16 = max((s for _, _, s in in16), default=float("-inf"))
                max_z_in16 = max((zz for _, zz, _ in in16), default=float("-inf"))
                first_post = next((e for e, _, s in post if s >= thr), None)
                tol = row["anchors"]["onset_tolerant"]
                # margin at the post-onset max of the alarm statistic
                entries.append(
                    {
                        "trace_id": row["trace_id"],
                        "batch": trace.batch,
                        "arm": row["arm"],
                        "domain": row["domain"],
                        "channel": row["channel"],
                        "workflow": row["workflow"],
                        "workflow_family": row["workflow_family"],
                        "decode_len": row["decode_token_count"],
                        "onset": onset,
                        "completion_boundary": row["completion_boundary"],
                        "class": miss_class(row, 16),
                        "first_alarm_end": row["first_alarm_end"],
                        "pre_onset_alarm_ends": row.get("pre_onset_alarm_ends", []),
                        "threshold": thr,
                        "post_max_stat": post_max_stat,
                        "post_max_z": post_max_z,
                        "margin_at_post_max": post_max_stat - thr,
                        "max_stat_in16": max_stat_in16,
                        "max_z_in16": max_z_in16,
                        "margin_in16": max_stat_in16 - thr,
                        "first_alarm_after_onset": first_post,
                        "delay_if_pre_ignored": None if first_post is None else first_post - onset,
                        "tolerant_pre_alarm": tol["pre_alarm"],
                        "tolerant_first": tol["first_alarm_end"],
                        "n_windows_post_onset": len(post),
                        "windows_to_onset16": sum(1 for e, _, _ in post if e <= onset + 16),
                        "text_onset_m16_p32": snippet(trace, onset - 16, onset + 32),
                        "z_series": [
                            [e, round(z[i], 3)]
                            for e, i in idx.items()
                            if onset - 8 <= e <= onset + 32
                        ],
                        "stat_series": [
                            [e, round(stat[i], 3)]
                            for e, i in idx.items()
                            if onset - 8 <= e <= onset + 32
                        ],
                    }
                )
            report[key][case_run["case"]] = {
                "n_drift": len(drift),
                "n_hit16": len(drift) - len(misses),
                "n_miss": len(misses),
                "verification_mismatches": mismatch,
                "misses": entries,
            }
            print(
                f"{key} {case_run['case']}: drift={len(drift)} miss={len(misses)} "
                f"mismatches={len(mismatch)}",
                flush=True,
            )
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "misses.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print("written", OUT / "misses.json")


if __name__ == "__main__":
    main()
