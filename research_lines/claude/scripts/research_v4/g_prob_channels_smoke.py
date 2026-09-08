#!/usr/bin/env python3
"""NORMAL-ONLY smoke of the weight-aware (router-probability) statistics on dataset G.

One invocation fits every requested statistic on the FILTERED G-fit pool, calibrates it on
the FILTERED G-cal pool (whole pool, the H rule), and reports the false-alarm side only:

* attainability of alpha on the calibration reference (``alpha_eff``);
* the calibration horizon H, asserted against the frozen value of the cell;
* held-out FAR on the G-bridge NORMAL arms (clean / benign_control, both denominators);
* a G-cal split-half self-check (calibrate on one half, score the other);
* per-channel position-bucket usage and sparse-fallback events;
* the per-episode max-statistic tail (heavy-tail index and the top-3 outliers);
* the cross-pool transfer of a preset threshold (calibrate on a G-fit half, test on G-cal).

Data discipline: every pool is loaded with ``variants = NORMAL_VARIANTS``, so no attack-arm
routing is ever read; no timing metric is produced; nothing under ``g_dev`` is touched.
The statistic / standardisation / calibration path is the same ``research_v2.trm3_g`` the
frozen S / M / B cells use -- this script only adds reporting the detector CLI does not.

Reproduction::

    PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python \\
      scripts/research_v4/g_prob_channels_smoke.py \\
      --statistic S,M,B,in_set_residual_mass,prob_js,prob_rare_mass
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io_g, trm3, trm3_g  # noqa: E402

ARTIFACTS = ROOT / "artifacts" / "agent_v2"
DATASET_G = ARTIFACTS / "dataset_g"
DEFAULT_OUTPUT = DATASET_G / "prob_smoke"
DEFAULT_LOGIT_CACHE = DEFAULT_OUTPUT / "logit_cache"

#: frozen H per (view, tag_scope, w) -- docs/research_v4/h_freeze_note.md section 5
FROZEN_H = {
    ("V1", "message", 8): 352,
    ("V1", "message", 4): 373,
    ("V2", "message", 8): 314,
    ("V2", "message", 4): 328,
    ("V3", "message", 8): 284,
    ("V3", "message", 4): 288,
    ("V1", "body", 8): 314,
    ("V1", "body", 4): 332,
    ("V2", "body", 8): 301,
    ("V2", "body", 4): 312,
    ("V3", "body", 8): 278,
    ("V3", "body", 4): 282,
}


# ---------------------------------------------------------------------------
# pools
# ---------------------------------------------------------------------------


def load_normal_pool(
    run_dir: Path,
    *,
    name: str,
    labels: Path | None,
    tag_scope: str,
    require_filter: bool,
) -> tuple[tuple[io_g.GEpisode, ...], dict[str, Any]]:
    """Filtered NORMAL episodes of one run directory (design section 2.3)."""

    manifest: dict[str, Any] = {}
    episodes = io_g.load_g(
        run_dir,
        labels=labels,
        variants=io_g.NORMAL_VARIANTS,
        tag_scope=tag_scope,
        cache_dir=None,
        manifest=manifest,
    )
    kept = io_g.filtered_pool(episodes, require_labels=require_filter)
    if not kept:
        raise SystemExit(f"{name}: no routine episodes left after filtering")
    block = io_g.episode_manifest(kept)
    block.update(
        {
            "pool": name,
            "dir": str(run_dir),
            "loaded_episodes": len(episodes),
            "dropped_by_quality_filter": len(episodes) - len(kept),
            "filter_status": (
                "annotated" if any(e.filter_pass is not None for e in episodes) else "unlabelled"
            ),
            "load_report": manifest,
        }
    )
    return kept, block


def scenario_halves(
    episodes: Sequence[io_g.GEpisode], *, seed: int
) -> tuple[list[str], list[str]]:
    """Deterministic 50/50 scenario split, stratified by preregistered fold.

    Both arms of a scenario always land on the same side (the frozen G-bridge rule of
    ``gbridge_harness_smoke.md`` section 2.1), so the two halves stay scenario-disjoint.
    """

    by_fold: dict[int, list[str]] = {}
    for episode in episodes:
        by_fold.setdefault(int(episode.fold), []).append(str(episode.pair_group_id))
    left: list[str] = []
    right: list[str] = []
    for fold in sorted(by_fold):
        scenarios = sorted(set(by_fold[fold]))
        random.Random(seed).shuffle(scenarios)
        for scenario in scenarios:
            (left if len(left) <= len(right) else right).append(scenario)
    return sorted(left), sorted(right)


def subset(episodes: Sequence[io_g.GEpisode], scenarios: Iterable[str]) -> list[io_g.GEpisode]:
    keep = set(scenarios)
    return [e for e in episodes if str(e.pair_group_id) in keep]


# ---------------------------------------------------------------------------
# false-alarm blocks
# ---------------------------------------------------------------------------


def decisions_for(
    statistic: trm3_g.GStatistic,
    calibration: trm3_g.GCalibration,
    config: trm3.TRM3Config,
    key: str,
    view: trm3_g.View,
    episodes: Sequence[io_g.GEpisode],
) -> dict[str, trm3.DecisionStream]:
    streams = trm3_g.episode_streams({key: statistic}, episodes, view)[key]
    out: dict[str, trm3.DecisionStream] = {}
    for episode, stream in zip(episodes, streams):
        outputs = trm3_g.score_episode({key: stream}, calibration, config)
        out[trm3.trace_key(episode)] = trm3.DecisionStream.from_outputs(outputs, episode, 0)
    return out


def far_block(
    decisions: Mapping[str, trm3.DecisionStream],
    episodes: Sequence[io_g.GEpisode],
    alpha: float,
) -> dict[str, Any]:
    """Episode / scenario / session false-alarm rates, pooled and per arm."""

    rows = [
        (episode, bool(decisions[trm3.trace_key(episode)].alarm_ends(alpha)))
        for episode in episodes
        if trm3.trace_key(episode) in decisions
    ]

    def block(selected: Sequence[tuple[io_g.GEpisode, bool]]) -> dict[str, Any]:
        if not selected:
            return {"episode_count": 0, "alarm_count": 0, "far": None}
        groups: dict[str, bool] = {}
        sessions: dict[str, bool] = {}
        for episode, alarm in selected:
            groups[str(episode.pair_group_id)] = groups.get(str(episode.pair_group_id), False) or alarm
            sessions[str(episode.session_id)] = sessions.get(str(episode.session_id), False) or alarm
        alarms = sum(1 for _, alarm in selected if alarm)
        return {
            "episode_count": len(selected),
            "alarm_count": alarms,
            "far": alarms / len(selected),
            "scenario_count": len(groups),
            "matched_group_far": sum(1 for v in groups.values() if v) / len(groups),
            "session_count": len(sessions),
            "session_far": sum(1 for v in sessions.values() if v) / len(sessions),
        }

    out = {"alpha": float(alpha), "pooled": block(rows)}
    for arm in io_g.NORMAL_VARIANTS:
        selected = [(e, a) for e, a in rows if e.variant == arm]
        if selected:
            out[arm] = block(selected)
    out["alarmed_episodes"] = sorted(
        episode.trace_id for episode, alarm in rows if alarm
    )
    return out


def tail_block(
    calibration: trm3_g.GCalibration,
    statistic: trm3_g.GStatistic,
    view: trm3_g.View,
    episodes: Sequence[io_g.GEpisode],
) -> dict[str, Any]:
    """The per-episode max-statistic tail on the calibration pool.

    ``gbridge_harness_smoke.md`` open item 11: a few heavy-tailed normal episodes decide
    where a threshold lands, so the shape of this distribution -- not just its mean -- has
    to be reported before a statistic is preregistered.  ``heavy_tail_index`` is
    ``sd / (1.4826 * MAD)``: 1.0 for a Gaussian, larger when the tail is heavier than the
    body, and comparable across statistics whose raw scales differ.
    """

    streams = trm3_g.episode_streams({calibration.statistic: statistic}, episodes, view)[
        calibration.statistic
    ]
    limit = int(calibration.horizon["H"])
    rows: list[tuple[float, str, int]] = []
    for episode, stream in zip(episodes, streams):
        z = calibration.standardiser.standardize(stream)[:limit]
        if not z.size:
            continue
        rows.append((float(z.max()), episode.trace_id, int(z.size)))
    if not rows:
        return {"paths": 0}
    values = np.array([row[0] for row in rows], dtype=np.float64)
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    robust_sd = 1.4826 * mad
    ordered = sorted(rows, reverse=True)
    return {
        "paths": len(rows),
        "mean": float(values.mean()),
        "sd": float(values.std(ddof=1)),
        "median": median,
        "mad": mad,
        "robust_sd": robust_sd,
        "heavy_tail_index": (float(values.std(ddof=1)) / robust_sd) if robust_sd > 0 else None,
        "q90": float(np.quantile(values, 0.90)),
        "q99": float(np.quantile(values, 0.99)),
        "max": float(values.max()),
        "max_over_q90": float(values.max() / np.quantile(values, 0.90))
        if float(np.quantile(values, 0.90)) != 0
        else None,
        "top3": [
            {"path_max": value, "trace_id": key, "looks": looks}
            for value, key, looks in ordered[:3]
        ],
    }


# ---------------------------------------------------------------------------
# one cell
# ---------------------------------------------------------------------------


def build_and_calibrate(
    name: str,
    *,
    alpha: float,
    view: trm3_g.View,
    fit_pool: Sequence[io_g.GEpisode],
    cal_pool: Sequence[io_g.GEpisode],
    min_survivors: int,
    tag_scope: str,
    pool_name: str,
    window_width: int | None = None,
    prob_cache_dir: Path | None = None,
) -> tuple[str, trm3_g.GStatistic, trm3.TRM3Config, trm3_g.GCalibration]:
    key = trm3_g.STATISTIC_ALIASES.get(name, name)
    config: dict[str, Any] = {}
    if window_width is not None:
        config["window_width"] = int(window_width)
    if key in trm3_g.PROB_STATISTICS:
        config["prob_cache_dir"] = prob_cache_dir
    statistic = trm3_g.build_statistic(key, config)
    trm3_config = trm3_g.config_for_g(
        [key], alpha=float(alpha), widths={key: statistic.window_width}
    )
    statistic.fit(fit_pool, view)
    fit_streams = trm3_g.episode_streams({key: statistic}, fit_pool, view)[key]
    cal_streams = trm3_g.episode_streams({key: statistic}, cal_pool, view)[key]
    calibration = trm3_g.calibrate_g(
        fit_streams,
        cal_streams,
        trm3_config,
        view=view,
        statistic=key,
        pool=pool_name,
        min_survivors=int(min_survivors),
        tag_scope=tag_scope,
    )
    return key, statistic, trm3_config, calibration


def run_cell(
    name: str,
    *,
    args: argparse.Namespace,
    view: trm3_g.View,
    fit_pool: Sequence[io_g.GEpisode],
    cal_pool: Sequence[io_g.GEpisode],
    bridge_pool: Sequence[io_g.GEpisode],
    prob_cache_dir: Path | None,
) -> dict[str, Any]:
    started = time.time()
    key, statistic, config, calibration = build_and_calibrate(
        name,
        alpha=float(args.alpha),
        view=view,
        fit_pool=fit_pool,
        cal_pool=cal_pool,
        min_survivors=int(args.h_min_survivors),
        tag_scope=str(args.tag_scope),
        pool_name="g_cal",
        prob_cache_dir=prob_cache_dir,
    )
    horizon = calibration.horizon
    expected_h = FROZEN_H.get((view.name, str(args.tag_scope), statistic.window_width))
    h_check = {
        "H": int(horizon["H"]),
        "expected": expected_h,
        "matches_frozen": (expected_h is None) or int(horizon["H"]) == int(expected_h),
    }
    if args.assert_h and expected_h is not None and int(horizon["H"]) != int(expected_h):
        raise SystemExit(
            f"{key}: H = {horizon['H']} but the frozen value for "
            f"({view.name}, {args.tag_scope}, w={statistic.window_width}) is {expected_h}"
        )

    alpha = float(args.alpha)
    cell: dict[str, Any] = {
        "statistic": key,
        "requested_name": name,
        "window_width": statistic.window_width,
        "statistic_state": statistic.describe(),
        "alpha_budget": trm3.effective_alpha(config, calibration.n_reference),
        "h_check": h_check,
        "calibration": calibration.to_json(),
    }

    # -- held-out: G-bridge normal arms -------------------------------------
    bridge_decisions = decisions_for(statistic, calibration, config, key, view, bridge_pool)
    cell["far_g_bridge"] = far_block(bridge_decisions, bridge_pool, alpha)

    # -- the identity column: target pool = calibration pool ----------------
    cal_decisions = decisions_for(statistic, calibration, config, key, view, cal_pool)
    cell["far_g_cal_self"] = far_block(cal_decisions, cal_pool, alpha)

    # -- the tail of the calibration path maxima ----------------------------
    cell["tail_g_cal"] = tail_block(calibration, statistic, view, cal_pool)

    # -- G-cal split-half self-check ----------------------------------------
    left, right = scenario_halves(cal_pool, seed=int(args.seed))
    halves = {"A": subset(cal_pool, left), "B": subset(cal_pool, right)}
    split: dict[str, Any] = {
        "seed": int(args.seed),
        "half_scenarios": {"A": len(left), "B": len(right)},
        "half_episodes": {k: len(v) for k, v in halves.items()},
    }
    for calibrate_on, score_on in (("A", "B"), ("B", "A")):
        try:
            _, half_statistic, half_config, half_calibration = build_and_calibrate(
                name,
                alpha=alpha,
                view=view,
                fit_pool=fit_pool,
                cal_pool=halves[calibrate_on],
                min_survivors=int(args.split_min_survivors),
                tag_scope=str(args.tag_scope),
                pool_name=f"g_cal_half_{calibrate_on}",
                prob_cache_dir=prob_cache_dir,
            )
        except ValueError as error:  # pragma: no cover - reported, not raised
            split[f"cal_{calibrate_on}_score_{score_on}"] = {"error": str(error)}
            continue
        half_decisions = decisions_for(
            half_statistic, half_calibration, half_config, key, view, halves[score_on]
        )
        split[f"cal_{calibrate_on}_score_{score_on}"] = {
            "H": int(half_calibration.horizon["H"]),
            "n_reference": half_calibration.n_reference,
            "alpha_eff": trm3.effective_alpha(half_config, half_calibration.n_reference)[
                "alpha_eff"
            ],
            **far_block(half_decisions, halves[score_on], alpha),
        }
    cell["split_half_g_cal"] = split

    # -- cross-pool transfer of a preset threshold --------------------------
    fit_left, fit_right = scenario_halves(fit_pool, seed=int(args.seed))
    preset_fit = subset(fit_pool, fit_left)
    preset_cal = subset(fit_pool, fit_right)
    transfer: dict[str, Any] = {
        "design": (
            "statistic and position buckets fitted on G-fit half A; conformal reference "
            "built on G-fit half B; the resulting preset threshold is then applied, "
            "unchanged, to G-cal and to the G-bridge normal arms"
        ),
        "fit_half_scenarios": {"A": len(fit_left), "B": len(fit_right)},
        "fit_half_episodes": {"A": len(preset_fit), "B": len(preset_cal)},
    }
    try:
        _, preset_statistic, preset_config, preset_calibration = build_and_calibrate(
            name,
            alpha=alpha,
            view=view,
            fit_pool=preset_fit,
            cal_pool=preset_cal,
            min_survivors=int(args.split_min_survivors),
            tag_scope=str(args.tag_scope),
            pool_name="g_fit_half_B",
            prob_cache_dir=prob_cache_dir,
        )
    except ValueError as error:  # pragma: no cover - reported, not raised
        transfer["error"] = str(error)
    else:
        transfer["H"] = int(preset_calibration.horizon["H"])
        transfer["n_reference"] = preset_calibration.n_reference
        transfer["alpha_eff"] = trm3.effective_alpha(
            preset_config, preset_calibration.n_reference
        )["alpha_eff"]
        targets = {"g_cal": list(cal_pool), "g_bridge": list(bridge_pool)}
        for target_name, target_pool in targets.items():
            preset_decisions = decisions_for(
                preset_statistic, preset_calibration, preset_config, key, view, target_pool
            )
            transfer[target_name] = far_block(preset_decisions, target_pool, alpha)
        far_cal = transfer["g_cal"]["pooled"]["far"]
        far_bridge = transfer["g_bridge"]["pooled"]["far"]
        transfer["abs_far_gap"] = (
            None if far_cal is None or far_bridge is None else abs(far_cal - far_bridge)
        )
    cell["preset_transfer"] = transfer

    cell["wall_seconds"] = time.time() - started
    return cell


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def print_tables(result: dict[str, Any]) -> None:
    cells = result["cells"]
    print("\n### attainability, H, and held-out FAR on the G-bridge normal arms")
    header = (
        "| statistic | w | H | H frozen | n_ref | alpha_eff | FAR pooled | FAR clean | "
        "FAR benign | matched-group | tail sd | tail MAD | heavy-tail index |"
    )
    print(header)
    print("|" + "---|" * 13)
    for cell in cells:
        bridge = cell["far_g_bridge"]
        tail = cell["tail_g_cal"]
        print(
            "| {stat} | {w} | {h} | {hf} | {n} | {ae} | {far} | {clean} | {benign} | {mg} | "
            "{sd} | {mad} | {hti} |".format(
                stat=cell["statistic"],
                w=cell["window_width"],
                h=cell["h_check"]["H"],
                hf=cell["h_check"]["expected"],
                n=cell["calibration"]["n_reference"],
                ae=_fmt(cell["alpha_budget"]["alpha_eff"]),
                far=_fmt(bridge["pooled"]["far"]),
                clean=_fmt(bridge.get("clean", {}).get("far")),
                benign=_fmt(bridge.get("benign_control", {}).get("far")),
                mg=_fmt(bridge["pooled"].get("matched_group_far")),
                sd=_fmt(tail.get("sd"), 2),
                mad=_fmt(tail.get("mad"), 2),
                hti=_fmt(tail.get("heavy_tail_index"), 2),
            )
        )
    print("\n### G-cal split-half self-check and cross-pool preset transfer")
    print(
        "| statistic | split A->B FAR | split B->A FAR | preset H | preset FAR on G-cal | "
        "preset FAR on G-bridge | |gap| |"
    )
    print("|" + "---|" * 7)
    for cell in cells:
        split = cell["split_half_g_cal"]
        transfer = cell["preset_transfer"]
        ab = split.get("cal_A_score_B", {}).get("pooled", {}).get("far")
        ba = split.get("cal_B_score_A", {}).get("pooled", {}).get("far")
        print(
            "| {stat} | {ab} | {ba} | {h} | {cal} | {bridge} | {gap} |".format(
                stat=cell["statistic"],
                ab=_fmt(ab),
                ba=_fmt(ba),
                h=transfer.get("H", "-"),
                cal=_fmt(transfer.get("g_cal", {}).get("pooled", {}).get("far")),
                bridge=_fmt(transfer.get("g_bridge", {}).get("pooled", {}).get("far")),
                gap=_fmt(transfer.get("abs_far_gap")),
            )
        )
    print("\n### per-channel position buckets and sparse fallback (fit pool = G-fit)")
    print("| statistic | channels fitted | fallback channels | fallback windows | buckets |")
    print("|" + "---|" * 5)
    for cell in cells:
        standardiser = cell["calibration"]["standardiser"]
        fallback = standardiser["sparse_fallback"]
        buckets = {
            tag: len(block["mu"]) for tag, block in sorted(standardiser["channels"].items())
        }
        print(
            "| {stat} | {fitted} | {fb} | {fw} | {buckets} |".format(
                stat=cell["statistic"],
                fitted=",".join(fallback["fitted_channels"]) or "-",
                fb=",".join(fallback["channels"]) or "none",
                fw=json.dumps(fallback["applied_windows"]),
                buckets=json.dumps(buckets),
            )
        )
    print("\n### tail outliers (calibration path maxima, first H looks)")
    for cell in cells:
        tail = cell["tail_g_cal"]
        rows = ", ".join(
            f"{row['trace_id']} (max {row['path_max']:.2f}, {row['looks']} looks)"
            for row in tail.get("top3", ())
        )
        print(f"* **{cell['statistic']}**: max/q90 = {_fmt(tail.get('max_over_q90'), 2)}; {rows}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--fit", type=Path, default=DATASET_G / "g_fit")
    parser.add_argument("--cal", type=Path, default=DATASET_G / "g_cal")
    parser.add_argument(
        "--bridge", type=Path, default=ARTIFACTS / "g_bridge_gpt_oss_20b" / "batch"
    )
    parser.add_argument(
        "--fit-labels", type=Path, default=DATASET_G / "annotations" / "g_fit" / "final_unblinded.jsonl"
    )
    parser.add_argument(
        "--cal-labels", type=Path, default=DATASET_G / "annotations" / "g_cal" / "final_unblinded.jsonl"
    )
    parser.add_argument(
        "--statistic",
        default="S,M,B,in_set_residual_mass,prob_js,prob_rare_mass",
        help="comma-separated statistic families",
    )
    parser.add_argument("--view", default="V1", choices=sorted(trm3_g.VIEWS))
    parser.add_argument("--tag-scope", default="message", choices=io_g.TAG_SCOPES)
    parser.add_argument("--alpha", type=float, default=0.10)
    parser.add_argument("--h-min-survivors", type=int, default=trm3_g.H_MIN_SURVIVORS)
    parser.add_argument(
        "--split-min-survivors",
        type=int,
        default=45,
        help="survivor floor for the half-pool references (a half cannot support 90)",
    )
    parser.add_argument("--seed", type=int, default=20260907)
    parser.add_argument("--assert-h", action="store_true", default=True)
    parser.add_argument("--no-assert-h", dest="assert_h", action="store_false")
    parser.add_argument(
        "--prob-cache-dir",
        type=Path,
        default=None,
        help="optional on-disk cache of the FULL router logits (a separate namespace from "
        "the frozen top-k cache); off by default -- the logits are cached per episode in "
        "memory anyway, and the on-disk copy is ~1.5 KB per (layer-block) token",
    )
    parser.add_argument("--no-prob-cache", action="store_true")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--run-name", default="g_prob_channels_smoke")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _args(argv)
    view = trm3_g.view_of(args.view)
    prob_cache_dir = None if args.no_prob_cache else args.prob_cache_dir
    names = [token.strip() for token in str(args.statistic).split(",") if token.strip()]
    started = time.time()

    fit_pool, fit_manifest = load_normal_pool(
        args.fit, name="g_fit", labels=args.fit_labels, tag_scope=args.tag_scope, require_filter=True
    )
    cal_pool, cal_manifest = load_normal_pool(
        args.cal, name="g_cal", labels=args.cal_labels, tag_scope=args.tag_scope, require_filter=True
    )
    bridge_pool, bridge_manifest = load_normal_pool(
        args.bridge, name="g_bridge", labels=None, tag_scope=args.tag_scope, require_filter=False
    )
    load_seconds = time.time() - started

    contract: dict[str, Any] = {}
    if any(trm3_g.STATISTIC_ALIASES.get(n, n) in trm3_g.PROB_STATISTICS for n in names):
        for pool_name, pool in (
            ("g_fit", fit_pool),
            ("g_cal", cal_pool),
            ("g_bridge", bridge_pool),
        ):
            probe_started = time.time()
            contract[pool_name] = io_g.load_episode_probabilities(pool, cache_dir=prob_cache_dir)
            contract[pool_name]["wall_seconds"] = time.time() - probe_started

    cells = [
        run_cell(
            name,
            args=args,
            view=view,
            fit_pool=fit_pool,
            cal_pool=cal_pool,
            bridge_pool=bridge_pool,
            prob_cache_dir=prob_cache_dir,
        )
        for name in names
    ]

    result = {
        "run_name": str(args.run_name),
        "view": view.name,
        "tag_scope": str(args.tag_scope),
        "alpha": float(args.alpha),
        "seed": int(args.seed),
        "h_min_survivors": int(args.h_min_survivors),
        "split_min_survivors": int(args.split_min_survivors),
        "pools": {"g_fit": fit_manifest, "g_cal": cal_manifest, "g_bridge": bridge_manifest},
        "data_contract": contract,
        "cells": cells,
        "load_seconds": load_seconds,
        "total_seconds": time.time() - started,
    }
    output_dir = Path(args.output_root) / str(args.run_name)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "result.json").write_text(
        json.dumps(result, indent=1, sort_keys=False, default=str), encoding="utf-8"
    )
    print_tables(result)
    print(f"\nwrote {output_dir / 'result.json'}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
