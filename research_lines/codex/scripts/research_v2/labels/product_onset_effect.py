#!/usr/bin/env python3
"""Anchor-swap evaluation: evidence_onset vs product_onset for the two frozen candidates.

Pure re-reading of the SAVED score streams under artifacts/agent_v2/research_v2/{wgm,pdm_d1_middle_s1}.
No scorer is re-run; the detector is untouched.  Only the evaluation anchor changes.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import torch

ROOT = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
sys.path.insert(0, str(ROOT / "scripts" / "research_v2" / "zoom" / "missed_drift"))

from common import ART, cases_for, load_result, mode_d_rows, streams_from_result  # noqa: E402
from research_v2 import io as rio  # noqa: E402

torch.set_num_threads(8)

CANDIDATES = {
    "CAND-A": (ART / "wgm" / "c2_g1_middle_late" / "result.json", 8),
    "CAND-B": (ART / "pdm_d1_middle_s1" / "result.json", 4),
}
LAB = ROOT / "docs" / "research_v2" / "labels"
HORIZONS = (4, 8, 16)


def read_jsonl(p: Path) -> dict[str, dict]:
    return {json.loads(l)["trace_id"]: json.loads(l)
            for l in p.read_text(encoding="utf-8").splitlines() if l.strip()}


def anchor_block(ends: torch.Tensor, alarm_ends: torch.Tensor, anchor: int) -> dict:
    """Identical to harness._anchor_block with band=0."""
    pre_eligible = bool((ends < anchor).any())
    pre_alarm = bool((alarm_ends < anchor).any())
    eligible = alarm_ends[alarm_ends >= anchor]
    first = int(eligible[0]) if eligible.numel() else None
    latency = None if first is None else max(0, first - anchor)
    return {
        "anchor": anchor,
        "pre_eligible": pre_eligible,
        "pre_alarm": pre_alarm,
        "first_alarm_end": first,
        "latency": latency,
        "hit": (not pre_alarm) and first is not None,
        "reachable": {str(h): bool(((ends >= anchor) & (ends <= anchor + h)).any())
                      for h in (4, 8, 16, 32)},
    }


def metrics_from_blocks(blocks: list[dict]) -> dict:
    total = len(blocks)
    pre_elig = sum(1 for b in blocks if b["pre_eligible"])
    pre_alarm = sum(1 for b in blocks if b["pre_alarm"])
    out = {
        "drift_trace_count": total,
        "pre_alarm_denominator": pre_elig,
        "pre_alarm_count": pre_alarm,
        "pre_alarm_rate": (pre_alarm / pre_elig) if pre_elig else None,
        "pre_alarm_rate_all_drift": (pre_alarm / total) if total else None,
    }
    for h in HORIZONS:
        hits = sum(1 for b in blocks
                   if b["hit"] and b["latency"] is not None and b["latency"] <= h)
        out[f"recall_plus_{h}_count"] = hits
        out[f"recall_plus_{h}"] = hits / total if total else None
        out[f"reachable_plus_{h}"] = (sum(1 for b in blocks if b["reachable"][str(h)]) / total
                                      if total else None)
    final = sum(1 for b in blocks if b["hit"])
    out["recall_final_count"] = final
    out["recall_final"] = final / total if total else None
    lat = [b["latency"] for b in blocks if b["hit"] and b["latency"] is not None]
    out["median_latency"] = float(statistics.median(lat)) if lat else None
    out["latencies"] = sorted(lat)
    return out


def main() -> None:
    A = read_jsonl(LAB / "product_onset_v1_A.jsonl")
    ADJ = read_jsonl(LAB / "product_onset_v1_adjudicated_draft.jsonl")
    anchors = {
        "evidence_onset": None,  # use trace.evidence_onset
        "product_onset_A": {k: v["product_onset"] for k, v in A.items()},
        "product_onset_adjudicated": {k: v["product_onset"] for k, v in ADJ.items()},
    }

    batches = rio.load_core()
    cases = cases_for(batches)
    frozen = {
        "CAND-A": {"b1_to_b2": {"far": 0.098, "r8": 0.429, "r16": 0.600},
                   "b2_to_b1": {"far": 0.115, "r8": 0.500, "r16": 0.625}},
        "CAND-B": {"b1_to_b2": {"far": 0.078, "r8": 0.486, "r16": 0.514},
                   "b2_to_b1": {"far": 0.083, "r8": 0.500, "r16": 0.625}},
    }
    report: dict = {
        "what_this_is": (
            "Evaluation-anchor swap only. The detectors, their frozen configs, the saved "
            "score streams, the mode-D bucket standardization and the conformal thresholds "
            "are byte-identical across all three columns; only the per-trace anchor used by "
            "the strict onset metrics changes. This is NOT a detector improvement."
        ),
        "candidates": {},
    }

    for key, (path, width) in CANDIDATES.items():
        result = load_result(path)
        report["candidates"][key] = {"result_json": str(path), "window_width": width, "cases": {}}
        for case_run in result["case_runs"]:
            if (case_run["split"] != "S1" or case_run["window_width"] != width
                    or case_run["routine_definition"] != "cb"):
                continue
            case = cases[case_run["case"]]
            streams = streams_from_result(case_run)
            rows, detail = mode_d_rows(case, streams)

            # verification against the stored harness alarms
            stored = {r[0]: r for r in next(
                c for c in case_run["candidates"]
                if c["candidate_id"].endswith("mode=D|alpha=0.1|reading=persist2")
            )["trace_alarms"]}
            mismatch = []
            for row in rows:
                s = stored.get(row["trace_id"])
                if s is None:
                    mismatch.append([row["trace_id"], "missing"])
                elif s[2] != row["first_alarm_end"]:
                    mismatch.append([row["trace_id"], s[2], row["first_alarm_end"]])

            negatives = [r for r in rows if not r["positive"]]
            far = sum(1 for r in negatives if r["false_alarm"]) / len(negatives)
            by_arm = {}
            for name in ("clean", "benign", "resist"):
                sel = [r for r in negatives if r["arm_class"] == name]
                by_arm[name] = (sum(1 for r in sel if r["false_alarm"]) / len(sel)) if sel else None

            drift = [r for r in rows if r["positive"]]
            # rebuild alarm ends per drift trace from the reproduced statistic stream
            per_trace = {}
            for row in drift:
                d = detail[row["trace_id"]]
                ends = d["ends"]
                alarm_ends = ends[d["stat"] >= d["threshold"]]
                per_trace[row["trace_id"]] = (ends, alarm_ends)

            case_block = {
                "n_drift": len(drift),
                "n_negative": len(negatives),
                "verification_mismatches": mismatch,
                "non_drift_false_alarm_rate": far,
                "false_alarm_rate_by_arm": by_arm,
                "anchors": {},
                "moved": [],
            }

            blocks_by_anchor = {}
            anchor_values = {}
            for aname, amap in anchors.items():
                blocks = []
                avals = {}
                for row in drift:
                    tid = row["trace_id"]
                    ends, alarm_ends = per_trace[tid]
                    a = row["evidence_onset"] if amap is None else amap[tid]
                    avals[tid] = int(a)
                    blocks.append((tid, anchor_block(ends, alarm_ends, int(a))))
                blocks_by_anchor[aname] = dict(blocks)
                anchor_values[aname] = avals
                case_block["anchors"][aname] = metrics_from_blocks([b for _, b in blocks])

            # per-trace deltas for traces whose anchor moved (evidence -> adjudicated)
            for row in drift:
                tid = row["trace_id"]
                ev = anchor_values["evidence_onset"][tid]
                pa = anchor_values["product_onset_A"][tid]
                pj = anchor_values["product_onset_adjudicated"][tid]
                if ev == pa and ev == pj:
                    continue
                be = blocks_by_anchor["evidence_onset"][tid]
                bp = blocks_by_anchor["product_onset_adjudicated"][tid]

                def status(b):
                    if b["pre_alarm"]:
                        return "pre_onset_disqualified"
                    if b["first_alarm_end"] is None:
                        return "no_alarm"
                    return f"hit16" if b["latency"] <= 16 else "late_alarm"

                se, sp = status(be), status(bp)
                if se == sp and be["latency"] == bp["latency"]:
                    change = "unchanged"
                elif se != "hit16" and sp == "hit16":
                    change = "became_hit16"
                elif se == "hit16" and sp != "hit16":
                    change = "lost_hit16"
                elif se != "hit16" and sp != "hit16":
                    change = "stayed_miss"
                else:
                    change = "latency_change"
                ends_all, alarm_all = per_trace[tid]
                global_first = int(alarm_all[0]) if alarm_all.numel() else None
                case_block["moved"].append({
                    "trace_id": tid,
                    "first_alarm_end_global": global_first,
                    "pre_alarm_evidence": be["pre_alarm"],
                    "pre_alarm_product": bp["pre_alarm"],
                    "domain": row["domain"],
                    "channel": row["channel"],
                    "evidence_onset": ev,
                    "product_onset_A": pa,
                    "product_onset_adjudicated": pj,
                    "delta": ev - pj,
                    "first_eligible_alarm_evidence": be["first_alarm_end"],
                    "first_eligible_alarm_product": bp["first_alarm_end"],
                    "status_evidence": se,
                    "status_product": sp,
                    "latency_evidence": be["latency"],
                    "latency_product": bp["latency"],
                    "latency_delta": (None if be["latency"] is None or bp["latency"] is None
                                      else bp["latency"] - be["latency"]),
                    "change": change,
                })

            report["candidates"][key]["cases"][case_run["case"]] = case_block
            fz = frozen[key][case_run["case"]]
            ev = case_block["anchors"]["evidence_onset"]
            print(f"{key} {case_run['case']}: mismatch={len(mismatch)} "
                  f"FAR={far:.3f}(frozen {fz['far']}) "
                  f"R8={ev['recall_plus_8']:.3f}(frozen {fz['r8']}) "
                  f"R16={ev['recall_plus_16']:.3f}(frozen {fz['r16']}) "
                  f"lat={ev['median_latency']}", flush=True)

    # ---- inter-annotator agreement on the 15 review traces -------------------
    Bl = read_jsonl(LAB / "product_onset_v1_B.jsonl")
    sample = [l.strip() for l in (LAB / "review_sample_15.txt").read_text().splitlines() if l.strip()]
    diffs = [abs(A[t]["product_onset"] - Bl[t]["product_onset"]) for t in sample]
    report["agreement"] = {
        "n": len(sample),
        "exact": sum(1 for d in diffs if d == 0),
        "within_2": sum(1 for d in diffs if d <= 2),
        "within_5": sum(1 for d in diffs if d <= 5),
        "median_abs_diff": float(statistics.median(diffs)),
        "max_abs_diff": max(diffs),
        "product_class_agreement": sum(1 for t in sample
                                       if A[t]["product_class"] == Bl[t]["product_class"]),
        "rows_with_any_field_disagreement": [
            t for t in sample
            if any(A[t].get(k) != Bl[t].get(k) for k in
                   ("product_onset", "product_class", "announcement_onset",
                    "announcement_text", "confidence", "flag"))
        ],
        "note": ("product_onset agrees exactly on all 15 review traces, so the "
                 "'product_onset_A' and 'product_onset_adjudicated' columns below are "
                 "numerically identical by construction; the two adjudications touch only "
                 "confidence / announcement_onset / flag."),
    }

    # ---- anchor-swap deltas ---------------------------------------------------
    keys = ("pre_alarm_rate", "recall_plus_4", "recall_plus_8", "recall_plus_16",
            "recall_final", "median_latency")
    for cand in report["candidates"].values():
        for cb in cand["cases"].values():
            base = cb["anchors"]["evidence_onset"]
            cb["delta_vs_evidence_onset"] = {
                an: {k: (None if base[k] is None or m[k] is None else round(m[k] - base[k], 6))
                     for k in keys}
                for an, m in cb["anchors"].items() if an != "evidence_onset"
            }
            cb["identical_A_vs_adjudicated"] = (
                cb["anchors"]["product_onset_A"] == cb["anchors"]["product_onset_adjudicated"]
            )

    out = ART / "labels" / "product_onset_effect.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    print("written", out)


if __name__ == "__main__":
    main()
