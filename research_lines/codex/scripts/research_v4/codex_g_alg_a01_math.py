"""A01 exact, causal Hellinger neighbours; no labels, calibration, or file access."""
from __future__ import annotations

from dataclasses import dataclass
from collections import Counter

import numpy as np

CELLS = ("U_mean", "W_mean", "U_ordered", "W_ordered")
WIDTH, K, LAYERS, EXPERTS = 8, 3, 24, 32
QUERY_BLOCK, BANK_BLOCK = 64, 2048
DISTANCE_TOLERANCE = 2e-6


def representations(ids, logits):
    """[L,T,4] actual selected IDs and [L,T,E] logits -> two [T,L,E] simplexes."""
    ids, logits = np.asarray(ids), np.asarray(logits, dtype=np.float32)
    if ids.ndim != 3 or ids.shape[0] != LAYERS or ids.shape[2] != 4:
        raise ValueError("expected actual IDs [24,T,4]")
    if not np.issubdtype(ids.dtype, np.integer) or np.any((ids < 0) | (ids >= EXPERTS)):
        raise ValueError("invalid expert IDs")
    if np.any(np.diff(np.sort(ids, axis=-1), axis=-1) == 0):
        raise ValueError("selected experts must be distinct")
    if logits.shape != (*ids.shape[:2], EXPERTS) or not np.isfinite(logits).all():
        raise ValueError("invalid logits")
    selected = np.take_along_axis(logits, ids, axis=-1)
    selected = np.exp(selected - selected.max(axis=-1, keepdims=True))
    selected /= selected.sum(axis=-1, keepdims=True)
    uniform = np.zeros_like(logits)
    weighted = np.zeros_like(logits)
    np.put_along_axis(uniform, ids, .25, axis=-1)
    np.put_along_axis(weighted, ids, selected, axis=-1)
    return tuple(np.ascontiguousarray(p.transpose(1, 0, 2)) for p in (uniform, weighted))


def valid_ends(ends, tags, step_ids, width=WIDTH):
    ends = np.asarray(ends)
    tags, step_ids = np.asarray(tags), np.asarray(step_ids)
    if tags.ndim != 1 or step_ids.shape != tags.shape:
        raise ValueError("token tags and steps disagree")
    if ends.ndim != 1 or not np.issubdtype(ends.dtype, np.integer):
        raise ValueError("integer 1D endpoints required")
    if np.any(np.diff(ends) <= 0) or np.any(ends < width-1) or np.any(ends >= len(tags)):
        raise ValueError("invalid endpoints")
    for lag in range(width):
        if np.any(tags[ends-lag] != tags[ends]) or np.any(step_ids[ends-lag] != step_ids[ends]):
            raise ValueError("window crosses channel or generation step")
    return ends.astype(np.int64, copy=False)


def features(p, ends):
    """Square roots / sqrt(L) encode the layer-average Hellinger geometry."""
    p = np.asarray(p, dtype=np.float32)
    ends = np.asarray(ends, dtype=np.int64)
    if p.ndim != 3 or p.shape[1:] != (LAYERS, EXPERTS):
        raise ValueError("expected probabilities [T,24,32]")
    if not np.isfinite(p).all() or np.any(p < 0):
        raise ValueError("probabilities must be nonnegative and finite")
    if not np.allclose(p.sum(-1), 1, rtol=0, atol=1e-6):
        raise ValueError("not layerwise probability simplexes")
    if np.any(ends < WIDTH-1) or np.any(ends >= len(p)):
        raise ValueError("invalid endpoints")
    # Fixed eight-add reduction; never cumulative subtraction dependent on distant prefix.
    means = np.zeros((len(ends), LAYERS, EXPERTS), dtype=np.float32)
    for lag in range(WIDTH):
        means += p[ends-(WIDTH-1)+lag] / WIDTH
    return (np.sqrt(p / LAYERS).reshape(len(p), -1),
            np.sqrt(means / LAYERS).reshape(len(ends), LAYERS*EXPERTS))


