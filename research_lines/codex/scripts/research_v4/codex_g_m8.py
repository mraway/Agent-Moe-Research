"""Source-frozen M8 mechanism analysis of existing OPEN G-dev M7 artifacts."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
import resource
import time

import numpy as np
from safetensors.torch import load as load_bytes

from research_v2 import trm3_g
from research_v4 import codex_g_m7 as m7
from research_v4 import codex_g_m8_math as mm
from research_v4.codex_g_m1 import ROOT, BASE, EXPECTED_ARMS, sha, write_json
from research_v4.codex_g_m7_statistics import TokenUnigram

OUT = BASE / "m8_lexical_matched_mechanisms_v1"
SOURCES = tuple(sorted(set(m7.SOURCES) | {
    "docs/research_v4/codex_g_m8_analysis_spec.md", "scripts/research_v4/codex_g_m8.py",
    "scripts/research_v4/codex_g_m8_math.py", "scripts/research_v4/codex_g_m8_audit.py",
    "tests/test_research_v4_codex_g_m8.py"}))
M7_SHA = {
    "calibrate/threshold_manifest.json": "3f46f5f107f8011a717370db020c357fb66e805758e8a00668d0aca47a1775f7",
    "calibrate/run_manifest.json": "16e6c8e6c5f3ccd9b9217877bf8c450072a10bbd667db2e6896ecca5928d4222",
    "score/run_manifest.json": "6dd737b5bb55b91e481467f32e92a410ed2de9caa38991b53eaa257db57318cd",
    "score/look_streams.npz": "223fadcac5c8ef164607634c4fbc06ac8a3085ce5493fdf3696b4e0eb1cc6efa",
    "audit/checks.json": "ea9f7067fc987d7283a2bd7a1d4f589f859383ccdf15e17745a9252312928b3d",
}
EXPECTED_POOLS = dict(zip(mm.POOLS, (293, 408, 53, 33, 12, 24), strict=True))


class M8AccessGuard(m7.m6.M6AccessGuard):
    def check_path(self, path):
        resolved = super().check_path(path)
        artifact_root = ROOT / "artifacts"
        allowed = (m7.OUT, m7.m6.M3 / "topk_cache/g_dev", OUT)
        if artifact_root in resolved.parents and not any(r == resolved or r in resolved.parents for r in allowed):
            self.blocked_attempts += 1
            raise PermissionError("M8 reads only frozen M7 and existing M3 G-dev top-k caches")
        return resolved

    def audit(self, event, args):
        super().audit(event, args)
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            path = self.check_path(args[0])
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            if (flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC)
                    and ROOT / "artifacts" in path.parents and OUT not in path.parents):
                self.blocked_attempts += 1
                raise PermissionError("M8 cannot write prior artifacts")


def source_freeze(expected):
    head = m7.harness.git_output("rev-parse", "HEAD")
    if head != expected or m7.harness.git_output("diff", "HEAD", "--name-only"):
        raise ValueError("commit sources and pass exact clean tracked HEAD before input access")
    if not set(SOURCES).issubset(set(m7.harness.git_output("ls-files").splitlines())):
        raise ValueError("M8 implementation, tests and independent audit must be committed")
    return head, {p: sha((ROOT / p).read_bytes()) for p in SOURCES}


def prior_inputs():
    hashes = {}
    for name, expected in M7_SHA.items():
        body = (m7.OUT / name).read_bytes()
        if sha(body) != expected: raise ValueError(f"M7 frozen input changed: {name}")
        hashes[str((m7.OUT / name).relative_to(ROOT))] = expected
    logs = {}
    for stage in ("calibrate", "score"):
        log = json.loads((m7.OUT / stage / "run_manifest.json").read_text())
        if log["status"] != "completed" or log["access_guard"]["blocked_attempts"]:
            raise ValueError("M7 did not finish cleanly")
        if log["threshold_manifest_sha256"] != M7_SHA["calibrate/threshold_manifest.json"]:
            raise ValueError("M7 threshold mismatch")
        for name, expected in log["output_sha256"].items():
            path = m7.OUT / stage / name
            if sha(path.read_bytes()) != expected: raise ValueError(f"M7 output changed: {path}")
            hashes[str(path.relative_to(ROOT))] = expected
        logs[stage] = log
    if json.loads((m7.OUT / "audit/checks.json").read_text())["status"] != "PASS":
        raise ValueError("M7 independent audit did not pass")
    return logs, hashes


def build_inventory(guard, manifest, metadata, arrays, expected_cache):
    keys = arrays["keys"]
    if set(keys) != set(metadata) or tuple(arrays["columns"]) != ("S", "CW", "TU"):
        raise ValueError("M7 keys/columns changed")
    total = len(arrays["ends"])
    episode = np.repeat(np.arange(len(keys)), np.diff(arrays["offsets"]))
    structure = np.zeros((total, 6), dtype=np.int64)
    valid = np.zeros(total, dtype=bool)
    tokens8 = np.zeros((total, 8), dtype=np.int64)
    z = np.zeros((total, 3), dtype=np.float64)
    states = manifest["cells"]
    unigram = {fold: TokenUnigram().load_state(state["statistics"]["TU"])
               for fold, state in states["TU"]["folds"].items()}
    standardisers = {(fold, col): trm3_g.standardiser_from_state(state["calibrations"][name]["standardiser"])
                    for col, name in enumerate(("S", "CW", "TU")) for fold, state in states[name]["folds"].items()}
    hashes, maximum_tu_error = {}, 0.
    for ei, key in enumerate(keys):
        row = metadata[str(key)]; fold = str(row["fold"])
        if not str(key).startswith("g_dev|"): raise ValueError("not G-dev")
        cache_name = str(key).split("|", 1)[1].replace("#ep", "--ep")+".safetensors"
        path = guard.check_path(m7.m6.M3 / "topk_cache/g_dev" / cache_name)
        body = path.read_bytes(); relative = str(path.relative_to(ROOT))
        if sha(body) != expected_cache[relative]: raise ValueError("M7-audited token cache changed")
        hashes[relative] = sha(body)
        token_ids = load_bytes(body)["token_ids"].numpy()
        if token_ids.shape != (row["token_count"],): raise ValueError("token count mismatch")
        sl = slice(int(arrays["offsets"][ei]), int(arrays["offsets"][ei+1]))
        ends = arrays["ends"][sl]; tags = arrays["tags"][sl]; ordinals = arrays["ordinals"][sl]
        step, good, windows = mm.window_geometry(ends, token_ids, row["step_output_lengths"])
        structure[sl] = np.column_stack([np.full(len(ends), row["fold"]), [mm.TAGS.index(t) for t in tags],
                                        np.full(len(ends), row["episode_index"]), step, ordinals//32, ends//64])
        valid[sl] = good; tokens8[sl] = windows
        tu = unigram[fold]
        rebuilt = np.array([np.mean([tu.tables[str(tag)].get(int(t), tu.unknown[str(tag)]) for t in window])
                            for tag, window in zip(tags, windows, strict=True)])
        error = float(np.max(np.abs(rebuilt-arrays["raw"][sl, 2]), initial=0.))
        maximum_tu_error = max(maximum_tu_error, error)
        np.testing.assert_allclose(rebuilt, arrays["raw"][sl, 2], rtol=0, atol=1e-10)
        for col in range(3):
            stream = trm3_g.EpisodeStream(key=str(key), ends=ends, scores=arrays["raw"][sl, col],
                                        tags=tags.tolist(), ordinals=ordinals)
            z[sl, col] = standardisers[(fold, col)].standardize(stream)
    values = np.column_stack([arrays["raw"], z])
    if not np.isfinite(values).all(): raise ValueError("nonfinite local scores")
    f = mm.MatchFeatures(keys, episode, arrays["ends"], structure, tokens8, arrays["raw"][:, 2], valid)
    inventory = dict(keys=keys, offsets=arrays["offsets"], episode=episode, ends=f.ends, structure=structure,
                     tokens=tokens8, tu=f.tu, valid=valid, ordinals=arrays["ordinals"], values=values,
                     columns=np.array(mm.COLUMNS))
    return f, inventory, hashes, maximum_tu_error


def frozen_matches(f, metadata, pools, phase_rows):
    queries = np.concatenate([phase_rows[p][f.valid[phase_rows[p]]] for p in mm.PHASES])
    phases = np.concatenate([np.full(int(f.valid[phase_rows[p]].sum()), i, dtype=np.int64) for i, p in enumerate(mm.PHASES)])
    shape = (len(mm.POOLS), len(mm.VERSIONS), len(queries))
    donors = np.full((*shape, 3), -1, dtype=np.int64)
    reasons = np.zeros(shape, dtype=np.int64)
    for pi, pool in enumerate(mm.POOLS):
        ep_ids = np.array([i for i, k in enumerate(f.keys) if k in pools[pool]], dtype=np.int64)
        bank = np.flatnonzero(np.isin(f.episode, ep_ids) & f.valid)
        matcher = mm.Matcher(f, bank)
        for vi, version in enumerate(mm.VERSIONS):
            for qi, query in enumerate(queries):
                chosen, reason = matcher.select(int(query), version)
                donors[pi, vi, qi, :len(chosen)] = chosen; reasons[pi, vi, qi] = reason
        print(json.dumps({"stage": "matching_complete", "pool": pool}), flush=True)
    return dict(queries=queries, phases=phases, donors=donors, reasons=reasons,
                pools=np.array(mm.POOLS), versions=np.array(mm.VERSIONS), phase_names=np.array(mm.PHASES))


def residuals(values, graph):
    effects = np.full((*graph["reasons"].shape, 6), np.nan)
    # Outcome access is separate, AFTER the full matching graph is fixed.
    for pi in range(len(mm.POOLS)):
        for vi in range(len(mm.VERSIONS)):
            for qi, (q, ds) in enumerate(zip(graph["queries"], graph["donors"][pi, vi], strict=True)):
                ds = ds[ds >= 0]
                if len(ds): effects[pi, vi, qi] = values[q]-values[ds].mean(0)
    return effects


def summarize(f, metadata, values, graph, candidates, phase_rows):
    matrix, comparisons = {}, {}
    for hi, phase in enumerate(mm.PHASES):
        mask = graph["phases"] == hi; qs = graph["queries"][mask]
        matrix[phase] = {}; comparisons[phase] = {}
        for pi, pool in enumerate(mm.POOLS):
            matrix[phase][pool] = {}; comparisons[phase][pool] = {}
            for vi, version in enumerate(mm.VERSIONS):
                matrix[phase][pool][version] = mm.cell_summary(
                    f, metadata, phase_rows[phase], qs, graph["donors"][pi, vi, mask],
                    graph["effects"][pi, vi, mask], graph["reasons"][pi, vi, mask], values, candidates)
            for left, right in (("L0", "L1"), ("L1", "L1_tight")):
                li, ri = mm.VERSIONS.index(left), mm.VERSIONS.index(right)
                dl, dr = graph["donors"][pi, li, mask], graph["donors"][pi, ri, mask]
                both = (dr >= 0).sum(1) >= 1
                if not np.all((dl[both] >= 0).any(1)): raise ValueError("nested support broken")
                # The same exact query windows, not merely the same episodes.
                el, er = graph["effects"][pi, li, mask][both], graph["effects"][pi, ri, mask][both]
                comparisons[phase][pool][left+"_to_"+right] = {
                    "query_looks": int(both.sum()), "same_queries": qs[both].tolist(),
                    left: mm.effect_summary(f, metadata, qs[both], el),
                    right: mm.effect_summary(f, metadata, qs[both], er),
                    "right_minus_left": mm.effect_summary(f, metadata, qs[both], er-el)}
        print(json.dumps({"stage": "summary_complete", "phase": phase}), flush=True)
    return {"matrix": matrix, "common_query_comparisons": comparisons}


def run(expected):
    start = time.monotonic()
    guard = M8AccessGuard(ROOT, "analysis"); guard.install()
    head, sources = source_freeze(expected)
    if OUT.exists(): raise ValueError("refusing to overwrite an existing M8 run")
    logs, hashes = prior_inputs()
    manifest = json.loads((m7.OUT / "calibrate/threshold_manifest.json").read_text())
    metadata = json.loads((m7.OUT / "score/episode_metadata.json").read_text())
    arrays = m7.read_npz(m7.OUT / "score/look_streams.npz", M7_SHA["score/look_streams.npz"])
    if dict(Counter(r["variant"] for r in metadata.values())) != EXPECTED_ARMS: raise ValueError("arm counts changed")
    pools = mm.pool_episodes(metadata)
    if {k: len(v) for k, v in pools.items()} != EXPECTED_POOLS: raise ValueError("donor pool scopes changed")
    f, inventory, cache_hashes, error = build_inventory(guard, manifest, metadata, arrays, logs["score"]["input_sha256"])
    positives, phase_rows = mm.phase_queries(f, metadata)
    if len(positives) != 126: raise ValueError("X positive denominator changed")
    OUT.mkdir(parents=True)
    log = {"status": "started", "implementation_commit": head, "source_sha256": sources,
           "input_sha256": {**hashes, **cache_hashes}, "started_utc": datetime.now(timezone.utc).isoformat(),
           "evidence_role": "G-dev development mechanism analysis only; no causal or confirmatory claim"}
    write_json(OUT / "run_manifest.json", log)
    np.savez_compressed(OUT / "look_inventory.npz", **inventory)
    write_json(OUT / "episode_metadata.json", metadata)
    graph = frozen_matches(f, metadata, pools, phase_rows)
    # Freeze a score-blind graph on disk before computing any routing residuals.
    np.savez_compressed(OUT / "match_graph.npz", **graph)
    graph_sha = sha((OUT / "match_graph.npz").read_bytes())
    graph["effects"] = residuals(inventory["values"], graph)
    np.savez_compressed(OUT / "residuals.npz", effects=graph["effects"])
    result = summarize(f, metadata, inventory["values"], graph, len(positives), phase_rows)
    result.update(columns=mm.COLUMNS, donor_pools={k: len(v) for k, v in pools.items()},
                  matching_spec="caf32c6; causal prefix only; no route/outcome based matching",
                  uncertainty="2000 query-family cluster bootstraps, fixed donor bank and matching graph; donor uncertainty NOT covered; descriptive only",
                  positives=len(positives), episodes=len(f.keys), looks=len(f.ends),
                  valid_looks=int(f.valid.sum()), cross_step_looks=int((~f.valid).sum()),
                  tu_reconstruction_max_absolute_error=error, matching_graph_sha256=graph_sha)
    write_json(OUT / "result.json", result)
    if prior_inputs()[1] != hashes: raise ValueError("M7 changed during analysis")
    for p, digest in cache_hashes.items():
        if sha((ROOT / p).read_bytes()) != digest: raise ValueError("token cache changed during analysis")
    if source_freeze(expected) != (head, sources): raise ValueError("sources changed during analysis")
    log.update(status="completed", finished_utc=datetime.now(timezone.utc).isoformat(),
               elapsed_seconds=time.monotonic()-start, peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2,
               access_guard=guard.summary(), unchanged_prior_inputs=True,
               output_sha256={p.name: sha(p.read_bytes()) for p in sorted(OUT.iterdir()) if p.is_file() and p.name != "run_manifest.json"})
    if log["access_guard"]["blocked_attempts"]: raise ValueError("blocked access attempted")
    write_json(OUT / "run_manifest.json", log)
    print(json.dumps({k: log[k] for k in ("status", "implementation_commit", "elapsed_seconds", "peak_rss_gib")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--freeze-commit", required=True)
    run(parser.parse_args().freeze_commit)
