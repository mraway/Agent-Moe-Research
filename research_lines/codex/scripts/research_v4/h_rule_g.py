#!/usr/bin/env python3
"""Look-based survival and the H rule on the FILTERED normal pool of dataset G.

    h_rule_g.py [--out artifacts/agent_v2/dataset_g/h_rule]

This is the recomputation that design section 15.1 requires *before* any attack
routing is unsealed: ``H = the largest number of LOOKS at which at least
``--min-survivors`` filtered G-cal paths are still alive``.  It uses the detector
harness's own definitions -- ``io_g.load_g`` for the episode / channel-tag axis and
``trm3_g.segmented_windows`` for the endpoint grid -- but it computes **no detector
score, no calibration, no p value and no FAR**: the feature matrix handed to
``segmented_windows`` is a zero column, so only the ``ends`` bookkeeping is exercised.

The routing tensors are loaded because the loader always loads them; nothing in this
script reads or summarises them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io_g, trm3_g  # noqa: E402


SUBSETS = ("g_fit", "g_cal")
VIEWS = ("V1", "V2", "V3")
WIDTHS = (8, 4)
TAG_SCOPES = ("message", "body")
LOOK_GRID = (64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448)
TOKEN_GRID = (64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448)


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def endpoint_ends(episode: Any, view: trm3_g.View, width: int) -> np.ndarray:
    """The harness's endpoint grid for one episode, with no statistic attached.

    ``trm3_g.segmented_windows`` is called with a zero [T, 1] feature matrix: the
    returned ``ends`` are exactly the ones the detector would see (they depend only on
    the channel tags, the view and the window width), and no routing value is read.
    """

    tokens = int(episode.token_count)
    features = torch.zeros((tokens, 1), dtype=torch.float64)
    ends, _means, _tags, _ordinals = trm3_g.segmented_windows(
        features, episode.channel_tags, view, width
    )
    return np.asarray(ends, dtype=np.int64)


def describe(values: Sequence[int]) -> dict[str, Any]:
    array = sorted(int(v) for v in values)
    if not array:
        return {"n": 0}
    return {
        "n": len(array),
        "min": array[0],
        "p10": array[max(0, int(round(0.10 * (len(array) - 1))))],
        "p25": array[max(0, int(round(0.25 * (len(array) - 1))))],
        "median": float(statistics.median(array)),
        "p75": array[max(0, int(round(0.75 * (len(array) - 1))))],
        "p90": array[max(0, int(round(0.90 * (len(array) - 1))))],
        "max": array[-1],
        "mean": round(sum(array) / len(array), 3),
        "total": sum(array),
    }


def survival(values: Sequence[int], grid: Sequence[int]) -> dict[str, int]:
    array = np.asarray(list(values), dtype=np.int64)
    return {str(int(k)): int((array >= int(k)).sum()) for k in grid}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "artifacts/agent_v2/dataset_g")
    parser.add_argument("--out", type=Path, default=ROOT / "artifacts/agent_v2/dataset_g/h_rule")
    parser.add_argument("--min-survivors", type=int, default=trm3_g.H_MIN_SURVIVORS)
    args = parser.parse_args()

    root = Path(args.root)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    label_paths = {
        subset: root / "annotations" / subset / "final_unblinded.jsonl" for subset in SUBSETS
    }
    inputs = {
        "min_survivors": int(args.min_survivors),
        "filter_rule": (
            "filter_pass = behavior == 'on_task' and coverage in {'full','partial'} and "
            "material_errors empty and not unauthorized_tool_attempt "
            "(design section 2.3; derived by io_g.normalise_label_row)"
        ),
        "h_rule": (
            "H = largest number of LOOKS at which >= min_survivors filtered G-cal paths "
            "are still alive (trm3_g.h_horizon); a look = one causal-window endpoint of "
            "trm3_g.segmented_windows, which never straddles a channel boundary"
        ),
        "labels": {},
        "mapping": {},
        "packets": {},
    }
    for subset in SUBSETS:
        inputs["labels"][subset] = {
            "path": str(label_paths[subset].relative_to(ROOT)),
            "sha256": sha256(label_paths[subset]),
        }
        mapping = root / "private" / subset / "case_mapping.jsonl"
        inputs["mapping"][subset] = {
            "path": str(mapping.relative_to(ROOT)),
            "sha256": sha256(mapping),
        }
        packet = root / "packets" / subset / "packet.jsonl"
        inputs["packets"][subset] = {
            "path": str(packet.relative_to(ROOT)),
            "sha256": sha256(packet),
        }

    report: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "inputs": inputs,
        "pools": {},
        "cells": [],
        "episodes": [],
    }

    # ---- load -------------------------------------------------------------
    episodes: dict[tuple[str, str], tuple[Any, ...]] = {}
    for scope in TAG_SCOPES:
        for subset in SUBSETS:
            manifest: dict[str, Any] = {}
            t0 = time.time()
            loaded = io_g.load_g(
                root / subset,
                labels=label_paths[subset],
                variants=io_g.NORMAL_VARIANTS,
                tag_scope=scope,
                cache_dir=None,
                manifest=manifest,
            )
            manifest["load_seconds"] = round(time.time() - t0, 2)
            episodes[(scope, subset)] = loaded
            report["pools"][f"{subset}/{scope}"] = manifest
            if any(not e.normal for e in loaded):
                raise SystemExit("a non-normal arm reached the pool")
            if any(e.filter_pass is None for e in loaded):
                raise SystemExit("an episode is missing its quality annotation")
            print(
                f"loaded {subset} scope={scope}: {len(loaded)} episodes "
                f"({manifest['load_seconds']}s)",
                flush=True,
            )

    # ---- per-episode look counts -----------------------------------------
    look_counts: dict[tuple[str, str, str, int], dict[str, int]] = {}
    end_tokens: dict[tuple[str, str, str, int], dict[str, np.ndarray]] = {}
    for scope in TAG_SCOPES:
        for subset in SUBSETS:
            for view_name in VIEWS:
                view = trm3_g.view_of(view_name)
                for width in WIDTHS:
                    counts: dict[str, int] = {}
                    ends_by_key: dict[str, np.ndarray] = {}
                    for episode in episodes[(scope, subset)]:
                        ends = endpoint_ends(episode, view, width)
                        counts[episode.trace_id] = int(ends.size)
                        ends_by_key[episode.trace_id] = ends
                    look_counts[(scope, subset, view_name, width)] = counts
                    end_tokens[(scope, subset, view_name, width)] = ends_by_key
            print(f"endpoints done {subset} scope={scope}", flush=True)

    # ---- episode table ----------------------------------------------------
    for scope in TAG_SCOPES:
        for subset in SUBSETS:
            for episode in episodes[(scope, subset)]:
                row = {
                    "subset": subset,
                    "tag_scope": scope,
                    "episode_id": episode.trace_id,
                    "arm": episode.variant,
                    "filter_pass": bool(episode.filter_pass),
                    "token_count": int(episode.token_count),
                    "step_count": int(episode.step_count),
                    "channel_counts": episode.channel_counts(),
                    "looks": {
                        f"{view_name}_w{width}": look_counts[(scope, subset, view_name, width)][
                            episode.trace_id
                        ]
                        for view_name in VIEWS
                        for width in WIDTHS
                    },
                }
                report["episodes"].append(row)

    # ---- cells ------------------------------------------------------------
    pool_specs = [
        ("g_cal", "filtered", lambda e: bool(e.filter_pass)),
        ("g_fit", "filtered", lambda e: bool(e.filter_pass)),
        ("g_cal", "unfiltered", lambda e: True),
        ("g_fit", "unfiltered", lambda e: True),
    ]
    for scope in TAG_SCOPES:
        for subset, pool_name, predicate in pool_specs:
            pool = [e for e in episodes[(scope, subset)] if predicate(e)]
            keys = [e.trace_id for e in pool]
            token_counts = [int(e.token_count) for e in pool]
            for view_name in VIEWS:
                for width in WIDTHS:
                    counts = look_counts[(scope, subset, view_name, width)]
                    ends_by_key = end_tokens[(scope, subset, view_name, width)]
                    lengths = [counts[k] for k in keys]
                    horizon = trm3_g.h_horizon(lengths, min_survivors=int(args.min_survivors))
                    h = int(horizon["H"])
                    token_at_h: list[int] = []
                    if h >= 1:
                        for key in keys:
                            ends = ends_by_key[key]
                            if ends.size >= h:
                                token_at_h.append(int(ends[h - 1]))
                    censored_paths = int(horizon["censored_paths"])
                    total_paths = len(pool)
                    cell = {
                        "tag_scope": scope,
                        "subset": subset,
                        "pool": pool_name,
                        "view": view_name,
                        "window_width": width,
                        "paths": total_paths,
                        "arms": {
                            arm: sum(1 for e in pool if e.variant == arm)
                            for arm in sorted({e.variant for e in pool})
                        },
                        "look_distribution": describe(lengths),
                        "token_distribution": describe(token_counts),
                        "looks_per_token": (
                            round(sum(lengths) / sum(token_counts), 4) if token_counts else None
                        ),
                        "survival_looks": survival(lengths, LOOK_GRID),
                        "survival_tokens": survival(token_counts, TOKEN_GRID),
                        "H": h,
                        "survivors_at_H": int(horizon["survivors_at_H"]),
                        "min_survivors": int(horizon["min_survivors"]),
                        "margin": int(horizon["survivors_at_H"]) - int(horizon["min_survivors"]),
                        "censored_paths": censored_paths,
                        "censored_path_fraction": (
                            round(censored_paths / total_paths, 4) if total_paths else None
                        ),
                        "paths_ending_before_H": total_paths - int(horizon["survivors_at_H"]),
                        "paths_ending_before_H_fraction": (
                            round((total_paths - int(horizon["survivors_at_H"])) / total_paths, 4)
                            if total_paths
                            else None
                        ),
                        "censored_endpoints": int(horizon["censored_endpoints"]),
                        "total_endpoints": int(horizon["total_endpoints"]),
                        "censored_endpoint_fraction": (
                            round(
                                int(horizon["censored_endpoints"]) / int(horizon["total_endpoints"]),
                                4,
                            )
                            if int(horizon["total_endpoints"])
                            else None
                        ),
                        "token_of_Hth_look": describe(token_at_h) if token_at_h else {"n": 0},
                        "largest_grid_k_with_min_survivors": max(
                            (k for k in LOOK_GRID if sum(1 for v in lengths if v >= k) >= int(args.min_survivors)),
                            default=None,
                        ),
                    }
                    # -- gate, tail and robustness -----------------------------
                    order = sorted(lengths, reverse=True)
                    floor = int(args.min_survivors)
                    cell["gate_H_min"] = 128
                    cell["gate_pass_looks"] = bool(h >= 128)
                    cell["survivors_at_gate_128_looks"] = int(
                        sum(1 for v in lengths if v >= 128)
                    )
                    cell["gate_margin_paths_at_128"] = (
                        cell["survivors_at_gate_128_looks"] - floor
                    )
                    cell["tail_ranks"] = {
                        str(rank): int(order[rank - 1])
                        for rank in range(max(1, floor - 5), min(len(order), floor + 6) + 1)
                        if rank <= len(order)
                    }
                    cell["H_if_longest_dropped"] = {
                        str(drop): (int(order[floor - 1 + drop]) if floor - 1 + drop < len(order) else 0)
                        for drop in (1, 2, 3, 5, 10)
                    }
                    survivors_keys = [k for k in keys if counts[k] >= h] if h else []
                    survivor_arms: dict[str, int] = {}
                    for episode in pool:
                        if episode.trace_id in set(survivors_keys):
                            survivor_arms[episode.variant] = survivor_arms.get(episode.variant, 0) + 1
                    cell["survivor_arms_at_H"] = dict(sorted(survivor_arms.items()))
                    # token-axis reference (the proxy used by the annotation report)
                    token_order = np.sort(np.asarray(token_counts, dtype=np.int64))[::-1]
                    token_h = int(token_order[floor - 1]) if token_order.size >= floor else 0
                    cell["token_axis_proxy"] = {
                        "H_token_raw": token_h,
                        "survivors_at_H_token": int((token_order >= token_h).sum()) if token_h else 0,
                        "survivors_at_token_384": int((token_order >= 384).sum()),
                        "token_needed_for_H_looks_median": (
                            cell["token_of_Hth_look"].get("median") if h else None
                        ),
                    }
                    report["cells"].append(cell)
            print(f"cells done {subset}/{pool_name} scope={scope}", flush=True)

    out_path = out_dir / "h_rule_g.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
