"""Stage-1 diagnostic monitor: separate historical alarms from current score state.

This module does not equate score recovery with successful resistance. Conformal
statements require exchangeable *groups*, a fixed scorer and stopping protocol.
The local state machine is descriptive and has no separate error-rate guarantee.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Sequence

import torch

from phase_a.normal_manifold import evenly_spaced_positions, selection_window_signatures


WIDTH = 8
NEIGHBORS = 5
ANCHORS_PER_TRACE = 8
STATE_WIDTH = 32
METHODS = (
    "state_knn", "diagonal_distance", "marginal_surprisal", "unseen_fraction",
    "state_independent_layers", "state_shuffled_bank",
)
CLAUDE_SOURCE = {
    "path": ".claude/worktrees/algorithm-research-proposals-427363/src/research_v2/scorers/wgm.py",
    "sha256": "62676ef4755199823d1928642e2f00f1a92c5fdbc0ce59d38f875ecf9ee6443d",
    "borrowed": "WGMScorer._whitening (window-weighted branch), global g1 fit/score equations",
    "adaptations": "standalone all-layer helper; no registry/workflow/PCA/harness dependency",
}


def conformal_threshold(maxima: Sequence[float], alpha: float) -> dict:
    """Strict > order-statistic rule, including the otherwise-missed +infinity rank."""
    if not 0 < alpha < 1 or not maxima:
        raise ValueError("need calibration groups and alpha in (0, 1)")
    values = sorted(float(x) for x in maxima)
    if any(math.isnan(x) or x == math.inf for x in values) or not any(math.isfinite(x) for x in values):
        raise ValueError("calibration needs finite evidence; only empty paths may be -inf")
    rank = math.ceil((len(values) + 1) * (1 - alpha))
    threshold = values[rank - 1] if rank <= len(values) else math.inf
    return {
        "threshold": threshold, "rank": rank, "groups": len(values), "alpha": alpha,
        "effective_alpha_upper_bound": max(0, len(values) + 1 - rank) / (len(values) + 1),
        "attainable": threshold < math.inf, "comparison": "strict_gt",
    }


def history_tail(maxima: Sequence[float], value: float) -> float:
    if not maxima:
        raise ValueError("empty calibration reference")
    return (1 + sum(x >= value for x in maxima)) / (len(maxima) + 1)


def monitor_stream(
    endpoints: Sequence[int], scores: Sequence[float], threshold: float,
    *, depth: int = STATE_WIDTH,
) -> dict:
    """Prefix-invariant state transitions; endpoints must be consecutive eligible looks.

    Event history may only accumulate. Current-state evidence deliberately does not.
    Equality to the threshold is not an alarm. +inf is the valid abstaining threshold.
    """
    if len(endpoints) != len(scores) or depth < 2 or math.isnan(threshold):
        raise ValueError("invalid monitor configuration or stream lengths")
    if any(int(t) != t for t in endpoints):
        raise ValueError("fractional endpoint")
    ends = [int(t) for t in endpoints]
    if any(t < 0 for t in ends) or any(b != a + 1 for a, b in zip(ends, ends[1:])):
        raise ValueError("endpoints must be nonnegative and consecutive")
    if any(not math.isfinite(float(x)) for x in scores):
        raise ValueError("score stream must be finite")
    first_alarm = None
    episode_start = None
    episodes = 0
    state = "NORMAL"
    flags: deque[bool] = deque(maxlen=depth)
    transitions = []
    running = -math.inf
    outputs = []
    for t, value in zip(ends, scores):
        running = max(running, float(value))
        above = value > threshold
        previous = state
        if above and (episode_start is None or state == "RECOVERING"):
            episode_start = t
            episodes += 1
            flags.clear()
            if first_alarm is None:
                first_alarm = t
        if episode_start is not None:
            flags.append(above)
            if len(flags) < depth:
                state = "UNCERTAIN"
            elif not any(flags):
                state = "RECOVERING"
            elif 2 * sum(flags) >= depth:
                state = "SUSTAINED"
            else:
                state = "UNCERTAIN"
        censored = episode_start is not None and len(flags) < depth
        row = {"endpoint": t, "raw_score": float(value), "history_max": running,
               "first_alarm": first_alarm, "episode_start": episode_start,
               "episode_index": episodes, "state": state, "censored": censored}
        outputs.append(row)
        if state != previous:
            transitions.append(dict(row))
    return {
        "first_alarm": first_alarm, "final_state": state, "episode_count": episodes,
        "censored": bool(outputs and outputs[-1]["censored"]),
        "transitions": transitions, "outputs": outputs,
    }


def synthetic_monitor_audit() -> dict:
    cases = {
        "normal": [0.0] * 96,
        "single_spike_then_normal": [0.0] * 10 + [10.0] + [0.0] * 85,
        "sustained": [0.0] * 10 + [10.0] * 86,
        "repeated": [0.0] * 10 + [10.0] * 40 + [0.0] * 40 + [10.0] * 40,
        "censored": [0.0] * 10 + [10.0] * 4,
    }
    result = {}
    for name, scores in cases.items():
        ends = list(range(len(scores)))
        revised = monitor_stream(ends, scores, 5.0)
        # Minimal mathematical reproduction of TRM-3's max->monotone-p problem.
        # Not imported from or written to Claude; not its full detector benchmark.
        accumulated = []
        running = -math.inf
        for score in scores:
            running = max(running, score)
            accumulated.append(running)
        legacy = monitor_stream(ends, accumulated, 5.0)
        result[name] = {
            "local_state": revised["final_state"], "legacy_accumulated_state": legacy["final_state"],
            "episodes": revised["episode_count"], "censored": revised["censored"],
            "first_alarm": revised["first_alarm"], "states_seen": sorted({r["state"] for r in revised["outputs"]}),
        }
    return result


def validate_group_roles(fit: Sequence[str], cal: Sequence[str], evaluation: Sequence[str]) -> None:
    sets = [set(fit), set(cal), set(evaluation)]
    if any(not s for s in sets) or any(sets[i] & sets[j] for i in range(3) for j in range(i)):
        raise ValueError("fit/calibration/evaluation groups overlap or are empty")


def onset_hit(alarm: int | None, interval: Sequence[int] | None, horizon: int) -> dict | None:
    """Uncertainty bounds at the SAME deadline definition, without tolerance inflation."""
    if interval is None:
        return None
    low, high = (int(t) for t in interval)
    if low < 0 or high < low or horizon < 0:
        raise ValueError("invalid onset interval or recall horizon")
    return {
        "start_point": alarm is not None and low <= alarm <= low + horizon,
        "definite": alarm is not None and high <= alarm <= low + horizon,
        "possible": alarm is not None and low <= alarm <= high + horizon,
    }


@dataclass
class DiagonalGeometry:
    mu: torch.Tensor
    sd: torch.Tensor
    centre: torch.Tensor

    @classmethod
    def fit(cls, blocks: Sequence[torch.Tensor]) -> "DiagonalGeometry":
        """Extracted/adapted from Claude WGM g1; see CLAUDE_SOURCE for provenance."""
        matrix = torch.cat(list(blocks))
        if len(matrix) < 2:
            raise ValueError("at least two fit windows required")
        mu = matrix.mean(0)
        sd = matrix.std(0) + 1e-3
        centre = ((matrix - mu) / sd).mean(0)
        return cls(mu, sd, centre)

    def score(self, windows: torch.Tensor) -> torch.Tensor:
        return (((windows - self.mu) / self.sd - self.centre) ** 2).sum(1)


@dataclass
class StaticBank:
    anchors: torch.Tensor
    shuffled_anchors: torch.Tensor
    diagonal: DiagonalGeometry
    counts: torch.Tensor
    fit_trace_count: int
    fit_window_count: int


def signatures(top_k: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    if top_k.ndim != 3 or top_k.shape[0] != 16 or top_k.shape[2] != 8:
        raise ValueError("expected [16, token, 8] routing")
    if top_k.numel() and (int(top_k.min()) < 0 or int(top_k.max()) >= 64):
        raise ValueError("expert IDs outside [0, 63]")
    # This adapter returns exactly the causal normalized layer marginals.
    return selection_window_signatures(top_k, WIDTH)


def shuffle_reference_layers(bank: torch.Tensor, seed: int = 901) -> torch.Tensor:
    generator = torch.Generator().manual_seed(seed)
    result = bank.clone()
    for layer in range(bank.shape[1]):
        result[:, layer] = bank[torch.randperm(len(bank), generator=generator), layer]
    return result


def fit_static_bank(top_k_streams: Sequence[torch.Tensor]) -> StaticBank:
    blocks = []
    anchors = []
    counts = torch.zeros((16, 64), dtype=torch.float64)
    eligible_traces = 0
    for top_k in top_k_streams:
        _, state = signatures(top_k)
        for layer in range(16):
            counts[layer] += torch.bincount(top_k[layer].long().reshape(-1), minlength=64)
        if len(state):
            blocks.append((state * 8).reshape(len(state), -1))
            selected = evenly_spaced_positions(len(state), ANCHORS_PER_TRACE)
            anchors.append(state[list(selected)].sqrt())
            eligible_traces += 1
    if not anchors:
        raise ValueError("no fit windows")
    anchor = torch.cat(anchors)
    if len(anchor) < NEIGHBORS:
        raise ValueError("insufficient reference anchors")
    return StaticBank(anchor, shuffle_reference_layers(anchor), DiagonalGeometry.fit(blocks),
                      counts, eligible_traces, sum(len(b) for b in blocks))


def layer_distances(query: torch.Tensor, bank: torch.Tensor) -> torch.Tensor:
    affinity = torch.einsum("nle,mle->nml", query, bank)
    return (1 - affinity).clamp_min(0).sqrt()


def state_scores(query: torch.Tensor, bank: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    if len(bank) < NEIGHBORS:
        raise ValueError("insufficient reference anchors")
    distances = layer_distances(query, bank)
    joint = distances.mean(2).kthvalue(NEIGHBORS, dim=1).values
    independent = distances.kthvalue(NEIGHBORS, dim=1).values.mean(1)
    return joint, independent


def score_static(top_k: torch.Tensor, bank: StaticBank) -> tuple[list[int], dict[str, list[float]]]:
    ends, state = signatures(top_k)
    if not len(ends):
        return [], {method: [] for method in METHODS}
    query = state.sqrt()
    joint, independent = state_scores(query, bank.anchors)
    shuffled, _ = state_scores(query, bank.shuffled_anchors)
    q = (bank.counts + .5) / (bank.counts.sum(1, keepdim=True) + 32)
    scores = {
        "state_knn": joint,
        "state_independent_layers": independent,
        "state_shuffled_bank": shuffled,
        "diagonal_distance": bank.diagonal.score((state * 8).reshape(len(state), -1)),
        "marginal_surprisal": (state.double() * -q.log()[None]).sum(2).mean(1),
        "unseen_fraction": (state.double() * (bank.counts == 0)[None]).sum(2).mean(1),
    }
    if any(not torch.isfinite(value).all() for value in scores.values()):
        raise ValueError("nonfinite score")
    return ends.tolist(), {key: value.tolist() for key, value in scores.items()}
