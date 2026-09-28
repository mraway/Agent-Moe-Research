"""Forward-hook capture for Hugging Face mixture-of-experts routers.

The recorder is model-agnostic: a :class:`RouterAdapter` knows where a given
architecture keeps its per-layer router, and how to turn one hook firing into
the three tensors the trace schema needs (full per-token router logits
``[T, E]``, top-k weights ``[T, k]`` and top-k expert ids ``[T, k]``). The
OLMoE adapter is the default and reproduces the original hard-coded behaviour
exactly; ``qwen3_moe`` and ``gpt_oss`` adapters are additive.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any

import torch

from .schema import RoutingStep, StepMetadata


StepSink = Callable[[RoutingStep], None]


@dataclass(frozen=True)
class RouterLayerCapture:
    """One router layer's raw observation for a single forward.

    ``logits`` are the full pre-softmax router scores over every expert with
    one row per token; ``top_k_weights`` and ``top_k_ids`` are the values the
    architecture actually multiplies the expert outputs by, and the expert
    indices it selected.
    """

    logits: torch.Tensor
    top_k_weights: torch.Tensor
    top_k_ids: torch.Tensor


def _rows_from_hook_input(inputs: tuple[Any, ...], hidden_dim: int) -> torch.Tensor:
    """Return the hook's hidden states as ``[tokens, hidden]`` rows."""

    if not inputs:
        raise TypeError("router hook received no positional inputs")
    hidden_states = inputs[0]
    if not isinstance(hidden_states, torch.Tensor):
        raise TypeError("first router input must be a hidden-state tensor")
    if hidden_states.shape[-1] != hidden_dim:
        raise ValueError(
            f"router input last dimension {hidden_states.shape[-1]} != hidden dim {hidden_dim}"
        )
    return hidden_states.reshape(-1, hidden_dim)


def _logits_from_router_weight(router: torch.nn.Module, rows: torch.Tensor) -> torch.Tensor:
    """Recompute full router logits from the router's own linear parameters.

    Used only when a module hides its logits (returns probabilities or top-k
    values alone) or when a fused/quantised kernel replaced the forward that
    would otherwise expose them.
    """

    weight = getattr(router, "weight", None)
    if not isinstance(weight, torch.Tensor):
        raise TypeError("router does not expose a linear weight to recover logits from")
    bias = getattr(router, "bias", None)
    if bias is not None and not isinstance(bias, torch.Tensor):
        bias = None
    return torch.nn.functional.linear(rows.to(weight.dtype), weight, bias)


class RouterAdapter:
    """Locate an architecture's routers and decode one hook firing."""

    name = "abstract"
    model_types: tuple[str, ...] = ()
    capture_module = "mlp.gate"
    logits_semantics = "pre_softmax_linear_over_all_experts"
    top_k_weight_semantics = "unspecified"
    shared_expert_count = 0

    def attach_points(self, model: torch.nn.Module) -> list[tuple[int, torch.nn.Module]]:
        """Return ``(decoder_layer_index, module_to_hook)`` for every MoE layer."""

        raise NotImplementedError

    def router_of(self, module: torch.nn.Module) -> torch.nn.Module:
        """Return the module carrying ``num_experts`` / ``top_k`` / ``weight``."""

        return module

    def weight_semantics(self, router: torch.nn.Module) -> str:
        """What the captured top-k weights mean for this router instance."""

        del router
        return self.top_k_weight_semantics

    def parse(
        self,
        *,
        layer_index: int,
        module: torch.nn.Module,
        inputs: tuple[Any, ...],
        output: Any,
        expected_rows: int,
    ) -> RouterLayerCapture:
        raise NotImplementedError

    # -- shared helpers -------------------------------------------------

    @staticmethod
    def _decoder_layers(model: torch.nn.Module) -> Any:
        base_model = getattr(model, "model", None)
        layers = getattr(base_model, "layers", None)
        if layers is None or not layers:
            raise TypeError("expected model.model.layers on a decoder-only causal LM")
        return layers

    def describe(
        self,
        model: torch.nn.Module,
        points: Sequence[tuple[int, torch.nn.Module]],
    ) -> dict[str, Any]:
        """Router facts recorded into the trace instead of being assumed."""

        if not points:
            raise TypeError(f"adapter {self.name} found no router layers")
        routers = [self.router_of(module) for _, module in points]
        expert_counts = {int(router.num_experts) for router in routers}
        top_ks = {int(router.top_k) for router in routers}
        if len(expert_counts) != 1 or len(top_ks) != 1:
            raise TypeError(
                f"adapter {self.name} found non-uniform routers: "
                f"num_experts={sorted(expert_counts)} top_k={sorted(top_ks)}"
            )
        config = getattr(model, "config", None)
        try:
            total_layers = len(self._decoder_layers(model))
        except TypeError:
            total_layers = len(points)
        first = routers[0]
        return {
            "router_adapter": self.name,
            "model_type": getattr(config, "model_type", None),
            "num_experts": expert_counts.pop(),
            "top_k": top_ks.pop(),
            "num_moe_layers": len(points),
            "num_hidden_layers": total_layers,
            "moe_layer_indices": [int(index) for index, _ in points],
            "capture_module": self.capture_module,
            "router_hidden_dim": int(getattr(first, "hidden_dim", -1)),
            "router_has_bias": getattr(first, "bias", None) is not None,
            "router_logits_semantics": self.logits_semantics,
            "top_k_weight_semantics": self.weight_semantics(first),
            "shared_expert_count": self.shared_expert_count,
        }


