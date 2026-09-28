"""M13: injected nonexecution fourth-node controls on frozen M12 normal pairs."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
import resource
import time
import numpy as np

from research_v4 import codex_g_mech_m12 as m12
from research_v4 import codex_g_mech_m13_math as mm
from research_v4.codex_g_m1 import ROOT, BASE, sha, write_json

m11, m10, m8 = m12.m11, m12.m10, m12.m8
OUT = BASE/"mechanism_m13_injected_controls_v1"
M12_SHA = {"run_manifest.json": "2c524ef95b359fb76f6e2d5842b17e55a6983bbbbfc4f2d2ff24a01b3d2aa65c",
           "audit/checks.json": "e011f6c6662d9c77be4fdb49cb6243ba39f5de93374d5b33c953bc0455db1d62"}
SOURCES = tuple(sorted(set(m12.SOURCES) | {
    "docs/research_v4/codex_g_mech_m13_analysis_spec.md", "scripts/research_v4/codex_g_mech_m13.py",
    "scripts/research_v4/codex_g_mech_m13_math.py", "scripts/research_v4/codex_g_mech_m13_audit.py",
    "tests/test_research_v4_codex_g_mech_m13.py"}))


class M13AccessGuard(m8.m7.m6.M6AccessGuard):
    def check_path(self, path):
        p = super().check_path(path)
        roots = (m8.m7.OUT, m8.OUT, m10.m9.OUT, m10.OUT, m11.OUT, m12.OUT, OUT,
                 m8.m7.m6.M3/"topk_cache/g_dev", m8.m7.m6.M3/"logit_cache/g_dev")
        if ROOT/"artifacts" in p.parents and not any(r == p or r in p.parents for r in roots):
            self.blocked_attempts += 1
            raise PermissionError("M13 only permits frozen M7-M12 and hashed private G-dev caches")
        return p

    def audit(self, event, args):
        super().audit(event, args)
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            p = self.check_path(args[0]); flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC) and ROOT/"artifacts" in p.parents and OUT not in p.parents:
                self.blocked_attempts += 1
                raise PermissionError("M13 cannot write previous or other-line artifacts")


def source_freeze(expected):
    git = m8.m7.harness.git_output; head = git("rev-parse", "HEAD")
    if head != expected or git("diff", "HEAD", "--name-only"):
        raise ValueError("M13 requires exact clean tracked source freeze before data access")
    if not set(SOURCES).issubset(set(git("ls-files").splitlines())):
        raise ValueError("M13 plan, code, tests and audit must be committed")
    return head, {p: sha((ROOT/p).read_bytes()) for p in SOURCES}


def prior_inputs():
    logs, hashes = m12.prior_inputs()
    for name, expected in M12_SHA.items():
        p = m12.OUT/name
        if sha(p.read_bytes()) != expected: raise ValueError("M12 frozen log/audit changed")
        hashes[str(p.relative_to(ROOT))] = expected
    log = json.loads((m12.OUT/"run_manifest.json").read_text())
    assert log["status"] == "completed" and not log["access_guard"]["blocked_attempts"]
    assert json.loads((m12.OUT/"audit/checks.json").read_text())["status"] == "PASS"
    for name, expected in log["output_sha256"].items():
        p = m12.OUT/name
        if sha(p.read_bytes()) != expected: raise ValueError("M12 output changed")
        hashes[str(p.relative_to(ROOT))] = expected
    return logs, hashes


def categories(f, metadata):
    return mm.ContextCategories(**{dest: np.array([metadata[str(k)][field] for k in f.keys]) for dest, field in
        (("scenarios", "scenario"), ("channels", "injection_channel"), ("families", "family"), ("tiers", "tier"))})


def frozen_graph(f, metadata, old):
    bank, stats = mm.control_banks(f, metadata); cat = categories(f, metadata)
    mask = old["phases"] == 0; queries = old["queries"][mask]
    normals = old["pairs"][[0, 3]][:, mask]
    shape = (2, 4, 3, len(queries)); controls = np.full(shape, -1, dtype=np.int64)
    reasons = np.ones(shape, dtype=np.int64); candidates = np.zeros((*shape, 3), dtype=np.int64)
    for gi, group in enumerate(mm.GROUPS):
        matcher = mm.ControlMatcher(f, bank[group], cat)
        for li, length in enumerate(mm.LENGTHS):
            for ci in range(3):
                for qi, q in enumerate(queries):
                    control, reason, counts = matcher.select(int(q), normals[li, qi], length=length, level=ci)
                    controls[li, gi, ci, qi] = control; reasons[li, gi, ci, qi] = reason; candidates[li, gi, ci, qi] = counts
        print(json.dumps({"stage": "score_blind_control_matching", "group": group,
                          "matches_L1_L8_by_level": (reasons[:, gi] == 0).sum(2).tolist()}), flush=True)
    assert (np.diff(candidates, axis=2) <= 0).all()
    assert (np.diff((reasons == 0).astype(int), axis=2) <= 0).all()
    return {"queries": queries, "normals": normals, "controls": controls, "reasons": reasons, "candidates": candidates,
            "lengths": np.array(mm.LENGTHS), "groups": np.array(mm.GROUPS), "levels": np.array(mm.LEVELS),
            **{f"bank_{g}": b for g, b in bank.items()}}, stats


def build_vectors(guard, a, metadata, used, expected_cache):
    vectors = np.empty((len(used), 3, 24, 32)); actual = np.empty((len(used), 24, 4), dtype=np.int64); hashes = {}
    for ei, key in enumerate(a["keys"]):
        loc = np.flatnonzero(a["episode"][used] == ei)
        if not len(loc): continue
        assert str(key).startswith("g_dev|"); content = []
        for path in m10.m9.cache_paths(key):
            body = guard.check_path(path).read_bytes(); rel = str(path.relative_to(ROOT))
            if sha(body) != expected_cache[rel]: raise ValueError("M7-audited cache changed")
            hashes[rel] = sha(body); content.append(m8.load_bytes(body))
        row = metadata[str(key)]; ids = content[0]["top_k_ids"].numpy(); tokens = content[0]["token_ids"].numpy()
        logits = content[1]["router_logits"].double().numpy(); ends = a["ends"][used[loc]]
        assert ids.shape == (24, row["token_count"], 4) and logits.shape == (24, row["token_count"], 32)
        _, valid, windows = m8.mm.window_geometry(ends, tokens, row["step_output_lengths"])
        assert valid.all(); np.testing.assert_array_equal(windows, a["tokens"][used[loc]])
        np.testing.assert_array_equal(a["structure"][used[loc], 0], np.full(len(loc), row["fold"]))
        actual[loc] = ids[:, ends].transpose(1, 0, 2)
        vectors[loc] = m11.mm.routing_vectors(actual[loc], logits[:, ends].transpose(1, 0, 2))
    assert np.isfinite(vectors).all() and (vectors >= 0).all()
    error = abs(vectors.sum(-1)-1).max(initial=0.); assert error < 1e-12
    prior = m8.m7.read_npz(m12.OUT/"routing_vectors.npz")
    overlap, here, there = np.intersect1d(used, prior["used"], return_indices=True)
    np.testing.assert_array_equal(vectors[here], prior["vectors"][there]); np.testing.assert_array_equal(actual[here], prior["actual_ids"][there])
    return vectors, actual, hashes, {"simplex_max_error": float(error), "M12_overlap_looks": len(overlap), "M12_vector_max_error": 0.}


def subset(f, metadata, nodes, values, profiles):
    qs, ds, cs = nodes[:, 0], nodes[:, 1:3], nodes[:, 3]
    row = m11.subset(f, metadata, qs, ds, values, profiles)
    row["control_distribution"] = m8.mm.distribution(f, metadata, cs)
    if not len(qs): return row
    edges = mm.balance(f, nodes); counts = Counter(f.episode[qs].tolist())
    reuse = Counter(f.episode[cs].tolist()); weights, sw = defaultdict(float), defaultdict(float)
    for q, c in zip(qs, cs, strict=True):
        key = str(f.keys[f.episode[c]]); w = 1/(len(counts)*counts[int(f.episode[q])])
        weights[key] += w; sw[metadata[key]["scenario"]] += w
    row.update(four_balance={"columns": [f"{var}_{i}_{j}" for var in ("TU", "end") for i, j in mm.EDGE_ROLES],
                             "episode_equal_mean": m8.mm.plain_mean(f, qs, edges), "edge_max": edges.max(0).tolist()},
               control_reuse={str(f.keys[e]): n for e, n in sorted(reuse.items())},
               control_effective_weight=dict(sorted(weights.items())), control_scenario_effective_weight=dict(sorted(sw.items())),
               max_control_effective_weight=max(weights.values()), max_control_scenario_effective_weight=max(sw.values()),
               control_stop_reasons=dict(Counter(str(metadata[str(f.keys[e])]["stop_reason"]) for e in reuse)),
               control_E_offsets=[{"query_row": int(q), "control_row": int(c),
                                    "offset": None if metadata[str(f.keys[f.episode[c]])]["e"] is None else
                                    int(f.ends[c]-metadata[str(f.keys[f.episode[c]])]["e"])} for q, c in zip(qs, cs, strict=True)])
    return row


def conditional(f, metadata, qs, terms, mask):
    keep, values, counts = mm.masked_values(terms, mask)
    return {"effect": m11.summary(f, metadata, qs[keep], values), "candidate_looks": len(qs),
            "eligible_layer_quads": int(counts.sum()), "layer_counts": mask.sum(0).tolist(),
            "look_layer_counts": [{"query_row": int(q), "count": int(n)} for q, n in zip(qs, counts, strict=True)],
            "query_distribution": m8.mm.distribution(f, metadata, qs[keep])}


def summarize(f, metadata, graph, records, terms, same):
    values, profiles = mm.aggregate(terms)
    lookup = np.full(graph["reasons"].shape, -1, dtype=np.int64)
    for ri, r in enumerate(records): lookup[tuple(r[:4])] = ri
    good = graph["reasons"] == 0; matrix = {}
    def indices(li, gi, ci, mask):
        idx = lookup[li, gi, ci, np.flatnonzero(mask)]
        if (idx < 0).any(): raise ValueError("unmatched query in summary")
        return idx
    def sub(li, gi, ci, mask):
        idx = indices(li, gi, ci, mask)
        return subset(f, metadata, records[idx, 4:], values[idx], profiles[idx])
    def paired(left, right, mask):
        ia, ib = indices(*left, mask), indices(*right, mask); qs = graph["queries"][mask]
        return {"effect": m11.summary(f, metadata, qs, values[ib]-values[ia]),
                "layers": m11.summary(f, metadata, qs, profiles[ib]-profiles[ia])}
    for li, length in enumerate(mm.LENGTHS):
        matrix[f"L{length}"] = {}
        base = (graph["normals"][li] >= 0).all(1)
        for gi, group in enumerate(mm.GROUPS):
            matrix[f"L{length}"][group] = {}
            common = good[li, gi].all(0)
            for ci, level in enumerate(mm.LEVELS):
                mask = good[li, gi, ci]; idx = indices(li, gi, ci, mask); qs = graph["queries"][mask]
                pos = mask.copy(); pos[np.flatnonzero(mask)] = mm.balance(f, records[idx, 4:])[:, 6:].max(1) <= 16
                base_common = mask & good[li, gi, 0]; length_common = mask & good[1-li, gi, ci]
                row = {"candidate_episodes": 126, "candidate_looks": len(graph["queries"]),
                       "base_episodes": len(np.unique(f.episode[graph["queries"][base]])), "base_looks": int(base.sum()),
                       "failure_looks": dict(Counter(mm.REASONS[int(x)] for x in graph["reasons"][li, gi, ci])),
                       "candidates_episode_equal": m8.mm.plain_mean(f, graph["queries"][base], graph["candidates"][li, gi, ci, base]),
                       "matched": sub(li, gi, ci, mask), "endpoint_diameter_le16": sub(li, gi, ci, pos),
                       "three_level_common": sub(li, gi, ci, common),
                       "channel_common_baseline": sub(li, gi, 0, base_common), "channel_common_current": sub(li, gi, ci, base_common),
                       "paired_level_change": paired((li, gi, 0), (li, gi, ci), base_common),
                       "length_common_L1": sub(0, gi, ci, length_common), "length_common_L8": sub(1, gi, ci, length_common),
                       "paired_length_change": paired((0, gi, ci), (1, gi, ci), length_common),
                       "same_four_support_layers": conditional(f, metadata, qs, terms[idx], same[idx]),
                       "changed_controls_on_channel_common": int((graph["controls"][li, gi, ci, base_common] != graph["controls"][li, gi, 0, base_common]).sum())}
                matrix[f"L{length}"][group][level] = row
            print(json.dumps({"stage": "control_summaries_completed", "length": length, "group": group}), flush=True)
    return matrix


def run(expected):
    start = time.monotonic(); guard = M13AccessGuard(ROOT, "analysis"); guard.install()
    head, sources = source_freeze(expected)
    if OUT.exists(): raise ValueError("refusing to overwrite M13 outputs")
    logs, hashes = prior_inputs(); read = m8.m7.read_npz
    a = read(m8.OUT/"look_inventory.npz"); old = read(m12.OUT/"triplet_graph.npz")
    metadata = json.loads((m8.OUT/"episode_metadata.json").read_text()); f = m10.features_of(a)
    graph, bank_counts = frozen_graph(f, metadata, old)
    assert {g: bank_counts[g]["episodes"] for g in mm.GROUPS} == dict(zip(mm.GROUPS, (40, 33, 12, 53), strict=True))
    assert sum(r["variant"] == "attack" and not r["attack_bearing"] for r in metadata.values()) == 88
    OUT.mkdir(parents=True); np.savez_compressed(OUT/"control_graph.npz", **graph)
    write_json(OUT/"bank_counts.json", bank_counts); graph_sha = sha((OUT/"control_graph.npz").read_bytes())
    log = {"status": "graph_frozen_before_route_measurement", "implementation_commit": head,
           "source_sha256": sources, "input_sha256": hashes, "graph_sha256": graph_sha,
           "started_utc": datetime.now(timezone.utc).isoformat(), "evidence_role": "G-dev development injected nonexecution mechanism controls; not causal, confirmatory or a detector"}
    write_json(OUT/"run_manifest.json", log)
    records, edges, edge_indices = mm.records_and_edges(graph); used = np.unique(records[:, 4:])
    vectors, ids, cache_hashes, replay = build_vectors(guard, a, metadata, used, logs["score"]["input_sha256"])
    nodes = np.searchsorted(used, edges); quad = np.searchsorted(used, records[:, 4:]); folds = a["structure"][used, 0]
    np.testing.assert_array_equal(folds[nodes[:, 0]], folds[nodes[:, 1]])
    distances = mm.distances(vectors, nodes); terms = mm.measures(distances, edge_indices)
    np.testing.assert_allclose(terms[:, :, :, 5], terms[:, :, :, 3]-terms[:, :, :, 4], rtol=0, atol=1e-14)
    support = vectors[:, 0] > 0; same = (support[quad] == support[quad[:, :1]]).all((1, 3))
    np.savez_compressed(OUT/"routing_vectors.npz", used=used, vectors=vectors, actual_ids=ids, folds=folds)
    np.savez_compressed(OUT/"quad_metrics.npz", records=records, edges=edges, edge_indices=edge_indices, distances=distances, terms=terms, same=same)
    matrix = summarize(f, metadata, graph, records, terms, same)
    write_json(OUT/"result.json", {"matrix": matrix, "bank_counts": bank_counts, "columns": mm.COLUMNS,
               "layer_columns": mm.LAYER_COLUMNS, "conditional_columns": mm.CONDITIONAL_COLUMNS,
               "used_looks": len(used), "quadruplets": len(records), "unique_edges": len(edges), "replay": replay,
               "excluded_pre_injection_episodes": 88,
               "uncertainty": "query-family bootstrap conditional on selected normal/control banks and graph; retrospective outcomes; no causal or detector interpretation"})
    log["input_sha256"].update(cache_hashes)
    for path, digest in log["input_sha256"].items():
        if sha((ROOT/path).read_bytes()) != digest: raise ValueError("M13 input changed during run")
    assert source_freeze(expected) == (head, sources) and sha((OUT/"control_graph.npz").read_bytes()) == graph_sha
    log.update(status="completed", finished_utc=datetime.now(timezone.utc).isoformat(), elapsed_seconds=time.monotonic()-start,
               peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2, access_guard=guard.summary(),
               output_sha256={p.name: sha(p.read_bytes()) for p in sorted(OUT.iterdir()) if p.is_file() and p.name != "run_manifest.json"})
    assert not log["access_guard"]["blocked_attempts"]
    write_json(OUT/"run_manifest.json", log)
    print(json.dumps({k: log[k] for k in ("status", "implementation_commit", "elapsed_seconds", "peak_rss_gib")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--freeze-commit", required=True)
    run(parser.parse_args().freeze_commit)
