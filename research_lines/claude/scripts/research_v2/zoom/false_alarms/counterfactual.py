from __future__ import annotations
import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from fa_core import *  # noqa
from research_v2 import io as rio

CASES = {"b1_to_b2": "b2", "b2_to_b1": "b1"}
OUT = f"{ROOT}/artifacts/agent_v2/research_v2/zoom/false_alarms"


def far_by_tertile(rows, traces):
    lens = np.array([t.token_count for t in traces])
    e = (float(np.quantile(lens, 1/3)), float(np.quantile(lens, 2/3)))
    out = {"edges": e}
    for tier in (0, 1, 2):
        sub = [r for r in rows if not r.positive and
               (0 if r.token_count <= e[0] else (1 if r.token_count <= e[1] else 2)) == tier]
        n = len(sub)
        fa = sum(1 for r in sub if r.first_alarm_end is not None)
        out[f"tier{tier}"] = {"n": n, "fa": fa, "far": fa / n if n else None}
    return out


def main():
    core = rio.load_core()
    all_out = {}
    for key in ("A", "B"):
        w = CAND[key]["w"]
        rules = [
            ("baseline", lambda: BaselineRule(0.10)),
            ("length_tertile", lambda: LengthTertileRule(0.10)),
            ("anytime", lambda: AnytimeRule(0.10)),
            ("block_2of2", lambda w=w: BlockGateRule(w, 2, 2, 0.10)),
            ("block_2of3", lambda w=w: BlockGateRule(w, 2, 3, 0.10)),
            ("block_3of4", lambda w=w: BlockGateRule(w, 3, 4, 0.10)),
            ("anytime+block_2of3", lambda w=w: CombinedRule(w, 2, 3, 0.10)),
        ]
        for case, tgt in CASES.items():
            traces = list(core[tgt])
            cr, streams = load_case(key, case)
            for name, factory in rules:
                rows, info = evaluate_rule(factory, traces, streams)
                m = metrics(rows)
                m["far_by_tertile"] = far_by_tertile(rows, traces)
                m["fa_trace_ids"] = sorted(r.trace_id for r in rows
                                           if not r.positive and r.first_alarm_end is not None)
                m["thresholds"] = {str(h): (v if not isinstance(v, dict) else v) for h, v in info.items()}
                all_out[f"{key}|{case}|{name}"] = m
                print(f"{key} {case:10s} {name:20s} FAR {m['far']:.3f} ({m['fa_total']}/{m['n_non_drift']}) "
                      f"c/b/r {m['far_clean']:.3f}/{m['far_benign']:.3f}/{m['far_resist']:.3f} "
                      f"| +8 {m['recall_8']:.3f} +16 {m['recall_16']:.3f} fin {m['recall_final']:.3f} "
                      f"lat {m['median_latency']}")
            print()
    json.dump(all_out, open(f"{OUT}/counterfactuals.json", "w"), indent=1, default=str)

main()
