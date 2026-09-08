"""Sharded safetensors writer for stream-readable routing traces."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import torch
from safetensors.torch import save_file

from .schema import RoutingStep


class ShardedTraceWriter:
    """Write one independently readable tensor shard per model forward."""

    def __init__(self, output_dir: Path, trace_metadata: dict[str, Any]) -> None:
        self.output_dir = output_dir.resolve()
        self.steps_dir = self.output_dir / "steps"
        self.output_dir.mkdir(parents=True, exist_ok=False)
        self.steps_dir.mkdir()
        self._trace_metadata = dict(trace_metadata)
        self._trace_metadata["schema_version"] = 3
        self._trace_metadata["complete"] = False
        self._write_json(self.output_dir / "trace.json", self._trace_metadata)
        self._manifest = (self.output_dir / "manifest.jsonl").open("x", encoding="utf-8")
        self._written_steps = 0

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(path)

    def write_step(self, step: RoutingStep) -> None:
        step.validate()
        if step.metadata.step_index != self._written_steps:
            raise ValueError(
                f"expected step {self._written_steps}, received {step.metadata.step_index}"
            )
        stem = f"{step.metadata.step_index:06d}_{step.metadata.phase}"
        destination = self.steps_dir / f"{stem}.safetensors"
        temporary = self.steps_dir / f".{stem}.tmp"
        tensors = {
            "token_ids": torch.tensor(step.metadata.token_ids, dtype=torch.int64),
            "positions": torch.tensor(step.metadata.positions, dtype=torch.int64),
            "router_logits": step.router_logits.contiguous(),
            "top_k_ids": step.top_k_ids.contiguous(),
            "top_k_weights": step.top_k_weights.contiguous(),
            "router_entropy": step.router_entropy.contiguous(),
            "router_margin": step.router_margin.contiguous(),
            "effective_experts": step.effective_experts.contiguous(),
        }
        save_file(
            tensors,
            temporary,
            metadata={
                "schema_version": "3",
                "step_index": str(step.metadata.step_index),
                "phase": step.metadata.phase,
            },
        )
        temporary.replace(destination)

        manifest_row = asdict(step.metadata)
        manifest_row.update(
            {
                "tensor_file": destination.relative_to(self.output_dir).as_posix(),
                "router_logits_shape": list(step.router_logits.shape),
                "top_k_shape": list(step.top_k_ids.shape),
                "router_logits_dtype": str(step.router_logits.dtype),
                "top_k_ids_dtype": str(step.top_k_ids.dtype),
                "top_k_weights_dtype": str(step.top_k_weights.dtype),
            }
        )
        self._manifest.write(json.dumps(manifest_row, ensure_ascii=False) + "\n")
        self._manifest.flush()
        self._written_steps += 1

    def finalize(
        self,
        summary: dict[str, Any],
        *,
        metadata_updates: dict[str, Any] | None = None,
    ) -> None:
        if metadata_updates:
            reserved = {"schema_version", "complete", "step_count", "summary"}
            overlap = reserved.intersection(metadata_updates)
            if overlap:
                raise ValueError(f"final metadata cannot replace reserved fields: {sorted(overlap)}")
            self._trace_metadata.update(metadata_updates)
        self._trace_metadata["complete"] = True
        self._trace_metadata["step_count"] = self._written_steps
        self._trace_metadata["summary"] = summary
        self._write_json(self.output_dir / "trace.json", self._trace_metadata)
        self.close()

    def close(self) -> None:
        if not self._manifest.closed:
            self._manifest.close()

    def __enter__(self) -> ShardedTraceWriter:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()