@dataclass
class Bank:
    token_roots: tuple[np.ndarray, np.ndarray]
    mean_roots: tuple[np.ndarray, np.ndarray]
    token_ends: np.ndarray
    scenarios: tuple[str, ...]
    scenario_index: np.ndarray
    keys: np.ndarray
    ends: np.ndarray

    def state(self):
        return {"token_u": self.token_roots[0], "token_w": self.token_roots[1],
                "mean_u": self.mean_roots[0], "mean_w": self.mean_roots[1],
                "token_ends": self.token_ends, "scenarios": np.asarray(self.scenarios),
                "scenario_index": self.scenario_index, "keys": self.keys, "ends": self.ends}

    @classmethod
    def restore(cls, state):
        return cls((state["token_u"], state["token_w"]), (state["mean_u"], state["mean_w"]),
                   state["token_ends"], tuple(map(str, state["scenarios"])),
                   state["scenario_index"], state["keys"], state["ends"])


def build_bank(records):
    """Records: (scenario, key, selected ends, (U probabilities, W probabilities))."""
    records = sorted(records, key=lambda r: (r[0], r[1]))
    if not records or any(not len(r[2]) for r in records):
        raise ValueError("bank must contain nonempty window records")
    if len({r[1] for r in records}) != len(records):
        raise ValueError("duplicate bank episode")
    scenarios = tuple(sorted({r[0] for r in records}))
    if len(scenarios) < K:
        raise ValueError("fewer than three donor scenarios")
    token_blocks, mean_blocks = [[], []], [[], []]
    token_ends, groups, keys, ends = [], [], [], []
    offset = 0
    for scenario, key, ep_ends, ps in records:
        ep_ends = np.asarray(ep_ends, dtype=np.int64)
        if np.any(np.diff(ep_ends) <= 0) or len(ps[0]) != len(ps[1]):
            raise ValueError("unaligned bank inputs")
        for i, p in enumerate(ps):
            token, mean = features(p, ep_ends)
            token_blocks[i].append(token); mean_blocks[i].append(mean)
        token_ends.append(ep_ends+offset); ends.append(ep_ends)
        groups.extend([scenarios.index(scenario)] * len(ep_ends))
        keys.extend([key] * len(ep_ends)); offset += len(ps[0])
    return Bank(tuple(np.concatenate(b) for b in token_blocks),
                tuple(np.concatenate(b) for b in mean_blocks), np.concatenate(token_ends),
                scenarios, np.asarray(groups, dtype=np.int64), np.asarray(keys), np.concatenate(ends))


def _h2_roots(a, b):
    # Own norms preserve the direct formula even with float32 simplex roundoff.
    result = .5 * (np.einsum("ij,ij->i", a, a)[:, None] +
                   np.einsum("ij,ij->i", b, b)[None, :]) - a @ b.T
    if not np.isfinite(result).all() or np.any(result < -DISTANCE_TOLERANCE):
        raise ValueError("invalid Hellinger distance")
    return np.maximum(result, 0)


