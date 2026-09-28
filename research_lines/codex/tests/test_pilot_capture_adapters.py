"""Router-adapter regression and coverage tests for the model pilot.

The first test is the OLMoE regression guard: it re-implements the
pre-adapter hook verbatim and asserts the adapter-based recorder produces
bit-identical tensors on a tiny synthetic gate.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import MethodType

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from routing import (  # noqa: E402
    GptOssRouterAdapter,
    ShardedTraceWriter,
    validate_trace,
    OlmoeRouterAdapter,
    Qwen3MoeRouterAdapter,
    RouterTraceRecorder,
    available_router_adapters,
    describe_model_routers,
    resolve_router_adapter,
)


# --------------------------------------------------------------------------
# Frozen pre-adapter reference implementation (do not refactor).
# --------------------------------------------------------------------------


def _reference_olmoe_capture(model: torch.nn.Module, input_ids: torch.Tensor) -> dict[str, torch.Tensor]:
    gates: list[torch.nn.Module] = []
    for layer_index, layer in enumerate(model.model.layers):
        mlp = getattr(layer, "mlp", None)
        gate = getattr(mlp, "gate", None)
        if gate is None:
            raise TypeError(f"layer {layer_index} has no mlp.gate router")
        if not all(hasattr(gate, name) for name in ("num_experts", "top_k", "weight")):
            raise TypeError(f"layer {layer_index} gate is not an OLMoE-style top-k router")
        gates.append(gate)

    captured: dict[int, tuple[torch.Tensor, ...]] = {}
    expected_rows = int(input_ids.numel())

    def make_hook(layer_index: int):
        def hook(module, inputs, output):
            del module, inputs
            if not isinstance(output, tuple) or len(output) < 3:
                raise TypeError("OLMoE router must return (logits, weights, expert_ids)")
            logits, weights, expert_ids = output[:3]
            if logits.ndim != 2 or logits.shape[0] != expected_rows:
                raise ValueError(
                    f"layer {layer_index} produced {tuple(logits.shape)} for {expected_rows} tokens"
                )
            cpu_logits = logits.detach().to(device="cpu").contiguous()
            probabilities = torch.softmax(cpu_logits.float(), dim=-1)
            top_two = probabilities.topk(2, dim=-1).values
            entropy = -(
                probabilities * probabilities.clamp_min(torch.finfo(torch.float32).tiny).log()
            ).sum(dim=-1)
            captured[layer_index] = (
                cpu_logits,
                weights.detach().to(device="cpu").contiguous(),
                expert_ids.detach().to(device="cpu", dtype=torch.int16).contiguous(),
                entropy.contiguous(),
                (top_two[:, 0] - top_two[:, 1]).contiguous(),
                entropy.exp().contiguous(),
            )

        return hook

    handles = [gate.register_forward_hook(make_hook(index)) for index, gate in enumerate(gates)]
    try:
        model(input_ids)
    finally:
        for handle in handles:
            handle.remove()

    ordered = [captured[index] for index in range(len(gates))]
    names = (
        "router_logits",
        "top_k_weights",
        "top_k_ids",
        "router_entropy",
        "router_margin",
        "effective_experts",
    )
    return {
        name: torch.stack([values[position] for values in ordered])
        for position, name in enumerate(names)
    }


# --------------------------------------------------------------------------
# Tiny synthetic OLMoE-shaped model (same construction as test_routing_capture).
# --------------------------------------------------------------------------


class _SyntheticGate(torch.nn.Module):
    def __init__(self, layer_index: int, *, num_experts: int = 5, top_k: int = 2, hidden: int = 3) -> None:
        super().__init__()
        self.num_experts = num_experts
        self.top_k = top_k
        self.hidden_dim = hidden
        self.norm_topk_prob = True
        generator = torch.Generator().manual_seed(1000 + layer_index)
        self.weight = torch.nn.Parameter(
            torch.randn(num_experts, hidden, generator=generator, dtype=torch.float32)
        )

    def forward(self, hidden_states: torch.Tensor):
        rows = hidden_states.reshape(-1, self.hidden_dim)
        logits = torch.nn.functional.linear(rows, self.weight)
        probabilities = torch.softmax(logits, dtype=torch.float32, dim=-1)
        values, expert_ids = torch.topk(probabilities, self.top_k, dim=-1)
        if self.norm_topk_prob:
            values = values / values.sum(dim=-1, keepdim=True)
        return logits, values.to(logits.dtype), expert_ids


class _SyntheticMlp(torch.nn.Module):
    def __init__(self, layer_index: int) -> None:
        super().__init__()
        self.gate = _SyntheticGate(layer_index)


class _SyntheticLayer(torch.nn.Module):
    def __init__(self, layer_index: int) -> None:
        super().__init__()
        self.mlp = _SyntheticMlp(layer_index)


class _SyntheticBase(torch.nn.Module):
    def __init__(self, layer_count: int) -> None:
        super().__init__()
        self.layers = torch.nn.ModuleList([_SyntheticLayer(index) for index in range(layer_count)])


class _SyntheticCausalLm(torch.nn.Module):
    def __init__(self, layer_count: int = 3) -> None:
        super().__init__()
        self.model = _SyntheticBase(layer_count)

    def forward(self, input_ids: torch.Tensor) -> None:
        hidden = input_ids.float().unsqueeze(-1).repeat(1, 1, 3)
        for layer in self.model.layers:
            layer.mlp.gate(hidden)


class _HiddenLogitsGate(_SyntheticGate):
    """A gate that hides its logits: it returns only top-k values and ids."""

    def forward(self, hidden_states: torch.Tensor):
        logits, values, expert_ids = super().forward(hidden_states)
        del logits
        return values, expert_ids


class _HiddenLogitsCausalLm(_SyntheticCausalLm):
    def __init__(self) -> None:
        super().__init__(layer_count=2)
        for layer in self.model.layers:
            layer.mlp.gate = _HiddenLogitsGate(0)


def _record(model: torch.nn.Module, input_ids: torch.Tensor, *, adapter=None, forward=None):
    with RouterTraceRecorder(model, router_adapter=adapter) as recorder:
        token_count = int(input_ids.numel())
        with recorder.record_step(
            phase="prefill",
            input_ids=input_ids,
            positions=range(token_count),
            token_texts=tuple(str(value) for value in input_ids.reshape(-1).tolist()),
            token_roles=("user",) * token_count,
            conversation_turns=(1,) * token_count,
            agent_steps=(0,) * token_count,
        ):
            with torch.inference_mode():
                (forward or model)(input_ids)
        return recorder, recorder.steps[0]


class OlmoeRegressionTest(unittest.TestCase):
    def test_adapter_recorder_matches_frozen_pre_adapter_capture(self) -> None:
        input_ids = torch.tensor([[2, 5, 7, 11]])
        reference = _reference_olmoe_capture(_SyntheticCausalLm(), input_ids)
        _, step = _record(_SyntheticCausalLm(), input_ids)
        for name, expected in reference.items():
            actual = getattr(step, name)
            self.assertEqual(actual.dtype, expected.dtype, name)
            self.assertTrue(torch.equal(actual, expected), f"{name} diverged from the frozen capture")

    def test_default_adapter_is_olmoe_for_models_without_config(self) -> None:
        adapter = resolve_router_adapter(_SyntheticCausalLm())
        self.assertIsInstance(adapter, OlmoeRouterAdapter)
        self.assertIn("olmoe", available_router_adapters())

    def test_olmoe_adapter_error_messages_are_unchanged(self) -> None:
        class _NoGateLayer(torch.nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.mlp = torch.nn.Identity()

        class _NoGateModel(torch.nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.model = torch.nn.Module()
                self.model.layers = torch.nn.ModuleList([_NoGateLayer()])

        with self.assertRaisesRegex(TypeError, r"layer 0 has no mlp\.gate router"):
            RouterTraceRecorder(_NoGateModel())

        empty = torch.nn.Module()
        empty.model = torch.nn.Module()
        empty.model.layers = torch.nn.ModuleList()
        with self.assertRaisesRegex(TypeError, "expected model.model.layers on an OLMoE causal LM"):
            RouterTraceRecorder(empty)

    def test_router_metadata_reports_measured_geometry(self) -> None:
        model = _SyntheticCausalLm(layer_count=3)
        metadata = describe_model_routers(model)
        self.assertEqual(metadata["router_adapter"], "olmoe")
        self.assertEqual(metadata["num_experts"], 5)
        self.assertEqual(metadata["top_k"], 2)
        self.assertEqual(metadata["num_moe_layers"], 3)
        self.assertEqual(metadata["moe_layer_indices"], [0, 1, 2])
        self.assertEqual(metadata["capture_module"], "mlp.gate")

    def test_logits_are_reconstructed_when_the_module_hides_them(self) -> None:
        model = _HiddenLogitsCausalLm()
        input_ids = torch.tensor([[3, 4]])
        _, step = _record(model, input_ids, adapter="qwen3_moe")
        self.assertEqual(step.router_logits.shape, (2, 2, 5))
        hidden = input_ids.float().unsqueeze(-1).repeat(1, 1, 3).reshape(-1, 3)
        for layer_index, layer in enumerate(model.model.layers):
            expected = torch.nn.functional.linear(hidden, layer.mlp.gate.weight)
            self.assertTrue(torch.allclose(step.router_logits[layer_index], expected, atol=1e-6))
            values, ids = torch.topk(torch.softmax(expected, dim=-1), 2, dim=-1)
            self.assertTrue(torch.equal(step.top_k_ids[layer_index].long(), ids))


def _tiny_qwen3_moe():
    from transformers import Qwen3MoeConfig
    from transformers.models.qwen3_moe.modeling_qwen3_moe import Qwen3MoeForCausalLM

    torch.manual_seed(7)
    config = Qwen3MoeConfig(
        vocab_size=32,
        hidden_size=16,
        intermediate_size=24,
        moe_intermediate_size=8,
        num_hidden_layers=3,
        num_attention_heads=4,
        num_key_value_heads=2,
        head_dim=4,
        num_experts=6,
        num_experts_per_tok=2,
        decoder_sparse_step=1,
        mlp_only_layers=[1],
        max_position_embeddings=64,
        attn_implementation="eager",
    )
    return Qwen3MoeForCausalLM(config).eval()


def _tiny_gpt_oss():
    from transformers import GptOssConfig
    from transformers.models.gpt_oss.modeling_gpt_oss import GptOssForCausalLM

    torch.manual_seed(11)
    config = GptOssConfig(
        vocab_size=32,
        hidden_size=16,
        intermediate_size=8,
        num_hidden_layers=2,
        num_attention_heads=4,
        num_key_value_heads=2,
        head_dim=4,
        num_local_experts=5,
        num_experts_per_tok=2,
        max_position_embeddings=64,
        sliding_window=8,
        attn_implementation="eager",
        rope_parameters={"rope_type": "default", "rope_theta": 10000.0},
    )
    return GptOssForCausalLM(config).eval()


class Qwen3MoeAdapterTest(unittest.TestCase):
    def test_adapter_is_selected_from_model_type(self) -> None:
        model = _tiny_qwen3_moe()
        self.assertIsInstance(resolve_router_adapter(model), Qwen3MoeRouterAdapter)

    def test_capture_skips_dense_layers_and_matches_model_router_logits(self) -> None:
        model = _tiny_qwen3_moe()
        input_ids = torch.tensor([[1, 2, 3, 4]])
        recorder, step = _record(model, input_ids, forward=lambda ids: model(input_ids=ids))

        self.assertEqual(recorder.moe_layer_indices, (0, 2))
        self.assertEqual(step.router_logits.shape, (2, 4, 6))
        self.assertEqual(step.top_k_ids.shape, (2, 4, 2))
        self.assertEqual(step.top_k_weights.shape, (2, 4, 2))

        with torch.inference_mode():
            reference = model(input_ids=input_ids, output_router_logits=True).router_logits
        self.assertEqual(len(reference), 2)
        self.assertTrue(torch.allclose(torch.stack(list(reference)).float(), step.router_logits.float()))

    def test_top_k_ids_agree_with_the_router_module_selection(self) -> None:
        model = _tiny_qwen3_moe()
        input_ids = torch.tensor([[5, 6, 7]])
        captured_inputs: dict[int, torch.Tensor] = {}
        handles = []
        for index, layer in enumerate(model.model.layers):
            gate = getattr(layer.mlp, "gate", None)
            if gate is None:
                continue

            def make(index):
                def hook(module, inputs, output):
                    captured_inputs[index] = inputs[0].detach().clone()

                return hook

            handles.append(gate.register_forward_hook(make(index)))
        try:
            _, step = _record(model, input_ids, forward=lambda ids: model(input_ids=ids))
        finally:
            for handle in handles:
                handle.remove()

        for position, layer_index in enumerate((0, 2)):
            gate = model.model.layers[layer_index].mlp.gate
            with torch.inference_mode():
                logits, weights, ids = gate(captured_inputs[layer_index])
            self.assertTrue(torch.equal(step.top_k_ids[position].long(), ids.cpu()))
            self.assertTrue(torch.allclose(step.top_k_weights[position], weights.cpu()))
            self.assertTrue(torch.allclose(step.router_logits[position], logits.cpu()))

    def test_router_metadata_is_measured_not_assumed(self) -> None:
        metadata = describe_model_routers(_tiny_qwen3_moe())
        self.assertEqual(metadata["router_adapter"], "qwen3_moe")
        self.assertEqual(metadata["num_experts"], 6)
        self.assertEqual(metadata["top_k"], 2)
        self.assertEqual(metadata["num_moe_layers"], 2)
        self.assertEqual(metadata["num_hidden_layers"], 3)
        self.assertEqual(metadata["moe_layer_indices"], [0, 2])
        self.assertFalse(metadata["router_has_bias"])


class GptOssAdapterTest(unittest.TestCase):
    def test_adapter_is_selected_from_model_type(self) -> None:
        self.assertIsInstance(resolve_router_adapter(_tiny_gpt_oss()), GptOssRouterAdapter)

    def test_capture_matches_the_router_modules_own_outputs(self) -> None:
        model = _tiny_gpt_oss()
        input_ids = torch.tensor([[1, 2, 3, 4]])
        captured_inputs: dict[int, torch.Tensor] = {}
        handles = []
        for index, layer in enumerate(model.model.layers):

            def make(index):
                def hook(module, inputs, output):
                    captured_inputs[index] = inputs[0].detach().clone()

                return hook

            handles.append(layer.mlp.register_forward_hook(make(index)))
        try:
            recorder, step = _record(model, input_ids, forward=lambda ids: model(input_ids=ids))
        finally:
            for handle in handles:
                handle.remove()

        self.assertEqual(recorder.moe_layer_indices, (0, 1))
        self.assertEqual(step.router_logits.shape, (2, 4, 5))
        self.assertEqual(step.top_k_ids.shape, (2, 4, 2))
        for layer_index, layer in enumerate(model.model.layers):
            rows = captured_inputs[layer_index].reshape(-1, layer.mlp.router.hidden_dim)
            with torch.inference_mode():
                logits, scores, ids = layer.mlp.router(rows)
            self.assertTrue(torch.allclose(step.router_logits[layer_index], logits.cpu(), atol=1e-6))
            self.assertTrue(torch.equal(step.top_k_ids[layer_index].long(), ids.cpu()))
            self.assertTrue(torch.allclose(step.top_k_weights[layer_index], scores.cpu(), atol=1e-6))

    def test_capture_survives_the_mxfp4_forward_replacement(self) -> None:
        """MXFP4 replaces GptOssMLP.forward and never calls the router submodule."""

        model = _tiny_gpt_oss()
        input_ids = torch.tensor([[1, 2, 3, 4]])
        _, eager_step = _record(model, input_ids, forward=lambda ids: model(input_ids=ids))

        def mxfp4_like_forward(self, hidden_states):
            batch_size = hidden_states.shape[0]
            rows = hidden_states.reshape(-1, self.router.hidden_dim)
            router_logits = torch.nn.functional.linear(rows, self.router.weight, self.router.bias)
            values, ids = torch.topk(router_logits, self.router.top_k, dim=-1)
            scores = torch.softmax(values, dim=-1)
            routed = self.experts(rows, ids, scores).reshape(batch_size, -1, self.router.hidden_dim)
            return routed, router_logits

        for layer in model.model.layers:
            layer.mlp.forward = MethodType(mxfp4_like_forward, layer.mlp)
        _, fused_step = _record(model, input_ids, forward=lambda ids: model(input_ids=ids))

        self.assertTrue(torch.allclose(fused_step.router_logits, eager_step.router_logits, atol=1e-6))
        self.assertTrue(torch.equal(fused_step.top_k_ids, eager_step.top_k_ids))
        self.assertTrue(torch.allclose(fused_step.top_k_weights, eager_step.top_k_weights, atol=1e-6))

    def test_fused_moe_kernels_are_refused(self) -> None:
        model = _tiny_gpt_oss()
        model._use_kernels = True
        with self.assertRaisesRegex(TypeError, "use_kernels=False"):
            RouterTraceRecorder(model)

    def test_router_metadata_is_measured_not_assumed(self) -> None:
        metadata = describe_model_routers(_tiny_gpt_oss())
        self.assertEqual(metadata["router_adapter"], "gpt_oss")
        self.assertEqual(metadata["num_experts"], 5)
        self.assertEqual(metadata["top_k"], 2)
        self.assertEqual(metadata["num_moe_layers"], 2)
        self.assertEqual(metadata["capture_module"], "mlp")
        self.assertTrue(metadata["router_has_bias"])
        self.assertEqual(metadata["shared_expert_count"], 0)
        self.assertEqual(
            metadata["top_k_weight_semantics"], "softmax_over_selected_logits_only"
        )


class TraceRoundTripTest(unittest.TestCase):
    """A written trace must validate under each architecture's weight semantics."""

    def _round_trip(self, model, input_ids) -> dict[str, object]:
        metadata = {"trace_id": "pilot-round-trip", "router": describe_model_routers(model)}
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "trace"
            with ShardedTraceWriter(output_dir, metadata) as writer:
                with RouterTraceRecorder(model, sink=writer.write_step, retain_steps=False) as recorder:
                    token_count = int(input_ids.numel())
                    with recorder.record_step(
                        phase="prefill",
                        input_ids=input_ids,
                        positions=range(token_count),
                        token_texts=tuple(str(value) for value in input_ids.reshape(-1).tolist()),
                        token_roles=("user",) * token_count,
                        conversation_turns=(1,) * token_count,
                        agent_steps=(0,) * token_count,
                    ):
                        with torch.inference_mode():
                            model(input_ids=input_ids)
                writer.finalize({"test": True})
            return validate_trace(output_dir)

    def test_qwen3_moe_trace_validates(self) -> None:
        report = self._round_trip(_tiny_qwen3_moe(), torch.tensor([[1, 2, 3, 4]]))
        self.assertTrue(report["passed"])
        self.assertEqual(report["token_count"], 4)

    def test_gpt_oss_trace_validates(self) -> None:
        report = self._round_trip(_tiny_gpt_oss(), torch.tensor([[1, 2, 3, 4]]))
        self.assertTrue(report["passed"])
        self.assertEqual(report["token_count"], 4)

    def test_weight_semantics_are_derived_from_the_router(self) -> None:
        qwen = _tiny_qwen3_moe()
        gates = [
            layer.mlp.gate for layer in qwen.model.layers if hasattr(layer.mlp, "gate")
        ]
        for gate in gates:
            gate.norm_topk_prob = False
        self.assertEqual(
            describe_model_routers(qwen)["top_k_weight_semantics"],
            "softmax_probability_over_all_experts",
        )
        for gate in gates:
            gate.norm_topk_prob = True
        self.assertEqual(
            describe_model_routers(qwen)["top_k_weight_semantics"],
            "renormalised_softmax_probability_over_top_k",
        )
        # Qwen3-30B-A3B ships norm_topk_prob=true, so the renormalised trace
        # must validate too.
        report = self._round_trip(qwen, torch.tensor([[1, 2, 3]]))
        self.assertTrue(report["passed"])


if __name__ == "__main__":
    unittest.main()
