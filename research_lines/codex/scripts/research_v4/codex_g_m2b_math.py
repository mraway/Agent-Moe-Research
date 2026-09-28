"""Label-free probability components and exact, fixed-support JS dynamics."""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import torch

from research_v2 import trm3_g
from research_v4.codex_g_m1_math import js_divergence

REPS = ("U", "W", "P", "V", "P_no_mass", "P_no_selected", "P_no_tail", "P_support_mass")
BASIS = ("V", "Q", "P_no_selected", "P_no_tail", "P_support_mass")
PHYSICAL = ("selected_mass", "selected_entropy_normalized", "tail_entropy_normalized")
DYNAMIC = ("total", "mass", "selected", "tail")
VIEW = trm3_g.view_of("V1")
WIDTH, HORIZON = 8, 352


def probability_basis(rep: torch.Tensor) -> tuple:
    """M1 [U,W,P,T,L,E] layout -> five [R,T,L,E] sufficient components.

    Divide P by its double-precision sum solely to remove float32 softmax sum
    roundoff. This is not a new softmax, top-k decision or learned transform.
    """
    if rep.ndim != 4 or rep.shape[0] != 3 or not torch.isfinite(rep).all():
        raise ValueError("expected finite [3,T,L,E] M1 representations")
    u, w, original = rep.double()
    selected = u > 0
    k = selected.sum(-1, keepdim=True)
    if bool((k <= 1).any()) or bool((k >= u.shape[-1] - 1).any()):
        raise ValueError("selected and tail groups each need at least two experts")
    p = original / original.sum(-1, keepdim=True)
    m = (p * selected).sum(-1, keepdim=True)
    if bool(((m <= 0) | (m >= 1)).any()):
        raise ValueError("degenerate probability mass: no silent fill-in")
    v, q = p * selected / m, p * ~selected / (1 - m)
    r = (~selected).double() / (u.shape[-1] - k)
    basis = torch.stack((v, q, m * u + (1 - m) * q,
                         m * v + (1 - m) * r, m * u + (1 - m) * r)).float()
    entropy = lambda x: -(x * x.clamp_min(1e-300).log()).sum(-1)
    physical = torch.stack((m[..., 0], entropy(v) / k[..., 0].double().log(),
                            entropy(q) / (u.shape[-1] - k[..., 0]).double().log()), 1).float()
    contract = {"P_sum_roundoff_max": float((original.sum(-1) - 1).abs().max()),
                "identity_max_error": float((p - m * v - (1 - m) * q).abs().max()),
                "V_W_max_error": float((v - w).abs().max()),
                "basis_simplex_max_error": float((basis.double().sum(-1) - 1).abs().max())}
    if contract["P_sum_roundoff_max"] > 1e-5 or contract["identity_max_error"] > 1e-12:
        raise ValueError("probability decomposition contract failed")
    return basis, physical, p, contract


def complete_ablations(basis: torch.Tensor, m0: torch.Tensor) -> torch.Tensor:
    """[N,5,L,E], [N,L] or [L] -> [N,5,L,E], using fitted mass only."""
    if basis.ndim != 4 or basis.shape[1] != 5 or not torch.isfinite(m0).all():
        raise ValueError("invalid ablation basis/mass shape")
    if bool(((m0 <= 0) | (m0 >= 1)).any()):
        raise ValueError("fitted mass outside open simplex")
    mass = m0[..., None]
    no_mass = mass * basis[:, 0] + (1 - mass) * basis[:, 1]
    return torch.stack((basis[:, 0], no_mass, basis[:, 2], basis[:, 3], basis[:, 4]), 1)


def causal_windows(values: torch.Tensor, tags: Sequence[str]) -> tuple:
    """[T,...] -> common M1 channel-only w8/H352 grid, with no event input."""
    ends, means, out_tags, ordinals = trm3_g.segmented_windows(
        values.reshape(values.shape[0], -1), tags, VIEW, WIDTH)
    n = min(len(ends), HORIZON)
    return ends[:n], means[:n].reshape((n,) + values.shape[1:]), out_tags[:n], ordinals[:n]


def layer_bands(values: torch.Tensor) -> torch.Tensor:
    """Replace final layer dimension by all/early/middle/late equal-layer means."""
    if values.shape[-1] != 24:
        raise ValueError("fixed study requires exactly 24 layers")
    return torch.stack((values.mean(-1), *(values[..., a:a + 8].mean(-1) for a in (0, 8, 16))), -1)


