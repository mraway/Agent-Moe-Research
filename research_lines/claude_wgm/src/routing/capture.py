"""Forward-hook capture for Hugging Face OLMoE routers."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

import torch

from .schema import RoutingStep, StepMetadata


StepSink = Callable[[RoutingStep], None]


class RouterTraceRecorder:
    """Capture every OLMoE gate output while an explicit forward is active.

    The caller brackets each model forward with :meth:`record_step`, which
    supplies the token IDs and positions that the flattened router rows
    represent. Batch size is intentionally restricted to one for P1 so token
    alignment cannot be ambiguous.
    """

    def __init__(
        self,
        model: torch.nn.Module,
        *,
        sink: StepSink | None = None,
        retain_steps: bool = True,
    ) -> None:
        self.model = model
        self.sink = sink
        self.retain_steps = retain_steps
        self.steps: list[RoutingStep] = []
        self._handles: list[Any] = []
        self._active: StepMetadata | None = None
        self._active_started = 0.0
        self._layer_outputs: dict[
            int,
            tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor],
        ] = {}
        self._next_step_index = 0
        self._gates = self._find_gates(model)

    @property
    def next_step_index(self) -> int:
        """Index that will be assigned to the next successfully recorded forward."""

        return self._next_step_index

    @staticmethod
    def _find_gates(model: torch.nn.Module) -> list[torch.nn.Module]:
        base_model = getattr(model, "model", None)
        layers = getattr(base_model, "layers", None)
        if layers is None or not layers:
            raise TypeError("expected model.model.layers on an OLMoE causal LM")

        gates: list[torch.nn.Module] = []
        for layer_index, layer in enumerate(layers):
            mlp = getattr(layer, "mlp", None)
            gate = getattr(mlp, "gate", None)
            if gate is None:
                raise TypeError(f"layer {layer_index} has no mlp.gate router")
            if not all(hasattr(gate, name) for name in ("num_experts", "top_k", "weight")):
                raise TypeError(f"layer {layer_index} gate is not an OLMoE-style top-k router")
            gates.append(gate)
        return gates

    def __enter__(self) -> RouterTraceRecorder:
        if self._handles:
            raise RuntimeError("recorder hooks are already installed")
        for layer_index, gate in enumerate(self._gates):
            self._handles.append(gate.register_forward_hook(self._make_hook(layer_index)))
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        for handle in self._handles:
            handle.remove()
        self._handles.clear()
        self._active = None
        self._layer_outputs.clear()

    def _make_hook(self, layer_index: int) -> Callable[..., None]:
        def hook(module: torch.nn.Module, inputs: tuple[Any, ...], output: Any) -> None:
            del module, inputs
            if self._active is None:
                return
            if layer_index in self._layer_outputs:
                raise RuntimeError(f"router layer {layer_index} ran twice in one forward")
            if not isinstance(output, tuple) or len(output) < 3:
                raise TypeError("OLMoE router must return (logits, weights, expert_ids)")
            logits, weights, expert_ids = output[:3]
            expected_rows = len(self._active.token_ids)
            if logits.ndim != 2 or logits.shape[0] != expected_rows:
                raise ValueError(
                    f"layer {layer_index} produced {tuple(logits.shape)} for {expected_rows} tokens"
                )
            cpu_logits = logits.detach().to(device="cpu").contiguous()
            probabilities = torch.softmax(cpu_logits.float(), dim=-1)
            top_two = probabilities.topk(2, dim=-1).values
            entropy = -(probabilities * probabilities.clamp_min(torch.finfo(torch.float32).tiny).log()).sum(
                dim=-1
            )
            self._layer_outputs[layer_index] = (
                cpu_logits,
                weights.detach().to(device="cpu").contiguous(),
                expert_ids.detach().to(device="cpu", dtype=torch.int16).contiguous(),
                entropy.contiguous(),
                (top_two[:, 0] - top_two[:, 1]).contiguous(),
                entropy.exp().contiguous(),
            )

        return hook

    @contextmanager
    def record_step(
        self,
        *,
        phase: str,
        input_ids: torch.Tensor,
        positions: Sequence[int],
        token_texts: Sequence[str],
        token_roles: Sequence[str],
        conversation_turns: Sequence[int],
        agent_steps: Sequence[int],
        tool_boundaries: Sequence[bool] | None = None,
    ) -> Iterator[None]:
        if not self._handles:
            raise RuntimeError("use RouterTraceRecorder as a context manager")
        if self._active is not None:
            raise RuntimeError("routing steps cannot be nested")
        if input_ids.ndim != 2 or input_ids.shape[0] != 1:
            raise ValueError("P1 routing capture requires input_ids with batch size one")

        metadata = StepMetadata(
            step_index=self._next_step_index,
            phase=phase,  # type: ignore[arg-type]
            token_ids=tuple(int(value) for value in input_ids.detach().cpu().reshape(-1).tolist()),
            positions=tuple(int(value) for value in positions),
            token_texts=tuple(str(value) for value in token_texts),
            token_roles=tuple(str(value) for value in token_roles),
            conversation_turns=tuple(int(value) for value in conversation_turns),
            agent_steps=tuple(int(value) for value in agent_steps),
            tool_boundaries=tuple(bool(value) for value in (tool_boundaries or [False] * input_ids.numel())),
        )
        metadata.validate()
        self._active = metadata
        self._layer_outputs = {}
        self._active_started = time.perf_counter()
        try:
            yield
        except BaseException:
            self._active = None
            self._layer_outputs.clear()
            raise

        elapsed = time.perf_counter() - self._active_started
        missing = sorted(set(range(len(self._gates))) - set(self._layer_outputs))
        if missing:
            self._active = None
            self._layer_outputs.clear()
            raise RuntimeError(f"missing router outputs for layers: {missing}")

        ordered = [self._layer_outputs[index] for index in range(len(self._gates))]
        step = RoutingStep(
            metadata=replace(metadata, forward_seconds=elapsed),
            router_logits=torch.stack([values[0] for values in ordered]),
            top_k_weights=torch.stack([values[1] for values in ordered]),
            top_k_ids=torch.stack([values[2] for values in ordered]),
            router_entropy=torch.stack([values[3] for values in ordered]),
            router_margin=torch.stack([values[4] for values in ordered]),
            effective_experts=torch.stack([values[5] for values in ordered]),
        )
        step.validate()
        self._active = None
        self._layer_outputs = {}
        if self.retain_steps:
            self.steps.append(step)
        if self.sink is not None:
            self.sink(step)
        self._next_step_index += 1
