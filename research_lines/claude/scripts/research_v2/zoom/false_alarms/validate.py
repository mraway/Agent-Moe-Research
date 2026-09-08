from __future__ import annotations
import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from fa_core import *  # noqa
from research_v2 import io as rio

CASES = {"b1_to_b2": "b2", "b2_to_b1": "b1"}

def main():
    core = rio.load_core()
    for key in ("A", "B"):
        for case, tgt in CASES.items():
            cr, streams = load_case(key, case)
            traces = [t for t in core[tgt]]
            rows, info = evaluate_rule(lambda: BaselineRule(0.10), traces, streams)
            m = metrics(rows)
            cid = f"S1:{case}|w{CAND[key]['w']}|routine=cb|mode=D|alpha=0.1|reading=persist2"
            ref = [c for c in cr["candidates"] if c["candidate_id"] == cid][0]["metrics"]
            print(f"== {key} {case}")
            print(f"  repro FAR {m['far']:.4f} ({m['fa_total']}/{m['n_non_drift']})  ref {ref['non_drift_false_alarm_rate']:.4f} ({ref['non_drift_false_alarm_count']}/{ref['non_drift_trace_count']})")
            for a in ("clean","benign","resist"):
                print(f"    {a}: repro {m['fa_'+a]}/{m['n_'+a]}  ref {ref['by_arm'][a]['false_alarm_count']}/{ref['by_arm'][a]['trace_count']}")
            o = ref["onset_strict"]
            print(f"  recall+8 repro {m['recall_8_count']}/{m['n_drift']} ref {o['recall_plus_8_count']}/{o['trace_count']}; +16 {m['recall_16_count']} vs {o['recall_plus_16_count']}; final {m['recall_final_count']} vs {o['recall_final_count']}; medlat {m['median_latency']} vs {o['median_latency']}")

main()
