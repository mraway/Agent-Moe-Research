"""Post-result descriptive M6 error/timing ledger; no new scores or workpoints."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import time

import numpy as np

from research_v2 import io_g
from research_v4 import codex_g_m6 as m6
from research_v4.codex_g_m1 import ROOT, sha, write_json
from research_v4.codex_g_m6_audit import first, read_arrays


def paired_binary(a, b, keys):
    """No inferential p value: normal selection/calibration induces dependence."""
    return {"n": len(keys), "both": sum(a[k] and b[k] for k in keys),
            "only_baseline": sorted(k for k in keys if a[k] and not b[k]),
            "only_candidate": sorted(k for k in keys if b[k] and not a[k]),
            "neither": sum(not a[k] and not b[k] for k in keys)}


def paired_timing(a, b, keys):
    delta = [b[k] - a[k] for k in keys]
    return {"n": len(keys), "earlier": sum(d < 0 for d in delta), "equal": sum(d == 0 for d in delta),
            "later": sum(d > 0 for d in delta), "median_candidate_minus_baseline_tokens": float(np.median(delta)) if delta else None,
            "note": "Posthoc, conditional on BOTH detectors hitting; not unconditional latency, survival analysis or evidence of superiority"}


def run():
    start = time.monotonic()
    guard = m6.M6AccessGuard(ROOT, "readouts"); guard.install()
    out = m6.OUT / "audit/descriptive_readouts.json"
    if out.exists(): raise ValueError("do not overwrite M6 descriptive ledger")
    audit_path = m6.OUT / "audit/checks.json"
    audit = json.loads(audit_path.read_text())
    assert audit["status"] == "passed"
    for stage, expected in audit["run_manifest_sha256"].items():
        log = m6.checked_json(m6.OUT / stage / "run_manifest.json", expected)
        for name, digest in log["output_sha256"].items():
            assert sha((m6.OUT / stage / name).read_bytes()) == digest
    for name, expected in audit["source_sha256"].items():
        assert sha((ROOT / name).read_bytes()) == expected
    threshold = m6.checked_json(m6.OUT / "calibrate/threshold_manifest.json", audit["threshold_manifest_sha256"])
    data = read_arrays(m6.OUT / "score/look_streams.npz")
    result = json.loads((m6.OUT / "score/result.json").read_text())
    metadata = json.loads((m6.OUT / "score/episode_metadata.json").read_text())
    saved = {key: {field: data[field][data["offsets"][i]:data["offsets"][i+1]] for field in ("p", "ends", "tags")}
             for i, key in enumerate(data["keys"])}
    output = {}
    for mode in ("nominal", "matched"):
        firsts, alarms = {}, {}
        for col, name in enumerate(m6.CELLS):
            alpha = .1 if mode == "nominal" else threshold["workpoints"][name]["alpha"]
            firsts[name] = {k: first(v["ends"], v["p"][:, col], alpha) for k, v in saved.items()}
            alarms[name] = {k: t is not None for k, t in firsts[name].items()}
        normals = [k for k, m in metadata.items() if m["variant"] in io_g.NORMAL_VARIANTS]
        filtered = [k for k in normals if metadata[k]["filter_pass"] is True]
        block = {"pairs": {}}
        for name in ("CW", "CH"):
            base = result[mode]["S"]["metrics"]["positives_anchored"]["per_episode"]
            candidate = result[mode][name]["metrics"]["positives_anchored"]["per_episode"]
            common_hits = [k for k, r in base.items() if r["reachable_plus_16"] and r["hit_plus_16"] and candidate[k]["hit_plus_16"]]
            block["pairs"][name] = {
                "normal_all": paired_binary(alarms["S"], alarms[name], normals),
                "normal_filtered": paired_binary(alarms["S"], alarms[name], filtered),
                "common_hit_timing": paired_timing(firsts["S"], firsts[name], common_hits),
            }
        positives = result[mode]["S"]["metrics"]["positives_anchored"]["per_episode"]
        misses = []
        for key, row in positives.items():
            if not row["reachable_plus_16"] or any(result[mode][n]["metrics"]["positives_anchored"]["per_episode"][key]["hit_plus_16"] for n in m6.CELLS):
                continue
            end = int(saved[key]["ends"][-1])
            misses.append({"key": key, **metadata[key], "E": row["e_view"], "X": row["x"], "H_end": end,
                "X_beyond_H": row["x"] > end, "firsts": {n: firsts[n][key] for n in m6.CELLS}})
        block["common_misses"] = {"n": len(misses), "X_beyond_H": sum(m["X_beyond_H"] for m in misses), "rows": misses,
            "by_family": dict(Counter(m["family"] for m in misses)), "by_domain": dict(Counter(m["domain_group"] for m in misses))}
        examples = {}
        for key in ("g_dev|g-dev-153--attack#ep0", "g_dev|g-dev-177--attack#ep0"):
            v = saved[key]
            examples[key] = {"selection": "Posthoc: the only newly hit episodes, not a predefined cohort", "E": positives[key]["e_view"],
                "X": positives[key]["x"], "H_end": int(v["ends"][-1]), "firsts": firsts_by_key(firsts, key), "alarm_channels": {}}
            for name in m6.CELLS:
                t = firsts[name][key]
                examples[key]["alarm_channels"][name] = None if t is None else str(v["tags"][np.flatnonzero(v["ends"] == t)[0]])
        block["examples"] = examples
        output[mode] = block
    payload = {"status": "completed", "role": "POSTHOC descriptive, frozen M6 scores only; no data/model/threshold changes",
        "inputs": {"audit_sha256": sha(audit_path.read_bytes()), "threshold_sha256": audit["threshold_manifest_sha256"],
                   "streams_sha256": audit["streams_sha256"]},
        "source_sha256": sha(Path(__file__).read_bytes()), "results": output,
        "elapsed_seconds": time.monotonic() - start, "guard": guard.summary()}
    write_json(out, payload)
    for mode, b in output.items():
        print(json.dumps({"mode": mode, "pairs": {n: {k: (v if k == "common_hit_timing" else
            {f: len(x) if isinstance(x, list) else x for f, x in v.items()}) for k, v in p.items()} for n, p in b["pairs"].items()},
            "common_misses": {k: v for k, v in b["common_misses"].items() if k != "rows"}, "examples": b["examples"]}))


def firsts_by_key(firsts, key):
    return {n: firsts[n][key] for n in m6.CELLS}


if __name__ == "__main__":
    run()
