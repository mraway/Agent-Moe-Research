"""Fixed spectral-floor ablation, W only; pure numpy, no dataset I/O."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from research_v4 import codex_g_alg_a03_math as old

FLOOR = .1
CELLS = ("W_diag_floor", "W_block_floor", "W_full_floor")


def floored(sigma, lower):
    sigma = np.asarray(sigma, np.float64)
    if (sigma.ndim != 2 or sigma.shape[0] != sigma.shape[1] or not len(sigma)
            or not np.isfinite(sigma).all() or not np.allclose(sigma, sigma.T, rtol=0, atol=1e-12)
            or not np.isfinite(lower) or lower <= 0):
        raise ValueError("finite symmetric SPD matrix and positive floor required")
    values, vectors = np.linalg.eigh(sigma)
    if values[0] <= 0: raise ValueError("source regularized matrix must be SPD")
    raised = np.maximum(values, lower)
    return vectors/np.sqrt(raised), {"eigenvalues": values.tolist(), "raised": int(np.count_nonzero(values < lower)),
                                    "lower": float(lower), "condition_before": float(values[-1]/values[0]),
                                    "condition_after": float(raised[-1]/raised[0])}


@dataclass
class Model:
    mu: np.ndarray
    cov: np.ndarray
    diag_whitener: np.ndarray
    block_whitener: np.ndarray
    full_whitener: np.ndarray
    layer_width: int = 32

    def state(self):
        return {**{k: getattr(self, k) for k in ("mu", "cov", "diag_whitener", "block_whitener", "full_whitener")},
                "layer_width": np.asarray(self.layer_width), "floor": np.asarray(FLOOR),
                "rho": np.asarray(old.RHO), "eta": np.asarray(old.ETA)}

    @classmethod
    def restore(cls, state):
        if (float(state["floor"]), float(state["rho"]), float(state["eta"])) != (FLOOR, .5, .01):
            raise ValueError("fixed regularization changed")
        model = cls(*(np.asarray(state[k]) for k in ("mu", "cov", "diag_whitener", "block_whitener", "full_whitener")),
                    layer_width=int(state["layer_width"]))
        if model.mu.ndim != 1 or not len(model.mu) or model.layer_width <= 0 or len(model.mu) % model.layer_width:
            raise ValueError("invalid dimension or partial layer")
        d, w = len(model.mu), model.layer_width
        for k, shape in zip(("mu", "cov", "diag_whitener", "block_whitener", "full_whitener"),
                            ((d,), (d, d), (d,), (d//w, w, w), (d, d))):
            if getattr(model, k).shape != shape or not np.isfinite(getattr(model, k)).all():
                raise ValueError("invalid frozen matrix")
        return model


def derive(mu, cov, layer_width=32):
    mu, cov = np.asarray(mu, np.float64), np.asarray(cov, np.float64)
    if mu.ndim != 1 or cov.shape != (len(mu), len(mu)) or not np.isfinite(mu).all():
        raise ValueError("aligned finite mean/covariance required")
    a, b, c = old.matrices(cov, layer_width)
    lower = FLOOR*np.trace(cov)/len(cov)
    diag = 1/np.sqrt(np.maximum(np.diag(a), lower))
    blocks, binfo = [], []
    for lo in range(0, len(mu), layer_width):
        transform, row = floored(b[lo:lo+layer_width, lo:lo+layer_width], lower)
        blocks.append(transform); binfo.append(row)
    full, finfo = floored(c, lower)
    model = Model(mu.copy(), cov.copy(), diag, np.stack(blocks), full, layer_width)
    before, after = np.diag(a), np.maximum(np.diag(a), lower)
    info = {"floor_ratio": FLOOR, "variance": float(np.trace(cov)/len(cov)), "lower": float(lower),
            "diag": {"raised": int(np.count_nonzero(before < lower)), "condition_before": float(before.max()/before.min()),
                     "condition_after": float(after.max()/after.min())}, "blocks": binfo, "full": finfo}
    return Model.restore(model.state()), info


def score(roots, model, block=256):
    roots = np.asarray(roots)
    if roots.ndim != 2 or roots.shape[1] != len(model.mu) or not np.isfinite(roots).all() or block <= 0:
        raise ValueError("aligned finite query required")
    n, d = roots.shape; out = np.empty((n, 3), np.float64)
    for lo in range(0, n, block):
        delta = roots[lo:lo+block].astype(np.float64)-model.mu
        out[lo:lo+len(delta), 0] = np.square(delta*model.diag_whitener).sum(1)/d
        value = np.zeros(len(delta))
        for layer, transform in enumerate(model.block_whitener):
            start = layer*model.layer_width
            value += np.square(delta[:, start:start+model.layer_width] @ transform).sum(1)
        out[lo:lo+len(delta), 1] = value/d
        out[lo:lo+len(delta), 2] = np.square(delta @ model.full_whitener).sum(1)/d
    if not np.isfinite(out).all() or np.any(out < 0): raise ValueError("invalid score")
    return out
