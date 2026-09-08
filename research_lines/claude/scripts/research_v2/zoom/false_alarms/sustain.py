from __future__ import annotations
import json, sys, os, collections
sys.path.insert(0, os.path.dirname(__file__))
from fa_core import *  # noqa
from research_v2 import io as rio

CASES = {"b1_to_b2": "b2", "b2_to_b1": "b1"}
OUT = f"{ROOT}/artifacts/agent_v2/research_v2/zoom/false_alarms"

def main():
    core = rio.load_core()
    cases_json = json.load(open(f"{OUT}/false_alarm_cases.json"))
    cls = {(r["cand"], r["case"], r["trace_id"]): r["class"] for r in cases_json["cases"]}
    dump = {}
    print("## Sustained-excursion gate at the FIXED alpha=0.10 threshold (FAR can only shrink)")
    print("| cand | case | rule | FAR | clean | benign | resist | +8 | +16 | final | lat | FA classes kept |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for key in ("A", "B"):
        w = CAND[key]["w"]
        for case, tgt in CASES.items():
            traces = list(core[tgt]); cr, streams = load_case(key, case)
            variants = [("baseline persist2", lambda: BaselineRule(0.10)),
                        ("max (h only)", lambda w=w: FixedThresholdSustainRule(w, 1, 1, 0.10, "max")),
                        ("block 1of1", lambda w=w: FixedThresholdSustainRule(w, 1, 1, 0.10, "max")),
                        ("block 2of2", lambda w=w: FixedThresholdSustainRule(w, 2, 2, 0.10, "max")),
                        ("block 2of3", lambda w=w: FixedThresholdSustainRule(w, 2, 3, 0.10, "max")),
                        ("block 3of4", lambda w=w: FixedThresholdSustainRule(w, 3, 4, 0.10, "max")),
                        ("persist2 run2", lambda w=w: FixedThresholdSustainRule(w, base="persist2", consecutive=2)),
                        ("persist2 run4", lambda w=w: FixedThresholdSustainRule(w, base="persist2", consecutive=4)),
                        ("persist2 run8", lambda w=w: FixedThresholdSustainRule(w, base="persist2", consecutive=8)),
                        ]
            for name, f in variants:
                if name == "max (h only)":
                    continue
                rows, _ = evaluate_rule(f, traces, streams)
                m = metrics(rows)
                cc = collections.Counter(cls.get((key, case, r.trace_id), "?")
                                         for r in rows if not r.positive and r.first_alarm_end is not None)
                dump[f"{key}|{case}|{name}"] = {k: v for k, v in m.items() if k != "fa_trace_ids"}
                dump[f"{key}|{case}|{name}"]["fa_classes"] = dict(cc)
                dump[f"{key}|{case}|{name}"]["fa_trace_ids"] = sorted(r.trace_id for r in rows if not r.positive and r.first_alarm_end is not None)
                print(f"| {key} | {case} | {name} | {m['far']:.3f} ({m['fa_total']}/{m['n_non_drift']}) | "
                      f"{m['far_clean']:.3f} | {m['far_benign']:.3f} | {m['far_resist']:.3f} | "
                      f"{m['recall_8']:.3f} | {m['recall_16']:.3f} | {m['recall_final']:.3f} | {m['median_latency']} | "
                      + "".join(f"{k}{v} " for k, v in sorted(cc.items())) + "|")
            print("|" + "---|" * 12)
    json.dump(dump, open(f"{OUT}/sustain.json", "w"), indent=1, default=str)

main()
