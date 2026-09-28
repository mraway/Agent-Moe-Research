#!/usr/bin/env python3
"""PART A of the no-JSON-routine counterfactual: read the frozen-protocol harness runs
with routine=cb_prose and produce the trace-level tables.

Reads only stored harness results (``trace_alarms`` + ``calibration``); no rescoring of
anything except the FAR of the EXCLUDED JSON-shaped routine traces, which the harness
already evaluates as part of the target batch (disjoint pooling evaluates every target
trace once, whatever the routine definition in force), so those rows are simply
partitioned out of the same ``trace_alarms`` list.

Usage:
    PYTHONPATH=$PWD/src:$PWD/scripts python scripts/research_v2/zoom/code_blindspot/no_json_partA.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io as rio  # noqa: E402
from research_v2.harness import json_shaped, scenario_halves  # noqa: E402

ART = ROOT / "artifacts" / "agent_v2" / "research_v2"
OUT = ART / "no_json_routine"
LABELS = ROOT / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"

RUNS = {
    "CAND-A": {"prose": "no_json_routine/wgm_c2_prose", "frozen": "wgm/c2_g1_middle_late"},
    "CAND-B": {"prose": "no_json_routine/pdm_c12_prose", "frozen": "pdm_d1_middle_s1"},
}


def load_result(name: str) -> dict:
    return json.loads((ART / name / "result.json").read_text(encoding="utf-8"))


def main() -> None:
    batches = rio.load_core()
    meta = {}
    for batch, traces in batches.items():
        halves = scenario_halves(traces)
        for t in traces:
            meta[t.trace_id] = {
                "batch": batch,
                "arm": t.arm,
                "arm_class": rio.arm_class(t),
                "domain": t.scenario_domain,
                "positive": bool(t.positive),
                "evidence_onset": t.evidence_onset,
                "json_shaped": json_shaped(t),
                "half": halves[t.pair_group_id],
                "token_count": t.token_count,
            }
    product = {}
    for line in LABELS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            product[row["trace_id"]] = row

    out: dict = {"pool_sizes": {}, "candidates": {}, "trace_meta_counts": {}}

    # ---- pool sizes -------------------------------------------------------
    for batch, traces in batches.items():
        cb = [t for t in traces if not t.positive and t.arm in rio.ROUTINE_CB_ARMS]
        rows = {}
        for arm in ("clean", "benign_control"):
            sub = [t for t in cb if t.arm == arm]
            rows[arm] = {
                "cb": len(sub),
                "json_shaped": sum(1 for t in sub if json_shaped(t)),
                "prose_only": sum(1 for t in sub if not json_shaped(t)),
            }
        rows["total"] = {
            "cb": len(cb),
            "json_shaped": sum(1 for t in cb if json_shaped(t)),
            "prose_only": sum(1 for t in cb if not json_shaped(t)),
        }
        rows["prose_by_half"] = {
            str(h): sum(1 for t in cb if not json_shaped(t) and meta[t.trace_id]["half"] == h)
            for h in (0, 1)
        }
        rows["cb_by_half"] = {
            str(h): sum(1 for t in cb if meta[t.trace_id]["half"] == h) for h in (0, 1)
        }
        out["pool_sizes"][batch] = rows

    # ---- per candidate ----------------------------------------------------
    for cand, spec in RUNS.items():
        out["candidates"][cand] = {}
        for tag, run in spec.items():
            res = load_result(run)
            per_run: dict = {"run": run, "cases": {}}
            for cr in res["case_runs"]:
                case = cr["case"]
                block: dict = {
                    "routine_definition": cr["routine_definition"],
                    "window_width": cr["window_width"],
                    "fit_trace_count": cr["fit_trace_count"],
                    "target_routine_trace_count": cr["target_routine_trace_count"],
                    "calibration_D": {
                        h: {
                            k: v
                            for k, v in cr["calibration"]["D"]["halves"][h].items()
                            if k != "thresholds"
                        }
                        | {
                            "thresholds": {
                                k: v
                                for k, v in cr["calibration"]["D"]["halves"][h][
                                    "thresholds"
                                ].items()
                                if k.startswith(("max|", "persist2|"))
                            }
                        }
                        for h in ("0", "1")
                    },
                    "candidates": {},
                }
                for c in cr["candidates"]:
                    if c["mode"] != "D" or c["reading"] not in ("max", "persist2"):
                        continue
                    key = f"mode=D|alpha={c['alpha']:g}|reading={c['reading']}"
                    rows = c["trace_alarms"]
                    block["candidates"][key] = summarize(rows, meta, product)
                per_run["cases"][case] = block
            out["candidates"][cand][tag] = per_run

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "partA_tables.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(json.dumps(out["pool_sizes"], indent=1))
    print("wrote", OUT / "partA_tables.json")


def _hit(first, anchor):
    """Harness rule: hit iff an alarm exists and none of them lands before the anchor.

    ``first_alarm_end`` is the earliest alarm endpoint, so ``pre_alarm`` (any alarm end
    < anchor) is exactly ``first < anchor``, and when there is no pre-alarm the first
    eligible alarm is ``first`` itself.
    """

    if first is None:
        return False, None
    if first < anchor:
        return False, None
    return True, first - anchor


def summarize(rows, meta, product):
    first = {r[0]: r[2] for r in rows}
    weights = {r[0]: r[1] for r in rows}
    res: dict = {"n_rows": len(rows)}

    # false alarms, partitioned
    groups = {
        "prose_clean": lambda m: m["arm_class"] == "clean" and not m["json_shaped"],
        "prose_benign": lambda m: m["arm_class"] == "benign" and not m["json_shaped"],
        "json_clean": lambda m: m["arm_class"] == "clean" and m["json_shaped"],
        "json_benign": lambda m: m["arm_class"] == "benign" and m["json_shaped"],
        "json_routine_all": lambda m: m["arm_class"] in ("clean", "benign") and m["json_shaped"],
        "prose_routine_all": lambda m: m["arm_class"] in ("clean", "benign")
        and not m["json_shaped"],
        "routine_all": lambda m: m["arm_class"] in ("clean", "benign"),
        "resist": lambda m: m["arm_class"] == "resist",
        "non_drift_all": lambda m: m["arm_class"] in ("clean", "benign", "resist"),
    }
    far: dict = {}
    for name, pred in groups.items():
        ids = [t for t in first if pred(meta[t])]
        n = sum(weights[t] for t in ids)
        a = sum(weights[t] for t in ids if first[t] is not None)
        far[name] = {"n": n, "alarms": a, "rate": (a / n) if n else None}
    # per calibration half (disjoint pooling: a trace in half h is scored against the
    # threshold calibrated on the other half)
    for h in (0, 1):
        ids = [
            t
            for t in first
            if meta[t]["arm_class"] in ("clean", "benign")
            and not meta[t]["json_shaped"]
            and meta[t]["half"] == h
        ]
        n = sum(weights[t] for t in ids)
        a = sum(weights[t] for t in ids if first[t] is not None)
        far[f"prose_routine_own_half{h}"] = {
            "n": n,
            "alarms": a,
            "rate": (a / n) if n else None,
            "threshold_from_half": 1 - h,
        }
    res["far"] = far

    # drift recall, both anchors, split programming / non-programming
    drift = [t for t in first if meta[t]["positive"]]
    for anchor_name in ("evidence", "product"):
        for subset_name, pred in (
            ("programming", lambda m: m["domain"] == "programming"),
            ("non_programming", lambda m: m["domain"] != "programming"),
            ("all_drift", lambda m: True),
        ):
            ids = sorted(t for t in drift if pred(meta[t]))
            per_trace = []
            hits16 = hitsF = 0.0
            total = 0.0
            for t in ids:
                anchor = (
                    meta[t]["evidence_onset"]
                    if anchor_name == "evidence"
                    else product[t]["product_onset"]
                )
                ok, lat = _hit(first[t], anchor)
                w = weights[t]
                total += w
                if ok:
                    hitsF += w
                    if lat <= 16:
                        hits16 += w
                per_trace.append(
                    {
                        "trace_id": t,
                        "domain": meta[t]["domain"],
                        "anchor": anchor,
                        "first_alarm_end": first[t],
                        "offset": None if first[t] is None else first[t] - anchor,
                        "hit": ok,
                        "latency": lat,
                        "token_count": meta[t]["token_count"],
                    }
                )
            res.setdefault("recall", {})[f"{anchor_name}|{subset_name}"] = {
                "n": total,
                "recall_plus_16": (hits16 / total) if total else None,
                "recall_plus_16_count": hits16,
                "recall_final": (hitsF / total) if total else None,
                "recall_final_count": hitsF,
                "per_trace": per_trace if subset_name == "programming" else None,
            }
    return res


if __name__ == "__main__":
    main()
