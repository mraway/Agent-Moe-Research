#!/usr/bin/env python3
"""Pinned OLMoE metadata and CUDA smoke test for milestone P0."""

from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "configs" / "olmoe_p0.json"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Download only the small config/tokenizer files; do not load weights.",
    )
    parser.add_argument(
        "--local-files-only",
        action="store_true",
        help="Disallow network access and use only the configured local cache.",
    )
    parser.add_argument("--report", type=Path, help="Optional JSON report path.")
    return parser.parse_args()


def _load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        config = json.load(handle)
    config["cache_dir"] = str((ROOT / config["cache_dir"]).resolve())
    return config


def _metadata(config: dict[str, Any], local_files_only: bool) -> tuple[Any, Any, dict[str, Any]]:
    from transformers import AutoConfig, AutoTokenizer

    common = {
        "revision": config["revision"],
        "cache_dir": config["cache_dir"],
        "local_files_only": local_files_only,
    }
    started = time.perf_counter()
    model_config = AutoConfig.from_pretrained(config["model_id"], **common)
    tokenizer = AutoTokenizer.from_pretrained(config["model_id"], **common)
    elapsed = time.perf_counter() - started
    summary = {
        "model_type": model_config.model_type,
        "architectures": model_config.architectures,
        "num_hidden_layers": model_config.num_hidden_layers,
        "num_experts": model_config.num_experts,
        "num_experts_per_tok": model_config.num_experts_per_tok,
        "hidden_size": model_config.hidden_size,
        "vocab_size": model_config.vocab_size,
        "tokenizer_class": tokenizer.__class__.__name__,
        "metadata_load_seconds": round(elapsed, 3),
    }
    return model_config, tokenizer, summary


def _gpu_smoke(config: dict[str, Any], tokenizer: Any, local_files_only: bool) -> dict[str, Any]:
    import torch
    from transformers import AutoModelForCausalLM

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available to PyTorch")
    if config["dtype"] != "bfloat16":
        raise ValueError(f"Unsupported P0 dtype: {config['dtype']}")

    torch.manual_seed(config["seed"])
    torch.cuda.manual_seed_all(config["seed"])
    torch.cuda.reset_peak_memory_stats()

    load_started = time.perf_counter()
    model = AutoModelForCausalLM.from_pretrained(
        config["model_id"],
        revision=config["revision"],
        cache_dir=config["cache_dir"],
        local_files_only=local_files_only,
        dtype=torch.bfloat16,
        device_map=config["device_map"],
        attn_implementation=config["attn_implementation"],
    )
    model.eval()
    torch.cuda.synchronize()
    load_seconds = time.perf_counter() - load_started

    messages = [
        {
            "role": "system",
            "content": "You are a concise customer-service assistant for an online store.",
        },
        {
            "role": "user",
            "content": "My order is delayed. Briefly tell me the first step to check its status.",
        },
    ]
    encoded = tokenizer.apply_chat_template(
        messages,
        add_generation_prompt=True,
        return_tensors="pt",
    )
    if isinstance(encoded, torch.Tensor):
        model_inputs = {"input_ids": encoded.to(model.device)}
    else:
        model_inputs = {name: tensor.to(model.device) for name, tensor in encoded.items()}
    input_ids = model_inputs["input_ids"]

    with torch.inference_mode():
        forward = model(
            **model_inputs,
            use_cache=False,
            output_router_logits=True,
            return_dict=True,
        )
    router_logits = forward.router_logits
    if router_logits is None:
        raise AssertionError("The model did not return router logits")

    generation_kwargs = {
        "do_sample": False,
        "max_new_tokens": config["max_new_tokens"],
        "use_cache": True,
        "pad_token_id": tokenizer.eos_token_id,
    }
    generations = []
    generation_seconds = []
    with torch.inference_mode():
        for _ in range(2):
            started = time.perf_counter()
            output_ids = model.generate(**model_inputs, **generation_kwargs)
            torch.cuda.synchronize()
            generation_seconds.append(round(time.perf_counter() - started, 3))
            generations.append(output_ids[:, input_ids.shape[1] :].cpu())

    repeatable = bool(torch.equal(generations[0], generations[1]))
    if not repeatable:
        raise AssertionError("Greedy generation was not token-identical across two runs")

    return {
        "torch_version": torch.__version__,
        "torch_cuda_version": torch.version.cuda,
        "device_name": torch.cuda.get_device_name(0),
        "device_capability": list(torch.cuda.get_device_capability(0)),
        "model_load_seconds": round(load_seconds, 3),
        "prompt_tokens": input_ids.shape[1],
        "generated_tokens": generations[0].shape[1],
        "generated_text": tokenizer.decode(generations[0][0], skip_special_tokens=True),
        "greedy_repeatable": repeatable,
        "router_tensor_count": len(router_logits),
        "router_shapes": [list(tensor.shape) for tensor in router_logits],
        "peak_cuda_allocated_mib": round(torch.cuda.max_memory_allocated() / 1024**2, 2),
        "peak_cuda_reserved_mib": round(torch.cuda.max_memory_reserved() / 1024**2, 2),
        "peak_process_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2),
        "generation_seconds": generation_seconds,
    }


def main() -> int:
    args = _args()
    config = _load_config(args.config.resolve())
    _, tokenizer, metadata = _metadata(config, args.local_files_only)
    report: dict[str, Any] = {
        "model_id": config["model_id"],
        "revision": config["revision"],
        "cache_dir": config["cache_dir"],
        "metadata": metadata,
    }
    if not args.metadata_only:
        report["gpu_smoke"] = _gpu_smoke(config, tokenizer, args.local_files_only)

    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    print(rendered)
    if args.report:
        report_path = args.report.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
