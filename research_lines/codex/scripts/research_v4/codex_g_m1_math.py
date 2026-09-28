"""Pure tensor/statistical helpers for the fixed, descriptive Codex G M1 study."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

import numpy as np
import torch

REPRESENTATIONS = ("U", "W", "P")
BANDS = ("all", "early", "middle", "late")
STAGES = ("pre_E", "E", "E_to_X", "X", "post_X")


def js_divergence(p: torch.Tensor, q: torch.Tensor) -> torch.Tensor:
    """Natural-log JS, summed only on the final expert dimension."""
    p, q = p.double().clamp_min(0), q.double().clamp_min(0)
    p = p / p.sum(-1, keepdim=True).clamp_min(1e-30)
    q = q / q.sum(-1, keepdim=True).clamp_min(1e-30)
    m = (p + q) * 0.5
    return (0.5 * (
        torch.where(p > 0, p * (p.clamp_min(1e-30).log() - m.clamp_min(1e-30).log()), 0.0)
        + torch.where(q > 0, q * (q.clamp_min(1e-30).log() - m.clamp_min(1e-30).log()), 0.0)
    ).sum(-1)).clamp_min(0)


def representations(ids: torch.Tensor, weights: torch.Tensor, logits: torch.Tensor) -> tuple[torch.Tensor, dict[str, float]]:
    """Return [3,T,L,E], using observed identities and stored mixing weights."""
    if ids.ndim != 3 or weights.shape != ids.shape or logits.ndim != 3 or logits.shape[:2] != ids.shape[:2]:
        raise ValueError("routing tensor shape mismatch")
    if not torch.isfinite(weights).all() or not torch.isfinite(logits).all():
        raise ValueError("nonfinite routing tensor")
    ids = ids.long()
    if bool((ids < 0).any()) or bool((ids >= logits.shape[-1]).any()):
        raise ValueError("expert id outside router range")
    ordered = ids.sort(-1).values
    if bool((ordered[..., 1:] == ordered[..., :-1]).any()):
        raise ValueError("duplicate selected expert")
    weights = weights.float()
    total = weights.sum(-1, keepdim=True)
    if bool((weights < 0).any()) or bool((total <= 0).any()):
        raise ValueError("invalid gate weights")
    simplex_error = float((total - 1).abs().max())
    reconstructed = torch.softmax(torch.gather(logits.float(), -1, ids), -1)
    reconstruction_error = float((weights - reconstructed).abs().max())
    if simplex_error > 0.01 or reconstruction_error > 0.01:
        raise ValueError(f"gate contract failed: simplex={simplex_error}, reconstruction={reconstruction_error}")
    uniform = torch.zeros_like(logits, dtype=torch.float32).scatter_(-1, ids, 1.0 / ids.shape[-1])
    weighted = torch.zeros_like(uniform).scatter_(-1, ids, weights / total)
    probability = torch.softmax(logits.float(), -1)
    result = torch.stack((uniform, weighted, probability)).permute(0, 2, 1, 3).contiguous()
    return result, {"gate_simplex_max_error": simplex_error, "gate_reconstruction_max_error": reconstruction_error}


def token_diagnostics(rep: torch.Tensor, ids: torch.Tensor, tags: Sequence[str], step_starts: Sequence[int]) -> dict[str, np.ndarray]:
    """Causal changes, excluding channel/step boundaries; no labels are accepted."""
    u, w, p = rep.double()
    t, layers, _ = u.shape
    k = ids.shape[-1]
    uniform_loss = js_divergence(w, u)
    entropy = -(torch.where(w > 0, w * w.clamp_min(1e-30).log(), 0.0)).sum(-1)
    concentration = 1.0 - entropy / np.log(k)
    valid = np.zeros(t, dtype=bool)
    if t > 1:
        valid[1:] = [tags[i] == tags[i - 1] and tags[i] in {"analysis", "commentary", "final"} for i in range(1, t)]
    for start in step_starts:
        if 0 <= start < t:
            valid[start] = False
    ordered = ids.sort(-1).values.permute(1, 0, 2)
    stable = torch.zeros((t, layers), dtype=torch.bool)
    if t > 1:
        stable[1:] = (ordered[1:] == ordered[:-1]).all(-1)
    stable &= torch.from_numpy(valid)[:, None]
    changes = []
    for vector in (w, p):
        change = torch.zeros((t, layers), dtype=torch.float64)
        if t > 1:
            change[1:] = js_divergence(vector[1:], vector[:-1])
        changes.append(change)
    return {
        "token_kept": np.asarray([tag in {"analysis", "commentary", "final"} for tag in tags]),
        "uniformisation_js": uniform_loss.numpy(),
        "gate_concentration": concentration.numpy(),
        "valid_pair": valid,
        "stable_mask": stable.numpy(),
        "W_change": changes[0].numpy(),
        "P_change": changes[1].numpy(),
    }


def diagnostic_summary(diag: dict[str, np.ndarray], lo: int, hi: int) -> dict[str, Any]:
    lo, hi = max(0, lo), min(hi, len(diag["valid_pair"]) - 1)
    if hi < lo:
        return {"tokens": 0}
    span = slice(lo, hi + 1)
    valid = diag["valid_pair"][span]
    stable = diag["stable_mask"][span]
    kept = diag["token_kept"][span]
    layers = stable.shape[-1]
    stable_n, pairs_n = int(stable.sum()), int(valid.sum()) * layers
    def conditional(array: np.ndarray, mask: np.ndarray) -> float | None:
        return float(array[mask].mean()) if bool(mask.any()) else None
    return {
        "positions_in_range": hi - lo + 1,
        "tokens": int(kept.sum()),
        "uniformisation_js": conditional(diag["uniformisation_js"][span], kept),
        "gate_concentration": conditional(diag["gate_concentration"][span], kept),
        "valid_layer_pairs": pairs_n,
        "stable_layer_pairs": stable_n,
        "stable_layer_fraction": stable_n / pairs_n if pairs_n else None,
        "stable_pairs_by_layer": stable.sum(0).tolist(),
        "W_change_stable": conditional(diag["W_change"][span], stable),
        "P_change_stable": conditional(diag["P_change"][span], stable),
        "W_change_all": conditional(diag["W_change"][span], np.broadcast_to(valid[:, None], stable.shape)),
        "P_change_all": conditional(diag["P_change"][span], np.broadcast_to(valid[:, None], stable.shape)),
    }


def stage_bounds(e: int | None, x: int | None) -> dict[str, tuple[int, int] | None]:
    out = {name: None for name in STAGES}
    if e is not None:
        out["pre_E"], out["E"] = (max(0, e - 16), e - 1), (e, e + 16)
        if x is not None:
            out.update(E_to_X=(e + 17, x - 1), X=(x, x + 16), post_X=(x + 17, x + 64))
    return out


def band_scores(means: torch.Tensor, q: torch.Tensor) -> np.ndarray:
    """[3,N,L,E], [3,N,L,E] -> [N,3,4]; all and fixed thirds."""
    per_layer = js_divergence(means, q)
    layers = per_layer.shape[-1]
    if layers % 3:
        raise ValueError("three fixed layer thirds require divisible layer count")
    width = layers // 3
    bands = [per_layer.mean(-1)] + [per_layer[..., i * width:(i + 1) * width].mean(-1) for i in range(3)]
    return torch.stack(bands, -1).permute(1, 0, 2).numpy()


def match_keys(tag: str, bucket: int, episode_index: int) -> tuple[tuple, ...]:
    return ((0, tag, bucket, episode_index), (1, tag, bucket), (2, tag, episode_index), (3, tag), (4,))


class NormalPercentiles:
    """Window-reference midranks; deliberately NOT conformal p values."""
    def __init__(self, references: Sequence[dict[str, Any]], min_windows: int = 30, min_episodes: int = 10) -> None:
        chunks, episodes = defaultdict(list), defaultdict(set)
        for row in references:
            for tag, bucket in sorted(set(zip(row["tags"], row["ordinals"] // 32))):
                mask = (np.asarray(row["tags"]) == tag) & (row["ordinals"] // 32 == bucket)
                for key in match_keys(str(tag), int(bucket), row["episode_index"]):
                    chunks[key].append(row["scores"][mask].reshape(int(mask.sum()), -1))
                    episodes[key].add(row["key"])
        self.references = {}
        for key, blocks in chunks.items():
            n = sum(len(block) for block in blocks)
            if n >= min_windows and len(episodes[key]) >= min_episodes:
                self.references[key] = np.sort(np.concatenate(blocks), axis=0)

    def transform(self, scores: np.ndarray, tags: Sequence[str], ordinals: np.ndarray, episode_index: int) -> tuple[np.ndarray, np.ndarray]:
        if not len(scores):
            return np.empty_like(scores), np.zeros(0, dtype=np.uint8)
        flat = scores.reshape(len(scores), -1)
        output = np.full_like(flat, np.nan)
        levels = np.full(len(scores), 255, dtype=np.uint8)
        for tag, bucket in sorted(set(zip(tags, ordinals // 32))):
            indices = np.flatnonzero((np.asarray(tags) == tag) & (ordinals // 32 == bucket))
            for key in match_keys(str(tag), int(bucket), episode_index):
                ref = self.references.get(key)
                if ref is None:
                    continue
                for col in range(flat.shape[1]):
                    values = flat[indices, col]
                    left = np.searchsorted(ref[:, col], values, side="left")
                    right = np.searchsorted(ref[:, col], values, side="right")
                    output[indices, col] = (left + right) / (2.0 * len(ref))
                levels[indices] = key[0]
                break
        return output.reshape(scores.shape), levels


def cluster_mean(values: Sequence[float], clusters: Sequence[str], replicates: int = 2000, seed: int = 20260908) -> dict[str, Any]:
    if len(values) != len(clusters):
        raise ValueError("values and cluster ids disagree")
    valid = [(float(v), str(c)) for v, c in zip(values, clusters) if np.isfinite(v)]
    if not valid:
        return {"n": 0, "cluster_count": 0, "mean": None, "ci": None, "cluster_sizes": {}}
    by_cluster = defaultdict(list)
    for value, cluster in valid:
        by_cluster[cluster].append(value)
    names = sorted(by_cluster)
    sizes = np.array([len(by_cluster[name]) for name in names])
    sums = np.array([sum(by_cluster[name]) for name in names])
    ci = None
    if len(names) > 1:
        picks = np.random.default_rng(seed).integers(0, len(names), (replicates, len(names)))
        samples = sums[picks].sum(-1) / sizes[picks].sum(-1)
        ci = np.quantile(samples, [0.025, 0.975]).tolist()
    return {
        "n": len(valid), "cluster_count": len(names), "mean": float(np.mean([v for v, _ in valid])),
        "ci": ci, "cluster_sizes": {name: len(by_cluster[name]) for name in names},
    }


def finite_mean(values: Sequence[Any]) -> float | None:
    items = [float(v) for v in values if v is not None and np.isfinite(v)]
    return float(np.mean(items)) if items else None
