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


@dataclass
class LoadedTrace:
    """One trace: frozen metadata plus its cached causal decode routing."""

    record: ManifoldTrace
    top_k_ids: torch.Tensor  # [16, T, 8] long
    token_ids: torch.Tensor  # [T] long
    scenario_domain: str
    brief_condition: str = "absent"
    _probabilities: torch.Tensor | None = field(default=None, repr=False)

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
    """clean / benign / resist / drift, the four evaluation classes of spec 1.7."""

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
    # 37 resisted attack).  ``arms`` restricts the pool further so a caller can match the
    # routine definition it is calibrating under (``cb`` = clean + benign_control).
    drift_rows = [row for row in rows if row["goal_plan_deviation_started"]]
    rows = [row for row in rows if not row["goal_plan_deviation_started"]]
    if len(drift_rows) + len(rows) != 120:
        raise ValueError("b1 brief=present partition failed")
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
