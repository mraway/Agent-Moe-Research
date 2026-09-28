"""Post-result M7 descriptive ledger; no threshold/algorithm/result changes.

Explicitly added after primary results: all-arm trajectory-class controls complement
the frozen runner's attack-only class controls, and low-p attainability is separated
from observation censoring. This does not constitute another preregistered test.
"""
from __future__ import annotations

from collections import Counter
import json

from research_v4 import codex_g_m7 as m7
from research_v4.codex_g_m1 import ROOT, sha, write_json


def class_controls(metadata, alarms):
    out = {}
    for name in ("over_refusal", "engaged_only", "committed_no_execution"):
        all_keys = [k for k, r in metadata.items() if r["trajectory_class"] == name]
        attack_keys = [k for k in all_keys if metadata[k]["variant"] == "attack"]
        out[name] = {scope: {"alarms": sum(alarms[k] is not None for k in keys), "n": len(keys)}
                     for scope, keys in (("all_arms", all_keys), ("attack_arm", attack_keys))}
    return out


def resolution(metadata, alarms, alpha, support):
    out = {}
    for fold, spec in support.items():
        keys = [k for k, r in metadata.items() if r["variant"] == "attack" and r["x"] is not None and str(r["fold"]) == fold]
        attainable = alpha >= spec["min_possible_p"]
        alarms_count = sum(alarms[k] is not None for k in keys)
        if not attainable: assert alarms_count == 0
        out[fold] = {"n_positive": len(keys), "alarm_count": alarms_count, "alpha": alpha,
                     "min_possible_p": spec["min_possible_p"], "attainable": attainable,
                     "calibration_forced_silent_count": 0 if attainable else len(keys)}
    return out


def legacy_state(alarm, row):
    valid = alarm is not None and row["e"] is not None and row["e"] <= alarm <= min(row["x"]+16, row["old_h_end"])
    return bool(valid)


def run():
    guard = m7.m6.M6AccessGuard(ROOT, "readouts"); guard.install()
    out = m7.OUT / "posthoc"
    if out.exists(): raise ValueError("do not overwrite posthoc output")
    audit = json.loads((m7.OUT / "audit/checks.json").read_text())
    assert audit["status"] == "PASS"
    inputs = {}
    def read(path, expected=None):
        body = path.read_bytes(); digest = sha(body)
        if expected is not None: assert digest == expected
        inputs[str(path.relative_to(ROOT))] = digest
        return json.loads(body)
    logs = {stage: read(m7.OUT / stage / "run_manifest.json", audit["run_sha256"][stage]) for stage in ("calibrate", "score")}
    def score(name): return read(m7.OUT / "score" / name, logs["score"]["output_sha256"][name])
    meta, ledger, result = score("episode_metadata.json"), score("alarm_ledger.json"), score("result.json")
    manifest = read(m7.OUT / "calibrate/threshold_manifest.json", audit["threshold_sha256"])
    controls, support = {}, {}
    for mode, levels in manifest["workpoints"].items():
        for level, points in levels.items():
            key = f"{mode}/{level}"
            controls[key] = {}; support[key] = {}
            for name, alarms in ledger["first_alarms"][key].items():
                controls[key][name] = class_controls(meta, alarms)
                support[key][name] = resolution(meta, alarms, points[name]["alpha"], manifest["support"][name])
    transitions, timing = {}, {}
    for level in ("0.1", "0.01"):
        firsts = ledger["first_alarms"][f"matched/{level}"]
        readings = result["readings"]["matched"][level]
        transitions[level] = {}
        for name in ("S", "CW"):
            rows = []
            for key, old in ledger["M6_matched_first_alarms"].items():
                m = meta[key]
                rows.append({"key": key, "old_observation_incomplete": m["old_incomplete_16"],
                             "old_hit": legacy_state(old[name]["alarm"], m), "new_reason": readings[name]["reasons"][key],
                             "old_alarm": old[name]["alarm"], "new_alarm": firsts[name][key],
                             "e": m["e"], "x": m["x"], "old_h_end": m["old_h_end"], "new_last_look": m["last_look"]})
            transitions[level][name] = {"rows": rows, "counts": dict(Counter(
                f'cut={r["old_observation_incomplete"]}|old_hit={r["old_hit"]}|new={r["new_reason"]}' for r in rows))}
        timing[level] = {}
        for a, b in (("S", "TU"), ("CW", "TU"), ("CW", "S")):
            keys = [k for k in readings[a]["hits"] if readings[a]["hits"][k] and readings[b]["hits"][k]]
            timing[level][f"{a}_vs_{b}"] = {"both_timely": len(keys),
                "earlier_a": sum(firsts[a][k] < firsts[b][k] for k in keys),
                "same": sum(firsts[a][k] == firsts[b][k] for k in keys),
                "later_a": sum(firsts[a][k] > firsts[b][k] for k in keys),
                "note": "conditional on BOTH timely hits, not unconditional lead-time gain"}
    out.mkdir()
    write_json(out / "readouts.json", {"role": "post-result descriptive audit; no new main comparison",
               "class_scope_note": "M7 main control_alarms.over_refusal is attack-only 53; planned all-arm 59 is supplied here, keeping both explicit",
               "all_class_controls": controls, "p_resolution": support, "legacy_transitions": transitions,
               "paired_timing": timing, "scope_limit": "benign mention is not authorized cross-task execution; absent matched authorization controls remain unidentified"})
    sources = {p: sha((ROOT / p).read_bytes()) for p in ("scripts/research_v4/codex_g_m7_readouts.py", "tests/test_research_v4_codex_g_m7_readouts.py")}
    write_json(out / "manifest.json", {"input_sha256": inputs, "source_sha256": sources,
               "readouts_sha256": sha((out / "readouts.json").read_bytes()), "access_guard": guard.summary()})
    assert not guard.blocked_attempts
    print(json.dumps({"class_controls_at_matched_10pct": controls["matched/0.1"], "timing": timing}), flush=True)


if __name__ == "__main__": run()
