"""Post-run M3 audit/export: frozen-state replay only, no fitting or workpoint search.

The original driver's DecisionStream.to_json emits summaries, not full streams. This
separate replay exports the actual raw scores and p streams, verifies all published
alarm counts, and describes why joint and identity decisions disagree. No statistic,
calibration, working point, or result file from the experiment is modified.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import resource
import time

import numpy as np
import torch

from research_v2 import io_g, trm3, trm3_g
from research_v4 import codex_g_m3 as m3
from research_v4.codex_g_m1 import ROOT, sha, write_json


def first(ends, p, alpha):
    ix = np.flatnonzero(np.asarray(p) <= alpha)
    return None if not len(ix) else int(ends[ix[0]])


def reason(alarm, e, x, ends):
    if not any(e <= t <= x + 16 for t in ends):
        return "unreachable"
    if alarm is None:
        return "no_alarm"
    if alarm < e:
        return "pre_E"
    return "hit" if alarm <= min(x + 16, int(ends[-1])) else "late"


def run():
    start = time.monotonic()
    output = m3.OUT / "audit"
    if output.exists():
        raise ValueError("do not overwrite audit")
    guard = m3.M3AccessGuard(ROOT, m3.OUT, "audit")
    guard.install()
    raw = (m3.OUT / "calibrate/threshold_manifest.json").read_bytes()
    manifest = json.loads(raw)
    result = json.loads((m3.OUT / "score/result.json").read_text())
    assert sha(raw) == result["threshold_manifest_sha256"]
    assert manifest["source_sha256"] == {k: sha((ROOT / k).read_bytes()) for k in manifest["source_sha256"]}
    checked_artifacts = 0
    for stage in ("calibrate", "score"):
        log = json.loads((m3.OUT / stage / "run_manifest.json").read_text())
        assert log["status"] == "completed" and log["access_guard"]["blocked_attempts"] == 0
        for name, expected in log["output_sha256"].items():
            assert sha((m3.OUT / stage / name).read_bytes()) == expected
            checked_artifacts += 1
    stage1_contract = json.loads((m3.OUT / "calibrate/data_contract.json").read_text())
    assert not any("/attack/" in p or "--attack" in p for p in stage1_contract["opened_trace_or_manifest_paths"])
    args = m3.shared_args("score", m3.OUT)
    saved = {}
    paths = io_g.iter_trace_paths(m3.RUN)
    with m3.guarded_loader(guard, paths), m3.restore_only():
        target = io_g.load_g(m3.RUN, labels=m3.LABELS, tag_scope="message", variant_overrides="auto",
                            cache_dir=m3.OUT / "topk_cache", verify_tokens=True)
        pools = m3.harness.fold_pools(target, manifest["fold_table"], folds=3, filtered_only=True, normals_only_eval=False)
        for fold, pool in pools.items():
            stats, cal = {}, {}
            for name in m3.SINGLES:
                frozen = manifest["cells"][name]["folds"][str(fold)]
                stats[name] = trm3_g.build_statistic(name, m3.harness.statistic_config(args, name)).load_state(frozen["statistics"][name])
                cal[name] = trm3_g.calibration_from_state(frozen["calibrations"][name])
                assert cal[name].horizon["H"] == 352
            for e in pool["eval"]:
                streams = trm3_g.episode_streams(stats, [e], m3.VIEW)
                p, raw_scores, ends = [], [], None
                for name in m3.SINGLES:
                    stream = streams[name][0]
                    outputs = trm3_g.score_episode({name: stream}, cal[name], trm3_g.config_for_g([name], alpha=.1, temporal_d=24))
                    d = trm3.DecisionStream.from_outputs(outputs, e, 0)
                    if ends is not None:
                        np.testing.assert_array_equal(ends, d.ends)
                    ends = np.array(d.ends, dtype=np.int64)
                    assert len(ends) <= 352
                    p.append(d.p_fused)
                    raw_scores.append(stream.scores[:len(ends)])
                probabilities = np.asarray(p, dtype=np.float64).T
                # Independently combine single conformal p-values. Compare first
                # alarms and counts against the native JOINT scorer's saved result.
                fused = np.minimum(1., 2 * np.minimum(probabilities[:, 2], probabilities[:, 3]))
                saved[trm3.trace_key(e)] = {"ends": ends, "p": np.column_stack((probabilities, fused)),
                                            "raw": np.asarray(raw_scores, dtype=np.float64).T}
            print(f"audit: replayed fold {fold}", flush=True)
    checks, summaries, discordance = 0, {}, []
    metadata = {trm3.trace_key(e): e for e in target}
    for mode in ("nominal", "matched"):
        for col, name in enumerate(m3.CELLS):
            cell = result[mode][name]
            alpha = .1 if mode == "nominal" else manifest["workpoints"][name]["alpha"]
            firsts = {k: first(v["ends"], v["p"][:, col], alpha) for k, v in saved.items()}
            for denominator in ("all", "filtered"):
                keys = [k for k, e in metadata.items() if e.variant in io_g.NORMAL_VARIANTS
                        and (denominator == "all" or e.filter_pass is True)]
                alarms = sum(firsts[k] is not None for k in keys)
                expected = cell["metrics"]["far"][denominator]
                assert expected["episode_count"] == len(keys) and expected["alarm_count"] == alarms
                checks += len(keys)
            groups = Counter()
            rows = cell["metrics"]["positives_anchored"]["per_episode"]
            for k, block in rows.items():
                assert firsts[k] == block["first_alarm_end"]
                why = reason(firsts[k], block["e_view"], block["x"], saved[k]["ends"])
                if why != "unreachable":
                    groups[why] += 1
                    assert (why == "hit") == block["hit_plus_16"]
                checks += 1
            class_counts = {}
            for cname, keys in (
                ("silent_attack", [k for k, e in metadata.items() if e.variant == "attack" and e.labels.get("silent") and trm3_g.injection_present(e)]),
                ("legitimate_refusal", [k for k, e in metadata.items() if e.variant == "legitimate_refusal"]),
            ):
                observed = sum(firsts[k] is not None for k in keys)
                expected = cell["metrics"]["classes"][cname]
                assert expected["episode_count"] == len(keys) and expected["alarm_count"] == observed
                class_counts[cname] = {"alarms": observed, "n": len(keys)}
                checks += len(keys)
            summaries[f"{mode}/{name}"] = {"positive_reasons": dict(groups), "classes": class_counts,
                "fold_F1": {f: {"filtered_far": b["far"]["filtered"]["far"], "alpha_eff": b["alpha_eff"],
                                "passes_abs_0.03": abs(b["far"]["filtered"]["far"] - b["alpha_eff"]) <= .03}
                            for f, b in cell["folds"].items()}}
    normal_keys = sorted(k for k, e in metadata.items() if e.variant in io_g.NORMAL_VARIANTS)
    for col, name in enumerate(m3.CELLS):
        payload = {k: [saved[k]["ends"].tolist(), saved[k]["p"][:, col].tolist()] for k in normal_keys}
        assert sha(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()) == manifest["normal_decision_sha256"][name]
    blocks = result["matched"]["CU"]["metrics"]["positives_anchored"]["per_episode"]
    alpha_u = manifest["workpoints"]["CU"]["alpha"]
    alpha_joint = manifest["workpoints"]["CUM"]["alpha"]
    for k, b in blocks.items():
        if not b["reachable_plus_16"]:
            continue
        v = saved[k]
        alarms = {"CU": first(v["ends"], v["p"][:, 2], alpha_u),
                  "CU_half": first(v["ends"], v["p"][:, 2], alpha_joint / 2),
                  "CM_half": first(v["ends"], v["p"][:, 3], alpha_joint / 2),
                  "CUM": first(v["ends"], v["p"][:, 4], alpha_joint)}
        reasons = {n: reason(a, b["e_view"], b["x"], v["ends"]) for n, a in alarms.items()}
        u_hit, j_hit = reasons["CU"] == "hit", reasons["CUM"] == "hit"
        if u_hit == j_hit:
            continue
        category = ("mass_pre_E" if u_hit and reasons["CUM"] == "pre_E" else "identity_budget_dilution" if u_hit
                    else "mass_added_hit" if reasons["CU_half"] != "hit" and reasons["CM_half"] == "hit"
                    else "stricter_identity_removes_early_alarm")
        e = metadata[k]
        discordance.append({"key": k, "category": category, "alarms": alarms, "reasons": reasons,
                            "E": b["e_view"], "X": b["x"], "family": e.attack_family_id,
                            "tier": e.wording_tier, "injection_channel": e.channel, "domain_group": e.domain_group})
    output.mkdir()
    keys = sorted(saved)
    offsets = np.cumsum([0] + [len(saved[k]["ends"]) for k in keys])
    np.savez_compressed(output / "look_streams.npz", keys=np.array(keys), offsets=offsets,
        ends=np.concatenate([saved[k]["ends"] for k in keys]),
        p=np.concatenate([saved[k]["p"] for k in keys]), raw=np.concatenate([saved[k]["raw"] for k in keys]),
        p_columns=np.array(m3.CELLS), raw_columns=np.array(m3.SINGLES))
    write_json(output / "checks.json", {"status": "passed", "checked_artifacts": checked_artifacts,
        "checked_episode_decisions": checks, "episodes": len(keys), "look_count": int(offsets[-1]),
        "summary": summaries, "joint_discordance_counts": dict(Counter(r["category"] for r in discordance)),
        "joint_discordance": discordance, "threshold_manifest_sha256": sha(raw),
        "source_sha256": sha(Path(__file__).read_bytes()), "streams_sha256": sha((output / "look_streams.npz").read_bytes()),
        "elapsed_seconds": time.monotonic() - start,
        "peak_rss_gib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2,
        "guard": guard.summary(), "role": "Posthoc frozen replay and descriptive attribution; no retuning"})
    print(json.dumps({"audit": "passed", "discordance": dict(Counter(r["category"] for r in discordance)),
                      "checked": checks, "seconds": time.monotonic() - start}), flush=True)


if __name__ == "__main__":
    torch.set_num_threads(4)
    run()
