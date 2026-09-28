"""M6: two fixed causal modulations of the unmodified G rare surprisal."""
from __future__ import annotations

import torch

from research_v2 import io_g, trm3_g
from research_v4.codex_g_m3_statistics import fingerprint


def selected_log_margin(logits, ids):
    """log(p_e / min_actual_selected(p)); no re-ranking or softmax required."""
    if logits.ndim != 3 or ids.ndim != 3 or logits.shape[:2] != ids.shape[:2]:
        raise ValueError("expected aligned [L,T,E] logits and [L,T,K] ids")
    values = logits.double().gather(-1, ids.long())
    if not bool(torch.isfinite(values).all()):
        raise ValueError("nonfinite selected logits")
    return values - values.amin(-1, keepdim=True)


def causal_streak(ids, num_experts, tags, step_starts=(), cap=8):
    """Selected-coordinate streaks [L,T,K]; reset on steps/channels, no future."""
    if ids.ndim != 3 or ids.shape[1] != len(tags) or cap < 2:
        raise ValueError("invalid streak geometry or cap")
    layers, tokens, _ = ids.shape
    mask = torch.zeros((layers, tokens, num_experts), dtype=torch.bool)
    mask.scatter_(-1, ids.long(), True)
    starts = set(int(x) for x in step_starts if 0 <= int(x) < tokens)
    reset = torch.tensor([i == 0 or i in starts or tags[i] != tags[i - 1] for i in range(tokens)], dtype=torch.int64)
    segments = reset.cumsum(0)
    alive = mask.clone()
    streak = mask.to(torch.int64)
    for lag in range(1, min(cap, tokens)):
        prior = torch.zeros_like(mask)
        prior[:, lag:] = mask[:, :-lag] & (segments[lag:] == segments[:-lag])[None, :, None]
        alive &= prior
        streak += alive
    return streak.gather(-1, ids.long())


class ModulatedRare(trm3_g.RareSurprisal):
    """Shared S fit, but stricter normal-only/state contracts for the new classes."""
    recipe = "abstract"

    def __init__(self, *, window_width=8, layers=None, rare_threshold=.02, smoothing=.5, strength=1.):
        super().__init__(window_width=window_width, layers=layers, rare_threshold=rare_threshold, smoothing=smoothing)
        self.strength = float(strength)
        if not 0 <= self.strength < float("inf"):
            raise ValueError("strength must be finite and nonnegative")

    def config(self):
        return {**trm3_g.GStatistic.config(self), "rare_threshold": self.rare_threshold,
                "smoothing": self.smoothing, "strength": self.strength, "recipe": self.recipe,
                "precision": "float64", "history_cap": 8 if self.name == "CH" else None}

    def fit(self, episodes, view):
        if not episodes or any(e.variant not in io_g.NORMAL_VARIANTS or e.filter_pass is not True for e in episodes):
            raise ValueError("M6 fit requires only quality-filtered normal episodes")
        return super().fit(episodes, view)

    def per_token(self, episode):
        if self.strength == 0:
            return super().per_token(episode)
        if self.surprisal is None:
            raise ValueError("fit first")
        ids = episode.top_k_ids[list(self._layers)].long()
        table = self.surprisal[:, None, :].expand(len(self._layers), ids.shape[1], self.surprisal.shape[1])
        base = table.gather(-1, ids)
        return (base * (1 + self.strength * self.extra(episode, ids))).sum(dim=(0, 2))[:, None]

    def extra(self, episode, ids):
        raise NotImplementedError

    def top_coordinates(self, episode, end, n=3):
        # The inherited S attribution would omit the new contribution. Attribution
        # is disabled in M6; use the explicit documented empty fallback if requested.
        return []

    def state_dict(self):
        body = super().state_dict()
        return {**body, "fingerprint": fingerprint(body)}

    def load_state(self, state):
        body = {k: v for k, v in state.items() if k != "fingerprint"}
        if fingerprint(body) != state["fingerprint"]:
            raise ValueError("fitted-state fingerprint mismatch")
        if body["statistic"] != self.name or body["config"] != self.config():
            raise ValueError("fitted recipe/config mismatch")
        super().load_state(body)
        if (self.q.ndim != 2 or self.q.shape[0] != len(self._layers)
                or not bool(torch.isfinite(self.q).all()) or bool((self.q <= 0).any())):
            raise ValueError("invalid fitted q")
        self._feature_memo = None
        return self


class RelativeWeightRare(ModulatedRare):
    name = channel = "CW"
    recipe = "S_times_one_plus_actual_selected_logit_margin_v1"

    def __init__(self, *, prob_cache_dir=None, **kwargs):
        super().__init__(**kwargs)
        self.prob_cache_dir = prob_cache_dir

    def extra(self, episode, ids):
        logits = episode.router_logits(cache_dir=self.prob_cache_dir, keep=False)[list(self._layers)]
        return selected_log_margin(logits, ids)


class PersistentRare(ModulatedRare):
    name = channel = "CH"
    recipe = "S_times_one_plus_causal_streak8_minus_one_over_seven_v1"

    def extra(self, episode, ids):
        starts = [int(s["global_token_offset"]) for s in episode.step_spans]
        streak = causal_streak(ids, self.surprisal.shape[1], episode.channel_tags, starts, cap=8)
        return (streak.double() - 1) / 7


def register():
    for name, cls in (("CW", RelativeWeightRare), ("CH", PersistentRare)):
        if name in trm3_g.STATISTICS and trm3_g.STATISTICS[name] is not cls:
            raise ValueError(f"refusing to overwrite statistic {name}")
        trm3_g.STATISTICS[name] = cls
    # Only supplies the harness's explicit private cache path; CW never computes a
    # full softmax. No shared source or built-in statistic is modified.
    if "CW" not in trm3_g.PROB_STATISTICS:
        trm3_g.PROB_STATISTICS = (*trm3_g.PROB_STATISTICS, "CW")


register()