def _softmax_weight_semantics(router: torch.nn.Module) -> str:
    if bool(getattr(router, "norm_topk_prob", False)):
        return "renormalised_softmax_probability_over_top_k"
    return "softmax_probability_over_all_experts"


def _is_top_k_router(module: Any) -> bool:
    return module is not None and all(
        hasattr(module, name) for name in ("num_experts", "top_k", "weight")
    )


class OlmoeRouterAdapter(RouterAdapter):
    """Default adapter: ``model.model.layers[i].mlp.gate`` on every layer.

    Reproduces the pre-adapter behaviour byte for byte, including the error
    messages, so existing OLMoE traces stay reproducible.
    """

    name = "olmoe"
    model_types = ("olmoe",)
    capture_module = "mlp.gate"
    top_k_weight_semantics = "softmax_probability_over_all_experts"

    def weight_semantics(self, router: torch.nn.Module) -> str:
        return _softmax_weight_semantics(router)

    def attach_points(self, model: torch.nn.Module) -> list[tuple[int, torch.nn.Module]]:
        base_model = getattr(model, "model", None)
        layers = getattr(base_model, "layers", None)
        if layers is None or not layers:
            raise TypeError("expected model.model.layers on an OLMoE causal LM")

        points: list[tuple[int, torch.nn.Module]] = []
        for layer_index, layer in enumerate(layers):
            mlp = getattr(layer, "mlp", None)
            gate = getattr(mlp, "gate", None)
            if gate is None:
                raise TypeError(f"layer {layer_index} has no mlp.gate router")
            if not _is_top_k_router(gate):
                raise TypeError(f"layer {layer_index} gate is not an OLMoE-style top-k router")
            points.append((layer_index, gate))
        return points

    def parse(
        self,
        *,
        layer_index: int,
        module: torch.nn.Module,
        inputs: tuple[Any, ...],
        output: Any,
        expected_rows: int,
    ) -> RouterLayerCapture:
        del module, inputs
        if not isinstance(output, tuple) or len(output) < 3:
            raise TypeError("OLMoE router must return (logits, weights, expert_ids)")
        logits, weights, expert_ids = output[:3]
        if logits.ndim != 2 or logits.shape[0] != expected_rows:
            raise ValueError(
                f"layer {layer_index} produced {tuple(logits.shape)} for {expected_rows} tokens"
            )
        return RouterLayerCapture(logits=logits, top_k_weights=weights, top_k_ids=expert_ids)


