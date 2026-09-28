"""Source-frozen M14: temporal organization and labelled return on OPEN G-dev."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
import resource
import time
import numpy as np

from research_v2 import io_g
from research_v4 import codex_g_m8 as m8, codex_g_mech_m14_math as mm
from research_v4.codex_access_guard import CodexGAccessGuard
from research_v4.codex_g_m1 import ROOT, BASE, LABELS, EXPECTED_ARMS, sha, write_json

OUT = BASE/"mechanism_m14_temporal_v1"
M8_SHA = {"run_manifest.json": "467e23777b3fdf88f5c941d8f475c0c765f47e906ccc1ba9d2decb90c6d2d59a",
          "audit/checks.json": "62576c1199bae31ae865cf2ffe328970c00f7365caa9ff95fe8f0c2c873a37da"}
SOURCES = tuple(sorted(set(m8.SOURCES) | {
    "docs/research_v4/codex_g_mechanism_workstream.md", "docs/research_v4/attack_annotation_guideline.md",
    "docs/research_v4/codex_g_mech_m14_analysis_spec.md", "scripts/research_v4/codex_g_mech_m14.py",
    "scripts/research_v4/codex_g_mech_m14_math.py", "scripts/research_v4/codex_g_mech_m14_audit.py",
    "tests/test_research_v4_codex_g_mech_m14.py"}))
GROUPS = ("normal_all", "normal_filtered", "legitimate_refusal", "execution", "support_resumed_after_execution",
          "engaged_only", "committed_no_execution", "over_refusal", "silent")


class M14AccessGuard(CodexGAccessGuard):
    def check_path(self, path):
        p = super().check_path(path)
        allowed = (m8.m7.OUT, m8.OUT, OUT)
        if ROOT/"artifacts" in p.parents and p != LABELS and not any(r == p or r in p.parents for r in allowed):
            self.blocked_attempts += 1
            raise PermissionError("M14 permits only frozen M7/M8, the G-dev label file, and its own output")
        return p

    def audit(self, event, args):
        super().audit(event, args)
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            p = self.check_path(args[0]); flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC) and ROOT/"artifacts" in p.parents and OUT not in p.parents:
                self.blocked_attempts += 1
                raise PermissionError("M14 cannot change prior or other-line artifacts")


def source_freeze(expected):
    git = m8.m7.harness.git_output; head = git("rev-parse", "HEAD")
    if head != expected or git("diff", "HEAD", "--name-only"): raise ValueError("M14 requires exact clean tracked HEAD")
    if not set(SOURCES).issubset(set(git("ls-files").splitlines())): raise ValueError("commit M14 plan, implementation, tests and audit")
    return head, {p: sha((ROOT/p).read_bytes()) for p in SOURCES}


def prior_inputs():
    logs, hashes = m8.prior_inputs()
    for name, expected in M8_SHA.items():
        p = m8.OUT/name
        if sha(p.read_bytes()) != expected: raise ValueError("M8 frozen manifest/audit changed")
        hashes[str(p.relative_to(ROOT))] = expected
    log = json.loads((m8.OUT/"run_manifest.json").read_text())
    assert log["status"] == "completed" and not log["access_guard"]["blocked_attempts"]
    assert json.loads((m8.OUT/"audit/checks.json").read_text())["status"] == "PASS"
    for name, expected in log["output_sha256"].items():
        p = m8.OUT/name
        if sha(p.read_bytes()) != expected: raise ValueError("M8 output changed")
        hashes[str(p.relative_to(ROOT))] = expected
    label_sha = sha(LABELS.read_bytes()); rel = str(LABELS.relative_to(ROOT))
    if label_sha != logs["score"]["input_sha256"][rel]: raise ValueError("M7 G-dev labels changed")
    hashes[rel] = label_sha
    return hashes


def read_inputs():
    a = m8.m7.read_npz(m8.OUT/"look_inventory.npz")
    metadata = json.loads((m8.OUT/"episode_metadata.json").read_text())
    labels = {f"g_dev|{trace}#ep{ep}": row for (trace, ep), row in io_g.read_labels(LABELS).items()}
    assert set(a["keys"]) == set(metadata) == set(labels) and len(metadata) == 784
    assert len(a["ends"]) == 190284 and a["valid"].all()
    assert dict(Counter(r["variant"] for r in metadata.values())) == EXPECTED_ARMS
    assert mm.normal_mask(a["keys"], metadata).sum() == 293
    assert sum(r["variant"] == "attack" and r["attack_bearing"] for r in metadata.values()) == 264
    assert sum(r["variant"] == "attack" and not r["attack_bearing"] for r in metadata.values()) == 88
    return a, metadata, labels


def summarize(a, metadata, episodes, values):
    return mm.summary(episodes, values, a["keys"], metadata, m8.mm.clustered_ci)


def reuse(a, episodes, donor_eps):
    if not len(episodes): return dict(unique_donor_episodes=0, maximum_weight=None)
    counts = Counter(map(int, episodes)); weights = defaultdict(float)
    for ep, donors in zip(episodes, donor_eps, strict=True):
        for d in donors: weights[int(d)] += 1/(len(counts)*counts[int(ep)]*len(donors))
    return dict(unique_donor_episodes=len(weights), maximum_weight=max(weights.values(), default=None),
                weights={str(a["keys"][i]): w for i, w in sorted(weights.items())})


def summarize_segments(a, metadata, ranks, grid, graph, reasons, values):
    groups = np.array([mm.group_of(metadata[str(a["keys"][e])]) for e in grid[:, 0]])
    normal = mm.normal_mask(a["keys"], metadata); result = {}
    for group in GROUPS:
        scope = normal[grid[:, 0]] if group == "normal_filtered" else groups == group
        query = np.flatnonzero(scope & (grid[:, 3] >= 2) & np.isfinite(values).all((1, 2, 3)))
        cell = dict(segments=int(scope.sum()), one_block_segments=int((scope & (grid[:, 3] == 1)).sum()),
                    eligible_segments=len(query), eligible_episodes=len(set(grid[query, 0].tolist())), thresholds={})
        for ti, threshold in enumerate(mm.THRESHOLDS):
            v = values[:, ti].reshape((len(grid), len(mm.COLUMNS)))
            sub = {"unmatched": summarize(a, metadata, grid[query, 0], v[query]), "matched": {}}
            for li, level in enumerate(mm.LEVELS):
                qs = query[(graph[li, query] >= 0).any(1)]
                donor_ids = [ds[ds >= 0] for ds in graph[li, qs]]
                normals = np.array([v[ds].mean(0) for ds in donor_ids]).reshape((-1, len(mm.COLUMNS)))
                paired = np.column_stack([v[qs], normals, v[qs]-normals])
                seed_diff = np.array([ranks[grid[q, 2]]-ranks[grid[ds, 2]].mean(0)
                                      for q, ds in zip(qs, donor_ids, strict=True)]).reshape((-1, 3))
                item = {"summary": summarize(a, metadata, grid[qs, 0], paired),
                        "seed_rank_balance": summarize(a, metadata, grid[qs, 0], seed_diff),
                        "reasons": dict(Counter(reasons[li, query])),
                        "donor_reuse": reuse(a, grid[qs, 0], [grid[ds, 0] for ds in donor_ids]), "strata": {}}
                if li == 0:
                    masks = {f"channel/{name}": grid[qs, 5] == i for i, name in enumerate(("analysis", "commentary", "final"))}
                    for low, high in ((2, 3), (4, 7), (8, 15), (16, 31), (32, 100000)):
                        masks[f"blocks/{low}-{high}"] = (grid[qs, 3] >= low) & (grid[qs, 3] <= high)
                    item["strata"] = {k: summarize(a, metadata, grid[qs[mask], 0], paired[mask]) for k, mask in masks.items()}
                sub["matched"][level] = item
            cell["thresholds"][str(threshold)] = sub
        result[group] = cell
    return result


def summarize_recovery(a, metadata, ranks, events, donors, posts, reasons, values, common):
    eps = np.array([e["episode"] for e in events], int); result = {}
    groups = {"all": np.ones(len(events), bool)}
    for rel in ("before_X", "after_X", "no_X", "overlaps_X"):
        groups[rel] = np.array([e["relation"] == rel for e in events])
    for group in ("execution", "support_resumed_after_execution", "engaged_only", "committed_no_execution"):
        groups["class/"+group] = np.array([e["group"] == group for e in events])
    groups["re_execution"] = np.array([e["re_execution"] for e in events])
    groups["high_pre_CW"] = np.array([e["pre"] >= 0 and ranks[e["pre"], 1] > .9 for e in events])
    data = np.column_stack([a["values"][:, :3], ranks])
    query_changes = np.full((len(events), 4, 6), np.nan)
    for j, ev in enumerate(events):
        if ev["pre"] < 0: continue
        for h, p in enumerate(ev["posts"]):
            if p >= 0: query_changes[j, h] = data[p]-data[ev["pre"]]
    for group, mask in groups.items():
        cell = {"labelled_events": int(mask.sum()), "query_only": {}, "levels": {}}
        for h in range(4):
            cell["query_only"][str(8*(h+1))] = {
                "reasons": dict(Counter(e["reasons"][h] for e, keep in zip(events, mask, strict=True) if keep)),
                "summary": summarize(a, metadata, eps[mask], query_changes[mask, h])}
        for li, level in enumerate(mm.LEVELS):
            item = {"matching_reasons": dict(Counter(reasons[li, mask])), "horizons": {}}
            for h in range(4):
                good = mask & np.isfinite(values[li, :, h]).all(1)
                usable_donors = [a["episode"][donors[li, j][posts[li, j, :, h] >= 0]] for j in np.flatnonzero(good)]
                item["horizons"][str(8*(h+1))] = {
                    "summary": summarize(a, metadata, eps[good], values[li, good, h]),
                    "donor_reuse": reuse(a, eps[good], usable_donors)}
            good = mask & np.isfinite(common[li]).all((1, 2))
            item["same_cohort_same_donors_four_blocks"] = summarize(a, metadata, eps[good], common[li, good].reshape((-1, 72)))
            cell["levels"][level] = item
        result[group] = cell
    return result


def run(expected):
    start = time.monotonic(); guard = M14AccessGuard(ROOT); guard.install()
    head, sources = source_freeze(expected)
    if OUT.exists(): raise ValueError("refusing to overwrite M14 output")
    hashes = prior_inputs(); a, metadata, labels = read_inputs()
    events, label_audit, index, run_ids = mm.recovery_inventory(a, metadata, labels)
    segments, _ = mm.runs(a)
    OUT.mkdir(parents=True)
    reference = mm.fit_reference(a, metadata)
    np.savez_compressed(OUT/"normal_reference.npz", **reference)
    log = dict(status="normal_reference_frozen_before_attack_percentiles", implementation_commit=head,
               source_sha256=sources, input_sha256=hashes, started_utc=datetime.now(timezone.utc).isoformat(),
               normal_reference_sha256=sha((OUT/"normal_reference.npz").read_bytes()),
               evidence_role="G-dev development temporal mechanism description; not detector, causal or confirmatory evidence")
    write_json(OUT/"run_manifest.json", log)
    ranks = mm.percentiles(a, metadata, reference)
    np.savez_compressed(OUT/"local_percentiles.npz", ranks=ranks)
    graphs = {}
    for offset in (0, 4):
        grid = mm.grids(a, segments, offset); donors, reasons = mm.segment_graph(a, metadata, ranks, grid)
        graphs.update({f"offset{offset}_grid": grid, f"offset{offset}_donors": donors, f"offset{offset}_reasons": reasons})
    donors, posts, reasons = mm.recovery_graph(a, metadata, ranks, events, index, run_ids)
    graphs.update(recovery_donors=donors, recovery_posts=posts, recovery_reasons=reasons)
    np.savez_compressed(OUT/"temporal_graph.npz", **graphs)
    write_json(OUT/"recovery_events.json", events); write_json(OUT/"label_audit.json", label_audit)
    graph_sha = sha((OUT/"temporal_graph.npz").read_bytes())
    log.update(status="matching_graph_frozen_before_temporal_outcomes", graph_sha256=graph_sha)
    write_json(OUT/"run_manifest.json", log)
    print(json.dumps({"stage": log["status"], "recovery_label_counts": label_audit["counts"]}), flush=True)
    metrics, serial = {}, {}
    for offset in (0, 4):
        grid = graphs[f"offset{offset}_grid"]; v = mm.segment_metrics(ranks, grid); metrics[f"offset{offset}"] = v
        serial[str(offset)] = summarize_segments(a, metadata, ranks, grid, graphs[f"offset{offset}_donors"], graphs[f"offset{offset}_reasons"], v)
        print(json.dumps({"stage": "temporal_summary", "grid_offset": offset, "segments": len(grid)}), flush=True)
    rec, available = mm.recovery_values(a, ranks, events, donors, posts)
    common, common_available = mm.recovery_values(a, ranks, events, donors, posts, common=True)
    metrics.update(recovery=rec, recovery_donor_count=available, recovery_common=common, recovery_common_donor_count=common_available)
    np.savez_compressed(OUT/"temporal_metrics.npz", **metrics)
    result = {"serial": serial, "recovery": summarize_recovery(a, metadata, ranks, events, donors, posts, reasons, rec, common),
              "label_audit": label_audit, "columns": mm.COLUMNS, "paired_columns": [f"{s}/{c}" for s in ("query", "normal", "excess") for c in mm.COLUMNS],
              "recovery_columns": mm.REC_COLUMNS, "episodes": len(a["keys"]), "looks": len(a["ends"]),
              "missing_reference_looks": int((~np.isfinite(ranks).all(1)).sum()), "excluded_preinjection_episodes": 88,
              "reference_episode_counts": {k: len(v) for k, v in reference.items() if k.endswith("_episodes")},
              "uncertainty": "episode-equal query-family and family-tier bootstrap conditional on fixed normals/donors; length-matched retrospective analysis, no online or causal interpretation"}
    write_json(OUT/"result.json", result)
    assert prior_inputs() == hashes and source_freeze(expected) == (head, sources)
    assert sha((OUT/"temporal_graph.npz").read_bytes()) == graph_sha
    log.update(status="completed", elapsed_seconds=time.monotonic()-start,
               finished_utc=datetime.now(timezone.utc).isoformat(), peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2,
               access_guard=guard.summary(), output_sha256={p.name: sha(p.read_bytes()) for p in sorted(OUT.iterdir()) if p.is_file() and p.name != "run_manifest.json"})
    assert not log["access_guard"]["blocked_attempts"]
    write_json(OUT/"run_manifest.json", log)
    print(json.dumps({k: log[k] for k in ("status", "elapsed_seconds", "peak_rss_gib")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--freeze-commit", required=True)
    run(parser.parse_args().freeze_commit)
