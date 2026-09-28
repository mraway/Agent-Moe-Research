"""M12: frozen local-history controls, complete G-dev episodes, CPU caches only."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
import resource
import time
import numpy as np

from research_v4 import codex_g_mech_m11 as m11
from research_v4 import codex_g_mech_m12_math as mm
from research_v4.codex_g_m1 import ROOT, BASE, sha, write_json

m10 = m11.m10
m8 = m10.m9.m8
OUT = BASE/"mechanism_m12_history_control_v1"
M11_SHA = {"run_manifest.json": "e894ef194a4300ab093c7b0c04beb3463bb18a712aef5d8693f98d72bdd51b5f",
           "audit/checks.json": "e30deb3e8acd762b74ba0304d23c8face4f5d3c5ef95ea44dd2cb3ce4c4570e6"}
SOURCES = tuple(sorted(set(m11.SOURCES) | {
    "docs/research_v4/codex_g_mech_m12_analysis_spec.md", "scripts/research_v4/codex_g_mech_m12.py",
    "scripts/research_v4/codex_g_mech_m12_math.py", "scripts/research_v4/codex_g_mech_m12_audit.py",
    "tests/test_research_v4_codex_g_mech_m12.py"}))


class M12AccessGuard(m8.m7.m6.M6AccessGuard):
    def check_path(self, path):
        p = super().check_path(path)
        roots = (m8.m7.OUT, m8.OUT, m10.m9.OUT, m10.OUT, m11.OUT, OUT,
                 m8.m7.m6.M3/"topk_cache/g_dev", m8.m7.m6.M3/"logit_cache/g_dev")
        if ROOT/"artifacts" in p.parents and not any(r == p or r in p.parents for r in roots):
            self.blocked_attempts += 1
            raise PermissionError("M12 reads only frozen M7-M11 and hashed private G-dev caches")
        return p

    def audit(self, event, args):
        super().audit(event, args)
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            p = self.check_path(args[0]); flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC) and ROOT/"artifacts" in p.parents and OUT not in p.parents:
                self.blocked_attempts += 1
                raise PermissionError("M12 cannot write earlier or other-line artifacts")


def source_freeze(expected):
    git = m8.m7.harness.git_output; head = git("rev-parse", "HEAD")
    if head != expected or git("diff", "HEAD", "--name-only"):
        raise ValueError("M12 requires exact clean tracked source freeze before data access")
    if not set(SOURCES).issubset(set(git("ls-files").splitlines())):
        raise ValueError("M12 plan, implementation, tests and audit must be committed")
    return head, {p: sha((ROOT/p).read_bytes()) for p in SOURCES}


def prior_inputs():
    logs, hashes = m11.prior_inputs()
    for name, expected in M11_SHA.items():
        p = m11.OUT/name
        if sha(p.read_bytes()) != expected: raise ValueError("M11 frozen log/audit changed")
        hashes[str(p.relative_to(ROOT))] = expected
    log = json.loads((m11.OUT/"run_manifest.json").read_text())
    assert log["status"] == "completed" and not log["access_guard"]["blocked_attempts"]
    assert json.loads((m11.OUT/"audit/checks.json").read_text())["status"] == "PASS"
    for name, expected in log["output_sha256"].items():
        p = m11.OUT/name
        if sha(p.read_bytes()) != expected: raise ValueError("M11 output changed")
        hashes[str(p.relative_to(ROOT))] = expected
    return logs, hashes


def frozen_graph(f, old, metadata):
    pools = m8.mm.pool_episodes(metadata)
    epids = [ei for ei, key in enumerate(f.keys) if key in pools["normal_filtered"]]
    bank = np.flatnonzero(np.isin(f.episode, epids) & f.valid)
    scenarios = np.array([metadata[str(k)]["scenario"] for k in f.keys])
    matcher = mm.HistoryPairMatcher(f, bank, scenarios)
    pairs = np.full((4, len(old["queries"]), 2), -1, dtype=np.int64)
    reasons = np.zeros(pairs.shape[:2], dtype=np.int64)
    counts = np.zeros((*reasons.shape, 3), dtype=np.int64); fixed = np.zeros_like(reasons, dtype=bool)
    for ci, length in enumerate(mm.LENGTHS):
        for qi, q in enumerate(old["queries"]):
            ds, reason, candidates = matcher.select(int(q), length=length)
            pairs[ci, qi, :len(ds)] = ds; reasons[ci, qi] = reason; counts[ci, qi] = candidates
        fixed[ci] = mm.fixed_retained(f, old["queries"], old["pairs"][0], length)
        print(json.dumps({"stage": "score_blind_history_matching", "length": length,
                          "matched_looks": int((reasons[ci] == 0).sum()), "fixed_retained": int(fixed[ci].sum())}), flush=True)
    np.testing.assert_array_equal(pairs[0], old["pairs"][0]); np.testing.assert_array_equal(reasons[0], old["reasons"][0])
    assert (np.diff(counts, axis=0) <= 0).all() and (np.diff(fixed.astype(int), axis=0) <= 0).all()
    return {"queries": old["queries"], "phases": old["phases"], "phase_names": old["phase_names"],
            "cells": np.array([f"L{n}" for n in mm.LENGTHS]), "lengths": np.array(mm.LENGTHS),
            "pairs": pairs, "reasons": reasons, "candidate_counts": counts, "fixed_retained": fixed}


def summarize(f, metadata, graph, records, terms, normal_same, three_same):
    values, profiles = m11.mm.aggregate_layers(terms)
    lookup = mm.record_lookup(records, len(graph["queries"]))
    matched = graph["reasons"] == 0; common_all = matched.all(0)
    matrix = {}
    def subset(ci, mask):
        qi = np.flatnonzero(mask); idx = lookup[ci, qi]
        if (idx < 0).any(): raise ValueError("subset includes unmatched query")
        return m11.subset(f, metadata, graph["queries"][qi], graph["pairs"][ci, qi], values[idx], profiles[idx])
    for hi, phase in enumerate(graph["phase_names"]):
        ph = graph["phases"] == hi; matrix[str(phase)] = {}
        for ci, length in enumerate(mm.LENGTHS):
            mask = ph & matched[ci]; fixed = ph & graph["fixed_retained"][ci]
            co = mask & matched[0]; idx = lookup[ci, np.flatnonzero(mask)]
            qs = graph["queries"][mask]; ds = graph["pairs"][ci, mask]
            pos = mask.copy(); pos[np.flatnonzero(mask)] = m10.mm.edge_distances(f, qs, ds)[:, 3:].max(1) <= 16
            previous = matched[max(0, ci-1)]
            row = {"candidate_episodes": 126,
                   "episodes_with_query_looks": len(np.unique(f.episode[graph["queries"][ph]])),
                   "candidate_looks": int(ph.sum()),
                   "failure_looks": dict(Counter(mm.REASONS[int(r)] for r in graph["reasons"][ci, ph])),
                   "candidate_counts_episode_equal": m8.mm.plain_mean(f, graph["queries"][ph], graph["candidate_counts"][ci, ph]),
                   "candidate_columns": mm.CANDIDATE_COLUMNS,
                   "matched": subset(ci, mask), "endpoint_diameter_le16": subset(ci, pos),
                   "all_length_common": subset(ci, ph & common_all),
                   "L1_common_baseline": subset(0, co), "L1_common_history": subset(ci, co),
                   "paired_change": {"effect": m11.summary(f, metadata, graph["queries"][co],
                                              mm.paired_values(values, lookup, np.flatnonzero(co), ci)),
                                     "layers": m11.summary(f, metadata, graph["queries"][co],
                                              mm.paired_values(profiles, lookup, np.flatnonzero(co), ci))},
                   "fixed_graph_retained": subset(0, fixed),
                   "fixed_graph_rejected": subset(0, ph & matched[0] & ~fixed),
                   "new_vs_previous_queries": graph["queries"][mask & ~previous].tolist(),
                   "lost_vs_previous_queries": graph["queries"][ph & previous & ~matched[ci]].tolist(),
                   "new_vs_L1_queries": graph["queries"][mask & ~matched[0]].tolist(),
                   "changed_donor_pairs_on_L1_common": int((graph["pairs"][ci, co] != graph["pairs"][0, co]).any(1).sum()),
                   "normal_same_support_layers": m11.conditional_summary(f, metadata, qs, terms[idx], normal_same[idx], normal_only=True),
                   "three_same_support_layers": m11.conditional_summary(f, metadata, qs, terms[idx], three_same[idx], normal_only=False)}
            matrix[str(phase)][f"L{length}"] = row
        print(json.dumps({"stage": "history_summaries_completed", "phase": str(phase)}), flush=True)
    return matrix


def run(expected):
    start = time.monotonic(); guard = M12AccessGuard(ROOT, "analysis"); guard.install()
    head, sources = source_freeze(expected)
    if OUT.exists(): raise ValueError("refusing to overwrite M12 output")
    logs, hashes = prior_inputs(); read = m8.m7.read_npz
    a = read(m8.OUT/"look_inventory.npz"); old_graph = read(m10.OUT/"triplet_graph.npz")
    metadata = json.loads((m8.OUT/"episode_metadata.json").read_text())
    f = m10.features_of(a); graph = frozen_graph(f, old_graph, metadata)
    OUT.mkdir(parents=True); np.savez_compressed(OUT/"triplet_graph.npz", **graph)
    graph_sha = sha((OUT/"triplet_graph.npz").read_bytes())
    log = {"status": "graph_frozen_before_route_measurement", "implementation_commit": head,
           "source_sha256": sources, "input_sha256": hashes, "graph_sha256": graph_sha,
           "started_utc": datetime.now(timezone.utc).isoformat(),
           "evidence_role": "G-dev development local-history mechanism; no detector, causal or confirmatory claims"}
    write_json(OUT/"run_manifest.json", log)
    manifest = json.loads((m8.m7.OUT/"calibrate/threshold_manifest.json").read_text())
    used, current, cache_hashes, replay = m10.build_current(guard, a, graph, metadata, manifest, logs["score"]["input_sha256"])
    vectors, ids, rare, vector_hashes, vector_replay = m11.build_vectors(guard, a, metadata, manifest,
                                  logs["score"]["input_sha256"], {"used": used, "current": current})
    assert cache_hashes == vector_hashes
    old = read(m11.OUT/"routing_vectors.npz")
    overlap, here, there = np.intersect1d(used, old["used"], return_indices=True)
    np.testing.assert_array_equal(vectors[here], old["vectors"][there]); np.testing.assert_array_equal(ids[here], old["actual_ids"][there])
    np.testing.assert_array_equal(rare, old["rare"])
    replay.update(simplex_max_error=vector_replay["simplex_max_error"], current_vector_replay_error=vector_replay["M10_current_max_error"],
                  M11_overlap_looks=len(overlap), M11_vector_max_error=0.)
    records, edges, edge_indices = m11.mm.records_and_edges(graph)
    np.testing.assert_array_equal(used, np.unique(records[:, 3:6]))
    nodes = np.searchsorted(used, edges); tri = np.searchsorted(used, records[:, 3:6]); folds = a["structure"][used, 0]
    np.testing.assert_array_equal(folds[nodes[:, 0]], folds[nodes[:, 1]])
    distances = m11.mm.pair_metrics(vectors, nodes, rare[folds[nodes[:, 0]]])
    terms = m11.mm.triplet_measures(distances, edge_indices)
    for off in (0, 4, 8): np.testing.assert_allclose(terms[:, off], terms[:, off+1]+terms[:, off+2], rtol=0, atol=1e-14)
    support = vectors[:, 0] > 0
    normal_same = (support[tri[:, 1]] == support[tri[:, 2]]).all(-1)
    three_same = normal_same & (support[tri[:, 0]] == support[tri[:, 1]]).all(-1)
    np.savez_compressed(OUT/"routing_vectors.npz", used=used, vectors=vectors, actual_ids=ids, rare=rare, folds=folds, current=current)
    np.savez_compressed(OUT/"pair_metrics.npz", records=records, edges=edges, edge_indices=edge_indices,
                        distances=distances, terms=terms, normal_same=normal_same, three_same=three_same)
    matrix = summarize(f, metadata, graph, records, terms, normal_same, three_same)
    write_json(OUT/"result.json", {"matrix": matrix, "columns": m11.mm.COLUMNS, "layer_columns": m11.mm.LAYER_COLUMNS,
         "conditional_columns": m11.mm.CONDITIONAL_COLUMNS, "normal_conditional_columns": m11.mm.DISTANCES,
         "used_looks": len(used), "triplets": len(records), "unique_edges": len(edges), "replay": replay,
         "uncertainty": "family bootstrap conditional on bank/graph/mask; repeated development; no causal or detection interpretation"})
    log["input_sha256"].update(cache_hashes)
    for path, digest in log["input_sha256"].items():
        if sha((ROOT/path).read_bytes()) != digest: raise ValueError("M12 input changed during run")
    assert source_freeze(expected) == (head, sources) and sha((OUT/"triplet_graph.npz").read_bytes()) == graph_sha
    log.update(status="completed", finished_utc=datetime.now(timezone.utc).isoformat(), elapsed_seconds=time.monotonic()-start,
               peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2, access_guard=guard.summary(),
               output_sha256={p.name: sha(p.read_bytes()) for p in sorted(OUT.iterdir()) if p.is_file() and p.name != "run_manifest.json"})
    assert not log["access_guard"]["blocked_attempts"]
    write_json(OUT/"run_manifest.json", log)
    print(json.dumps({k: log[k] for k in ("status", "implementation_commit", "elapsed_seconds", "peak_rss_gib")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--freeze-commit", required=True)
    run(parser.parse_args().freeze_commit)
