"""REFUTATION lens, part 4: is the "code is the MOST separable domain inside routine's
own top-variance subspace" result stable across routine references?  Same subspace test
under three whitening references (benign-arm fit = the colleague's, clean-arm fit,
all-240 fit), plus the z-shift split into top-variance-decile vs the rest.
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


def main() -> None:
    lab = {json.loads(l)["trace_id"]: json.loads(l)
           for l in LABELS.read_text(encoding="utf-8").splitlines() if l.strip()}
    batches = rio.load_core()
    traces = [t for b in ("b1", "b2") for t in batches[b]]
    cache = {}
    for t in traces:
        e, w = selection_rate_windows(t.top_k_ids, W, LAYERS)
        cache[t.trace_id] = (e.numpy(), w.numpy().astype(np.float64))
    routine = sorted([t for t in traces if rio.arm_class(t) in ("clean", "benign")], key=lambda t: t.trace_id)
    drift = [t for t in traces if rio.arm_class(t) == "drift"]
    doms = {}
    for t in drift:
        doms.setdefault(lab[t.trace_id]["domain"], []).append(t)

    def region(t):
        o = int(lab[t.trace_id]["product_onset"]); T = int(t.token_ids.shape[0])
        hi = min(o + SPAN, T); e, w = cache[t.trace_id]
        return w[(e >= o + W - 1) & (e <= hi - 1)]

    refs = {"benign_arm_fit(theirs)": routine[0::2], "clean_arm_fit": routine[1::2], "all240_fit": routine}
    res = {}
    for tag, fit_traces in refs.items():
        X = np.concatenate([cache[t.trace_id][1] for t in fit_traces])
        mu = X.mean(0); sd = X.std(0, ddof=1) + FLOOR
        top = sd >= np.quantile(sd, .90)
        bot = sd <= np.quantile(sd, .10)
        gtop = (((X[:, top] - mu[top]) / sd[top]) ** 2).sum(1)
        gbot = (((X[:, bot] - mu[bot]) / sd[bot]) ** 2).sum(1)
        gall = (((X - mu) / sd) ** 2).sum(1)
        e = {"domains": {}, "code_traces": {}}
        for d, ts in sorted(doms.items()):
            x = np.concatenate([region(t) for t in ts])
            z = (x.mean(0) - mu) / sd
            e["domains"][d] = {
                "top10_ratio": float(np.median((((x[:, top] - mu[top]) / sd[top]) ** 2).sum(1)) / np.median(gtop)),
                "top10_pctile": float((gtop < np.median((((x[:, top] - mu[top]) / sd[top]) ** 2).sum(1))).mean()),
                "bot10_ratio": float(np.median((((x[:, bot] - mu[bot]) / sd[bot]) ** 2).sum(1)) / np.median(gbot)),
                "all_pctile": float((gall < np.median((((x - mu) / sd) ** 2).sum(1))).mean()),
                "z_top10": float(np.linalg.norm(z[top])), "z_rest": float(np.linalg.norm(z[~top])),
                "z_total": float(np.linalg.norm(z))}
        for t in doms["programming"]:
            x = region(t)
            e["code_traces"][t.trace_id] = {
                "top10_pctile": float((gtop < np.median((((x[:, top] - mu[top]) / sd[top]) ** 2).sum(1))).mean()),
                "all_pctile": float((gall < np.median((((x - mu) / sd) ** 2).sum(1))).mean())}
        res[tag] = e

    (OUT / "refute_tokenid_subspace_robust.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    for tag, e in res.items():
        print(f"\n=== {tag}")
        print(f"   {'domain':20s} {'top10%dims ratio':>16s} {'pctile':>7s} {'bot10%dims ratio':>16s} {'all-704 pctile':>14s}   z_top / z_rest / z_tot")
        for d, v in sorted(e["domains"].items(), key=lambda kv: -kv[1]["top10_ratio"]):
            print(f"   {d:20s} {v['top10_ratio']:16.3f} {v['top10_pctile']:7.3f} {v['bot10_ratio']:16.2f} {v['all_pctile']:14.3f}   "
                  f"{v['z_top10']:6.2f} / {v['z_rest']:7.2f} / {v['z_total']:7.2f}")
        print("   code traces (top10% pctile / all-704 pctile):",
              {k[:22]: (round(v["top10_pctile"], 3), round(v["all_pctile"], 3)) for k, v in e["code_traces"].items()})


if __name__ == "__main__":
    main()
