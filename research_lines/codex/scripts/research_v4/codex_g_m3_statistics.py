"""M3's two causal statistics; registration only, no shared-source changes."""
from __future__ import annotations

import hashlib
import json

import torch

from research_v2 import io_g, trm3_g

TAGS = ("analysis", "commentary", "final")


def fingerprint(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class FrozenState:
    """JSON round trips include the recipe, resolved geometry, and fitted arrays."""

    def _initialize(self, episodes):
        if not episodes or any(e.variant not in io_g.NORMAL_VARIANTS or e.filter_pass is not True for e in episodes):
            raise ValueError("M3 fit accepts only quality-filtered normal episodes")
        self.geometry = trm3_g.router_geometry(episodes[0])
        if any(trm3_g.router_geometry(e) != self.geometry for e in episodes):
            raise ValueError("mixed router geometry")
        self._layers = self._resolve_layers(self.geometry)

    def config(self):
        return {**super().config(), "recipe": self.recipe, "precision": "float64_window"}

    def state_dict(self):
        body = {"statistic": self.name, "config": self.config(), "geometry": self.geometry,
                "resolved_layers": list(self._layers), "payload": self._payload()}
        return {**body, "fingerprint": fingerprint(body)}

    def load_state(self, state):
        body = {k: v for k, v in state.items() if k != "fingerprint"}
        if fingerprint(body) != state["fingerprint"]:
            raise ValueError("fitted-state fingerprint mismatch")
        if body["statistic"] != self.name or body["config"] != self.config():
            raise ValueError("fitted recipe/config mismatch")
        self.geometry = dict(body["geometry"])
        self._layers = self._resolve_layers(self.geometry)
        if list(self._layers) != body["resolved_layers"]:
            raise ValueError("resolved layers mismatch")
        self._restore(body["payload"])
        return self

    def describe(self):
        return {**super().describe(), "layers": list(self._layers), "fingerprint": self.state_dict()["fingerprint"]}


class IdentityJS(FrozenState, trm3_g.GStatistic):
    name = channel = "CU"
    recipe = "uniform_actual_topk__channel_token_mean_q__layer_mean_js_nats_v1"

    def __init__(self, *, window_width=8, layers=None):
        super().__init__(window_width=window_width, layers=layers)
        self._layers = ()

    def _uniform(self, episode):
        ids = episode.top_k_ids[list(self._layers)].long().permute(1, 0, 2)
        out = torch.zeros((*ids.shape[:2], self.geometry["num_experts"]), dtype=torch.float64)
        return out.scatter_add_(2, ids, torch.full(ids.shape, 1.0 / ids.shape[-1], dtype=torch.float64))

    def fit(self, episodes, view):
        self._initialize(episodes)
        sums = torch.zeros((len(TAGS), len(self._layers), self.geometry["num_experts"]), dtype=torch.float64)
        counts = torch.zeros(len(TAGS), dtype=torch.int64)
        for episode in episodes:
            values = self._uniform(episode)
            for i, tag in enumerate(TAGS):
                mask = torch.tensor([t == tag and t in view.channels for t in episode.channel_tags])
                sums[i] += values[mask].sum(0)
                counts[i] += mask.sum()
        if bool((counts == 0).any()):
            raise ValueError("CU needs a normal reference for every registered V1 channel")
        self.q = sums / counts[:, None, None]
        self.counts = counts.tolist()
        return self

    def per_token(self, episode):
        # The channel is observed metadata. Homogeneous windows recover this one-hot
        # exactly, without overriding GStatistic.stream or its endpoint grid.
        channels = torch.tensor([[float(tag == t) for t in TAGS] for tag in episode.channel_tags], dtype=torch.float64)
        return torch.cat((self._uniform(episode).flatten(1), channels), dim=1)

    def window_score(self, means):
        channels = means[:, -len(TAGS):]
        if not bool(((channels == 0) | (channels == 1)).all()) or not bool((channels.sum(1) == 1).all()):
            raise ValueError("CU received a cross-channel or unsupported window")
        p = means[:, :-len(TAGS)].reshape(-1, len(self._layers), self.geometry["num_experts"])
        q = self.q[channels.argmax(1)]
        middle = (p + q) * .5
        # xlogy handles 0*log(0); explicit clamping avoids the undefined 0/0 branch.
        value = .5 * (torch.special.xlogy(p, p.clamp_min(1e-300) / middle.clamp_min(1e-300))
                      + torch.special.xlogy(q, q.clamp_min(1e-300) / middle.clamp_min(1e-300)))
        return value.sum(-1).mean(-1)

    def _payload(self):
        return {"q": self.q.tolist(), "channel_token_counts": self.counts, "tags": list(TAGS)}

    def _restore(self, payload):
        if payload["tags"] != list(TAGS):
            raise ValueError("channel order changed")
        self.q = torch.tensor(payload["q"], dtype=torch.float64)
        self.counts = list(payload["channel_token_counts"])
        if self.q.shape != (3, len(self._layers), self.geometry["num_experts"]):
            raise ValueError("reference shape mismatch")
        if not bool(torch.isfinite(self.q).all()) or bool((self.q < 0).any()) or not torch.allclose(self.q.sum(-1), torch.ones_like(self.q.sum(-1))):
            raise ValueError("invalid reference simplex")


class SelectedMass(FrozenState, trm3_g.ProbStatistic):
    name = channel = "CM"
    recipe = "full_softmax_actual_selected_mass__one_sided_high__layer_mean_v1"

    def __init__(self, *, window_width=8, layers=None, prob_cache_dir=None):
        # Never default to a shared cache.
        super().__init__(window_width=window_width, layers=layers, prob_cache_dir=prob_cache_dir)
        self._layers = ()

    def fit(self, episodes, view):
        self._initialize(episodes)
        return self

    def per_token(self, episode):
        p = self._probabilities(episode).double()
        p = p / p.sum(-1, keepdim=True)
        ids = episode.top_k_ids[list(self._layers)].long()
        return p.gather(-1, ids).sum(-1).T.contiguous()

    def window_score(self, means):
        return means.mean(-1)

    def _payload(self):
        return {}

    def _restore(self, payload):
        if payload:
            raise ValueError("CM has no fitted predictive parameters")
        self._prob_memo = None


def register():
    for name, cls in (("CU", IdentityJS), ("CM", SelectedMass)):
        if name in trm3_g.STATISTICS and trm3_g.STATISTICS[name] is not cls:
            raise ValueError(f"refusing to overwrite statistic {name}")
        trm3_g.STATISTICS[name] = cls
    if "CM" not in trm3_g.PROB_STATISTICS:
        trm3_g.PROB_STATISTICS = (*trm3_g.PROB_STATISTICS, "CM")


register()
