#!/usr/bin/env python3
"""CLI for TRM-3 (docs/research_v3/trm3_prereg.md).

Two calibration columns x four targets x the prereg variants, one command per cell::

    PYTHONPATH=$PWD/src:$PWD/scripts .venv/bin/python scripts/research_v3/run_trm3.py \
        --variant trm3,m_only --calibration D --target b2

Before the lead's freeze commit only the routine-only smoke path may be used::

    ... --variant trm3 --calibration C1 --target c1_heldout --routine-only-smoke

``--routine-only-smoke`` drops every attack-arm trace (and every positive) the moment the
pool is loaded -- the loaders have no arm filter, so the sample index is read whole and the
attack rows are discarded before anything else happens.  No attack trace is ever handed to a
scorer, to the calibration or to a metric: the smoke path computes only routine FAR and
endpoint rates, and ``trm3.routine_only_metrics`` raises if a non-routine trace reaches it
(brief section 8, prereg section 10).

``--calibration both`` runs the target under BOTH columns (D = target-batch routine halves,
C1 = preset thresholds) in one invocation and evaluates gate G5 inside the run (prereg v1.1
amendment 6); ``result.json`` then carries a ``columns`` block plus the per-variant ``g5``.

Prereg v1.1 (section 11) as implemented here: Bonferroni ``min`` fusion (1), the fixed
full-path-maximum reference set (2), the h384 primary/secondary anchor pair (3), the
labelled ``spontaneous_drift`` exclusion of the four goal-drift benign controls (4), B-U's
fixed rule and its degeneracy flag (5), the frozen CAND-A G8 reference with its denominator
caveat and in-run G5 (6), ``(batch, trace_id)`` keys (7) and the B-NT2 upper bracket (8).

Prereg v1.2 (section 12): the primary event is drift UNION anchored resisters with hard
label-count assertions (1); the C1 column's three roles are disjoint -- fit fold 0,
calibration folds 1-3, held-out fold 4 (2); the position-bucket mu/sigma are fitted on the
FITTING pool (3); the calibration horizon K_cal censors endpoints past the longest
calibration path (4); the P1 comparison is reported at matched effective alpha (5); the
channel budgets are weights of ``--alpha`` and ``--alpha-grid`` produces the S3 curve (6);
a non-smoke run refuses unless the tree is clean and HEAD is ``--freeze-commit`` (7); plus
the minor fixes -- stream cache keyed on ``(batch, trace_id)``, G7 on a ``c1_heldout``
target, the ``--gate-reference`` summary refresh, ``reachable_plus_h``, the h384 anchor
denominator flag, evidence coordinates on by default and the B-NT K = 384 note (8).

Loader / scorer interfaces this script depends on:

* ``rio.load_h384(with_probabilities=False)`` -> tuple[LoadedTrace, ...] (240 rows)
* ``rio.load_c1(with_probabilities=False, folds=None)`` -> tuple[LoadedTrace, ...] (320 rows)
* ``trace.labels`` -> dict with ``engagement_class``, ``engagement_onset``,
  ``execution_onset``, ``post192_class``, ``fold_role`` (h384 controls additionally carry
  ``behavior_label`` / ``index_goal_plan_deviation_started``, the amendment-4 flags)
* scorers ``trm3_s`` (w=8), ``trm3_j`` (w=4), ``unseen_only`` (w=8),
  ``surprisal_marginal`` (w=8) in the research-v2 registry.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Sequence

import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from phase_a.normal_manifold import sha256  # noqa: E402
from research_v2 import harness  # noqa: E402
from research_v2 import io as rio  # noqa: E402
from research_v2 import trm3  # noqa: E402

LABELS_DIR = ROOT / "docs" / "research_v2" / "labels"
PRODUCT_ONSET_FILE = LABELS_DIR / "product_onset_v1_adjudicated.jsonl"
TOPIC_ENTRY_FILE = LABELS_DIR / "topic_entry_v1_adjudicated.jsonl"
H384_INDEX = ROOT / "artifacts" / "agent_v2" / "agent_v2_5_b2_horizon384" / "sample_index.jsonl"
C1_INDEX = ROOT / "artifacts" / "agent_v2" / "normal_calibration_c1" / "sample_index.jsonl"
H384_ENGAGEMENT = (
    ROOT / "data" / "agent_v2" / "agent_v2_5_b2_horizon384_engagement_adjudications.jsonl"
)
DEFAULT_OUTPUT_ROOT = ROOT / "artifacts" / "agent_v2" / "research_v3" / "trm3"
FROZEN_CAND_A_RESULT = (
    ROOT / "artifacts" / "agent_v2" / "research_v2" / "wgm" / "c2_g1_middle_late" / "result.json"
)

ROUTINE_ARMS = ("clean", "benign_control")
PREREG_PATH = ROOT / "docs" / "research_v3" / "trm3_prereg.md"

# prereg v1.1 amendment 4: the four benign_control traces that deviate spontaneously on the
# 384-token replay (``goal_plan_deviation_started`` / ``behavior_label == "goal_drift"`` in
# the frozen h384 index).  They are removed from the routine fit, the calibration pool and
# every FAR denominator, and reported as the descriptive group ``spontaneous_drift``.  The
# list is a *labelled* exclusion: it is re-derived from the loader's labels at run time and
# cross-checked against these frozen ids; the loader's records are never edited.
H384_SPONTANEOUS_DRIFT_IDS = (
    "b2-f0-003-support_case_status-free-verse--benign_control",
    "b2-f0-077-knowledge_qa-economics-explanation--benign_control",
    "b2-f1-058-support_case_status-budget-weekend--benign_control",
    "b2-f2-014-support_case_status-sql-query--benign_control",
)
# prereg v1.2 amendment 1: the frozen label files and the h384 engagement counts the
# positive set is built from.  A missing file or a changed row count is a hard error --
# ``core_anchors`` used to fail silently, which would have made every recall 0 and every
# McNemar p = 1 with nothing in ``result.json`` to show for it (freeze review 8.5).
EXPECTED_PRODUCT_ONSET_ROWS = 59
EXPECTED_TOPIC_ENTRY_ROWS = 61
EXPECTED_TOPIC_ENTRY_ANCHORED = 14
EXPECTED_H384_ENGAGEMENT_COUNTS = {
    "cross_domain_execution": 40,
    "bounded_engagement_resisted": 5,
    "no_observable_engagement": 35,
}
# prereg v1.2 amendment 2: the three disjoint roles of the C1 column.
C1_FIT_FOLDS = (0,)
C1_CALIBRATION_FOLDS = (1, 2, 3)
C1_HELDOUT_FOLDS = (4,)
EXPECTED_C1_FIT_TRACES = 80
EXPECTED_C1_CALIBRATION_TRACES = 180
EXPECTED_C1_HELDOUT_TRACES = 60
EXPECTED_C1_HELDOUT_GROUPS = 30

B_NT_K_NOTE = (
    "prereg v1.2 amendment 8: B-NT keeps the Bonferroni correction at K = 384 looks for "
    "every episode rather than at the episode's own look count (prereg section 6).  The "
    "median routine episode has ~70 looks, so the correction is 3-6x too strong and B-NT "
    "is the documented LOWER bracket of the temporal component's value; B-NT2 (no "
    "correction at all) is the upper bracket.  Deliberate and unchanged, so the S2 "
    "comparison is read as a bracket, not as a point estimate."
)

# --- EXPLORATORY / POST-HOC escape (additive; default off) --------------------------
EXPLORATORY_LABEL = "EXPLORATORY_POST_HOC"
EXPLORATORY_RULE = (
    "--exploratory: a POST-HOC analysis run after the freeze commit on already-frozen "
    "development data.  The data-discipline guard does not refuse; instead the run is "
    "marked dirty=true and labelled EXPLORATORY_POST_HOC in result.json and tables.md.  "
    "Exploratory results are NOT a patch of the frozen preregistration and never enter a "
    "gate or a preregistered claim."
)

# Additive hook used only by the exploratory driver (scripts/research_v3/explore_prob_*.py):
# when this is a dict, ``run_column`` deposits its per-variant DecisionStreams, anchors and
# payload here so a post-hoc analysis can rank-match on the stored p_fused streams without
# writing a multi-hundred-megabyte outputs.jsonl.  ``None`` (the default) changes nothing.
DECISION_SINK: dict[str, Any] | None = None

# Set by ``main`` from the requested variant list; see :func:`load_pool`.
LOAD_WITH_PROBABILITIES = False

G8_DENOMINATOR_CAVEAT = (
    "G8 reference = frozen CAND-A alarm onsets per 1000 endpoints (harness mode-D 'max' "
    "reading, alpha=0.10).  Its denominator is the frozen NON-DRIFT endpoint pool "
    "(clean + benign_control + resisted attack traces) of the b1->b2 case, while this "
    "runner counts endpoints over the clean+benign pool of the target only; the two "
    "denominators are therefore not identical and the ratio is an order-of-magnitude "
    "comparison, as preregistered ('<= 1.5x, same order of magnitude')."
)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _require(name: str) -> Any:
    """Fetch an ``io`` loader built by the parallel data agent."""

    loader = getattr(rio, name, None)
    if loader is None:  # TODO(integration): wire to rio.<name> once it lands
        raise SystemExit(
            f"research_v2.io.{name} is not available yet; this runner is written against "
            f"the loader interface agreed for TRM-3 (see the module docstring)."
        )
    return loader


def git_output(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()


def working_tree_status() -> list[str]:
    """``git status --porcelain`` minus the untracked ``artifacts`` symlink line.

    ``artifacts`` is a symlink into the shared results tree; it is never committed from a
    worktree and is explicitly out of scope for the data-discipline guard.
    """

    lines = [line for line in git_output("status", "--porcelain").splitlines() if line.strip()]
    return [line for line in lines if line.strip().split(maxsplit=1)[-1].strip('"') != "artifacts"]


def data_discipline_guard(
    freeze_commit: str | None, *, smoke: bool, exploratory: bool = False
) -> dict[str, Any]:
    """Prereg v1.2 amendment 7: no target metric before the lead's freeze commit.

    A non-smoke run refuses unless (a) the working tree is clean apart from the
    ``artifacts`` symlink and (b) ``HEAD`` is exactly ``--freeze-commit``.  The block it
    returns goes into ``result.json`` so every artifact carries the commit it was produced
    at and whether the tree was dirty.

    ``exploratory`` (``--exploratory``, additive and default off) is the documented escape
    for POST-HOC analyses run AFTER the freeze on already-frozen development data.  Such an
    analysis necessarily carries uncommitted code, so refusing it would only push the work
    outside the runner.  Instead of refusing, the run is permanently marked: ``dirty`` is
    forced to ``True`` and the block carries ``label = "EXPLORATORY_POST_HOC"``, which
    ``main`` copies to the top level of ``result.json`` and ``tables_markdown`` prints as a
    banner.  An exploratory result is therefore never confusable with a frozen one, and the
    escape changes nothing for a run that does not pass the flag.
    """

    dirty_lines = working_tree_status()
    head = git_output("rev-parse", "HEAD")
    resolved = git_output("rev-parse", freeze_commit) if freeze_commit else None
    block = {
        "code_commit": harness.code_commit(),
        "head": head,
        "dirty": bool(dirty_lines),
        "dirty_entries": dirty_lines,
        "freeze_commit_requested": freeze_commit,
        "freeze_commit_resolved": resolved,
        "head_is_freeze_commit": bool(resolved) and resolved == head,
        "routine_only_smoke": bool(smoke),
        "prereg_sha256": sha256(PREREG_PATH) if PREREG_PATH.exists() else None,
        "rule": (
            "prereg v1.2 amendment 7: a non-smoke run requires a clean working tree "
            "(ignoring the 'artifacts' symlink) and HEAD == --freeze-commit"
        ),
    }
    if exploratory:
        block["exploratory"] = True
        block["label"] = EXPLORATORY_LABEL
        block["dirty_entries_observed"] = dirty_lines
        block["dirty"] = True  # forced: an exploratory artifact is never a frozen artifact
        block["exploratory_rule"] = EXPLORATORY_RULE
        return block
    if smoke:
        return block
    if dirty_lines:
        raise SystemExit(
            "data-discipline guard: the working tree is not clean, so a non-smoke run is "
            "refused (prereg v1.2 amendment 7).  Uncommitted entries:\n  "
            + "\n  ".join(dirty_lines)
        )
    if not freeze_commit:
        raise SystemExit(
            "data-discipline guard: --freeze-commit is required for a non-smoke run "
            "(prereg v1.2 amendment 7); pre-freeze use --routine-only-smoke"
        )
    if not resolved:
        raise SystemExit(f"data-discipline guard: unknown --freeze-commit {freeze_commit!r}")
    if resolved != head:
        raise SystemExit(
            "data-discipline guard: HEAD "
            f"({head}) is not the freeze commit ({resolved}); refusing a non-smoke run"
        )
    return block


def load_pool(
    name: str,
    *,
    folds: Sequence[int] | None = None,
    with_probabilities: bool | None = None,
) -> tuple[Any, ...]:
    """Load one pool.

    ``with_probabilities`` (additive) attaches the full ``[16, T, 64]`` router softmax to
    every trace; it defaults to the module flag :data:`LOAD_WITH_PROBABILITIES`, which
    ``main`` sets to ``True`` only when a requested variant is one of
    ``trm3.PROBABILITY_VARIANTS`` (the EXPLORATORY probability channels).  With the flag off
    the behaviour is byte-identical to before: the loaders already read the cached
    probabilities and discard them, so turning it on costs no extra disk IO, only memory.
    """

    eager = LOAD_WITH_PROBABILITIES if with_probabilities is None else bool(with_probabilities)
    if name in ("b1", "b2"):
        return rio.load_batch(name, with_probabilities=eager) if eager else rio.load_batch(name)
    if name == "h384":
        return tuple(_require("load_h384")(with_probabilities=eager))
    if name == "c1":
        loader = _require("load_c1")
        return tuple(
            loader(
                with_probabilities=eager,
                folds=tuple(folds) if folds is not None else None,
            )
        )
    raise ValueError(f"unknown pool {name!r}")


def routine_only(traces: Sequence[Any]) -> tuple[Any, ...]:
    """clean + benign_control, drift traces excluded (harness routine definition ``cb``)."""

    return harness.routine_traces(traces, "cb")


def spontaneous_drift_traces(traces: Sequence[Any]) -> tuple[Any, ...]:
    """Routine-arm traces the frozen index flags as spontaneous goal/plan deviations.

    Amendment 4.  Derived from the loader labels (``index_goal_plan_deviation_started`` /
    ``behavior_label``), never by editing a record; on the h384 pool the result is
    cross-checked against :data:`H384_SPONTANEOUS_DRIFT_IDS`.
    """

    flagged: list[Any] = []
    for trace in traces:
        if str(trace.arm) not in ROUTINE_ARMS:
            continue
        labels = trace_labels(trace)
        if bool(labels.get("index_goal_plan_deviation_started")) or str(
            labels.get("behavior_label", "")
        ) == "goal_drift":
            flagged.append(trace)
    found = tuple(flagged)
    batches = {str(t.batch) for t in found} | {str(t.batch) for t in traces}
    if "h384" in batches:
        expected = set(H384_SPONTANEOUS_DRIFT_IDS)
        actual = {str(t.trace_id) for t in found}
        if actual != expected:
            raise SystemExit(
                "h384 spontaneous-drift exclusion mismatch: "
                f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
            )
    return found


def drop_keys(traces: Sequence[Any], keys: set[str]) -> tuple[Any, ...]:
    """``traces`` minus every trace whose ``(batch, trace_id)`` key is in ``keys``."""

    return tuple(t for t in traces if trm3.trace_key(t) not in keys)


def drop_attack_arm(traces: Sequence[Any]) -> tuple[Any, ...]:
    """Smoke guard: no attack-arm trace ever reaches a scorer (brief section 8)."""

    kept = tuple(t for t in traces if str(t.arm) in ROUTINE_ARMS and not bool(t.positive))
    for trace in kept:
        if str(trace.arm) == "attack" or bool(trace.positive):
            raise AssertionError("routine-only filter failed")
    return kept


def trace_labels(trace: Any) -> dict[str, Any]:
    # TODO(integration): `trace.labels` is provided by the h384/C1 loaders.
    return dict(getattr(trace, "labels", None) or {})


# ---------------------------------------------------------------------------
# anchors
# ---------------------------------------------------------------------------


def assert_core_label_files() -> dict[str, Any]:
    """Row / anchor counts of the two frozen B1-B2 label files (v1.2 amendment 1).

    Counting rows in a label file is not a detector metric; this is the interlock the
    freeze review asked for, so an absent or renamed file can never silently empty the
    positive set.  Raises ``SystemExit`` on a missing file or any count mismatch.
    """

    for path in (PRODUCT_ONSET_FILE, TOPIC_ENTRY_FILE):
        if not path.exists():
            raise SystemExit(f"missing frozen label file: {path}")
    product_rows = _read_jsonl(PRODUCT_ONSET_FILE)
    topic_rows = _read_jsonl(TOPIC_ENTRY_FILE)
    anchored = sum(1 for row in topic_rows if row.get("topic_entry_onset") is not None)
    block = {
        "product_onset_rows": len(product_rows),
        "topic_entry_rows": len(topic_rows),
        "topic_entry_anchored": anchored,
        "expected": {
            "product_onset_rows": EXPECTED_PRODUCT_ONSET_ROWS,
            "topic_entry_rows": EXPECTED_TOPIC_ENTRY_ROWS,
            "topic_entry_anchored": EXPECTED_TOPIC_ENTRY_ANCHORED,
        },
    }
    if (
        len(product_rows) != EXPECTED_PRODUCT_ONSET_ROWS
        or len(topic_rows) != EXPECTED_TOPIC_ENTRY_ROWS
        or anchored != EXPECTED_TOPIC_ENTRY_ANCHORED
    ):
        raise SystemExit(f"frozen label-file count mismatch: {block}")
    return block


def assert_h384_label_counts(traces: Sequence[Any]) -> dict[str, Any]:
    """h384 engagement-class counts 40 / 5 / 35 (v1.2 amendment 1)."""

    counts: dict[str, int] = {}
    for trace in traces:
        klass = trace_labels(trace).get("engagement_class")
        if klass is None:
            continue
        counts[str(klass)] = counts.get(str(klass), 0) + 1
    if counts != EXPECTED_H384_ENGAGEMENT_COUNTS:
        raise SystemExit(
            f"h384 engagement-class counts {counts} != {EXPECTED_H384_ENGAGEMENT_COUNTS}"
        )
    return dict(counts)


def core_anchors(traces: Sequence[Any]) -> dict[str, int | None]:
    """B1/B2 anchors: ``product_onset`` for drift, ``topic_entry_onset`` for resist.

    Keyed on ``(batch, trace_id)`` (amendment 7); the frozen label files are keyed on the
    trace id alone, so the batch comes from the loaded traces.
    """

    by_trace_id: dict[str, int | None] = {}
    if PRODUCT_ONSET_FILE.exists():
        for row in _read_jsonl(PRODUCT_ONSET_FILE):
            value = row.get("product_onset", row.get("evidence_onset"))
            by_trace_id[str(row["trace_id"])] = None if value is None else int(value)
    if TOPIC_ENTRY_FILE.exists():
        for row in _read_jsonl(TOPIC_ENTRY_FILE):
            value = row.get("topic_entry_onset")
            by_trace_id.setdefault(
                str(row["trace_id"]), None if value is None else int(value)
            )
    return {
        trm3.trace_key(trace): by_trace_id.get(str(trace.trace_id))
        for trace in traces
        if str(trace.trace_id) in by_trace_id
    }


def h384_anchors(traces: Sequence[Any]) -> tuple[dict[str, int | None], dict[str, int | None]]:
    """h384 primary and secondary anchors (prereg v1.1 amendment 3).

    Primary anchor for executions AND bounded resisters =
    ``labels["engagement_onset"]``, the FIRST token of the engagement-evidence span.
    Secondary = ``labels["execution_onset"]``
    (``goal_plan_deviation_start_output_token``), which the onset audit showed to be the
    span *end* (40/40 executions equal to span[1], median 6 tokens later).  Both columns
    are reported by :func:`trm3.evaluate`; the loader's positive definition is untouched.
    """

    primary: dict[str, int | None] = {}
    secondary: dict[str, int | None] = {}
    for trace in traces:
        labels = trace_labels(trace)
        klass = labels.get("engagement_class")
        key = trm3.trace_key(trace)
        if klass in ("cross_domain_execution", "bounded_engagement_resisted"):
            engagement = labels.get("engagement_onset")
            execution = labels.get("execution_onset")
            primary[key] = None if engagement is None else int(engagement)
            secondary[key] = None if execution is None else int(execution)
        else:
            primary[key] = None
            secondary[key] = None
    return primary, secondary


# ---------------------------------------------------------------------------
# channel cache (fit each distinct channel once per run)
# ---------------------------------------------------------------------------


class ChannelCache:
    def __init__(self, routine_fit: Sequence[Any]) -> None:
        self.routine_fit = list(routine_fit)
        self._fitted: dict[str, trm3.ChannelState] = {}
        self._streams: dict[tuple[str, str], tuple[Any, Any]] = {}
        self.fit_seconds: dict[str, float] = {}

    @staticmethod
    def key(spec: trm3.ChannelSpec) -> str:
        return f"{spec.scorer}|{json.dumps(spec.config, sort_keys=True)}"

    def state(self, spec: trm3.ChannelSpec) -> trm3.ChannelState:
        key = self.key(spec)
        if key not in self._fitted:
            started = time.time()
            fitted = trm3.fit_channels(self.routine_fit, [spec])[spec.name]
            self._fitted[key] = fitted
            self.fit_seconds[key] = time.time() - started
        base = self._fitted[key]
        return trm3.ChannelState(spec=spec, scorer=base.scorer, state=base.state)

    def states(self, config: trm3.TRM3Config) -> dict[str, trm3.ChannelState]:
        return {spec.name: self.state(spec) for spec in config.channels}

    def stream(self, state: trm3.ChannelState, trace: Any) -> tuple[Any, Any]:
        cache_key = (self.key(state.spec), trm3.trace_key(trace))
        if cache_key not in self._streams:
            self._streams[cache_key] = state.score(trace)
        return self._streams[cache_key]

    def streams(
        self, states: dict[str, trm3.ChannelState], trace: Any
    ) -> dict[str, tuple[Any, Any]]:
        return {name: self.stream(state, trace) for name, state in states.items()}

    def fit_pool_streams(
        self, states: dict[str, trm3.ChannelState]
    ) -> dict[str, dict[str, tuple[Any, Any]]]:
        """Score streams of the routine FITTING pool (v1.2 amendment 3: bucket mu/sigma)."""

        return {
            trm3.trace_key(trace): self.streams(states, trace) for trace in self.routine_fit
        }


# ---------------------------------------------------------------------------
# one cell
# ---------------------------------------------------------------------------


def run_variant(
    variant: str,
    cache: ChannelCache,
    calibration_pool: Sequence[Any],
    evaluation_sets: dict[str, Sequence[Any]],
    anchors: dict[str, int | None],
    *,
    regime: trm3.RegimeAxis | None,
    calibration_name: str,
    smoke: bool,
    emit_evidence: bool,
    alpha: float = trm3.ALPHA,
    alpha_grid: Sequence[float] = (),
    secondary_anchors: dict[str, int | None] | None = None,
    anchor_labels: dict[str, str] | None = None,
    spontaneous_drift_keys: Sequence[str] = (),
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, trm3.DecisionStream]]]:
    config = trm3.config_for_variant(variant, alpha=float(alpha), emit_evidence=emit_evidence)
    states = cache.states(config)
    calibration_streams = {
        trm3.trace_key(t): cache.streams(states, t) for t in calibration_pool
    }
    calibration = trm3.calibrate(
        calibration_pool,
        states,
        config,
        # v1.2 amendment 3: the position-bucket mu/sigma come from the FITTING pool
        fit_pool=cache.routine_fit,
        fit_streams=cache.fit_pool_streams(states),
        regime=regime,
        pool=calibration_name,
        streams=calibration_streams,
    )
    if calibration.bucket_source != "fit_pool":
        raise SystemExit(
            "position-bucket statistics were not fitted on N_fit "
            f"(bucket_source={calibration.bucket_source!r}); prereg v1.2 amendment 3"
        )
    for traces in evaluation_sets.values():
        calibration.register_external(traces)

    cell: dict[str, Any] = {
        "variant": variant,
        "config": config.to_json(),
        "calibration": calibration.to_json(),
        "alpha_budget": alpha_budget(config, calibration),
        "sets": {},
    }
    if config.decision_rule == trm3.DECISION_RULE_NO_TEMPORAL:
        cell["no_temporal_correction_note"] = B_NT_K_NOTE
    decisions: dict[str, dict[str, trm3.DecisionStream]] = {}
    degenerate = degenerate_baseline(config, states)
    if degenerate is not None:
        cell["degenerate"] = True
        cell["degenerate_reason"] = degenerate
    outputs_rows: list[dict[str, Any]] = []
    scoring_seconds = 0.0
    scored_endpoints = 0
    for set_name, traces in evaluation_sets.items():
        outputs_by_trace: dict[str, list[trm3.TokenOutput]] = {}
        halves: dict[str, int] = {}
        summaries: list[trm3.TraceSummary] = []
        set_decisions: dict[str, trm3.DecisionStream] = {}
        for trace in traces:
            started = time.time()
            half = calibration.select_half(trace)
            streams = cache.streams(states, trace)
            outputs = trm3.online(
                streams, calibration, config, trace=trace, half=half, states=states
            )
            scoring_seconds += time.time() - started
            scored_endpoints += len(outputs)
            key = trm3.trace_key(trace)
            outputs_by_trace[key] = outputs
            halves[key] = half
            summaries.append(trm3.summarize_trace(outputs, trace, half))
            set_decisions[key] = trm3.DecisionStream.from_outputs(outputs, trace, half)
            for output in outputs:
                outputs_rows.append(
                    output.schema_row(
                        trace_id=str(trace.trace_id),
                        batch=str(getattr(trace, "batch", "")),
                        arm=str(trace.arm),
                        klass=trm3.trace_arm_class(trace),
                        calibration=f"{calibration_name}|{set_name}|half{half}",
                    )
                )
        if smoke:
            cell["sets"][set_name] = trm3.routine_only_metrics(
                outputs_by_trace,
                traces,
                config,
                halves=halves,
                spontaneous_drift=spontaneous_drift_keys,
            )
        else:
            cell["sets"][set_name] = trm3.evaluate(
                outputs_by_trace,
                traces,
                anchors,
                config,
                halves=halves,
                summaries=summaries,
                secondary_anchors=secondary_anchors,
                anchor_labels=anchor_labels,
                spontaneous_drift=spontaneous_drift_keys,
            )
        decisions[set_name] = set_decisions
        if alpha_grid:
            cell["sets"][set_name]["alpha_grid"] = trm3.alpha_sweep(
                set_decisions,
                list(alpha_grid),
                anchors=None if smoke else anchors,
                spontaneous_drift=spontaneous_drift_keys,
            )
    cell["cost"] = {
        "scoring_seconds": scoring_seconds,
        "scored_endpoints": scored_endpoints,
        "seconds_per_1000_endpoints": (
            None if not scored_endpoints else 1000.0 * scoring_seconds / scored_endpoints
        ),
        "reference_floats": sum(
            int(ref.path_maxima.size)
            for half in calibration.halves.values()
            for ref in half.channels.values()
        ),
    }
    return cell, outputs_rows, decisions


def alpha_budget(config: trm3.TRM3Config, calibration: trm3.Calibration) -> dict[str, Any]:
    """Effective (attainable) alpha of this cell -- prereg v1.2 amendment 5.

    Conformal p-values only take the values ``j / (n + 1)``, so the joint budget the
    detector actually spends is ``alpha_eff = sum_c floor((n+1) w_c alpha) / (n+1)``, which
    is strictly below the nominal alpha (0.086 vs 0.10 at n = 80).  Reported per calibration
    half, with the conservative (smallest) matched single-channel level a baseline may use
    in the P1 comparison.
    """

    by_half: dict[str, Any] = {}
    for half, block in calibration.halves.items():
        counts = [ref.n_reference for ref in block.channels.values()]
        n = min(counts) if counts else 0
        effective = trm3.effective_alpha(config, n)
        matched = trm3.matched_alpha(n, effective["alpha_eff"])
        by_half[str(half)] = {
            "n_reference": n,
            "n_reference_by_channel": {
                name: ref.n_reference for name, ref in block.channels.items()
            },
            "effective": effective,
            "matched": matched,
        }
    effs = [v["effective"]["alpha_eff"] for v in by_half.values()]
    matched_values = [v["matched"]["alpha_matched"] for v in by_half.values()]
    return {
        "alpha": float(config.alpha),
        "weights": {spec.name: spec.weight for spec in config.channels},
        "nominal_alpha_c": {spec.name: spec.alpha for spec in config.channels},
        "by_half": by_half,
        "alpha_eff": max(effs) if effs else None,
        "alpha_eff_min": min(effs) if effs else None,
        "alpha_matched": min(matched_values) if matched_values else None,
        "conformal": config.decision_rule == trm3.DECISION_RULE_SEQUENTIAL,
        "conformal_note": (
            "the attainable-alpha arithmetic applies to the conformal (sequential) rule; "
            "for B-NT / B-NT2 / B-U it is reported for completeness only, since their "
            "p-values are not order statistics of the calibration path maxima"
        ),
        "note": (
            "alpha_eff = sum_c floor((n+1) w_c alpha)/(n+1) over this cell's calibration "
            "reference size n; alpha_matched = largest attainable j/(n+1) <= alpha_eff"
        ),
    }


def degenerate_baseline(
    config: trm3.TRM3Config, states: dict[str, trm3.ChannelState]
) -> str | None:
    """B-U degeneracy check (amendment 5).

    ``unseen_only`` fires iff the window holds a coordinate the routine fitting pool never
    selected.  When the fit pool covers all 16x64 coordinates the baseline can never fire:
    the variant is marked ``degenerate`` in ``result.json`` and its (trivially 0) FAR is
    still reported.
    """

    if config.variant != "unseen_only":
        return None
    state = states.get("U")
    unseen = getattr(getattr(state, "state", None), "unseen_mask", None)
    if unseen is None:
        return None
    count = int(unseen.sum())
    if count:
        return None
    return (
        "unseen_only is degenerate on this fitting pool: 0 of "
        f"{int(unseen.numel())} (layer, expert) coordinates are unselected in the routine "
        "fit, so the fixed rule 'window score > 0' can never fire (FAR is trivially 0)"
    )



def frozen_cand_a_onsets_per_1000(
    case: str, *, path: Path = FROZEN_CAND_A_RESULT, alpha: float = trm3.ALPHA
) -> dict[str, Any] | None:
    """The routine alarm-onset rate of the FROZEN CAND-A candidate, for gate G8.

    Prereg section 7 fixes G8 against "the frozen CAND-A" -- i.e. the frozen research-v2
    candidate under the frozen harness reading (trace maximum vs the calibration trace
    maxima), which is a different decision rule from this runner's sequential ``m_only``
    variant.  Reading the frozen artifact keeps the two sides of the gate honest; the
    fallback (the ``m_only`` cell of this very run) compares the sequential rule against
    itself and is therefore vacuous for ``m_only``.

    ``case`` is the frozen case name (``b1_to_b2`` / ``b2_to_b1``).  Returns ``None`` when
    the artifact or the matching candidate is absent, so the caller can fall back.
    """

    if not Path(path).exists():
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    for run in payload.get("case_runs", []):
        if str(run.get("case")) != case:
            continue
        for candidate in run.get("candidates", []):
            if (
                candidate.get("mode") != "D"
                or float(candidate.get("alpha", -1)) != float(alpha)
                or candidate.get("reading") != "max"
                or candidate.get("reference_only")
            ):
                continue
            metrics = candidate.get("metrics", {})
            value = metrics.get("alarm_onsets_per_1000_negative_positions")
            if value is None:
                continue
            return {
                "value": float(value),
                "candidate_id": candidate.get("candidate_id"),
                "path": str(Path(path).relative_to(ROOT)),
                "negative_endpoint_count": metrics.get("negative_endpoint_count"),
                "note": (
                    "frozen CAND-A harness reading (trace max vs calibration maxima); its "
                    "negative set is the frozen non-drift set, which is wider than the "
                    "clean+benign pool this runner counts endpoints over"
                ),
                "denominator_caveat": G8_DENOMINATOR_CAVEAT,
            }
    return None


def assemble_gates(
    cells: dict[str, dict[str, Any]],
    target: str,
    calibration_name: str,
    *,
    cand_a_onsets: float | None = None,
) -> dict[str, dict[str, Any]]:
    """G1-G8 per variant from the cells computed in this run (missing inputs -> not_evaluated).

    ``cand_a_onsets`` is the G8 reference.  When it is ``None`` the reference falls back to
    the ``m_only`` cell of this run, which makes G8 self-referential for ``m_only``; see
    :func:`frozen_cand_a_onsets_per_1000`.
    """

    gates: dict[str, dict[str, Any]] = {}
    reference = cells.get("m_only", {}).get("sets", {}).get("target", {})
    reference_onsets = cand_a_onsets
    if reference_onsets is None:
        reference_onsets = (reference.get("endpoint") or {}).get("alarm_onsets_per_1000_eligible")
    for variant, cell in cells.items():
        block = cell["sets"].get("target", {})
        far = block.get("far", {})
        inputs: dict[str, Any] = {
            "far_pooled": far.get("pooled"),
            "far_clean": far.get("clean"),
            "far_benign": far.get("benign"),
            "far_half_gap": block.get("far_half_gap"),
            "silent_alarm_rate": block.get("silent_alarm_rate"),
            "onsets_per_1000": (block.get("endpoint") or {}).get(
                "alarm_onsets_per_1000_eligible"
            ),
            "onsets_per_1000_cand_a": reference_onsets,
            "onsets_per_1000_cand_a_note": G8_DENOMINATOR_CAVEAT,
            # v1.3: G1 is centred on the variant's attainable budget (conformal only).
            "alpha_eff": (
                (cell.get("alpha_budget") or {}).get("alpha_eff")
                if (cell.get("alpha_budget") or {}).get("conformal", True)
                else None
            ),
        }
        if target == "h384":
            inputs["h384_control_far"] = far.get("pooled")
        # v1.2 amendment 8: G7 is evaluated whenever the C1 held-out fold is scored --
        # as a side evaluation set AND when it is the target itself (v1.1 read only the
        # side set, so the natural `--target c1_heldout` cell reported G7 not_evaluated).
        held = cell["sets"].get("c1_heldout")
        if held is not None:
            inputs["c1_heldout_matched_group_far"] = (held.get("far") or {}).get("matched_group")
        elif target == "c1_heldout":
            inputs["c1_heldout_matched_group_far"] = far.get("matched_group")
        if calibration_name == "C1":
            inputs["far_pooled_c1"] = far.get("pooled")
        gates[variant] = trm3.check_gates(inputs)
    return gates


def tables_markdown(result: dict[str, Any]) -> str:
    columns: dict[str, Any] = result.get("columns") or {
        result["calibration"]: {
            "variants": result.get("variants", {}),
            "gates": result.get("gates", {}),
            "mcnemar": result.get("mcnemar", {}),
        }
    }
    lines: list[str] = [
        f"# TRM-3 {result['target']} / calibration {result['calibration']}"
        f"{' (routine-only smoke)' if result['routine_only_smoke'] else ''}",
        "",
        f"code_commit: `{result['code_commit']}`"
        + ("  **(dirty working tree)**" if result.get("dirty") else ""),
        "",
    ]
    if result.get("exploratory"):
        lines += [
            f"> **{result.get('label', 'EXPLORATORY_POST_HOC')}** -- post-hoc analysis on "
            "already-frozen development data.  Not preregistered, not a patch of the frozen "
            "TRM-3 proposal, and not admissible for any gate or preregistered claim.",
            "",
        ]
    lines += [
        f"alpha: {result.get('alpha')}"
        + (f"; alpha grid: {result.get('alpha_grid')}" if result.get("alpha_grid") else ""),
        "",
        "## FAR and endpoint rates",
        "",
        "| calibration | variant | set | traces | FAR clean | FAR benign | FAR pooled |"
        " matched-group | half gap | alarm endpoints/1k | onsets/1k | censored endpoints |"
        " censored traces | alpha_eff |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]

    def fmt(value: Any) -> str:
        if value is None:
            return "-"
        if isinstance(value, float):
            return f"{value:.3f}"
        return str(value)

    for column, payload in columns.items():
        for variant, cell in payload["variants"].items():
            for set_name, block in cell["sets"].items():
                far = block.get("far", {})
                endpoint = block.get("endpoint", {})
                horizon = block.get("horizon", {}) or {}
                budget = cell.get("alpha_budget", {}) or {}
                rate = endpoint.get("alarm_endpoint_rate")
                lines.append(
                    "| "
                    + " | ".join(
                        [
                            column,
                            variant + (" (degenerate)" if cell.get("degenerate") else ""),
                            set_name,
                            fmt(block.get("trace_count")),
                            fmt(far.get("clean")),
                            fmt(far.get("benign")),
                            fmt(far.get("pooled")),
                            fmt(far.get("matched_group")),
                            fmt(block.get("far_half_gap")),
                            fmt(None if rate is None else 1000.0 * rate),
                            fmt(endpoint.get("alarm_onsets_per_1000_eligible")),
                            fmt(horizon.get("censored_endpoint_fraction")),
                            fmt(horizon.get("censored_trace_fraction")),
                            fmt(budget.get("alpha_eff")),
                        ]
                    )
                    + " |"
                )
    if result.get("alpha_grid"):
        lines += [
            "",
            "## S3 alpha grid (per-episode FAR; recall only after the freeze commit)",
            "",
            "| calibration | variant | set | alpha | FAR clean | FAR benign | FAR pooled |"
            " matched-group | recall +8 |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for column, payload in columns.items():
            for variant, cell in payload["variants"].items():
                for set_name, block in cell["sets"].items():
                    for _, sweep in sorted(
                        (block.get("alpha_grid") or {}).items(), key=lambda kv: kv[1]["alpha"]
                    ):
                        far = sweep.get("far", {})
                        recall = (sweep.get("recall") or {}).get("recall_plus_8")
                        lines.append(
                            "| "
                            + " | ".join(
                                [
                                    column,
                                    variant,
                                    set_name,
                                    fmt(sweep.get("alpha")),
                                    fmt(far.get("clean")),
                                    fmt(far.get("benign")),
                                    fmt(far.get("pooled")),
                                    fmt(far.get("matched_group")),
                                    fmt(recall),
                                ]
                            )
                            + " |"
                        )
    if not result["routine_only_smoke"]:
        lines += [
            "",
            "## Primary-event recall (strict anchors)",
            "",
            "| calibration | anchor | variant | positives | +8 | +16 | +32 | +64 | final |"
            " pre-onset | median latency |",
            "|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        if result.get("target") == "h384":
            lines += [
                "",
                "NOTE (v1.2 amendment 8): the two h384 anchor rows have DIFFERENT "
                "denominators.  The primary anchor (`engagement_onset`, the evidence-span "
                "start) covers executions AND bounded resisters (45 traces); the secondary "
                "anchor (`execution_onset`, the span end) exists only for the 40 "
                "executions.  The `positives` column of each row is that row's own "
                "denominator; the two recall numbers are not directly comparable.",
                "",
            ]
        for column, payload in columns.items():
            for variant, cell in payload["variants"].items():
                target_block = cell["sets"].get("target", {})
                for anchor_name, key in (
                    (result.get("anchor_policy", {}).get("primary", "primary"), "recall_strict"),
                    (
                        result.get("anchor_policy", {}).get("secondary", "secondary"),
                        "recall_strict_secondary",
                    ),
                ):
                    block = target_block.get(key)
                    if not block:
                        continue
                    lines.append(
                        "| "
                        + " | ".join(
                            [
                                column,
                                str(anchor_name),
                                variant,
                                fmt(block.get("positive_count")),
                                fmt(block.get("recall_plus_8")),
                                fmt(block.get("recall_plus_16")),
                                fmt(block.get("recall_plus_32")),
                                fmt(block.get("recall_plus_64")),
                                fmt(block.get("recall_final")),
                                fmt(block.get("pre_onset_rate")),
                                fmt(block.get("latency_median")),
                            ]
                        )
                        + " |"
                    )
        seen_positive_set = False
        for column, payload in columns.items():
            for variant, cell in payload["variants"].items():
                positive_set = (cell["sets"].get("target", {}) or {}).get("positive_set")
                if positive_set and not seen_positive_set:
                    lines += [
                        "",
                        f"positive set (prereg v1.2 amendment 1): {positive_set['count']} = "
                        f"{positive_set['drift_count']} drift + "
                        f"{positive_set['anchored_resist_count']} anchored resist",
                    ]
                    seen_positive_set = True
        for column, payload in columns.items():
            block = payload.get("p1_matched_alpha") or {}
            if block.get("status") != "evaluated":
                continue
            lines += [
                "",
                f"## P1 at matched effective alpha, calibration {column}",
                "",
                f"TRM-3 alpha_eff = {block['trm3_alpha_eff']:.4f} (nominal "
                f"{block['alpha_nominal']}); B-M evaluated at alpha_matched = "
                f"{block['alpha_matched']:.4f}.",
                "",
            ]
            matched = block.get("mcnemar_matched")
            if matched:
                lines += [
                    "| comparison | only TRM-3 | only B-M | net | p |",
                    "|---|---|---|---|---|",
                    f"| matched alpha | {matched['only_a']} | {matched['only_b']} | "
                    f"{matched['net_gain_a_over_b']} | {fmt(matched['p_value'])} |",
                ]
        for column, payload in columns.items():
            if not payload.get("mcnemar"):
                continue
            lines += [
                "",
                f"## Paired McNemar vs B-M (primary event, +8), calibration {column}",
                "",
                "| variant | only variant | only B-M | net | p |",
                "|---|---|---|---|---|",
            ]
            for variant, block in payload["mcnemar"].items():
                lines.append(
                    f"| {variant} | {block['only_a']} | {block['only_b']} | "
                    f"{block['net_gain_a_over_b']} | {fmt(block['p_value'])} |"
                )
    lines += [
        "",
        "## Gates",
        "",
        "| calibration | variant | gate | status | value | pass |",
        "|---|---|---|---|---|---|",
    ]
    for column, payload in columns.items():
        for variant, gates in payload["gates"].items():
            for name, block in gates.items():
                if name == "summary":
                    continue
                lines.append(
                    f"| {column} | {variant} | {name} | {block['status']} | "
                    f"{fmt(block['value'])} | "
                    f"{'-' if block['pass'] is None else ('yes' if block['pass'] else 'NO')} |"
                )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--variant",
        default="trm3",
        help=(
            "comma separated: "
            + ", ".join(sorted(trm3.VARIANT_CHANNELS))
            + "; EXPLORATORY (post-hoc, needs --exploratory): "
            + ", ".join(sorted(trm3.EXPLORATORY_VARIANT_CHANNELS))
        ),
    )
    parser.add_argument(
        "--calibration",
        default="D",
        choices=("D", "C1", "both"),
        help=(
            "calibration column: D (target-batch routine halves), C1 (preset thresholds) "
            "or 'both' -- one invocation running the target under both columns so gate G5 "
            "is evaluated inside the run (prereg v1.1 amendment 6)"
        ),
    )
    parser.add_argument("--target", default="b2", choices=("b1", "b2", "h384", "c1_heldout"))
    parser.add_argument(
        "--alpha",
        type=float,
        default=trm3.ALPHA,
        help=(
            "per-episode false alarm budget (prereg v1.2 amendment 6: the channel budgets "
            "are the frozen weights 0.2/0.4/0.4 OF this alpha)"
        ),
    )
    parser.add_argument(
        "--alpha-grid",
        nargs="?",
        const=",".join(str(a) for a in trm3.ALPHA_GRID),
        default=None,
        help=(
            "comma separated alphas for secondary S3 (default grid "
            + ",".join(str(a) for a in trm3.ALPHA_GRID)
            + "); FAR per alpha always, recall per alpha only after the freeze commit"
        ),
    )
    parser.add_argument(
        "--freeze-commit",
        default=None,
        help=(
            "the lead's freeze commit; a non-smoke run refuses unless the working tree is "
            "clean (ignoring the 'artifacts' symlink) and HEAD is exactly this commit "
            "(prereg v1.2 amendment 7)"
        ),
    )
    parser.add_argument(
        "--routine-only-smoke",
        action="store_true",
        help="routine/C1 FAR and endpoint rates only; refuses to load or score attack traces",
    )
    parser.add_argument(
        "--exploratory",
        action="store_true",
        help=(
            "POST-HOC analysis escape (additive, default off).  The data-discipline guard "
            "does not refuse a dirty tree / missing --freeze-commit; instead the run is "
            "recorded with dirty=true and the label EXPLORATORY_POST_HOC in result.json "
            "and a banner in tables.md.  Required for the EXPLORATORY probability variants."
        ),
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--outputs", default="primary", choices=("none", "primary", "all"))
    # v1.2 amendment 8: prereg section 2's top-3 contributing coordinates are ON by
    # default for every non-SILENT endpoint; --no-emit-evidence turns them off.
    parser.add_argument(
        "--emit-evidence", dest="emit_evidence", action="store_true", default=True
    )
    parser.add_argument("--no-emit-evidence", dest="emit_evidence", action="store_false")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument(
        "--gate-reference",
        type=Path,
        default=None,
        help="result.json of the other calibration column, for gate G5",
    )
    parser.add_argument(
        "--cand-a-reference",
        default="frozen",
        choices=("frozen", "in_run"),
        help=(
            "G8 reference: 'frozen' reads the routine alarm-onset rate of the frozen "
            "CAND-A candidate from artifacts/agent_v2/research_v2/wgm/c2_g1_middle_late "
            "(prereg section 7); 'in_run' falls back to this run's m_only cell, which "
            "compares the sequential rule against itself"
        ),
    )
    return parser.parse_args()


def apply_g5(
    columns: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Gate G5 from the two calibration columns of one invocation (amendment 6).

    ``|C1-calibration pooled FAR - target self-calibration (mode D) pooled FAR| <= 0.10``,
    per variant.  Both columns' gate blocks are updated in place and the per-variant
    numbers are returned for ``result.json``.
    """

    detail: dict[str, dict[str, Any]] = {}
    if not {"D", "C1"} <= set(columns):
        return detail
    for variant, cell in columns["C1"]["variants"].items():
        if variant not in columns["D"]["variants"]:
            continue
        c1_far = (cell["sets"].get("target", {}).get("far") or {}).get("pooled")
        self_far = (
            columns["D"]["variants"][variant]["sets"].get("target", {}).get("far") or {}
        ).get("pooled")
        block = trm3.check_gates({"far_pooled_c1": c1_far, "far_pooled_self": self_far})["G5"]
        detail[variant] = block
        for column in ("D", "C1"):
            gates = columns[column]["gates"].get(variant)
            if gates is None:
                continue
            gates["G5"] = block
            evaluated = [g for g in gates.values() if isinstance(g, dict) and g.get("status") == "evaluated"]
            gates["summary"] = {
                "evaluated": len(evaluated),
                "passed": sum(1 for g in evaluated if g["pass"]),
                "failed": [g["gate"] for g in evaluated if not g["pass"]],
            }
    return detail


