"""Build the markdown tables and per-trace CSV for the timing zoom report."""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

OUT = Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/research_v2/zoom/timing")
AUDIT = json.loads((OUT / "timing_audit.json").read_text())
BAND = json.loads((OUT / "layerband_cf.json").read_text())
CASES = ("b1_to_b2", "b2_to_b1")
WIDTH = {"CAND-A": 8, "CAND-B": 4}


def q(values, p):
    values = sorted(values)
    if not values:
        return None
    n = len(values)
    pos = (n - 1) * p
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return values[lo] * (1 - frac) + values[hi] * frac


def fmt(value, digits=3):
    return "-" if value is None else f"{value:.{digits}f}"


def mechanism(rec: dict[str, Any]) -> str:
    if rec["pre_onset_alarm"]:
        return "pre-onset alarm (disqualified)"
    if rec["alarm_end"] is None:
        return "no alarm (miss)"
    if rec["latency"] is not None and rec["latency"] <= 4:
        return "fast hit (latency<=4)"
    parts = {
        "late signal rise": rec["comp_rise"] or 0,
        "threshold-margin limited": rec["comp_margin"] or 0,
        "persistence limited": rec["comp_persist"] or 0,
    }
    order = ["threshold-margin limited", "late signal rise", "persistence limited"]
    best = max(order, key=lambda k: (parts[k], -order.index(k)))
    return best


# ---------------------------------------------------------------------------
lines: list[str] = []
A = lines.append

A("## T1  Latency decomposition of clean hits (mode D, alpha=0.10, persist2)\n")
A("| direction | candidate | drift n | hits | pre-onset | miss | latency med [IQR] | rise med | margin med | persist med | window fill at alarm med |")
A("|---|---|---|---|---|---|---|---|---|---|---|")
mech_counter: dict[str, Counter] = {}
for case in CASES:
    for cand in ("CAND-A", "CAND-B"):
        T = AUDIT["cases"][case][cand]["timing"]
        hits = [r for r in T if r["hit"]]
        mech_counter[f"{case}|{cand}"] = Counter(mechanism(r) for r in T)
        lat = [r["latency"] for r in hits]
        A(
            f"| {case} | {cand} | {len(T)} | {len(hits)} | "
            f"{sum(1 for r in T if r['pre_onset_alarm'])} | "
            f"{sum(1 for r in T if not r['pre_onset_alarm'] and r['alarm_end'] is None)} | "
            f"{statistics.median(lat):.1f} [{q(lat,.25):.1f}, {q(lat,.75):.1f}] | "
            f"{statistics.median([r['comp_rise'] for r in hits]):.1f} | "
            f"{statistics.median([r['comp_margin'] for r in hits]):.1f} | "
            f"{statistics.median([r['comp_persist'] for r in hits]):.1f} | "
            f"{statistics.median([r['window_fill_at_alarm'] for r in hits]):.1f} |"
        )
A("")

A("## T2  Mechanism classes (all drift traces, denominator = drift n)\n")
classes = [
    "fast hit (latency<=4)",
    "threshold-margin limited",
    "late signal rise",
    "persistence limited",
    "pre-onset alarm (disqualified)",
    "no alarm (miss)",
]
A("| mechanism | " + " | ".join(f"{c}/{k}" for k in ("CAND-A", "CAND-B") for c in CASES) + " |")
A("|---" * 5 + "|")
for cls in classes:
    cells = []
    for cand in ("CAND-A", "CAND-B"):
        for case in CASES:
            counter = mech_counter[f"{case}|{cand}"]
            total = sum(counter.values())
            cells.append(f"{counter.get(cls,0)}/{total}")
    A(f"| {cls} | " + " | ".join(cells) + " |")
A("")

A("## T3  Per-layer first crossing of its own cross-fitted tail (alpha=0.10), clean hits only\n")
for case in CASES:
    for cand in ("CAND-A", "CAND-B"):
        block = AUDIT["cases"][case][cand]
        hit_ids = {r["trace_id"] for r in block["timing"] if r["hit"]}
        rows = [r for r in block["layer_crossings"] if r["trace_id"] in hit_ids]
        A(f"\n**{case} / {cand}** (n = {len(rows)} clean hits)\n")
        A("| layer / depth step | traces that ever cross after onset | median offset | crossings within +8 | pre-onset crossings |")
        A("|---|---|---|---|---|")
        for name in block["layer_names"]:
            offs = [r["layers"][name]["first_offset"] for r in rows if r["layers"][name]["first_offset"] is not None]
            med = f"{statistics.median(offs):.1f}" if offs else "-"
            A(
                f"| {name} | {len(offs)}/{len(rows)} | {med} | "
                f"{sum(1 for o in offs if o <= 8)}/{len(rows)} | "
                f"{sum(1 for r in rows if r['layers'][name]['pre_onset'])}/{len(rows)} |"
            )