class Qwen3MoeRouterAdapter(RouterAdapter):
    """``Qwen3MoeTopKRouter`` at ``layers[i].mlp.gate`` on sparse layers only.

    Qwen3-MoE keeps dense ``Qwen3MoeMLP`` layers when ``mlp_only_layers`` or
    ``decoder_sparse_step`` say so; those layers are skipped rather than
    treated as an error. The router returns
    ``(router_logits, router_scores, router_indices)`` where ``router_logits``
    are the full pre-softmax scores and ``router_scores`` are top-k softmax
    probabilities renormalised to sum to one when ``norm_topk_prob``.
    """

    name = "qwen3_moe"
    model_types = ("qwen3_moe", "qwen3_next", "qwen2_moe")
    capture_module = "mlp.gate"
    top_k_weight_semantics = "renormalised_softmax_probability_over_top_k"

    def weight_semantics(self, router: torch.nn.Module) -> str:
        return _softmax_weight_semantics(router)

    def attach_points(self, model: torch.nn.Module) -> list[tuple[int, torch.nn.Module]]:
        layers = self._decoder_layers(model)
        points: list[tuple[int, torch.nn.Module]] = []
        for layer_index, layer in enumerate(layers):
            mlp = getattr(layer, "mlp", None)
            gate = getattr(mlp, "gate", None)
            if not _is_top_k_router(gate):
                continue  # dense MLP layer
            points.append((layer_index, gate))
        if not points:
            raise TypeError("no Qwen3-MoE top-k routers found under model.model.layers[*].mlp.gate")
        return points

    def parse(
        self,
        *,
        layer_index: int,
        module: torch.nn.Module,
        inputs: tuple[Any, ...],
        output: Any,
        expected_rows: int,
    ) -> RouterLayerCapture:
        num_experts = int(module.num_experts)
        top_k = int(module.top_k)
        logits: torch.Tensor | None = None
        weights: torch.Tensor | None = None
        expert_ids: torch.Tensor | None = None
        if isinstance(output, tuple) and len(output) >= 3:
            candidate = output[0]
            if (
                isinstance(candidate, torch.Tensor)
                and candidate.ndim == 2
                and candidate.shape[1] == num_experts
            ):
                logits = candidate
                weights = output[1]
                expert_ids = output[2]
        if logits is None:
            rows = _rows_from_hook_input(inputs, int(module.hidden_dim))
            logits = _logits_from_router_weight(module, rows)
            weights = None
            expert_ids = None
        if logits.ndim != 2 or logits.shape[0] != expected_rows:
            raise ValueError(
                f"layer {layer_index} produced {tuple(logits.shape)} for {expected_rows} tokens"
            )
        if weights is None or expert_ids is None:
            probabilities = torch.softmax(logits, dtype=torch.float, dim=-1)
            values, expert_ids = torch.topk(probabilities, top_k, dim=-1)
            if bool(getattr(module, "norm_topk_prob", False)):
                values = values / values.sum(dim=-1, keepdim=True)
            weights = values.to(logits.dtype)
        return RouterLayerCapture(logits=logits, top_k_weights=weights, top_k_ids=expert_ids)


class GptOssRouterAdapter(RouterAdapter):
    """GPT-OSS routers, hooked at ``layers[i].mlp`` rather than ``mlp.router``.

    ``GptOssTopKRouter`` itself returns ``(router_logits, router_scores,
    router_indices)``, but the MXFP4 path in
    ``transformers.integrations.mxfp4`` replaces ``GptOssMLP.forward`` with a
    function that inlines ``F.linear(hidden, router.weight, router.bias)`` and
    never calls the router submodule, so a hook on ``mlp.router`` would never
    fire. Hooking the MLP works for both paths: the MXFP4 forward returns the
    full logits as its second output, and otherwise the logits are recomputed
    from the router's (unquantised) weight and bias applied to the hook input,
    which is exactly what both forwards compute.

    Top-k weights are the softmax over the selected logits only (GPT-OSS does
    not softmax over all experts), so they sum to one but are not comparable
    to a full-vocabulary probability.
    """

    name = "gpt_oss"
    model_types = ("gpt_oss",)
    capture_module = "mlp"
    top_k_weight_semantics = "softmax_over_selected_logits_only"

    def attach_points(self, model: torch.nn.Module) -> list[tuple[int, torch.nn.Module]]:
        if bool(getattr(model, "use_kernels", False)):
            raise TypeError(
                "GPT-OSS routing capture requires use_kernels=False: the MegaBlocks "
                "fused MoE kernel replaces GptOssMLP.forward and hides the router"
            )
        layers = self._decoder_layers(model)
        points: list[tuple[int, torch.nn.Module]] = []
        for layer_index, layer in enumerate(layers):
            mlp = getattr(layer, "mlp", None)
            router = getattr(mlp, "router", None)
            if not _is_top_k_router(router):
                continue
            points.append((layer_index, mlp))
        if not points:
            raise TypeError("no GPT-OSS routers found under model.model.layers[*].mlp.router")
        return points

    def router_of(self, module: torch.nn.Module) -> torch.nn.Module:
        return module.router

    def parse(
        self,
        *,
        layer_index: int,
        module: torch.nn.Module,
        inputs: tuple[Any, ...],
        output: Any,
        expected_rows: int,
    ) -> RouterLayerCapture:
        router = self.router_of(module)
        num_experts = int(router.num_experts)
        top_k = int(router.top_k)
        logits: torch.Tensor | None = None
        candidates = output if isinstance(output, tuple) else (output,)
        for candidate in candidates:
            if (
                isinstance(candidate, torch.Tensor)
                and candidate.ndim == 2
                and candidate.shape == (expected_rows, num_experts)
                and candidate.is_floating_point()
            ):
                logits = candidate
                break
        if logits is None:
            rows = _rows_from_hook_input(inputs, int(router.hidden_dim))
            logits = _logits_from_router_weight(router, rows)
        if logits.ndim != 2 or logits.shape[0] != expected_rows:
            raise ValueError(
                f"layer {layer_index} produced {tuple(logits.shape)} for {expected_rows} tokens"
            )
        values, expert_ids = torch.topk(logits, top_k, dim=-1)
        weights = torch.softmax(values, dim=-1, dtype=values.dtype)
        return RouterLayerCapture(logits=logits, top_k_weights=weights, top_k_ids=expert_ids)


