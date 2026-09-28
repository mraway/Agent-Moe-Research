"""M11: frozen M10 triplets, full routing vectors and normal-background controls."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import resource
import time
import numpy as np

from research_v4 import codex_g_mech_m10 as m10
from research_v4 import codex_g_mech_m11_math as mm
from research_v4.codex_g_m1 import ROOT, BASE, sha, write_json

OUT = BASE/"mechanism_m11_full_routing_v1"
M10_SHA = {"run_manifest.json": "76fbafd3e2c6196859e0821b6b2d750d020ac5a7e66dfb87236e70ffacf888b8",
           "audit/checks.json": "d0dc0a737e3a5404aef5b75986edc960bf67d7ab1746c9ba79d71b59b3b34752"}
SOURCES = tuple(sorted(set(m10.SOURCES) | {
    "docs/research_v4/codex_g_mech_m11_analysis_spec.md", "scripts/research_v4/codex_g_mech_m11.py",
    "scripts/research_v4/codex_g_mech_m11_math.py", "scripts/research_v4/codex_g_mech_m11_audit.py",
    "tests/test_research_v4_codex_g_mech_m11.py"}))


class M11AccessGuard(m10.m9.m8.m7.m6.M6AccessGuard):
    def check_path(self, path):
        p = super().check_path(path)
        roots = (m10.m9.m8.m7.OUT, m10.m9.m8.OUT, m10.m9.OUT, m10.OUT, OUT,
                 m10.m9.m8.m7.m6.M3/"topk_cache/g_dev", m10.m9.m8.m7.m6.M3/"logit_cache/g_dev")
        if ROOT/"artifacts" in p.parents and not any(r == p or r in p.parents for r in roots):
            self.blocked_attempts += 1
            raise PermissionError("M11 permits frozen M7-M10 and SHA-audited private G-dev caches only")
        return p

    def audit(self, event, args):
        super().audit(event, args)
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            p = self.check_path(args[0]); flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC) and ROOT/"artifacts" in p.parents and OUT not in p.parents:
                self.blocked_attempts += 1
                raise PermissionError("M11 cannot write earlier artifacts")


def source_freeze(expected):
    git = m10.m9.m8.m7.harness.git_output
    head = git("rev-parse", "HEAD")
    if head != expected or git("diff", "HEAD", "--name-only"):
        raise ValueError("M11 requires exact clean tracked source freeze before input access")
    if not set(SOURCES).issubset(set(git("ls-files").splitlines())):
        raise ValueError("M11 sources, plan, tests and audit must be committed")
    return head, {p: sha((ROOT/p).read_bytes()) for p in SOURCES}


def prior_inputs():
    logs, hashes = m10.prior_inputs()
    for name, expected in M10_SHA.items():
        p = m10.OUT/name
        if sha(p.read_bytes()) != expected: raise ValueError("M10 frozen log/audit changed")
        hashes[str(p.relative_to(ROOT))] = expected
    log = json.loads((m10.OUT/"run_manifest.json").read_text())
    assert log["status"] == "completed" and not log["access_guard"]["blocked_attempts"]
    assert json.loads((m10.OUT/"audit/checks.json").read_text())["status"] == "PASS"
    for name, expected in log["output_sha256"].items():
        p = m10.OUT/name
        if sha(p.read_bytes()) != expected: raise ValueError("M10 output changed")
        hashes[str(p.relative_to(ROOT))] = expected
    return logs, hashes


def build_vectors(guard, a, metadata, manifest, expected_cache, old_current):
    used = old_current["used"]; vectors = np.empty((len(used), 3, 24, 32))
    actual_ids = np.empty((len(used), 24, 4), dtype=np.int64); hashes = {}
    q = np.stack([np.array(manifest["cells"]["S"]["folds"][str(k)]["statistics"]["S"]["q"], dtype=float) for k in range(3)])
    for k in range(3):
        assert manifest["cells"]["S"]["folds"][str(k)]["statistics"]["S"]["config"]["rare_threshold"] == .02
    max_replay = 0.
    for ei, key in enumerate(a["keys"]):
        loc = np.flatnonzero(a["episode"][used] == ei)
        if not len(loc): continue
        assert str(key).startswith("g_dev|")
        row = metadata[str(key)]; contents = []
        for path in m10.m9.cache_paths(key):
            body = guard.check_path(path).read_bytes(); rel = str(path.relative_to(ROOT))
            if sha(body) != expected_cache[rel]: raise ValueError("M7-audited cache changed")
            hashes[rel] = sha(body); contents.append(m10.m9.m8.load_bytes(body))
        tokens = contents[0]["token_ids"].numpy(); ids = contents[0]["top_k_ids"].numpy()
        logits = contents[1]["router_logits"].double().numpy(); ends = a["ends"][used[loc]]
        assert ids.shape == (24, row["token_count"], 4) and logits.shape == (24, row["token_count"], 32)
        np.testing.assert_array_equal(tokens[ends], a["tokens"][used[loc], -1])
        np.testing.assert_array_equal(a["structure"][used[loc], 0], np.full(len(loc), row["fold"]))
        point_ids = ids[:, ends].transpose(1, 0, 2); point_logits = logits[:, ends].transpose(1, 0, 2)
        actual_ids[loc] = point_ids; vectors[loc] = mm.routing_vectors(point_ids, point_logits)
        rarity = np.where(q[row["fold"]] < .02, -np.log(q[row["fold"]]), 0.)
        rebuilt = m10.m9.dm.token_layer_terms(ids[:, ends], logits[:, ends], rarity)
        np.testing.assert_allclose(rebuilt, old_current["current"][loc], rtol=0, atol=1e-11)
        max_replay = max(max_replay, float(np.abs(rebuilt-old_current["current"][loc]).max()))
    error = np.max(abs(vectors.sum(-1)-1)) if len(used) else 0.
    assert error <= 1e-12 and (vectors >= 0).all() and np.isfinite(vectors).all()
    return vectors, actual_ids, q < .02, hashes, {"simplex_max_error": float(error), "M10_current_max_error": max_replay}


def positive_tolerance(reading):
    if reading["mean"] is not None:
        means = np.array([row["value"] for row in reading["episode_values"]])
        reading["positive_fraction"] = (means > mm.ZERO_TOL).mean(0).tolist()
    reading["positive_tolerance"] = mm.ZERO_TOL
    return reading


def summary(f, metadata, qs, values):
    return positive_tolerance(m10.m9.m8.mm.effect_summary(f, metadata, qs, values))


def subset(f, metadata, qs, ds, values, profiles):
    row = m10.subset(f, metadata, qs, ds, values, profiles)
    positive_tolerance(row["effect"]); positive_tolerance(row["layers"])
    return row


def conditional_summary(f, metadata, qs, terms, mask, *, normal_only):
    keep, values, counts = mm.masked_layers(terms, mask, normal_only=normal_only)
    return {"effect": summary(f, metadata, qs[keep], values), "candidate_looks": len(qs),
            "eligible_layer_pairs": int(counts.sum()), "layer_counts": mask.sum(0).tolist(),
            "look_layer_counts": [{"query_row": int(q), "count": int(n)} for q, n in zip(qs, counts, strict=True)],
            "query_distribution": m10.m9.m8.mm.distribution(f, metadata, qs[keep])}


def summarize(a, graph, records, terms, normal_same, three_same, normal_zero, prior):
    f, metadata = a["features"], a["metadata"]
    values, profiles = mm.aggregate_layers(terms)
    common = (graph["reasons"] == 0).all(0)
    matrix, diagnostics = {}, {}
    for hi, phase in enumerate(graph["phase_names"]):
        matrix[str(phase)] = {}
        for ci, name in enumerate(graph["cells"]):
            idx = np.flatnonzero((records[:, 0] == hi) & (records[:, 1] == ci))
            qs, ds = records[idx, 3], records[idx, 4:6]
            co = common[records[idx, 2]]
            old = prior["matrix"][str(phase)][str(name)]
            row = {"candidate_episodes": old["candidate_episodes"], "candidate_looks": old["candidate_looks"],
                   "failure_looks": old["failure_looks"],
                   "matched": subset(f, metadata, qs, ds, values[idx], profiles[idx]),
                   "four_cell_common": subset(f, metadata, qs[co], ds[co], values[idx][co], profiles[idx][co])}
            if ci == 0:
                pos = m10.mm.edge_distances(f, qs, ds)[:, 3:].max(1) <= 16
                row["endpoint_diameter_le16"] = subset(f, metadata, qs[pos], ds[pos], values[idx][pos], profiles[idx][pos])
                floor = normal_zero[idx]; floor_values = values[idx][floor]
                zeros = {"effect": summary(f, metadata, qs[floor], floor_values), "candidate_looks": len(qs),
                         "query_distribution": m10.m9.m8.mm.distribution(f, metadata, qs[floor]),
                         "normal_nonzero_look_counts": {r: int((floor_values[:, mm.COLUMNS.index(r+"/TV_all/all/N")] > mm.ZERO_TOL).sum()) for r in mm.REPS}}
                diagnostics[str(phase)] = {"normal_both_S_zero": zeros,
                    "normal_same_support_layers": conditional_summary(f, metadata, qs, terms[idx], normal_same[idx], normal_only=True),
                    "three_same_support_layers": conditional_summary(f, metadata, qs, terms[idx], three_same[idx], normal_only=False)}
            matrix[str(phase)][str(name)] = row
        print(json.dumps({"stage": "summaries_completed", "phase": str(phase)}), flush=True)
    return matrix, diagnostics


def run(expected):
    start = time.monotonic(); guard = M11AccessGuard(ROOT, "analysis"); guard.install()
    head, sources = source_freeze(expected)
    if OUT.exists(): raise ValueError("refusing to overwrite M11 artifacts")
    logs, hashes = prior_inputs(); read = m10.m9.m8.m7.read_npz
    a = read(m10.m9.m8.OUT/"look_inventory.npz"); graph = read(m10.OUT/"triplet_graph.npz")
    old_current = read(m10.OUT/"current_terms.npz")
    metadata = json.loads((m10.m9.m8.OUT/"episode_metadata.json").read_text())
    manifest = json.loads((m10.m9.m8.m7.OUT/"calibrate/threshold_manifest.json").read_text())
    prior = json.loads((m10.OUT/"result.json").read_text())
    records, edges, indices = mm.records_and_edges(graph)
    used = old_current["used"]
    np.testing.assert_array_equal(used, np.unique(records[:, 3:6]))
    OUT.mkdir(parents=True)
    log = {"status": "started", "implementation_commit": head, "source_sha256": sources,
           "input_sha256": hashes, "M10_graph_sha256": sha((m10.OUT/"triplet_graph.npz").read_bytes()),
           "started_utc": datetime.now(timezone.utc).isoformat(), "evidence_role": "G-dev development mechanism; full routing on unchanged M10 graph; no detector"}
    write_json(OUT/"run_manifest.json", log)
    vectors, actual_ids, rare, cache_hashes, replay = build_vectors(guard, a, metadata, manifest, logs["score"]["input_sha256"], old_current)
    edge_nodes = np.searchsorted(used, edges); node_indices = np.searchsorted(used, records[:, 3:6])
    folds = a["structure"][used, 0]; assert np.array_equal(folds[edge_nodes[:, 0]], folds[edge_nodes[:, 1]])
    distances = mm.pair_metrics(vectors, edge_nodes, rare[folds[edge_nodes[:, 0]]])
    terms = mm.triplet_measures(distances, indices)
    for offset in (0, 4, 8): np.testing.assert_allclose(terms[:, offset], terms[:, offset+1]+terms[:, offset+2], rtol=0, atol=1e-14)
    support = vectors[:, 0] > 0
    normal_same = (support[node_indices[:, 1]] == support[node_indices[:, 2]]).all(-1)
    three_same = normal_same & (support[node_indices[:, 0]] == support[node_indices[:, 1]]).all(-1)
    normal_zero = (old_current["current"][node_indices[:, 1:], :, 0].sum((1, 2)) == 0.)
    np.savez_compressed(OUT/"routing_vectors.npz", used=used, vectors=vectors, actual_ids=actual_ids, rare=rare, folds=folds)
    np.savez_compressed(OUT/"pair_metrics.npz", records=records, edges=edges, edge_indices=indices, distances=distances,
                        terms=terms, normal_same=normal_same, three_same=three_same, normal_zero=normal_zero)
    matrix, diagnostics = summarize({"features": m10.features_of(a), "metadata": metadata}, graph, records, terms, normal_same, three_same, normal_zero, prior)
    write_json(OUT/"result.json", {"matrix": matrix, "diagnostics": diagnostics, "columns": mm.COLUMNS,
          "layer_columns": mm.LAYER_COLUMNS, "conditional_columns": mm.CONDITIONAL_COLUMNS, "normal_conditional_columns": mm.DISTANCES,
          "used_looks": len(used), "triplets": len(records), "unique_edges": len(edges), "replay": replay,
          "uncertainty": "family bootstrap conditional on bank/graph/mask; repeated development; no causal or detector inference"})
    log["input_sha256"].update(cache_hashes)
    for path, digest in log["input_sha256"].items():
        if sha((ROOT/path).read_bytes()) != digest: raise ValueError("input changed during M11")
    if source_freeze(expected) != (head, sources): raise ValueError("sources changed during M11")
    log.update(status="completed", finished_utc=datetime.now(timezone.utc).isoformat(), elapsed_seconds=time.monotonic()-start,
               peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2, access_guard=guard.summary(),
               output_sha256={p.name: sha(p.read_bytes()) for p in sorted(OUT.iterdir()) if p.is_file() and p.name != "run_manifest.json"})
    assert not log["access_guard"]["blocked_attempts"]
    write_json(OUT/"run_manifest.json", log)
    print(json.dumps({k: log[k] for k in ("status", "implementation_commit", "elapsed_seconds", "peak_rss_gib")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--freeze-commit", required=True)
    run(parser.parse_args().freeze_commit)
