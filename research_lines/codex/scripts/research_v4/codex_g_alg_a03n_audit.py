"""Independent moments/linear-solve/prototype replay for A03-N normal diagnostics."""
from __future__ import annotations
import argparse
import json
import time
import numpy as np
import torch
from research_v4 import codex_g_alg_a03n_run as run


def literal_moments(x, keys, scenarios):
    """Uncentred second moment with independently constructed group/episode weights."""
    groups = sorted(set(scenarios)); w = np.zeros(len(x))
    for group in groups:
        episodes = sorted(set(keys[scenarios == group]))
        for key in episodes:
            mask = keys == key
            w[mask] = 1/(len(groups)*len(episodes)*mask.sum())
    mu = w @ x; second = np.zeros((x.shape[1], x.shape[1]))
    for lo in range(0, len(x), 256):
        block = x[lo:lo+256].astype(np.float64)
        second += block.T @ (block*w[lo:lo+256, None])
    return mu, second-np.outer(mu, mu), w


def literal_score(x, mu, cov):
    d = x.shape[1]
    matrix = .5*cov+np.diag(.5*np.diag(cov)+.01*np.trace(cov)/d)
    delta = x.astype(np.float64)-mu
    return (delta*np.linalg.solve(matrix, delta.T).T).sum(1)/d


def audit_bank(fold, tag, ctx, checked, budget, selection, features, summary, state):
    bank, model, _, _ = run.bank_inputs(fold, tag, ctx, checked)
    rows = [selection["queries"][int(i)] for i in state["query_indices"]]
    if any(r["fold"] != fold or r["tag"] != tag for r in rows): raise AssertionError("query identity mismatch")
    roots = features[[r["feature_index"] for r in rows]]
    gm, gc, counts = [], [], []
    for g in range(4):
        budget.check(); mask = bank.scenario_index % 4 == g
        mu, cov, _ = literal_moments(bank.mean_roots[1][mask], bank.keys[mask], bank.scenario_index[mask])
        gm.append(mu); gc.append(cov); counts.append(len(set(bank.scenario_index[mask])))
    gm, gc, counts = np.asarray(gm), np.asarray(gc), np.asarray(counts)
    np.testing.assert_allclose(state["group_means"], gm, rtol=0, atol=1e-12)
    np.testing.assert_allclose(state["group_covariances"], gc, rtol=0, atol=1e-12)
    np.testing.assert_array_equal(counts, state["group_scenario_counts"])
    zq = (roots.astype(np.float64)-model.mu[1]) @ model.full_whitener[1]
    zfit = (bank.mean_roots[1].astype(np.float64)-model.mu[1]) @ model.full_whitener[1]
    _, _, w = literal_moments(bank.mean_roots[1], bank.keys, bank.scenario_index)
    max_error = 0.
    for i in range(5):
        budget.check(); groups = np.arange(4) != i-1 if i else np.ones(4, bool)
        p = counts[groups]/counts[groups].sum(); mean = p @ gm[groups]
        raw_second = np.asarray([c+np.outer(m, m) for c, m in zip(gc[groups], gm[groups])])
        cov = np.einsum("g,gij->ij", p, raw_second)-np.outer(mean, mean)
        np.testing.assert_allclose(mean, state["means"][i], rtol=0, atol=1e-12)
        np.testing.assert_allclose(cov, state["covariances"][i], rtol=0, atol=1e-12)
        q = literal_score(roots, state["means"][i], state["covariances"][i])
        np.testing.assert_allclose(q, state["scores"][i], rtol=1e-9, atol=1e-10)
        np.testing.assert_allclose(state["parts"][i].sum(1), q, rtol=1e-9, atol=1e-10)
        np.testing.assert_allclose(state["euclidean_parts"][i].sum(1), np.square(roots-state["means"][i]).sum(1), rtol=1e-9, atol=1e-10)
        max_error = max(max_error, float(np.max(abs(q-state["scores"][i]))))
        centres = state["centres"][i]
        # Explicit coordinate distances, not the norm/dot-product kernel used in fitting.
        ds = np.stack([np.square(zq-c).sum(1) for c in centres], axis=1)
        labels = ds.argmin(1)
        np.testing.assert_array_equal(labels, state["labels"][i])
        np.testing.assert_allclose(ds.min(1)/roots.shape[1], state["two_scores"][i], rtol=1e-9, atol=1e-10)
        if summary["models"][i]["centres"]["status"] == "converged":
            keep = np.ones(len(zfit), bool) if i == 0 else bank.scenario_index % 4 != i-1
            train_labels = np.stack([np.square(zfit[keep]-c).sum(1) for c in centres], axis=1).argmin(1)
            for j in range(2):
                mask = train_labels == j
                literal = (zfit[keep][mask]*w[keep][mask, None]).sum(0)/w[keep][mask].sum()
                np.testing.assert_allclose(literal, centres[j], rtol=1e-9, atol=1e-10)
        if not 0 <= summary["models"][i]["small_subspace_overlap"] <= 1+1e-10:
            raise AssertionError("invalid subspace overlap")
    if run.math.summarise(rows, state) != summary["summaries"]: raise AssertionError("summary replay mismatch")
    return {"fold": fold, "tag": tag, "query_count": len(rows), "score_checks": 5*len(rows),
            "prototype_checks": 5*len(rows), "linear_solve_max_error": max_error}


