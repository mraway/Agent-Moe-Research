"""Cluster bootstrap + power for the P1 R+8 difference (LENS = statistics). Read-only."""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_statistics_lib as L  # noqa: E402
import audit_statistics_recompute as R  # noqa: E402

SEED = 20260906
DRAWS = 1000
INDEX = {
    "b2": L.ROOT / "artifacts/agent_v2/agent_v2_5_b2/sample_index.jsonl",
    "b1": L.ROOT / "artifacts/agent_v2/agent_v2_5_b1/sample_index.jsonl",
    "h384": L.ROOT / "artifacts/agent_v2/agent_v2_5_b2_horizon384/sample_index.jsonl",
}
CELLS = {"b2": "final_b2_both", "b1": "final_b1_both", "h384": "final_h384_both"}


def families(tag):
    out = {}
    for row in R.read_jsonl(INDEX[tag]):
        batch = "h384" if tag == "h384" else tag
        out[f"{row['trace_id']}"] = row["attack_family_id"]
    return out


def boot(hits_a, hits_b, clusters, draws=DRAWS, seed=SEED, need_net=3):
    keys = sorted(hits_a)
    by_cluster = {}
    for k in keys:
        by_cluster.setdefault(clusters[k], []).append(k)
    names = sorted(by_cluster)
    rng = random.Random(seed)
    diffs_c, diffs_t = [], []
    for _ in range(draws):
        pick = [by_cluster[names[rng.randrange(len(names))]] for _ in names]
        flat = [k for grp in pick for k in grp]
        if not flat:
            continue
        a = sum(hits_a[k] for k in flat) / len(flat)
        b = sum(hits_b[k] for k in flat) / len(flat)
        diffs_c.append(a - b)
    rng2 = random.Random(seed)
    for _ in range(draws):
        flat = [keys[rng2.randrange(len(keys))] for _ in keys]
        a = sum(hits_a[k] for k in flat) / len(flat)
        b = sum(hits_b[k] for k in flat) / len(flat)
        diffs_t.append(a - b)

    n_pos = len(keys)

    def summarize(d):
        d = sorted(d)
        n = len(d)
        target = float(need_net) / n_pos
        return {
            "mean": sum(d) / n,
            "ci95": (d[int(0.025 * n)], d[min(n - 1, int(0.975 * n))]),
            "p_gt_0": sum(1 for x in d if x > 0) / n,
            "prereg_target": target,
            "p_ge_prereg_target": sum(1 for x in d if x >= target - 1e-12) / n,
        }

    return {"cluster": summarize(diffs_c), "trace": summarize(diffs_t),
            "n_clusters": len(names)}


OUT = {}
for tag, cell in CELLS.items():
    d = L.load(cell)
    fam = families(tag)
    OUT[tag] = {}
    for col in d["calibration_columns"]:
        C = d["columns"][col]
        trm = C["variants"]["trm3"]["sets"]["target"]
        summaries = trm["summaries"]
        anch = (R.h384_anchor_maps()[0] if tag == "h384" else R.core_anchors(summaries, "product"))
        pos = [s["key"] for s in R.positives_of(summaries, anch)]
        clusters = {k: fam[k.split("|", 1)[1]] for k in pos}
        smt = {s["key"]: s for s in summaries}
        hits_t = {k: R.anchor_hit(smt[k]["alarm_ends"], smt[k]["last_end"], anch[k])["hit_plus_8"] for k in pos}
        pm = C["p1_matched_alpha"]
        hits_bm_matched = {k: bool(v) for k, v in
                           list(pm["b_m_at_matched_alpha"].values())[0]["primary_event_hits_plus_8"].items()}
        need = 4 if tag == "h384" else 3  # prereg section 7 net thresholds
        smm = {s["key"]: s for s in C["variants"]["m_only"]["sets"]["target"]["summaries"]}
        hits_bm_nom = {k: R.anchor_hit(smm[k]["alarm_ends"], smm[k]["last_end"], anch[k])["hit_plus_8"] for k in pos}
        OUT[tag][col] = {
            "matched": boot(hits_t, hits_bm_matched, clusters, need_net=need),
            "nominal": boot(hits_t, hits_bm_nom, clusters, need_net=need),
            "observed_diff_matched": sum(hits_t.values()) / len(pos) - sum(hits_bm_matched[k] for k in pos) / len(pos),
            "observed_diff_nominal": sum(hits_t.values()) / len(pos) - sum(hits_bm_nom.values()) / len(pos),
        }
print(json.dumps(OUT, indent=1))
