from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import torch
from safetensors.torch import load_file


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from routing import RouterTraceRecorder, ShardedTraceWriter, validate_trace  # noqa: E402


class _FakeGate(torch.nn.Module):
    def __init__(self, layer_index: int) -> None:
        super().__init__()
        self.num_experts = 4
        self.top_k = 2
        self.weight = torch.nn.Parameter(
            torch.arange(12, dtype=torch.float32).reshape(4, 3) + layer_index
        )

    def forward(self, hidden_states: torch.Tensor):
        logits = torch.nn.functional.linear(hidden_states.reshape(-1, 3), self.weight)
        probabilities = torch.softmax(logits, dtype=torch.float32, dim=-1)
        weights, expert_ids = torch.topk(probabilities, self.top_k, dim=-1)
        return logits, weights, expert_ids


class _FakeMlp(torch.nn.Module):
    def __init__(self, layer_index: int) -> None:
        super().__init__()
        self.gate = _FakeGate(layer_index)


class _FakeLayer(torch.nn.Module):
    def __init__(self, layer_index: int) -> None:
        super().__init__()
        self.mlp = _FakeMlp(layer_index)


class _FakeBase(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.layers = torch.nn.ModuleList([_FakeLayer(0), _FakeLayer(1)])


class _FakeCausalLm(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.model = _FakeBase()

    def forward(self, input_ids: torch.Tensor) -> None:
        hidden = input_ids.float().unsqueeze(-1).repeat(1, 1, 3)
        for layer in self.model.layers:
            layer.mlp.gate(hidden)


class RoutingCaptureTest(unittest.TestCase):
    def test_capture_aligns_layers_tokens_and_top_k(self) -> None:
        model = _FakeCausalLm()
        input_ids = torch.tensor([[2, 5, 7]])

        with RouterTraceRecorder(model) as recorder:
            with recorder.record_step(
                phase="prefill",
                input_ids=input_ids,
                positions=(0, 1, 2),
                token_texts=("a", "b", "c"),
                token_roles=("system", "user", "assistant"),
                conversation_turns=(0, 1, 1),
                agent_steps=(0, 0, 0),
            ):
                model(input_ids)

        self.assertEqual(len(recorder.steps), 1)
        step = recorder.steps[0]
        self.assertEqual(step.router_logits.shape, (2, 3, 4))
        self.assertEqual(step.top_k_ids.shape, (2, 3, 2))
        self.assertEqual(step.top_k_weights.shape, (2, 3, 2))
        self.assertEqual(step.router_entropy.shape, (2, 3))
        self.assertEqual(step.router_margin.shape, (2, 3))
        self.assertEqual(step.effective_experts.shape, (2, 3))
        expected_ids = torch.topk(torch.softmax(step.router_logits.float(), dim=-1), 2, dim=-1).indices
        self.assertTrue(torch.equal(step.top_k_ids.long(), expected_ids))
        self.assertEqual(step.metadata.token_ids, (2, 5, 7))

    def test_stream_writer_emits_independent_step_shards(self) -> None:
        model = _FakeCausalLm()
        received_indices: list[int] = []
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "trace"
            with ShardedTraceWriter(output_dir, {"trace_id": "unit-test"}) as writer:
                def sink(step):
                    received_indices.append(step.metadata.step_index)
                    writer.write_step(step)

                with RouterTraceRecorder(model, sink=sink, retain_steps=False) as recorder:
                    for token_id, position in ((3, 0), (4, 1)):
                        input_ids = torch.tensor([[token_id]])
                        with recorder.record_step(
                            phase="decode",
                            input_ids=input_ids,
                            positions=(position,),
                            token_texts=(str(token_id),),
                            token_roles=("assistant",),
                            conversation_turns=(1,),
                            agent_steps=(0,),
                        ):
                            model(input_ids)
                writer.finalize(
                    {"test": True},
                    metadata_updates={"events": [{"kind": "unit_test"}]},
                )

            self.assertEqual(received_indices, [0, 1])
            manifest_rows = [
                json.loads(line)
                for line in (output_dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([row["step_index"] for row in manifest_rows], [0, 1])
            trace = json.loads((output_dir / "trace.json").read_text(encoding="utf-8"))
            self.assertTrue(trace["complete"])
            self.assertEqual(trace["step_count"], 2)
            self.assertEqual(trace["events"], [{"kind": "unit_test"}])
            first = load_file(output_dir / manifest_rows[0]["tensor_file"])
            self.assertEqual(first["router_logits"].shape, (2, 1, 4))
            self.assertTrue(torch.equal(first["token_ids"], torch.tensor([3])))
            validation = validate_trace(output_dir)
            self.assertTrue(validation["passed"])
            self.assertEqual(validation["token_count"], 2)

    def test_writer_rejects_reserved_final_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            writer = ShardedTraceWriter(Path(temporary) / "trace", {"trace_id": "reserved"})
            with self.assertRaisesRegex(ValueError, "reserved fields"):
                writer.finalize({"test": True}, metadata_updates={"complete": True})
            writer.close()

    def test_validator_resets_positions_at_each_prefill(self) -> None:
        model = _FakeCausalLm()
        with tempfile.TemporaryDirectory() as temporary:
            output_dir = Path(temporary) / "multi-turn-trace"
            with ShardedTraceWriter(output_dir, {"trace_id": "multi-turn"}) as writer:
                with RouterTraceRecorder(model, sink=writer.write_step, retain_steps=False) as recorder:
                    cases = (
                        ("prefill", (2, 3), (0, 1)),
                        ("decode", (4,), (2,)),
                        ("prefill", (2, 3, 4, 5), (0, 1, 2, 3)),
                        ("decode", (6,), (4,)),
                    )
                    for phase, token_ids, positions in cases:
                        input_ids = torch.tensor([token_ids])
                        with recorder.record_step(
                            phase=phase,
                            input_ids=input_ids,
                            positions=positions,
                            token_texts=tuple(str(value) for value in token_ids),
                            token_roles=tuple("assistant" for _ in token_ids),
                            conversation_turns=tuple(1 for _ in token_ids),
                            agent_steps=tuple(0 for _ in token_ids),
                        ):
                            model(input_ids)
                writer.finalize({"test": True})

            validation = validate_trace(output_dir)
            self.assertTrue(validation["passed"])
            self.assertEqual(validation["step_count"], 4)
            self.assertEqual(validation["token_count"], 8)


if __name__ == "__main__":
    unittest.main()
