"""Normal-only A03-N diagnostics; no labels, thresholds, detector fitting or I/O."""
from __future__ import annotations

from itertools import permutations
import numpy as np
from research_v4 import codex_g_alg_a03_math as old

GROUPS, MAX_ITER = 4, 50


def sigma(cov):
    c = np.asarray(cov, np.float64)
    if c.ndim != 2 or c.shape[0] != c.shape[1] or not np.isfinite(c).all():
        raise ValueError("finite square covariance required")
    if not np.allclose(c, c.T, rtol=0, atol=1e-12) or np.trace(c) <= 0:
        raise ValueError("symmetric positive-trace covariance required")
    return .5*c + np.diag(.5*np.diag(c)+.01*np.trace(c)/len(c))


def mixture(means, covs, masses):
    means, covs, masses = map(lambda x: np.asarray(x, np.float64), (means, covs, masses))
    if means.ndim != 2 or covs.shape != (len(means), means.shape[1], means.shape[1]) or masses.shape != (len(means),):
        raise ValueError("unaligned mixture")
    if not all(np.isfinite(v).all() for v in (means, covs, masses)) or np.any(masses <= 0):
        raise ValueError("positive finite mixture required")
    masses = masses/masses.sum(); mu = masses @ means
    delta = means-mu
    cov = np.einsum("g,gij->ij", masses, covs)+np.einsum("g,gi,gj->ij", masses, delta, delta)
    return mu, (cov+cov.T)/2


def grouped_moments(roots, keys, scenarios):
    scenarios = np.asarray(scenarios, np.int64)
    if set(scenarios) != set(range(len(set(scenarios)))) or len(set(scenarios)) < GROUPS:
        raise ValueError("contiguous scenario indices, at least four required")
    group = scenarios % GROUPS; means, covs, masses = [], [], []
    for g in range(GROUPS):
        mask = group == g
        w = old.balanced_weights(np.asarray(keys)[mask], scenarios[mask])
        mu, cov = old.moments(np.asarray(roots)[mask], w)
        means.append(mu); covs.append(cov); masses.append(len(set(scenarios[mask])))
    return np.asarray(means), np.asarray(covs), np.asarray(masses, np.float64), group


