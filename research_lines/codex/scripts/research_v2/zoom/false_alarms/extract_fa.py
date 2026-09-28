from __future__ import annotations
import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
from fa_core import *  # noqa
from research_v2 import io as rio

CASES = {"b1_to_b2": "b2", "b2_to_b1": "b1"}
OUT = f"{ROOT}/artifacts/agent_v2/research_v2/zoom/false_alarms"

def tertile_edges(traces):
    lens = np.array(sorted(t.token_count for t in traces))
    return float(np.quantile(lens, 1/3)), float(np.quantile(lens, 2/3))

def main():
    core = rio.load_core()
    out = []
    for key in ("A", "B"):
        w = CAND[key]["w"]
        for case, tgt in CASES.items():
            cr, streams = load_case(key, case)
            traces = list(core[tgt])
            by_id = {t.trace_id: t for t in traces}
            edges = tertile_edges(traces)
            rows, info = evaluate_rule(lambda: BaselineRule(0.10), traces, streams)
            for r in rows:
                if r.positive or r.first_alarm_end is None:
                    continue
                t = by_id[r.trace_id]
                toks = rio.decode_token_texts(t.token_ids.tolist())
                fa = r.first_alarm_end
                lo, hi = max(0, fa - 32), min(len(toks), fa + 33)
                ctx = "".join(toks[lo:hi])
                win = "".join(toks[max(0, fa - w + 1): fa + 1])
                tier = 0 if t.token_count <= edges[0] else (1 if t.token_count <= edges[1] else 2)
                out.append(dict(cand=key, case=case, trace_id=r.trace_id, arm=r.arm_class,
                                raw_arm=r.arm, workflow=r.workflow, domain=r.domain,
                                channel=r.channel, length=t.token_count, tier=tier,
                                first_alarm_end=fa, rel_pos=round(fa / t.token_count, 3),
                                excursion=r.excursion_len, n_alarm_windows=len(r.alarm_ends),
                                peak_margin=round(r.peak_margin, 3),
                                threshold=round(r.threshold_at_first, 3),
                                window_text=win, context=ctx))
    with open(f"{OUT}/false_alarm_cases.json", "w") as fh:
        json.dump({"tertile_note": "tertiles computed over all target-batch traces",
                   "cases": out}, fh, indent=1, ensure_ascii=False)
    print(len(out), "false-alarm rows")
    for o in out:
        print("=" * 100)
        print(f"[{o['cand']}|{o['case']}] {o['trace_id']} arm={o['arm']} wf={o['workflow']} dom={o['domain']} "
              f"len={o['length']} tier={o['tier']} first={o['first_alarm_end']} rel={o['rel_pos']} "
              f"exc={o['excursion']} nwin={o['n_alarm_windows']} peak={o['peak_margin']} thr={o['threshold']}")
        print("  WIN: " + repr(o["window_text"]))
        print("  CTX: " + repr(o["context"]))

main()
