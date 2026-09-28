"""Loader for Agent v3 / gpt-oss traces (dataset G), additive to ``research_v2.io``.

``research_v2.io`` loads the frozen OLMoE pools (b1/b2, h384, C1): one trace = one decode
stream of 16 x T x 8 routing.  Dataset G is different in four ways that the detector
harness has to see (design ``docs/research_v4/agent_v3_dataset_design.md`` sections 1.1,
1.3, 4, 5 and the interface note ``dataset_g_interface_for_codex.md``):

1. **The guarantee unit is an episode, not a trace.**  One episode = one user turn = all
   generation of every agent step of that turn, concatenated on a single causal token
   axis that does not reset between steps.  A multi-turn session trace therefore yields
   several :class:`GEpisode` objects, each with its own token axis (the same axis the
   routing-blind annotation uses, ``trace.json["token_axis"]``).
2. **Routing lives in per-step shards.**  ``steps/NNNNNN_decode.safetensors`` holds one
   generated token each (``top_k_ids [24, 1, 4]``); prefill shards are excluded, exactly
   as the frozen loaders exclude the prompt.  The loader concatenates the decode shards
   of every step of the episode in token order and caches the result.
3. **Every token carries a harmony channel tag** (analysis / commentary / final).  The
   tag is a runtime-causal protocol state, so the detector is allowed to condition on it
   (design section 1.3); the views V1 / V2 / V3 of ``research_v2.trm3_g`` are built from
   this tag array.
4. **The router geometry is different** (24 MoE layers, 32 experts, top-4).  It is read
   from ``trace.json["router"]`` and attached to every episode as ``.router``, so that
   ``features.router_geometry`` and the G statistics never hard-code a geometry.

Nothing here reads a label unless the caller passes ``labels``; the label file is the
routing-blind annotation of design section 4 and stays empty until annotation exists.

Data discipline: a run whose ``dataset_role`` marks it as a probe (``p0_probe_not_data``)
never yields attack-arm episodes.  Requesting them explicitly raises.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import torch
from safetensors.torch import load_file, save_file

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = REPO_ROOT / "artifacts" / "agent_v2"
DEFAULT_G_CACHE_DIR = ARTIFACTS / "research_v4" / "g_routing_cache"
#: separate cache namespace for the FULL router logits (research v4 probability channels).
#: kept apart from ``DEFAULT_G_CACHE_DIR`` so the frozen top-k cache files are never rewritten.
DEFAULT_G_LOGIT_CACHE_DIR = ARTIFACTS / "dataset_g" / "prob_smoke" / "logit_cache"

ANALYSIS = "analysis"
COMMENTARY = "commentary"
FINAL = "final"
OTHER = "other"
HARMONY_CHANNELS = (ANALYSIS, COMMENTARY, FINAL)
ALL_TAGS = HARMONY_CHANNELS + (OTHER,)

CLEAN = "clean"
BENIGN_CONTROL = "benign_control"
BENIGN_LEXICAL = "benign_lexical"
LEGITIMATE_REFUSAL = "legitimate_refusal"
ATTACK = "attack"
#: false-alarm denominators of design section 7 (legitimate_refusal is a third outcome and
#: is deliberately NOT one of them)
NORMAL_VARIANTS = (CLEAN, BENIGN_CONTROL, BENIGN_LEXICAL)
KNOWN_VARIANTS = NORMAL_VARIANTS + (LEGITIMATE_REFUSAL, ATTACK)
#: the two "hard normal" scenario roles G-dev collects UNDER THE ``clean`` ARM NAME; their
#: real identity lives only in the subset config's ``scenarios[*].factory.normal_variant``
#: (freeze review 2026-09-07, blocking item ARM IDENTITY).  Recovering them is what makes
#: prereg section 4's per-arm denominators and gate F2's second conjunct evaluable.
SPECIAL_NORMAL_VARIANTS = (BENIGN_LEXICAL, LEGITIMATE_REFUSAL)
#: where :func:`subset_config_for_run` looks a subset config up by name
G_CONFIG_DIR = REPO_ROOT / "configs" / "dataset_g"
#: sentinel default of ``load_g(variant_overrides=...)``: discover the subset config
VARIANT_OVERRIDES_AUTO = "auto"
#: prereg section 4 as corrected by the freeze review: the five-way episode census of the
#: G-dev target (clean 192 + benign_lexical 24 + legitimate_refusal 24 = the 240 the run log
#: reports as one ``clean`` column).
G_DEV_VARIANT_COUNTS: dict[str, int] = {
    CLEAN: 192,
    BENIGN_CONTROL: 192,
    BENIGN_LEXICAL: 24,
    LEGITIMATE_REFUSAL: 24,
    ATTACK: 352,
}
#: ``dataset_role`` values whose routing must never be used as data (design section 5)
PROBE_ROLES = ("p0_probe_not_data",)
#: directory names holding traces the collection driver SET ASIDE (a partial CUDA-crash
#: trace, a scenario re-collected by a later resume).  ``scripts/research_v4/g_dev_missing.py``
#: moves them there and the collection plan does not count them; a plain ``rglob`` would
#: sweep them back into the pool (G-dev: 5 traces, among them two duplicate attack copies of
#: g-dev-052), so every walk here skips a path with one of these components.
QUARANTINE_DIR_NAMES = ("quarantine", "_quarantine")

TAG_SCOPES = ("message", "body")


# ---------------------------------------------------------------------------
# router geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouterMeta:
    """``trace.json["router"]`` reduced to what a detector may use."""

    num_moe_layers: int
    num_experts: int
    top_k: int
    adapter: str = ""
    model_type: str = ""
    moe_layer_indices: tuple[int, ...] = ()

    @classmethod
    def from_json(cls, payload: Mapping[str, Any]) -> "RouterMeta":
        return cls(
            num_moe_layers=int(payload["num_moe_layers"]),
            num_experts=int(payload["num_experts"]),
            top_k=int(payload["top_k"]),
            adapter=str(payload.get("router_adapter", "")),
            model_type=str(payload.get("model_type", "")),
            moe_layer_indices=tuple(int(v) for v in payload.get("moe_layer_indices", ())),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "num_moe_layers": self.num_moe_layers,
            "num_experts": self.num_experts,
            "top_k": self.top_k,
            "router_adapter": self.adapter,
            "model_type": self.model_type,
        }

    def get(self, key: str, default: Any = None) -> Any:
        """Mapping-style access so ``features.router_geometry`` can read this object."""

        return self.to_json().get(key, default)

    @property
    def layers(self) -> tuple[int, ...]:
        return tuple(range(self.num_moe_layers))


# ---------------------------------------------------------------------------
# one episode
# ---------------------------------------------------------------------------


@dataclass
class GEpisode:
    """One user turn of one Agent v3 trace: the guarantee unit of design section 8.

    ``LoadedTrace``-compatible: it exposes ``trace_id`` / ``batch`` / ``arm`` /
    ``pair_group_id`` / ``workflow`` / ``channel`` / ``domain`` / ``scenario_domain`` /
    ``fold`` / ``positive`` / ``token_count`` / ``top_k_ids`` / ``token_ids`` / ``labels``,
    which is everything ``research_v2.trm3`` and the scorer registry touch.  ``trace_id``
    is the EPISODE id (``"<trace>#ep<i>"``) so that ``trm3.trace_key`` stays unique when a
    session trace carries several episodes.
    """

    source_trace_id: str
    episode_index: int
    batch: str
    variant: str
    top_k_ids: torch.Tensor  # [L, T, k] long
    token_ids: torch.Tensor  # [T] long
    channel_tags: tuple[str, ...]  # [T] harmony channel per generated token
    router: RouterMeta
    pair_group_id: str
    fold: int
    workflow: str
    channel: str  # injection channel of the arm (none / direct_user / ...)
    domain: str
    scenario_domain: str
    domain_group: str = ""
    wording_tier: str = ""
    conversation_turn: int = 1
    episode_count: int = 1
    stop_reason: str = ""
    step_count: int = 0
    attack_family_id: str = ""
    dataset_role: str = ""
    tool_events: tuple[dict[str, Any], ...] = ()
    step_spans: tuple[dict[str, Any], ...] = ()
    channel_segments: tuple[dict[str, Any], ...] = ()
    trace_dir: Path | None = None
    labels: dict[str, Any] = field(default_factory=dict)
    #: lazily loaded FULL router logits ``[L, T, E]`` (bfloat16, exactly as stored).
    #: research v4 probability channels only; ``None`` until :meth:`router_logits` is called.
    logits: torch.Tensor | None = None

    # -- LoadedTrace-compatible surface --------------------------------------
    @property
    def trace_id(self) -> str:
        return f"{self.source_trace_id}#ep{self.episode_index}"

    @property
    def session_id(self) -> str:
        return self.source_trace_id

    @property
    def episode_id(self) -> str:
        return self.trace_id

    @property
    def arm(self) -> str:
        """The perturbation arm; identical to :attr:`variant` for dataset G."""

        return self.variant

    @property
    def workflow_family(self) -> str:
        return self.workflow

    @property
    def token_count(self) -> int:
        return int(self.top_k_ids.shape[1])

    @property
    def group_id(self) -> str:
        return self.pair_group_id

    @property
    def positive(self) -> bool:
        """Never a loader decision on dataset G.

        Positivity is a per-VIEW property (design section 4: the primary anchor is the
        earliest E reachable in that view), so it is decided in
        ``research_v2.trm3_g.view_anchors`` from the annotation, not here.  The attribute
        exists because the frozen machinery reads it; it is True only when the annotation
        marks an engagement anywhere.
        """

        return bool(self.labels.get("has_engagement", False))

    @property
    def normal(self) -> bool:
        return self.variant in NORMAL_VARIANTS

    @property
    def filter_pass(self) -> bool | None:
        """Quality filter of design section 2.3 (``None`` = not annotated yet)."""

        value = self.labels.get("filter_pass")
        return None if value is None else bool(value)

    # -- router probabilities (research v4, additive) -------------------------
    def router_logits(
        self,
        *,
        cache_dir: Path | str | None = DEFAULT_G_LOGIT_CACHE_DIR,
        keep: bool = True,
    ) -> torch.Tensor:
        """``[L, T, E]`` bfloat16 FULL router logits of this episode, loaded on demand.

        The trace shards store ``router_logits`` (all ``E`` experts, pre-softmax, bias
        included) next to ``top_k_ids`` / ``top_k_weights``; the frozen loader reads only
        the top-k tensors, so this is the accessor the probability channels use.  The
        result is cached on the episode (``keep``) and, when ``cache_dir`` is given, in a
        SEPARATE safetensors namespace so the existing top-k cache files are never
        rewritten.
        """

        if self.logits is not None:
            return self.logits
        if self.trace_dir is None:
            raise ValueError(f"{self.trace_id}: no trace_dir, cannot read router logits")
        cache_file = None
        if cache_dir is not None:
            safe = self.source_trace_id.replace("/", "_")
            cache_file = (
                Path(cache_dir)
                / str(self.batch).replace("/", "_")
                / f"{safe}--ep{self.episode_index}.logits.safetensors"
            )
        logits: torch.Tensor | None = None
        if cache_file is not None and cache_file.exists():
            logits = load_file(cache_file)["router_logits"]
        if logits is None:
            logits = episode_logits(self.trace_dir, self.step_spans)
            if cache_file is not None:
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                save_file({"router_logits": logits.contiguous()}, cache_file)
        expected = (self.router.num_moe_layers, self.token_count, self.router.num_experts)
        if tuple(logits.shape) != expected:
            raise ValueError(
                f"{self.trace_id}: router logits {tuple(logits.shape)} do not match the "
                f"router metadata {expected}"
            )
        if keep:
            self.logits = logits
        return logits

    def probabilities(
        self,
        *,
        cache_dir: Path | str | None = DEFAULT_G_LOGIT_CACHE_DIR,
        dtype: torch.dtype = torch.float32,
        keep: bool = True,
    ) -> torch.Tensor:
        """``[L, T, E]`` FULL router softmax of this episode.

        ``softmax`` over ALL experts of the stored logits, computed in ``dtype`` (float32
        by default).  NOTE the gpt-oss semantics: the model itself softmaxes only over the
        four SELECTED logits (``top_k_weight_semantics = softmax_over_selected_logits_only``),
        so this distribution is the router's implied distribution over all experts, not the
        vector the model multiplies expert outputs by.  See
        ``docs/research_v4/g_prob_channels_smoke.md`` section 1.
        """

        return torch.softmax(self.router_logits(cache_dir=cache_dir, keep=keep).to(dtype), dim=-1)

    def release_logits(self) -> None:
        """Drop the in-memory logits (the caller decides when a pool stops paying for them)."""

        self.logits = None

    def channel_mask(self, channels: Sequence[str]) -> torch.Tensor:
        keep = set(channels)
        return torch.tensor(
            [tag in keep for tag in self.channel_tags], dtype=torch.bool
        )

    def channel_counts(self) -> dict[str, int]:
        counts = {tag: 0 for tag in ALL_TAGS}
        for tag in self.channel_tags:
            counts[tag] = counts.get(tag, 0) + 1
        return counts

    def to_json(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "source_trace_id": self.source_trace_id,
            "session_id": self.session_id,
            "episode_index": self.episode_index,
            "batch": self.batch,
            "variant": self.variant,
            "pair_group_id": self.pair_group_id,
            "fold": self.fold,
            "workflow": self.workflow,
            "channel": self.channel,
            "domain": self.domain,
            "domain_group": self.domain_group,
            "wording_tier": self.wording_tier,
            "attack_family_id": self.attack_family_id,
            "conversation_turn": self.conversation_turn,
            "episode_count": self.episode_count,
            "stop_reason": self.stop_reason,
            "step_count": self.step_count,
            "token_count": self.token_count,
            "channel_counts": self.channel_counts(),
            "tool_event_count": len(self.tool_events),
            "dataset_role": self.dataset_role,
            "router": self.router.to_json(),
            "filter_pass": self.filter_pass,
            "labelled": bool(self.labels),
        }


# ---------------------------------------------------------------------------
# channel tags
# ---------------------------------------------------------------------------


def segment_spans(
    steps: Sequence[Mapping[str, Any]],
    *,
    vocabulary: "TokenTextVocabulary | None" = None,
) -> list[dict[str, Any]]:
    """Channel segments of one episode on the EPISODE token axis.

    Each ``channel_segments`` entry of a step already carries ``global_header_start`` /
    ``global_body_start`` / ``global_body_end`` on that axis (Agent v3 writes them); the
    terminator index is step-local, so it is shifted by the step's
    ``global_token_offset``.  Spans are half-open on the body and inclusive on the
    terminator, matching ``src/agent_v3/harmony.py``.

    A step written by the v2.5 deterministic controller has no ``channel_segments``.  If
    ``vocabulary`` is given (built from the trace's own ``manifest.jsonl`` by
    :func:`token_text_vocabulary`) the segments are recomputed exactly, by running the
    frozen ``agent_v3.harmony.segment_channels`` over the step's ``output_token_ids``;
    otherwise the coarse ``channel_boundaries`` fallback is used.
    """

    spans: list[dict[str, Any]] = []
    for step in steps:
        offset = int(step.get("global_token_offset", 0))
        segments = step.get("channel_segments")
        if segments is None:
            token_ids = step.get("output_token_ids")
            if vocabulary is not None and token_ids:
                spans.extend(
                    spans_from_token_ids(
                        token_ids,
                        vocabulary,
                        offset=offset,
                        agent_step=int(step.get("agent_step", 0)),
                    )
                )
            else:
                spans.extend(_spans_from_boundaries(step, offset))
            continue
        for segment in segments:
            header = int(segment.get("global_header_start", offset + int(segment["header_start"])))
            body_start = int(segment.get("global_body_start", offset + int(segment["body_start"])))
            body_end = int(segment.get("global_body_end", offset + int(segment["body_end"])))
            terminator = segment.get("terminator_index")
            end = body_end - 1 if terminator is None or int(terminator) < 0 else offset + int(terminator)
            spans.append(
                {
                    "channel": str(segment["channel"]),
                    "header_start": header,
                    "body_start": body_start,
                    "body_end": body_end,
                    "end": max(end, body_end - 1),
                    "recipient": segment.get("recipient"),
                    "agent_step": int(step.get("agent_step", 0)),
                }
            )
    spans.sort(key=lambda span: span["header_start"])
    return spans


def _spans_from_boundaries(step: Mapping[str, Any], offset: int) -> list[dict[str, Any]]:
    """Fallback for traces that only carry ``channel_boundaries`` (pilot / v2.5 layout).

    ``channel_boundaries[c]`` is the index of the token at which the marker of channel
    ``c`` completes; the body starts at ``+1`` and runs to the next marker.  Used only
    when ``channel_segments`` is absent; the exact spans of Agent v3 are preferred.
    """

    boundaries = {
        str(name): int(value)
        for name, value in (step.get("channel_boundaries") or {}).items()
        if int(value) >= 0
    }
    if not boundaries:
        return []
    total = int(step.get("output_token_count", 0))
    ordered = sorted(boundaries.items(), key=lambda item: item[1])
    spans: list[dict[str, Any]] = []
    for index, (name, marker) in enumerate(ordered):
        stop = ordered[index + 1][1] if index + 1 < len(ordered) else total - 1
        spans.append(
            {
                "channel": name,
                "header_start": offset + max(marker - 1, 0),
                "body_start": offset + marker + 1,
                "body_end": offset + stop + 1,
                "end": offset + stop,
                "recipient": None,
                "agent_step": int(step.get("agent_step", 0)),
            }
        )
    return spans


# ---------------------------------------------------------------------------
# v2.5 / pilot layout: rebuild the token axis and the channel spans from the
# per-step ``manifest.jsonl`` that every capture run writes next to the shards
# ---------------------------------------------------------------------------

#: The harmony protocol tokens ``agent_v3.harmony`` segments on.
HARMONY_PIECES = (
    "<|start|>",
    "<|message|>",
    "<|channel|>",
    "<|constrain|>",
    "<|end|>",
    "<|call|>",
    "<|return|>",
)


class TokenTextVocabulary:
    """A tokenizer-shaped view of the literal token texts a capture run recorded.

    ``manifest.jsonl`` stores ``token_texts`` beside ``token_ids`` for every captured
    step, so a trace carries its own id -> piece table and the loader never has to open a
    tokenizer (let alone a model) to segment harmony channels.  Only the two methods
    ``agent_v3.harmony`` actually calls are provided.  A harmony piece the run never
    emitted gets a negative sentinel id, which no real token id can equal, so the
    segmenter simply never matches it.
    """

    def __init__(self, text_by_id: Mapping[int, str]) -> None:
        self._text_by_id = {int(k): str(v) for k, v in text_by_id.items()}
        self._id_by_text: dict[str, int] = {}
        for token_id, text in sorted(self._text_by_id.items()):
            self._id_by_text.setdefault(text, token_id)
        self._sentinels: dict[str, int] = {}

    def __len__(self) -> int:
        return len(self._text_by_id)

    def piece_id(self, piece: str) -> int:
        """Id of a literal piece, or a stable negative sentinel if it was never seen."""

        if piece in self._id_by_text:
            return self._id_by_text[piece]
        if piece not in self._sentinels:
            self._sentinels[piece] = -(len(self._sentinels) + 1)
        return self._sentinels[piece]

    # -- the tokenizer surface agent_v3.harmony uses --------------------------
    def convert_tokens_to_ids(self, piece: str) -> int:
        return self.piece_id(piece)

    def decode(self, token_ids: Sequence[int], **_: Any) -> str:
        return "".join(self._text_by_id.get(int(v), "") for v in token_ids)


_MANIFEST_CACHE: dict[str, tuple[tuple[dict[str, Any], ...], dict[int, str]]] = {}


def read_step_manifest(trace_dir: Path) -> tuple[tuple[dict[str, Any], ...], dict[int, str]]:
    """``(decode rows, id -> token text)`` from ``manifest.jsonl``; ``((), {})`` if absent.

    Only the three fields the loader needs are kept from each decode row, so the cache
    stays small; prefill rows contribute their token texts (that is where the harmony
    protocol tokens of the rendered prompt appear) but no row entry.
    """

    key = str(trace_dir)
    cached = _MANIFEST_CACHE.get(key)
    if cached is not None:
        return cached
    path = Path(trace_dir) / "manifest.jsonl"
    decode_rows: list[dict[str, Any]] = []
    texts: dict[int, str] = {}
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                for token_id, text in zip(row.get("token_ids") or (), row.get("token_texts") or ()):
                    texts.setdefault(int(token_id), str(text))
                if str(row.get("phase")) != "decode":
                    continue
                decode_rows.append(
                    {
                        "step_index": int(row["step_index"]),
                        "token_ids": [int(v) for v in (row.get("token_ids") or ())],
                        "agent_steps": [int(v) for v in (row.get("agent_steps") or ())],
                    }
                )
    decode_rows.sort(key=lambda row: row["step_index"])
    value = (tuple(decode_rows), texts)
    _MANIFEST_CACHE[key] = value
    return value


def token_text_vocabulary(trace_dir: Path) -> TokenTextVocabulary | None:
    """The trace's own id -> piece table, or ``None`` when it has no manifest."""

    _, texts = read_step_manifest(Path(trace_dir))
    return TokenTextVocabulary(texts) if texts else None


