#!/usr/bin/env python3
"""Zoom audit (compute): per-token scoring cost and fitted-state footprint of CAND-A / CAND-B.

CAND-A = wgm g1 whitened distance, layers 5-15, w=8.
CAND-B = pdm d1 depth chain, layers 5-11, w=4.

Measures, on CPU (torch threads = 6):
  * fit() wall clock and fitted-state bytes,
  * batch (whole-trace, vectorised) scoring cost amortised per decode token,
  * streaming (one token at a time, incremental) scoring cost per token,
  * the cost of the raw feature extraction alone (top-8 id -> window selection rate /
    top-1 chain gather), so the distance / table-lookup part can be separated.
Routing itself is free at inference: the top-8 ids are already produced by the router.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
torch.set_num_threads(6)

from research_v2 import io as rio  # noqa: E402
from research_v2.harness import build_split_cases, routine_traces  # noqa: E402
from research_v2.scorers import build as build_scorer  # noqa: E402

OUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "zoom" / "compute"
OUT.mkdir(parents=True, exist_ok=True)
EXPERTS = 64
TOPK = 8


def tensor_bytes(obj) -> int:
    if obj is None:
        return 0
    if isinstance(obj, torch.Tensor):
        return obj.numel() * obj.element_size()
    if isinstance(obj, dict):
        return sum(tensor_bytes(v) for v in obj.values())
    if isinstance(obj, (list, tuple)):
        return sum(tensor_bytes(v) for v in obj)
    if isinstance(obj, float):
        return 8
    return 0


def state_bytes(state) -> dict:
    out = {}
    if hasattr(state, "__dataclass_fields__"):
        for f in state.__dataclass_fields__:
            out[f] = tensor_bytes(getattr(state, f))
    else:
        for k, v in state.items():
            out[k] = tensor_bytes(v)
    out["total"] = sum(out.values())
    return out


# ---------------------------------------------------------------- streaming refs
class StreamingWGM:
    """Incremental CAND-A: keeps a length-w ring of top-8 ids and a running count vector."""

    def __init__(self, mu, sd, centre, layers, width):
        self.layers = list(layers)
        self.width = width
        self.inv_sd = (1.0 / sd).clone()
        self.mu = mu.clone()
        self.centre = centre.clone()
        self.dim = len(self.layers) * EXPERTS
        self.counts = torch.zeros(self.dim, dtype=torch.float32)
        self.ring: list[torch.Tensor] = []
        # flat offsets for scatter: layer index * 64
        self.offsets = torch.arange(len(self.layers), dtype=torch.long).unsqueeze(1) * EXPERTS

    def step(self, token_top_k: torch.Tensor) -> float | None:
        """token_top_k: [16, 8] long for one decode token; returns score or None."""
        flat = (token_top_k[self.layers, :].long() + self.offsets).reshape(-1)
        self.counts.index_add_(0, flat, torch.ones(flat.numel(), dtype=torch.float32))
        self.ring.append(flat)
        if len(self.ring) > self.width:
            old = self.ring.pop(0)
            self.counts.index_add_(0, old, torch.full((old.numel(),), -1.0))
        if len(self.ring) < self.width:
            return None
        z = (self.counts / self.width - self.mu) * self.inv_sd - self.centre
        return float((z * z).sum())


class StreamingPDM:
    """Incremental CAND-B: per token gather 1 marginal + (L-1) transition log-probs,
    then a running mean over the last w standardized values."""

    def __init__(self, log_depth, log_depth_initial, mean, sd, layers, width):
        self.layers = list(layers)
        self.log_depth = log_depth
        self.log_init = log_depth_initial
        self.mean = mean
        self.sd = sd
        self.width = width
        self.buf: list[float] = []
        self.total = 0.0

    def step(self, token_top_k: torch.Tensor) -> float | None:
        top1 = token_top_k[self.layers, 0].long()
        acc = float(self.log_init[top1[0]])
        for i in range(len(self.layers) - 1):
            acc += float(self.log_depth[i][top1[i], top1[i + 1]])
        u = (-acc - self.mean) / self.sd
        self.buf.append(u)
        self.total += u
        if len(self.buf) > self.width:
            self.total -= self.buf.pop(0)
        if len(self.buf) < self.width:
            return None
        return self.total / self.width


def bench(name, scorer_name, cfg, fit_pool, targets, streaming_factory):
    scorer = build_scorer(scorer_name, dict(cfg))
    t0 = time.perf_counter()
    state = scorer.fit(fit_pool)
    fit_s = time.perf_counter() - t0

    tokens = sum(int(t.top_k_ids.shape[1]) for t in targets)
    # warm-up
    for t in targets[:3]:
        scorer.score(state, t)
    t0 = time.perf_counter()
    for t in targets:
        scorer.score(state, t)
    batch_s = time.perf_counter() - t0

    # feature extraction only
    t0 = time.perf_counter()
    feat_tokens = 0
    for t in targets:
        if scorer_name == "wgm":
            from research_v2.features import selection_rate_windows

            selection_rate_windows(t.top_k_ids, cfg["window_width"], scorer.layers)
        else:
            t.top_k_ids[list(scorer.layers), :, 0].long()
        feat_tokens += int(t.top_k_ids.shape[1])
    feat_s = time.perf_counter() - t0

    # streaming
    stream_targets = targets[:40]
    stream_tokens = sum(int(t.top_k_ids.shape[1]) for t in stream_targets)
    t0 = time.perf_counter()
    for t in stream_targets:
        eng = streaming_factory(state)
        tk = t.top_k_ids
        for j in range(tk.shape[1]):
            eng.step(tk[:, j, :])
    stream_s = time.perf_counter() - t0

    sb = state_bytes(state)
    return {
        "name": name,
        "scorer": scorer_name,
        "config": scorer.config(),
        "fit_traces": len(fit_pool),
        "fit_tokens": sum(int(t.top_k_ids.shape[1]) for t in fit_pool),
        "fit_seconds": round(fit_s, 4),
        "score_traces": len(targets),
        "score_tokens": tokens,
        "batch_total_seconds": round(batch_s, 4),
        "batch_us_per_token": round(batch_s / tokens * 1e6, 3),
        "feature_only_us_per_token": round(feat_s / feat_tokens * 1e6, 3),
        "streaming_traces": len(stream_targets),
        "streaming_tokens": stream_tokens,
        "streaming_us_per_token": round(stream_s / stream_tokens * 1e6, 3),
        "state_bytes": sb,
    }


def main() -> None:
    batches = rio.load_core()
    case = [c for c in build_split_cases(batches, "S1") if c.name == "b1_to_b2"][0]
    fit_pool = routine_traces(case.fit_traces, "cb")
    targets = list(case.target_traces)

    rows = []
    rows.append(
        bench(
            "CAND-A wgm g1 L5-15 w8",
            "wgm",
            {"metric": "g1", "layers": "middle_late", "window_width": 8},
            fit_pool,
            targets,
            lambda st: StreamingWGM(st.mu, st.sd, st.centre, range(5, 16), 8),
        )
    )
    rows.append(
        bench(
            "CAND-B pdm d1 L5-11 w4",
            "pdm",
            {"model": "d1", "layers": "middle", "window_width": 4},
            fit_pool,
            targets,
            lambda st: StreamingPDM(
                st.log_depth, st.log_depth_initial, st.mean["u"], st.sd["u"], range(5, 12), 4
            ),
        )
    )
    # reduced variants for the frontier cost column
    rows.append(
        bench(
            "CAND-A wgm g1 L(3 layers) w8",
            "wgm",
            {"metric": "g1", "layers": [7, 9, 11], "window_width": 8},
            fit_pool,
            targets,
            lambda st: StreamingWGM(st.mu, st.sd, st.centre, [7, 9, 11], 8),
        )
    )
    rows.append(
        bench(
            "CAND-B pdm d1 L(3 layers) w4",
            "pdm",
            {"model": "d1", "layers": [7, 9, 11], "window_width": 4},
            fit_pool,
            targets,
            lambda st: StreamingPDM(
                st.log_depth, st.log_depth_initial, st.mean["u"], st.sd["u"], [7, 9, 11], 4
            ),
        )
    )
    payload = {"torch_threads": 6, "rows": rows}
    (OUT / "cost_bench.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for r in rows:
        print(
            f"{r['name']:32s} fit={r['fit_seconds']:7.3f}s "
            f"batch={r['batch_us_per_token']:8.3f} us/tok "
            f"feat={r['feature_only_us_per_token']:8.3f} "
            f"stream={r['streaming_us_per_token']:8.3f} "
            f"state={r['state_bytes']['total']/1024:8.1f} KiB"
        )


if __name__ == "__main__":
    main()
