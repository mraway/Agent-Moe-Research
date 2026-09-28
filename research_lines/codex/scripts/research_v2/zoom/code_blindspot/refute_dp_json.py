"""REFUTER step E: (i) routine's OWN tool-call-JSON windows as the null for rmass /
entropy / top-1; (ii) displacement-direction cosines recomputed; (iii) entropy & top-1
per drift domain; (iv) the b1-f1-058 structured non-code control.  Diagnostic only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

from research_v2 import io as rio

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot/refute_depth_profile"
WIN, STRIDE = 48, 8
BAND = list(range(11, 15))
PROG = ["b1-f2-012", "b1-f2-014", "b1-f3-016", "b2-f2-011",
        "b2-f2-012", "b2-f2-014", "b2-f2-015", "b2-f3-020"]
JSON_RE = re.compile(r'"[a-z_]+"\s*:|tool_id|\{"|"\}|\}\}')


def main():
    z = np.load(OUT / "tokens.npz")
    meta = json.loads((OUT / "meta.json").read_text())
    tr = meta["traces"]
    off = np.cumsum([0] + [m["T"] for m in tr])
    rm, ent, top1 = z["rmass_fit"], z["ent"], z["top1"]
    res = {}

    # decode texts once per trace
    batches = rio.load_core()
    by_id = {t.trace_id: t for b in ("b1", "b2") for t in batches[b]}
    texts = {}
    for m in tr:
        texts[m["trace_id"]] = rio.decode_token_texts(by_id[m["trace_id"]].token_ids.tolist())

    # ---- routine held-out windows, flagged by whether they carry tool-call JSON ----
    rows = []
    for i, m in enumerate(tr):
        if not m["in_held"]:
            continue
        s, T = off[i], m["T"]
        tx = texts[m["trace_id"]]
        for a in range(0, max(1, T - WIN + 1), STRIDE):
            b = min(a + WIN, T)
            if b - a < 8:
                continue
            seg = "".join(tx[a:b])
            rows.append({"trace_id": m["trace_id"], "start": a,
                         "json": bool(JSON_RE.search(seg)),
                         "n_json_hits": len(JSON_RE.findall(seg)),
                         "rmass": float(np.median(rm[s + a:s + b], axis=0)[BAND].mean()),
                         "ent": float(np.median(ent[s + a:s + b], axis=0)[13]),
                         "top1": float(np.median(top1[s + a:s + b], axis=0)[13])})
    J = np.array([r["json"] for r in rows])
    RM = np.array([r["rmass"] for r in rows])
    EN = np.array([r["ent"] for r in rows])
    T1 = np.array([r["top1"] for r in rows])
    heavy = np.array([r["n_json_hits"] >= 3 for r in rows])
    print("## routine held-out 48-token windows split by tool-call-JSON content")
    for lbl, msk in (("all", np.ones_like(J)), ("no JSON", ~J), ("has JSON", J),
                     (">=3 JSON hits", heavy)):
        msk = msk.astype(bool)
        print(f"  {lbl:14s} n={msk.sum():4d}  rmass(L11-14) med {np.median(RM[msk]):.3f} "
              f"q90 {np.quantile(RM[msk],.9):.3f} q95 {np.quantile(RM[msk],.95):.3f} "
              f"max {RM[msk].max():.3f} | entL13 med {np.median(EN[msk]):.3f} "
              f"q10 {np.quantile(EN[msk],.1):.3f} min {EN[msk].min():.3f} | "
              f"top1L13 med {np.median(T1[msk]):.3f} q90 {np.quantile(T1[msk],.9):.3f}")
    res["routine_json_windows"] = {
        lbl: {"n": int(m.astype(bool).sum()),
              "rmass_median": float(np.median(RM[m.astype(bool)])),
              "rmass_q95": float(np.quantile(RM[m.astype(bool)], .95)),
              "rmass_max": float(RM[m.astype(bool)].max()),
              "ent_median": float(np.median(EN[m.astype(bool)])),
              "ent_q10": float(np.quantile(EN[m.astype(bool)], .10)),
              "ent_min": float(EN[m.astype(bool)].min())}
        for lbl, m in (("all", np.ones_like(J)), ("no_json", ~J), ("has_json", J),
                       ("json_ge3", heavy))}

    # code windows against the JSON-only null
    print("\n## each code trace against the routine JSON-window null (n=%d) and the "
          "no-JSON null (n=%d)" % (J.sum(), (~J).sum()))
    print("| trace | rmass | %all < | %JSON-win < | %noJSON-win < | entL13 | %JSON-win > | top1L13 |")
    print("|" + "---|" * 8)
    code_rows = {}
    for sid in PROG:
        i = next(j for j, m in enumerate(tr) if m["short"] == sid)
        m = tr[i]; s = off[i]
        a = max(0, min(int(m["product_onset"]), m["T"] - 1)); b = min(a + WIN, m["T"])
        v = float(np.median(rm[s + a:s + b], axis=0)[BAND].mean())
        e = float(np.median(ent[s + a:s + b], axis=0)[13])
        t1 = float(np.median(top1[s + a:s + b], axis=0)[13])
        code_rows[sid] = {"rmass": v, "ent": e, "top1": t1,
                          "pct_all": float((RM < v).mean() * 100),
                          "pct_json": float((RM[J] < v).mean() * 100),
                          "pct_nojson": float((RM[~J] < v).mean() * 100),
                          "ent_pct_json": float((EN[J] > e).mean() * 100)}
        r = code_rows[sid]
        print(f"| {sid} | {v:.3f} | {r['pct_all']:.0f} | {r['pct_json']:.0f} | "
              f"{r['pct_nojson']:.0f} | {e:.3f} | {r['ent_pct_json']:.0f} | {t1:.3f} |")
    res["code_vs_json_null"] = code_rows

    # ---- entropy / top1 per drift domain --------------------------------------
    print("\n## entropy(L13) and top-1(L13) per drift trace by domain "
          f"(routine window median ent {np.median(EN):.3f}, top1 {np.median(T1):.3f})")
    dom = {}
    for i, m in enumerate(tr):
        if m["arm_class"] != "drift":
            continue
        s = off[i]
        a = max(0, min(int(m["product_onset"]), m["T"] - 1)); b = min(a + WIN, m["T"])
        e = float(np.median(ent[s + a:s + b], axis=0)[13])
        t1 = float(np.median(top1[s + a:s + b], axis=0)[13])
        k = "programming" if m["is_code"] else m["domain"]
        dom.setdefault(k, []).append((m["short"], round(e, 3), round(t1, 3),
                                      round(float((EN < e).mean() * 100))))
    for k in sorted(dom):
        v = sorted(dom[k], key=lambda x: x[1])
        print(f"{k:20s} " + "  ".join(f"{a}:ent{b}(p{d})" for a, b, c, d in v))
    res["ent_by_domain"] = dom

    # ---- displacement cosines --------------------------------------------------
    zp = np.load(REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot/depth_profile/window_probs.npz")
    # recompute from my own cache instead: group mean probs per layer
    print("\n## displacement-direction cosines, recomputed from raw probabilities")
    import torch
    ref = None
    sums = {"code": np.zeros((16, 64)), "other": np.zeros((16, 64)), "routine_held": np.zeros((16, 64))}
    cnt = {k: 0 for k in sums}
    per_dom = {}
    for i, m in enumerate(tr):
        t = by_id[m["trace_id"]]
        if m["arm_class"] == "drift":
            a = max(0, min(int(m["product_onset"]), m["T"] - 1)); b = min(a + WIN, m["T"])
            p = t.probabilities().float()[:, a:b, :].sum(1).numpy()
            k = "code" if m["is_code"] else "other"
            sums[k] += p; cnt[k] += (b - a)
            d = "programming" if m["is_code"] else m["domain"]
            per_dom.setdefault(d, [np.zeros((16, 64)), 0])
            per_dom[d][0] += p; per_dom[d][1] += (b - a)
        elif m["in_held"]:
            p = t.probabilities().float().sum(1).numpy()
            sums["routine_held"] += p; cnt["routine_held"] += m["T"]
        t._probabilities = None
    ref_p = z["ref_p"]           # fit-half token-mean distribution
    mean = {k: sums[k] / cnt[k] for k in sums}
    dom_mean = {k: v[0] / v[1] for k, v in per_dom.items()}
    dcode = mean["code"] - ref_p
    doth = mean["other"] - ref_p
    cos = []
    for l in range(16):
        c = float(dcode[l] @ doth[l] / (np.linalg.norm(dcode[l]) * np.linalg.norm(doth[l])))
        cos.append(c)
    print("cos(code displacement, other-drift displacement) by layer:")
    print("  " + " ".join(f"L{l}:{cos[l]:+.3f}" for l in range(16)))
    res["cos_code_other"] = cos
    print("\ncos(code displacement, each domain's displacement) at L11-15:")
    percos = {}
    for d, mm in sorted(dom_mean.items()):
        if d == "programming":
            continue
        dd = mm - ref_p
        percos[d] = [float(dcode[l] @ dd[l] / (np.linalg.norm(dcode[l]) * np.linalg.norm(dd[l])))
                     for l in range(16)]
        print(f"  {d:20s} " + " ".join(f"{percos[d][l]:+.2f}" for l in range(11, 16)))
    res["cos_code_domain"] = percos
    # routine held-vs-fit displacement (noise scale)
    drt = mean["routine_held"] - ref_p
    print("\ncos(code displacement, routine held-vs-fit displacement) L11-15: " +
          " ".join(f"{float(dcode[l]@drt[l]/(np.linalg.norm(dcode[l])*np.linalg.norm(drt[l]))):+.2f}"
                   for l in range(11, 16)))
    res["cos_code_routine_noise"] = [
        float(dcode[l] @ drt[l] / (np.linalg.norm(dcode[l]) * np.linalg.norm(drt[l])))
        for l in range(16)]
    (OUT / "jsonnull.json").write_text(json.dumps(res), encoding="utf-8")


if __name__ == "__main__":
    main()