def spans_from_token_ids(
    output_token_ids: Sequence[int],
    vocabulary: TokenTextVocabulary,
    *,
    offset: int = 0,
    agent_step: int = 0,
) -> list[dict[str, Any]]:
    """Exact channel spans of one step, on the episode axis.

    Runs the same ``agent_v3.harmony.segment_channels`` that Agent v3 runs when it writes
    ``channel_segments``, so a v2.5-layout trace and an Agent v3 trace are segmented by
    one implementation.  The import is local: ``research_v2`` stays importable without
    ``agent_v3`` for callers that only touch Agent-v3-shaped traces.
    """

    from agent_v3 import harmony  # local: keeps research_v2 free of an agent_v3 import

    specials = harmony.HarmonySpecials(
        start=vocabulary.piece_id("<|start|>"),
        message=vocabulary.piece_id("<|message|>"),
        channel=vocabulary.piece_id("<|channel|>"),
        constrain=vocabulary.piece_id("<|constrain|>"),
        end=vocabulary.piece_id("<|end|>"),
        call=vocabulary.piece_id("<|call|>"),
        return_=vocabulary.piece_id("<|return|>"),
    )
    spans: list[dict[str, Any]] = []
    for segment in harmony.segment_channels(vocabulary, output_token_ids, specials):
        terminator = int(segment.terminator_index)
        end = segment.body_end - 1 if terminator < 0 else terminator
        spans.append(
            {
                "channel": str(segment.channel) or OTHER,
                "header_start": offset + int(segment.header_start),
                "body_start": offset + int(segment.body_start),
                "body_end": offset + int(segment.body_end),
                "end": offset + max(end, segment.body_end - 1),
                "recipient": segment.recipient,
                "agent_step": int(agent_step),
            }
        )
    return spans


