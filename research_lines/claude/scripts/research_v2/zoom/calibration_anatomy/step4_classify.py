"""Step 7: shape classification of the threshold-setting windows + drift margin case table."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import CANDIDATES, OUT, decode_window, load_case_streams, rio, target_batch_of  # noqa

PROTOCOL = re.compile(r'\{"type"\s*:\s*"(action|assistant|tool_result|answer)"|"arguments"\s*:|\{"ok"\s*:|provenance"\s*:|"article_id')
JSON_FIELD = re.compile(r'"[A-Za-z_][\w ]*"\s*:\s*(\{|\[|"|true|false|-?\d)')


def json_shaped(win: str) -> bool:
    """A JSON action/tool envelope: several quoted fields plus a brace in the same window."""
    return len(JSON_FIELD.findall(win)) >= 3 and ("{" in win or "}" in win)
DISCLAIM = re.compile(
    r"inert label|evaluation label|should not create|is not relevant|unrelated to|"
    r"beyond the scope|should be disregarded|no fiction scene is requested|"
    r"not intended for real-world|do not (?:create|produce)|labels only|"
    r"this does not authorize|are used by", re.I)
REFUSAL = re.compile(r"i can'?t|i cannot|i'?m not able|unable to (?:help|assist)|cannot assist", re.I)


def degenerate(win: str) -> bool:
    if re.search(r"(\\n){6,}", win):
        return True
    if re.search(r"(\n\s*){8,}", win):
        return True
    lines = [l.strip() for l in win.splitlines() if l.strip()]
    return bool(lines) and max(lines.count(l) for l in set(lines)) >= 4


def classify(win: str) -> tuple[str, list[str]]:
    tags = []
    if degenerate(win):
        tags.append("degenerate")
    if DISCLAIM.search(win) or REFUSAL.search(win):
        tags.append("offtopic_disclaimer")
    if PROTOCOL.search(win) or json_shaped(win):
        tags.append("protocol_json")
    if not tags:
        tags.append("ordinary_prose")
    for primary in ("degenerate", "offtopic_disclaimer", "protocol_json", "ordinary_prose"):
        if primary in tags:
            return primary, tags
    raise AssertionError


def main():
    top = json.loads((OUT / "step1_top_tail.json").read_text(encoding="utf-8"))
    b1p = json.loads((OUT / "step3_b1present.json").read_text(encoding="utf-8"))
    counts = {}
    table = []
    for key, blk in top.items():
        for case, dblk in blk["directions"].items():
            for half, hb in dblk["halves"].items():
                for r in hb["top"]:
                    primary, tags = classify(r["snippet"])
                    row = {"pool": "frozen", "candidate": key, "direction": case,
                           "cal_half": int(half), **r,
                           "primary_class": primary, "tags": tags,
                           "threshold": hb["threshold"], "n_cal": hb["n_calibration"],
                           "setter_index_from_top": hb["setter_index_from_top"]}
                    table.append(row)
                    if r["rank_from_top"] <= 5:
                        counts.setdefault(("frozen", key, case), {}).setdefault(primary, 0)
                        counts[("frozen", key, case)][primary] += 1
    for key, blk in b1p.items():
        for half, hb in blk["halves"].items():
            for r in hb["top"]:
                primary, tags = classify(r["snippet"])
                row = {"pool": "b1_present", "candidate": key, "direction": "b2_to_b1",
                       "cal_half": int(half), **r, "primary_class": primary, "tags": tags,
                       "threshold": hb["threshold"], "n_cal": hb["n_calibration"],
                       "setter_index_from_top": hb["setter_index_from_top"]}
                table.append(row)
                if r["rank_from_top"] <= 5:
                    counts.setdefault(("b1_present", key, "b2_to_b1"), {}).setdefault(primary, 0)
                    counts[("b1_present", key, "b2_to_b1")][primary] += 1
    flat = {"|".join(k): v for k, v in counts.items()}
    # aggregate over the eight frozen top-5 lists
    agg = {}
    arm_agg = {}
    for row in table:
        if row["pool"] == "frozen" and row["rank_from_top"] <= 5:
            agg[row["primary_class"]] = agg.get(row["primary_class"], 0) + 1
            arm_agg[row["arm"]] = arm_agg.get(row["arm"], 0) + 1
    setters = [r for r in table if r["is_threshold_setter"]]
    payload = {"per_list_top5_counts": flat, "frozen_top5_aggregate": agg,
               "frozen_top5_arm_aggregate": arm_agg,
               "threshold_setters": setters, "rows": table}
    (OUT / "step4_classification.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("frozen top-5 aggregate (8 lists x 5 = 40):", agg)
    print("frozen top-5 arm aggregate:", arm_agg)
    for k, v in flat.items():
        print(" ", k, v)
    print("\nthreshold setters:")
    for s in setters:
        print(f"  {s['pool']:>10} {s['candidate']} {s['direction']} half{s['cal_half']} "
              f"h={s['threshold']:.3f} {s['trace_id']} arm={s['arm']} len={s['decode_len']} "
              f"class={s['primary_class']}")

    # ---- drift margin case table -----------------------------------------------
    exp = json.loads((OUT / "step2_experiments.json").read_text(encoding="utf-8"))
    batches = rio.load_core()
    cases = []
    for key, cfg in CANDIDATES.items():
        for case in ("b1_to_b2", "b2_to_b1"):
            payload_, run, streams = load_case_streams(cfg["result"], case, cfg["window_width"])
            tmap = {t.trace_id: t for t in batches[target_batch_of(case)]}
            for m in exp[key]["directions"][case]["margins"]["rows"]:
                t = tmap[m["trace_id"]]
                sc, en = streams[t.trace_id]
                en = en.numpy()
                post = en[en >= t.evidence_onset]
                cases.append({
                    "candidate": key, "direction": case, "cal_half": m["cal_half"],
                    "trace_id": t.trace_id, "arm": t.arm, "domain": t.scenario_domain,
                    "channel": t.channel, "workflow": t.workflow,
                    "decode_len": t.token_count, "onset": t.evidence_onset,
                    "threshold": m["threshold"], "post_onset_max": m["post_max"],
                    "margin": m["margin"], "hit": m["hit"], "pre_alarm": m["pre_alarm"],
                    "latency": m["latency"],
                    "snippet_at_onset": decode_window(t, int(t.evidence_onset), 32),
                })
    (OUT / "step4_drift_margins.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\nclean misses (no pre-onset alarm, no post-onset alarm):")
    for c in sorted([c for c in cases if not c["hit"] and not c["pre_alarm"]],
                    key=lambda c: -c["margin"]):
        print(f"  {c['candidate']} {c['direction']} h{c['cal_half']} {c['trace_id']} "
              f"{c['domain']}/{c['workflow']} len={c['decode_len']} onset={c['onset']} "
              f"max={c['post_onset_max']:.3f} h={c['threshold']:.3f} margin={c['margin']:+.3f}")
            
    print("\ntightest hits (margin < 1.0):")
    for c in sorted([c for c in cases if c["hit"] and c["margin"] < 1.0], key=lambda c: c["margin"]):
        print(f"  {c['candidate']} {c['direction']} h{c['cal_half']} {c['trace_id']} "
              f"{c['domain']} len={c['decode_len']} onset={c['onset']} margin={c['margin']:+.3f} lat={c['latency']}")


if __name__ == "__main__":
    main()