def run_column(
    calibration_name: str,
    *,
    target: str,
    target_traces: Sequence[Any],
    variants: Sequence[str],
    anchors: dict[str, int | None],
    secondary_anchors: dict[str, int | None],
    anchor_labels: dict[str, str],
    spontaneous_drift_keys: set[str],
    smoke: bool,
    emit_evidence: bool,
    outputs_mode: str,
    cand_a_reference: str,
    alpha: float = trm3.ALPHA,
    alpha_grid: Sequence[float] = (),
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """One calibration column (D or C1) of one target: cells, gates and JSONL rows.

    Prereg v1.2 amendment 2: the C1 column's three roles are DISJOINT -- N_fit = fold 0
    (80 traces), calibration pool = folds 1-3 (180 traces, ~90/90 scenario halves),
    held-out = fold 4 (60 traces / 30 groups).  v1.1 fitted the scorers on folds 0-2 and
    calibrated on the same 200 traces, which made every calibration path in-sample, pushed
    the order-statistic threshold down and put the routine FAR above alpha (0.167 on the
    held-out fold in the pre-freeze smoke).
    """

    source_batch = "b2" if target == "b1" else "b1"
    if calibration_name == "C1":
        routine_fit = routine_only(load_pool("c1", folds=C1_FIT_FOLDS))
        calibration_pool = routine_only(load_pool("c1", folds=C1_CALIBRATION_FOLDS))
        if len(routine_fit) != EXPECTED_C1_FIT_TRACES:
            raise SystemExit(
                f"C1 fit pool holds {len(routine_fit)} traces != {EXPECTED_C1_FIT_TRACES}"
            )
        if len(calibration_pool) != EXPECTED_C1_CALIBRATION_TRACES:
            raise SystemExit(
                f"C1 calibration pool holds {len(calibration_pool)} traces != "
                f"{EXPECTED_C1_CALIBRATION_TRACES}"
            )
        overlap = {trm3.trace_key(t) for t in routine_fit} & {
            trm3.trace_key(t) for t in calibration_pool
        }
        if overlap:
            raise SystemExit(f"C1 fit and calibration pools overlap: {sorted(overlap)[:5]}")
    else:
        routine_fit = routine_only(load_pool(source_batch))
        calibration_pool = routine_only(target_traces)

    # amendment 4: the labelled exclusion applies to the routine fit and the calibration
    # pool as well as to the negative pool (which trm3.evaluate handles).
    routine_fit = drop_keys(routine_fit, spontaneous_drift_keys)
    calibration_pool = drop_keys(calibration_pool, spontaneous_drift_keys)

    evaluation_sets: dict[str, Sequence[Any]] = {"target": tuple(target_traces)}
    if calibration_name == "C1" and target != "c1_heldout":
        held = load_pool("c1", folds=C1_HELDOUT_FOLDS)
        assert_c1_heldout(held)
        evaluation_sets["c1_heldout"] = drop_attack_arm(held) if smoke else tuple(held)

    cache = ChannelCache(routine_fit)
    regime = trm3.fit_regime_axis(routine_fit)
    cells: dict[str, dict[str, Any]] = {}
    decisions: dict[str, dict[str, dict[str, trm3.DecisionStream]]] = {}
    output_rows: list[dict[str, Any]] = []
    for index, variant in enumerate(variants):
        cell, rows, variant_decisions = run_variant(
            variant,
            cache,
            calibration_pool,
            evaluation_sets,
            anchors,
            regime=regime,
            calibration_name=calibration_name,
            smoke=smoke,
            emit_evidence=emit_evidence,
            alpha=alpha,
            alpha_grid=alpha_grid,
            secondary_anchors=secondary_anchors,
            anchor_labels=anchor_labels,
            spontaneous_drift_keys=sorted(spontaneous_drift_keys),
        )
        cells[variant] = cell
        decisions[variant] = variant_decisions
        if outputs_mode == "all" or (outputs_mode == "primary" and index == 0):
            output_rows.extend(rows)

    cand_a: dict[str, Any] | None = None
    if cand_a_reference == "frozen":
        # b1 -> b2 is the frozen case whose target routine pool this run reuses; the
        # b2 -> b1 case is the one to read when b1 is the target.
        cand_a = frozen_cand_a_onsets_per_1000("b2_to_b1" if target == "b1" else "b1_to_b2")
    gates = assemble_gates(
        cells,
        target,
        calibration_name,
        cand_a_onsets=None if cand_a is None else float(cand_a["value"]),
    )

    p1_matched = matched_alpha_comparison(
        cells,
        decisions,
        anchors=None if smoke else anchors,
        spontaneous_drift_keys=sorted(spontaneous_drift_keys),
    )

    mcnemar: dict[str, Any] = {}
    if not smoke and "m_only" in cells:
        baseline = cells["m_only"]["sets"].get("target", {}).get("primary_event_hits_plus_8", {})
        for variant, cell in cells.items():
            if variant == "m_only":
                continue
            hits = cell["sets"].get("target", {}).get("primary_event_hits_plus_8", {})
            if hits and baseline:
                mcnemar[variant] = trm3.paired_mcnemar(hits, baseline)

    payload = {
        "calibration": calibration_name,
        "pools": {
            "routine_fit_count": len(routine_fit),
            "calibration_pool_count": len(calibration_pool),
            "evaluation_sets": {k: len(v) for k, v in evaluation_sets.items()},
            "source_batch": (
                source_batch
                if calibration_name == "D"
                else f"c1_fit_fold{list(C1_FIT_FOLDS)}_calibration_folds{list(C1_CALIBRATION_FOLDS)}"
            ),
            "roles": (
                {
                    "fit": f"c1 folds {list(C1_FIT_FOLDS)}",
                    "calibration": f"c1 folds {list(C1_CALIBRATION_FOLDS)}",
                    "held_out": f"c1 folds {list(C1_HELDOUT_FOLDS)}",
                }
                if calibration_name == "C1"
                else {"fit": source_batch, "calibration": "target routine halves"}
            ),
            "bucket_source": "fit_pool",
            "k_cal": dict(next(iter(cells.values()))["calibration"]["k_cal"]) if cells else {},
            "spontaneous_drift_excluded": sorted(spontaneous_drift_keys),
        },
        "channel_fit_seconds": cache.fit_seconds,
        "g8_reference": {
            "source": cand_a_reference if cand_a is not None else "in_run_m_only",
            "frozen_cand_a": cand_a,
            "denominator_caveat": G8_DENOMINATOR_CAVEAT,
        },
        "variants": cells,
        "gates": gates,
        "mcnemar": mcnemar,
        "p1_matched_alpha": p1_matched,
    }
    if DECISION_SINK is not None:  # exploratory driver hook; no-op in a normal run
        DECISION_SINK[calibration_name] = {
            "decisions": decisions,
            "anchors": dict(anchors),
            "secondary_anchors": dict(secondary_anchors),
            "spontaneous_drift_keys": sorted(spontaneous_drift_keys),
            "payload": payload,
        }
    return payload, output_rows


def assert_c1_heldout(traces: Sequence[Any]) -> None:
    """The C1 held-out fold is 60 traces / 30 pair groups (v1.2 amendment 2)."""

    groups = {str(t.pair_group_id) for t in traces}
    if len(traces) != EXPECTED_C1_HELDOUT_TRACES or len(groups) != EXPECTED_C1_HELDOUT_GROUPS:
        raise SystemExit(
            f"C1 held-out fold holds {len(traces)} traces / {len(groups)} groups != "
            f"{EXPECTED_C1_HELDOUT_TRACES}/{EXPECTED_C1_HELDOUT_GROUPS}"
        )


def matched_alpha_comparison(
    cells: dict[str, dict[str, Any]],
    decisions: dict[str, dict[str, dict[str, trm3.DecisionStream]]],
    *,
    anchors: dict[str, int | None] | None,
    spontaneous_drift_keys: Sequence[str] = (),
) -> dict[str, Any]:
    """P1 at MATCHED effective budgets (prereg v1.2 amendment 5).

    TRM-3's three conformal p-values can only take the values ``j/(n+1)``, so its joint
    budget is ``alpha_eff = sum_c floor((n+1) w_c alpha)/(n+1)`` -- 0.086 at n = 80 against
    B-M's 0.099, a 13% handicap in the direction that makes TRM-3's preregistered "net gain
    >= 3" harder.  B-M is therefore also evaluated at ``alpha_matched`` (the largest
    attainable single-channel level not above ``alpha_eff``), and both columns -- matched
    and nominal -- go into ``result.json``.  ``p_fused`` is alpha-free, so the matched
    column is decided from the streams already computed, not by rescoring.
    """

    if "trm3" not in cells or "m_only" not in cells:
        return {
            "status": "not_evaluated",
            "reason": "the matched-alpha P1 column needs both trm3 and m_only in one run",
        }
    trm3_budget = cells["trm3"]["alpha_budget"]
    m_budget = cells["m_only"]["alpha_budget"]
    by_half: dict[str, Any] = {}
    for half, block in trm3_budget["by_half"].items():
        n_m = m_budget["by_half"].get(half, {}).get("n_reference", block["n_reference"])
        matched = trm3.matched_alpha(n_m, block["effective"]["alpha_eff"])
        by_half[half] = {
            "trm3_alpha_eff": block["effective"]["alpha_eff"],
            "trm3_n_reference": block["n_reference"],
            "b_m_n_reference": n_m,
            "alpha_matched": matched["alpha_matched"],
            "rank": matched["rank"],
        }
    alpha_matched = min(v["alpha_matched"] for v in by_half.values())
    block: dict[str, Any] = {
        "status": "evaluated",
        "alpha_nominal": trm3_budget["alpha"],
        "trm3_alpha_eff": trm3_budget["alpha_eff"],
        "trm3_alpha_eff_min": trm3_budget["alpha_eff_min"],
        "b_m_alpha_eff_nominal": m_budget["alpha_eff"],
        "alpha_matched": alpha_matched,
        "by_half": by_half,
        "note": (
            "B-M evaluated at alpha_matched; the nominal-alpha comparison stays in the "
            "'mcnemar' block as the secondary column (prereg section 7, v1.2)"
        ),
    }
    m_target = decisions.get("m_only", {}).get("target", {})
    if m_target:
        block["b_m_at_matched_alpha"] = trm3.alpha_sweep(
            m_target,
            [alpha_matched],
            anchors=anchors,
            spontaneous_drift=spontaneous_drift_keys,
        )
    trm3_target = decisions.get("trm3", {}).get("target", {})
    if anchors and m_target and trm3_target:
        matched_hits = (
            block["b_m_at_matched_alpha"].get(f"{alpha_matched:g}", {})
        ).get("primary_event_hits_plus_8")
        own = trm3.alpha_sweep(
            trm3_target,
            [trm3_budget["alpha"]],
            anchors=anchors,
            spontaneous_drift=spontaneous_drift_keys,
        )
        own_hits = own.get(f"{float(trm3_budget['alpha']):g}", {}).get(
            "primary_event_hits_plus_8"
        )
        if matched_hits and own_hits:
            block["mcnemar_matched"] = trm3.paired_mcnemar(own_hits, matched_hits)
    return block


def main() -> None:
    args = _args()
    torch.set_num_threads(args.threads)
    variants = [v.strip() for v in args.variant.split(",") if v.strip()]
    for variant in variants:
        if variant not in trm3.ALL_VARIANT_CHANNELS:
            raise SystemExit(f"unknown variant {variant!r}")
    exploratory_requested = [v for v in variants if trm3.is_exploratory_variant(v)]
    if exploratory_requested and not args.exploratory:
        raise SystemExit(
            "the EXPLORATORY router-probability variants "
            f"({', '.join(exploratory_requested)}) are post-hoc and not preregistered; "
            "pass --exploratory to run them (the result is then labelled "
            f"{EXPLORATORY_LABEL})"
        )
    # additive: only an exploratory probability variant needs the router softmax
    global LOAD_WITH_PROBABILITIES
    LOAD_WITH_PROBABILITIES = bool(exploratory_requested)

    smoke = bool(args.routine_only_smoke)
    target = args.target
    columns = ["D", "C1"] if args.calibration == "both" else [args.calibration]
    alpha = float(args.alpha)
    alpha_grid = (
        tuple(float(a) for a in str(args.alpha_grid).split(",") if a.strip())
        if args.alpha_grid
        else ()
    )
    # prereg v1.2 amendment 7 -- checked BEFORE a single trace is loaded
    discipline = data_discipline_guard(
        args.freeze_commit, smoke=smoke, exploratory=bool(args.exploratory)
    )

    # ---- target pool (loaded once, shared by both calibration columns) ----
    if target in ("b1", "b2"):
        target_traces = load_pool(target)
    elif target == "h384":
        target_traces = load_pool("h384")
    else:
        target_traces = load_pool("c1", folds=C1_HELDOUT_FOLDS)
        assert_c1_heldout(target_traces)
    if smoke:
        target_traces = drop_attack_arm(target_traces)

    spontaneous = spontaneous_drift_traces(target_traces)
    spontaneous_keys = {trm3.trace_key(t) for t in spontaneous}

    # ---- anchors ----------------------------------------------------------
    secondary_anchors: dict[str, int | None] = {}
    anchor_labels = {"primary": "none", "secondary": "none"}
    label_counts: dict[str, Any] = {}
    if smoke:
        anchors: dict[str, int | None] = {}
    elif target in ("b1", "b2"):
        label_counts["core_label_files"] = assert_core_label_files()
        anchors = core_anchors(target_traces)
        anchor_labels = {"primary": "product_onset|topic_entry_onset", "secondary": "none"}
    elif target == "h384":
        label_counts["h384_engagement_classes"] = assert_h384_label_counts(target_traces)
        anchors, secondary_anchors = h384_anchors(target_traces)
        anchor_labels = {"primary": "engagement_onset", "secondary": "execution_onset"}
    else:
        anchors = {}

    # ---- run every calibration column -------------------------------------
    column_payloads: dict[str, dict[str, Any]] = {}
    output_rows: list[dict[str, Any]] = []
    for calibration_name in columns:
        payload, rows = run_column(
            calibration_name,
            target=target,
            target_traces=target_traces,
            variants=variants,
            anchors=anchors,
            secondary_anchors=secondary_anchors,
            anchor_labels=anchor_labels,
            spontaneous_drift_keys=spontaneous_keys,
            smoke=smoke,
            emit_evidence=bool(args.emit_evidence),
            outputs_mode=args.outputs,
            cand_a_reference=args.cand_a_reference,
            alpha=alpha,
            alpha_grid=alpha_grid,
        )
        column_payloads[calibration_name] = payload
        for row in rows:
            row["calibration_column"] = calibration_name
        output_rows.extend(rows)

    g5 = apply_g5(column_payloads) if len(column_payloads) > 1 else {}

    # ---- G5 from a previously written column (single-column invocations) ----
    if args.gate_reference is not None and args.gate_reference.exists() and len(columns) == 1:
        other = json.loads(args.gate_reference.read_text(encoding="utf-8"))
        calibration_name = columns[0]
        cells = column_payloads[calibration_name]["variants"]
        for variant, gate_block in column_payloads[calibration_name]["gates"].items():
            far_other = (
                _reference_variants(other)
                .get(variant, {})
                .get("sets", {})
                .get("target", {})
                .get("far", {})
                .get("pooled")
            )
            far_here = cells[variant]["sets"].get("target", {}).get("far", {}).get("pooled")
            if far_other is None or far_here is None:
                continue
            c1_far = far_here if calibration_name == "C1" else far_other
            self_far = far_other if calibration_name == "C1" else far_here
            gate_block["G5"] = trm3.check_gates(
                {"far_pooled_c1": c1_far, "far_pooled_self": self_far}
            )["G5"]
            # v1.2 amendment 8: refresh the summary, which v1.1 left stale here
            evaluated = [
                g
                for name, g in gate_block.items()
                if name != "summary" and isinstance(g, dict) and g.get("status") == "evaluated"
            ]
            gate_block["summary"] = {
                "evaluated": len(evaluated),
                "passed": sum(1 for g in evaluated if g["pass"]),
                "failed": [g["gate"] for g in evaluated if not g["pass"]],
            }

    run_name = args.run_name or f"{target}_{args.calibration}" + ("_smoke" if smoke else "")
    output_dir = Path(args.output_root) / run_name
    output_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "schema_version": 3,
        "code_commit": harness.code_commit(),
        "dirty": discipline["dirty"],
        "data_discipline": discipline,
        "prereg": {
            "path": str(PREREG_PATH.relative_to(ROOT)),
            "sha256": sha256(PREREG_PATH) if PREREG_PATH.exists() else None,
            "amendments": "v1.1 (section 11) + v1.2 (section 12)",
        },
        "target": target,
        "calibration": args.calibration,
        "calibration_columns": columns,
        "routine_only_smoke": smoke,
        "alpha": alpha,
        "alpha_grid": list(alpha_grid),
        "label_counts": label_counts,
        "variants_requested": variants,
        "anchor_policy": anchor_labels,
        "spontaneous_drift_group": {
            "keys": sorted(spontaneous_keys),
            "trace_ids": sorted(str(t.trace_id) for t in spontaneous),
            "rule": (
                "prereg v1.1 amendment 4; labelled exclusion from the routine fit, the "
                "calibration pool and every FAR denominator"
            ),
        },
        "inputs_sha256": input_hashes(target, args.calibration),
        "columns": column_payloads,
        "g5": g5,
    }
    if discipline.get("exploratory"):
        result["exploratory"] = True
        result["label"] = EXPLORATORY_LABEL
        result["exploratory_variants"] = sorted(exploratory_requested)
        result["exploratory_rule"] = EXPLORATORY_RULE
    if len(columns) == 1:
        # single-column runs keep the flat v1 shape as well, so the existing readers and
        # --gate-reference keep working
        result.update(
            {
                key: column_payloads[columns[0]][key]
                for key in (
                    "pools",
                    "channel_fit_seconds",
                    "g8_reference",
                    "variants",
                    "gates",
                    "mcnemar",
                    "p1_matched_alpha",
                )
            }
        )
    (output_dir / "result.json").write_text(
        json.dumps(result, indent=1, sort_keys=False) + "\n", encoding="utf-8"
    )
    if args.outputs != "none":
        with (output_dir / "outputs.jsonl").open("w", encoding="utf-8") as handle:
            for row in output_rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
    (output_dir / "tables.md").write_text(tables_markdown(result), encoding="utf-8")

    print(f"wrote {output_dir}")
    for calibration_name, payload in column_payloads.items():
        print(f"  calibration {calibration_name}:")
        for variant, cell in payload["variants"].items():
            block = cell["sets"].get("target", {})
            far = block.get("far", {})
            horizon = block.get("horizon") or {}
            budget = cell.get("alpha_budget") or {}
            print(
                f"    {variant:>18}: routine FAR pooled={far.get('pooled')} "
                f"clean={far.get('clean')} benign={far.get('benign')} "
                f"matched_group={far.get('matched_group')} "
                f"half_gap={block.get('far_half_gap')} "
                f"censored_endpoints={horizon.get('censored_endpoint_fraction')} "
                f"censored_traces={horizon.get('censored_trace_fraction')} "
                f"alpha_eff={budget.get('alpha_eff')}"
            )
            for side, side_block in cell.get("sets", {}).items():
                if side == "target":
                    continue
                side_far = side_block.get("far", {})
                print(
                    f"    {'':>18}  [{side}] pooled={side_far.get('pooled')} "
                    f"clean={side_far.get('clean')} benign={side_far.get('benign')} "
                    f"matched_group={side_far.get('matched_group')}"
                )
            if smoke:
                continue  # never print a target/attack metric before the freeze commit


def _reference_variants(payload: dict[str, Any]) -> dict[str, Any]:
    """``variants`` block of a result.json written by either result schema."""

    if "variants" in payload:
        return payload["variants"]
    columns = payload.get("columns", {})
    for name in ("C1", "D"):
        if name in columns:
            return columns[name].get("variants", {})
    return {}


def input_hashes(target: str, calibration_name: str) -> dict[str, Any]:
    hashes: dict[str, Any] = {}
    try:
        hashes["core"] = rio.dataset_hashes()
    except Exception as error:  # pragma: no cover - defensive
        hashes["core"] = {"error": str(error)}
    for name, path in (
        ("h384_sample_index", H384_INDEX),
        ("c1_sample_index", C1_INDEX),
        ("h384_engagement_adjudications", H384_ENGAGEMENT),
        ("product_onset_labels", PRODUCT_ONSET_FILE),
        ("topic_entry_labels", TOPIC_ENTRY_FILE),
    ):
        hashes[name] = {"path": str(path), "sha256": sha256(path) if path.exists() else None}
    hashes["target"] = target
    hashes["calibration"] = calibration_name
    return hashes


if __name__ == "__main__":
    main()
