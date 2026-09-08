#!/usr/bin/env python3
"""Run the TRM-3 detector families on dataset G (Agent v3 / gpt-oss episodes).

One invocation = one preregistered cell: (view, statistic, alpha) fitted on ``--fit``,
calibrated on ``--cal`` and scored on ``--target``.  The sequential-conformal core is the
frozen ``research_v2.trm3``; everything dataset G changes (episode token axis, harmony
channel views, channel-conditioned standardisation, whole-pool calibration, the H rule)
lives in ``research_v2.trm3_g`` and ``research_v2.io_g``.

Examples
--------
Routine-only smoke on the P0 probe (attack arms are refused by the loader)::

    python scripts/research_v4/run_detectors_g.py \
        --fit artifacts/agent_v2/agent_v3_p0/batch --fit-scenarios 001,011,016,021 \
        --cal artifacts/agent_v2/agent_v3_p0/batch --cal-scenarios 036,041,066,071 \
        --target artifacts/agent_v2/agent_v3_p0/batch \
        --view V1 --statistic M --alpha 0.10 \
        --h-min-survivors 6 --min-bucket-traces 3 --normal-only-smoke

Gate F7 needs a G-SESSION target (prereg 9.2 / 7.5): ``--session-turns-config`` is read per
``pair_group_id``, so on a G-dev target every session falls back to the global
``--session-turns`` and ``session.configured_turn_sessions`` is 0.  The session cell is a
run of its own::

    python scripts/research_v4/run_detectors_g.py \
        --fit    artifacts/agent_v2/dataset_g/g_fit \
        --cal    artifacts/agent_v2/dataset_g/g_cal \
        --target artifacts/agent_v2/dataset_g/g_session \
        --fit-labels    .../annotations/g_fit/final_unblinded.jsonl \
        --cal-labels    .../annotations/g_cal/final_unblinded.jsonl \
        --target-labels .../annotations/g_session/final_unblinded.jsonl \
        --view V1 --tag-scope message --statistic S --alpha 0.10 --window-s 8 \
        --session-alpha 0.10 --session-turns-config configs/dataset_g/g_session.json \
        --outputs all --require-quality-labels

``--outputs all`` is what writes ``outputs.jsonl``: one row per look with ``p``, ``p_inst``,
``hysteresis_state`` / ``_e0`` / ``_segment``, ``view``, ``statistic``, ``episode_index`` and
``session_id`` -- the input of prereg 2.8 / section 14 item 4.  ``--outputs primary`` writes
``result.json`` only.

Data discipline
---------------
* a probe run (``dataset_role == p0_probe_not_data``) never yields attack-arm episodes,
  and any pool drawn from one requires ``--normal-only-smoke``;
* ``--normal-only-smoke`` additionally refuses every attack-arm episode in every pool and
  computes false-alarm material only;
* without labels there are no positives, and the recall blocks say so;
* the three pools take THEIR OWN label files (``--fit-labels`` / ``--cal-labels`` /
  ``--target-labels``; ``--labels`` is the legacy single-file form and is still accepted),
  and every file's sha256 lands in ``result.json`` (prereg item 47 / section 15 item 3);
* a non-smoke run passes the freeze guard: clean working tree, ``HEAD == --freeze-commit``,
  ``--prereg-sha256`` and ``--labels-sha256`` matching the files on disk (item 43), and it
  asserts the frozen H, the per-channel attainability floor and the 24-layer band (items
  13 / 15 / 7) before any metric is written.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from research_v2 import io_g, trm3, trm3_g  # noqa: E402

DEFAULT_OUTPUT_ROOT = ROOT / "artifacts" / "agent_v2" / "research_v4" / "detectors_g"
#: the FROZEN preregistration the guard hashes (freeze review, blocking item PREREG_PATH:
#: this used to point at ``detector_prereg_v3_1_draft.md``, so section 19.7's own reproduce
#: command -- which passes the sha256 of the frozen file -- was refused by the guard).
#: ``tests/test_research_v4_prereg_v3_1.py`` pins the file name.
PREREG_PATH = ROOT / "docs" / "research_v4" / "detector_prereg_v3_1.md"


# ---------------------------------------------------------------------------
# provenance and the freeze guard (prereg section 15, item 43)
# ---------------------------------------------------------------------------


def sha256_file(path: Path) -> str | None:
    path = Path(path)
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def git_output(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()


def working_tree_status() -> list[str]:
    """``git status --porcelain`` minus the untracked ``artifacts`` symlink line."""

    lines = [line for line in git_output("status", "--porcelain").splitlines() if line.strip()]
    return [line for line in lines if line.strip().split(maxsplit=1)[-1].strip('"') != "artifacts"]


def freeze_guard(
    args: argparse.Namespace, label_hashes: Mapping[str, Any]
) -> dict[str, Any]:
    """Prereg section 15: no target metric before the lead's freeze commit.

    Mirrors ``scripts/research_v3/run_trm3.py:data_discipline_guard`` and adds the two
    hash checks section 15 requires: the preregistration file and the label files must be
    exactly the ones the freeze commit names.  A ``--normal-only-smoke`` run records the
    same block and is never refused (it produces false-alarm material only).
    """

    dirty = working_tree_status()
    head = git_output("rev-parse", "HEAD")
    resolved = git_output("rev-parse", args.freeze_commit) if args.freeze_commit else None
    prereg_sha = sha256_file(PREREG_PATH)
    block: dict[str, Any] = {
        "head": head,
        "dirty": bool(dirty),
        "dirty_entries": dirty,
        "freeze_commit_requested": args.freeze_commit,
        "freeze_commit_resolved": resolved,
        "head_is_freeze_commit": bool(resolved) and resolved == head,
        "prereg_path": str(PREREG_PATH),
        "prereg_sha256": prereg_sha,
        "prereg_sha256_expected": args.prereg_sha256,
        "prereg_sha256_matches": (
            None if not args.prereg_sha256 else prereg_sha == args.prereg_sha256
        ),
        "labels_sha256_expected": list(args.labels_sha256 or ()),
        "label_sha256": {
            name: entry.get("sha256") for name, entry in label_hashes.items()
        },
        "normal_only_smoke": bool(args.normal_only_smoke),
        "rule": (
            "prereg section 15: a non-smoke run requires a clean working tree (ignoring the "
            "'artifacts' symlink), HEAD == --freeze-commit, --prereg-sha256 equal to the "
            "preregistration on disk and every --labels-sha256 present among the label files"
        ),
    }
    present = {value for value in block["label_sha256"].values() if value}
    missing = [value for value in (args.labels_sha256 or ()) if value not in present]
    block["labels_sha256_missing"] = missing
    if args.normal_only_smoke:
        block["enforced"] = False
        return block
    block["enforced"] = True
    if dirty:
        raise SystemExit(
            "freeze guard: the working tree is not clean, so a non-smoke run is refused "
            "(prereg section 15 item 2).  Uncommitted entries:\n  " + "\n  ".join(dirty)
        )
    if not args.freeze_commit:
        raise SystemExit(
            "freeze guard: --freeze-commit is required for a non-smoke run (prereg section "
            "15 item 2); before the freeze use --normal-only-smoke"
        )
    if not resolved:
        raise SystemExit(f"freeze guard: unknown --freeze-commit {args.freeze_commit!r}")
    if resolved != head:
        raise SystemExit(
            f"freeze guard: HEAD ({head}) is not the freeze commit ({resolved}); refusing"
        )
    if args.prereg_sha256 and prereg_sha != args.prereg_sha256:
        raise SystemExit(
            f"freeze guard: --prereg-sha256 {args.prereg_sha256} does not match "
            f"{PREREG_PATH} ({prereg_sha}); refusing"
        )
    if missing:
        raise SystemExit(
            "freeze guard: --labels-sha256 values not found among the label files: "
            f"{missing}; the label files present are {sorted(present)}"
        )
    return block


# ---------------------------------------------------------------------------
# pools
# ---------------------------------------------------------------------------


def _split(value: str | None) -> list[str] | None:
    if value is None:
        return None
    tokens = [token.strip() for token in value.split(",") if token.strip()]
    return tokens or None


def pool_labels(args: argparse.Namespace, pool: str) -> Path | None:
    """The label file of one pool (item 47).

    The primary cell fits on G-fit, calibrates on G-cal and scores G-dev, and those are
    THREE different ``final_unblinded.jsonl`` files.  ``--labels`` (one file for all three)
    is kept for the smoke runs and as the "merged file" option of prereg section 19; a
    per-pool switch overrides it.  Without a pool's labels its episodes carry no quality
    annotation, ``filtered_pool`` cannot filter, and the 288 / 279 pools do not exist --
    which is why :func:`routine_pool` refuses that combination outside a smoke run.
    """

    return _pool_specific_labels(args, pool) or args.labels


def _pool_specific_labels(args: argparse.Namespace, pool: str) -> Path | None:
    return {"fit": args.fit_labels, "cal": args.cal_labels, "target": args.target_labels}[pool]


def label_provenance(args: argparse.Namespace) -> dict[str, Any]:
    """``{pool: {path, sha256, rows}}`` for every label file actually used."""

    out: dict[str, Any] = {}
    for pool in ("fit", "cal", "target"):
        path = pool_labels(args, pool)
        if path is None:
            out[pool] = {"path": None, "sha256": None, "rows": 0, "source": "none"}
            continue
        path = Path(path)
        rows = 0
        if path.exists():
            rows = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        out[pool] = {
            "path": str(path),
            "sha256": sha256_file(path),
            "rows": rows,
            "source": "pool" if _pool_specific_labels(args, pool) else "shared",
        }
    return out


def load_pool(
    dirs: Sequence[Path],
    *,
    name: str,
    scenarios: Sequence[str] | None,
    labels: Path | None,
    tag_scope: str,
    cache_dir: Path | None,
    variants: Sequence[str] | None = None,
) -> tuple[tuple[io_g.GEpisode, ...], dict[str, Any]]:
    episodes: list[io_g.GEpisode] = []
    reports: list[dict[str, Any]] = []
    for directory in dirs:
        manifest: dict[str, Any] = {}
        episodes.extend(
            io_g.load_g(
                directory,
                labels=labels,
                variants=variants,
                scenarios=scenarios,
                tag_scope=tag_scope,
                cache_dir=cache_dir,
                manifest=manifest,
            )
        )
        reports.append(manifest)
    block = io_g.episode_manifest(episodes)
    block["pool"] = name
    block["dirs"] = [str(d) for d in dirs]
    block["scenarios_filter"] = list(scenarios or ())
    block["load_reports"] = reports
    block["dataset_roles"] = sorted({e.dataset_role for e in episodes})
    return tuple(episodes), block


def routine_pool(
    episodes: Sequence[io_g.GEpisode],
    *,
    name: str,
    require_filter: bool,
    smoke: bool = False,
) -> tuple[tuple[io_g.GEpisode, ...], dict[str, Any]]:
    """Design section 2.3: the fitting and calibration pools are filtered normal traffic.

    Item 47: when ``--require-quality-labels`` is on and the pool carries no annotation at
    all, the filter silently degrades to "keep everything" -- the 288 / 279 pools of the
    preregistration would then not be the pools that ran.  A non-smoke run refuses instead.
    """

    normals = io_g.normal_episodes(episodes)
    annotated = sum(1 for e in normals if e.filter_pass is not None)
    degraded = bool(require_filter and not annotated)
    if degraded and not smoke:
        raise SystemExit(
            f"{name}: --require-quality-labels was requested but no episode of this pool "
            "carries a quality annotation; pass this pool's final_unblinded.jsonl "
            f"(--{name}-labels) or run with --normal-only-smoke (prereg item 47)"
        )
    kept = io_g.filtered_pool(normals, require_labels=require_filter and not degraded)
    report = {
        "pool": name,
        "input_episodes": len(episodes),
        "normal_episodes": len(normals),
        "dropped_non_normal": len(episodes) - len(normals),
        "dropped_by_quality_filter": len(normals) - len(kept),
        "filter_status": "annotated" if annotated else "unlabelled",
        "annotated_episodes": annotated,
        "quality_filter_requested": bool(require_filter),
        # smoke only: an unlabelled pool would otherwise empty out entirely
        "quality_filter_degraded": degraded,
        "kept": len(kept),
    }
    if not kept:
        raise SystemExit(f"{name}: no routine episodes left after filtering")
    return kept, report


# ---------------------------------------------------------------------------
# one cell
# ---------------------------------------------------------------------------


def statistic_config(args: argparse.Namespace, name: str) -> dict[str, Any]:
    """Per-family construction config.

    Items 34 / 35: the weight-aware families (``R`` / ``J`` / ``RM``) are ordinary members
    of the registry here -- ``.get`` on the width table instead of an indexing that only
    knew the four selection families, plus the ``--prob-cache-dir`` they need for the FULL
    router logits (a separate cache namespace from the frozen top-k one).
    """

    config: dict[str, Any] = {}
    widths = {
        "S": args.window_s,
        "M": args.window_m,
        "P": args.window_p,
        "B": args.window_b,
        "R": args.window_prob,
        "J": args.window_prob,
        "RM": args.window_prob,
    }
    width = widths.get(name)
    if width is not None:
        config["window_width"] = int(width)
    layers = _split(args.layers)
    if layers:
        config["layers"] = tuple(int(v) for v in layers)
    if name in ("S",):
        config["rare_threshold"] = float(args.rare_threshold)
    if name in trm3_g.PROB_STATISTICS:
        config["prob_cache_dir"] = None if args.no_prob_cache else args.prob_cache_dir
    return config


def tertile_cutpoints(args: argparse.Namespace) -> tuple[int, int] | None:
    """Item 24: the FROZEN filtered-G-cal length cutpoints, never re-derived on the target."""

    if args.tertile_cutpoints_from_target:
        return None
    values = _split(args.tertile_cutpoints)
    if values:
        if len(values) != 2:
            raise SystemExit("--tertile-cutpoints takes exactly two comma-separated integers")
        return (int(values[0]), int(values[1]))
    return trm3_g.G_CAL_TERTILE_CUTPOINTS


def tolerance_bands(args: argparse.Namespace) -> tuple[int, ...]:
    """Item 20: the anchor tolerance family, ``0 / 4 / 5 / 8`` by default."""

    values = _split(args.tolerance_bands)
    return tuple(int(v) for v in values) if values else trm3_g.TOLERANCE_BANDS


_SESSION_TURNS_CACHE: dict[str, dict[str, int]] = {}


def session_turns_map(args: argparse.Namespace) -> dict[str, int] | None:
    """Item 33: ``{pair_group_id: factory.session_turns}`` from an experiment config."""

    if not args.session_turns_config:
        return None
    cached = _SESSION_TURNS_CACHE.get(str(args.session_turns_config))
    if cached is not None:
        return cached
    payload = json.loads(Path(args.session_turns_config).read_text(encoding="utf-8"))
    scenarios = payload.get("scenarios") or payload.get("fixtures") or []
    out: dict[str, int] = {}
    for scenario in scenarios:
        group = str(scenario.get("pair_group_id", "") or "")
        turns = (scenario.get("factory") or {}).get("session_turns")
        if group and isinstance(turns, int):
            out[group] = int(turns)
        elif group and isinstance(turns, list):
            out[group] = len(turns)
    if not out:
        raise SystemExit(
            f"--session-turns-config {args.session_turns_config} carries no "
            "scenarios[*].factory.session_turns"
        )
    _SESSION_TURNS_CACHE[str(args.session_turns_config)] = out
    return out


def hysteresis_summary(
    tracks: Mapping[str, dict[str, Any]],
    episodes: Sequence[io_g.GEpisode],
    args: argparse.Namespace,
) -> dict[str, Any]:
    """Descriptive roll-up of ``p_inst`` and the hysteresis machine (items 31 / 32).

    Never used for an alarm, a false-alarm rate or the conformal guarantee -- prereg
    section 2.7 and lead ruling 5.  Reported by trajectory class, which is the comparison
    the prereg asks for (does RECOVERING finally fire on bounded resistance?).
    """

    by_key = {trm3.trace_key(e): e for e in episodes}
    states: dict[str, int] = {}
    by_class: dict[str, dict[str, Any]] = {}
    entries = 0
    exits = 0
    for key, track in tracks.items():
        episode = by_key.get(key)
        klass = str(((episode.labels if episode else {}) or {}).get("trajectory_class") or "")
        klass = klass or f"unlabelled_{episode.variant if episode else 'unknown'}"
        block = by_class.setdefault(
            klass,
            {"episode_count": 0, "states": {}, "entries": 0, "exits": 0, "re_entries": 0},
        )
        final = str(track["final_state"])
        block["episode_count"] += 1
        block["states"][final] = block["states"].get(final, 0) + 1
        block["entries"] += len(track["entries"])
        block["exits"] += len(track["exits"])
        block["re_entries"] += max(0, int(track["segment_count"]) - 1)
        states[final] = states.get(final, 0) + 1
        entries += len(track["entries"])
        exits += len(track["exits"])
    earliest = [
        track["earliest_decision_end_final"]
        for track in tracks.values()
        if track["earliest_decision_end_final"] is not None
    ]
    return {
        "d": int(args.temporal_d),
        "enter": float(args.alpha),
        "exit": float(args.temporal_exit),
        "episodes": len(tracks),
        "episodes_with_entry": sum(1 for t in tracks.values() if t["entries"]),
        "episodes_with_exit": sum(1 for t in tracks.values() if t["exits"]),
        "entries": entries,
        "exits": exits,
        "final_states": dict(sorted(states.items())),
        "by_trajectory_class": {k: v for k, v in sorted(by_class.items())},
        "earliest_decision_end_median": (
            None if not earliest else float(sorted(earliest)[len(earliest) // 2])
        ),
        "note": (
            "prereg 2.7 / lead ruling 5: p_inst is descriptive only -- no alarm, no FAR, "
            "no change to the conformal guarantee; entry is the first alarm (p <= alpha), "
            "exit is D consecutive endpoints with p_inst > the provisional level"
        ),
    }


def cell_config(args: argparse.Namespace, key: str, width: int) -> trm3.TRM3Config:
    """The ``TRM3Config`` of one cell, including the optional OR arm (item 36)."""

    if args.or_arm:
        arm = trm3_g.STATISTIC_ALIASES.get(args.or_arm, args.or_arm)
        if arm != key:
            return trm3_g.config_for_g(
                [key, arm],
                alphas={key: float(args.alpha), arm: float(args.alpha_extra)},
                widths={key: int(width)},
                emit_evidence=bool(args.attribution),
                temporal_d=int(args.temporal_d),
            )
    return trm3_g.config_for_g(
        [key],
        alpha=float(args.alpha),
        widths={key: int(width)},
        emit_evidence=bool(args.attribution),
        temporal_d=int(args.temporal_d),
    )


def frozen_assertions(
    args: argparse.Namespace,
    *,
    key: str,
    width: int,
    calibration: trm3_g.GCalibration,
    config: trm3.TRM3Config,
    statistic: trm3_g.GStatistic,
) -> list[dict[str, Any]]:
    """Items 13 / 15 / 7: H, attainability and the layer band, asserted before metrics.

    Returns one row per check.  ``main`` raises ``SystemExit`` when any row is not ``ok``
    and the run is not a smoke run; the rows land in ``result.json`` either way.
    """

    rows: list[dict[str, Any]] = []
    expected = (
        int(args.expect_h)
        if args.expect_h is not None
        else trm3_g.frozen_h(args.tag_scope, args.view, int(width))
    )
    measured = int(calibration.horizon["H"])
    # freeze review (should-fix 5): an UNTABLED cell used to pass silently, so
    # ``--window-s 6`` (or any w outside {4, 8}) asserted nothing at all.  Outside a smoke
    # run an untabled cell is now a failure, which ``main`` turns into SystemExit; passing
    # ``--expect-h`` explicitly is the documented escape.
    untabled = expected is None
    rows.append(
        {
            "check": "horizon_H",
            "cell": [str(args.tag_scope), str(args.view), int(width)],
            "expected": expected,
            "observed": measured,
            "ok": (
                measured == expected
                if not untabled
                else bool(args.normal_only_smoke)
            ),
            "source": "docs/research_v4/h_freeze_note.md section 8 (prereg 2.6 table)",
            "note": (
                "no frozen value for this cell -- the 12-cell table covers w in {4, 8}; a "
                "non-smoke run is refused (pass --expect-h to assert an explicit value)"
                if untabled
                else ""
            ),
        }
    )
    attain = trm3_g.attainability(config, calibration.n_reference, floor=int(args.attainability_floor))
    rows.append(
        {
            "check": "attainability",
            "expected": f"floor((n+1)*alpha_c) >= {int(args.attainability_floor)} for every channel",
            "observed": attain,
            "ok": bool(attain["ok"]),
            "source": "prereg 2.5 / gate N4 / item 15",
        }
    )
    layers = list(statistic.describe().get("layers") or [])
    rows.append(
        {
            "check": "layer_band",
            "expected": list(trm3_g.ALL_LAYERS) if args.expect_all_layers else layers,
            "observed": layers,
            "ok": (not args.expect_all_layers) or layers == list(trm3_g.ALL_LAYERS),
            "source": "prereg note 1 of 2026-09-07 / item 7 (all 24 MoE layers)",
        }
    )
    rows.append(
        {
            "check": "n_reference",
            "expected": int(args.expect_n_reference) if args.expect_n_reference else None,
            "observed": int(calibration.n_reference),
            "ok": (not args.expect_n_reference)
            or int(calibration.n_reference) == int(args.expect_n_reference),
            "source": "prereg section 15 item 4",
        }
    )
    rows.append(
        {
            "check": "tag_scope",
            "expected": str(args.tag_scope),
            "observed": str(calibration.tag_scope),
            "ok": str(calibration.tag_scope) == str(args.tag_scope),
            "source": "prereg section 15 item 4",
        }
    )
    return rows


def target_pool_assertions(
    args: argparse.Namespace, target_pool: Sequence[io_g.GEpisode]
) -> list[dict[str, Any]]:
    """Freeze review (ARM IDENTITY): the G-dev target must expose all FIVE arms.

    ``io_g.load_g`` recovers ``benign_lexical`` / ``legitimate_refusal`` from the subset
    config; if that join were to silently stop working, every one of those 48 episodes
    would fall back into ``clean`` and prereg section 4's denominators, gate F2's second
    conjunct and section 15.3 item 7 would all be wrong WITHOUT any error.  A non-smoke run
    on the whole G-dev target therefore asserts the census

        clean 192 / benign_control 192 / benign_lexical 24 / legitimate_refusal 24 /
        attack 352

    A scenario-filtered or smoke run records the row and does not enforce it.
    """

    subsets = sorted(
        {
            Path(path).stem
            for path in (io_g.subset_config_for_run(d) for d in args.target)
            if path is not None
        }
    )
    observed = {}
    for episode in target_pool:
        observed[episode.variant] = observed.get(episode.variant, 0) + 1
    is_g_dev = subsets == ["g_dev"]
    filtered = bool(_split(args.target_scenarios)) or bool(args.normal_only_smoke)
    enforced = is_g_dev and not filtered
    return [
        {
            "check": "target_variant_census",
            "target_subsets": subsets,
            "expected": dict(io_g.G_DEV_VARIANT_COUNTS) if enforced else None,
            "observed": dict(sorted(observed.items())),
            "ok": (not enforced) or observed == dict(io_g.G_DEV_VARIANT_COUNTS),
            "enforced": enforced,
            "source": "prereg section 4 as corrected by the freeze review (ARM IDENTITY)",
            "note": (
                ""
                if enforced
                else (
                    "not enforced: "
                    + (
                        "the target is not the whole G-dev subset"
                        if not is_g_dev
                        else "a scenario filter or --normal-only-smoke is in force"
                    )
                )
            ),
        }
    ]


def run_cell(
    statistic_name: str,
    *,
    args: argparse.Namespace,
    view: trm3_g.View,
    fit_pool: Sequence[io_g.GEpisode],
    cal_pool: Sequence[io_g.GEpisode],
    target_pool: Sequence[io_g.GEpisode],
) -> dict[str, Any]:
    started = time.time()
    key = trm3_g.STATISTIC_ALIASES.get(statistic_name, statistic_name)
    statistic = trm3_g.build_statistic(key, statistic_config(args, key))
    config = cell_config(args, key, statistic.window_width)
    statistics = {key: statistic}
    arm_key = None
    if len(config.channels) > 1:
        arm_key = next(spec.name for spec in config.channels if spec.name != key)
        statistics[arm_key] = trm3_g.build_statistic(arm_key, statistic_config(args, arm_key))
    for name, fitted in statistics.items():
        fitted.fit(fit_pool, view)
    fit_seconds = time.time() - started

    fit_streams_by_name = trm3_g.episode_streams(statistics, fit_pool, view)
    cal_streams_by_name = trm3_g.episode_streams(statistics, cal_pool, view)
    target_streams_by_name = trm3_g.episode_streams(statistics, target_pool, view)
    fit_streams = fit_streams_by_name[key]
    cal_streams = cal_streams_by_name[key]
    target_streams = target_streams_by_name[key]

    calibrations = {
        name: trm3_g.calibrate_g(
            fit_streams_by_name[name],
            cal_streams_by_name[name],
            trm3_g.config_for_g([name], alpha=float(args.alpha)),
            view=view,
            statistic=name,
            pool=args.cal_name,
            min_survivors=int(args.h_min_survivors),
            bucket_size=int(args.bucket_size),
            min_bucket_traces=int(args.min_bucket_traces),
            min_channel_windows=int(args.min_channel_windows),
            min_channel_traces=int(args.min_channel_traces),
            pooled_fallback=not args.strict_channel_buckets,
            tag_scope=str(args.tag_scope),
            standardise=not args.no_standardise,
        )
        for name in statistics
    }
    calibration = calibrations[key]
    if arm_key is not None:
        # one reference per channel, one H: the OR arm is decided on the same look budget
        merged = dict(calibration.reference.channels)
        merged[arm_key] = calibrations[arm_key].reference.channels[arm_key]
        calibration.reference.channels = merged
        calibration.reference.k_cal = {
            name: int(calibration.horizon["H"]) for name in statistics
        }

    assertions = frozen_assertions(
        args,
        key=key,
        width=int(statistic.window_width),
        calibration=calibration,
        config=config,
        statistic=statistic,
    )

    scoring_started = time.time()
    outputs_by_key: dict[str, list[trm3.TokenOutput]] = {}
    decisions: dict[str, trm3.DecisionStream] = {}
    hysteresis: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    standardisers = {name: calibrations[name].standardiser for name in statistics}
    for index, episode in enumerate(target_pool):
        streams = {name: target_streams_by_name[name][index] for name in statistics}
        outputs = trm3_g.score_episode(
            streams,
            calibration,
            config,
            statistics=statistics if args.attribution else None,
            episode=episode,
            standardisers=standardisers,
        )
        for output in outputs:
            # trm3.online stamps the half-calibration id; dataset G calibrates on the whole
            # pool, so the row carries the GCalibration version instead.
            output.calibration_version = calibration.version
        episode_key = trm3.trace_key(episode)
        outputs_by_key[episode_key] = outputs
        decisions[episode_key] = trm3.DecisionStream.from_outputs(outputs, episode, 0)
        track = trm3_g.episode_hysteresis(
            streams[key],
            calibration,
            config,
            outputs,
            channel=key,
            d=int(args.temporal_d),
            enter=float(args.alpha),
            exit_threshold=float(args.temporal_exit),
        )
        hysteresis[episode_key] = track
        if args.outputs == "all":
            p_inst = track["p_inst"]
            by_end = {int(end): position for position, end in enumerate(track["ends"])}
            for output in outputs:
                row = output.schema_row(
                    trace_id=episode.trace_id,
                    batch=episode.batch,
                    arm=episode.variant,
                    klass=episode.variant,
                    calibration=f"{args.cal_name}|{view.name}|{key}",
                )
                row["view"] = view.name
                row["statistic"] = key
                row["episode_index"] = episode.episode_index
                row["session_id"] = episode.session_id
                position = by_end.get(int(output.end))
                row["p_inst"] = None if position is None else p_inst[position]
                row["hysteresis_state"] = (
                    None if position is None else track["state"][position]
                )
                row["hysteresis_e0"] = None if position is None else track["e0"][position]
                row["hysteresis_segment"] = (
                    None if position is None else track["segment"][position]
                )
                rows.append(row)
    scoring_seconds = time.time() - scoring_started

    anchors = trm3_g.view_anchors(
        target_pool,
        view,
        e_denominator_arms=None if args.e_denominator_all_arms else trm3_g.E_DENOMINATOR_ARMS,
    )
    metrics = trm3_g.evaluate_g(
        outputs_by_key,
        target_pool,
        config,
        view,
        anchors=anchors,
        session_alpha=float(args.session_alpha),
        session_turns=int(args.session_turns),
        decisions=decisions,
        tertile_cutpoints=tertile_cutpoints(args),
        bands=tolerance_bands(args),
        turns_by_scenario=session_turns_map(args),
    )
    metrics["hysteresis"] = hysteresis_summary(hysteresis, target_pool, args)
    normal_keys = [
        trm3.trace_key(e) for e in target_pool if e.variant in io_g.NORMAL_VARIANTS
    ]
    # the grid is reported on BOTH denominators of prereg section 4: `all` is every normal
    # input, `filtered` is the quality-filtered pool gate F1 judges and the one the matched
    # operating point uses (freeze review 8).
    matched_keys, matched_denominator = matching_normal_keys(target_pool)
    alpha_grid = {
        f"{alpha:g}": {
            "alpha": alpha,
            "measured_far": trm3_g.measured_far(decisions, normal_keys, alpha),
            "measured_far_filtered": trm3_g.measured_far(decisions, matched_keys, alpha),
        }
        for alpha in (0.05, 0.10, 0.15)
    }
    alpha_grid["denominators"] = {
        "all": {"normal_count": len(normal_keys), "name": "normal_union"},
        "filtered": matched_denominator,
    }
    endpoints = sum(len(v) for v in outputs_by_key.values())
    attributed = [
        output
        for outputs in outputs_by_key.values()
        for output in outputs
        if output.top_coordinates
    ]
    ends_by_key = {
        episode_key: [int(o.end) for o in outputs if not o.horizon_censored]
        for episode_key, outputs in outputs_by_key.items()
    }
    return {
        "statistic": key,
        "statistic_state": statistic.describe(),
        "or_arm": (
            None
            if arm_key is None
            else {
                "statistic": arm_key,
                "alpha_extra": float(args.alpha_extra),
                "state": statistics[arm_key].describe(),
                "horizon": dict(calibrations[arm_key].horizon),
                "rule": "alarm iff p_primary <= alpha or p_arm <= alpha_extra (prereg 11.1)",
            }
        ),
        "config": config.to_json(),
        "alpha_budget": trm3.effective_alpha(config, calibration.n_reference),
        "attainability": trm3_g.attainability(
            config, calibration.n_reference, floor=int(args.attainability_floor)
        ),
        "assertions": assertions,
        "calibration": calibration.to_json(),
        "metrics": metrics,
        "alpha_grid": alpha_grid,
        "anchors": {k: v.to_json() for k, v in sorted(anchors.items())},
        # NOTE: this block stays exactly ``{"tag_scope": ...} | sparse_fallback_json()``;
        # the A-raw switch is reported in ``ablation`` below so the shape is unchanged.
        "standardisation": {
            "tag_scope": str(args.tag_scope),
            **calibration.standardiser.sparse_fallback_json(),
        },
        "ablation": {
            "standardised": not args.no_standardise,
            "name": "A-raw (raw full-path maximum, no position buckets)"
            if args.no_standardise
            else None,
            "e_denominator_all_arms": bool(args.e_denominator_all_arms),
            "tertile_source": (
                "target_pool_thirds" if args.tertile_cutpoints_from_target else "frozen_g_cal"
            ),
        },
        "attribution": {
            "enabled": bool(args.attribution),
            "top_n": int(config.top_coordinates),
            "endpoints_with_coordinates": len(attributed),
            "example": attributed[0].top_coordinates if attributed else [],
        },
        "stream_shapes": {
            "fit_endpoints": [len(s) for s in fit_streams],
            "cal_endpoints": [len(s) for s in cal_streams],
            "target_endpoints": [len(s) for s in target_streams],
        },
        "cost": {
            "fit_seconds": fit_seconds,
            "scoring_seconds": scoring_seconds,
            "scored_endpoints": endpoints,
            "seconds_per_1000_endpoints": (
                None if not endpoints else 1000.0 * scoring_seconds / endpoints
            ),
        },
        "_decisions": decisions,
        "_anchors": anchors,
        "_ends": ends_by_key,
        "_rows": rows,
    }


#: name of the denominator the matched operating point is chosen on, recorded in
#: ``result.json`` so prereg 15.3 item 5 can be checked mechanically.
MATCHED_FAR_DENOMINATOR = "filtered_normal_union_no_legitimate_refusal"
MATCHED_FAR_DENOMINATOR_FALLBACK = "normal_union_unlabelled_fallback"


def matching_normal_keys(
    target_pool: Sequence[io_g.GEpisode],
) -> tuple[list[str], dict[str, Any]]:
    """The normal denominator the H1 operating point is matched on (freeze review 8).

    Prereg 2.5 grants exchangeability on the QUALITY-FILTERED normal pool, and prereg
    section 4 keeps ``legitimate_refusal`` out of every false-alarm denominator, so the
    matched measured FAR is taken over

        filtered(clean union benign_control union benign_lexical)

    with ``filter_pass is True`` only -- an unannotated episode (``None``) is NEVER counted,
    which is the difference from ``evaluate_g``'s ``far.filtered`` block
    (``filter_pass is not False``).  A pool with no quality annotation at all (a smoke run)
    would leave that empty, so it falls back to the unfiltered normal union and says so.
    """

    normals = [e for e in target_pool if e.variant in io_g.NORMAL_VARIANTS]
    filtered = [e for e in normals if e.filter_pass is True]
    used, name = (
        (filtered, MATCHED_FAR_DENOMINATOR)
        if filtered
        else (normals, MATCHED_FAR_DENOMINATOR_FALLBACK)
    )
    block = {
        "denominator": name,
        "normal_count": len(used),
        "normal_union_count": len(normals),
        "filtered_count": len(filtered),
        "unlabelled_normal_episodes": sum(1 for e in normals if e.filter_pass is None),
        "excluded_legitimate_refusal": sum(
            1 for e in target_pool if e.variant == io_g.LEGITIMATE_REFUSAL
        ),
        "by_variant": {
            variant: sum(1 for e in used if e.variant == variant)
            for variant in io_g.NORMAL_VARIANTS
        },
        "rule": (
            "prereg 7.4 as fixed by the freeze review: the matched measured FAR is taken "
            "on the FILTERED normal union (filter_pass is True; None never counted) and "
            "legitimate_refusal is excluded from it"
        ),
    }
    return [trm3.trace_key(e) for e in used], block


def compare_cells(
    primary: dict[str, Any],
    secondary: dict[str, Any],
    target_pool: Sequence[io_g.GEpisode],
    *,
    alpha: float,
    horizon: int = trm3_g.PRIMARY_HORIZON,
    replicates: int = 2000,
    band: int = 0,
    convention: str = "penalty",
) -> dict[str, Any]:
    """The H1 comparison: matched MEASURED FAR, cluster bootstrap, exact McNemar.

    Item 27 (the defect this fixes): the matched alpha used to be reported and then thrown
    away -- the secondary's hits still came from its NOMINAL-alpha metrics block, so the
    comparison was not the "matched measured FAR" one the preregistration decides H1 on.
    Here the secondary's hits are recomputed from its alpha-free ``DecisionStream`` at the
    matched alpha (``trm3_g.hits_at_alpha``), and BOTH rows are reported: ``matched`` is the
    primary row of prereg section 7.4, ``nominal`` is the co-reported one.

    Item 28: the point estimate and interval are clustered on ``attack_family_id`` (16
    families, the conservative primary unit of lead ruling 4); the ``(family, wording
    tier)`` = 48-cluster robustness column is computed next to it.
    """

    normal_keys, denominator = matching_normal_keys(target_pool)
    far_a = trm3_g.measured_far(primary["_decisions"], normal_keys, alpha)
    far_b_nominal = trm3_g.measured_far(secondary["_decisions"], normal_keys, alpha)
    matched = (
        None
        if far_a is None
        else trm3_g.matched_alpha_by_measured_far(
            secondary["_decisions"], normal_keys, far_a
        )
    )
    anchors = primary["_anchors"]
    families = {
        trm3.trace_key(e): (e.attack_family_id or e.pair_group_id) for e in target_pool
    }
    tiers = {
        trm3.trace_key(e): f"{e.attack_family_id or e.pair_group_id}|{e.wording_tier}"
        for e in target_pool
    }

    def _bootstrap(hits_a: Mapping[str, bool], hits_b: Mapping[str, bool]) -> dict[str, Any]:
        block = trm3_g.cluster_bootstrap_paired(
            hits_a, hits_b, families, replicates=replicates
        )
        block["robustness_48_cluster"] = trm3_g.cluster_bootstrap_paired(
            hits_a, hits_b, tiers, replicates=replicates
        )
        return block

    hits_a = trm3_g.hits_at_alpha(
        primary["_decisions"],
        anchors,
        primary["_ends"],
        alpha,
        horizon=horizon,
        band=band,
        convention=convention,
    )
    hits_b_nominal = trm3_g.hits_at_alpha(
        secondary["_decisions"],
        anchors,
        secondary["_ends"],
        alpha,
        horizon=horizon,
        band=band,
        convention=convention,
    )
    rows: dict[str, Any] = {
        "nominal": {
            "alpha_primary": float(alpha),
            "alpha_secondary": float(alpha),
            "measured_far_primary": far_a,
            "measured_far_secondary": far_b_nominal,
            "bootstrap": _bootstrap(hits_a, hits_b_nominal),
        }
    }
    if matched is not None:
        matched["denominator"] = denominator["denominator"]
        matched["denominator_detail"] = denominator
        hits_b_matched = trm3_g.hits_at_alpha(
            secondary["_decisions"],
            anchors,
            secondary["_ends"],
            float(matched["alpha"]),
            horizon=horizon,
            band=band,
            convention=convention,
        )
        rows["matched"] = {
            "alpha_primary": float(alpha),
            "alpha_secondary": float(matched["alpha"]),
            "measured_far_primary": far_a,
            "measured_far_secondary": matched["measured_far"],
            "bootstrap": _bootstrap(hits_a, hits_b_matched),
        }
    return {
        "horizon": horizon,
        "alpha": alpha,
        "band": int(band),
        "hit_convention": convention,
        "primary": primary["statistic"],
        "secondary": secondary["statistic"],
        "measured_far_primary": far_a,
        "matched_alpha_secondary": matched,
        "normal_denominator": denominator,
        "primary_row": "matched" if "matched" in rows else "nominal",
        "rows": rows,
        # kept at the old key so existing readers keep working: the PRIMARY row
        "bootstrap": rows.get("matched", rows["nominal"])["bootstrap"],
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fit", type=Path, action="append", required=True, help="G-fit run directory")
    parser.add_argument("--cal", type=Path, action="append", required=True, help="G-cal run directory")
    parser.add_argument("--target", type=Path, action="append", required=True, help="target run directory")
    parser.add_argument("--fit-scenarios", default=None, help="comma-separated pair_group_id filter")
    parser.add_argument("--cal-scenarios", default=None)
    parser.add_argument("--target-scenarios", default=None)
    parser.add_argument("--view", default="V1", choices=sorted(trm3_g.VIEWS))
    parser.add_argument(
        "--statistic",
        default="M",
        help="statistic family; comma-separated runs several cells.  Selection families: "
        "S (rare-coordinate surprisal), P (marginal surprisal, the H1 baseline), "
        "M (WGM g1), B (CAND-B depth chain).  Weight-aware families (need the FULL router "
        "logits, see --prob-cache-dir): R / in_set_residual_mass, J / prob_js, "
        "RM / prob_rare_mass",
    )
    parser.add_argument("--compare-statistic", default=None, help="second family for the matched-FAR comparison")
    parser.add_argument("--alpha", type=float, default=trm3.ALPHA)
    parser.add_argument(
        "--labels", type=Path, default=None, help="routing-blind annotation JSONL for every pool"
    )
    parser.add_argument(
        "--fit-labels", type=Path, default=None, help="annotation JSONL of the FITTING pool (overrides --labels)"
    )
    parser.add_argument(
        "--cal-labels", type=Path, default=None, help="annotation JSONL of the CALIBRATION pool"
    )
    parser.add_argument(
        "--target-labels", type=Path, default=None, help="annotation JSONL of the TARGET pool"
    )
    parser.add_argument("--normal-only-smoke", action="store_true")
    parser.add_argument("--h-min-survivors", type=int, default=trm3_g.H_MIN_SURVIVORS)
    parser.add_argument("--bucket-size", type=int, default=trm3_g.BUCKET_SIZE)
    parser.add_argument("--min-bucket-traces", type=int, default=trm3_g.MIN_BUCKET_TRACES)
    parser.add_argument(
        "--min-channel-windows",
        type=int,
        default=trm3_g.MIN_CHANNEL_WINDOWS,
        help="a fit-pool channel with fewer windows than this falls back to the pooled "
        "all-channel position buckets (prereg note 3 of 2026-09-07)",
    )
    parser.add_argument(
        "--min-channel-traces",
        type=int,
        default=trm3_g.MIN_CHANNEL_TRACES,
        help="a fit-pool channel with fewer contributing episodes than this falls back to "
        "the pooled all-channel position buckets",
    )
    parser.add_argument(
        "--strict-channel-buckets",
        action="store_true",
        help="disable the sparse-channel fallback; a thin or unseen channel is then an "
        "error again (diagnostic only -- the prereg requires the fallback)",
    )
    parser.add_argument("--window-s", type=int, default=None)
    parser.add_argument("--window-m", type=int, default=None)
    parser.add_argument("--window-p", type=int, default=None)
    parser.add_argument("--window-b", type=int, default=None)
    parser.add_argument(
        "--window-prob", type=int, default=None, help="window width of R / J / RM (default 8)"
    )
    parser.add_argument(
        "--prob-cache-dir",
        type=Path,
        default=None,
        help="on-disk cache of the FULL router logits used by the weight-aware families "
        "(R / J / RM); a SEPARATE namespace from the frozen top-k routing cache.  Off by "
        f"default (the logits are memoised per episode anyway and the on-disk copy is "
        f"~1.5 KB per token); the conventional location is {io_g.DEFAULT_G_LOGIT_CACHE_DIR}",
    )
    parser.add_argument(
        "--no-prob-cache",
        action="store_true",
        help="never write or read the router-logit cache (logits are still memoised per episode)",
    )
    parser.add_argument(
        "--or-arm",
        default=None,
        help="add one code-domain OR arm with its OWN Bonferroni budget (prereg 11.1 / "
        "ruling 20.1 keeps prob_js only): alarm iff p_primary <= --alpha or "
        "p_arm <= --alpha-extra",
    )
    parser.add_argument("--alpha-extra", type=float, default=trm3_g.ALPHA_EXTRA)
    parser.add_argument(
        "--attribution",
        action="store_true",
        default=True,
        help="attach the top-3 contributing (layer, expert) coordinates to every "
        "non-silent endpoint (prereg 2.8 / brief 3.6; on by default)",
    )
    parser.add_argument("--no-attribution", dest="attribution", action="store_false")
    parser.add_argument(
        "--temporal-d",
        type=int,
        default=trm3_g.TEMPORAL_D,
        help="hysteresis depth D in looks (prereg 2.7: 24 consecutive p_inst > exit)",
    )
    parser.add_argument("--temporal-exit", type=float, default=trm3_g.TEMPORAL_EXIT)
    parser.add_argument(
        "--tertile-cutpoints",
        default=None,
        help="two comma-separated generated-token cutpoints; default = the frozen filtered "
        f"G-cal pair {trm3_g.G_CAL_TERTILE_CUTPOINTS} (prereg 7.3 / item 24)",
    )
    parser.add_argument(
        "--tertile-cutpoints-from-target",
        action="store_true",
        help="DIAGNOSTIC ONLY: re-derive the length thirds on the target pool",
    )
    parser.add_argument(
        "--tolerance-bands",
        default=None,
        help="anchor tolerance family, comma-separated; default 0,4,5,8 (prereg 5 / item 20)",
    )
    parser.add_argument(
        "--e-denominator-all-arms",
        action="store_true",
        help="DIAGNOSTIC ONLY: keep every labelled arm in the E denominator; the prereg "
        "keeps the attack arm only (legitimate_refusal is a control, ruling 4)",
    )
    parser.add_argument(
        "--session-turns-config",
        type=Path,
        default=None,
        help="experiment config whose scenarios[*].factory.session_turns give the "
        "per-session CONFIGURED turn count of the session budget (item 33)",
    )
    parser.add_argument(
        "--no-standardise",
        action="store_true",
        help="ablation A-raw (item 39): calibrate on the RAW full-path maximum, with the "
        "channel-conditioned position buckets replaced by the identity",
    )
    parser.add_argument("--freeze-commit", default=None, help="required for a non-smoke run")
    parser.add_argument("--prereg-sha256", default=None)
    parser.add_argument(
        "--labels-sha256",
        action="append",
        default=None,
        help="expected sha256 of a label file; repeatable, each must match one of the "
        "files actually loaded",
    )
    parser.add_argument(
        "--expect-h",
        type=int,
        default=None,
        help="assert this H instead of the frozen table value of (tag_scope, view, w)",
    )
    parser.add_argument("--expect-n-reference", type=int, default=None)
    parser.add_argument(
        "--expect-all-layers",
        action="store_true",
        default=True,
        help="assert the layer band is all 24 MoE layers (prereg note 1 of 2026-09-07)",
    )
    parser.add_argument("--no-expect-all-layers", dest="expect_all_layers", action="store_false")
    parser.add_argument("--attainability-floor", type=int, default=trm3_g.ATTAINABILITY_FLOOR)
    parser.add_argument("--layers", default=None, help="comma-separated MoE layer band (default: all)")
    parser.add_argument("--rare-threshold", type=float, default=trm3_g.RARE_THRESHOLD)
    parser.add_argument("--session-alpha", type=float, default=trm3_g.SESSION_ALPHA)
    parser.add_argument("--session-turns", type=int, default=trm3_g.SESSION_TURNS)
    parser.add_argument(
        "--tag-scope",
        default="message",
        choices=io_g.TAG_SCOPES,
        help="channel-tag convention: 'message' (default, the channel header tokens belong "
        "to the message) or 'body' (headers are 'other' and enter no view); recorded at the "
        "top level of result.json and on every calibration block",
    )
    parser.add_argument("--cal-name", default="g_cal")
    parser.add_argument("--cache-dir", type=Path, default=io_g.DEFAULT_G_CACHE_DIR)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--require-quality-labels", action="store_true")
    parser.add_argument("--outputs", default="primary", choices=("none", "primary", "all"))
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--bootstrap-replicates", type=int, default=2000)
    return parser.parse_args(argv)


def git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:  # pragma: no cover - provenance only
        return ""


def main(argv: Sequence[str] | None = None) -> int:
    args = _args(argv)
    view = trm3_g.view_of(args.view)
    cache_dir = None if args.no_cache else args.cache_dir
    variants = tuple(io_g.NORMAL_VARIANTS) if args.normal_only_smoke else None

    labels = label_provenance(args)
    discipline = freeze_guard(args, labels)

    fit_raw, fit_manifest = load_pool(
        args.fit,
        name="fit",
        scenarios=_split(args.fit_scenarios),
        labels=pool_labels(args, "fit"),
        tag_scope=args.tag_scope,
        cache_dir=cache_dir,
        variants=variants,
    )
    cal_raw, cal_manifest = load_pool(
        args.cal,
        name="cal",
        scenarios=_split(args.cal_scenarios),
        labels=pool_labels(args, "cal"),
        tag_scope=args.tag_scope,
        cache_dir=cache_dir,
        variants=variants,
    )
    target_pool, target_manifest = load_pool(
        args.target,
        name="target",
        scenarios=_split(args.target_scenarios),
        labels=pool_labels(args, "target"),
        tag_scope=args.tag_scope,
        cache_dir=cache_dir,
        variants=variants,
    )

    probe_roles = {
        role
        for manifest in (fit_manifest, cal_manifest, target_manifest)
        for role in manifest["dataset_roles"]
        if role in io_g.PROBE_ROLES
    }
    if probe_roles and not args.normal_only_smoke:
        raise SystemExit(
            f"pools contain probe material {sorted(probe_roles)} (design section 5: not data); "
            "only --normal-only-smoke may read it"
        )
    if args.normal_only_smoke:
        offenders = [
            e.trace_id
            for e in list(fit_raw) + list(cal_raw) + list(target_pool)
            if e.variant not in io_g.NORMAL_VARIANTS
        ]
        if offenders:
            raise SystemExit(f"--normal-only-smoke saw non-routine episodes: {offenders[:5]}")

    fit_pool, fit_report = routine_pool(
        fit_raw,
        name="fit",
        require_filter=args.require_quality_labels,
        smoke=bool(args.normal_only_smoke),
    )
    cal_pool, cal_report = routine_pool(
        cal_raw,
        name="cal",
        require_filter=args.require_quality_labels,
        smoke=bool(args.normal_only_smoke),
    )
    overlap = {e.pair_group_id for e in fit_pool} & {e.pair_group_id for e in cal_pool}
    if overlap:
        raise SystemExit(
            f"the fitting and calibration pools share {len(overlap)} scenario(s): "
            f"{sorted(overlap)[:5]}; design section 5 requires them to be disjoint"
        )

    names = [
        trm3_g.STATISTIC_ALIASES.get(name, name) for name in (_split(args.statistic) or ["M"])
    ]
    cells = {
        name: run_cell(
            name,
            args=args,
            view=view,
            fit_pool=fit_pool,
            cal_pool=cal_pool,
            target_pool=target_pool,
        )
        for name in names
    }
    comparison = None
    if args.compare_statistic:
        secondary_name = trm3_g.STATISTIC_ALIASES.get(
            args.compare_statistic, args.compare_statistic
        )
        if secondary_name not in cells:
            cells[secondary_name] = run_cell(
                secondary_name,
                args=args,
                view=view,
                fit_pool=fit_pool,
                cal_pool=cal_pool,
                target_pool=target_pool,
            )
        comparison = compare_cells(
            cells[names[0]],
            cells[secondary_name],
            target_pool,
            alpha=float(args.alpha),
            replicates=int(args.bootstrap_replicates),
        )

    pool_assertions = target_pool_assertions(args, target_pool)
    failed = [
        {"statistic": name, **row}
        for name, cell in cells.items()
        for row in cell["assertions"]
        if not row["ok"]
    ] + [{"statistic": None, **row} for row in pool_assertions if not row["ok"]]
    if failed and not args.normal_only_smoke:
        raise SystemExit(
            "frozen-parameter assertions failed (prereg section 15 item 4):\n"
            + json.dumps(failed, indent=2, default=str)
        )

    rows = [row for cell in cells.values() for row in cell["_rows"]]
    payload = {
        "kind": "research_v4_detector_harness_g",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "code_commit": git_commit(),
        "prereg": {
            "path": str(PREREG_PATH),
            "sha256": discipline["prereg_sha256"],
            # the window width is per statistic, so the cell's third coordinate lives in
            # each cells.<stat>.assertions[horizon_H].cell row
            "frozen_h_cell_prefix": [str(args.tag_scope), str(args.view)],
            "frozen_h_table": {
                "|".join(str(v) for v in cell): value
                for cell, value in sorted(trm3_g.H_FREEZE_TABLE.items())
            },
            "tertile_cutpoints": tertile_cutpoints(args),
            "tolerance_bands": list(tolerance_bands(args)),
        },
        "data_discipline_guard": discipline,
        "inputs": {"label_sha256": labels},
        "assertions": {
            "failed": failed,
            "enforced": not args.normal_only_smoke,
            "by_statistic": {name: cell["assertions"] for name, cell in cells.items()},
            "target_pool": pool_assertions,
        },
        "args": {
            key: (
                [str(v) for v in value]
                if isinstance(value, list)
                else (str(value) if isinstance(value, Path) else value)
            )
            for key, value in vars(args).items()
        },
        "view": {"name": view.name, "channels": list(view.channels), "description": view.description},
        "tag_scope": str(args.tag_scope),
        "tag_scope_detail": {
            "scope": str(args.tag_scope),
            "choices": list(io_g.TAG_SCOPES),
            "description": (
                "message: the channel header tokens belong to the message they open "
                "(default, prereg note 2 of 2026-09-07); "
                "body: only the message body is tagged and the markers are 'other', "
                "so they enter no view"
            ),
            "load_reports_agree": sorted(
                {
                    str(report.get("tag_scope"))
                    for manifest in (fit_manifest, cal_manifest, target_manifest)
                    for report in manifest["load_reports"]
                }
            ),
        },
        "pools": {
            "fit": {
                **fit_manifest,
                **fit_report,
                "labels": labels["fit"],
                "final": io_g.episode_manifest(fit_pool),
            },
            "cal": {
                **cal_manifest,
                **cal_report,
                "labels": labels["cal"],
                "final": io_g.episode_manifest(cal_pool),
            },
            "target": {**target_manifest, "labels": labels["target"]},
        },
        "cells": {
            name: {k: v for k, v in cell.items() if not k.startswith("_")}
            for name, cell in cells.items()
        },
        "comparison": comparison,
    }

    run_name = args.run_name or f"{view.name}_{'-'.join(names)}_a{args.alpha:g}"
    out_dir = Path(args.output_root) / run_name
    if args.outputs != "none":
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "result.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
        if rows:
            with (out_dir / "outputs.jsonl").open("w", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, sort_keys=True) + "\n")
    print_summary(payload, out_dir if args.outputs != "none" else None)
    return 0


def print_summary(payload: dict[str, Any], out_dir: Path | None) -> None:
    view = payload["view"]["name"]
    pools = payload["pools"]
    print(f"view {view} ({', '.join(payload['view']['channels'])}) tag_scope={payload['tag_scope']}")
    for name in ("fit", "cal", "target"):
        block = pools[name]
        final = block.get("final", block)
        print(
            f"  {name:<6} episodes={final['episode_count']:<4} sessions={final['session_count']:<4} "
            f"scenarios={final['scenario_count']:<3} tokens={final['token_count']:<6} "
            f"variants={final['variants']}"
        )
    for name in ("fit", "cal", "target"):
        block = pools[name].get("labels") or {}
        print(
            f"  {name:<6} labels={block.get('path')} rows={block.get('rows')} "
            f"sha256={(block.get('sha256') or '')[:16]} filter={pools[name].get('filter_status')}"
        )
    for name, cell in payload["cells"].items():
        horizon = cell["calibration"]["horizon"]
        metrics = cell["metrics"]
        far = metrics["far"]
        checks = {row["check"]: row["ok"] for row in cell.get("assertions", ())}
        print(
            f"  [{name}] H={horizon['H']} (survivors {horizon['survivors_at_H']}/"
            f"{horizon['calibration_paths']}, censored paths {horizon['censored_paths']}), "
            f"n_ref={cell['calibration']['n_reference']}, "
            f"alpha_eff={cell['alpha_budget']['alpha_eff']:.4f}"
        )
        print(
            "        assertions "
            + " ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in sorted(checks.items()))
        )
        print(
            f"        FAR all={far['all']['far']} filtered={far['filtered']['far']} "
            f"clean={far['clean']['all']['far']} benign_control={far['benign_control']['all']['far']} "
            f"onsets/1000={metrics['endpoint']['alarm_onsets_per_1000_eligible']}"
        )
        print(
            f"        positives={metrics['positives']['count']} excluded={metrics['positives']['excluded']} "
            f"session_far={metrics['session']['session_far']} "
            f"(alpha_ep={metrics['session']['alpha_episode']:.4f})"
        )
        fallback = cell["standardisation"]
        print(
            f"        buckets fitted={fallback['fitted_channels']} "
            f"fallback={fallback['channels'] or '[]'} "
            f"applied_windows={fallback['applied_windows'] or '{}'} "
            f"unseen={fallback['applied_channels_absent_from_fit'] or '[]'}"
        )
        hyst = metrics.get("hysteresis") or {}
        print(
            f"        p_inst D={hyst.get('d')} entries={hyst.get('entries')} "
            f"exits={hyst.get('exits')} states={hyst.get('final_states')} "
            f"attribution_endpoints={cell['attribution']['endpoints_with_coordinates']}"
        )
    if payload["comparison"]:
        comparison = payload["comparison"]
        for row_name, row in comparison["rows"].items():
            boot = row["bootstrap"]
            print(
                f"  comparison[{row_name}] alpha_b={row['alpha_secondary']:.6g} "
                f"far_a={row['measured_far_primary']} far_b={row['measured_far_secondary']} "
                f"pairs={boot['pair_count']} delta={boot['point_estimate']} ci={boot['ci']} "
                f"mcnemar_p={(boot.get('mcnemar') or {}).get('p_value')}"
            )
    if out_dir is not None:
        print(f"  wrote {out_dir}")


if __name__ == "__main__":
    raise SystemExit(main())
