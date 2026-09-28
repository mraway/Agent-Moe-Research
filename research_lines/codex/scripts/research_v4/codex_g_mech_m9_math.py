"""Exact, additive M9 measurements; no fitting, matching or alarm selection."""
from __future__ import annotations

import unicodedata
import numpy as np

PARTS = ("window", "current", "current_part", "history_part", "current_early", "current_middle",
         "current_late", "window_early", "window_middle", "window_late")
COLUMNS = tuple(f"{stat}/{part}" for stat in ("S", "CW") for part in PARTS)
LAYER_COLUMNS = tuple(f"{stat}/{scope}/layer{layer}" for stat in ("S", "CW")
                      for scope in ("current", "window") for layer in range(24))
CATEGORIES = ("special", "whitespace", "letter", "number", "punctuation_symbol", "other")


def token_layer_terms(ids, logits, rarity):
    """[L,T,K], [L,T,E], [L,E] -> [T,L,2] for S and the frozen CW."""
    ids = np.asarray(ids, dtype=np.int64); logits = np.asarray(logits, dtype=np.float64)
    rarity = np.asarray(rarity, dtype=np.float64)
    if ids.ndim != 3 or logits.ndim != 3 or ids.shape[:2] != logits.shape[:2] or rarity.shape != (ids.shape[0], logits.shape[2]):
        raise ValueError("unaligned routing geometry")
    if ids.min(initial=0) < 0 or ids.max(initial=0) >= rarity.shape[1]: raise ValueError("expert out of range")
    if not np.isfinite(logits).all() or not np.isfinite(rarity).all() or (rarity < 0).any(): raise ValueError("invalid contributions")
    base = rarity[np.arange(ids.shape[0])[:, None, None], ids]
    selected = np.take_along_axis(logits, ids, axis=2)
    margin = selected-selected.min(axis=2, keepdims=True)
    return np.stack([base.sum(2).T, (base*(1+margin)).sum(2).T], axis=2)


def measures(terms):
    """[look,lag(-7..0),layer,stat] -> 20 additive terms and 96 layer terms."""
    if terms.ndim != 4 or terms.shape[1:] != (8, 24, 2): raise ValueError("expected [N,8,24,2]")
    current = terms[:, -1]
    window = terms.mean(1)
    blocks, layers = [], []
    for j in range(2):
        point = current[:, :, j]; full = window[:, :, j]
        blocks.extend([full.sum(1), point.sum(1), point.sum(1)/8, terms[:, :7, :, j].sum((1, 2))/8])
        blocks.extend(point[:, start:start+8].sum(1) for start in (0, 8, 16))
        blocks.extend(full[:, start:start+8].sum(1) for start in (0, 8, 16))
        layers.extend([point, full])
    return np.column_stack(blocks), np.concatenate(layers, axis=1)


def differences(features, lookup, queries, donors):
    out = np.full((len(queries), features.shape[1]), np.nan)
    for i, (q, ds) in enumerate(zip(queries, donors, strict=True)):
        ds = ds[ds >= 0]
        if len(ds): out[i] = features[lookup[q]]-features[lookup[ds]].mean(0)
    return out


def category(piece, *, special=False):
    if special: return "special"
    if piece and piece.isspace(): return "whitespace"
    if any(unicodedata.category(c).startswith("L") for c in piece): return "letter"
    if any(unicodedata.category(c).startswith("N") for c in piece): return "number"
    stripped = [c for c in piece if not c.isspace()]
    if stripped and all(unicodedata.category(c)[0] in ("P", "S") for c in stripped): return "punctuation_symbol"
    return "other"


def causal_context(tokens, end, step_lengths, width=32):
    if sum(step_lengths) != len(tokens) or end < 0 or end >= len(tokens): raise ValueError("invalid causal token axis")
    starts = np.cumsum([0]+list(step_lengths))
    step = int(np.searchsorted(starts[1:], end, side="right"))
    start = max(int(starts[step]), end-width+1)
    return start, np.asarray(tokens[start:end+1], dtype=np.int64)


def signed_shares(mean):
    if mean is None: return {s: None for s in ("S", "CW")}
    return {s: (float(mean[COLUMNS.index(s+"/current_part")]/mean[COLUMNS.index(s+"/window")])
                if abs(mean[COLUMNS.index(s+"/window")]) > 1e-9 else None) for s in ("S", "CW")}
