from __future__ import annotations
import json, sys, os, collections
sys.path.insert(0, os.path.dirname(__file__))
from fa_core import *  # noqa
from research_v2 import io as rio

CASES = {"b1_to_b2": "b2", "b2_to_b1": "b1"}
OUT = f"{ROOT}/artifacts/agent_v2/research_v2/zoom/false_alarms"

def main():
    core = rio.load_core()
    dump = {}
    print("## divgate variants (alpha=0.10)")
    print("| cand | case | min_distinct/w | FAR | clean | benign | resist | +8 | +16 | final | lat |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for key in ("A", "B"):
        w = CAND[key]["w"]
        for case, tgt in CASES.items():
            traces = list(core[tgt]); cr, streams = load_case(key, case)
            for md in range(2, w + 1):
                rows, _ = evaluate_rule(lambda md=md, w=w: DiversityGateRule(w, md, 0.10), traces, streams)
                m = metrics(rows)
                dump[f"divgate|{key}|{case}|{md}"] = {k: v for k, v in m.items() if k != "fa_trace_ids"}
                print(f"| {key} | {case} | {md}/{w} | {m['far']:.3f} ({m['fa_total']}/{m['n_non_drift']}) | "
                      f"{m['far_clean']:.3f} | {m['far_benign']:.3f} | {m['far_resist']:.3f} | "
                      f"{m['recall_8']:.3f} | {m['recall_16']:.3f} | {m['recall_final']:.3f} | {m['median_latency']} |")
    print("\n## peak-margin separation, baseline alpha=0.10 (persist2 z above threshold)")
    print("| cand | case | FA peak margin med / max | drift-hit peak margin med / min | FA excursion med (windows) | drift excursion med |")
    print("|---|---|---|---|---|---|")
    for key in ("A", "B"):
        for case, tgt in CASES.items():
            traces = list(core[tgt]); cr, streams = load_case(key, case)
            rows, _ = evaluate_rule(lambda: BaselineRule(0.10), traces, streams)
            fa = [r for r in rows if not r.positive and r.first_alarm_end is not None]
            dr = [r for r in rows if r.positive and r.first_alarm_end is not None
                  and not any(e < r.evidence_onset for e in r.alarm_ends)]
            fm = np.array([r.peak_margin for r in fa]); dm = np.array([r.peak_margin for r in dr])
            fe = np.array([r.excursion_len for r in fa]); de = np.array([r.excursion_len for r in dr])
            print(f"| {key} | {case} | {np.median(fm):.2f} / {fm.max():.2f} | {np.median(dm):.2f} / {dm.min():.2f} | "
                  f"{np.median(fe):.0f} | {np.median(de):.0f} |")
            dump[f"margins|{key}|{case}"] = {"fa_margins": fm.tolist(), "drift_margins": dm.tolist(),
                                             "fa_excursions": fe.tolist(), "drift_excursions": de.tolist()}
    json.dump(dump, open(f"{OUT}/extras.json", "w"), indent=1)

main()