def spectrum(roots, mu, cov):
    roots, mu = np.asarray(roots, np.float64), np.asarray(mu, np.float64)
    d = len(mu)
    if roots.ndim != 2 or roots.shape[1] != d or d % 4 or not np.isfinite(roots).all():
        raise ValueError("finite roots with dimension divisible by four required")
    values, vectors = np.linalg.eigh(sigma(cov))
    if values[0] <= 0: raise ValueError("nonpositive regularized eigenvalue")
    projected2 = np.square((roots-mu) @ vectors)
    parts = (projected2/values/d).reshape(len(roots), 4, d//4).sum(-1)
    energies = projected2.reshape(len(roots), 4, d//4).sum(-1)
    return values, vectors, parts, energies


def distances(x, centres):
    x, centres = np.asarray(x, np.float64), np.asarray(centres, np.float64)
    if x.ndim != 2 or centres.shape != (2, x.shape[1]) or not np.isfinite(x).all() or not np.isfinite(centres).all():
        raise ValueError("finite aligned two-centre inputs required")
    return np.maximum(np.square(x).sum(1)[:, None]+np.square(centres).sum(1)[None, :]-2*x@centres.T, 0)


def two_centres(x, weights, max_iter=MAX_ITER):
    x, weights = np.asarray(x, np.float64), np.asarray(weights, np.float64)
    if x.ndim != 2 or len(x) < 2 or weights.shape != (len(x),) or max_iter < 1:
        raise ValueError("nonempty weighted points required")
    if not np.isfinite(x).all() or not np.isfinite(weights).all() or np.any(weights <= 0):
        raise ValueError("finite positive weights required")
    weights = weights/weights.sum()
    first = int(np.argmax(np.square(x).sum(1)))
    second = int(np.argmax(np.square(x-x[first]).sum(1)))
    centres = x[[first, second]].copy()
    labels = distances(x, centres).argmin(1); history = []
    status = "max_iter"
    for iteration in range(1, max_iter+1):
        mass = np.bincount(labels, weights=weights, minlength=2)
        if np.any(mass == 0):
            status = "empty_cluster"; break
        centres = np.stack([weights[labels == j] @ x[labels == j]/mass[j] for j in range(2)])
        ds = distances(x, centres); fresh = ds.argmin(1)
        history.append(float(weights @ ds[np.arange(len(x)), fresh]))
        if np.array_equal(fresh, labels):
            labels = fresh; status = "converged"; break
        labels = fresh
    return centres, {"status": status, "iterations": iteration, "objective_history": history,
                     "initial_rows": [first, second]}


def align_centres(reference, centres):
    choices = list(permutations((0, 1)))
    loss = [float(np.square(reference-centres[list(p)]).sum()) for p in choices]
    return centres[list(choices[int(np.argmin(loss))])]


def cluster_support(x, weights, scenarios, centres):
    labels = distances(x, centres).argmin(1)
    group_ids = sorted(set(map(int, scenarios))); dominant = [0, 0]
    for g in group_ids:
        mask = scenarios == g
        dominant[int(np.argmax(np.bincount(labels[mask], weights=weights[mask], minlength=2)))] += 1
    return {"mass": np.bincount(labels, weights=weights, minlength=2).tolist(),
            "any_scenarios": [len(set(scenarios[labels == j])) for j in range(2)],
            "dominant_scenarios": dominant, "total_scenarios": len(group_ids)}


def diagnose(roots, keys, scenarios, model, queries, check=lambda: None):
    """One fixed fold/channel; four scenario deletions, original metric for prototypes."""
    roots, queries = np.asarray(roots), np.asarray(queries)
    weights = old.balanced_weights(keys, scenarios)
    gm, gc, mass, group = grouped_moments(roots, keys, scenarios)
    mu0, cov0 = mixture(gm, gc, mass)
    np.testing.assert_allclose(mu0, model.mu[1], rtol=0, atol=1e-12)
    np.testing.assert_allclose(cov0, model.cov[1], rtol=0, atol=1e-12)
    mus, covs = [model.mu[1]], [model.cov[1]]
    for g in range(GROUPS):
        keep = np.arange(GROUPS) != g
        mu, cov = mixture(gm[keep], gc[keep], mass[keep]); mus.append(mu); covs.append(cov)
    d = queries.shape[1]
    whitened_fit = (roots.astype(np.float64)-model.mu[1]) @ model.full_whitener[1]
    whitened_q = (queries.astype(np.float64)-model.mu[1]) @ model.full_whitener[1]
    scores, parts, energy, spectra, overlaps, centres, labels, two_scores, records = [], [], [], [], [], [], [], [], []
    reference_vectors = None
    for i, (mu, cov) in enumerate(zip(mus, covs)):
        check(); values, vectors, qp, en = spectrum(queries, mu, cov)
        spectra.append(values); scores.append(qp.sum(1)); parts.append(qp); energy.append(en)
        if i == 0: reference_vectors = vectors[:, :d//4].copy()
        overlaps.append(float(np.square(reference_vectors.T @ vectors[:, :d//4]).sum()/(d//4)))
        keep = np.ones(len(roots), bool) if i == 0 else group != i-1
        ws = weights[keep]/weights[keep].sum()
        centre, info = two_centres(whitened_fit[keep], ws)
        if i: centre = align_centres(centres[0], centre)
        centres.append(centre)
        ds = distances(whitened_q, centre); lab = ds.argmin(1)
        labels.append(lab); two_scores.append(ds[np.arange(len(queries)), lab]/d)
        info.update(cluster_support(whitened_fit[keep], ws, scenarios[keep], centre))
        records.append({"delete_group": None if i == 0 else i-1,
                        "fit_scenarios": len(set(scenarios[keep])), "fit_looks": int(keep.sum()),
                        "condition_number": float(values[-1]/values[0]),
                        "small_subspace_overlap": overlaps[-1],
                        "small_boundary_eigenvalue_ratio": float(values[d//4]/values[d//4-1]),
                        "relative_covariance_frobenius_change": float(np.linalg.norm(cov-covs[0])/np.linalg.norm(covs[0])),
                        "covariance_participation_rank": float(np.trace(cov)**2/np.square(cov).sum()),
                        "centres": info})
    state = {"means": np.asarray(mus), "covariances": np.asarray(covs), "eigenvalues": np.asarray(spectra),
             "scores": np.asarray(scores), "parts": np.asarray(parts), "euclidean_parts": np.asarray(energy),
             "centres": np.asarray(centres), "labels": np.asarray(labels), "two_scores": np.asarray(two_scores),
             "group_means": gm, "group_covariances": gc, "group_scenario_counts": mass}
    return state, {"models": records, "group_scenario_counts": mass.astype(int).tolist(),
                   "raw_mean_error": float(np.max(abs(mu0-model.mu[1]))),
                   "raw_cov_error": float(np.max(abs(cov0-model.cov[1])))}


def scenario_mean(values, rows):
    groups = sorted({r["scenario"] for r in rows})
    return float(np.mean([np.mean([v for v, r in zip(values, rows) if r["scenario"] == g]) for g in groups]))


def summarise(rows, state):
    out = {}
    for role in ("fit", "cal", "eval"):
        for kind in ("middle", "peak", "old_tail"):
            for quality in ("all", "passed", "failed"):
                indices = [i for i, r in enumerate(rows) if r["role"] == role and r["kind"] == kind and
                           (quality == "all" or (r["filter_pass"] is True) == (quality == "passed"))]
                if not indices: continue
                rr = [rows[i] for i in indices]; q = state["scores"][:, indices]; tiny = np.finfo(float).tiny
                ratios = q[1:]/np.maximum(q[0], tiny)
                multiplicative = np.maximum(ratios, 1/np.maximum(ratios, tiny)).max(0)
                fractions = state["parts"][0, indices, 0]/np.maximum(q[0], tiny)
                en = state["euclidean_parts"][0, indices]
                gain = 1-state["two_scores"][0, indices]/np.maximum(q[0], tiny)
                agreement = [scenario_mean((state["labels"][i, indices] == state["labels"][0, indices]).astype(float), rr)
                             for i in range(1, 5)]
                out[f"{role}/{kind}/{quality}"] = {
                    "episodes": len(rr), "scenarios": len({r["scenario"] for r in rr}),
                    "q_median_p90": np.quantile(q[0], [.5, .9]).tolist(),
                    "small_quarter_q_fraction_median": float(np.median(fractions)),
                    "small_quarter_euclidean_fraction_median": float(np.median(en[:, 0]/np.maximum(en.sum(1), tiny))),
                    "delete_max_factor_median_p90": np.quantile(multiplicative, [.5, .9]).tolist(),
                    "delete_factor_gt_1_5_scenario_mean": scenario_mean((multiplicative > 1.5).astype(float), rr),
                    "two_centre_relative_reduction_median_p10_p90": np.quantile(gain, [.5, .1, .9]).tolist(),
                    "two_centre_reduction_positive_scenario_mean": scenario_mean((gain > 0).astype(float), rr),
                    "centre_delete_agreement_scenario_mean": agreement,
                    "note": "episode quantiles and scenario-balanced means; not independent looks, FAR or evidence of density modes"}
    return out