def with_token_axis(
    trace_dir: Path, steps: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Fill in ``routing_step_index_first_decode`` / ``global_token_offset``.

    Agent v3 writes both on every ``model_generation`` event; the v2.5 controller writes
    neither, and the mapping step -> decode shard then has to come from
    ``manifest.jsonl``.  The reconstruction is self-checking: the decode shards assigned
    to a step must be contiguous, must carry that step's ``agent_step`` and must decode to
    exactly the step's ``output_token_ids``.  A wrong mapping raises here instead of
    turning into silent scores.
    """

    needed = ("routing_step_index_first_decode", "global_token_offset")
    if all(key in step for step in steps for key in needed):
        return [dict(step) for step in steps]
    decode_rows, _ = read_step_manifest(Path(trace_dir))
    if not decode_rows:
        raise ValueError(
            f"{trace_dir}: the generation events carry no token axis and there is no "
            "manifest.jsonl to rebuild it from"
        )
    out: list[dict[str, Any]] = []
    cursor = 0
    offset = 0
    for step in steps:
        count = int(step.get("output_token_count") or len(step.get("output_token_ids") or ()))
        block = decode_rows[cursor : cursor + count]
        if len(block) != count:
            raise ValueError(
                f"{trace_dir}: {len(decode_rows)} decode shards cannot cover "
                f"{cursor + count} generated tokens"
            )
        first = int(block[0]["step_index"]) if block else 0
        if [int(row["step_index"]) for row in block] != list(range(first, first + count)):
            raise ValueError(
                f"{trace_dir}: decode shards of agent step {step.get('agent_step')} are not contiguous"
            )
        manifest_ids = [value for row in block for value in row["token_ids"]]
        expected = [int(value) for value in (step.get("output_token_ids") or ())]
        if expected and manifest_ids != expected:
            raise ValueError(
                f"{trace_dir}: manifest decode tokens disagree with the generation event "
                f"of agent step {step.get('agent_step')}"
            )
        declared = step.get("agent_step")
        seen = {value for row in block for value in row["agent_steps"]}
        if declared is not None and seen and seen != {int(declared)}:
            raise ValueError(
                f"{trace_dir}: decode shards {first}..{first + count - 1} carry agent steps "
                f"{sorted(seen)} but the generation event declares {declared}"
            )
        row_out = dict(step)
        row_out.setdefault("routing_step_index_first_decode", first)
        row_out.setdefault("global_token_offset", offset)
        out.append(row_out)
        cursor += count
        offset += count
    return out


def channel_tag_array(
    spans: Sequence[Mapping[str, Any]], token_count: int, *, scope: str = "message"
) -> tuple[str, ...]:
    """Per-token harmony channel of one episode.

    ``scope="message"`` (default) tags the whole message span -- the channel header, the
    body and the terminator -- plus the two-token ``<|start|>assistant`` preamble that
    precedes a header, by attributing every gap to the message that FOLLOWS it.  The
    protocol markers are generated tokens with real routing, the runtime sees them, and
    keeping them inside the run makes the causal windows of short analysis messages
    usable; their contribution is a per-channel constant that the channel-conditioned
    mu/sigma absorbs.

    ``scope="body"`` tags message bodies only and leaves markers / preambles as
    ``"other"``, i.e. outside every view.  Provided for a sensitivity column.

    Tokens after the last segment (a truncated generation) are ``"other"``.
    """

    if scope not in TAG_SCOPES:
        raise ValueError(f"unknown tag scope {scope!r}; known: {TAG_SCOPES}")
    tags: list[str] = [OTHER] * int(token_count)
    if scope == "body":
        for span in spans:
            for index in range(max(0, span["body_start"]), min(span["body_end"], token_count)):
                tags[index] = span["channel"]
        return tuple(tags)
    previous_end = -1
    for span in spans:
        start = min(previous_end + 1, int(span["header_start"]))
        stop = min(int(span["end"]), token_count - 1)
        for index in range(max(0, start), stop + 1):
            tags[index] = span["channel"]
        previous_end = max(previous_end, stop)
    return tuple(tags)


def channel_runs(tags: Sequence[str], channels: Sequence[str]) -> list[tuple[int, int, str]]:
    """Maximal contiguous ``[start, stop)`` runs of one retained channel.

    The window statistics of the G views never straddle a channel boundary, so every
    causal window lives inside one of these runs (``trm3_g.segmented_windows``).
    """

    keep = set(channels)
    runs: list[tuple[int, int, str]] = []
    start: int | None = None
    for index, tag in enumerate(tags):
        if tag in keep:
            if start is None:
                start = index
            elif tags[index - 1] != tag:
                runs.append((start, index, tags[index - 1]))
                start = index
        elif start is not None:
            runs.append((start, index, tags[start]))
            start = None
    if start is not None:
        runs.append((start, len(tags), tags[start]))
    return runs


# ---------------------------------------------------------------------------
# labels (routing-blind annotation of design section 4)
# ---------------------------------------------------------------------------

ANCHOR_KEYS = ("e_analysis", "e_final", "c", "x", "x_tool")
COVERAGE_ORDER = ("none", "partial", "full")


def _anchor_token(value: Any) -> int | None:
    """First token of an anchor written as an int, a ``[first, last]`` span or a dict."""

    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, Mapping):
        for key in ("token", "first_token", "output_token_index", "start"):
            if key in value and value[key] is not None:
                return int(value[key])
        return None
    if isinstance(value, (list, tuple)):
        return None if not value else _anchor_token(value[0])
    raise ValueError(f"cannot read an anchor token from {value!r}")


def normalise_label_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """One annotation row -> the label dict carried by :class:`GEpisode`.

    Accepts anchors as ints or ``[first_token, evidence_end]`` spans (the form of the P0
    readout table).  Derived fields: ``has_engagement`` (E in any channel),
    ``filter_pass`` (design section 2.3: on_task and no unauthorised tool attempt and no
    material error and coverage >= partial), ``silent`` (attack arm, no E anywhere, and
    not an over-refusal / legitimate refusal, per design section 15.1).
    """

    out: dict[str, Any] = {key: value for key, value in row.items()}
    for key in ANCHOR_KEYS:
        out[key] = _anchor_token(row.get(key))
        span = row.get(key)
        if isinstance(span, (list, tuple)) and len(span) == 2:
            out[f"{key}_span"] = [int(span[0]), int(span[1])]
    out["has_engagement"] = out.get("e_analysis") is not None or out.get("e_final") is not None
    trajectory = str(row.get("trajectory_class", "") or "")
    out["trajectory_class"] = trajectory
    out["over_refusal"] = bool(
        row.get("over_refusal", trajectory == "over_refusal")
    )
    out["refusal_without_task_specific_content"] = bool(
        row.get("refusal_without_task_specific_content", False)
    )
    out["analysis_only_engagement"] = bool(row.get("analysis_only_engagement", False))
    if "filter_pass" in row:
        out["filter_pass"] = None if row["filter_pass"] is None else bool(row["filter_pass"])
    else:
        quality = row.get("quality")
        out["filter_pass"] = None if quality is None else _filter_pass(quality)
    if "silent" not in row:
        out["silent"] = bool(
            trajectory == "silent"
            or (
                not out["has_engagement"]
                and not out["over_refusal"]
                and trajectory not in ("legitimate_refusal", "over_refusal")
            )
        )
    return out


def _filter_pass(quality: Mapping[str, Any]) -> bool:
    behaviour = str(quality.get("behavior", quality.get("behaviour", "")))
    coverage = str(quality.get("coverage", ""))
    coverage_ok = coverage in COVERAGE_ORDER[1:] if coverage else False
    return bool(
        behaviour == "on_task"
        and coverage_ok
        and not bool(quality.get("material_errors", False))
        and not bool(quality.get("unauthorized_tool_attempt", False))
    )


def read_labels(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    """Read a routing-blind annotation JSONL keyed on ``(trace_id, episode_index)``.

    Rows may key the episode either as ``{"trace_id": ..., "episode_index": ...}`` or as
    a single ``{"trace_id": "<trace>#ep<i>"}`` / ``{"episode_id": ...}`` field.
    """

    labels: dict[tuple[str, int], dict[str, Any]] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        key = _label_key(row)
        if key in labels:
            raise ValueError(f"duplicate annotation row for {key}")
        labels[key] = normalise_label_row(row)
    return labels


def _label_key(row: Mapping[str, Any]) -> tuple[str, int]:
    identifier = str(row.get("episode_id") or row.get("trace_id"))
    if "#ep" in identifier:
        trace_id, _, index = identifier.partition("#ep")
        return trace_id, int(index)
    return identifier, int(row.get("episode_index", 0))


# ---------------------------------------------------------------------------
# routing shards
# ---------------------------------------------------------------------------


def _decode_shard(trace_dir: Path, index: int) -> Path:
    return trace_dir / "steps" / f"{index:06d}_decode.safetensors"


def episode_routing(
    trace_dir: Path, steps: Sequence[Mapping[str, Any]]
) -> tuple[torch.Tensor, torch.Tensor]:
    """Concatenate the decode shards of one episode on its causal token axis.

    ``token_axis`` of ``trace.json``: episode token ``g`` of step ``s`` lives in shard
    ``routing_step_index_first_decode[s] + (g - global_token_offset[s])``.  Prefill shards
    are never read (the frozen loaders likewise keep decode only).
    """

    top_k_blocks: list[torch.Tensor] = []
    token_blocks: list[torch.Tensor] = []
    for step in steps:
        first = int(step["routing_step_index_first_decode"])
        count = int(step["output_token_count"])
        for offset in range(count):
            shard = load_file(_decode_shard(trace_dir, first + offset))
            top_k_blocks.append(shard["top_k_ids"].long())
            token_blocks.append(shard["token_ids"].long())
    if not top_k_blocks:
        raise ValueError(f"episode without decode shards in {trace_dir}")
    return torch.cat(top_k_blocks, dim=1).contiguous(), torch.cat(token_blocks).contiguous()


def episode_logits(
    trace_dir: Path, steps: Sequence[Mapping[str, Any]]
) -> torch.Tensor:
    """``[L, T, E]`` FULL router logits of one episode, on its causal token axis.

    Same shard walk as :func:`episode_routing` (decode shards only, in token order), but
    reading the ``router_logits`` tensor instead of the top-k pair.  The dtype is left
    exactly as stored (bfloat16 on the gpt-oss traces) so nothing is silently upcast on
    the way in; callers softmax in the precision they want.
    """

    blocks: list[torch.Tensor] = []
    for step in steps:
        first = int(step["routing_step_index_first_decode"])
        count = int(step["output_token_count"])
        for offset in range(count):
            shard = load_file(_decode_shard(trace_dir, first + offset))
            if "router_logits" not in shard:
                raise ValueError(
                    f"{trace_dir}: shard {first + offset} has no `router_logits`; this "
                    "trace pool cannot serve the probability channels"
                )
            blocks.append(shard["router_logits"])
    if not blocks:
        raise ValueError(f"episode without decode shards in {trace_dir}")
    return torch.cat(blocks, dim=1).contiguous()


def load_episode_probabilities(
    episodes: Iterable[GEpisode],
    *,
    cache_dir: Path | str | None = DEFAULT_G_LOGIT_CACHE_DIR,
) -> dict[str, Any]:
    """Warm the router-logit cache of a pool; returns a provenance block.

    Reports the simplex deviation actually observed (``max |sum_e p - 1|`` in float32),
    the mean mass the full softmax puts on the SELECTED top-k, and the number of
    (layer, token) cells where the STORED ``top_k_ids`` are not an ``argtopk`` of the
    stored logits -- which on bfloat16 traces are exact ties (checked here: the in-set
    mass difference must be exactly zero).
    """

    pool = list(episodes)
    deviation = 0.0
    in_set_sum = 0.0
    cells = 0
    tie_cells = 0
    tie_mass_max = 0.0
    tokens = 0
    for episode in pool:
        probs = episode.probabilities(cache_dir=cache_dir)
        deviation = max(deviation, float((probs.sum(-1) - 1.0).abs().max()))
        ids = episode.top_k_ids.long()
        stored = torch.gather(probs, 2, ids).sum(-1)
        recomputed_ids = torch.topk(episode.router_logits(cache_dir=cache_dir).float(), ids.shape[2], dim=-1).indices
        recomputed = torch.gather(probs, 2, recomputed_ids).sum(-1)
        mismatch = (
            torch.sort(recomputed_ids, dim=-1).values != torch.sort(ids, dim=-1).values
        ).any(-1)
        tie_cells += int(mismatch.sum())
        if bool(mismatch.any()):
            tie_mass_max = max(tie_mass_max, float((stored - recomputed)[mismatch].abs().max()))
        in_set_sum += float(stored.sum())
        cells += int(stored.numel())
        tokens += int(episode.token_count)
    return {
        "episodes": len(pool),
        "tokens": tokens,
        "layer_token_cells": cells,
        "simplex_max_deviation": deviation,
        "in_set_mass_mean": (in_set_sum / cells) if cells else None,
        "top_k_id_mismatch_cells": tie_cells,
        "top_k_id_mismatch_rate": (tie_cells / cells) if cells else None,
        "tie_in_set_mass_max_abs_difference": tie_mass_max,
        "cache_dir": None if cache_dir is None else str(cache_dir),
    }


# ---------------------------------------------------------------------------
# the loader
# ---------------------------------------------------------------------------


def _generation_steps(trace: Mapping[str, Any]) -> dict[int, list[dict[str, Any]]]:
    """``{episode_index: [model_generation events in step order]}``.

    The events carry ``output_token_ids`` (used to verify the concatenated routing) as
    well as the channel segments and the token-axis fields; ``trace["episodes"]`` carries
    the same structure without the token ids, and is used only for episode metadata.

    A v2.5-layout trace has no ``episode_index`` on the event.  One episode = one user
    turn (design section 1.1), so the conversation turns are ranked and their rank is the
    episode index; the single-turn v2.5 traces all land in episode 0.
    """

    events = [
        dict(event)
        for event in trace.get("events", ())
        if event.get("kind") == "model_generation"
    ]
    turns = sorted({int(event.get("conversation_turn", 0)) for event in events})
    steps: dict[int, list[dict[str, Any]]] = {}
    for event in events:
        if "episode_index" in event:
            index = int(event["episode_index"])
        else:
            index = turns.index(int(event.get("conversation_turn", 0)))
        steps.setdefault(index, []).append(event)
    for rows in steps.values():
        rows.sort(key=lambda row: int(row.get("agent_step", 0)))
    return steps


# ---------------------------------------------------------------------------
# five-way arm identity (freeze review, blocking item ARM IDENTITY)
# ---------------------------------------------------------------------------


def iter_trace_paths(run_dir: Path | str) -> list[Path]:
    """Every ``trace.json`` under ``run_dir`` except the quarantined ones, sorted."""

    quarantined = set(QUARANTINE_DIR_NAMES)
    return [
        path
        for path in sorted(Path(run_dir).rglob("trace.json"))
        if not quarantined & set(path.parts)
    ]


def variant_overrides_from_config(
    config_path: Path | str,
) -> dict[str, str]:
    """``{scenario id -> benign_lexical | legitimate_refusal}`` from a SUBSET CONFIG.

    G-dev's two hard-normal scenario groups were collected under the arm name ``clean``
    (``configs/dataset_g/g_dev.json``'s ``collection_plan`` gives both of them
    ``arms = ["clean"]``), so ``perturbation.arm`` and the on-disk arm directory both read
    ``clean`` and :func:`_variant_of` cannot tell them apart.  Their true role is recorded
    ONLY in the subset config, as ``scenarios[*].factory.normal_variant``.

    The join key is the scenario id (``pair_group_id``, with ``base_task_id`` as an alias),
    NOT the run directory: the last 15 ``legitimate_refusal`` traces were re-collected into
    ``resume_*`` run directories, so a directory-name rule would mislabel them.

    Only the two special roles are returned.  A subset whose scenarios are all
    ``normal_variant == "attack_cell"`` (G-fit / G-cal / G-bridge) yields an EMPTY mapping,
    which is what keeps their loads byte-identical.
    """

    payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for scenario in payload.get("scenarios", ()) or ():
        factory = scenario.get("factory") or {}
        role = str(factory.get("normal_variant", "") or "")
        if role not in SPECIAL_NORMAL_VARIANTS:
            continue
        for key in (scenario.get("pair_group_id"), scenario.get("base_task_id")):
            if key:
                out[str(key)] = role
    return out


def fixture_map_from_config(config_path: Path | str) -> dict[str, str]:
    """``{scenario id -> factory.fixture_id}`` from a SUBSET CONFIG (metadata only).

    The v3.2 round-2 fold key ``fixture_rank_mod`` (freeze review DATA-1) ranks scenarios
    inside their own fixture -- the store world the routine task lives in -- so the harness
    needs the scenario -> fixture map before it may load a single episode.  It lives in the
    frozen subset config as ``scenarios[*].factory.fixture_id``; no trace and no routing
    shard is opened here, which is what lets stage 1 build the fold table on a batch whose
    attack arms are still sealed.

    Both ``pair_group_id`` and ``base_task_id`` are registered as join keys, exactly as in
    :func:`variant_overrides_from_config`.
    """

    payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for scenario in payload.get("scenarios", ()) or ():
        factory = scenario.get("factory") or {}
        fixture = str(
            factory.get("fixture_id", "") or scenario.get("fixture_id", "") or ""
        )
        if not fixture:
            continue
        for key in (scenario.get("pair_group_id"), scenario.get("base_task_id")):
            if key:
                out[str(key)] = fixture
    return out


def fixture_map(
    run_dir: Path | str, *, config: Path | str | None = None
) -> dict[str, Any]:
    """``{scenario: fixture}`` of a run directory plus the provenance of the config used."""

    path = Path(config) if config is not None else subset_config_for_run(run_dir)
    if path is None or not Path(path).is_file():
        return {
            "run_dir": str(run_dir),
            "config_path": None if path is None else str(path),
            "source": "explicit_config" if config is not None else "auto_no_config_found",
            "fixtures": {},
            "fixture_count": 0,
        }
    mapping = fixture_map_from_config(path)
    return {
        "run_dir": str(run_dir),
        "config_path": str(path),
        "source": "explicit_config" if config is not None else "auto_subset_config",
        "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "fixtures": mapping,
        "fixture_count": len({v for v in mapping.values()}),
        "scenario_count": len(mapping),
        "note": "subset config metadata only; no trace.json and no routing shard is read",
    }


def subset_config_for_run(run_dir: Path | str) -> Path | None:
    """The ``configs/dataset_g/<subset>.json`` a run directory was produced from.

    Two provenance records are tried, in order, over the run directory and its immediate
    children (a subset such as G-dev is collected as several run GROUPS under one root):

    1. ``run_summary.json["config_path"]`` -- the exact config the runner resolved;
    2. ``resolved_experiment_config.json["experiment_id"]`` (``dataset_g_dev`` ->
       ``g_dev.json``), used when the group directory has no ``run_summary.json``.

    Returns ``None`` when neither resolves to a file that exists, in which case the caller
    simply applies no overrides.  Only metadata JSON is read; no routing shard is opened.
    """

    run_dir = Path(run_dir)
    candidates = [run_dir, *sorted(p for p in run_dir.glob("*") if p.is_dir())]
    for directory in candidates:
        summary_path = directory / "run_summary.json"
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                summary = {}
            declared = str(summary.get("config_path", "") or "")
            if declared and Path(declared).exists():
                return Path(declared)
            resolved = _config_by_experiment_id(summary.get("experiment_id"))
            if resolved is not None:
                return resolved
        resolved_path = directory / "resolved_experiment_config.json"
        if resolved_path.exists():
            try:
                resolved_config = json.loads(resolved_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                resolved_config = {}
            resolved = _config_by_experiment_id(resolved_config.get("experiment_id"))
            if resolved is not None:
                return resolved
    return None


def _config_by_experiment_id(experiment_id: Any) -> Path | None:
    """``dataset_g_dev`` -> ``configs/dataset_g/g_dev.json`` when that file exists."""

    name = str(experiment_id or "")
    if not name:
        return None
    subset = name[len("dataset_") :] if name.startswith("dataset_") else name
    candidate = G_CONFIG_DIR / f"{subset}.json"
    return candidate if candidate.exists() else None


def _resolve_variant_overrides(
    variant_overrides: Any, run_dir: Path
) -> tuple[dict[str, str], str, str | None]:
    """``(overrides, source, config_path)`` for one ``load_g`` call."""

    if variant_overrides is None:
        return {}, "disabled", None
    if isinstance(variant_overrides, Mapping):
        return (
            {str(k): str(v) for k, v in variant_overrides.items()},
            "explicit_mapping",
            None,
        )
    if isinstance(variant_overrides, (str, Path)) and str(variant_overrides) != (
        VARIANT_OVERRIDES_AUTO
    ):
        path = Path(variant_overrides)
        return variant_overrides_from_config(path), "explicit_config", str(path)
    path = subset_config_for_run(run_dir)
    if path is None:
        return {}, "auto_no_config_found", None
    return variant_overrides_from_config(path), "auto_subset_config", str(path)


def load_g(
    run_dir: Path | str,
    *,
    labels: Path | str | Mapping[tuple[str, int], Mapping[str, Any]] | None = None,
    variants: Sequence[str] | None = None,
    scenarios: Sequence[str] | None = None,
    tag_scope: str = "message",
    cache_dir: Path | str | None = DEFAULT_G_CACHE_DIR,
    verify_tokens: bool = True,
    allow_probe_attack: bool = False,
    manifest: dict[str, Any] | None = None,
    variant_overrides: Mapping[str, str] | Path | str | None = VARIANT_OVERRIDES_AUTO,
) -> tuple[GEpisode, ...]:
    """Load every episode of an Agent v3 run directory.

    ``variants`` restricts the perturbation arms (``clean`` / ``benign_control`` /
    ``benign_lexical`` / ``legitimate_refusal`` / ``attack``); ``scenarios`` restricts
    ``pair_group_id`` by exact match or substring.  ``labels`` is the routing-blind
    annotation (path or already-read mapping); without it every ``labels`` dict is empty
    and the evaluation reports "no positives" rather than guessing.

    ``variant_overrides`` recovers the FIVE-way arm identity of prereg section 4 for the
    two G-dev scenario groups that were collected under the arm name ``clean``
    (:func:`variant_overrides_from_config`).  The default ``"auto"`` discovers the subset
    config from the run directory's own provenance (:func:`subset_config_for_run`) and
    applies it; a mapping or a config path forces one; ``None`` disables the join and
    restores the pre-freeze-review behaviour.  An override is applied ONLY to an episode
    whose arm resolved to ``clean`` -- a ``benign_control`` or ``attack`` trace of the same
    scenario keeps its own arm -- and the manifest records how many were re-labelled.

    ``cache_dir`` holds one safetensors per episode with the concatenated decode routing
    (``None`` disables the cache).  ``verify_tokens`` re-checks the concatenated token ids
    against ``output_token_ids`` of the generation events, which is what makes the
    step -> shard mapping auditable at load time.

    Data discipline (design section 5): a run whose ``dataset_role`` is a probe role never
    returns attack-arm episodes.  ``allow_probe_attack`` exists so the guard can be
    exercised by a test; no script sets it.
    """

    run_dir = Path(run_dir)
    if not run_dir.exists():
        raise FileNotFoundError(run_dir)
    label_map: Mapping[tuple[str, int], Mapping[str, Any]]
    if labels is None:
        label_map = {}
    elif isinstance(labels, (str, Path)):
        label_map = read_labels(Path(labels))
    else:
        label_map = {key: dict(value) for key, value in labels.items()}

    summary_path = run_dir / "run_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.exists() else {}
    batch = str(summary.get("experiment_id") or run_dir.name)
    overrides, override_source, override_config = _resolve_variant_overrides(
        variant_overrides, run_dir
    )
    wanted = None if variants is None else {str(v) for v in variants}
    if wanted is not None:
        unknown = wanted - set(KNOWN_VARIANTS)
        if unknown:
            raise ValueError(f"unknown variants requested: {sorted(unknown)}")

    report: dict[str, Any] = {
        "run_dir": str(run_dir),
        "batch": batch,
        "dataset_role": str(summary.get("dataset_role", "")),
        "trace_count": 0,
        "episode_count": 0,
        "skipped_probe_attack": 0,
        "skipped_variant": 0,
        "skipped_scenario": 0,
        "labelled_episodes": 0,
        "tag_scope": tag_scope,
        "variant_override_source": override_source,
        "variant_override_config": override_config,
        "variant_override_scenarios": len(overrides),
        "variant_overridden_traces": 0,
        "variant_overridden": {},
    }
    episodes: list[GEpisode] = []
    all_traces = sorted(run_dir.rglob("trace.json"))
    trace_paths = iter_trace_paths(run_dir)
    report["skipped_quarantine"] = len(all_traces) - len(trace_paths)
    for trace_path in trace_paths:
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        variant = _variant_of(trace, trace_path.parent)
        role = str(trace.get("dataset_role", summary.get("dataset_role", "")))
        pair_group = str(trace.get("pair_group_id", ""))
        if overrides and variant == CLEAN:
            override = overrides.get(pair_group) or overrides.get(
                str(trace.get("base_task_id", ""))
            )
            if override:
                variant = override
                report["variant_overridden_traces"] += 1
                report["variant_overridden"][override] = (
                    report["variant_overridden"].get(override, 0) + 1
                )
        if role in PROBE_ROLES and variant == ATTACK and not allow_probe_attack:
            if wanted is not None and ATTACK in wanted:
                raise ValueError(
                    f"{run_dir} is a probe run ({role}); its attack routing is not data "
                    "and must not be scored"
                )
            report["skipped_probe_attack"] += 1
            continue
        if wanted is not None and variant not in wanted:
            report["skipped_variant"] += 1
            continue
        if scenarios is not None and not any(
            token == pair_group or token in pair_group for token in scenarios
        ):
            report["skipped_scenario"] += 1
            continue
        report["trace_count"] += 1
        episodes.extend(
            _episodes_of_trace(
                trace,
                trace_path.parent,
                batch=batch,
                label_map=label_map,
                tag_scope=tag_scope,
                cache_dir=None if cache_dir is None else Path(cache_dir),
                verify_tokens=verify_tokens,
                report=report,
                variant=variant,
            )
        )
    report["episode_count"] = len(episodes)
    if manifest is not None:
        manifest.update(report)
    return tuple(episodes)


def _variant_of(trace: Mapping[str, Any], trace_dir: Path) -> str:
    """Perturbation arm, from ``trace.json`` when it records one, else from the path.

    Batch runs lay traces out as ``<run>/<pair_group_id>/<arm>/trace.json``.  The v2.5
    controller does record ``perturbation.arm``; reading the directory as well turns a
    layout that drifts from the metadata into an error rather than a mislabelled arm.
    """

    declared = str(trace.get("perturbation", {}).get("arm", "") or "")
    from_path = Path(trace_dir).name
    if from_path in KNOWN_VARIANTS:
        if declared and declared != from_path:
            raise ValueError(
                f"{trace_dir}: perturbation.arm is {declared!r} but the trace sits in the "
                f"{from_path!r} directory"
            )
        return from_path
    return declared


def _episodes_of_trace(
    trace: Mapping[str, Any],
    trace_dir: Path,
    *,
    batch: str,
    label_map: Mapping[tuple[str, int], Mapping[str, Any]],
    tag_scope: str,
    cache_dir: Path | None,
    verify_tokens: bool,
    report: dict[str, Any],
    variant: str | None = None,
) -> list[GEpisode]:
    trace_id = str(trace["trace_id"])
    router = RouterMeta.from_json(trace["router"])
    perturbation = trace.get("perturbation", {})
    if variant is None:
        variant = _variant_of(trace, trace_dir)
    steps_by_episode = _generation_steps(trace)
    episode_meta = {int(e["episode_index"]): e for e in trace.get("episodes", ())}
    if not episode_meta:
        episode_meta = {index: {"episode_index": index} for index in steps_by_episode}
    vocabulary: TokenTextVocabulary | None = None
    if any(
        "channel_segments" not in step
        for rows in steps_by_episode.values()
        for step in rows
    ):
        vocabulary = token_text_vocabulary(trace_dir)
    out: list[GEpisode] = []
    for index in sorted(steps_by_episode):
        steps = with_token_axis(trace_dir, steps_by_episode[index])
        meta = episode_meta.get(index, {})
        top_k_ids, token_ids = _episode_tensors(
            trace_dir,
            trace_id,
            index,
            steps,
            router=router,
            cache_dir=cache_dir,
            batch=batch,
            verify_tokens=verify_tokens,
        )
        token_count = int(top_k_ids.shape[1])
        spans = segment_spans(steps, vocabulary=vocabulary)
        tags = channel_tag_array(spans, token_count, scope=tag_scope)
        tool_events = tuple(
            dict(event)
            for event in trace.get("tool_events", ())
            if int(event.get("episode_index", 0)) == index
        )
        label = dict(label_map.get((trace_id, index), {}))
        if label:
            report["labelled_episodes"] += 1
        out.append(
            GEpisode(
                source_trace_id=trace_id,
                episode_index=index,
                batch=batch,
                variant=variant,
                top_k_ids=top_k_ids,
                token_ids=token_ids,
                channel_tags=tags,
                router=router,
                pair_group_id=str(trace.get("pair_group_id", "")),
                fold=int(trace.get("preregistered_fold", 0) or 0),
                workflow=str(trace.get("task_mandate", {}).get("authorized_goal", "")),
                channel=str(perturbation.get("channel", "")),
                domain=str(perturbation.get("attack_goal") or trace.get("domain_group", "")),
                scenario_domain=str(trace.get("domain_group", "")),
                domain_group=str(trace.get("domain_group", "")),
                wording_tier=str(trace.get("wording_tier", "")),
                conversation_turn=int(meta.get("conversation_turn", index + 1)),
                episode_count=len(steps_by_episode),
                stop_reason=str(meta.get("stop_reason", "")),
                step_count=len(steps),
                attack_family_id=str(perturbation.get("attack_family_id") or ""),
                dataset_role=str(trace.get("dataset_role", "")),
                tool_events=tool_events,
                step_spans=tuple(
                    {
                        "agent_step": int(step.get("agent_step", 0)),
                        "global_token_offset": int(step.get("global_token_offset", 0)),
                        "output_token_count": int(step.get("output_token_count", 0)),
                        "routing_step_index_first_decode": int(
                            step["routing_step_index_first_decode"]
                        ),
                        "stop_reason": str(step.get("stop_reason", "")),
                    }
                    for step in steps
                ),
                channel_segments=tuple(spans),
                trace_dir=trace_dir,
                labels=label,
            )
        )
    return out


def _episode_tensors(
    trace_dir: Path,
    trace_id: str,
    episode_index: int,
    steps: Sequence[Mapping[str, Any]],
    *,
    router: RouterMeta,
    cache_dir: Path | None,
    batch: str,
    verify_tokens: bool,
) -> tuple[torch.Tensor, torch.Tensor]:
    expected_ids: list[int] = []
    for step in steps:
        expected_ids.extend(int(v) for v in step.get("output_token_ids", ()))
    cache_file = None
    if cache_dir is not None:
        safe = trace_id.replace("/", "_")
        cache_file = (
            Path(cache_dir) / str(batch).replace("/", "_") / f"{safe}--ep{episode_index}.safetensors"
        )
    if cache_file is not None and cache_file.exists():
        cached = load_file(cache_file)
        top_k_ids = cached["top_k_ids"].long()
        token_ids = cached["token_ids"].long()
    else:
        top_k_ids, token_ids = episode_routing(trace_dir, steps)
        if cache_file is not None:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            save_file(
                {
                    "top_k_ids": top_k_ids.to(torch.int16).contiguous(),
                    "token_ids": token_ids.to(torch.int64).contiguous(),
                },
                cache_file,
            )
    token_count = int(top_k_ids.shape[1])
    if tuple(top_k_ids.shape) != (router.num_moe_layers, token_count, router.top_k):
        raise ValueError(
            f"{trace_id} ep{episode_index}: routing shape {tuple(top_k_ids.shape)} does not "
            f"match the router metadata [{router.num_moe_layers}, T, {router.top_k}]"
        )
    if int(token_ids.shape[0]) != token_count:
        raise ValueError(f"{trace_id} ep{episode_index}: token-id / routing length mismatch")
    if top_k_ids.numel() and int(top_k_ids.max()) >= router.num_experts:
        raise ValueError(f"{trace_id} ep{episode_index}: expert id outside the router range")
    if verify_tokens and expected_ids:
        if len(expected_ids) != token_count:
            raise ValueError(
                f"{trace_id} ep{episode_index}: {token_count} routed tokens but "
                f"{len(expected_ids)} generated token ids"
            )
        if token_ids.tolist() != expected_ids:
            raise ValueError(
                f"{trace_id} ep{episode_index}: routed token ids disagree with the "
                "generation events (step -> shard mapping is wrong)"
            )
    return top_k_ids, token_ids


# ---------------------------------------------------------------------------
# pool helpers
# ---------------------------------------------------------------------------


def normal_episodes(episodes: Iterable[GEpisode]) -> tuple[GEpisode, ...]:
    """Clean / benign_control / benign_lexical only (the FAR denominators)."""

    return tuple(e for e in episodes if e.variant in NORMAL_VARIANTS)


def filtered_pool(episodes: Iterable[GEpisode], *, require_labels: bool = False) -> tuple[GEpisode, ...]:
    """The "correct routine" pool of design section 2.3.

    Episodes whose quality annotation passes the filter.  With ``require_labels=False``
    (the default) an unlabelled episode is kept and the caller reports the pool as
    ``filter_status = "unlabelled"``; with ``require_labels=True`` it is dropped.
    """

    out = []
    for episode in normal_episodes(episodes):
        status = episode.filter_pass
        if status is True or (status is None and not require_labels):
            out.append(episode)
    return tuple(out)


#: an attack trace whose injection arrives in the SECOND user turn: its episode 0 precedes
#: the injection and carries no attack content (``factory/assemble.py``: the first turn is
#: the withheld opening).  The D5 yield denominator of prereg 12.2 excludes those episodes.
MULTI_TURN_CHANNEL = "multi_turn_user"


def scenario_census(
    run_dir: Path | str,
    *,
    variant_overrides: Mapping[str, str] | Path | str | None = VARIANT_OVERRIDES_AUTO,
) -> dict[str, Any]:
    """METADATA-ONLY ``{pair_group_id: {variants, episodes, traces}}`` of a run directory.

    Reads ``trace.json`` (and the subset config the override join needs) and NOTHING else,
    exactly like :func:`variant_census`: no routing shard is opened, so it may be called on
    a batch whose attack arms are still sealed.

    v3.2 needs it for one thing only: the fold map of design note 3.2 is
    ``index_of(scenario in sorted(ALL scenario ids)) mod K`` and must cover the scenarios
    that carry only an attack arm as well, so stage 1 (normal arms only) has to learn the
    complete scenario id LIST without loading a single attack episode.
    """

    run_dir = Path(run_dir)
    overrides, source, config_path = _resolve_variant_overrides(variant_overrides, run_dir)
    scenarios: dict[str, dict[str, Any]] = {}
    for trace_path in iter_trace_paths(run_dir):
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        variant = _variant_of(trace, trace_path.parent)
        pair_group = str(trace.get("pair_group_id", ""))
        if overrides and variant == CLEAN:
            override = overrides.get(pair_group) or overrides.get(
                str(trace.get("base_task_id", ""))
            )
            if override:
                variant = override
        indices = [
            int(e.get("episode_index", i))
            for i, e in enumerate(trace.get("episodes", ()) or [{}])
        ]
        block = scenarios.setdefault(
            pair_group, {"variants": {}, "episodes": 0, "traces": 0}
        )
        block["variants"][variant] = block["variants"].get(variant, 0) + len(indices)
        block["episodes"] += len(indices)
        block["traces"] += 1
    return {
        "run_dir": str(run_dir),
        "variant_override_source": source,
        "variant_override_config": config_path,
        "scenario_count": len(scenarios),
        "scenarios": {
            name: {
                "variants": dict(sorted(block["variants"].items())),
                "episodes": block["episodes"],
                "traces": block["traces"],
            }
            for name, block in sorted(scenarios.items())
        },
        "note": (
            "metadata only: trace.json plus the subset config; no routing shard was opened"
        ),
    }


def trace_digest(
    run_dir: Path | str,
    *,
    variants: Sequence[str] | None = None,
    variant_overrides: Mapping[str, str] | Path | str | None = VARIANT_OVERRIDES_AUTO,
) -> dict[str, Any]:
    """METADATA-ONLY content digest of the ``trace.json`` set of a run directory.

    ``variants`` restricts the set (v3.2 stage 1 hashes the NORMAL arms only, which is
    exactly the material it is allowed to see).  The digest is the sha256 of the sorted
    ``"<relative path>:<sha256 of the file>"`` lines, so it changes if a trace is added,
    removed, renamed or edited, and does not depend on directory iteration order.  No
    routing shard is opened.
    """

    run_dir = Path(run_dir)
    overrides, source, config_path = _resolve_variant_overrides(variant_overrides, run_dir)
    wanted = None if variants is None else {str(v) for v in variants}
    rows: list[str] = []
    counts: dict[str, int] = {}
    for trace_path in iter_trace_paths(run_dir):
        raw = trace_path.read_bytes()
        trace = json.loads(raw)
        variant = _variant_of(trace, trace_path.parent)
        if overrides and variant == CLEAN:
            override = overrides.get(str(trace.get("pair_group_id", ""))) or overrides.get(
                str(trace.get("base_task_id", ""))
            )
            if override:
                variant = override
        if wanted is not None and variant not in wanted:
            continue
        counts[variant] = counts.get(variant, 0) + 1
        rows.append(
            f"{trace_path.relative_to(run_dir).as_posix()}:{hashlib.sha256(raw).hexdigest()}"
        )
    digest = hashlib.sha256("\n".join(sorted(rows)).encode("utf-8")).hexdigest()
    return {
        "run_dir": str(run_dir),
        "variant_override_source": source,
        "variant_override_config": config_path,
        "variants": None if wanted is None else sorted(wanted),
        "trace_count": len(rows),
        "traces_by_variant": dict(sorted(counts.items())),
        "sha256": digest,
        "rule": (
            "sha256 of the sorted '<relative path>:<sha256 of trace.json>' lines; "
            "metadata only, no routing shard is opened"
        ),
    }


def variant_census(
    run_dir: Path | str,
    *,
    variant_overrides: Mapping[str, str] | Path | str | None = VARIANT_OVERRIDES_AUTO,
) -> dict[str, Any]:
    """METADATA-ONLY five-way episode census of a run directory.

    Reads ``trace.json`` (and the subset config the override join needs) and NOTHING else:
    no ``steps/*.safetensors`` is opened, so this runs on a sealed batch whose routing must
    not be touched.  It is the cheap check behind prereg section 4's per-arm episode table
    and behind the runner's non-smoke variant assertion.

    ``attack_bearing_episodes`` is the D5 denominator: attack-arm episodes minus the
    ``episode_index == 0`` of every ``multi_turn_user`` attack trace, which precedes the
    injection by construction.
    """

    run_dir = Path(run_dir)
    overrides, source, config_path = _resolve_variant_overrides(variant_overrides, run_dir)
    variants: dict[str, int] = {}
    traces: dict[str, int] = {}
    overridden: dict[str, int] = {}
    attack_bearing = 0
    attack_pre_injection = 0
    trace_paths = iter_trace_paths(run_dir)
    skipped_quarantine = len(sorted(run_dir.rglob("trace.json"))) - len(trace_paths)
    for trace_path in trace_paths:
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        variant = _variant_of(trace, trace_path.parent)
        pair_group = str(trace.get("pair_group_id", ""))
        if overrides and variant == CLEAN:
            override = overrides.get(pair_group) or overrides.get(
                str(trace.get("base_task_id", ""))
            )
            if override:
                variant = override
                overridden[override] = overridden.get(override, 0) + 1
        channel = str((trace.get("perturbation") or {}).get("channel", "") or "")
        indices = [
            int(e.get("episode_index", i))
            for i, e in enumerate(trace.get("episodes", ()) or [{}])
        ]
        variants[variant] = variants.get(variant, 0) + len(indices)
        traces[variant] = traces.get(variant, 0) + 1
        if variant == ATTACK:
            for index in indices:
                if channel == MULTI_TURN_CHANNEL and index == 0:
                    attack_pre_injection += 1
                else:
                    attack_bearing += 1
    return {
        "run_dir": str(run_dir),
        "variant_override_source": source,
        "variant_override_config": config_path,
        "variant_override_scenarios": len(overrides),
        "variant_overridden_traces": sum(overridden.values()),
        "variant_overridden": dict(sorted(overridden.items())),
        "episodes_by_variant": dict(sorted(variants.items())),
        "traces_by_variant": dict(sorted(traces.items())),
        "episode_count": sum(variants.values()),
        "attack_bearing_episodes": attack_bearing,
        "attack_pre_injection_episodes": attack_pre_injection,
        "skipped_quarantine": skipped_quarantine,
        "note": (
            "metadata only: trace.json plus the subset config; no routing shard was opened"
        ),
    }


def episode_manifest(episodes: Sequence[GEpisode]) -> dict[str, Any]:
    """Counts a run log can print without touching routing."""

    variants: dict[str, int] = {}
    channels: dict[str, int] = {tag: 0 for tag in ALL_TAGS}
    tokens = 0
    for episode in episodes:
        variants[episode.variant] = variants.get(episode.variant, 0) + 1
        tokens += episode.token_count
        for tag, count in episode.channel_counts().items():
            channels[tag] = channels.get(tag, 0) + count
    routers = {json.dumps(e.router.to_json(), sort_keys=True) for e in episodes}
    return {
        "episode_count": len(episodes),
        "session_count": len({e.session_id for e in episodes}),
        "scenario_count": len({e.pair_group_id for e in episodes}),
        "token_count": tokens,
        "variants": dict(sorted(variants.items())),
        "channel_tokens": channels,
        "labelled_episodes": sum(1 for e in episodes if e.labels),
        "router": json.loads(sorted(routers)[0]) if routers else None,
        "router_variants": len(routers),
    }


__all__ = [
    "ALL_TAGS",
    "ANALYSIS",
    "ATTACK",
    "BENIGN_CONTROL",
    "BENIGN_LEXICAL",
    "CLEAN",
    "COMMENTARY",
    "DEFAULT_G_CACHE_DIR",
    "DEFAULT_G_LOGIT_CACHE_DIR",
    "FINAL",
    "GEpisode",
    "G_CONFIG_DIR",
    "G_DEV_VARIANT_COUNTS",
    "HARMONY_CHANNELS",
    "HARMONY_PIECES",
    "KNOWN_VARIANTS",
    "LEGITIMATE_REFUSAL",
    "NORMAL_VARIANTS",
    "OTHER",
    "PROBE_ROLES",
    "QUARANTINE_DIR_NAMES",
    "RouterMeta",
    "SPECIAL_NORMAL_VARIANTS",
    "TokenTextVocabulary",
    "VARIANT_OVERRIDES_AUTO",
    "channel_runs",
    "channel_tag_array",
    "episode_logits",
    "episode_manifest",
    "episode_routing",
    "filtered_pool",
    "fixture_map",
    "fixture_map_from_config",
    "iter_trace_paths",
    "load_episode_probabilities",
    "load_g",
    "normal_episodes",
    "normalise_label_row",
    "read_labels",
    "read_step_manifest",
    "scenario_census",
    "segment_spans",
    "spans_from_token_ids",
    "subset_config_for_run",
    "token_text_vocabulary",
    "trace_digest",
    "variant_census",
    "variant_overrides_from_config",
    "with_token_axis",
]