def partition_js(p: torch.Tensor, q: torch.Tensor, selected: torch.Tensor) -> torch.Tensor:
    """Exact grouped JS: [..,E] -> [.., total/mass/selected/tail].

    Within-group terms are the group mixture mass times generalized weighted
    JS. Subtracting the group-level JS from its expert-level terms implements
    the chain rule, including when the two group masses differ.
    """
    p, q = p.double(), q.double()
    if (p.shape != q.shape or p.shape != selected.shape or selected.dtype != torch.bool
            or not torch.isfinite(p).all() or not torch.isfinite(q).all()
            or bool((p < 0).any()) or bool((q < 0).any())
            or bool((p.sum(-1) <= 0).any()) or bool((q.sum(-1) <= 0).any())):
        raise ValueError("invalid JS partition inputs")
    p, q = p / p.sum(-1, keepdim=True), q / q.sum(-1, keepdim=True)
    def term(a, b):
        midpoint = (a + b) / 2
        return .5 * (a * (a.clamp_min(1e-300).log() - midpoint.clamp_min(1e-300).log())
                     + b * (b.clamp_min(1e-300).log() - midpoint.clamp_min(1e-300).log()))
    per_expert = term(p, q)
    mass_terms, conditional = [], []
    for mask in (selected, ~selected):
        marginal = term((p * mask).sum(-1), (q * mask).sum(-1))
        within = (per_expert * mask).sum(-1) - marginal
        if bool((within < -1e-12).any()):
            raise ValueError("negative generalized JS component")
        mass_terms.append(marginal)
        conditional.append(within.clamp_min(0))
    result = torch.stack((per_expert.sum(-1), sum(mass_terms), *conditional), -1)
    if not torch.allclose(result[..., 0], result[..., 1:].sum(-1), atol=2e-12, rtol=0):
        raise ValueError("JS chain rule failed")
    return result


def stable_dynamics(p: torch.Tensor, ids: torch.Tensor, tags: Sequence[str], step_starts: Sequence[int]) -> dict:
    """Endpoint-pair diagnostic, not JS of a window mean against normal q."""
    t, layers, _ = p.shape
    if layers != 24 or ids.shape[:2] != (24, t) or len(tags) != t:
        raise ValueError("dynamic geometry mismatch")
    valid = np.zeros(t, dtype=bool)
    valid[1:] = [tags[i] == tags[i - 1] and tags[i] in VIEW.channels for i in range(1, t)]
    for start in step_starts:
        if 0 <= start < t: valid[start] = False
    ordered = ids.sort(-1).values.permute(1, 0, 2)
    stable = torch.zeros((t, 24), dtype=torch.bool)
    stable[1:] = (ordered[1:] == ordered[:-1]).all(-1)
    stable &= torch.from_numpy(valid)[:, None]
    terms = torch.zeros((t, 24, 4), dtype=torch.float64)
    if t > 1:
        selected = torch.zeros_like(p[1:], dtype=torch.bool).scatter_(-1, ids[:, 1:].permute(1, 0, 2).long(), True)
        terms[1:] = partition_js(p[1:], p[:-1], selected)
    numerator, denominator = [], []
    for a, b in ((0, 24), (0, 8), (8, 16), (16, 24)):
        numerator.append((terms[:, a:b] * stable[:, a:b, None]).sum(1))
        denominator.append(stable[:, a:b].sum(1))
    return {"sum": torch.stack(numerator, -1).numpy(),
            "count": torch.stack(denominator, -1).numpy(),
            "valid_count": valid[:, None] * np.array([24, 8, 8, 8])[None, :],
            "identity_error": float((terms[..., 0] - terms[..., 1:].sum(-1)).abs().max()),
            "direct_js_error": float((terms[1:, :, 0] - js_divergence(p[1:], p[:-1])).abs().max()) if t > 1 else 0.0}


def dynamic_interval(dynamics: dict, endpoints: np.ndarray) -> dict:
    count = dynamics["count"][endpoints].sum(0)
    valid = dynamics["valid_count"][endpoints].sum(0)
    numerator = dynamics["sum"][endpoints].sum(0)
    means = [[float(numerator[c, b] / count[b]) if count[b] else None for b in range(4)] for c in range(4)]
    return {"stable_pairs": count.tolist(), "valid_pairs": valid.tolist(), "means": means}