def neighbour_scores(ps, ends, bank, scenario, *, query_block=QUERY_BLOCK, bank_block=BANK_BLOCK):
    """Exact scenario-balanced scores, all four cells, plus reproducible donor indices."""
    ends = np.asarray(ends, dtype=np.int64)
    if query_block <= 0 or bank_block <= 0:
        raise ValueError("positive block sizes required")
    if len(bank.scenarios) - int(scenario in bank.scenarios) < K:
        raise ValueError("fewer than three scenarios after entire-scenario exclusion")
    prepared = [features(p, ends) for p in ps]
    scores = np.empty((len(ends), len(CELLS)), dtype=np.float64)
    donor_rows = np.empty((len(ends), len(CELLS), K), dtype=np.int64)
    donor_distances = np.empty_like(donor_rows, dtype=np.float64)
    for lo in range(0, len(ends), query_block):
        hi = min(lo+query_block, len(ends)); qe = ends[lo:hi]
        qt = np.unique((qe[:, None] - np.arange(WIDTH)).ravel())
        qmap = [np.searchsorted(qt, qe-(WIDTH-1)+lag) for lag in range(WIDTH)]
        minima = np.full((len(qe), len(CELLS), len(bank.scenarios)), np.inf, dtype=np.float32)
        rows = np.full(minima.shape, -1, dtype=np.int64)
        for b0 in range(0, len(bank.ends), bank_block):
            b1 = min(b0+bank_block, len(bank.ends)); be = bank.token_ends[b0:b1]
            bt = np.unique((be[:, None] - np.arange(WIDTH)).ravel())
            bmap = [np.searchsorted(bt, be-(WIDTH-1)+lag) for lag in range(WIDTH)]
            distances = np.empty((len(qe), len(CELLS), len(be)), dtype=np.float32)
            for rep in range(2):
                distances[:, rep, :] = _h2_roots(prepared[rep][1][lo:hi], bank.mean_roots[rep][b0:b1])
                token_d = _h2_roots(prepared[rep][0][qt], bank.token_roots[rep][bt])
                ordered = np.zeros((len(qe), len(be)), dtype=np.float32)
                for qi, bi in zip(qmap, bmap):
                    ordered += token_d[qi[:, None], bi[None, :]] / WIDTH
                distances[:, rep+2, :] = ordered
            for group in np.unique(bank.scenario_index[b0:b1]):
                if bank.scenarios[group] == scenario:
                    continue
                local = np.flatnonzero(bank.scenario_index[b0:b1] == group)
                d = distances[:, :, local]
                ix = d.argmin(axis=-1)
                values = np.take_along_axis(d, ix[..., None], axis=-1)[..., 0]
                wins = values < minima[:, :, group]  # equality retains earlier key/end
                minima[:, :, group] = np.where(wins, values, minima[:, :, group])
                rows[:, :, group] = np.where(wins, b0+local[ix], rows[:, :, group])
        order = np.argsort(minima, axis=-1, kind="stable")[..., :K]
        d = np.take_along_axis(minima, order, axis=-1).astype(np.float64)
        r = np.take_along_axis(rows, order, axis=-1)
        if not np.isfinite(d).all() or np.any(r < 0):
            raise ValueError("missing finite independent donors")
        scores[lo:hi] = d.mean(-1); donor_rows[lo:hi] = r; donor_distances[lo:hi] = d
    return scores, donor_rows, donor_distances


def support_audit(metadata, streams):
    """Inspect ALL normal fit/cal/eval requirements without routing values or labels."""
    conditions = {k: sorted({(str(tag), int(metadata[k]["episode_index"])) for tag in s["tags"]})
                  for k, s in streams.items()}
    banks, failures = {}, []
    for outer in range(3):
        fit_keys = sorted(k for k, r in metadata.items() if r["fold"] == (outer+1)%3 and r["filter_pass"] is True)
        if any(metadata[k]["variant"] not in ("clean", "benign_control", "benign_lexical") for k in fit_keys):
            raise ValueError("non-normal episode in fit bank")
        query_keys = sorted(k for k, r in metadata.items() if r["fold"] == outer or r["filter_pass"] is True)
        by_condition = {}
        for condition in sorted({c for k in query_keys for c in conditions[k]}):
            keys = [k for k in fit_keys if condition in conditions[k]]
            counts = Counter()
            for key in keys:
                counts[metadata[key]["scenario"]] += sum(str(t) == condition[0] for t in streams[key]["tags"])
            name = f"{condition[0]}/ep{condition[1]}"
            by_condition[name] = {"channel": condition[0], "episode_index": condition[1],
                                  "fit_keys": keys, "scenario_window_counts": dict(sorted(counts.items())),
                                  "scenarios": len(counts), "windows": sum(counts.values())}
            for key in query_keys:
                if condition not in conditions[key]:
                    continue
                excluded = int(metadata[key]["scenario"] in counts)
                if len(counts)-excluded < K:
                    failures.append({"outer_fold": outer, "condition": name, "query_key": key,
                                     "role": "eval" if metadata[key]["fold"] == outer else
                                             "fit" if metadata[key]["fold"] == (outer+1)%3 else "cal",
                                     "available_scenarios_after_exclusion": len(counts)-excluded})
        banks[str(outer)] = {"fit_fold": (outer+1)%3, "cal_fold": (outer+2)%3,
                             "fit_keys": fit_keys, "conditions": by_condition}
    return {"status": "coverage_failed" if failures else "coverage_passed", "banks": banks,
            "unsupported_queries": failures, "normal_episodes": len(metadata)}
