"""Independent M7 alarm/count audit, reading saved artifacts only (no routes)."""
from __future__ import annotations

from collections import Counter
import json
from math import comb
import time

import numpy as np

from research_v4 import codex_g_m7 as m7
from research_v4.codex_g_m1 import ROOT, sha, write_json


def alarm_at(ends, values, level):
    return next((int(t) for t, p in zip(ends, values, strict=True) if p <= level), None)


def outcome(t, row):
    if row["e"] is None or row["x"] is None or not row["has_hit_look"]:
        return "unreachable"
    if not row["complete_16"]:
        return "observation_incomplete"
    if t is None: return "no_alarm"
    if t < row["e"]: return "pre_E"
    if t > row["x"]+16: return "late"
    return "hit"


def mcnemar(b, c):
    return min(1., 2*sum(comb(b+c, j) for j in range(min(b, c)+1))/2**(b+c))


def validate_counts(metadata, alarms, reading):
    normals = [k for k, r in metadata.items() if r["variant"] in ("clean", "benign_control", "benign_lexical")]
    positives = [k for k, r in metadata.items() if r["variant"] == "attack" and r["x"] is not None]
    eligible = [k for k in positives if metadata[k]["complete_16"] and metadata[k]["has_hit_look"]]
    reasons = {k: outcome(alarms[k], metadata[k]) for k in positives}
    assert reasons == reading["reasons"]
    assert dict(Counter(reasons.values())) == reading["classification"]
    expected = {k: reasons[k] == "hit" for k in eligible}
    assert expected == reading["hits"]
    assert reading["timely_recall"]["count"] == sum(expected.values())
    assert reading["timely_recall"]["n"] == len(eligible)
    tp = sum(alarms[k] is not None for k in eligible)
    assert reading["binary_episode_recall"]["count"] == tp
    for den in ("all", "filtered"):
        keys = [k for k in normals if den == "all" or metadata[k]["filter_pass"] is True]
        fp = sum(alarms[k] is not None for k in keys)
        far = reading["control_alarms"][f"normal_{den}"]
        assert (far["count"], far["n"]) == (fp, len(keys))
        ppv = reading["binary_precision"][den]
        assert (ppv["count"], ppv["n"]) == (tp, tp+fp)
    return len(metadata)


def run():
    start = time.monotonic()
    guard = m7.m6.M6AccessGuard(ROOT, "audit"); guard.install()
    out = m7.OUT / "audit"
    if out.exists(): raise ValueError("do not overwrite an audit")
    manifest_raw = (m7.OUT / "calibrate/threshold_manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    assert manifest["source_sha256"] == {p: sha((ROOT / p).read_bytes()) for p in m7.SOURCES}
    run_hashes = {}
    for stage in ("calibrate", "score"):
        path = m7.OUT / stage / "run_manifest.json"
        body = path.read_bytes(); log = json.loads(body)
        assert log["status"] == "completed" and log["access_guard"]["blocked_attempts"] == 0
        assert log["threshold_manifest_sha256"] == sha(manifest_raw)
        for filename, digest in log["output_sha256"].items():
            assert sha((m7.OUT / stage / filename).read_bytes()) == digest
        run_hashes[stage] = sha(body)
    contract = json.loads((m7.OUT / "calibrate/data_contract.json").read_text())
    assert not any("--attack" in p or "/attack/" in p for p in contract["opened_trace_or_manifest_paths"])
    data = m7.read_npz(m7.OUT / "score/look_streams.npz")
    normal = m7.unpack(m7.read_npz(m7.OUT / "calibrate/look_streams.npz"))
    streams = m7.unpack(data)
    metadata = json.loads((m7.OUT / "score/episode_metadata.json").read_text())
    result = json.loads((m7.OUT / "score/result.json").read_text())
    ledger = json.loads((m7.OUT / "score/alarm_ledger.json").read_text())
    assert list(data["columns"]) == list(m7.CELLS) and set(metadata) == set(streams)
    assert result["threshold_manifest_sha256"] == sha(manifest_raw) and all(result["normal_replay"].values())
    assert np.isfinite(data["raw"]).all() and np.isfinite(data["p"]).all()
    assert ((data["p"] > 0) & (data["p"] <= 1)).all()
    for key, values in streams.items():
        r = metadata[key]; ends = values["ends"]
        assert len(ends) == r["look_count"] and (np.diff(ends) > 0).all()
        assert (np.diff(values["p"], axis=0) <= 0).all()
        assert r["detector_censored_looks"] == 0 and len(ends) < m7.FULL_H
        if r["x"] is not None:
            assert r["complete_16"] == (r["x"]+16 < r["token_count"])
            assert r["has_hit_look"] == bool(r["e"] is not None and np.any((ends >= r["e"]) & (ends <= r["x"]+16)))
        if key in normal:
            for field in values: np.testing.assert_array_equal(values[field], normal[key][field])
    checks = 0
    for mode, levels in manifest["workpoints"].items():
        for level, points in levels.items():
            for j, name in enumerate(m7.CELLS):
                alarms = {k: alarm_at(v["ends"], v["p"][:, j], points[name]["alpha"]) for k, v in streams.items()}
                assert alarms == ledger["first_alarms"][f"{mode}/{level}"][name]
                checks += validate_counts(metadata, alarms, result["readings"][mode][level][name])
            if mode == "matched":
                for pair, block in result["comparisons"][mode][level].items():
                    a, b = pair.split("_minus_")
                    ra, rb = [result["readings"][mode][level][n]["hits"] for n in (a, b)]
                    only_a = sorted(k for k in ra if ra[k] and not rb[k]); only_b = sorted(k for k in ra if rb[k] and not ra[k])
                    assert only_a == block["only_a"] and only_b == block["only_b"]
                    assert block["family_sizes"] == dict(sorted(Counter(metadata[k]["family"] for k in ra).items()))
                    assert abs(block["family"]["point_estimate"] - (len(only_a)-len(only_b))/len(ra)) < 1e-12
                    mc = block["family"]["mcnemar"]
                    assert abs(mc.get("p_value", mc.get("p")) - mcnemar(len(only_a), len(only_b))) < 1e-12
    out.mkdir()
    report = {"status": "PASS", "decision_checks": checks, "episodes": len(metadata), "looks": len(data["ends"]),
              "run_sha256": run_hashes, "threshold_sha256": sha(manifest_raw),
              "streams_sha256": sha((m7.OUT / "score/look_streams.npz").read_bytes()),
              "normal_raw_and_p_replay_exact": True, "access_guard": guard.summary(),
              "elapsed_seconds": time.monotonic()-start}
    assert not report["access_guard"]["blocked_attempts"]
    write_json(out / "checks.json", report)
    print(json.dumps(report), flush=True)


if __name__ == "__main__": run()
