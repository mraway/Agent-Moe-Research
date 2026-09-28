"""Frozen, cached-data-only M10 normal-normal mechanism placebo. No detector."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
import resource
import time

import numpy as np

from research_v2 import trm3_g
from research_v4 import codex_g_mech_m9 as m9
from research_v4 import codex_g_mech_m10_math as mm
from research_v4.codex_g_m1 import ROOT, BASE, sha, write_json

OUT = BASE/"mechanism_m10_normal_placebo_v1"
M9_SHA = {"run_manifest.json": "1cee7157cc3772bd706bdd0960e49e415d6ee1fb6f5e41cb3d1987cad68f410b",
          "audit/checks.json": "d45a6721fc591815ba2a05b1f75093dc7c0360d5366315014c02519ff8923648"}
SOURCES = tuple(sorted(set(m9.SOURCES) | {
    "docs/research_v4/codex_g_mech_m10_analysis_spec.md",
    "scripts/research_v4/codex_g_mech_m10.py", "scripts/research_v4/codex_g_mech_m10_math.py",
    "scripts/research_v4/codex_g_mech_m10_audit.py", "tests/test_research_v4_codex_g_mech_m10.py"}))


class M10AccessGuard(m9.m8.m7.m6.M6AccessGuard):
    def check_path(self, path):
        p = super().check_path(path)
        roots = (m9.m8.m7.OUT, m9.m8.OUT, m9.OUT, OUT,
                 m9.m8.m7.m6.M3/"topk_cache/g_dev", m9.m8.m7.m6.M3/"logit_cache/g_dev")
        if ROOT/"artifacts" in p.parents and not any(r == p or r in p.parents for r in roots):
            self.blocked_attempts += 1
            raise PermissionError("M10 reads only frozen M7-M9 and SHA-audited private G-dev caches")
        return p

    def audit(self, event, args):
        super().audit(event, args)
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            p = self.check_path(args[0]); flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC) and ROOT/"artifacts" in p.parents and OUT not in p.parents:
                self.blocked_attempts += 1
                raise PermissionError("M10 cannot alter earlier artifacts")


def source_freeze(expected):
    head = m9.m8.m7.harness.git_output("rev-parse", "HEAD")
    if head != expected or m9.m8.m7.harness.git_output("diff", "HEAD", "--name-only"):
        raise ValueError("M10 requires exact clean tracked source freeze before input access")
    if not set(SOURCES).issubset(set(m9.m8.m7.harness.git_output("ls-files").splitlines())):
        raise ValueError("M10 sources, tests and audit must be committed")
    return head, {p: sha((ROOT/p).read_bytes()) for p in SOURCES}


def prior_inputs():
    logs, hashes = m9.prior_inputs()
    for name, expected in M9_SHA.items():
        p = m9.OUT/name; body = p.read_bytes()
        if sha(body) != expected: raise ValueError("M9 frozen log/audit changed")
        hashes[str(p.relative_to(ROOT))] = expected
    log = json.loads((m9.OUT/"run_manifest.json").read_text())
    assert log["status"] == "completed" and not log["access_guard"]["blocked_attempts"]
    assert json.loads((m9.OUT/"audit/checks.json").read_text())["status"] == "PASS"
    for name, expected in log["output_sha256"].items():
        p = m9.OUT/name
        if sha(p.read_bytes()) != expected: raise ValueError("M9 output changed")
        hashes[str(p.relative_to(ROOT))] = expected
    return logs, hashes


def features_of(a):
    return m9.m8.mm.MatchFeatures(**{k: a[k] for k in ("keys", "episode", "ends", "structure", "tokens", "tu", "valid")})


def frozen_graph(f, g, metadata):
    pools = m9.m8.mm.pool_episodes(metadata)
    scenarios = np.array([metadata[str(k)]["scenario"] for k in f.keys])
    pairs = np.full((len(mm.CELLS), len(g["queries"]), 2), -1, dtype=np.int64)
    reasons = np.zeros(pairs.shape[:2], dtype=np.int64)
    for ci, (name, pool, distinct, caliper) in enumerate(mm.CELLS):
        epids = [ei for ei, key in enumerate(f.keys) if key in pools[pool]]
        bank = np.flatnonzero(np.isin(f.episode, epids) & f.valid)
        matcher = mm.PairMatcher(f, bank, scenarios)
        for qi, q in enumerate(g["queries"]):
            ds, reason = matcher.select(int(q), distinct=distinct, caliper=caliper)
            pairs[ci, qi, :len(ds)] = ds; reasons[ci, qi] = reason
        # All strict pairs must already have a current-token M8 single-donor match.
        pi, vi = (0 if pool == "normal_filtered" else 1), (3 if caliper == .1 else 1)
        assert ((reasons[ci] != 0) | (g["donors"][pi, vi] >= 0).any(1)).all()
        print(json.dumps({"stage": "score_blind_matching", "cell": name,
                          "matched_looks_all_phases": int((reasons[ci] == 0).sum())}), flush=True)
    return {"queries": g["queries"], "phases": g["phases"], "pairs": pairs, "reasons": reasons,
            "cells": np.array([x[0] for x in mm.CELLS]), "phase_names": g["phase_names"]}


def build_current(guard, a, graph, metadata, manifest, inventory):
    keep = (graph["reasons"] == 0).any(0)
    used = np.unique(np.r_[graph["queries"][keep], graph["pairs"][graph["pairs"] >= 0]])
    current = np.empty((len(used), 24, 2)); hashes = {}; max_error = np.zeros(2)
    states = {}
    for fold, block in manifest["cells"]["S"]["folds"].items():
        s = trm3_g.RareSurprisal().load_state(block["statistics"]["S"])
        cw = manifest["cells"]["CW"]["folds"][fold]["statistics"]["CW"]
        assert s._layers == tuple(range(24)) and cw["config"]["strength"] == 1.
        assert cw["config"]["recipe"] == "S_times_one_plus_actual_selected_logit_margin_v1"
        assert cw["q"] == block["statistics"]["S"]["q"]
        assert cw["config"]["rare_threshold"] == s.rare_threshold
        states[fold] = s.surprisal.numpy()
    for ei, key in enumerate(a["keys"]):
        loc = np.flatnonzero(a["episode"][used] == ei)
        if not len(loc): continue
        row = metadata[str(key)]; contents = []
        for p in m9.cache_paths(key):
            body = guard.check_path(p).read_bytes(); rel = str(p.relative_to(ROOT))
            if sha(body) != inventory[rel]: raise ValueError("M7-audited routing cache changed")
            hashes[rel] = sha(body); contents.append(m9.m8.load_bytes(body))
        ids = contents[0]["top_k_ids"].numpy(); tokens = contents[0]["token_ids"].numpy()
        logits = contents[1]["router_logits"].double().numpy()
        assert ids.shape == (24, row["token_count"], 4) and logits.shape == (24, row["token_count"], 32)
        all_terms = m9.dm.token_layer_terms(ids, logits, states[str(row["fold"])])
        ends = a["ends"][used[loc]]
        _, valid, windows = m9.m8.mm.window_geometry(ends, tokens, row["step_output_lengths"])
        assert valid.all()
        np.testing.assert_array_equal(windows, a["tokens"][used[loc]])
        current[loc] = all_terms[ends]
        rebuilt = all_terms[ends[:, None]+np.arange(-7, 1)].mean(1).sum(1)
        np.testing.assert_allclose(rebuilt, a["values"][used[loc], :2], rtol=0, atol=1e-9)
        max_error = np.maximum(max_error, np.abs(rebuilt-a["values"][used[loc], :2]).max(0))
    old = m9.m8.m7.read_npz(m9.OUT/"window_terms.npz")
    overlap, here, there = np.intersect1d(used, old["used"], return_indices=True)
    np.testing.assert_allclose(current[here], old["terms"][there, -1], rtol=0, atol=1e-11)
    return used, current, hashes, {"M8_window_max_error": max_error.tolist(), "M9_overlap_looks": len(overlap)}


def subset(f, metadata, queries, pairs, values, profiles):
    effect = m9.m8.mm.effect_summary(f, metadata, queries, values)
    layers = m9.m8.mm.effect_summary(f, metadata, queries, profiles)
    balance = mm.edge_distances(f, queries, pairs)
    out = {"effect": effect, "layers": layers, "query_distribution": m9.m8.mm.distribution(f, metadata, queries)}
    if not len(queries): return out
    counts = Counter(f.episode[queries].tolist()); reuse = Counter(f.episode[pairs.ravel()].tolist())
    weights, scenario_weights = defaultdict(float), defaultdict(float)
    for q, ds in zip(queries, pairs, strict=True):
        for d in ds:
            ep = int(f.episode[d]); weight = 1/(len(counts)*counts[int(f.episode[q])]*2)
            weights[str(f.keys[ep])] += weight
            scenario_weights[metadata[str(f.keys[ep])]["scenario"]] += weight
    out.update(balance={"columns": ["TU_q_a", "TU_q_b", "TU_a_b", "end_q_a", "end_q_b", "end_a_b"],
                        "episode_equal_mean": m9.m8.mm.plain_mean(f, queries, balance),
                        "edge_max": balance.max(0).tolist()},
               distinct_donor_episodes=len(reuse), distinct_donor_scenarios=len(scenario_weights),
               donor_episode_reuse={str(f.keys[e]): n for e, n in sorted(reuse.items())},
               donor_effective_weight=dict(sorted(weights.items())), max_donor_effective_weight=max(weights.values()),
               donor_scenario_effective_weight=dict(sorted(scenario_weights.items())),
               max_donor_scenario_effective_weight=max(scenario_weights.values()),
               donor_distribution=m9.m8.mm.distribution(f, metadata, pairs.ravel()))
    return out


def summarize(f, metadata, graph, current, lookup):
    result, cards = {}, []
    common = (graph["reasons"] == 0).all(0)
    for hi, phase in enumerate(graph["phase_names"]):
        phase_mask = graph["phases"] == hi; result[str(phase)] = {}
        for ci, (name, _, _, _) in enumerate(mm.CELLS):
            keep = phase_mask & (graph["reasons"][ci] == 0)
            qs, ds = graph["queries"][keep], graph["pairs"][ci, keep]
            values, profiles = mm.triplet_values(current, lookup, qs, ds)
            row = {"candidate_episodes": 126, "candidate_looks": int(phase_mask.sum()),
                   "failure_looks": dict(Counter(mm.REASONS[int(r)] for r in graph["reasons"][ci, phase_mask])),
                   "matched": subset(f, metadata, qs, ds, values, profiles)}
            co = common[keep]
            row["four_cell_common"] = subset(f, metadata, qs[co], ds[co], values[co], profiles[co])
            if ci == 0:
                pos = mm.edge_distances(f, qs, ds)[:, 3:].max(1) <= 16
                row["endpoint_diameter_le16"] = subset(f, metadata, qs[pos], ds[pos], values[pos], profiles[pos])
            result[str(phase)][name] = row
            for q, pair, v in zip(qs, ds, values, strict=True):
                cards.append({"phase": str(phase), "cell": name, "query_row": int(q),
                              "donor_rows": pair.tolist(), "values": v.tolist(),
                              "nodes": [{"row": int(d), "key": str(f.keys[f.episode[d]]),
                                         "scenario": metadata[str(f.keys[f.episode[d]])]["scenario"],
                                         "end": int(f.ends[d]), "tu": float(f.tu[d]),
                                         "token_id": int(f.tokens[d, -1]), "window_ids": f.tokens[d].tolist()}
                                        for d in [q, *pair]]})
    return result, cards


def run(expected):
    started = time.monotonic(); guard = M10AccessGuard(ROOT, "analysis"); guard.install()
    head, sources = source_freeze(expected)
    if OUT.exists(): raise ValueError("refusing to overwrite M10 output")
    logs, hashes = prior_inputs()
    a = m9.m8.m7.read_npz(m9.m8.OUT/"look_inventory.npz")
    g = m9.m8.m7.read_npz(m9.m8.OUT/"match_graph.npz")
    metadata = json.loads((m9.m8.OUT/"episode_metadata.json").read_text())
    f = features_of(a); graph = frozen_graph(f, g, metadata)
    OUT.mkdir(parents=True)
    np.savez_compressed(OUT/"triplet_graph.npz", **graph)
    graph_sha = sha((OUT/"triplet_graph.npz").read_bytes())
    log = {"status": "graph_frozen_before_route_measurement", "implementation_commit": head,
           "source_sha256": sources, "input_sha256": hashes, "graph_sha256": graph_sha,
           "started_utc": datetime.now(timezone.utc).isoformat(),
           "evidence_role": "G-dev development mechanism placebo; no threshold, detector, causal or confirmatory inference"}
    write_json(OUT/"run_manifest.json", log)
    manifest = json.loads((m9.m8.m7.OUT/"calibrate/threshold_manifest.json").read_text())
    used, current, cache_hashes, replay = build_current(guard, a, graph, metadata, manifest, logs["score"]["input_sha256"])
    lookup = np.full(len(a["ends"]), -1, dtype=np.int64); lookup[used] = np.arange(len(used))
    np.savez_compressed(OUT/"current_terms.npz", used=used, current=current)
    matrix, cards = summarize(f, metadata, graph, current, lookup)
    write_json(OUT/"result.json", {"matrix": matrix, "columns": mm.COLUMNS, "layer_columns": mm.LAYER_COLUMNS,
                                  "used_looks": len(used), "replay": replay,
                                  "uncertainty": "query-family bootstrap conditional on selected normal bank/graph; not causal, no detector rates"})
    write_json(OUT/"triplet_cards.json", {"columns": mm.COLUMNS, "cards": cards})
    log["input_sha256"].update(cache_hashes)
    for path, expected_sha in log["input_sha256"].items():
        if sha((ROOT/path).read_bytes()) != expected_sha: raise ValueError("input changed during M10")
    if source_freeze(expected) != (head, sources): raise ValueError("sources changed during M10")
    assert sha((OUT/"triplet_graph.npz").read_bytes()) == graph_sha
    log.update(status="completed", finished_utc=datetime.now(timezone.utc).isoformat(),
               elapsed_seconds=time.monotonic()-started, peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2,
               access_guard=guard.summary(),
               output_sha256={p.name: sha(p.read_bytes()) for p in sorted(OUT.iterdir()) if p.is_file() and p.name != "run_manifest.json"})
    assert not log["access_guard"]["blocked_attempts"]
    write_json(OUT/"run_manifest.json", log)
    print(json.dumps({k: log[k] for k in ("status", "implementation_commit", "elapsed_seconds", "peak_rss_gib")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--freeze-commit", required=True)
    run(parser.parse_args().freeze_commit)
