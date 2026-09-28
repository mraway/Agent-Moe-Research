"""A02 channel-only, exact mean-routing neighbours. No data/label access."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import numpy as np
from research_v4 import codex_g_alg_a01_math as old

CELLS = ("U_pool", "W_pool")
K, QUERY_BLOCK, BANK_BLOCK = 3, 64, 2048


@dataclass
class Bank:
    mean_roots: tuple[np.ndarray, np.ndarray]
    scenarios: tuple[str, ...]
    scenario_index: np.ndarray
    keys: np.ndarray
    ends: np.ndarray

    def state(self):
        return {"mean_u": self.mean_roots[0], "mean_w": self.mean_roots[1],
                "scenarios": np.asarray(self.scenarios), "scenario_index": self.scenario_index,
                "keys": self.keys, "ends": self.ends}

    @classmethod
    def restore(cls, state):
        return cls((state["mean_u"], state["mean_w"]), tuple(map(str, state["scenarios"])),
                   state["scenario_index"], state["keys"], state["ends"])


def build_bank(records):
    records = sorted(records, key=lambda r: (r[0], r[1]))
    if not records or any(not len(r[2]) for r in records):
        raise ValueError("nonempty records required")
    if len({r[1] for r in records}) != len(records):
        raise ValueError("duplicate bank episode")
    scenarios = tuple(sorted({r[0] for r in records}))
    if len(scenarios) < K:
        raise ValueError("fewer than three donor scenarios")
    means, groups, keys, ends = [[], []], [], [], []
    for scenario, key, ep_ends, ps in records:
        ep_ends = np.asarray(ep_ends, dtype=np.int64)
        if np.any(np.diff(ep_ends) <= 0) or len(ps) != 2 or len(ps[0]) != len(ps[1]):
            raise ValueError("unaligned records")
        for rep in range(2):
            means[rep].append(old.features(ps[rep], ep_ends)[1])
        ends.append(ep_ends); keys.extend([key]*len(ep_ends))
        groups.extend([scenarios.index(scenario)]*len(ep_ends))
    return Bank(tuple(np.concatenate(r) for r in means), scenarios, np.asarray(groups, np.int64),
                np.asarray(keys), np.concatenate(ends))


def neighbour_scores(ps, ends, bank, scenario, *, query_block=QUERY_BLOCK, bank_block=BANK_BLOCK):
    """Same Hellinger kernel as A01 means; no token-ordered distance computation."""
    ends = np.asarray(ends, np.int64)
    if query_block <= 0 or bank_block <= 0:
        raise ValueError("positive blocks required")
    if len(bank.scenarios)-int(scenario in bank.scenarios) < K:
        raise ValueError("fewer than three scenarios after whole-scenario exclusion")
    roots = [old.features(p, ends)[1] for p in ps]
    scores = np.empty((len(ends), 2), np.float64)
    donors = np.empty((len(ends), 2, K), np.int64)
    distances = np.empty(donors.shape, np.float64)
    for lo in range(0, len(ends), query_block):
        hi = min(lo+query_block, len(ends))
        minima = np.full((hi-lo, 2, len(bank.scenarios)), np.inf, np.float32)
        rows = np.full(minima.shape, -1, np.int64)
        for b0 in range(0, len(bank.ends), bank_block):
            b1 = min(b0+bank_block, len(bank.ends))
            d = np.stack([old._h2_roots(roots[r][lo:hi], bank.mean_roots[r][b0:b1])
                          for r in range(2)], axis=1)
            for group in np.unique(bank.scenario_index[b0:b1]):
                if bank.scenarios[group] == scenario:
                    continue
                local = np.flatnonzero(bank.scenario_index[b0:b1] == group)
                block = d[:, :, local]; ix = block.argmin(-1)
                v = np.take_along_axis(block, ix[..., None], -1)[..., 0]
                win = v < minima[:, :, group]
                minima[:, :, group] = np.where(win, v, minima[:, :, group])
                rows[:, :, group] = np.where(win, b0+local[ix], rows[:, :, group])
        order = np.argsort(minima, axis=-1, kind="stable")[..., :K]
        ds = np.take_along_axis(minima, order, -1).astype(np.float64)
        rs = np.take_along_axis(rows, order, -1)
        if not np.isfinite(ds).all() or np.any(rs < 0):
            raise ValueError("missing independent donors")
        scores[lo:hi] = ds.mean(-1); donors[lo:hi] = rs; distances[lo:hi] = ds
    return scores, donors, distances


def support_audit(meta, grid):
    if any(r["variant"] not in ("clean", "benign_control", "benign_lexical") for r in meta.values()):
        raise ValueError("support audit is normal-only")
    banks, failures = {}, []
    for outer in range(3):
        fit = sorted(k for k, r in meta.items() if r["fold"] == (outer+1)%3 and r["filter_pass"] is True)
        queries = sorted(k for k, r in meta.items() if r["fold"] == outer or r["filter_pass"] is True)
        conditions = {}
        for tag in sorted({str(t) for k in queries for t in grid[k]["tags"]}):
            keys = [k for k in fit if tag in grid[k]["tags"]]
            counts = Counter()
            for k in keys:
                counts[meta[k]["scenario"]] += int(np.count_nonzero(np.asarray(grid[k]["tags"]) == tag))
            available = [len(counts)-int(meta[k]["scenario"] in counts) for k in queries if tag in grid[k]["tags"]]
            conditions[tag] = {"fit_keys": keys, "windows": sum(counts.values()), "scenarios": len(counts),
                               "scenario_window_counts": dict(sorted(counts.items())),
                               "minimum_scenarios_after_exclusion": min(available)}
            if min(available) < K:
                failures.append({"fold": outer, "tag": tag, "minimum": min(available)})
        banks[str(outer)] = {"fit_keys": fit, "conditions": conditions}
    return {"banks": banks, "unsupported_queries": failures, "normal_episodes": len(meta)}
