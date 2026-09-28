"""Full-routing measurements on fixed triplets; no fitting, matching or alarms."""
from __future__ import annotations

import numpy as np

REPS = ("U", "W", "P")
PARTS = ("TV_all", "TV_rare", "TV_common", "JS_all")
DISTANCES = tuple(f"{r}/{part}" for r in REPS for part in PARTS)
BANDS = ("all", "early", "middle", "late")
METRICS = ("A", "N", "D")
COLUMNS = tuple(f"{d}/{b}/{m}" for d in DISTANCES for b in BANDS for m in METRICS)
LAYER_COLUMNS = tuple(f"{d}/layer{l}/{m}" for d in DISTANCES for l in range(24) for m in METRICS)
CONDITIONAL_COLUMNS = tuple(f"{d}/{m}" for d in DISTANCES for m in METRICS)
ZERO_TOL = 1e-12


def routing_vectors(ids, logits):
    """[N,L,K], [N,L,E] -> [N,3,L,E]: actual uniform support, W, full P."""
    ids, logits = np.asarray(ids), np.asarray(logits, dtype=np.float64)
    if ids.ndim != 3 or logits.ndim != 3 or ids.shape[:2] != logits.shape[:2]: raise ValueError("unaligned geometry")
    if ids.dtype.kind not in "iu" or ids.shape[2] < 1 or ids.min(initial=0) < 0 or ids.max(initial=0) >= logits.shape[2]:
        raise ValueError("invalid expert IDs")
    if (np.diff(np.sort(ids, axis=-1), axis=-1) == 0).any() or not np.isfinite(logits).all():
        raise ValueError("duplicate expert or nonfinite logit")
    support = np.zeros(logits.shape, dtype=bool)
    np.put_along_axis(support, ids, True, axis=-1)
    uniform = support.astype(float)/ids.shape[2]
    selected_logits = np.where(support, logits, -np.inf)
    weighted = np.exp(selected_logits-selected_logits.max(-1, keepdims=True))
    weighted /= weighted.sum(-1, keepdims=True)
    full = np.exp(logits-logits.max(-1, keepdims=True)); full /= full.sum(-1, keepdims=True)
    return np.stack([uniform, weighted, full], axis=1)


def js_divergence(a, b):
    """Normalized JS divergence (not sqrt). Zero mass contributes zero."""
    a, b = np.asarray(a), np.asarray(b); middle = (a+b)/2
    if a.shape != b.shape or (a < 0).any() or (b < 0).any(): raise ValueError("invalid distributions")
    terms = np.zeros_like(middle, dtype=float)
    for source in (a, b):
        ratio = np.ones_like(middle, dtype=float)
        np.divide(source, middle, out=ratio, where=source > 0)
        terms += source*np.log(ratio)
    result = terms.sum(-1)/(2*np.log(2))
    if (result < -ZERO_TOL).any(): raise ValueError("negative JS outside roundoff")
    return np.maximum(result, 0.)


def pair_metrics(vectors, edge_nodes, rare):
    """edge_nodes index vectors; rare is [edge,L,E], common to both endpoints."""
    v, w = vectors[edge_nodes[:, 0]], vectors[edge_nodes[:, 1]]
    if rare.shape != (len(edge_nodes), vectors.shape[2], vectors.shape[3]): raise ValueError("invalid rare masks")
    terms = abs(v-w)/2
    whole = terms.sum(-1); sparse = (terms*rare[:, None]).sum(-1); common = (terms*(~rare[:, None])).sum(-1)
    js = js_divergence(v, w)
    result = np.stack([x[:, r] for r in range(3) for x in (whole, sparse, common, js)], axis=1)
    if (result < -ZERO_TOL).any() or (result > 1+ZERO_TOL).any() or not np.isfinite(result).all():
        raise ValueError("distance outside probability bounds")
    return result


def records_and_edges(graph):
    """The exact M10 matched graph, without selection or extra exclusions."""
    rows = []
    for phase in range(3):
        for cell in range(4):
            for qi in np.flatnonzero((graph["phases"] == phase) & (graph["reasons"][cell] == 0)):
                q = int(graph["queries"][qi]); a, b = graph["pairs"][cell, qi]
                if q == a or q == b or a == b: raise ValueError("degenerate triplet")
                rows.append((phase, cell, int(qi), q, int(a), int(b)))
    records = np.array(rows, dtype=np.int64).reshape((-1, 6))
    edges = sorted({tuple(sorted((int(row[i]), int(row[j])))) for row in records for i, j in ((3, 4), (3, 5), (4, 5))})
    edges = np.array(edges, dtype=np.int64).reshape((-1, 2))
    lookup = {tuple(edge): i for i, edge in enumerate(edges)}
    indices = np.array([[lookup[tuple(sorted((row[i], row[j])))] for i, j in ((3, 4), (3, 5), (4, 5))]
                       for row in records], dtype=np.int64).reshape((-1, 3))
    return records, edges, indices


def triplet_measures(distances, edge_indices):
    attack = (distances[edge_indices[:, 0]]+distances[edge_indices[:, 1]])/2
    normal = distances[edge_indices[:, 2]]
    return np.stack([attack, normal, attack-normal], axis=-1)  # [R,12,24,3]


def aggregate_layers(terms):
    if terms.shape[1:] != (12, 24, 3): raise ValueError("expected [R,12,24,3]")
    means = np.stack([terms.mean(2), *(terms[:, :, start:start+8].mean(2) for start in (0, 8, 16))], axis=2)
    return means.reshape((len(terms), len(COLUMNS))), terms.reshape((len(terms), len(LAYER_COLUMNS)))


def masked_layers(terms, mask, *, normal_only=False):
    if mask.shape != (len(terms), 24): raise ValueError("invalid layer mask")
    counts = mask.sum(1); keep = counts > 0
    chosen = terms[keep, :, :, 1:2] if normal_only else terms[keep]
    values = (chosen*mask[keep, None, :, None]).sum(2)/counts[keep, None, None]
    return keep, values.reshape((int(keep.sum()), 12 if normal_only else 36)), counts
