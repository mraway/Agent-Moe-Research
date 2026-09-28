"""Exact intervals: recall / FAR headlines, h384 length tertiles, gate statistics. Read-only."""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_statistics_lib as L  # noqa: E402
import audit_statistics_recompute as R  # noqa: E402

CELLS = {"b2": "final_b2_both", "b1": "final_b1_both", "h384": "final_h384_both"}


def length_tertiles(summaries):
    lens = sorted((s["token_count"], s["key"]) for s in summaries)
    n = len(lens)
    third = max(1, n // 3)
    return {k: ("short" if i < third else "medium" if i < 2 * third else "long")
            for i, (_, k) in enumerate(lens)}


def cochran_armitage(rows, scores=(0, 1, 2)):
    N = sum(n for _, n in rows)
    K = sum(k for k, _ in rows)
    p = K / N
    T = sum(s * k for s, (k, _) in zip(scores, rows))
    sbar = sum(s * n for s, (_, n) in zip(scores, rows)) / N
    ET = p * sum(s * n for s, (_, n) in zip(scores, rows))
    VT = p * (1 - p) * sum(n * (s - sbar) ** 2 for s, (_, n) in zip(scores, rows))
    if VT <= 0:
        return 0.0, 1.0
    z = (T - ET) / math.sqrt(VT)
    return z, math.erfc(abs(z) / math.sqrt(2))


def newcombe(k1, n1, k2, n2, z=1.959963985):
    def wilson(k, n):
        p = k / n
        d = 1 + z * z / n
        c = (p + z * z / (2 * n)) / d
        h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
        return c - h, c + h
    l1, u1 = wilson(k1, n1)
    l2, u2 = wilson(k2, n2)
    p1, p2 = k1 / n1, k2 / n2
    return (p1 - p2 - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2),
            p1 - p2 + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2))


if __name__ == "__main__":
    print("== exact 95% Clopper-Pearson intervals, primary-event +8 recall and per-episode FAR")
    for tag, cell in CELLS.items():
        d = L.load(cell)
        for col in d["calibration_columns"]:
            C = d["columns"][col]
            for v in ("trm3", "m_only", "s_only"):
                T = C["variants"][v]["sets"]["target"]
                rs = T["recall_strict"]
                f = T["far"]
                print(f"  {tag}/{col:2s} {v:8s} R+8 {L.fmt_ci(rs['recall_plus_8_count'], rs['positive_count'])}"
                      f"  FARclean {L.fmt_ci(round(f['clean']*f['clean_count']), f['clean_count'])}"
                      f"  FARpooled {L.fmt_ci(round(f['pooled']*f['pooled_count']), f['pooled_count'])}")
    print()
    print("== h384 length-tertile FAR (prereg v1.3 amendment 5) with a Cochran-Armitage trend test")
    d = L.load("final_h384_both")
    for col in ("D", "C1"):
        for v in ("trm3", "m_only", "s_only"):
            T = d["columns"][col]["variants"][v]["sets"]["target"]
            summ = T["summaries"]
            sd = set((T.get("spontaneous_drift") or {}).get("keys", []) or [])
            tt = length_tertiles(summ)
            normals = [s for s in summ if s["arm_class"] in ("clean", "benign") and s["key"] not in sd]
            rows = []
            print(f"  h384/{col} {v}:")
            for g in ("short", "medium", "long"):
                rr = [s for s in normals if tt[s["key"]] == g]
                k = sum(1 for s in rr if s["alarm"])
                rows.append((k, len(rr)))
                toks = [s["token_count"] for s in rr]
                print(f"    {g:7s} tokens[{min(toks)}-{max(toks)}] FAR {L.fmt_ci(k, len(rr))}")
            z, p = cochran_armitage(rows)
            print(f"    trend z={z:.3f} two-sided p={p:.4f} one-sided p={p/2:.4f}")
    print()
    print("== gate statistics as intervals")
    print("  G4 h384/D silent-resist", L.fmt_ci(4, 35), "clean", L.fmt_ci(3, 80),
          "difference CI", tuple(round(x, 4) for x in newcombe(4, 35, 3, 80)), "(limit 0.05)")
    print("  G4 h384/C1 silent-resist", L.fmt_ci(7, 35), "clean", L.fmt_ci(2, 80),
          "difference CI", tuple(round(x, 4) for x in newcombe(7, 35, 2, 80)), "(limit 0.05)")
    print("  G7 C1 held-out matched-group", L.fmt_ci(5, 30), "(limit 0.15)")
    print("  G2 b2/C1 benign-clean CI", tuple(round(x, 4) for x in newcombe(11, 80, 2, 80)), "(limit 0.10)")
