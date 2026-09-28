"""M8 fixed lexical matching. Selection API cannot see routing scores/outcomes."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np

PHASES = ("E_at", "X_pre", "X_at")
POOLS = ("normal_filtered", "normal_all", "over_refusal", "engaged_only",
         "committed_no_execution", "legitimate_refusal")
VERSIONS = ("L0", "L1", "L8", "L1_tight")
COLUMNS = ("raw_S", "raw_CW", "raw_TU", "z_S", "z_CW", "z_TU")
TAGS = ("analysis", "commentary", "final")
REASONS = ("matched", "no_common_structure", "tu_outside_caliper",
           "current_token_mismatch", "entire_window_mismatch")
BOOTSTRAPS, SEED = 2000, 802608


def window_geometry(ends, tokens, step_lengths):
    """Use only completed step boundaries before each endpoint; exclude crossings.

    The last supplied step can be a partial prefix. Extending that step or adding
    future steps does not change any previous endpoint's geometry.
    """
    ends = np.asarray(ends, dtype=np.int64)
    tokens = np.asarray(tokens, dtype=np.int64)
    lengths = np.asarray(step_lengths, dtype=np.int64)
    if np.any(lengths < 0) or lengths.sum() != len(tokens):
        raise ValueError("step lengths must partition the generated token axis")
    if np.any(ends < 7) or np.any(ends >= len(tokens)) or np.any(np.diff(ends) <= 0):
        raise ValueError("invalid full-eight-token endpoint grid")
    boundaries = np.cumsum(lengths)
    step = np.searchsorted(boundaries, ends, side="right")
    first_step = np.searchsorted(boundaries, ends-7, side="right")
    windows = (np.lib.stride_tricks.sliding_window_view(tokens, 8)[ends-7].copy()
               if len(ends) else np.empty((0, 8), dtype=np.int64))
    return step, step == first_step, windows


@dataclass(frozen=True)
class MatchFeatures:
    keys: np.ndarray
    episode: np.ndarray
    ends: np.ndarray
    structure: np.ndarray  # fold, tag, episode_index, step, ordinal//32, end//64
    tokens: np.ndarray    # causal eight-token sequence
    tu: np.ndarray
    valid: np.ndarray

    def key(self, i):
        return tuple(int(x) for x in self.structure[i])


class Matcher:
    def __init__(self, features, bank):
        self.f = features
        self.index = defaultdict(list)
        for i in bank:
            if features.valid[i]:
                self.index[features.key(i)].append(int(i))
        self.index = {k: np.asarray(v, dtype=np.int64) for k, v in self.index.items()}

    def select(self, query, version):
        if version not in VERSIONS:
            raise ValueError(version)
        f = self.f
        if not f.valid[query]:
            raise ValueError("cross-step query is not eligible")
        c = self.index.get(f.key(query), np.empty(0, dtype=np.int64))
        c = c[f.episode[c] != f.episode[query]]
        if not len(c): return [], 1
        caliper = .10 if version == "L1_tight" else .25
        c = c[np.abs(f.tu[c]-f.tu[query]) <= caliper]
        if not len(c): return [], 2
        if version != "L0":
            c = c[f.tokens[c, -1] == f.tokens[query, -1]]
            if not len(c): return [], 3
        if version == "L8":
            c = c[np.all(f.tokens[c] == f.tokens[query], axis=1)]
            if not len(c): return [], 4
        # This sort sees only TU distance, causal endpoints, and episode keys.
        ordered = sorted(c.tolist(), key=lambda d: (
            abs(float(f.tu[d]-f.tu[query])), abs(int(f.ends[d]-f.ends[query])),
            int(f.ends[d]), str(f.keys[f.episode[d]])))
        seen, chosen = set(), []
        for d in ordered:
            ep = int(f.episode[d])
            if ep not in seen:
                seen.add(ep); chosen.append(d)
            if len(chosen) == 3: break
        return chosen, 0


def pool_episodes(metadata):
    normal = {k for k, r in metadata.items() if r["variant"] in ("clean", "benign_control", "benign_lexical")}
    attack = {k for k, r in metadata.items() if r["variant"] == "attack" and r["attack_bearing"] and r["x"] is None}
    return {"normal_all": normal,
            "normal_filtered": {k for k in normal if metadata[k]["filter_pass"] is True},
            **{n: {k for k in attack if metadata[k]["trajectory_class"] == n}
               for n in ("over_refusal", "engaged_only", "committed_no_execution")},
            "legitimate_refusal": {k for k, r in metadata.items() if r["variant"] == "legitimate_refusal"}}


def phase_queries(f, metadata):
    positives = {k for k, r in metadata.items() if r["variant"] == "attack" and r["x"] is not None}
    out = {n: [] for n in PHASES}
    for i, k in enumerate(f.keys):
        if k not in positives: continue
        r = metadata[str(k)]; rows = np.flatnonzero(f.episode == i)
        ranges = ((r["e"], r["e"]+16), (r["x"]-16, r["x"]-1), (r["x"], r["x"]+16))
        for n, (lo, hi) in zip(PHASES, ranges, strict=True):
            out[n].extend(rows[(f.ends[rows] >= lo) & (f.ends[rows] <= hi)].tolist())
    return sorted(positives), {k: np.array(v, dtype=np.int64) for k, v in out.items()}


def episode_means(episodes, values):
    episodes = np.asarray(episodes, dtype=np.int64)
    values = np.asarray(values, dtype=np.float64)
    unique, inverse, counts = np.unique(episodes, return_inverse=True, return_counts=True)
    sums = np.zeros((len(unique), values.shape[1]), dtype=np.float64)
    np.add.at(sums, inverse, values)
    return unique, sums/counts[:, None], counts


def clustered_ci(values, labels):
    groups = sorted(set(labels))
    counts = np.array([labels.count(k) for k in groups])
    sums = np.array([values[np.array([s == k for s in labels])].sum(0) for k in groups])
    draws = np.random.default_rng(SEED).integers(0, len(groups), size=(BOOTSTRAPS, len(groups)))
    means = sums[draws].sum(1)/counts[draws].sum(1)[:, None]
    return {"cluster_count": len(groups), "cluster_sizes": dict(zip(groups, counts.tolist(), strict=True)),
            "mean_ci95": np.quantile(means, [.025, .975], axis=0).T.tolist(),
            "replicates": BOOTSTRAPS, "seed": SEED,
            "degenerate_one_cluster": len(groups) == 1}


def effect_summary(f, metadata, queries, values):
    if not len(queries): return {"episodes": 0, "looks": 0, "mean": None, "episode_values": []}
    unique, means, counts = episode_means(f.episode[queries], values)
    rows = [metadata[str(f.keys[i])] for i in unique]
    labels = [str(r["family"]) for r in rows]
    return {"episodes": len(unique), "looks": len(queries), "mean": means.mean(0).tolist(),
            "median": np.median(means, axis=0).tolist(), "positive_fraction": (means > 0).mean(0).tolist(),
            "family": clustered_ci(means, labels),
            "family_tier": clustered_ci(means, [str(r["family"])+"|"+str(r["tier"]) for r in rows]),
            "episode_values": [{"key": str(f.keys[i]), "looks": int(n), "value": m.tolist()}
                               for i, n, m in zip(unique, counts, means, strict=True)]}


def plain_mean(f, indices, values):
    if not len(indices): return None
    return episode_means(f.episode[indices], values)[1].mean(0).tolist()


def distribution(f, metadata, indices):
    if not len(indices): return {"episodes": 0, "looks": 0}
    eps = np.unique(f.episode[indices])
    result = {"episodes": len(eps), "looks": len(indices)}
    for field in ("family", "tier", "domain_group", "injection_channel"):
        result[field+"_episodes"] = dict(sorted(Counter(str(metadata[str(f.keys[i])][field]) for i in eps).items()))
    for j, name in enumerate(("fold", "tag", "episode_index", "step", "ordinal_bin", "end_bin")):
        result[name+"_looks"] = dict(sorted(Counter(str(int(x)) for x in f.structure[indices, j]).items()))
    return result


def subset_summary(f, metadata, queries, donors, effects, values):
    if not len(queries):
        return {"effect": effect_summary(f, metadata, queries, effects), "query_distribution": distribution(f, metadata, queries),
                "distinct_donor_episodes": 0, "distinct_donor_scenarios": 0, "max_donor_reuse": 0,
                "query_mean": None, "donor_mean": None, "balance": None}
    used = donors[donors >= 0]
    counts = (donors >= 0).sum(1)
    donor_values = np.array([values[d[d >= 0]].mean(0) for d in donors])
    distances, endpoint_distances = [], []
    for q, ds in zip(queries, donors, strict=True):
        ds = ds[ds >= 0]
        distances.append(np.abs(f.tu[ds]-f.tu[q]).mean())
        endpoint_distances.append(np.abs(f.ends[ds]-f.ends[q]).mean())
    balance = np.column_stack([distances, endpoint_distances])
    reuse = Counter(int(e) for e in f.episode[used])
    weight = defaultdict(float)
    ep_counts = Counter(int(e) for e in f.episode[queries])
    for q, ds, n in zip(queries, donors, counts, strict=True):
        for d in ds[ds >= 0]:
            weight[int(f.episode[d])] += 1/(len(ep_counts)*ep_counts[int(f.episode[q])]*int(n))
    return {"effect": effect_summary(f, metadata, queries, effects),
            "query_mean": plain_mean(f, queries, values[queries]),
            "donor_mean": plain_mean(f, queries, donor_values),
            "query_distribution": distribution(f, metadata, queries),
            "donor_distribution": distribution(f, metadata, used),
            "distinct_donor_episodes": len(reuse),
            "distinct_donor_scenarios": len({metadata[str(f.keys[e])]["scenario"] for e in reuse}),
            "max_donor_reuse": max(reuse.values()),
            "donor_episode_query_window_counts": {str(f.keys[e]): n for e, n in sorted(reuse.items())},
            "donor_effective_weight": {str(f.keys[e]): w for e, w in sorted(weight.items())},
            "max_donor_effective_weight": max(weight.values()),
            "balance": {"columns": ["absolute_TU_distance", "absolute_endpoint_distance"],
                        "episode_equal_mean": plain_mean(f, queries, balance),
                        "edge_max_absolute_TU_distance": max(float(abs(f.tu[d]-f.tu[q])) for q, ds in zip(queries, donors, strict=True) for d in ds if d >= 0),
                        "edge_max_absolute_endpoint_distance": max(int(abs(f.ends[d]-f.ends[q])) for q, ds in zip(queries, donors, strict=True) for d in ds if d >= 0)}}


def cell_summary(f, metadata, all_queries, queries, donors, effects, reasons, values, candidates):
    count = (donors >= 0).sum(1)
    eligible_eps = len(np.unique(f.episode[queries]))
    result = {"candidate_episodes": candidates, "episodes_with_original_query_looks": len(np.unique(f.episode[all_queries])),
              "original_query_looks": len(all_queries), "cross_step_query_looks": int((~f.valid[all_queries]).sum()),
              "cross_step_query_distribution": distribution(f, metadata, all_queries[~f.valid[all_queries]]),
              "eligible_query_episodes": eligible_eps, "eligible_query_looks": len(queries),
              "failure_looks": dict(Counter(REASONS[int(r)] for r in reasons)),
              "all_original_query_mean": plain_mean(f, all_queries, values[all_queries]),
              "all_eligible_query_mean": plain_mean(f, queries, values[queries]),
              "all_eligible_query_distribution": distribution(f, metadata, queries)}
    for name, minimum in (("at_least_one", 1), ("at_least_three", 3)):
        mask = count >= minimum
        q = queries[mask]
        n = len(np.unique(f.episode[q]))
        result[name] = {"matched_episodes": n, "matched_looks": len(q),
                        "episode_fraction_of_candidates": n/candidates if candidates else None,
                        "episode_fraction_of_query_episodes": n/eligible_eps if eligible_eps else None,
                        "look_fraction_of_eligible": len(q)/len(queries) if len(queries) else None,
                        **subset_summary(f, metadata, q, donors[mask], effects[mask], values)}
    return result
