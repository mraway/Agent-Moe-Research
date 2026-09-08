from __future__ import annotations
import json, sys, os, collections
sys.path.insert(0, os.path.dirname(__file__))
from fa_core import *  # noqa
from fa_core import _distinct_counts
from research_v2 import io as rio

CASES = {"b1_to_b2": "b2", "b2_to_b1": "b1"}
OUT = f"{ROOT}/artifacts/agent_v2/research_v2/zoom/false_alarms"


class MaxRule(BaselineRule):
    key = "max"
    def fit(self, cal):
        self.h = conformal([float(z.max()) for _, z, e in cal if z.size], self.alpha)
        self.info = {"threshold": self.h}
        return self
    def evaluate(self, trace, z, ends):
        return z.copy(), np.full_like(z, self.h), ends


def main():
    core = rio.load_core()
    variants = lambda w: [
        ("baseline persist2 (frozen)", lambda: BaselineRule(0.10)),
        ("max reading (all ends)", lambda: MaxRule(0.10)),
        ("(ii) block 1of1 @fixed max-h", lambda w=w: FixedThresholdSustainRule(w, 1, 1, 0.10, "max")),
        ("(ii) block 2of2 @fixed max-h", lambda w=w: FixedThresholdSustainRule(w, 2, 2, 0.10, "max")),
        ("(ii) block 2of3 @fixed max-h", lambda w=w: FixedThresholdSustainRule(w, 2, 3, 0.10, "max")),
        ("(ii') persist2 run2 @fixed h", lambda w=w: FixedThresholdSustainRule(w, base="persist2", consecutive=2)),
        ("(ii') persist2 run4 @fixed h", lambda w=w: FixedThresholdSustainRule(w, base="persist2", consecutive=4)),
        ("(i-a) length tertile recal.", lambda: LengthTertileRule(0.10)),
        ("(i-b) anytime shape recal.", lambda: AnytimeShapeRule(0.10)),
        ("(iii) repetition gate", lambda w=w: DiversityGateRule(w, 3, 0.10)),
        ("(ii')+(iii) run2 + rep gate", None),
    ]
    agg = {}
    for key in ("A", "B"):
        w = CAND[key]["w"]
        for name, f in variants(w):
            tot = collections.Counter()
            per = {}
            for case, tgt in CASES.items():
                traces = list(core[tgt]); cr, streams = load_case(key, case)
                if f is None:
                    class Combo(FixedThresholdSustainRule):
                        def __init__(self, w=w):
                            super().__init__(w, base="persist2", consecutive=2)
                        def evaluate(self, trace, z, ends):
                            tok = trace.token_ids.numpy()
                            ok = _distinct_counts(tok, ends, self.w) >= 3
                            zz = z.copy(); zz[~ok] = NEG_INF
                            return super().evaluate(trace, zz, ends)
                        def fit(self, cal):
                            cal2 = []
                            for t, z, e in cal:
                                tok = t.token_ids.numpy()
                                ok = _distinct_counts(tok, e, self.w) >= 3
                                zz = z.copy(); zz[~ok] = NEG_INF
                                cal2.append((t, zz, e))
                            return super().fit(cal2)
                    rows, _ = evaluate_rule(Combo, traces, streams)
                else:
                    rows, _ = evaluate_rule(f, traces, streams)
                m = metrics(rows)
                per[case] = m
                tot["fa"] += m["fa_total"]; tot["n"] += m["n_non_drift"]
                for a in ("clean", "benign", "resist"):
                    tot["fa_" + a] += m["fa_" + a]; tot["n_" + a] += m["n_" + a]
                for h in (8, 16, "final"):
                    tot[f"r{h}"] += m[f"recall_{h}_count"]
                tot["nd"] += m["n_drift"]
            agg[(key, name)] = (tot, per)
    for key in ("A", "B"):
        base = agg[(key, "baseline persist2 (frozen)")][0]
        print(f"\n### CAND-{key}: pooled over both directions (non-drift n={base['n']}, drift n={base['nd']})")
        print("| rule | FAR | clean | benign | resist | +8 | +16 | final | dFAR | d(+8) | dFAR per d(+8) |")
        print("|---|---|---|---|---|---|---|---|---|---|---|")
        for name, _ in variants(CAND[key]["w"]):
            t = agg[(key, name)][0]
            far = t["fa"] / t["n"]; r8 = t["r8"] / t["nd"]; r16 = t["r16"] / t["nd"]; rf = t["rfinal"] / t["nd"]
            dfar = base["fa"] / base["n"] - far
            dr8 = base["r8"] / base["nd"] - r8
            ratio = f"{dfar/dr8:.2f}" if abs(dr8) > 1e-9 else ("inf" if dfar > 1e-9 else "-")
            print(f"| {name} | {far:.3f} ({t['fa']}/{t['n']}) | {t['fa_clean']}/{t['n_clean']} | "
                  f"{t['fa_benign']}/{t['n_benign']} | {t['fa_resist']}/{t['n_resist']} | "
                  f"{r8:.3f} | {r16:.3f} | {rf:.3f} | {dfar:+.3f} | {-dr8:+.3f} | {ratio} |")
    json.dump({f"{k}|{n}": {"pooled": dict(v[0]),
                            "per_case": {c: {kk: vv for kk, vv in m.items() if kk not in ("fa_trace_ids", "far_by_tertile")}
                                         for c, m in v[1].items()}}
               for (k, n), v in agg.items()}, open(f"{OUT}/ranking.json", "w"), indent=1, default=str)

main()
