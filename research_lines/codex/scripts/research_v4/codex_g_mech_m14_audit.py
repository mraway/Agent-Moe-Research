"""Independent M14 arithmetic/selection checks, using only frozen inputs/outputs."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import time
import numpy as np

from research_v4 import codex_g_mech_m14 as run
from research_v4.codex_g_m1 import ROOT, sha, write_json


def brute_metrics(values, threshold):
    h = [float(v) > threshold for v in values]; n = len(h); k = sum(h)
    lengths = []; current = 0
    for high in h+[False]:
        if high: current += 1
        elif current: lengths.append(current); current = 0
    observed = sum(h[i] and h[i+1] for i in range(n-1))
    expected_pairs = k*(k-1)/n
    excess = (observed-expected_pairs)/(n-1) if n > 1 else np.nan
    return np.array([sum(values)/n, k/n, excess, len(lengths), max(lengths, default=0)*8, int(h[0]), int(h[-1])])


def brute_select(a, metadata, ranks, query, candidate_looks, candidate_ids, *, family=False):
    if not np.isfinite(ranks[query]).all(): return []
    row = metadata[str(a["keys"][a["episode"][query]])]; choices = []
    for look, identity in zip(candidate_looks, candidate_ids, strict=True):
        other = metadata[str(a["keys"][a["episode"][look]])]
        if other["scenario"] == row["scenario"] or (family and other["family"] != row["family"]): continue
        if not np.isfinite(ranks[look]).all(): continue
        dcw = abs(float(ranks[look, 1]-ranks[query, 1])); dtu = abs(float(ranks[look, 2]-ranks[query, 2]))
        dt = abs(int(a["ends"][look])-int(a["ends"][query]))
        if dcw <= .100000000001 and dtu <= .100000000001 and dt <= 64:
            choices.append((dcw+dtu, dt, str(a["keys"][a["episode"][look]]), int(identity)))
    out, seen = [], set()
    for _, _, key, identity in sorted(choices):
        if key in seen: continue
        out.append(identity); seen.add(key)
        if len(out) == 3: break
    return out


def direct_rank(a, metadata, query, column):
    same = (a["structure"][:, :2] == a["structure"][query, :2]).all(1)
    ids = np.flatnonzero(same); episodes = []
    for ep in np.unique(a["episode"][ids]):
        row = metadata[str(a["keys"][ep])]
        if row["variant"] not in ("clean", "benign_control", "benign_lexical") or row["filter_pass"] is not True: continue
        if ep == a["episode"][query]: continue
        values = a["values"][ids[a["episode"][ids] == ep], column+3]; value = a["values"][query, column+3]
        episodes.append(float(((values < value).sum()+.5*(values == value).sum())/len(values)))
    return float(np.mean(episodes)) if len(episodes) >= 10 else np.nan


def check_summaries(node, metadata):
    count = 0
    if isinstance(node, dict):
        if "episode_values" in node:
            rows = node["episode_values"]; assert node["episodes"] == len(rows)
            assert node["rows"] == sum(r["rows"] for r in rows)
            if rows:
                v = np.array([r["values"] for r in rows]); np.testing.assert_allclose(v.mean(0), node["mean"], rtol=0, atol=1e-12)
                for kind in ("family", "family_tier"):
                    labels = [metadata[r["key"]]["family"]+("|"+metadata[r["key"]]["tier"] if kind == "family_tier" else "") for r in rows]
                    unique = sorted(set(labels)); sizes = Counter(labels)
                    sums = np.array([v[np.array(labels) == key].sum(0) for key in unique])
                    counts = np.array([sizes[key] for key in unique])
                    draws = np.random.default_rng(802608).integers(len(unique), size=(2000, len(unique)))
                    replicates = sums[draws].sum(1)/counts[draws].sum(1)[:, None]
                    np.testing.assert_allclose(np.quantile(replicates, [.025, .975], axis=0).T, node[kind]["mean_ci95"], rtol=0, atol=1e-12)
            else: assert node["mean"] is None
            count += 1
        for value in node.values(): count += check_summaries(value, metadata)
    elif isinstance(node, list):
        for value in node: count += check_summaries(value, metadata)
    return count


def audit():
    started = time.monotonic(); guard = run.M14AccessGuard(ROOT); guard.install()
    log = json.loads((run.OUT/"run_manifest.json").read_text()); assert log["status"] == "completed"
    for path, digest in {**log["input_sha256"], **log["source_sha256"]}.items():
        if sha((ROOT/path).read_bytes()) != digest: raise ValueError("M14 input/source changed")
    for name, digest in log["output_sha256"].items(): assert sha((run.OUT/name).read_bytes()) == digest
    a, metadata, labels = run.read_inputs(); read = run.m8.m7.read_npz
    ranks = read(run.OUT/"local_percentiles.npz")["ranks"]; graph = read(run.OUT/"temporal_graph.npz")
    metrics = read(run.OUT/"temporal_metrics.npz"); events = json.loads((run.OUT/"recovery_events.json").read_text())
    normal = run.mm.normal_mask(a["keys"], metadata)
    samples = set()
    for ep in range(len(a["keys"])):
        for tag in range(3):
            ix = np.flatnonzero((a["episode"] == ep) & (a["structure"][:, 1] == tag))
            if len(ix): samples.update((int(ix[0]), int(ix[len(ix)//2]), int(ix[-1])))
    # Direct episode-equal CDF, not sorted-table subtraction; CW at every selected point.
    maximum = 0.
    for i in sorted(samples):
        actual = direct_rank(a, metadata, i, 1)
        np.testing.assert_allclose(ranks[i, 1], actual, rtol=0, atol=1e-10, equal_nan=True)
        if np.isfinite(actual): maximum = max(maximum, abs(float(ranks[i, 1]-actual)))
    for i in sorted(samples)[::31]:
        for col in (0, 2): np.testing.assert_allclose(ranks[i, col], direct_rank(a, metadata, i, col), rtol=0, atol=1e-10, equal_nan=True)
    segments, run_ids = run.mm.runs(a); selections = sequences = 0
    for offset in (0, 4):
        grid = graph[f"offset{offset}_grid"]; donors = graph[f"offset{offset}_donors"]
        np.testing.assert_array_equal(grid, run.mm.grids(a, segments, offset))
        for i, row in enumerate(grid):
            ix = row[2]+8*np.arange(row[3]); assert (np.diff(a["ends"][ix]) == 8).all()
            assert (run_ids[ix] == row[1]).all()
            for t, threshold in enumerate((.9, .95)):
                for col in range(3):
                    if np.isfinite(ranks[ix, col]).all():
                        np.testing.assert_allclose(metrics[f"offset{offset}"][i, t, col], brute_metrics(ranks[ix, col], threshold), rtol=0, atol=1e-12, equal_nan=True)
                    sequences += 1
            allowed = np.flatnonzero(normal[grid[:, 0]] & (grid[:, 3:8] == row[3:8]).all(1))
            for level in range(2):
                expected = [] if run.mm.group_of(metadata[str(a["keys"][row[0]])]) == "excluded_preinjection" else brute_select(a, metadata, ranks, int(row[2]), grid[allowed, 2], allowed, family=bool(level))
                assert donors[level, i][donors[level, i] >= 0].tolist() == expected; selections += 1
    lookup = {(int(ep), int(end)): i for i, (ep, end) in enumerate(zip(a["episode"], a["ends"], strict=True))}
    for j, ev in enumerate(events):
        for level in range(2):
            pre = ev["pre"]
            bank = np.flatnonzero(normal[a["episode"]] & (a["structure"][:, :4] == a["structure"][pre, :4]).all(1)) if pre >= 0 else []
            expected = brute_select(a, metadata, ranks, pre, bank, bank, family=bool(level)) if pre >= 0 else []
            ds = graph["recovery_donors"][level, j]; assert ds[ds >= 0].tolist() == expected; selections += 1
            for di, d in enumerate(ds):
                for h in range(4):
                    p = lookup.get((int(a["episode"][d]), int(a["ends"][d])+8*(h+1)), -1) if d >= 0 else -1
                    if p >= 0 and run_ids[p] != run_ids[d]: p = -1
                    assert graph["recovery_posts"][level, j, di, h] == p
            for mode, name in ((False, "recovery"), (True, "recovery_common")):
                for h, p in enumerate(ev["posts"]):
                    dp = graph["recovery_posts"][level, j]
                    valid = (dp >= 0).all(1) if mode else dp[:, h] >= 0
                    valid &= ds >= 0
                    if mode and any(x < 0 for x in ev["posts"]): valid[:] = False
                    expected = np.full(18, np.nan)
                    if pre >= 0 and p >= 0 and valid.any():
                        terms = []
                        for data in (a["values"][:, :3], ranks):
                            for col in range(3):
                                q = float(data[p, col]-data[pre, col])
                                n = float(np.mean([data[dp[dj, h], col]-data[ds[dj], col] for dj in np.flatnonzero(valid)]))
                                terms.extend([q, n, q-n])
                        expected = np.array(terms)
                    np.testing.assert_allclose(metrics[name][level, j, h], expected, rtol=0, atol=1e-12, equal_nan=True)
    result = json.loads((run.OUT/"result.json").read_text()); summaries = check_summaries(result, metadata)
    checks = dict(status="PASS", independent_sequence_readouts=sequences, independent_matching_calls=selections,
                  direct_CW_rank_points=len(samples), direct_S_TU_rank_points=2*len(sorted(samples)[::31]),
                  rank_max_absolute_error=maximum, checked_summary_arithmetic_and_cluster_intervals=summaries,
                  source_and_input_hashes_checked=len(log["input_sha256"])+len(log["source_sha256"]),
                  elapsed_seconds=time.monotonic()-started, finished_utc=datetime.now(timezone.utc).isoformat(), access_guard=guard.summary())
    target = run.OUT/"audit"; target.mkdir(exist_ok=True)
    if (target/"checks.json").exists(): raise ValueError("refusing to overwrite M14 audit")
    write_json(target/"checks.json", checks); print(json.dumps(checks), flush=True)


if __name__ == "__main__": audit()