A("")


def row_from_headline(label, h):
    label = str(label).replace("|", " / ")
    return (
        f"| {label} | {fmt(h['far_all'])} | {fmt(h['far_clean'])} | {fmt(h['far_benign'])} | "
        f"{fmt(h['far_resist'])} | {fmt(h['r4'])} | {fmt(h['r8'])} | {fmt(h['r16'])} | {fmt(h['rf'])} | "
        f"{fmt(h['median_latency'],1)} | [{fmt(h['latency_iqr'][0],1)}, {fmt(h['latency_iqr'][1],1)}] |"
    )


HEAD = "| config | FAR_all | clean | benign | resist | R+4 | R+8 | R+16 | R_final | lat med | lat IQR |"
SEP = "|---|---|---|---|---|---|---|---|---|---|---|"

A("## T4  Window width sweep w in {1,4,8} (same scorer family, mode D, alpha=0.10)\n")
for case in CASES:
    A(f"\n**{case}**\n")
    A(HEAD)
    A(SEP)
    for cand in ("CAND-A", "CAND-B"):
        for key, h in AUDIT["cases"][case][cand]["width_sweep"].items():
            A(row_from_headline(f"{cand} {key}", h))
A("")

A("## T5  Per-layer early vote and non-overlapping blocks\n")
for case in CASES:
    A(f"\n**{case}**\n")
    A(HEAD + " k |")
    A(SEP + "---|")
    for cand in ("CAND-A", "CAND-B"):
        w = WIDTH[cand]
        h = AUDIT["cases"][case][cand]["width_sweep"][f"w{w}|persist2"]
        A(row_from_headline(f"{cand} frozen (w{w}, persist2)", h) + " - |")
        for key, hv in AUDIT["cases"][case][cand]["vote"].items():
            A(row_from_headline(f"{cand} vote {key}", hv) + f" {hv['k_thresholds']} |")
        for key, hb in AUDIT["cases"][case][cand]["blocks"].items():
            A(row_from_headline(f"{cand} {key}", hb) + " - |")
A("")

A("## T6  Layer-band / component counterfactuals (post-hoc, diagnostic only)\n")
for case in CASES:
    A(f"\n**{case}**\n")
    A(HEAD)
    A(SEP)
    for cand in ("CAND-A", "CAND-B"):
        w = WIDTH[cand]
        A(row_from_headline(f"{cand} frozen (w{w}, persist2)", AUDIT["cases"][case][cand]["width_sweep"][f"w{w}|persist2"]))
    for key, h in BAND[case].items():
        A(row_from_headline(key, h))
A("")

A("## T7  Alpha sweep: how much latency can be bought with false alarms\n")
for case in CASES:
    A(f"\n**{case}**\n")
    A("| candidate | reading | alpha | FAR_all | benign | resist | R+4 | R+8 | lat med |")
    A("|---|---|---|---|---|---|---|---|---|")
    for cand in ("CAND-A", "CAND-B"):
        for reading, sweep in AUDIT["cases"][case][cand]["alpha_sweep"].items():
            for r in sweep:
                if r["alpha"] not in (0.04, 0.08, 0.10, 0.15, 0.20, 0.30):
                    continue
                A(
                    f"| {cand} | {reading} | {r['alpha']:.2f} | {fmt(r['far_all'])} | {fmt(r['far_benign'])} | "
                    f"{fmt(r['far_resist'])} | {fmt(r['r4'])} | {fmt(r['r8'])} | {fmt(r['median_latency'],1)} |"
                )
A("")

# ---------------------------------------------------------------------------
# per-trace CSV + representative rows
fields = [
    "candidate", "case", "trace_id", "arm", "domain", "channel", "workflow", "decode_len",
    "onset", "completion_boundary", "cal_half", "threshold", "routine_median_z",
    "pre_onset_alarm", "hit", "latency", "alarm_end", "t_median_cross", "t_raw_cross",
    "comp_rise", "comp_margin", "comp_persist", "window_fill_at_alarm",
    "window_fill_at_raw_cross", "z_at_onset", "alarm_token", "mechanism",
    "alarm_snippet", "onset_snippet", "traj_ends", "traj_z", "traj_stat",
]
with (OUT / "per_trace_timing.csv").open("w", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for case in CASES:
        for cand in ("CAND-A", "CAND-B"):
            for rec in AUDIT["cases"][case][cand]["timing"]:
                rec = dict(rec)
                rec["mechanism"] = mechanism(rec)
                writer.writerow(rec)

(OUT / "tables.md").write_text("\n".join(lines))
print("\n".join(lines))
print("\nwrote", OUT / "tables.md", "and", OUT / "per_trace_timing.csv")
