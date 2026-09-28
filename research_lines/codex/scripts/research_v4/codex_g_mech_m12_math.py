"""Score-blind local-history matching. No route values or outcome selection."""
from __future__ import annotations

import numpy as np
from research_v4.codex_g_mech_m10_math import PairMatcher

LENGTHS = (1, 2, 4, 8)
REASONS = ("matched", "no_common_structure", "tu_outside_caliper", "current_token_mismatch",
           "fewer_than_two_other_scenario_episodes", "no_distinct_scenario_pair", "mutual_tu_failed",
           "history_suffix_mismatch")
CANDIDATE_COLUMNS = ("suffix_eligible_looks", "suffix_eligible_episodes", "suffix_eligible_scenarios")


class HistoryPairMatcher(PairMatcher):
    def select(self, query, *, length):
        if length not in LENGTHS: raise ValueError("unregistered suffix length")
        f = self.f
        if not f.valid[query]: raise ValueError("cross-step query")
        counts = np.zeros(3, dtype=np.int64)
        c = self.index.get(f.key(query), np.empty(0, dtype=np.int64))
        c = c[f.episode[c] != f.episode[query]]
        if not len(c): return [], 1, counts
        c = c[np.abs(f.tu[c]-f.tu[query]) <= .25]
        if not len(c): return [], 2, counts
        c = c[f.tokens[c, -1] == f.tokens[query, -1]]
        if not len(c): return [], 3, counts
        c = c[(f.tokens[c, -length:] == f.tokens[query, -length:]).all(1)]
        if not len(c): return [], 7, counts
        c = c[self.scenarios[f.episode[c]] != self.scenarios[f.episode[query]]]
        counts[:] = len(c), len(np.unique(f.episode[c])), len(np.unique(self.scenarios[f.episode[c]]))
        ordered = sorted(c.tolist(), key=lambda d: (
            abs(float(f.tu[d]-f.tu[query])), abs(int(f.ends[d]-f.ends[query])),
            int(f.ends[d]), str(f.keys[f.episode[d]])))
        chosen, seen = [], set()
        for d in ordered:
            ep = int(f.episode[d])
            if ep not in seen: chosen.append(d); seen.add(ep)
        if len(chosen) < 2: return [], 4, counts
        has_scenario_pair = False
        for i, a in enumerate(chosen):
            for b in chosen[i+1:]:
                if self.scenarios[f.episode[a]] == self.scenarios[f.episode[b]]: continue
                has_scenario_pair = True
                if abs(float(f.tu[a]-f.tu[b])) <= .25: return [a, b], 0, counts
        return [], 6 if has_scenario_pair else 5, counts


def fixed_retained(f, queries, pairs, length):
    if length not in LENGTHS: raise ValueError("unregistered suffix length")
    keep = (pairs >= 0).all(1)
    loc = np.flatnonzero(keep)
    keep[loc] = (f.tokens[pairs[loc], -length:] == f.tokens[queries[loc], None, -length:]).all((1, 2))
    return keep


def record_lookup(records, query_count):
    lookup = np.full((4, query_count), -1, dtype=np.int64)
    for ri, row in enumerate(records):
        ci, qi = int(row[1]), int(row[2])
        if lookup[ci, qi] != -1: raise ValueError("duplicate phase-query record")
        lookup[ci, qi] = ri
    return lookup


def paired_values(values, lookup, query_indices, cell):
    """The same queries and episode weights, never a difference of unmatched means."""
    left, right = lookup[0, query_indices], lookup[cell, query_indices]
    if (left < 0).any() or (right < 0).any(): raise ValueError("paired query missing")
    return values[right]-values[left]
