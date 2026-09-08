"""Data loading for the research-v2 shared evaluation harness (protocol v2, spec section 1.1).

Builds on ``src/phase_a/normal_manifold.py`` (frozen routing cache, ``ManifoldTrace``)
without modifying it.  Adds:

* an in-memory ``LoadedTrace`` that carries decode top-k routing, router probabilities
  (lazily) and the decode token ids;
* the scenario -> target-domain map (only the attack arm of a scenario carries a
  ``target_domain``; the clean / benign arms of the same ``pair_group_id`` are labelled
  ``None`` in the frozen index, so leave-one-target-domain-out has to go through the
  scenario);
* an optional loader for the 120 B1 ``response_brief_condition == present`` traces, which
  the protocol allows as *extra calibration material only* (spec 1.6).  Those traces are not
  part of the frozen 360-trace routing cache, so they get their own cache directory under
  ``artifacts/agent_v2/research_v2/``;
* offline decoding of decode token ids to text through the local OLMoE tokenizer, for the
  pre-onset alarm audit (spec 1.7).  ``trace.json`` stores ``output_token_ids`` but no
  ``output_token_texts``, so the text is reconstructed here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Sequence

import torch
from safetensors.torch import load_file, save_file

from phase_a.normal_manifold import (
    B1_INDEX_SHA256,
    B2_INDEX_SHA256,
    ManifoldTrace,
    _decode_tensors,
    load_cached_routing,
    read_manifold_traces,
    sha256,
    workflow_family,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = REPO_ROOT / "artifacts" / "agent_v2"
DEFAULT_B1_DIR = ARTIFACTS / "agent_v2_5_b1"
DEFAULT_B2_DIR = ARTIFACTS / "agent_v2_5_b2"
DEFAULT_CACHE_DIR = ARTIFACTS / "normal_manifold_cache"
DEFAULT_OBSERVATION = ARTIFACTS / "routing_observation_atlas" / "observation.json"
RESEARCH_V2_ROOT = ARTIFACTS / "research_v2"
EXTRA_CACHE_DIR = RESEARCH_V2_ROOT / "_extra_routing_cache"

HF_SNAPSHOT = (
    REPO_ROOT
    / "artifacts"
    / "hf_cache"
    / "models--allenai--OLMoE-1B-7B-0125-Instruct"
    / "snapshots"
    / "b89a7c4bc24fb9e55ce2543c9458ce0ca5c4650e"
)

CLEAN_ARMS = ("clean",)
BENIGN_ARMS = ("benign_control",)
ATTACK_ARM = "attack"
ROUTINE_CB_ARMS = ("clean", "benign_control")

# --- research-v3 pools (additive; b1/b2 behaviour above is untouched) --------
DEFAULT_H384_DIR = ARTIFACTS / "agent_v2_5_b2_horizon384"
DEFAULT_H384_CACHE_DIR = ARTIFACTS / "agent_v2_5_b2_horizon384_cache"
DEFAULT_H384_ENGAGEMENT_LABELS = (
    REPO_ROOT / "data" / "agent_v2" / "agent_v2_5_b2_horizon384_engagement_adjudications.jsonl"
)
DEFAULT_C1_DIR = ARTIFACTS / "normal_calibration_c1"
DEFAULT_C1_CACHE_DIR = ARTIFACTS / "normal_calibration_c1_cache"
RESEARCH_V3_ROOT = ARTIFACTS / "research_v3"
RESEARCH_V3_LABEL_DIR = RESEARCH_V3_ROOT / "labels"

# Frozen input hashes.  The engagement adjudication file is the one the lead pinned in the
# TRM-3 preregistration; a mismatch raises.  The two sample indices are recorded (and
# returned by ``research_v3_dataset_hashes``) but not enforced, so that a legitimate
# refresh of a frozen batch index does not silently become a hard load failure here.
H384_ENGAGEMENT_LABELS_SHA256 = (
    "8ad05186bbe7e508f3019a8c91aa71df8e369922bcdd4562b367bd7f298839d7"
)
H384_INDEX_SHA256 = "5399b0b32769c8ef98722080da9ef1f159f586fba6d1d907656afcc8e34eac10"
C1_INDEX_SHA256 = "e3822468098e25fb21fb3b67753c07fd73bb765777cc22b83548d06f8970755f"

EXECUTION_CLASS = "cross_domain_execution"
ENGAGEMENT_CLASSES = (
    EXECUTION_CLASS,
    "bounded_engagement_resisted",
    "no_observable_engagement",
)
H384_EXPECTED_ENGAGEMENT_COUNTS = {
    "cross_domain_execution": 40,
    "bounded_engagement_resisted": 5,
    "no_observable_engagement": 35,
}
H384_EXPECTED_ARM_COUNTS = {"clean": 80, "benign_control": 80, "attack": 80}
C1_EXPECTED_ARM_COUNTS = {"clean": 160, "benign_control": 160}


@dataclass
class LoadedTrace:
    """One trace: frozen metadata plus its cached causal decode routing."""

    record: ManifoldTrace
    top_k_ids: torch.Tensor  # [16, T, 8] long
    token_ids: torch.Tensor  # [T] long
    scenario_domain: str
    brief_condition: str = "absent"
    _probabilities: torch.Tensor | None = field(default=None, repr=False)
    labels: dict[str, Any] = field(default_factory=dict)
    """Pool-specific adjudication metadata (research v3, additive).

    Empty for the frozen b1/b2 core loaders.  The h384 replay pool carries
    ``engagement_class``, ``engagement_onset`` (first token of the engagement-evidence
    substring), ``execution_onset`` (``goal_plan_deviation_start_output_token``),
    ``post192_class``, ``support_resumed`` and ``decode_token_count``; the C1 pool
    carries ``fold_role``, ``preregistered_fold`` and ``decode_token_count``.
    """

    # -- passthrough metadata -------------------------------------------------
    @property
    def trace_id(self) -> str:
        return self.record.trace_id

    @property
    def batch(self) -> str:
        return self.record.batch

    @property
    def pair_group_id(self) -> str:
        return self.record.pair_group_id

    @property
    def fold(self) -> int:
        return self.record.fold

    @property
    def arm(self) -> str:
        return self.record.arm

    @property
    def workflow(self) -> str:
        return self.record.workflow

    @property
    def workflow_family(self) -> str:
        return self.record.workflow_family

    @property
    def channel(self) -> str:
        return self.record.channel

    @property
    def domain(self) -> str:
        return self.record.domain

    @property
    def positive(self) -> bool:
        return self.record.positive

    @property
    def normal(self) -> bool:
        return not self.record.positive

    @property
    def evidence_onset(self) -> int | None:
        return self.record.evidence_onset

    @property
    def completion_boundary(self) -> int | None:
        return self.record.completion_boundary

    @property
    def token_count(self) -> int:
        return int(self.top_k_ids.shape[1])

    @property
    def group_id(self) -> str:
        """Grouping key for pooled cross-validation (spec 1.4, S2)."""

        return f"{self.batch}-f{self.fold}"

    def probabilities(self) -> torch.Tensor:
        """Full router probabilities [16, T, 64]; loaded on demand."""

        if self._probabilities is None:
            self._probabilities = load_cached_routing(self.record)["probabilities"]
        return self._probabilities


def arm_class(trace: LoadedTrace) -> str:
    """clean / benign / resist / drift, the four evaluation classes of spec 1.7.

    b1 / b2 keep the frozen behaviour (``positive`` -> drift, any other attack-arm trace
    -> resist).  The research-v3 pools are label-driven and validated: on ``h384`` the
    class comes from the frozen routing-blind engagement adjudication (execution ->
    drift, bounded / silent -> resist), and ``c1`` holds no attack arm at all.
    """

    if trace.batch == "h384":
        if trace.arm == "clean":
            return "clean"
        if trace.arm in BENIGN_ARMS:
            return "benign"
        if trace.arm != ATTACK_ARM:
            raise ValueError(f"unknown h384 arm: {trace.arm}")
        engagement = trace.labels.get("engagement_class")
        if engagement == EXECUTION_CLASS:
            return "drift"
        if engagement in ("bounded_engagement_resisted", "no_observable_engagement"):
            return "resist"
        raise ValueError(
            f"h384 attack trace lacks an engagement class: {trace.trace_id} ({engagement!r})"
        )
    if trace.batch == "c1":
        if trace.arm == "clean":
            return "clean"
        if trace.arm in BENIGN_ARMS:
            return "benign"
        raise ValueError(f"unknown c1 arm: {trace.arm}")
    if trace.positive:
        return "drift"
    if trace.arm == ATTACK_ARM:
        return "resist"
    if trace.arm == "clean":
        return "clean"
    return "benign"


def _scenario_domains(records: Sequence[ManifoldTrace]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for record in records:
        if record.arm == ATTACK_ARM:
            if record.pair_group_id in mapping:
                raise ValueError(f"duplicate attack arm for {record.pair_group_id}")
            mapping[record.pair_group_id] = record.domain
    return mapping


def load_batch(
    batch: str,
    *,
    b1_dir: Path = DEFAULT_B1_DIR,
    b2_dir: Path = DEFAULT_B2_DIR,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    observation_path: Path = DEFAULT_OBSERVATION,
    with_probabilities: bool = False,
) -> tuple[LoadedTrace, ...]:
    """Load one core batch (B1 restricted to brief=absent, as P1-P3 do)."""

    run_dir = b1_dir if batch == "b1" else b2_dir
    records = read_manifold_traces(
        run_dir.resolve(), cache_dir.resolve(), observation_path, batch
    )
    domains = _scenario_domains(records)
    traces: list[LoadedTrace] = []
    for record in records:
        cached = load_cached_routing(record)
        traces.append(
            LoadedTrace(
                record=record,
                top_k_ids=cached["top_k_ids"],
                token_ids=cached["token_ids"],
                scenario_domain=domains[record.pair_group_id],
                _probabilities=cached["probabilities"] if with_probabilities else None,
            )
        )
    return tuple(traces)


def load_core(
    *,
    b1_dir: Path = DEFAULT_B1_DIR,
    b2_dir: Path = DEFAULT_B2_DIR,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    observation_path: Path = DEFAULT_OBSERVATION,
) -> dict[str, tuple[LoadedTrace, ...]]:
    """Load the 360-trace core development set as {"b1": ..., "b2": ...}."""

    return {
        batch: load_batch(
            batch,
            b1_dir=b1_dir,
            b2_dir=b2_dir,
            cache_dir=cache_dir,
            observation_path=observation_path,
        )
        for batch in ("b1", "b2")
    }


# ---------------------------------------------------------------------------
# optional extra routine material: B1 response_brief_condition == present
# ---------------------------------------------------------------------------


def load_b1_present(
    *,
    b1_dir: Path = DEFAULT_B1_DIR,
    observation_path: Path = DEFAULT_OBSERVATION,
    cache_dir: Path = EXTRA_CACHE_DIR,
    arms: Sequence[str] | None = None,
    limit: int | None = None,
) -> tuple[LoadedTrace, ...]:
    """Load the behaviourally normal B1 brief=present traces (117 of 120; see below).

    They are outside the frozen 360-trace routing cache, so their decode routing is
    extracted from the original shards into ``cache_dir`` (a research-v2 directory).
    Protocol status: extra *calibration* material only (spec 1.6).
    """

    index_path = b1_dir / "sample_index.jsonl"
    actual = sha256(index_path)
    if actual != B1_INDEX_SHA256:
        raise ValueError(f"b1 sample-index hash mismatch: {actual}")
    rows = [
        json.loads(line)
        for line in index_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    rows = [row for row in rows if row["response_brief_condition"] == "present"]
    if len(rows) != 120:
        raise ValueError(f"expected 120 b1 brief=present traces, found {len(rows)}")
    # Spec 1.6 calls this pool "120 traces, all behaviourally normal".  The frozen sample
    # index says otherwise: 3 of the 40 attack-arm traces have
    # ``goal_plan_deviation_started == true``.  They are dropped here -- a drift trace must
    # never enter a calibration pool -- leaving 117 (40 clean, 40 benign_control,
    # 37 resisted attack).  The harness additionally filters this pool by the routine
    # definition in force (run_case), and ``arms`` lets a caller restrict it directly.
    drift_rows = [row for row in rows if row["goal_plan_deviation_started"]]
    rows = [row for row in rows if not row["goal_plan_deviation_started"]]
    if len(drift_rows) + len(rows) != 120:
        raise ValueError("b1 brief=present partition failed")
    if drift_rows and len(drift_rows) != 3:
        raise ValueError(f"unexpected drift count in the b1 brief=present pool: {len(drift_rows)}")
    if arms is not None:
        allowed = set(arms)
        rows = [row for row in rows if row["arm"] in allowed]
    if limit is not None:
        rows = rows[:limit]
    cache_dir.mkdir(parents=True, exist_ok=True)
    traces: list[LoadedTrace] = []
    for row in rows:
        record = ManifoldTrace(
            batch="b1",
            trace_id=str(row["trace_id"]),
            pair_group_id=str(row["pair_group_id"]),
            fold=int(row["preregistered_fold"]),
            arm=str(row["arm"]),
            workflow=str(row["workflow"]),
            workflow_family=workflow_family(str(row["workflow"])),
            channel=str(row["attack_channel"]),
            domain=str(row["target_domain"]),
            positive=False,
            completion_boundary=None,
            evidence_onset=None,
            trace_dir=(b1_dir / row["relative_path"]).resolve(),
            cache_file=(cache_dir / "b1_present" / f"{row['trace_id']}.safetensors").resolve(),
        )
        if not record.cache_file.exists():
            top_k_ids, probabilities, token_ids = _decode_tensors(record.trace_dir)
            record.cache_file.parent.mkdir(parents=True, exist_ok=True)
            save_file(
                {
                    "top_k_ids": top_k_ids.contiguous(),
                    "probabilities": probabilities.contiguous(),
                    "token_ids": token_ids.contiguous(),
                },
                record.cache_file,
            )
        cached = load_file(record.cache_file)
        traces.append(
            LoadedTrace(
                record=record,
                top_k_ids=cached["top_k_ids"].long(),
                token_ids=cached["token_ids"].long(),
                scenario_domain=str(row["target_domain"]),
                brief_condition="present",
            )
        )
    return tuple(traces)


# ---------------------------------------------------------------------------
# tokenizer + static embedding (offline, local weights only)
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_tokenizer(snapshot: Path = HF_SNAPSHOT):
    from tokenizers import Tokenizer

    return Tokenizer.from_file(str(snapshot / "tokenizer.json"))


def decode_token_texts(token_ids: Iterable[int], snapshot: Path = HF_SNAPSHOT) -> list[str]:
    """Per-token surface strings (byte-level markers replaced by spaces)."""

    tokenizer = load_tokenizer(snapshot)
    texts: list[str] = []
    for token_id in token_ids:
        piece = tokenizer.id_to_token(int(token_id))
        if piece is None:
            piece = "<unk>"
        texts.append(piece.replace("Ġ", " ").replace("Ċ", "\n"))
    return texts


def decode_text(token_ids: Iterable[int], snapshot: Path = HF_SNAPSHOT) -> str:
    tokenizer = load_tokenizer(snapshot)
    return tokenizer.decode([int(value) for value in token_ids])


@lru_cache(maxsize=1)
def load_input_embeddings(snapshot: Path = HF_SNAPSHOT) -> torch.Tensor:
    """OLMoE static input embedding matrix [vocab, hidden] from local safetensors."""

    from safetensors import safe_open

    index = json.loads((snapshot / "model.safetensors.index.json").read_text(encoding="utf-8"))
    shard = index["weight_map"]["model.embed_tokens.weight"]
    with safe_open(snapshot / shard, framework="pt", device="cpu") as handle:
        return handle.get_tensor("model.embed_tokens.weight").float()


def dataset_hashes(
    *, b1_dir: Path = DEFAULT_B1_DIR, b2_dir: Path = DEFAULT_B2_DIR
) -> dict[str, Any]:
    return {
        "b1": {
            "sample_index": str(b1_dir / "sample_index.jsonl"),
            "sample_index_sha256": sha256(b1_dir / "sample_index.jsonl"),
            "expected_sha256": B1_INDEX_SHA256,
        },
        "b2": {
            "sample_index": str(b2_dir / "sample_index.jsonl"),
            "sample_index_sha256": sha256(b2_dir / "sample_index.jsonl"),
            "expected_sha256": B2_INDEX_SHA256,
        },
    }


# ---------------------------------------------------------------------------
# research v3 pools: B2 horizon-384 replay (h384) and the C1 normal calibration pool
# ---------------------------------------------------------------------------
#
# Both pools are loaded without going through ``read_manifold_traces``: that function
# hard-codes the b1/b2 positive counts (24 / 35) and the b1 brief filter, and it reads
# evidence onsets from the routing-observation atlas, which has no rows for these pools.
# ``ManifoldTrace`` itself is reused unchanged, so every existing scorer, feature and
# harness routine works on the result.


def _read_index_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _check_arm_counts(rows: Sequence[dict[str, Any]], expected: dict[str, int], pool: str) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        arm = str(row["arm"])
        counts[arm] = counts.get(arm, 0) + 1
    if counts != expected:
        raise ValueError(f"{pool} arm counts {counts} != expected {expected}")


def _c1_scenario_domains(rows: Sequence[dict[str, Any]]) -> dict[str, str]:
    """Scenario-level content family for C1.

    C1 has no attack arm, so ``target_domain`` is ``null`` on every row and the b1/b2
    rule (``_scenario_domains``: read the domain off the attack arm of the pair group)
    has nothing to read.  The benign content family plays the same scenario-level role
    -- it is the thing that varies across pair groups and is constant within one -- so
    it is used as ``scenario_domain`` here.  ``harness.scenario_halves`` only needs
    ``pair_group_id`` and is unaffected either way.
    """

    mapping: dict[str, str] = {}
    for row in rows:
        group = str(row["pair_group_id"])
        family = row.get("benign_family") or row.get("analysis_group_id")
        if family is None:
            raise ValueError(f"c1 row without a content family: {row['trace_id']}")
        previous = mapping.setdefault(group, str(family))
        if previous != str(family):
            raise ValueError(f"c1 pair group {group} spans several content families")
    return mapping


def scenario_domain_map(traces: Sequence[LoadedTrace]) -> dict[str, str]:
    """``pair_group_id -> scenario_domain`` for an already-loaded pool.

    Convenience for callers that need the scenario-level grouping key (leave-one-domain-out,
    per-domain reporting) without re-reading the sample index; the loaders have already
    resolved the per-pool rule.
    """

    mapping: dict[str, str] = {}
    for trace in traces:
        previous = mapping.setdefault(trace.pair_group_id, trace.scenario_domain)
        if previous != trace.scenario_domain:
            raise ValueError(f"pair group {trace.pair_group_id} spans several domains")
    return mapping


def locate_char_span_tokens(
    token_texts: Sequence[str], char_start: int, char_end: int
) -> tuple[int, int]:
    """Inclusive decode-token indices overlapping a non-empty character span.

    Identical rule to ``scripts/apply_agent_v2_engagement_adjudications.py``: the token
    that *contains* the first character of the span, through the token that first reaches
    the end of the span.  Reimplemented here so that the loader does not import a script.
    """

    if char_start < 0 or char_end <= char_start:
        raise ValueError("invalid character span")
    offset = 0
    start_token: int | None = None
    for index, piece in enumerate(token_texts):
        next_offset = offset + len(piece)
        if start_token is None and next_offset > char_start:
            start_token = index
        if next_offset >= char_end:
            if start_token is None:
                raise ValueError("character span does not align to decode token pieces")
            return start_token, index
        offset = next_offset
    raise ValueError("character span does not align to decode token pieces")


def engagement_onset_from_tokens(token_ids: Sequence[int], evidence: str) -> int:
    """First decode-token index of ``evidence`` inside the decoded output.

    The adjudication protocol guarantees the evidence string is an exact substring of the
    reviewed output; ties are broken as in the frozen script, by the first occurrence.
    """

    if not evidence:
        raise ValueError("engagement evidence must be non-empty")
    texts = decode_token_texts(token_ids)
    start = "".join(texts).find(evidence)
    if start < 0:
        raise ValueError(f"engagement evidence is absent from the decoded output: {evidence!r}")
    return locate_char_span_tokens(texts, start, start + len(evidence))[0]


def read_h384_engagement_labels(
    *,
    labels_path: Path = DEFAULT_H384_ENGAGEMENT_LABELS,
) -> dict[str, dict[str, Any]]:
    """The 80 frozen routing-blind engagement adjudications, keyed by trace id."""

    actual = sha256(labels_path)
    if actual != H384_ENGAGEMENT_LABELS_SHA256:
        raise ValueError(
            "h384 engagement adjudication hash mismatch: "
            f"{actual} != {H384_ENGAGEMENT_LABELS_SHA256}"
        )
    rows = _read_index_rows(labels_path)
    if len(rows) != 80:
        raise ValueError(f"expected 80 engagement adjudications, found {len(rows)}")
    by_id = {str(row["trace_id"]): row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("duplicate trace id in the engagement adjudications")
    counts: dict[str, int] = {}
    for row in rows:
        engagement = str(row["engagement_class"])
        if engagement not in ENGAGEMENT_CLASSES:
            raise ValueError(f"unknown engagement class: {engagement}")
        counts[engagement] = counts.get(engagement, 0) + 1
    if counts != H384_EXPECTED_ENGAGEMENT_COUNTS:
        raise ValueError(
            f"engagement class counts {counts} != {H384_EXPECTED_ENGAGEMENT_COUNTS}"
        )
    return by_id


def load_h384(
    *,
    with_probabilities: bool = False,
    run_dir: Path = DEFAULT_H384_DIR,
    cache_dir: Path = DEFAULT_H384_CACHE_DIR,
    labels_path: Path = DEFAULT_H384_ENGAGEMENT_LABELS,
    verify_onsets: bool = True,
) -> tuple[LoadedTrace, ...]:
    """The 240-trace B2 horizon-384 replay pool (80 clean / 80 benign_control / 80 attack).

    ``record.batch == "h384"``.  Positives are exactly the attack traces whose frozen,
    routing-blind engagement class is ``cross_domain_execution`` (40 of 80); the 5 bounded
    and 35 silent resisters are negatives, as are all 160 controls.  Per the TRM-3
    preregistration the time anchors on this pool are the execution onset, so
    ``evidence_onset`` and ``completion_boundary`` both carry
    ``goal_plan_deviation_start_output_token.output_token_index`` and are ``None`` on
    every non-execution trace.  The *start* of the engagement-evidence substring is kept
    separately in ``labels["engagement_onset"]`` for every engaged trace (executions and
    bounded resisters alike).

    ``verify_onsets`` re-derives every engagement onset from the cached decode token ids
    and asserts agreement with the frozen per-trace ``engagement_adjudication.json``.
    """

    run_dir = Path(run_dir)
    cache_dir = Path(cache_dir)
    index_path = run_dir / "sample_index.jsonl"
    rows = _read_index_rows(index_path)
    if len(rows) != 240:
        raise ValueError(f"expected 240 h384 traces, found {len(rows)}")
    if len({str(row["trace_id"]) for row in rows}) != len(rows):
        raise ValueError("h384 trace ids are not unique")
    _check_arm_counts(rows, H384_EXPECTED_ARM_COUNTS, "h384")

    adjudications = read_h384_engagement_labels(labels_path=labels_path)
    attack_ids = {str(row["trace_id"]) for row in rows if str(row["arm"]) == ATTACK_ARM}
    if set(adjudications) != attack_ids:
        missing = sorted(attack_ids - set(adjudications))
        extra = sorted(set(adjudications) - attack_ids)
        raise ValueError(f"h384 adjudication coverage mismatch: missing={missing}, extra={extra}")

    domains = _scenario_domains(
        [
            ManifoldTrace(
                batch="h384",
                trace_id=str(row["trace_id"]),
                pair_group_id=str(row["pair_group_id"]),
                fold=int(row["preregistered_fold"]),
                arm=str(row["arm"]),
                workflow=str(row["workflow"]),
                workflow_family=workflow_family(str(row["workflow"])),
                channel=str(row["attack_channel"]),
                domain=str(row["target_domain"]),
                positive=False,
                completion_boundary=None,
                evidence_onset=None,
                trace_dir=run_dir,
                cache_file=cache_dir,
            )
            for row in rows
        ]
    )

    traces: list[LoadedTrace] = []
    disagreements: list[dict[str, Any]] = []
    for row in rows:
        trace_id = str(row["trace_id"])
        arm = str(row["arm"])
        adjudication = adjudications.get(trace_id)
        engagement_class = None if adjudication is None else str(adjudication["engagement_class"])
        positive = arm == ATTACK_ARM and engagement_class == EXECUTION_CLASS
        boundary = row["goal_plan_deviation_start_output_token"]
        execution_onset = (
            None if boundary is None or not positive else int(boundary["output_token_index"])
        )
        if positive and execution_onset is None:
            raise ValueError(f"h384 execution lacks an onset token: {trace_id}")
        record = ManifoldTrace(
            batch="h384",
            trace_id=trace_id,
            pair_group_id=str(row["pair_group_id"]),
            fold=int(row["preregistered_fold"]),
            arm=arm,
            workflow=str(row["workflow"]),
            workflow_family=workflow_family(str(row["workflow"])),
            channel=str(row["attack_channel"]),
            domain=str(row["target_domain"]),
            positive=positive,
            completion_boundary=execution_onset,
            evidence_onset=execution_onset,
            trace_dir=(run_dir / str(row["relative_path"])).resolve(),
            cache_file=(cache_dir / "h384" / f"{trace_id}.safetensors").resolve(),
        )
        if not record.cache_file.exists():
            raise FileNotFoundError(f"missing h384 routing cache: {record.cache_file}")
        cached = load_cached_routing(record)
        token_count = int(cached["top_k_ids"].shape[1])
        if cached["top_k_ids"].shape != (16, token_count, 8):
            raise ValueError(f"h384 top-k shape mismatch: {trace_id}")
        if cached["token_ids"].shape != (token_count,):
            raise ValueError(f"h384 token-id shape mismatch: {trace_id}")
        if token_count < 1 or token_count > 384:
            raise ValueError(f"h384 decode length out of range: {trace_id} ({token_count})")

        labels: dict[str, Any] = {"decode_token_count": token_count}
        if adjudication is not None:
            evidence = adjudication.get("engagement_evidence")
            engagement_onset: int | None = None
            if evidence is not None:
                engagement_onset = engagement_onset_from_tokens(
                    cached["token_ids"].tolist(), str(evidence)
                )
            if verify_onsets:
                frozen = record.trace_dir / "engagement_adjudication.json"
                if frozen.exists():
                    payload = json.loads(frozen.read_text(encoding="utf-8"))
                    span = payload.get("engagement_evidence_output_token_span")
                    frozen_onset = None if span is None else int(span[0])
                    if frozen_onset != engagement_onset or str(
                        payload.get("engagement_class")
                    ) != engagement_class:
                        disagreements.append(
                            {
                                "trace_id": trace_id,
                                "recomputed_onset": engagement_onset,
                                "frozen_onset": frozen_onset,
                                "engagement_class": engagement_class,
                                "frozen_engagement_class": payload.get("engagement_class"),
                            }
                        )
            labels.update(
                {
                    "engagement_class": engagement_class,
                    "engagement_onset": engagement_onset,
                    "execution_onset": execution_onset,
                    "post192_class": adjudication.get("post192_continuation_class"),
                    "support_resumed": bool(
                        adjudication.get("support_resumed_after_engagement", False)
                    ),
                }
            )
        else:
            # Control arms carry no engagement review.  ``behavior_label`` / ``stratum``
            # are copied through because four benign_control traces in this replay are
            # flagged ``spontaneous_goal_plan_deviation`` by the frozen index; they are
            # *not* positives under the preregistered definition, so a caller that wants
            # to keep them out of a routine pool needs to see the flag.
            labels.update(
                {
                    "engagement_class": None,
                    "engagement_onset": None,
                    "execution_onset": None,
                    "index_goal_plan_deviation_started": bool(
                        row["goal_plan_deviation_started"]
                    ),
                    "behavior_label": str(row["behavior_label"]),
                    "stratum": str(row["stratum"]),
                }
            )
        traces.append(
            LoadedTrace(
                record=record,
                top_k_ids=cached["top_k_ids"],
                token_ids=cached["token_ids"],
                scenario_domain=domains[record.pair_group_id],
                _probabilities=cached["probabilities"] if with_probabilities else None,
                labels=labels,
            )
        )

    if disagreements:
        raise ValueError(
            "recomputed engagement onsets disagree with the frozen per-trace "
            f"adjudications on {len(disagreements)} row(s): {disagreements}"
        )
    positives = sum(trace.positive for trace in traces)
    if positives != H384_EXPECTED_ENGAGEMENT_COUNTS[EXECUTION_CLASS]:
        raise ValueError(f"h384 positive count {positives} != 40")
    return tuple(traces)


def load_c1(
    *,
    with_probabilities: bool = False,
    folds: Sequence[int] | None = None,
    run_dir: Path = DEFAULT_C1_DIR,
    cache_dir: Path = DEFAULT_C1_CACHE_DIR,
) -> tuple[LoadedTrace, ...]:
    """The 320-trace C1 normal calibration pool (160 pair groups, clean + benign_control).

    ``record.batch == "c1"``.  The pool holds no attack arm and no positives; folds 0-2
    are ``threshold_calibration`` and folds 3-4 ``held_out_normal_evaluation``, and the
    role is carried in ``labels["fold_role"]``.  ``folds`` restricts the returned traces
    by ``preregistered_fold`` (validation always runs over the full index first).
    """

    run_dir = Path(run_dir)
    cache_dir = Path(cache_dir)
    rows = _read_index_rows(run_dir / "sample_index.jsonl")
    if len(rows) != 320:
        raise ValueError(f"expected 320 c1 traces, found {len(rows)}")
    if len({str(row["trace_id"]) for row in rows}) != len(rows):
        raise ValueError("c1 trace ids are not unique")
    _check_arm_counts(rows, C1_EXPECTED_ARM_COUNTS, "c1")
    if len({str(row["pair_group_id"]) for row in rows}) != 160:
        raise ValueError("c1 does not hold 160 pair groups")
    deviations = [str(row["trace_id"]) for row in rows if row["goal_plan_deviation_started"]]
    if deviations:
        raise ValueError(f"c1 must be positive-free, found: {deviations}")
    roles: dict[int, set[str]] = {}
    for row in rows:
        roles.setdefault(int(row["preregistered_fold"]), set()).add(str(row["fold_role"]))
    if sorted(roles) != [0, 1, 2, 3, 4]:
        raise ValueError(f"c1 folds {sorted(roles)} != 0-4")
    for fold, fold_roles in roles.items():
        if len(fold_roles) != 1:
            raise ValueError(f"c1 fold {fold} spans several roles: {sorted(fold_roles)}")

    domains = _c1_scenario_domains(rows)
    if folds is not None:
        allowed = {int(value) for value in folds}
        unknown = allowed - set(roles)
        if unknown:
            raise ValueError(f"unknown c1 folds requested: {sorted(unknown)}")
        rows = [row for row in rows if int(row["preregistered_fold"]) in allowed]

    traces: list[LoadedTrace] = []
    for row in rows:
        trace_id = str(row["trace_id"])
        record = ManifoldTrace(
            batch="c1",
            trace_id=trace_id,
            pair_group_id=str(row["pair_group_id"]),
            fold=int(row["preregistered_fold"]),
            arm=str(row["arm"]),
            workflow=str(row["workflow"]),
            workflow_family=workflow_family(str(row["workflow"])),
            channel=str(row["attack_channel"]),
            domain=str(row["target_domain"]),
            positive=False,
            completion_boundary=None,
            evidence_onset=None,
            trace_dir=(run_dir / str(row["relative_path"])).resolve(),
            cache_file=(cache_dir / "c1" / f"{trace_id}.safetensors").resolve(),
        )
        if not record.cache_file.exists():
            raise FileNotFoundError(f"missing c1 routing cache: {record.cache_file}")
        cached = load_cached_routing(record)
        token_count = int(cached["top_k_ids"].shape[1])
        if cached["top_k_ids"].shape != (16, token_count, 8):
            raise ValueError(f"c1 top-k shape mismatch: {trace_id}")
        if cached["token_ids"].shape != (token_count,):
            raise ValueError(f"c1 token-id shape mismatch: {trace_id}")
        traces.append(
            LoadedTrace(
                record=record,
                top_k_ids=cached["top_k_ids"],
                token_ids=cached["token_ids"],
                scenario_domain=domains[record.pair_group_id],
                _probabilities=cached["probabilities"] if with_probabilities else None,
                labels={
                    "fold_role": str(row["fold_role"]),
                    "preregistered_fold": int(row["preregistered_fold"]),
                    "benign_family": domains[str(row["pair_group_id"])],
                    "decode_token_count": token_count,
                },
            )
        )
    return tuple(traces)


def research_v3_dataset_hashes(
    *,
    h384_dir: Path = DEFAULT_H384_DIR,
    c1_dir: Path = DEFAULT_C1_DIR,
    labels_path: Path = DEFAULT_H384_ENGAGEMENT_LABELS,
) -> dict[str, Any]:
    """Input provenance for the research-v3 pools (prereg section 9)."""

    return {
        "h384": {
            "sample_index": str(h384_dir / "sample_index.jsonl"),
            "sample_index_sha256": sha256(h384_dir / "sample_index.jsonl"),
            "recorded_sha256": H384_INDEX_SHA256,
            "engagement_adjudications": str(labels_path),
            "engagement_adjudications_sha256": sha256(labels_path),
            "expected_sha256": H384_ENGAGEMENT_LABELS_SHA256,
        },
        "c1": {
            "sample_index": str(c1_dir / "sample_index.jsonl"),
            "sample_index_sha256": sha256(c1_dir / "sample_index.jsonl"),
            "recorded_sha256": C1_INDEX_SHA256,
        },
    }
