"""Causal, label-free single-expert history controls for the fixed M5 study."""
from __future__ import annotations

import numpy as np

from research_v4.codex_g_m4_math import TAGS, segment_ids, tensors

LEVELS = ("B", "C", "H", "HP")
FIELDS = ("p", "conditional_p", "log_p_over_p4", "m", "count8", "streak8", "switches7")
PATTERNS = np.arange(256, dtype=np.int64)
BITS = (PATTERNS[:, None] >> np.arange(8)) & 1
COUNTS = BITS.sum(1)
STREAKS = np.cumprod(BITS, axis=1).sum(1)
SWITCHES = (BITS[:, 1:] != BITS[:, :-1]).sum(1)


def history_keys(coordinate, channel, history, episode_index, bucket):
    """Injective integer encoding, not hash bucketing or a similarity metric."""
    if not 0 <= episode_index < 16 or (len(bucket) and (bucket.min() < 0 or bucket.max() >= 64)):
        raise ValueError("unexpected episode/position index outside lossless encoding")
    base = coordinate * 3 + channel
    b = base * 2 + (history & 1)
    h = base * 256 + history
    return {"B": b, "C": b * 9 + COUNTS[history], "H": h,
            "HP": (h * 16 + episode_index) * 64 + bucket}


def causal_observations(ids, probability, tags, step_starts, rare, ends, ordinals, episode_index):
    p, mask, indices = tensors(ids, probability)
    p, mask, indices = p.numpy(), mask.numpy(), indices.numpy()
    ends = np.asarray(ends, dtype=np.int64)
    ordinals = np.asarray(ordinals, dtype=np.int64)
    if len(ends) != len(ordinals) or len(ends) > 352 or not (np.diff(ends) > 0).all():
        raise ValueError("invalid original look grid")
    runs = segment_ids(tags, step_starts)
    valid = ends >= 7
    valid &= runs[ends] == runs[np.maximum(0, ends - 7)]
    valid &= np.array([tags[t] in TAGS for t in ends], dtype=bool)
    ee, oo = ends[valid], ordinals[valid]
    ll, experts = np.nonzero(rare)
    count = len(ll)
    history = np.zeros((len(ee), count), dtype=np.int64)
    for lag in range(8):
        history |= mask[ee[:, None] - lag, ll, experts].astype(np.int64) << lag
    mass = (p * mask).sum(-1)
    smallest = np.take_along_axis(p, indices, axis=-1).min(-1)
    pe = p[ee[:, None], ll, experts]
    mm = mass[ee[:, None], ll]
    pp4 = smallest[ee[:, None], ll]
    selected = (history & 1).astype(bool)
    denominator = np.where(selected, mm, 1 - mm)
    if np.any(denominator <= 0) or np.any(pe <= 0) or np.any(pp4 <= 0):
        raise ValueError("degenerate full probability; no numerical floor chosen from data")
    values = np.stack((pe, pe / denominator, np.log(pe / pp4), mm,
                       COUNTS[history], STREAKS[history], SWITCHES[history]), -1).reshape(-1, len(FIELDS))
    coordinate = np.tile(ll * 32 + experts, len(ee))
    channel = np.repeat([TAGS.index(tags[t]) for t in ee], count).astype(np.int64)
    hh = history.ravel()
    keys = history_keys(coordinate, channel, hh, episode_index, np.repeat(oo // 32, count))
    if not np.isfinite(values).all():
        raise ValueError("nonfinite causal feature")
    return {"ends": np.repeat(ee, count), "selected": selected.ravel(), "history": hh,
            "keys": keys, "values": values, "eligible_looks": len(ee), "original_looks": len(ends)}


class ConditionalBank:
    """Normal-only builder; restored instances cannot accept donors."""
    def __init__(self):
        self.donors = set()
        self.rows = {}
        self.frozen = False

    def add(self, key, codes, values, *, normal, filtered):
        if self.frozen:
            raise AssertionError("frozen bank: no donor addition")
        if not normal or filtered is not True:
            raise ValueError("only filtered normal donors")
        if key in self.donors:
            raise ValueError("duplicate donor episode")
        self.donors.add(key)
        unique, inverse, counts = np.unique(codes, return_inverse=True, return_counts=True)
        totals = np.array([np.bincount(inverse, weights=values[:, j], minlength=len(unique)) for j in range(len(FIELDS))]).T
        for code, n, avg in zip(unique.tolist(), counts.tolist(), totals / counts[:, None]):
            if code not in self.rows:
                self.rows[code] = [n, 1, avg.copy()]
            else:
                row = self.rows[code]
                row[0] += n
                row[1] += 1
                row[2] += avg

    def finalize(self):
        good = sorted(k for k, v in self.rows.items() if v[0] >= 5 and v[1] >= 3)
        self.codes = np.array(good, dtype=np.int64)
        self.counts = np.array([[self.rows[k][0], self.rows[k][1]] for k in good], dtype=np.int64).reshape(-1, 2)
        self.means = np.array([self.rows[k][2] / self.rows[k][1] for k in good]).reshape(-1, len(FIELDS))
        self.report = {"bins": len(self.rows), "qualified_bins": len(good), "donor_episodes": len(self.donors),
                       "observations": sum(v[0] for v in self.rows.values())}
        self.frozen = True
        self.rows.clear()
        return self

    def export(self):
        if not self.frozen:
            raise ValueError("bank not frozen")
        return {"codes": self.codes, "counts": self.counts, "means": self.means}

    @classmethod
    def restore(cls, state):
        obj = cls()
        obj.codes, obj.counts, obj.means = (state[k].copy() for k in ("codes", "counts", "means"))
        if obj.codes.dtype.kind not in "iu" or not (np.diff(obj.codes) > 0).all():
            raise ValueError("invalid codes")
        if obj.means.shape != (len(obj.codes), len(FIELDS)) or obj.counts.shape != (len(obj.codes), 2):
            raise ValueError("invalid bank shape")
        if np.any(obj.counts < [5, 3]) or not np.isfinite(obj.means).all():
            raise ValueError("invalid frozen donor counts or means")
        obj.frozen = True
        return obj

    def lookup(self, codes):
        out = np.full((len(codes), len(FIELDS)), np.nan)
        if not len(self.codes):
            return np.zeros(len(codes), dtype=bool), out
        ix = np.searchsorted(self.codes, codes)
        good = (ix < len(self.codes)) & (self.codes[np.minimum(ix, len(self.codes) - 1)] == codes)
        out[good] = self.means[ix[good]]
        return good, out


def matched_controls(observations, banks):
    result = {level: banks[level].lookup(observations["keys"][level]) for level in LEVELS}
    for coarse, fine in (("B", "C"), ("C", "H"), ("H", "HP")):
        if np.any(result[fine][0] & ~result[coarse][0]):
            raise ValueError("nested exact bin support failed")
    for level in ("C", "H", "HP"):
        good, control = result[level]
        cols = [4] if level == "C" else [4, 5, 6]
        np.testing.assert_allclose(observations["values"][good][:, cols], control[good][:, cols], atol=1e-12, rtol=0)
    return result


def block(values, control, good):
    n = int(good.sum())
    return {"events": n, "raw": values[good].mean(0).tolist() if n else None,
            "control": control[good].mean(0).tolist() if n else None,
            "residual": (values[good] - control[good]).mean(0).tolist() if n else None}


def compare_on_queries(observations, controls, eligible):
    """No apparent attenuation caused by silently changing the target query set."""
    values = observations["values"]
    return {
        "eligible_events": int(eligible.sum()),
        "native": {k: block(values, c, eligible & good) for k, (good, c) in controls.items()},
        "aligned_H": {k: block(values, controls[k][1], eligible & controls["H"][0]) for k in ("B", "C", "H")},
        "aligned_HP": {k: block(values, controls[k][1], eligible & controls["HP"][0]) for k in ("H", "HP")},
    }
