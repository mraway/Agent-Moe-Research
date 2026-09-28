"""M7 weak lexical control: causal channel-specific normal unigram novelty."""
from __future__ import annotations

from collections import Counter
import math

import torch

from research_v2 import io_g, trm3_g
from research_v4.codex_g_m3_statistics import fingerprint
from research_v4.codex_g_m6_statistics import register as register_m6

TAGS = ("analysis", "commentary", "final")


class TokenUnigram(trm3_g.GStatistic):
    name = channel = "TU"

    def __init__(self, *, window_width=8, layers=None, smoothing=.5):
        super().__init__(window_width=window_width, layers=layers)
        self.smoothing = float(smoothing)
        if self.smoothing <= 0 or not math.isfinite(self.smoothing):
            raise ValueError("positive finite smoothing required")
        self.counts = None

    def config(self):
        return {**super().config(), "smoothing": self.smoothing,
                "recipe": "normal_per_channel_token_unigram_plus_one_merged_UNK_v1"}

    def fit(self, episodes, view):
        if not episodes or any(e.variant not in io_g.NORMAL_VARIANTS or e.filter_pass is not True for e in episodes):
            raise ValueError("TU fitting requires quality-filtered normal episodes")
        self.counts = {tag: Counter() for tag in TAGS}
        for ep in episodes:
            for token, tag in zip(ep.token_ids.tolist(), ep.channel_tags, strict=True):
                if tag in view.channels:
                    self.counts[tag][int(token)] += 1
        self._prepare()
        return self

    def _prepare(self):
        if set(self.counts) != set(TAGS) or any(not c for c in self.counts.values()):
            raise ValueError("TU needs a fitting reference in every V1 channel")
        if any(not isinstance(v, int) or v <= 0 for c in self.counts.values() for v in c.values()):
            raise ValueError("invalid unigram counts")
        self.tables, self.unknown = {}, {}
        for tag, counts in self.counts.items():
            denominator = sum(counts.values()) + self.smoothing * (len(counts) + 1)
            self.tables[tag] = {k: -math.log((v + self.smoothing) / denominator) for k, v in counts.items()}
            self.unknown[tag] = -math.log(self.smoothing / denominator)

    def per_token(self, episode):
        if self.counts is None:
            raise ValueError("fit first")
        values = [self.tables[tag].get(int(token), self.unknown[tag]) if tag in TAGS else 0.
                  for token, tag in zip(episode.token_ids.tolist(), episode.channel_tags, strict=True)]
        return torch.tensor(values, dtype=torch.float64)[:, None]

    def state_dict(self):
        if self.counts is None:
            raise ValueError("fit first")
        body = {"statistic": self.name, "config": self.config(),
                "counts": {t: {str(k): v for k, v in sorted(c.items())} for t, c in self.counts.items()}}
        return {**body, "fingerprint": fingerprint(body)}

    def load_state(self, state):
        body = {k: v for k, v in state.items() if k != "fingerprint"}
        if fingerprint(body) != state["fingerprint"]:
            raise ValueError("TU state fingerprint mismatch")
        if body["statistic"] != self.name or body["config"] != self.config():
            raise ValueError("TU config mismatch")
        self.counts = {t: {int(k): v for k, v in c.items()} for t, c in body["counts"].items()}
        self._prepare()
        return self


def register():
    register_m6()
    if "TU" in trm3_g.STATISTICS and trm3_g.STATISTICS["TU"] is not TokenUnigram:
        raise ValueError("refusing to overwrite TU")
    trm3_g.STATISTICS["TU"] = TokenUnigram


register()
