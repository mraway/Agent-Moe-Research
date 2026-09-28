"""A03 fixed second-order routing ablation. Pure numpy; no data/label I/O."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import numpy as np

CELLS = ("U_diag", "W_diag", "U_block", "W_block", "U_full", "W_full")
RHO, ETA, LAYER_WIDTH, BLOCK = .5, .01, 32, 256


def balanced_weights(keys, scenario_index):
    """Equal scenario -> equal episode -> equal look, including repeated rounds."""
    keys, scenario_index = np.asarray(keys), np.asarray(scenario_index)
    if keys.ndim != 1 or keys.shape != scenario_index.shape or not len(keys):
        raise ValueError("nonempty aligned keys/scenarios required")
    episodes = defaultdict(set)
    mapping = {}
    counts = Counter(map(str, keys))
    for k, s in zip(keys, scenario_index):
        k, s = str(k), int(s)
        if k in mapping and mapping[k] != s:
            raise ValueError("episode appears in multiple scenarios")
        mapping[k] = s
        episodes[s].add(k)
    weights = np.asarray([1/(len(episodes)*len(episodes[int(s)])*counts[str(k)])
                          for k, s in zip(keys, scenario_index)], np.float64)
    if not np.isclose(weights.sum(), 1., rtol=0, atol=1e-12):
        raise ValueError("weights do not sum to one")
    return weights


def moments(roots, weights, block=BLOCK):
    roots, weights = np.asarray(roots), np.asarray(weights, np.float64)
    if roots.ndim != 2 or len(roots) != len(weights) or not len(roots) or block <= 0:
        raise ValueError("nonempty aligned features required")
    if not np.isfinite(roots).all() or not np.isfinite(weights).all() or np.any(weights <= 0):
        raise ValueError("finite features and positive weights required")
    if not np.isclose(weights.sum(), 1., rtol=0, atol=1e-12):
        raise ValueError("unnormalized weights")
    mu = weights @ roots
    cov = np.zeros((roots.shape[1], roots.shape[1]), np.float64)
    for lo in range(0, len(roots), block):
        hi = min(lo+block, len(roots))
        centered = roots[lo:hi].astype(np.float64)-mu
        weighted = centered*np.sqrt(weights[lo:hi, None])
        cov += weighted.T @ weighted
    return mu, (cov+cov.T)/2


def matrices(cov, layer_width=LAYER_WIDTH):
    cov = np.asarray(cov, np.float64)
    if cov.ndim != 2 or cov.shape[0] != cov.shape[1] or len(cov) % layer_width:
        raise ValueError("covariance must have whole layers")
    if not np.isfinite(cov).all() or not np.allclose(cov, cov.T, rtol=0, atol=1e-12):
        raise ValueError("finite symmetric covariance required")
    variance = float(np.trace(cov)/len(cov))
    if variance <= 0 or np.any(np.diag(cov) < 0):
        raise ValueError("positive variance required")
    diagonal = np.diag(cov)+ETA*variance
    within = np.diag((1-RHO)*np.diag(cov)+ETA*variance)
    for lo in range(0, len(cov), layer_width):
        within[lo:lo+layer_width, lo:lo+layer_width] += RHO*cov[lo:lo+layer_width, lo:lo+layer_width]
    full = RHO*cov+np.diag((1-RHO)*np.diag(cov)+ETA*variance)
    return np.diag(diagonal), within, full


@dataclass
class Model:
    mu: np.ndarray
    cov: np.ndarray
    diag_whitener: np.ndarray
    block_whitener: np.ndarray
    full_whitener: np.ndarray
    layer_width: int = LAYER_WIDTH

    def state(self):
        return {"mu": self.mu, "cov": self.cov, "diag_whitener": self.diag_whitener,
                "block_whitener": self.block_whitener, "full_whitener": self.full_whitener,
                "layer_width": np.asarray(self.layer_width), "rho": np.asarray(RHO), "eta": np.asarray(ETA)}

    @classmethod
    def restore(cls, state):
        if float(state["rho"]) != RHO or float(state["eta"]) != ETA:
            raise ValueError("fixed regularization changed")
        model = cls(*(np.asarray(state[k]) for k in ("mu", "cov", "diag_whitener", "block_whitener", "full_whitener")),
                    layer_width=int(state["layer_width"]))
        d, w = model.mu.shape[-1], model.layer_width
        expected = ((2, d), (2, d, d), (2, d), (2, d//w, w, w), (2, d, d))
        for k, shape in zip(("mu", "cov", "diag_whitener", "block_whitener", "full_whitener"), expected):
            if getattr(model, k).shape != shape or not np.isfinite(getattr(model, k)).all():
                raise ValueError("invalid frozen matrix shape/value")
        return model


def fit(roots, weights, layer_width=LAYER_WIDTH):
    if len(roots) != 2 or roots[0].shape != roots[1].shape:
        raise ValueError("aligned U/W roots required")
    d = roots[0].shape[1]
    if d % layer_width:
        raise ValueError("partial layer")
    mus, covs, diag, blocks, full = [], [], [], [], []
    for r in roots:
        mu, cov = moments(r, weights)
        a, b, c = matrices(cov, layer_width)
        mus.append(mu); covs.append(cov); diag.append(1/np.sqrt(np.diag(a)))
        # Rows multiply L^{-T}: ||(y-mu)L^{-T}||^2 = (y-mu)Sigma^{-1}(y-mu)^T.
        blocks.append(np.stack([np.linalg.solve(np.linalg.cholesky(b[lo:lo+layer_width, lo:lo+layer_width]),
                                                np.eye(layer_width)).T for lo in range(0, d, layer_width)]))
        full.append(np.linalg.solve(np.linalg.cholesky(c), np.eye(d)).T)
    return Model(np.stack(mus), np.stack(covs), np.stack(diag), np.stack(blocks), np.stack(full), layer_width)


def score(roots, model, block=BLOCK):
    if len(roots) != 2 or roots[0].shape != roots[1].shape or roots[0].ndim != 2 or block <= 0:
        raise ValueError("aligned U/W query roots required")
    n, d = roots[0].shape
    if model.mu.shape != (2, d) or any(not np.isfinite(r).all() for r in roots):
        raise ValueError("invalid query dimension/value")
    out = np.empty((n, 6), np.float64)
    for lo in range(0, n, block):
        hi = min(lo+block, n)
        for rep in range(2):
            delta = np.asarray(roots[rep][lo:hi], np.float64)-model.mu[rep]
            out[lo:hi, rep] = np.square(delta*model.diag_whitener[rep]).sum(1)/d
            within = np.zeros(hi-lo)
            for layer, whiten in enumerate(model.block_whitener[rep]):
                start = layer*model.layer_width
                within += np.square(delta[:, start:start+model.layer_width] @ whiten).sum(1)
            out[lo:hi, rep+2] = within/d
            out[lo:hi, rep+4] = np.square(delta @ model.full_whitener[rep]).sum(1)/d
    if not np.isfinite(out).all() or np.any(out < 0):
        raise ValueError("nonfinite/negative scores")
    return out


def diagnostics(model):
    rows = []
    for rep, cov in enumerate(model.cov):
        norm2 = float(np.square(cov).sum())
        diag2 = float(np.square(np.diag(cov)).sum())
        within2 = sum(float(np.square(cov[lo:lo+model.layer_width, lo:lo+model.layer_width]).sum())
                      for lo in range(0, len(cov), model.layer_width))
        eigen = np.linalg.eigvalsh(matrices(cov, model.layer_width)[2])
        rows.append({"representation": ("U", "W")[rep], "dimension": len(cov),
                     "mean_coordinate_variance": float(np.trace(cov)/len(cov)),
                     "covariance_participation_rank": float(np.trace(cov)**2/norm2),
                     "within_layer_offdiag_energy_fraction": (within2-diag2)/norm2,
                     "cross_layer_energy_fraction": (norm2-within2)/norm2,
                     "full_regularized_condition_number": float(eigen[-1]/eigen[0]),
                     "note": "descriptive normal second moments; not independent sample size or detection gain"})
    return rows