def main(source_sha, pre_sha, run_sha):
    start = time.monotonic(); guard = run.Guard(); guard.install(); checked = run.pf.CheckedInputs(guard)
    run.verify_sources(source_sha, checked)
    prelog = run.prior_log(checked, "preflight", pre_sha, source_sha)
    runlog = run.prior_log(checked, "run", run_sha, source_sha)
    previous = run.RESERVE_SECONDS+prelog["elapsed_seconds"]+runlog["elapsed_seconds"]
    budget = run.parent.Budget(previous); folder = run.OUT/"audit"; folder.mkdir(exist_ok=False)
    status, error = "failed", None
    try:
        torch.set_num_threads(1); torch.set_num_interop_threads(1)
        selection = checked.json(run.OUT/"preflight/selection.json", prelog["output_sha256"]["selection.json"])
        features = run.ai.npz_bytes(checked.read(run.OUT/"preflight/features.npz", prelog["output_sha256"]["features.npz"]))["roots"]
        result = checked.json(run.OUT/"run/result.json", runlog["output_sha256"]["result.json"])
        ctx = run.context(checked); checks = []
        for fold, tag in run.CONDITIONS:
            name = f"{fold}/{tag}"; summary = result["banks"][name]
            root = run.OUT/"preflight/pilot" if name == "0/analysis" else run.OUT/"run"/f"fold{fold}_{tag}"
            state = run.ai.npz_bytes(checked.read(root/"state.npz", summary["state_sha256"]))
            check = audit_bank(fold, tag, ctx, checked, budget, selection, features, summary, state)
            checks.append(check); print(json.dumps({"bank_audit": check}), flush=True)
        # Independent checks of query endpoints and selections against frozen normal streams.
        checked_rows = 0
        for fold in range(3):
            rec = ctx[0]["folds"][str(fold)]
            streams = run.ai.unpack(run.ai.npz_bytes(checked.read(run.parent.OUT/f"calibrate/fold{fold}/look_streams.npz", rec["streams_sha256"])))
            for row in selection["queries"]:
                if row["fold"] != fold: continue
                s = streams[row["key"]]; ix = np.flatnonzero(s["tags"] == row["tag"])
                endpoint = row["end"]
                if row["kind"] == "middle" and endpoint != int(s["ends"][ix[len(ix)//2]]): raise AssertionError("middle mismatch")
                if row["kind"] == "peak":
                    highest = max(float(s["raw"][j, 5]) for j in ix)
                    first = min(int(s["ends"][j]) for j in ix if s["raw"][j, 5] == highest)
                    if endpoint != first: raise AssertionError("peak mismatch")
                role_fold = {"fit": (fold+1)%3, "cal": (fold+2)%3, "eval": fold}[row["role"]]
                if ctx[1][row["key"]]["fold"] != role_fold: raise AssertionError("role mismatch")
                checked_rows += 1
        # Rebuild all selected windows with a literal float64 eight-token mean.
        _, _, episodes, inventory = run.pf.normal_inputs(checked)
        windows = {r["feature_index"]: (r["key"], r["end"]) for r in selection["queries"]}
        root_error = 0.
        for key in sorted({k for k, _ in windows.values()}):
            budget.check(); ps = run.pf.routes(key, episodes, checked, inventory)[1]
            for i, (k, end) in windows.items():
                if k != key: continue
                literal = np.sqrt(ps[end-7:end+1].astype(np.float64).mean(0)/24).ravel()
                np.testing.assert_allclose(features[i], literal, rtol=0, atol=2e-7)
                root_error = max(root_error, float(np.max(abs(features[i]-literal))))
        run.ai.write_json(folder/"checks.json", {"status": "PASS", "banks": checks,
                          "query_selection_checks": checked_rows, "feature_window_checks": len(windows),
                          "feature_float64_max_error": root_error,
                          "score_checks": sum(c["score_checks"] for c in checks),
                          "prototype_checks": sum(c["prototype_checks"] for c in checks),
                          "role": "independent formula replay, not independent data or external reviewer"})
        run.verify_sources(source_sha, checked); checked.verify_again(); budget.check(); status = "completed"
    except Exception as exc:
        error = repr(exc); raise
    finally:
        log = {"status": status, "error": error, "source_manifest_sha256": source_sha,
               "previous_seconds_including_reserve": previous, "elapsed_seconds": time.monotonic()-start,
               "peak_gib": run.pf.rss(), "input_sha256": checked.hashes, "output_sha256": run.parent.hashes(folder),
               "access_guard": guard.summary()}
        run.ai.write_json(folder/"run_manifest.json", log)
        print(json.dumps({k: log[k] for k in ("status", "error", "elapsed_seconds", "peak_gib")}), flush=True)
        print(json.dumps({"run_manifest_sha256": run.pf.digest((folder/"run_manifest.json").read_bytes())}), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    for n in ("source", "pre", "run"): p.add_argument(f"--{n}-sha", required=True)
    a = p.parse_args(); main(a.source_sha, a.pre_sha, a.run_sha)
