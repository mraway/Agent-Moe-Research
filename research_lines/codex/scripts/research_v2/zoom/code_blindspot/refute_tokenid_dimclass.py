"""REFUTATION lens, part 5: which (layer, expert) dims actually carry each domain's
whitened displacement, which routine token class selects them, and the per-trace
effective-sd ranking under the clean-arm reference.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np

from research_v2 import io as rio
from research_v2.features import selection_rate_windows

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
LABELS = REPO / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"
W, LAYERS, FLOOR, SPAN = 8, tuple(range(5, 16)), 1e-3, 48
SYM = set("{}[]()<>=_|\\/*&^~`;$#@+")
CLS = ("whitespace", "code_symbol", "punctuation", "number", "word_or_other")


def klass(text):
    s = text.strip()
    if s == "":
        return 0
    if any(c in SYM for c in s):
        return 1
    if not any(c.isalnum() for c in s):
        return 2
    if any(c.isdigit() for c in s) and not any(c.isalpha() for c in s):
        return 3
    return 4


def main() -> None:
    lab = {json.loads(l)["trace_id"]: json.loads(l)
           for l in LABELS.read_text(encoding="utf-8").splitlines() if l.strip()}
    batches = rio.load_core()
    traces = [t for b in ("b1", "b2") for t in batches[b]]
    cache, sel, cls = {}, {}, {}
    for t in traces:
        e, w = selection_rate_windows(t.top_k_ids, W, LAYERS)
        cache[t.trace_id] = (e.numpy(), w.numpy().astype(np.float64))
        k = t.top_k_ids[list(LAYERS)].numpy()          # [L, T, 8]
        oh = np.zeros((k.shape[1], len(LAYERS) * 64), dtype=np.float32)
        for li in range(len(LAYERS)):
            for j in range(8):
                oh[np.arange(k.shape[1]), li * 64 + k[li, :, j]] = 1.0
        sel[t.trace_id] = oh
        cls[t.trace_id] = np.array([klass(x) for x in rio.decode_token_texts(t.token_ids.tolist())])

    routine = sorted([t for t in traces if rio.arm_class(t) in ("clean", "benign")], key=lambda t: t.trace_id)
    drift = [t for t in traces if rio.arm_class(t) == "drift"]
    doms = {}
    for t in drift:
        doms.setdefault(lab[t.trace_id]["domain"], []).append(t)

    def region(t):
        o = int(lab[t.trace_id]["product_onset"]); T = int(t.token_ids.shape[0])
        hi = min(o + SPAN, T); e, w = cache[t.trace_id]
        return w[(e >= o + W - 1) & (e <= hi - 1)], np.arange(o, hi)

    fitb = routine[0::2]
    X = np.concatenate([cache[t.trace_id][1] for t in fitb])
    mu = X.mean(0); sd = X.std(0, ddof=1) + FLOOR
    Sr = np.concatenate([sel[t.trace_id] for t in fitb]); Cr = np.concatenate([cls[t.trace_id] for t in fitb])
    # routine per-class selection rate of each dim, and the argmax class
    rate = np.stack([Sr[Cr == c].mean(0) for c in range(len(CLS))])          # [5, D]
    topcls = rate.argmax(0)

    res = {}
    for d, ts in sorted(doms.items()):
        x = np.concatenate([region(t)[0] for t in ts])
        Sd = np.concatenate([sel[t.trace_id][region(t)[1]] for t in ts])
        Cd = np.concatenate([cls[t.trace_id][region(t)[1]] for t in ts])
        rated = np.stack([Sd[Cd == c].mean(0) if (Cd == c).sum() else np.zeros(sd.shape) for c in range(len(CLS))])
        z = (x.mean(0) - mu) / sd
        raw = x.mean(0) - mu
        idx_z = np.argsort(-np.abs(z))[:15]
        idx_raw = np.argsort(-np.abs(raw))[:15]
        def tab(idx):
            return [{"layer": int(LAYERS[i // 64]), "expert": int(i % 64), "z": round(float(z[i]), 2),
                     "raw_d": round(float(raw[i]), 3), "sd": round(float(sd[i]), 3),
                     "routine_mean_rate": round(float(mu[i]), 4),
                     "routine_top_class": CLS[topcls[i]],
                     "drift_top_class": CLS[int(rated[:, i].argmax())]} for i in idx]
        res[d] = {"top15_by_z": tab(idx_z), "top15_by_raw": tab(idx_raw),
                  "share_top15z_of_z2": float((z[idx_z] ** 2).sum() / (z ** 2).sum()),
                  "routine_top_class_of_top15raw": {CLS[c]: int((topcls[idx_raw] == c).sum()) for c in range(len(CLS))},
                  "routine_top_class_of_top15z": {CLS[c]: int((topcls[idx_z] == c).sum()) for c in range(len(CLS))}}

    # per-trace eff sd under the clean-arm reference
    Xc = np.concatenate([cache[t.trace_id][1] for t in routine[1::2]])
    muc = Xc.mean(0); sdc = Xc.std(0, ddof=1) + FLOOR
    per = {}
    for t in drift:
        x = region(t)[0]; dd = x.mean(0) - muc
        per[t.trace_id] = {"domain": lab[t.trace_id]["domain"],
                           "eff_sd_cleanfit": float(np.linalg.norm(dd) / np.linalg.norm(dd / sdc))}
    res["_per_trace_eff_sd_cleanfit"] = per
    (OUT / "refute_tokenid_dimclass.json").write_text(json.dumps(res, indent=1), encoding="utf-8")

    for d in ("programming", "general_knowledge", "legal_analysis"):
        print(f"\n=== {d}: top-15 dims by |z| (share of z^2 = {res[d]['share_top15z_of_z2']:.3f})")
        for r in res[d]["top15_by_z"][:10]:
            print("   ", r)
        print("   routine-top-class counts, top15 by RAW shift:", res[d]["routine_top_class_of_top15raw"])
        print("   routine-top-class counts, top15 by |z|      :", res[d]["routine_top_class_of_top15z"])
    print("\nper-trace eff_sd under CLEAN-arm reference, ranked (code *):")
    rows = sorted(per.items(), key=lambda kv: -kv[1]["eff_sd_cleanfit"])
    for k, v in rows[:24]:
        print(("*" if v["domain"] == "programming" else " "), f"{v['eff_sd_cleanfit']:.4f}", v["domain"][:12], k[:38])
    e = np.array([v["eff_sd_cleanfit"] for _, v in rows]); c = np.array([v["domain"] == "programming" for _, v in rows])
    print("code range", e[c].min(), e[c].max(), "| non-code above code min:", int((e[~c] > e[c].min()).sum()), "of 51")


if __name__ == "__main__":
    main()
