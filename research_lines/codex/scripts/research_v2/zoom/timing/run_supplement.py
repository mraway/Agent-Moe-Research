"""Supplementary numbers: pre-onset alarm offsets, and the matched-FAR latency-floor table."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from timing_lib import (
    ALPHA,
    CandA,
    CandB,
    build_readings,
    conformal_threshold,
    alarm_row,
    load_cases,
    mode_d_halves,
    routine_traces,
)

OUT = Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/research_v2/zoom/timing")
AUDIT = json.loads((OUT / "timing_audit.json").read_text())
BAND = json.loads((OUT / "layerband_cf.json").read_text())
CASES = ("b1_to_b2", "b2_to_b1")


def pre_onset_offsets() -> dict[str, Any]:
    _, cases = load_cases()
    reading = build_readings(("persist2",))[0]
    out: dict[str, Any] = {}
    for case in cases:
        for cand_cls, w in ((CandA, 8), (CandB, 4)):
            cand = cand_cls(w)
            cand.fit(routine_traces(case.fit_traces, "cb"))
            raw = {t.trace_id: cand.stream(t) for t in case.target_traces}
            tr = [t for t in routine_traces(case.target_traces, "cb") if raw[t.trace_id][1].numel()]
            rows: list[dict[str, Any]] = []
            for ctx in mode_d_halves(case, tr, raw):
                maxima = [
                    float(reading.apply(ctx.stats.standardize(s, e)).max())
                    for s, e in ctx.cal_streams
                    if e.numel()
                ]
                h = float(conformal_threshold(maxima, ALPHA)["threshold"])
                for trace in ctx.evaluated:
                    if not trace.positive:
                        continue
                    s, e = raw[trace.trace_id]
                    row = alarm_row(trace, e, reading.apply(ctx.stats.standardize(s, e)), h)
                    pre = row["pre_onset_alarm_ends"]
                    tol = row["anchors"]["onset_tolerant"]
                    rows.append(
                        {
                            "trace_id": trace.trace_id,
                            "domain": trace.scenario_domain,
                            "onset": trace.evidence_onset,
                            "first_pre_offset": (min(pre) - trace.evidence_onset) if pre else None,
                            "tolerant_hit": tol["hit"],
                            "tolerant_latency": tol["latency"],
                        }
                    )
            pres = [r for r in rows if r["first_pre_offset"] is not None]
            out[f"{case.name}|{cand.key}"] = {
                "drift_n": len(rows),
                "pre_onset_n": len(pres),
                "pre_offsets": sorted(r["first_pre_offset"] for r in pres),
                "pre_within_band_n": sum(1 for r in pres if r["first_pre_offset"] >= -8),
                "tolerant_hit_n": sum(1 for r in rows if r["tolerant_hit"]),
                "tolerant_plus8_n": sum(
                    1
                    for r in rows
                    if r["tolerant_hit"] and r["tolerant_latency"] is not None and r["tolerant_latency"] <= 8
                ),
                "pre_rows": pres,
            }
    return out


def collect_variants() -> dict[str, dict[str, Any]]:
    """All variants keyed by name, per direction."""
    variants: dict[str, dict[str, Any]] = {}
    for case in CASES:
        for cand in ("CAND-A", "CAND-B"):
            block = AUDIT["cases"][case][cand]
            for key, h in block["width_sweep"].items():
                variants.setdefault(f"{cand} {key}", {})[case] = h
            for key, h in block["vote"].items():
                variants.setdefault(f"{cand} vote {key}", {})[case] = h
            for key, h in block["blocks"].items():
                variants.setdefault(f"{cand} {key}", {})[case] = h
        for key, h in BAND[case].items():
            variants.setdefault(key, {})[case] = h
    return variants


def matched_table() -> list[dict[str, Any]]:
    variants = collect_variants()
    rows = []
    frozen = {
        "CAND-A": variants["CAND-A w8|persist2"],
        "CAND-B": variants["CAND-B w4|persist2"],
    }
    for name, per_case in variants.items():
        if len(per_case) != 2:
            continue
        for ref_name, ref in frozen.items():
            ok = all(
                per_case[c]["far_benign"] <= ref[c]["far_benign"] + 0.021
                and per_case[c]["far_resist"] <= ref[c]["far_resist"] + 0.021
                and per_case[c]["far_all"] <= ref[c]["far_all"] + 0.021
                for c in CASES
            )
            faster = all(
                per_case[c]["median_latency"] is not None
                and per_case[c]["median_latency"] <= ref[c]["median_latency"]
                for c in CASES
            )
            rows.append(
                {
                    "variant": name,
                    "reference": ref_name,
                    "far_ok": ok,
                    "faster_or_equal": faster,
                    "lat": [per_case[c]["median_latency"] for c in CASES],
                    "r4": [per_case[c]["r4"] for c in CASES],
                    "r8": [per_case[c]["r8"] for c in CASES],
                    "far": [per_case[c]["far_all"] for c in CASES],
                    "benign": [per_case[c]["far_benign"] for c in CASES],
                    "resist": [per_case[c]["far_resist"] for c in CASES],
                }
            )
    return rows


def main() -> None:
    result = {"pre_onset": pre_onset_offsets(), "matched": matched_table()}
    (OUT / "supplement.json").write_text(json.dumps(result, indent=1))
    for key, block in result["pre_onset"].items():
        print(
            key,
            "drift", block["drift_n"],
            "pre-onset", block["pre_onset_n"],
            "offsets", block["pre_offsets"],
            "within -8", block["pre_within_band_n"],
            "tolerant hits", block["tolerant_hit_n"],
            "tolerant +8", block["tolerant_plus8_n"],
        )
    print()
    for row in result["matched"]:
        if row["far_ok"] and row["faster_or_equal"]:
            print("PASS", row["reference"], "|", row["variant"], row)


if __name__ == "__main__":
    main()
