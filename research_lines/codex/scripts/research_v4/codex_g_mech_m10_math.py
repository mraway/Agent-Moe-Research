"""Score-blind same-token triplets and descriptive normal-variability contrasts."""
from __future__ import annotations

from collections import defaultdict
import numpy as np

CELLS = (("filtered_distinct", "normal_filtered", True, .25),
         ("filtered_shared_allowed", "normal_filtered", False, .25),
         ("all_distinct", "normal_all", True, .25),
         ("filtered_distinct_tight", "normal_filtered", True, .10))
REASONS = ("matched", "no_common_structure", "tu_outside_caliper", "current_token_mismatch",
           "fewer_than_two_other_scenario_episodes", "no_distinct_scenario_pair", "mutual_tu_failed")
SCOPES = ("total", "early", "middle", "late")
METRICS = ("query", "normal_mean", "attack_normal_distance", "normal_normal_distance",
           "distance_excess", "signed_excess", "above_both")
COLUMNS = tuple(f"{s}/{b}/{m}" for s in ("S", "CW") for b in SCOPES for m in METRICS)
LAYER_METRICS = METRICS[2:6]
LAYER_COLUMNS = tuple(f"{s}/layer{l}/{m}" for s in ("S", "CW") for l in range(24) for m in LAYER_METRICS)
TIE_TOL = 1e-9


class PairMatcher:
    """No route, label, episode length or future content is accepted by this API."""
    def __init__(self, features, bank, scenarios):
        self.f, self.scenarios = features, np.asarray(scenarios)
        index = defaultdict(list)
        for d in bank:
            if features.valid[d]: index[features.key(d)].append(int(d))
        self.index = {k: np.array(v, dtype=np.int64) for k, v in index.items()}

    def select(self, query, *, distinct=True, caliper=.25):
        f = self.f
        if not f.valid[query]: raise ValueError("cross-step query")
        c = self.index.get(f.key(query), np.empty(0, dtype=np.int64))
        c = c[f.episode[c] != f.episode[query]]
        if not len(c): return [], 1
        c = c[np.abs(f.tu[c]-f.tu[query]) <= caliper]
        if not len(c): return [], 2
        c = c[f.tokens[c, -1] == f.tokens[query, -1]]
        if not len(c): return [], 3
        c = c[self.scenarios[f.episode[c]] != self.scenarios[f.episode[query]]]
        ordered = sorted(c.tolist(), key=lambda d: (
            abs(float(f.tu[d]-f.tu[query])), abs(int(f.ends[d]-f.ends[query])),
            int(f.ends[d]), str(f.keys[f.episode[d]])))
        chosen, seen = [], set()
        for d in ordered:
            ep = int(f.episode[d])
            if ep not in seen: chosen.append(d); seen.add(ep)
        if len(chosen) < 2: return [], 4
        has_scenario_pair = False
        for i, a in enumerate(chosen):
            for b in chosen[i+1:]:
                if distinct and self.scenarios[f.episode[a]] == self.scenarios[f.episode[b]]: continue
                has_scenario_pair = True
                if abs(float(f.tu[a]-f.tu[b])) <= caliper: return [a, b], 0
        return [], 6 if has_scenario_pair else 5


def contrasts(query, a, b):
    """Same-dimensional pairwise distances; never compare a centroid distance to N."""
    query, a, b = (np.asarray(x, dtype=np.float64) for x in (query, a, b))
    if query.shape != a.shape or a.shape != b.shape: raise ValueError("unaligned triplet")
    if not all(np.isfinite(x).all() for x in (query, a, b)): raise ValueError("nonfinite triplet")
    center = (a+b)/2
    attack_distance = (np.abs(query-a)+np.abs(query-b))/2
    normal_distance = np.abs(a-b)
    return np.stack([query, center, attack_distance, normal_distance, attack_distance-normal_distance,
                     query-center, (query-np.maximum(a, b) > TIE_TOL).astype(float)], axis=-1)


def current_features(current):
    if current.ndim != 3 or current.shape[1:] != (24, 2): raise ValueError("expected [N,24,2]")
    return np.column_stack([current[:, :, s].sum(1) if band == "total"
                            else current[:, (SCOPES.index(band)-1)*8:SCOPES.index(band)*8, s].sum(1)
                            for s in range(2) for band in SCOPES])


def triplet_values(current, lookup, queries, pairs):
    if pairs.shape != (len(queries), 2) or (pairs < 0).any(): raise ValueError("two donors required")
    features = current_features(current)
    indices = [lookup[queries], lookup[pairs[:, 0]], lookup[pairs[:, 1]]]
    if any((idx < 0).any() for idx in indices): raise ValueError("missing measured look")
    values = contrasts(*(features[idx] for idx in indices)).reshape((len(queries), len(COLUMNS)))
    layers = current.transpose(0, 2, 1).reshape((-1, 48))
    profiles = contrasts(*(layers[idx] for idx in indices))[:, :, 2:6].reshape((len(queries), len(LAYER_COLUMNS)))
    return values, profiles


def edge_distances(f, queries, pairs):
    """q-a, q-b, a-b for TU followed by causal generated-endpoint distances."""
    nodes = [queries, pairs[:, 0], pairs[:, 1]]
    return np.column_stack([np.abs(v[nodes[i]]-v[nodes[j]])
                            for v in (f.tu, f.ends) for i, j in ((0, 1), (0, 2), (1, 2))])
