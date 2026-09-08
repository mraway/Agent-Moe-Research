"""In-memory schema for one model forward's routing observations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import torch


Phase = Literal["prefill", "decode"]


@dataclass(frozen=True)
class StepMetadata:
    """Token identity and execution context shared by all router layers."""

    step_index: int
    phase: Phase
    token_ids: tuple[int, ...]
    positions: tuple[int, ...]
    token_texts: tuple[str, ...]
    token_roles: tuple[str, ...]
    conversation_turns: tuple[int, ...]
    agent_steps: tuple[int, ...]
    tool_boundaries: tuple[bool, ...]
    forward_seconds: float = 0.0

    def validate(self) -> None:
        if self.step_index < 0:
            raise ValueError("step_index must be non-negative")
        if self.phase not in ("prefill", "decode"):
            raise ValueError(f"unsupported phase: {self.phase}")
        if not self.token_ids:
            raise ValueError("a routing step must contain at least one token")
        if len(self.positions) != len(self.token_ids):
            raise ValueError("positions and token_ids must have equal length")
        if len(self.token_texts) != len(self.token_ids):
            raise ValueError("token_texts and token_ids must have equal length")
        aligned_fields = {
            "token_roles": self.token_roles,
            "conversation_turns": self.conversation_turns,
            "agent_steps": self.agent_steps,
            "tool_boundaries": self.tool_boundaries,
        }
        for name, values in aligned_fields.items():
            if len(values) != len(self.token_ids):
                raise ValueError(f"{name} and token_ids must have equal length")


@dataclass(frozen=True)
class RoutingStep:
    """Routing tensors for one prefill or decode forward.

    Tensor shapes are `[layer, token, expert]` for ``router_logits`` and
    `[layer, token, k]` for the two top-k tensors. All tensors reside on CPU.
    """

    metadata: StepMetadata
    router_logits: torch.Tensor
    top_k_ids: torch.Tensor
    top_k_weights: torch.Tensor
    router_entropy: torch.Tensor
    router_margin: torch.Tensor
    effective_experts: torch.Tensor

    def validate(self) -> None:
        self.metadata.validate()
        token_count = len(self.metadata.token_ids)
        if self.router_logits.ndim != 3:
            raise ValueError("router_logits must have shape [layer, token, expert]")
        if self.top_k_ids.ndim != 3 or self.top_k_weights.ndim != 3:
            raise ValueError("top-k tensors must have shape [layer, token, k]")
        if self.router_logits.shape[1] != token_count:
            raise ValueError("router_logits token dimension is misaligned")
        if self.top_k_ids.shape[:2] != self.router_logits.shape[:2]:
            raise ValueError("top_k_ids layer/token dimensions are misaligned")
        if self.top_k_weights.shape != self.top_k_ids.shape:
            raise ValueError("top-k ids and weights must have identical shapes")
        expected_scalar_shape = self.router_logits.shape[:2]
        scalar_features = {
            "router_entropy": self.router_entropy,
            "router_margin": self.router_margin,
            "effective_experts": self.effective_experts,
        }
        for name, tensor in scalar_features.items():
            if tensor.shape != expected_scalar_shape:
                raise ValueError(f"{name} must have shape [layer, token]")
        if self.router_logits.device.type != "cpu":
            raise ValueError("router_logits must be copied to CPU")
        if self.top_k_ids.device.type != "cpu" or self.top_k_weights.device.type != "cpu":
            raise ValueError("top-k tensors must be copied to CPU")
        if any(tensor.device.type != "cpu" for tensor in scalar_features.values()):
            raise ValueError("derived router features must be stored on CPU")
