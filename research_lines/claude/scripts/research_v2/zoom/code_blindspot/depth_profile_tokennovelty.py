"""Does the depth-12-15 routing deviation track *token novelty* rather than the domain label?

Routine unigram surprisal (fit half of routine, add-one smoothing) of each drift window's
tokens, against that window's whitened distance at layers 12-15 (feature P) and 5-15.
Also a code-character density measure.  Diagnostic only; both quantities are computed
post hoc on labelled windows.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

import numpy as np

from research_v2 import io as rio

REPO = Path(__file__).resolve().parents[4]
CACHE = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot/depth_profile"
LABELS = REPO / "docs/research_v2/labels/product_onset_v1_adjudicated.jsonl"
FLOOR = 1e-3
CODE_CHARS = set("{}[]()<>;=_/\\|#$%*+-\"'`:")


def spearman(x, y):
    rx = np.argsort(np.argsort(x)).astype(float)
    ry = np.argsort(np.argsort(y)).astype(float)
    rx -= rx.mean(); ry -= ry.mean()
    return float((rx @ ry) / (np.linalg.norm(rx) * np.linalg.norm(ry)))


def main():
    d = json.loads((CACHE / "depth_profile.json").read_text())
    zp = np.load(CACHE / "window_probs.npz")
    meta = d["window_meta"]
    fit_ids = set(d["fit_ids"])
    labels = {}
    for line in LABELS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            labels[r["trace_id"]] = r

    batches = rio.load_core()
    traces = {t.trace_id: t for b in batches.values() for t in b}

    counts = Counter()
    total = 0
    for tid in fit_ids:
        for v in traces[tid].token_ids.tolist():
            counts[v] += 1
            total += 1
    V = 50304
    def surprisal(ids):
        return float(np.mean([-math.log2((counts[i] + 1) / (total + V)) for i in ids]))

    def code_density(ids):
        texts = rio.decode_token_texts(ids)
        return float(np.mean([1.0 if any(c in CODE_CHARS for c in s) else 0.0 for s in texts]))

    def whit(layers, group, idx=None):
        F = np.concatenate([zp["routine_fit"][:, l, :] for l in layers], axis=1).astype(np.float64)
        mu, sd = F.mean(0), F.std(0) + FLOOR
        X = np.concatenate([zp[group][:, l, :] for l in layers], axis=1).astype(np.float64)
        return np.linalg.norm((X - mu) / sd, axis=1)

    groups = ["code"] + [f"other:{x}" for x in
                         ["cooking", "fiction", "general_knowledge", "legal_analysis",
                          "mathematics", "poetry", "travel_planning"]]
    rows = []
    for g in groups:
        s1215 = whit([12, 13, 14, 15], g)
        s515 = whit(list(range(5, 16)), g)
        for i, m in enumerate(meta[g]):
            t = traces[m["trace_id"]]
            ids = t.token_ids[m["start"]:m["end"]].tolist()
            rows.append({"trace_id": m["trace_id"], "group": g,
                         "domain": labels[m["trace_id"]]["domain"],
                         "surprisal": surprisal(ids), "code_density": code_density(ids),
                         "s1215": float(s1215[i]), "s515": float(s515[i])})

    # routine window null, for the reference scale
    rs = whit([12, 13, 14, 15], "routine")
    rsu = []
    for m in meta["routine"]:
        t = traces[m["trace_id"]]
        ids = t.token_ids[m["start"]:m["end"]].tolist()
        rsu.append({"surprisal": surprisal(ids), "code_density": code_density(ids)})
    print(f"routine windows: unigram surprisal median {np.median([r['surprisal'] for r in rsu]):.2f} "
          f"(q90 {np.quantile([r['surprisal'] for r in rsu], 0.9):.2f}), "
          f"code-char density median {np.median([r['code_density'] for r in rsu]):.2f} "
          f"(q90 {np.quantile([r['code_density'] for r in rsu], 0.9):.2f}); "
          f"whitened 12-15 median {np.median(rs):.2f}, q90 {np.quantile(rs, 0.9):.2f}")
    print()
    x = np.array([r["surprisal"] for r in rows]); y = np.array([r["s1215"] for r in rows])
    cd = np.array([r["code_density"] for r in rows])
    print(f"across the 59 drift windows: spearman(unigram surprisal, whitened 12-15) = "
          f"{spearman(x, y):.3f}; spearman(code-char density, whitened 12-15) = {spearman(cd, y):.3f}; "
          f"spearman(surprisal, whitened 5-15) = {spearman(x, np.array([r['s515'] for r in rows])):.3f}")
    cw = [r for r in rows if r["group"] == "code"]
    print(f"code windows only (n=8): spearman(surprisal, 12-15) = "
          f"{spearman(np.array([r['surprisal'] for r in cw]), np.array([r['s1215'] for r in cw])):.3f}")
    print()
    print("| trace | domain | unigram surprisal | code-char density | whitened 12-15 | whitened 5-15 |")
    print("|---|---|---|---|---|---|")
    for r in sorted(cw, key=lambda r: -r["s1215"]):
        print(f"| {'-'.join(r['trace_id'].split('-')[:3])} | programming | {r['surprisal']:.2f} | "
              f"{r['code_density']:.2f} | {r['s1215']:.2f} | {r['s515']:.2f} |")
    print()
    print("| domain | n | median surprisal | median code-char density | median whitened 12-15 |")
    print("|---|---|---|---|---|")
    for g in groups:
        sub = [r for r in rows if r["group"] == g]
        print(f"| {g} | {len(sub)} | {np.median([r['surprisal'] for r in sub]):.2f} | "
              f"{np.median([r['code_density'] for r in sub]):.2f} | "
              f"{np.median([r['s1215'] for r in sub]):.2f} |")
    print()


if __name__ == "__main__":
    main()
