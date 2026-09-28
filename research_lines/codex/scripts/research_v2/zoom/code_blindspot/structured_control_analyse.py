"""Matched control group for critic E7 -- step 3/3: grouping, tables, correlations.

GROUPING RULE (pre-declared, decoded text only, no routing information):
  structure_score = fraction of tokens in the product window carrying at least one of
  {newline, bracket ()[]{}<>, digit, colon, list marker} (see structured_control_score.py).
  A non-programming drift trace is STRUCTURED iff structure_score >= 0.20, else PROSE.
  0.20 sits inside the empirical gap (0.1875, 0.2051) immediately below the lowest of the
  three P3 tool-call-payload traces, which the task design requires to be structured.

Groups: CODE (8 programming; LITCODE = the 5 with literal code inside the window),
        STRUCT (non-programming, score >= 0.20), PROSE (non-programming, score < 0.20),
        PROSE_STRICT (PROSE restricted to poetry / fiction / general_knowledge).

Read-only post-hoc diagnosis on B1/B2.  Not a detector; no improvement claimed.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

RNG_SEED = 20260905


def _rank(a: np.ndarray) -> np.ndarray:
    """Average ranks with ties (identical to scipy.stats.rankdata 'average')."""
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(a.size, dtype=float)
    sa = a[order]
    i = 0
    while i < a.size:
        j = i
        while j + 1 < a.size and sa[j + 1] == sa[i]:
            j += 1
        ranks[order[i:j + 1]] = 0.5 * (i + j) + 1.0
        i = j + 1
    return ranks


def spearman(a: np.ndarray, b: np.ndarray, draws: int = 20000) -> tuple[float, float]:
    """Spearman rho + two-sided permutation p (no scipy in this venv)."""
    ra, rb = _rank(np.asarray(a, float)), _rank(np.asarray(b, float))
    ra = ra - ra.mean(); rb = rb - rb.mean()
    den = float(np.sqrt((ra ** 2).sum() * (rb ** 2).sum()))
    if den == 0.0:
        return float("nan"), float("nan")
    rho = float((ra * rb).sum() / den)
    rng = np.random.default_rng(RNG_SEED)
    hit = 0
    for _ in range(draws):
        perm = rng.permutation(rb)
        r = float((ra * perm).sum() / den)
        if abs(r) >= abs(rho) - 1e-12:
            hit += 1
    return rho, (hit + 1) / (draws + 1)


REPO = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
OUT = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot/structured_control"
THRESHOLD = 0.20
PROSE_DOMAINS = ("poetry", "fiction", "general_knowledge")


def group_of(r: dict) -> str:
    if r["domain"] == "programming":
        return "CODE"
    return "STRUCT" if r["structure_score"] >= THRESHOLD else "PROSE"


def summarise(vals):
    v = np.asarray([x for x in vals if x is not None], dtype=float)
    if not v.size:
        return {"n": 0}
    return {"n": int(v.size), "median": float(np.median(v)), "mean": float(v.mean()),
            "min": float(v.min()), "max": float(v.max()),
            "q25": float(np.quantile(v, 0.25)), "q75": float(np.quantile(v, 0.75))}


def main() -> None:
    sc = {r["short"]: r for r in json.loads((OUT / "structure_scores.json").read_text())}
    st = json.loads((OUT / "stats.json").read_text())
    rows = []
    for r in st["rows"]:
        s = sc[r["short"]]
        r = dict(r)
        r["structure_score"] = s["structure_score"]
        r["per_class"] = s["per_class"]
        r["is_literal_code"] = s["is_literal_code"]
        r["group"] = group_of(r)
        r["prose_strict"] = (r["group"] == "PROSE" and r["domain"] in PROSE_DOMAINS)
        rows.append(r)
    rows.sort(key=lambda r: (-r["structure_score"], r["short"]))

    def get(r, key):
        return {
            "candA_med_q95": r["candA"]["median_over_q95"],
            "candA_med_q99": r["candA"]["median_over_q99"],
            "candA_max16_q95": r["candA"]["max16_over_q95"],
            "candA_max16_q99": r["candA"]["max16_over_q99"],
            "rare": r["rare_expert_mass"],
            "rmass_L11": r["rmass"]["L11"],
            "rmass_L13": r["rmass"]["L13"],
            "rmass_L11_13": r["rmass"]["L11_13_mean"],
            "structure": r["structure_score"],
        }[key]

    keys = ("candA_med_q95", "candA_max16_q95", "rare", "rmass_L11_13", "rmass_L11", "rmass_L13")
    groups = {
        "CODE": [r for r in rows if r["group"] == "CODE"],
        "LITCODE": [r for r in rows if r["group"] == "CODE" and r["is_literal_code"]],
        "CODE_PROSE": [r for r in rows if r["group"] == "CODE" and not r["is_literal_code"]],
        "STRUCT": [r for r in rows if r["group"] == "STRUCT"],
        "PROSE": [r for r in rows if r["group"] == "PROSE"],
        "PROSE_STRICT": [r for r in rows if r["prose_strict"]],
    }
    gsum = {g: {k: summarise([get(r, k) for r in rs]) for k in keys} for g, rs in groups.items()}
    for g, rs in groups.items():
        gsum[g]["structure_score"] = summarise([r["structure_score"] for r in rs])
        for cand in ("candA", "candB"):
            offs = [r[f"{cand}_alarm"]["offset_from_product_onset"] for r in rs]
            fired = [o for o in offs if o is not None]
            gsum[g][f"{cand}_alarm"] = {
                "n": len(rs), "n_no_alarm": sum(1 for o in offs if o is None),
                "n_alarm_before_onset": sum(1 for o in fired if o < 0),
                "n_alarm_within_plus16": sum(1 for o in fired if 0 <= o <= 16),
                "n_alarm_within_plus48": sum(1 for o in fired if 0 <= o <= 48),
                "median_offset_fired": float(np.median(fired)) if fired else None,
                "offsets": offs,
            }

    # ---- Spearman correlations ---------------------------------------------------
    def spear(rs, key):
        x = [r["structure_score"] for r in rs]
        y = [get(r, key) for r in rs]
        pair = [(a, b) for a, b in zip(x, y) if b is not None]
        a = np.asarray([p[0] for p in pair]); b = np.asarray([p[1] for p in pair])
        rho, p = spearman(a, b)
        return {"n": int(a.size), "rho": rho, "p": p}

    corr = {
        "all59": {k: spear(rows, k) for k in keys},
        "nonprog51": {k: spear([r for r in rows if r["domain"] != "programming"], k) for k in keys},
    }
    # alarm-side correlation (offset among traces that fired, plus a censored coding)
    for scope, rs in (("all59", rows), ("nonprog51", [r for r in rows if r["domain"] != "programming"])):
        for cand in ("candA", "candB"):
            fired = [r for r in rs if r[f"{cand}_alarm"]["offset_from_product_onset"] is not None]
            x = np.asarray([r["structure_score"] for r in fired])
            y = np.asarray([r[f"{cand}_alarm"]["offset_from_product_onset"] for r in fired], float)
            rho, p = spearman(x, y)
            corr[scope][f"{cand}_offset_fired"] = {"n": int(x.size), "rho": rho, "p": p}

    payload = {"threshold": THRESHOLD, "meta": st["meta"], "group_summary": gsum,
               "spearman": corr,
               "group_members": {g: [r["short"] for r in rs] for g, rs in groups.items()},
               "rows": rows}
    (OUT / "analysis.json").write_text(json.dumps(payload, indent=2))

    # ---- console tables ----------------------------------------------------------
    hdr = (f"{'trace':11s} {'grp':6s} {'domain':18s} {'PC':3s} {'struct':>6s} "
           f"{'medq95':>7s} {'medq99':>7s} {'mx16q95':>8s} {'rare':>7s} "
           f"{'rmL11':>6s} {'rmL13':>6s} {'A_off':>6s} {'B_off':>6s}")
    print(hdr); print("-" * len(hdr))
    for r in rows:
        a = r["candA_alarm"]["offset_from_product_onset"]
        b = r["candB_alarm"]["offset_from_product_onset"]
        print(f"{r['short']:11s} {r['group'][:6]:6s} {r['domain']:18s} {r['product_class']:3s} "
              f"{r['structure_score']:6.4f} {r['candA']['median_over_q95']:7.3f} "
              f"{r['candA']['median_over_q99']:7.3f} {r['candA']['max16_over_q95']:8.3f} "
              f"{r['rare_expert_mass']:7.4f} {r['rmass']['L11']:6.3f} {r['rmass']['L13']:6.3f} "
              f"{('-' if a is None else str(a)):>6s} {('-' if b is None else str(b)):>6s}")
    print()
    for g in groups:
        s = gsum[g]
        print(f"{g:13s} n={s['structure_score']['n']:2d} struct={s['structure_score']['median']:.3f} "
              f"medq95={s['candA_med_q95']['median']:.3f} mx16q95={s['candA_max16_q95']['median']:.3f} "
              f"rare={s['rare']['median']:.4f} rmL11_13={s['rmass_L11_13']['median']:.3f} "
              f"A:noalarm={s['candA_alarm']['n_no_alarm']}/{s['candA_alarm']['n']} "
              f"A:+16={s['candA_alarm']['n_alarm_within_plus16']} "
              f"B:noalarm={s['candB_alarm']['n_no_alarm']}/{s['candB_alarm']['n']} "
              f"B:+16={s['candB_alarm']['n_alarm_within_plus16']}")
    print()
    print("Spearman(structure_score, X):")
    for scope in corr:
        for k, v in corr[scope].items():
            print(f"  {scope:10s} {k:20s} n={v['n']:2d} rho={v['rho']:+.3f} p={v['p']:.4f}")
    print("wrote", OUT / "analysis.json")


if __name__ == "__main__":
    main()
