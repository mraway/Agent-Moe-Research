from __future__ import annotations
import json, sys, os, collections
sys.path.insert(0, os.path.dirname(__file__))
from fa_core import *  # noqa
from research_v2 import io as rio

CASES = {"b1_to_b2": "b2", "b2_to_b1": "b1"}
OUT = f"{ROOT}/artifacts/agent_v2/research_v2/zoom/false_alarms"
ALPHAS = [0.02, 0.05, 0.075, 0.10, 0.15, 0.20, 0.30]


def tier_of(length, e):
    """Tertile with a >= rule on the top edge: B1 piles 37% of its decodes on the 192-token
    cap, so a strict '<= q67' rule leaves the long tertile empty."""
    if length <= e[0]:
        return 0
    return 2 if length >= e[1] else 1


def tertile_far(rows, traces):
    lens = np.array([t.token_count for t in traces])
    e = (float(np.quantile(lens, 1/3)), float(np.quantile(lens, 2/3)))
    res = []
    for tier in (0, 1, 2):
        sub = [r for r in rows if not r.positive and tier_of(r.token_count, e) == tier]
        fa = sum(1 for r in sub if r.first_alarm_end is not None)
        res.append((fa, len(sub)))
    return e, res


def main():
    core = rio.load_core()
    cls = {}
    cases_json = json.load(open(f"{OUT}/false_alarm_cases.json"))
    for r in cases_json["cases"]:
        cls[(r["cand"], r["case"], r["trace_id"])] = r["class"]
    dump = {}
    for key in ("A", "B"):
        w = CAND[key]["w"]
        rules = {
            "baseline": lambda a: BaselineRule(a),
            "length_tertile": lambda a: LengthTertileRule(a),
            "anytime_shape": lambda a: AnytimeShapeRule(a),
            "block_2of2": lambda a, w=w: BlockGateRule(w, 2, 2, a),
            "block_2of3": lambda a, w=w: BlockGateRule(w, 2, 3, a),
            "block_3of4": lambda a, w=w: BlockGateRule(w, 3, 4, a),
            "divgate": lambda a, w=w: DiversityGateRule(w, max(2, w // 2), a),
        }
        for case, tgt in CASES.items():
            traces = list(core[tgt])
            cr, streams = load_case(key, case)
            print(f"\n#### {key} {case}")
            print("| rule | alpha | FAR | clean | benign | resist | tier0 | tier1 | tier2 | +8 | +16 | final | lat |")
            print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for rname, rf in rules.items():
                for a in ALPHAS:
                    rows, info = evaluate_rule(lambda a=a, rf=rf: rf(a), traces, streams)
                    m = metrics(rows)
                    edges, tf = tertile_far(rows, traces)
                    rec = dict(rule=rname, alpha=a, far=m["far"], n=m["n_non_drift"], fa=m["fa_total"],
                               clean=m["far_clean"], benign=m["far_benign"], resist=m["far_resist"],
                               tertiles=tf, tertile_edges=edges,
                               r8=m["recall_8"], r16=m["recall_16"], rfin=m["recall_final"],
                               lat=m["median_latency"],
                               fa_classes=collections.Counter(
                                   cls.get((key, case, r.trace_id), "?")
                                   for r in rows if not r.positive and r.first_alarm_end is not None))
                    dump[f"{key}|{case}|{rname}|a{a}"] = {k: (dict(v) if isinstance(v, collections.Counter) else v)
                                                          for k, v in rec.items()}
                    print(f"| {rname} | {a} | {m['far']:.3f} ({m['fa_total']}/{m['n_non_drift']}) | "
                          f"{m['far_clean']:.3f} | {m['far_benign']:.3f} | {m['far_resist']:.3f} | "
                          + " | ".join(f"{x}/{y}" for x, y in tf) + " | "
                          f"{m['recall_8']:.3f} | {m['recall_16']:.3f} | {m['recall_final']:.3f} | {m['median_latency']} |")
    json.dump(dump, open(f"{OUT}/frontier.json", "w"), indent=1, default=str)

main()