_ROUTER_ADAPTERS: dict[str, Callable[[], RouterAdapter]] = {}
DEFAULT_ROUTER_ADAPTER = "olmoe"


def register_router_adapter(name: str, factory: Callable[[], RouterAdapter]) -> None:
    """Register a router adapter under ``name`` (last registration wins)."""

    _ROUTER_ADAPTERS[str(name)] = factory


def available_router_adapters() -> tuple[str, ...]:
    return tuple(sorted(_ROUTER_ADAPTERS))


for _adapter_class in (OlmoeRouterAdapter, Qwen3MoeRouterAdapter, GptOssRouterAdapter):
    register_router_adapter(_adapter_class.name, _adapter_class)


def resolve_router_adapter(
    model: torch.nn.Module,
    adapter: str | RouterAdapter | None = None,
) -> RouterAdapter:
    """Pick an adapter by explicit name, else by ``config.model_type``.

    Anything unrecognised falls back to the OLMoE adapter, which is what the
    recorder used before adapters existed.
    """

    if isinstance(adapter, RouterAdapter):
        return adapter
    if isinstance(adapter, str):
        if adapter not in _ROUTER_ADAPTERS:
            raise ValueError(
                f"unknown router adapter {adapter!r}; available: {available_router_adapters()}"
            )
        return _ROUTER_ADAPTERS[adapter]()
    model_type = getattr(getattr(model, "config", None), "model_type", None)
    if model_type is not None:
        for name in sorted(_ROUTER_ADAPTERS):
            candidate = _ROUTER_ADAPTERS[name]()
            if model_type in candidate.model_types:
                return candidate
    return _ROUTER_ADAPTERS[DEFAULT_ROUTER_ADAPTER]()


def describe_model_routers(
    model: torch.nn.Module,
    adapter: str | RouterAdapter | None = None,
) -> dict[str, Any]:
    """Router metadata (experts, top-k, MoE layer indices) without running a forward."""

    resolved = resolve_router_adapter(model, adapter)
    return resolved.describe(model, resolved.attach_points(model))


class RouterTraceRecorder:
    """Capture every MoE gate output while an explicit forward is active.

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
        router_adapter: str | RouterAdapter | None = None,
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
        self.adapter = resolve_router_adapter(model, router_adapter)
        self._attach_points = self.adapter.attach_points(model)
        self._gates = [module for _, module in self._attach_points]
        self.router_metadata = self.adapter.describe(model, self._attach_points)

    @property
    def next_step_index(self) -> int:
        """Index that will be assigned to the next successfully recorded forward."""

        return self._next_step_index

    @property
    def moe_layer_indices(self) -> tuple[int, ...]:
        """Decoder-layer index for each row of the captured layer axis."""

        return tuple(index for index, _ in self._attach_points)

    @staticmethod
    def _find_gates(model: torch.nn.Module) -> list[torch.nn.Module]:
        """Backwards-compatible OLMoE gate lookup."""

        return [module for _, module in OlmoeRouterAdapter().attach_points(model)]

    def __enter__(self) -> RouterTraceRecorder:
        if self._handles:
            raise RuntimeError("recorder hooks are already installed")
        for capture_index, (_, gate) in enumerate(self._attach_points):
            self._handles.append(gate.register_forward_hook(self._make_hook(capture_index)))
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        for handle in self._handles:
            handle.remove()
        self._handles.clear()
        self._active = None
        self._layer_outputs.clear()

    def _make_hook(self, layer_index: int) -> Callable[..., None]:
        def hook(module: torch.nn.Module, inputs: tuple[Any, ...], output: Any) -> None:
            if self._active is None:
                return
            if layer_index in self._layer_outputs:
                raise RuntimeError(f"router layer {layer_index} ran twice in one forward")
            expected_rows = len(self._active.token_ids)
            capture = self.adapter.parse(
                layer_index=layer_index,
                module=module,
                inputs=inputs,
                output=output,
                expected_rows=expected_rows,
            )
            logits = capture.logits
            weights = capture.top_k_weights
            expert_ids = capture.top_k_ids
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
