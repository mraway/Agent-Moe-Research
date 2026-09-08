"""Shared loading / window bookkeeping for the whitening lens (read-only diagnosis).

Reproduces CAND-A's fitted state (WGMScorer g1, layers 5-15, w=8, global centre,
variance floor 1e-3, identity transform, trace_equal_weight=False) and additionally
runs the *same* fit through the ``metric="g2"`` path so that the preregistered
``rank="auto"`` PCA subspace is available for the decomposition.  Nothing here is a
detector: every window set below is selected with post-hoc labels/anchors.
"""

from __future__ import annotations

import json
import string
from dataclasses import dataclass
from pathlib import Path

import torch

from research_v2 import io as rio
from research_v2.scorers.wgm import WGMScorer

REPO = Path("/home/wzh/Agent-Moe-Research/.claude/worktrees/algorithm-research-proposals-427363")
LABELS = REPO / "docs/research_v2/labels/product_onset_v1_adjudicated.jsonl"
CACHE = REPO / "artifacts/agent_v2/research_v2/zoom_code_blindspot"

LAYERS = tuple(range(5, 16))          # CAND-A middle_late
WIDTH = 8
VAR_FLOOR = 1e-3
D = len(LAYERS) * 64                  # 704
WINDOW_SPAN = 48                      # onset .. onset+48 (task definition)

PROG_IDS = (
    "b1-f2-012", "b1-f2-014", "b1-f3-016",
    "b2-f2-011", "b2-f2-012", "b2-f2-014", "b2-f2-015", "b2-f3-020",
)


def base_scorer(metric: str = "g1") -> WGMScorer:
    return WGMScorer(
        window_width=WIDTH,
        layers="middle_late",
        metric=metric,
        rank="auto",
        transform="identity",
        centre="global",
        variance_floor=VAR_FLOOR,
        trace_equal_weight=False,
    )


SCORER = base_scorer("g1")


def load_all():
    batches = rio.load_core()
    return batches


def labels():
    rows = [json.loads(l) for l in LABELS.read_text().splitlines() if l.strip()]
    return {r["trace_id"].split("--")[0][:9] if False else r["trace_id"]: r for r in rows}


def label_by_short():
    rows = [json.loads(l) for l in LABELS.read_text().splitlines() if l.strip()]
    out = {}
    for r in rows:
        short = "-".join(r["trace_id"].split("-")[:3])
        out[r["trace_id"]] = r
        out.setdefault(short, r)
    return out


def short_id(trace_id: str) -> str:
    return "-".join(trace_id.split("-")[:3])


def windows_of(trace) -> tuple[torch.Tensor, torch.Tensor]:
    """(ends [N], windows [N, 704]) -- exactly CAND-A's feature."""
    return SCORER._windows(trace)


# --- token-class helpers (prose / structured split, defined on token classes) ---
JSON_CHARS = ("{", '"', ":")
PUNCT = set(string.punctuation) | {"“", "”", "‘", "’", "—", "–", "…"}


def token_flags(texts: list[str]) -> tuple[list[bool], list[bool]]:
    """(has_json_char, is_digit_or_punct) per token."""
    json_flags, dp_flags = [], []
    for tok in texts:
        json_flags.append(any(c in tok for c in JSON_CHARS))
        core = tok.strip()
        if not core:
            dp_flags.append(False)
            continue
        has_alpha = any(c.isalpha() for c in core)
        has_dp = any(c.isdigit() or c in PUNCT for c in core)
        dp_flags.append((not has_alpha) and has_dp)
    return json_flags, dp_flags


def prose_window_mask(trace, ends: torch.Tensor) -> torch.Tensor:
    """True for windows whose 8 decoded tokens carry no '{', '\"', ':' and < 30% digit/punct tokens."""
    texts = rio.decode_token_texts(trace.token_ids.tolist())
    json_flags, dp_flags = token_flags(texts)
    mask = torch.zeros(ends.numel(), dtype=torch.bool)
    for i, e in enumerate(ends.tolist()):
        lo = e - WIDTH + 1
        seg_json = json_flags[lo : e + 1]
        seg_dp = dp_flags[lo : e + 1]
        if any(seg_json):
            continue
        if sum(seg_dp) / float(WIDTH) >= 0.30:
            continue
        mask[i] = True
    return mask
