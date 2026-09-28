"""Score-blind fourth-node controls and shared-background routing contrasts."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import numpy as np
from research_v4 import codex_g_mech_m11_math as v11

LENGTHS = (1, 8)
GROUPS = ("silent", "engaged_only", "committed_no_execution", "over_refusal")
LEVELS = ("channel", "channel_family", "channel_family_tier")
REASONS = ("matched", "no_base_normal_pair", "no_structural_control", "scenario_conflict",
           "injection_channel_mismatch", "family_mismatch", "tier_mismatch", "tu_outside_caliper", "suffix_mismatch")
DISTANCES = ("U/TV", "W/TV", "P/TV", "W/JS", "P/JS")
METRICS = ("Aq", "Ac", "N", "Dq", "Dc", "B", "Qc")
BANDS = ("all", "early", "middle", "late")
COLUMNS = tuple(f"{d}/{b}/{m}" for d in DISTANCES for b in BANDS for m in METRICS)
LAYER_COLUMNS = tuple(f"{d}/layer{l}/{m}" for d in DISTANCES for l in range(24) for m in METRICS)
CONDITIONAL_COLUMNS = tuple(f"{d}/{m}" for d in DISTANCES for m in METRICS)
# All six edges, with c sharing exactly the same a/b normal reference as q.
EDGE_ROLES = ((0, 1), (0, 2), (1, 2), (3, 1), (3, 2), (0, 3))


@dataclass(frozen=True)
class ContextCategories:
    scenarios: np.ndarray
    channels: np.ndarray
    families: np.ndarray
    tiers: np.ndarray


def control_banks(f, metadata):
    banks, counts = {}, {}
    for group in GROUPS:
        eps = [i for i, key in enumerate(f.keys) if metadata[str(key)]["variant"] == "attack"
               and metadata[str(key)]["attack_bearing"] and metadata[str(key)]["x"] is None
               and metadata[str(key)]["trajectory_class"] == group]
        all_valid, selected = [], []
        for ei in eps:
            row = metadata[str(f.keys[ei])]; looks = np.flatnonzero((f.episode == ei) & f.valid)
            all_valid.extend(looks.tolist())
            if group in ("engaged_only", "committed_no_execution"):
                if row["e"] is None: raise ValueError("event-bearing control lacks E")
                looks = looks[(f.ends[looks] >= row["e"]) & (f.ends[looks] <= row["e"]+16)]
            selected.extend(looks.tolist())
        banks[group] = np.array(sorted(selected), dtype=np.int64)
        counts[group] = {"episodes": len(eps), "episode_keys": [str(f.keys[i]) for i in eps],
                         "episodes_with_valid_looks": len(np.unique(f.episode[all_valid])), "valid_looks": len(all_valid),
                         "episodes_with_stage_looks": len(np.unique(f.episode[selected])), "stage_looks": len(selected),
                         "stop_reasons": dict(Counter(str(metadata[str(f.keys[i])]["stop_reason"]) for i in eps))}
    return banks, counts


class ControlMatcher:
    """Labels set bank membership outside this API; selection sees no routes/outcomes."""
    def __init__(self, features, bank, categories):
        self.f, self.c = features, categories
        idx = defaultdict(list)
        for d in bank:
            if features.valid[d]: idx[features.key(d)].append(int(d))
        self.index = {k: np.array(v, dtype=np.int64) for k, v in idx.items()}

    def select(self, q, normals, *, length, level):
        if length not in LENGTHS or level not in range(3): raise ValueError("unregistered control cell")
        f, cat = self.f, self.c; counts = np.zeros(3, dtype=np.int64)
        normals = np.asarray(normals, dtype=np.int64)
        if normals.shape != (2,): raise ValueError("exactly two frozen normals required")
        if (normals < 0).any(): return -1, 1, counts
        if not f.valid[q]: raise ValueError("cross-step query")
        candidates = self.index.get(f.key(q), np.empty(0, dtype=np.int64))
        if not len(candidates): return -1, 2, counts
        nodes = np.r_[q, normals]; ep = int(f.episode[q])
        candidates = candidates[~np.isin(cat.scenarios[f.episode[candidates]], cat.scenarios[f.episode[nodes]])]
        if not len(candidates): return -1, 3, counts
        candidates = candidates[cat.channels[f.episode[candidates]] == cat.channels[ep]]
        if not len(candidates): return -1, 4, counts
        if level >= 1:
            candidates = candidates[cat.families[f.episode[candidates]] == cat.families[ep]]
            if not len(candidates): return -1, 5, counts
        if level >= 2:
            candidates = candidates[cat.tiers[f.episode[candidates]] == cat.tiers[ep]]
            if not len(candidates): return -1, 6, counts
        candidates = candidates[(abs(f.tu[candidates, None]-f.tu[nodes]) <= .25).all(1)]
        if not len(candidates): return -1, 7, counts
        candidates = candidates[(f.tokens[candidates, -length:] == f.tokens[q, -length:]).all(1)]
        if not len(candidates): return -1, 8, counts
        counts[:] = len(candidates), len(np.unique(f.episode[candidates])), len(np.unique(cat.scenarios[f.episode[candidates]]))
        selected = min(candidates.tolist(), key=lambda d: (abs(float(f.tu[d]-f.tu[q])), abs(int(f.ends[d]-f.ends[q])),
                                                          int(f.ends[d]), str(f.keys[f.episode[d]])))
        return selected, 0, counts


def records_and_edges(graph):
    rows = []
    for li in range(2):
        for gi in range(4):
            for ci in range(3):
                for qi in np.flatnonzero(graph["reasons"][li, gi, ci] == 0):
                    rows.append((li, gi, ci, int(qi), int(graph["queries"][qi]),
                                 *map(int, graph["normals"][li, qi]), int(graph["controls"][li, gi, ci, qi])))
    records = np.array(rows, dtype=np.int64).reshape((-1, 8))
    edges = np.array(sorted({tuple(sorted((int(row[4+i]), int(row[4+j])))) for row in records for i, j in EDGE_ROLES}), dtype=np.int64).reshape((-1, 2))
    lookup = {tuple(e): i for i, e in enumerate(edges)}
    indices = np.array([[lookup[tuple(sorted((row[4+i], row[4+j])))] for i, j in EDGE_ROLES] for row in records], dtype=np.int64).reshape((-1, 6))
    return records, edges, indices


def distances(vectors, nodes):
    a, b = vectors[nodes[:, 0]], vectors[nodes[:, 1]]
    tv = abs(a-b).sum(-1)/2; js = v11.js_divergence(a, b)
    return np.stack([tv[:, 0], tv[:, 1], tv[:, 2], js[:, 1], js[:, 2]], axis=1)


def measures(d, indices):
    aq = (d[indices[:, 0]]+d[indices[:, 1]])/2; ac = (d[indices[:, 3]]+d[indices[:, 4]])/2
    normal = d[indices[:, 2]]
    return np.stack([aq, ac, normal, aq-normal, ac-normal, aq-ac, d[indices[:, 5]]], axis=-1)


def aggregate(terms):
    if terms.shape[1:] != (5, 24, 7): raise ValueError("expected [R,5,24,7]")
    main = np.stack([terms.mean(2), *(terms[:, :, s:s+8].mean(2) for s in (0, 8, 16))], axis=2)
    return main.reshape((-1, len(COLUMNS))), terms.reshape((-1, len(LAYER_COLUMNS)))


def balance(f, nodes):
    return np.column_stack([abs(v[nodes[:, i]]-v[nodes[:, j]]) for v in (f.tu, f.ends) for i, j in EDGE_ROLES])


def masked_values(terms, mask):
    if mask.shape != (len(terms), 24): raise ValueError("unaligned four-node support mask")
    counts = mask.sum(1); keep = counts > 0
    v = (terms[keep]*mask[keep, None, :, None]).sum(2)/counts[keep, None, None]
    return keep, v.reshape((-1, len(CONDITIONAL_COLUMNS))), counts
