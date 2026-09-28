#!/usr/bin/env python3
"""Engineering smoke for the gpt-oss-20b pilot load + router capture path.

Loads openai/gpt-oss-20b exactly as configs/pilot_gpt_oss_20b_mxfp4.json
describes, generates a short turn from the Atlas system prompt plus a trivial
support request with RouterTraceRecorder attached, and prints load time,
resident VRAM, tokens/s, captured tensor shapes, router metadata and a
top-k-vs-logits consistency check. Aborts if the MXFP4 quantizer falls back to
dequantize or if use_kernels is enabled. No detector statistic is computed.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import warnings
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from agent_v2 import load_agent_v2_definition  # noqa: E402
from phase_a import generate_routed_turn  # noqa: E402
from routing import RouterTraceRecorder  # noqa: E402

import run_agent_v2  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402


CAPTURED_LOGS: list[str] = []


class _Collector(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        CAPTURED_LOGS.append(f"{record.name}:{record.levelname}:{record.getMessage()}")


def main() -> int:
    model_config = json.loads(
        (ROOT / "configs/pilot_gpt_oss_20b_mxfp4.json").read_text(encoding="utf-8")
    )
    agent = load_agent_v2_definition(
        (ROOT / "configs/agent_v2_5_b2_support.json").resolve(), workspace_root=ROOT
    )

    handler = _Collector()
    logging.getLogger().addHandler(handler)
    logging.getLogger("transformers").setLevel(logging.INFO)
    logging.getLogger("transformers").addHandler(handler)

    common = {
        "revision": model_config["revision"],
        "cache_dir": (ROOT / model_config["cache_dir"]).resolve(),
        "local_files_only": True,
    }
    tokenizer = AutoTokenizer.from_pretrained(model_config["model_id"], **common)
    quantization_config = run_agent_v2._quantization_config(model_config)
    print("quantization_config:", repr(quantization_config), flush=True)

    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model = AutoModelForCausalLM.from_pretrained(
            model_config["model_id"],
            **common,
            dtype=torch.bfloat16,
            device_map=model_config["device_map"],
            attn_implementation=model_config["attn_implementation"],
            quantization_config=quantization_config,
        )
    model.eval()
    torch.cuda.synchronize()
    load_seconds = time.perf_counter() - started

    warning_texts = [str(item.message) for item in caught]
    all_logs = CAPTURED_LOGS + warning_texts
    dequant_hits = [
        text for text in all_logs
        if "dequant" in text.lower() and ("mxfp4" in text.lower() or "fall" in text.lower() or "not supported" in text.lower())
    ]
    print("=== load logs/warnings mentioning mxfp4/dequant/kernels ===", flush=True)
    for text in all_logs:
        low = text.lower()
        if any(key in low for key in ("mxfp4", "dequant", "kernel", "triton")):
            print("  LOG:", text[:400], flush=True)
    print("dequantize_fallback_hits:", json.dumps(dequant_hits)[:1000], flush=True)

    hf_quant = getattr(model.config, "quantization_config", None)
    print("model.config.quantization_config:", repr(hf_quant), flush=True)
    print("use_kernels:", getattr(model, "use_kernels", None), flush=True)
    if bool(getattr(model, "use_kernels", False)):
        print("ABORT: use_kernels is enabled", flush=True)
        return 3
    dequantize_flag = bool(getattr(hf_quant, "dequantize", False))
    print("quantization_config.dequantize:", dequantize_flag, flush=True)

    first_mlp = model.model.layers[0].mlp
    exp = first_mlp.experts
    print("experts class:", type(exp).__name__, flush=True)
    for name, param in list(exp.named_parameters()) + list(exp.named_buffers()):
        print(f"  experts.{name}: {tuple(param.shape)} {param.dtype} {param.device}", flush=True)
    print("router.weight:", tuple(first_mlp.router.weight.shape), first_mlp.router.weight.dtype, flush=True)

    weight_bytes = sum(
        p.numel() * p.element_size() for p in model.parameters()
    ) + sum(b.numel() * b.element_size() for b in model.buffers())
    print("param+buffer bytes on device:", weight_bytes, f"({weight_bytes/2**30:.2f} GiB)", flush=True)

    if dequantize_flag or dequant_hits:
        print("ABORT: MXFP4 dequantize fallback detected", flush=True)
        return 4

    print(f"load_seconds: {load_seconds:.2f}", flush=True)
    print(f"cuda_max_memory_allocated_MiB_after_load: {torch.cuda.max_memory_allocated()/2**20:.1f}", flush=True)

    messages = [
        {"role": "system", "content": agent.system_prompt},
        {
            "role": "user",
            "content": "Hi, can you tell me what your role is on this support desk in one sentence?",
        },
    ]

    torch.manual_seed(1234)
    torch.cuda.manual_seed_all(1234)
    torch.cuda.reset_peak_memory_stats()
    with RouterTraceRecorder(
        model, retain_steps=True, router_adapter=model_config.get("router_adapter")
    ) as recorder:
        print("=== router metadata ===", flush=True)
        print(json.dumps(recorder.router_metadata, indent=2), flush=True)
        print("moe_layer_indices:", list(recorder.moe_layer_indices), flush=True)
        gen_started = time.perf_counter()
        result = generate_routed_turn(
            model=model,
            tokenizer=tokenizer,
            messages=messages,
            recorder=recorder,
            conversation_turn=1,
            agent_step=0,
            max_new_tokens=64,
            stop_on_complete_protocol_object=False,
            decoding_strategy="sample",
            temperature=0.8,
            top_p=0.9,
            assistant_protocol="json_action_or_text",
            chat_template_kwargs=model_config.get("chat_template_kwargs"),
            channel_markers=run_agent_v2._channel_markers(model_config),
            extra_stop_token_ids=[int(v) for v in model_config.get("stop_token_ids", [])],
        )
        gen_seconds = time.perf_counter() - gen_started
        steps = list(recorder.steps)

    torch.cuda.synchronize()
    print(f"prompt_token_count: {result.prompt_token_count}", flush=True)
    print(f"output_token_count: {result.output_token_count}", flush=True)
    print(f"stop_reason: {result.stop_reason}", flush=True)
    print(f"generate_seconds: {gen_seconds:.3f}", flush=True)
    print(f"decode_tokens_per_second: {result.output_token_count/gen_seconds:.2f}", flush=True)
    print(f"channel_boundaries: {json.dumps(result.channel_boundaries)}", flush=True)
    print(f"cuda_max_memory_allocated_MiB_peak: {torch.cuda.max_memory_allocated()/2**20:.1f}", flush=True)
    print(f"cuda_max_memory_reserved_MiB_peak: {torch.cuda.max_memory_reserved()/2**20:.1f}", flush=True)

    print("=== raw text (special tokens kept) ===", flush=True)
    print(json.dumps(tokenizer.decode(result.output_token_ids, skip_special_tokens=False)), flush=True)
    print("=== result.text (skip_special_tokens=True, what the pipeline parses) ===", flush=True)
    print(json.dumps(result.text), flush=True)

    print(f"recorded_steps: {len(steps)} (1 prefill + {len(steps)-1} decode)", flush=True)
    for label, index in (("prefill", 0), ("first_decode", 1)):
        if index >= len(steps):
            continue
        step = steps[index]
        print(
            f"--- step[{index}] {label} phase={step.metadata.phase} rows={len(step.metadata.token_ids)} "
            f"logits={tuple(step.router_logits.shape)}{step.router_logits.dtype} "
            f"top_k_weights={tuple(step.top_k_weights.shape)}{step.top_k_weights.dtype} "
            f"top_k_ids={tuple(step.top_k_ids.shape)}{step.top_k_ids.dtype} "
            f"entropy={tuple(step.router_entropy.shape)} margin={tuple(step.router_margin.shape)} "
            f"effective_experts={tuple(step.effective_experts.shape)}",
            flush=True,
        )

    top_k = int(recorder.router_metadata["top_k"])
    mismatched_layers = []
    checked_rows = 0
    worst_weight_error = 0.0
    for step in steps:
        logits = step.router_logits.float()
        ids = step.top_k_ids.long()
        ref_vals, ref_ids = torch.topk(logits, top_k, dim=-1)
        gathered = torch.gather(logits, -1, ids)
        set_equal = (torch.sort(ids, dim=-1).values == torch.sort(ref_ids, dim=-1).values).all(dim=-1)
        value_equal = torch.isclose(torch.sort(gathered, dim=-1).values, torch.sort(ref_vals, dim=-1).values).all(dim=-1)
        ok = set_equal | value_equal
        checked_rows += int(ok.numel())
        bad = (~ok).nonzero()
        for layer_pos, row in bad.tolist():
            mismatched_layers.append((int(recorder.moe_layer_indices[layer_pos]), row))
        weight_sums = step.top_k_weights.float().sum(dim=-1)
        worst_weight_error = max(worst_weight_error, float((weight_sums - 1.0).abs().max()))
    print(f"worst_top_k_weight_sum_error: {worst_weight_error:.6f} (bfloat16 rounding; validator atol is 0.003)", flush=True)
    print(f"topk_consistency_checked_layer_token_rows: {checked_rows}", flush=True)
    print(f"topk_consistency_mismatches: {len(mismatched_layers)} {mismatched_layers[:10]}", flush=True)

    step = steps[1] if len(steps) > 1 else steps[0]
    print("sample first-decode layer0 top_k_ids:", step.top_k_ids[0, 0].tolist(), flush=True)
    print("sample first-decode layer0 top_k_weights:", [round(float(v), 4) for v in step.top_k_weights[0, 0]], flush=True)
    import subprocess

    print("=== nvidia-smi while resident ===", flush=True)
    print(
        subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv"],
            capture_output=True, text=True, check=False,
        ).stdout.strip(),
        flush=True,
    )
    print("SMOKE_OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
