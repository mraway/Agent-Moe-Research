"""REFUTER step B: is the rmass sharpening a decode-position / token-class artefact?

(1) routine null restricted to matched position buckets;
(2) token-class matched routine reference (resample routine tokens to the code window's
    token-class mix, and per-class rmass tables);
(3) routine's own tool-call-JSON-like windows (structured token windows) as the null;
(4) literal-code (5) vs prose-about-SQL (3) split;
(5) every other drift domain, b1-f1-058 and the legal traces.
Diagnostic only, post-hoc, B1/B2 development data.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot/refute_depth_profile"
WIN, STRIDE = 48, 8
PROG = ["b1-f2-012", "b1-f2-014", "b1-f3-016", "b2-f2-011",
        "b2-f2-012", "b2-f2-014", "b2-f2-015", "b2-f3-020"]
LITERAL = {"b1-f2-012", "b1-f2-014", "b1-f3-016", "b2-f2-011", "b2-f3-020"}
PROSE = {"b2-f2-012", "b2-f2-014", "b2-f2-015"}
BAND = list(range(11, 15))     # L11-14, the report's headline band
CLASSES = ("whitespace", "code_symbol", "punctuation", "code_keyword",
           "number", "fragment", "word", "other")
STRUCT = {CLASSES.index(c) for c in ("code_symbol", "punctuation", "whitespace", "number")}
rng = np.random.default_rng(0)


def main():
    z = np.load(OUT / "tokens.npz")
    meta = json.loads((OUT / "meta.json").read_text())
    tr = meta["traces"]
    off = np.cumsum([0] + [m["T"] for m in tr])
    rm = z["rmass_fit"]
    tc = z["tclass"]
    res = {}

    def win_stat(s, a, b, layers=BAND, arr=None):
        A = rm if arr is None else arr
        return float(np.median(A[s + a:s + b], axis=0)[layers].mean())

    # ---------- code / drift windows -------------------------------------------
    drift = []
    for i, m in enumerate(tr):
        if m["arm_class"] != "drift":
            continue
        s = off[i]
        a = max(0, min(int(m["product_onset"]), m["T"] - 1))
        b = min(a + WIN, m["T"])
        cls = tc[s + a:s + b]
        drift.append({"short": m["short"], "domain": m["domain"], "is_code": m["is_code"],
                      "batch": m["batch"], "onset": a, "end": b,
                      "rmass": win_stat(s, a, b),
                      "struct_frac": float(np.isin(cls, list(STRUCT)).mean()),
                      "codesym_frac": float((cls == CLASSES.index("code_symbol")).mean()),
                      "class_mix": np.bincount(cls, minlength=8).tolist()})
    res["drift"] = drift
    code = [d for d in drift if d["is_code"]]

    # ---------- routine null windows, with position + class annotation ----------
    null = []
    for i, m in enumerate(tr):
        if not m["in_held"]:
            continue
        s = off[i]
        for a in range(0, max(1, m["T"] - WIN + 1), STRIDE):
            b = min(a + WIN, m["T"])
            if b - a < 8:
                continue
            cls = tc[s + a:s + b]
            null.append({"trace_id": m["trace_id"], "start": a, "end": b,
                         "rmass": win_stat(s, a, b),
                         "struct_frac": float(np.isin(cls, list(STRUCT)).mean()),
                         "codesym_frac": float((cls == CLASSES.index("code_symbol")).mean()),
                         "bucket": a // 32})
    nrm = np.array([w["rmass"] for w in null])
    nbk = np.array([w["bucket"] for w in null])
    nsf = np.array([w["struct_frac"] for w in null])
    ncs = np.array([w["codesym_frac"] for w in null])
    res["n_null"] = len(null)

    print("## 1. rmass(L11-14) per code trace vs (a) all routine windows, "
          "(b) position-bucket-matched routine windows, (c) structured-routine windows")
    print("| trace | kind | onset bucket | struct frac | rmass | %routine win < | "
          "%same-bucket win < | %struct-routine win < | %routine TRACE < |")
    print("|" + "---|" * 9)
    # routine trace-level maxima / medians
    tr_rm = {}
    for i, m in enumerate(tr):
        if not m["in_held"]:
            continue
        s = off[i]
        tr_rm[m["trace_id"]] = win_stat(s, 0, m["T"])
    trvals = np.array(list(tr_rm.values()))
    # structured routine windows: top-quartile struct_frac
    thr_struct = np.quantile(nsf, 0.75)
    struct_mask = nsf >= thr_struct
    rows = []
    for d in code:
        bk = d["onset"] // 32
        same = nbk == bk
        r = {
            "short": d["short"],
            "kind": "literal" if d["short"] in LITERAL else "prose",
            "bucket": int(bk), "struct_frac": d["struct_frac"], "rmass": d["rmass"],
            "pct_all": float((nrm < d["rmass"]).mean() * 100),
            "pct_bucket": float((nrm[same] < d["rmass"]).mean() * 100) if same.any() else None,
            "n_bucket": int(same.sum()),
            "pct_struct": float((nrm[struct_mask] < d["rmass"]).mean() * 100),
            "pct_trace": float((trvals < d["rmass"]).mean() * 100),
        }
        rows.append(r)
        print(f"| {r['short']} | {r['kind']} | {r['bucket']} | {r['struct_frac']:.2f} | "
              f"{r['rmass']:.3f} | {r['pct_all']:.0f} | "
              f"{r['pct_bucket']:.0f} (n={r['n_bucket']}) | {r['pct_struct']:.0f} | "
              f"{r['pct_trace']:.0f} |")
    res["code_rows"] = rows
    res["struct_thr"] = float(thr_struct)
    res["routine_trace_rmass"] = {"n": len(trvals), "median": float(np.median(trvals)),
                                  "q90": float(np.quantile(trvals, .9)),
                                  "q95": float(np.quantile(trvals, .95)),
                                  "max": float(trvals.max())}
    res["routine_window_struct"] = {
        "n": int(struct_mask.sum()), "median": float(np.median(nrm[struct_mask])),
        "q90": float(np.quantile(nrm[struct_mask], .9)),
        "q95": float(np.quantile(nrm[struct_mask], .95)),
        "max": float(nrm[struct_mask].max()),
        "median_all": float(np.median(nrm))}
    print("\nroutine windows: all median %.3f q95 %.3f max %.3f | structured(top-quartile "
          "struct_frac>=%.2f, n=%d) median %.3f q95 %.3f max %.3f"
          % (np.median(nrm), np.quantile(nrm, .95), nrm.max(), thr_struct, struct_mask.sum(),
             np.median(nrm[struct_mask]), np.quantile(nrm[struct_mask], .95), nrm[struct_mask].max()))
    print("routine TRACE-level rmass(L11-14): median %.3f q90 %.3f q95 %.3f max %.3f (n=%d)"
          % (np.median(trvals), np.quantile(trvals, .9), np.quantile(trvals, .95),
             trvals.max(), len(trvals)))

    # ---------- correlation of rmass with structure in routine ------------------
    def _rank(a):
        a = np.asarray(a, float)
        order = np.argsort(a, kind="mergesort")
        r = np.empty(len(a), float)
        sa = a[order]
        i = 0
        while i < len(a):
            j = i
            while j + 1 < len(a) and sa[j + 1] == sa[i]:
                j += 1
            r[order[i:j + 1]] = (i + j) / 2.0
            i = j + 1
        return r

    def spearman(a, b):
        ra, rb = _rank(a), _rank(b)
        ra = ra - ra.mean(); rb = rb - rb.mean()
        return float((ra * rb).sum() / np.sqrt((ra**2).sum() * (rb**2).sum()))
    res["corr_struct_rmass_routine"] = {
        "spearman_struct": (spearman(nsf, nrm)),
        "spearman_codesym": (spearman(ncs, nrm)),
        "spearman_bucket": (spearman(nbk, nrm)),
    }
    print("\nrows: routine windows -- spearman(struct_frac, rmass) = %.3f ; "
          "spearman(code_symbol_frac, rmass) = %.3f ; spearman(position bucket, rmass) = %.3f"
          % (res["corr_struct_rmass_routine"]["spearman_struct"],
             res["corr_struct_rmass_routine"]["spearman_codesym"],
             res["corr_struct_rmass_routine"]["spearman_bucket"]))
    dv = np.array([d["struct_frac"] for d in drift]); dr = np.array([d["rmass"] for d in drift])
    print("all 59 drift windows -- spearman(struct_frac, rmass) = %.3f"
          % spearman(dv, dr))
    res["corr_struct_rmass_drift"] = (spearman(dv, dr))

    # ---------- per token-class rmass, code vs routine ---------------------------
    print("\n## 2. per-token-class rmass(L11-14 mean), code-window tokens vs routine tokens")
    print("| class | n code tok | code rmass | n routine tok | routine rmass | ratio |")
    print("|" + "---|" * 6)
    code_tok = []
    for d in code:
        i = next(j for j, m in enumerate(tr) if m["short"] == d["short"])
        s = off[i]
        code_tok.append(np.arange(s + d["onset"], s + d["end"]))
    code_ix = np.concatenate(code_tok)
    routine_ix = np.concatenate([np.arange(off[i], off[i + 1])
                                 for i, m in enumerate(tr) if m["in_held"]])
    percls = {}
    for c, name in enumerate(CLASSES):
        a = code_ix[tc[code_ix] == c]
        b = routine_ix[tc[routine_ix] == c]
        if len(a) < 5 or len(b) < 5:
            continue
        ca = float(rm[a][:, BAND].mean())
        cb = float(rm[b][:, BAND].mean())
        percls[name] = {"n_code": len(a), "code": ca, "n_routine": len(b), "routine": cb,
                        "ratio": ca / cb}
        print(f"| {name} | {len(a)} | {ca:.3f} | {len(b)} | {cb:.3f} | {ca/cb:.2f} |")
    res["per_class"] = percls

    # class-mix reweighted routine baseline: routine rmass if it had the code class mix
    mix = np.bincount(tc[code_ix], minlength=8).astype(float); mix /= mix.sum()
    rout_by_cls = np.array([rm[routine_ix[tc[routine_ix] == c]][:, BAND].mean()
                            if (tc[routine_ix] == c).sum() else np.nan for c in range(8)])
    ok = ~np.isnan(rout_by_cls)
    reweighted = float((mix[ok] * rout_by_cls[ok]).sum() / mix[ok].sum())
    plain = float(rm[routine_ix][:, BAND].mean())
    codeval = float(rm[code_ix][:, BAND].mean())
    res["class_reweight"] = {"routine_plain": plain, "routine_reweighted": reweighted,
                             "code": codeval,
                             "ratio_plain": codeval / plain,
                             "ratio_reweighted": codeval / reweighted}
    print(f"\nclass-mix reweighted routine baseline: routine plain {plain:.4f}, "
          f"reweighted to the code class mix {reweighted:.4f}, code {codeval:.4f} "
          f"-> ratio {codeval/plain:.3f} -> {codeval/reweighted:.3f}")

    # ---------- literal vs prose ------------------------------------------------
    lit = [r for r in rows if r["kind"] == "literal"]
    pro = [r for r in rows if r["kind"] == "prose"]
    res["literal_vs_prose"] = {
        "literal": {"rmass": [r["rmass"] for r in lit], "pct_trace": [r["pct_trace"] for r in lit]},
        "prose": {"rmass": [r["rmass"] for r in pro], "pct_trace": [r["pct_trace"] for r in pro]},
    }
    print(f"\n## 3. literal code (n=5) rmass {[round(r['rmass'],3) for r in lit]} ; "
          f"prose-about-SQL (n=3) {[round(r['rmass'],3) for r in pro]}")

    # ---------- other domains ---------------------------------------------------
    print("\n## 4. rmass(L11-14) per drift trace, all domains "
          "(routine trace median %.3f, q95 %.3f)" % (np.median(trvals), np.quantile(trvals, .95)))
    bydom = {}
    for d in drift:
        k = "programming" if d["is_code"] else d["domain"]
        bydom.setdefault(k, []).append((d["short"], round(d["rmass"], 3),
                                        round(float((trvals < d["rmass"]).mean() * 100))))
    for k in sorted(bydom):
        print(f"{k:20s} " + "  ".join(f"{a}:{b}({c}%)" for a, b, c in sorted(bydom[k], key=lambda x: -x[1])))
    res["by_domain"] = bydom
    (OUT / "confound.json").write_text(json.dumps(res), encoding="utf-8")


if __name__ == "__main__":
    main()
