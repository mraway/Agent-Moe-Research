"""Integrity and token-alignment validation for sharded routing traces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import torch
from safetensors.torch import load_file


TOKEN_FIELDS = (
    "token_ids",
    "positions",
    "token_texts",
    "token_roles",
    "conversation_turns",
    "agent_steps",
    "tool_boundaries",
)


DEFAULT_WEIGHT_SEMANTICS = "softmax_probability_over_all_experts"


def expected_top_k_weights(
    *,
    logits: torch.Tensor,
    expert_ids: torch.Tensor,
    selected_probabilities: torch.Tensor,
    semantics: str,
) -> torch.Tensor:
    """Reconstruct the top-k weights a router architecture is expected to emit.

    OLMoE without ``norm_topk_prob`` (the historical default) emits the plain
    softmax probability of each selected expert, which is what traces without
    a ``router`` metadata block are validated against.
    """

    if semantics in (DEFAULT_WEIGHT_SEMANTICS, "unspecified"):
        return selected_probabilities
    if semantics == "renormalised_softmax_probability_over_top_k":
        return selected_probabilities / selected_probabilities.sum(dim=-1, keepdim=True)
    if semantics == "softmax_over_selected_logits_only":
        return torch.softmax(logits.float().gather(-1, expert_ids), dim=-1)
    raise ValueError(f"unsupported top_k_weight_semantics: {semantics}")


def validate_trace(trace_dir: Path, *, weight_atol: float = 0.003) -> dict[str, Any]:
    """Validate all shards in a schema-v3 trace and return diagnostics."""

    trace_dir = trace_dir.resolve()
    trace_path = trace_dir / "trace.json"
    manifest_path = trace_dir / "manifest.jsonl"
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    if trace.get("schema_version") != 3:
        raise ValueError(f"expected schema_version 3, found {trace.get('schema_version')}")
    if not trace.get("complete"):
        raise ValueError("trace is marked incomplete")
    router_metadata = trace.get("router") or {}
    weight_semantics = str(
        router_metadata.get("top_k_weight_semantics", DEFAULT_WEIGHT_SEMANTICS)
    )

    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line]
    if len(rows) != trace.get("step_count"):
        raise ValueError("trace step_count does not match manifest length")
    if not rows:
        raise ValueError("trace manifest is empty")

    max_weight_error = 0.0
    full_logits_bytes = 0
    top_k_bytes = 0
    total_tokens = 0
    expected_next_position: int | None = None
    layer_count: int | None = None
    expert_count: int | None = None
    top_k: int | None = None

    for expected_step, row in enumerate(rows):
        if row["step_index"] != expected_step:
            raise ValueError(f"manifest step {expected_step} is numbered {row['step_index']}")
        token_count = len(row["token_ids"])
        for field in TOKEN_FIELDS:
            if len(row[field]) != token_count:
                raise ValueError(f"step {expected_step} field {field} is not token-aligned")
        positions = row["positions"]
        if positions != list(range(positions[0], positions[0] + token_count)):
            raise ValueError(f"step {expected_step} positions are not contiguous")
        if row["phase"] == "prefill":
            expected_next_position = None
        if expected_next_position is not None and positions[0] != expected_next_position:
            raise ValueError(f"step {expected_step} does not continue the prior token positions")
        expected_next_position = positions[-1] + 1

        shard_path = (trace_dir / row["tensor_file"]).resolve()
        if trace_dir not in shard_path.parents:
            raise ValueError(f"step {expected_step} tensor path escapes its trace directory")
        tensors = load_file(shard_path)
        required = {
            "token_ids",
            "positions",
            "router_logits",
            "top_k_ids",
            "top_k_weights",
            "router_entropy",
            "router_margin",
            "effective_experts",
        }
        if set(tensors) != required:
            raise ValueError(f"step {expected_step} tensor keys do not match schema")
        if tensors["token_ids"].tolist() != row["token_ids"]:
            raise ValueError(f"step {expected_step} token IDs differ between manifest and shard")
        if tensors["positions"].tolist() != positions:
            raise ValueError(f"step {expected_step} positions differ between manifest and shard")

        logits = tensors["router_logits"]
        expert_ids = tensors["top_k_ids"].long()
        weights = tensors["top_k_weights"].float()
        if logits.ndim != 3 or expert_ids.ndim != 3 or weights.ndim != 3:
            raise ValueError(f"step {expected_step} has invalid tensor ranks")
        if logits.shape[1] != token_count or expert_ids.shape[:2] != logits.shape[:2]:
            raise ValueError(f"step {expected_step} has misaligned tensor dimensions")
        if weights.shape != expert_ids.shape:
            raise ValueError(f"step {expected_step} top-k tensor shapes differ")

        current_layer_count, _, current_expert_count = logits.shape
        current_top_k = expert_ids.shape[-1]
        layer_count = current_layer_count if layer_count is None else layer_count
        expert_count = current_expert_count if expert_count is None else expert_count
        top_k = current_top_k if top_k is None else top_k
        if (current_layer_count, current_expert_count, current_top_k) != (
            layer_count,
            expert_count,
            top_k,
        ):
            raise ValueError(f"step {expected_step} changes the layer/expert/top-k dimensions")
        if expert_ids.min().item() < 0 or expert_ids.max().item() >= expert_count:
            raise ValueError(f"step {expected_step} contains an out-of-range expert ID")

        probabilities = torch.softmax(logits.float(), dim=-1)
        selected_probabilities = probabilities.gather(-1, expert_ids)
        kth_probability = probabilities.topk(top_k, dim=-1).values[..., -1]
        if torch.any(selected_probabilities.min(dim=-1).values + 1e-7 < kth_probability):
            raise ValueError(f"step {expected_step} expert IDs are not a valid top-k selection")
        expected_weights = expected_top_k_weights(
            logits=logits,
            expert_ids=expert_ids,
            selected_probabilities=selected_probabilities,
            semantics=weight_semantics,
        )
        weight_error = float((expected_weights - weights).abs().max().item())
        max_weight_error = max(max_weight_error, weight_error)
        if weight_error > weight_atol:
            raise ValueError(
                f"step {expected_step} top-k weights differ from logits by {weight_error:.6f}"
            )

        entropy = -(probabilities * probabilities.clamp_min(torch.finfo(torch.float32).tiny).log()).sum(dim=-1)
        top_two = probabilities.topk(2, dim=-1).values
        expected_features = {
            "router_entropy": entropy,
            "router_margin": top_two[..., 0] - top_two[..., 1],
            "effective_experts": entropy.exp(),
        }
        for name, expected in expected_features.items():
            if not torch.allclose(tensors[name].float(), expected, rtol=1e-6, atol=1e-6):
                raise ValueError(f"step {expected_step} derived feature {name} is inconsistent")

        total_tokens += token_count
        full_logits_bytes += logits.numel() * logits.element_size()
        top_k_bytes += expert_ids.numel() * 2 + tensors["top_k_weights"].numel() * tensors[
            "top_k_weights"
        ].element_size()

    return {
        "passed": True,
        "schema_version": 3,
        "step_count": len(rows),
        "token_count": total_tokens,
        "layer_count": layer_count,
        "expert_count": expert_count,
        "top_k": top_k,
        "max_top_k_weight_error": max_weight_error,
        "top_k_weight_atol": weight_atol,
        "logical_full_logits_bytes": full_logits_bytes,
        "logical_top_k_bytes": top_k_bytes,
    }
