#!/usr/bin/env python3
"""Capture and validate sharded prefill/decode routing traces from OLMoE."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from routing import RouterTraceRecorder, RoutingStep, ShardedTraceWriter  # noqa: E402


DEFAULT_CONFIG = ROOT / "configs" / "olmoe_p0.json"


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--max-new-tokens", type=int, default=16)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--local-files-only", action="store_true")
    return parser.parse_args()


def _token_pieces(tokenizer: Any, token_ids: torch.Tensor) -> tuple[str, ...]:
    return tuple(
        tokenizer.decode([int(token_id)], clean_up_tokenization_spaces=False)
        for token_id in token_ids.detach().cpu().reshape(-1).tolist()
    )


def _prompt_annotations(
    tokenizer: Any,
    messages: list[dict[str, str]],
    final_prompt_ids: torch.Tensor,
) -> tuple[tuple[str, ...], tuple[int, ...], tuple[int, ...], tuple[bool, ...]]:
    """Map chat-template tokens to their originating message without guessing."""

    final_ids = final_prompt_ids.detach().cpu().reshape(-1).tolist()
    roles: list[str] = []
    turns: list[int] = []
    previous_length = 0
    user_turn = 0
    for message_index, message in enumerate(messages):
        prefix = tokenizer.apply_chat_template(
            messages[: message_index + 1],
            add_generation_prompt=False,
            return_tensors="pt",
        )
        prefix_ids = prefix if isinstance(prefix, torch.Tensor) else prefix["input_ids"]
        prefix_values = prefix_ids.detach().cpu().reshape(-1).tolist()
        if final_ids[: len(prefix_values)] != prefix_values:
            raise ValueError("chat template prefixes are not token-stable; cannot assign prompt roles")
        if message["role"] == "user":
            user_turn += 1
        added = len(prefix_values) - previous_length
        roles.extend([message["role"]] * added)
        turns.extend([user_turn] * added)
        previous_length = len(prefix_values)

    if previous_length > len(final_ids):
        raise ValueError("message template is longer than the final generation prompt")
    generation_prefix = len(final_ids) - previous_length
    roles.extend(["assistant"] * generation_prefix)
    turns.extend([user_turn] * generation_prefix)
    if len(roles) != len(final_ids):
        raise AssertionError("prompt role annotation is not token-aligned")
    return (
        tuple(roles),
        tuple(turns),
        tuple(0 for _ in final_ids),
        tuple(False for _ in final_ids),
    )


def _trace_metadata(config: dict[str, Any], messages: list[dict[str, str]], repeat: int) -> dict[str, Any]:
    return {
        "trace_id": f"p1-smoke-{repeat:02d}",
        "created_at": datetime.now(UTC).isoformat(),
        "model_id": config["model_id"],
        "model_revision": config["revision"],
        "tokenizer_id": config["model_id"],
        "tokenizer_revision": config["revision"],
        "dtype": config["dtype"],
        "device": config["device_map"],
        "attention_implementation": config["attn_implementation"],
        "decoding": {"strategy": "greedy", "do_sample": False},
        "seed": config["seed"],
        "messages": messages,
        "task_id": "p1-delayed-order-smoke",
    }


def _capture_once(
    *,
    model: torch.nn.Module,
    tokenizer: Any,
    prompt_ids: torch.Tensor,
    config: dict[str, Any],
    messages: list[dict[str, str]],
    output_dir: Path,
    max_new_tokens: int,
    repeat: int,
    prompt_annotations: tuple[tuple[str, ...], tuple[int, ...], tuple[int, ...], tuple[bool, ...]],
) -> tuple[torch.Tensor, list[RoutingStep], dict[str, Any]]:
    torch.manual_seed(config["seed"])
    torch.cuda.manual_seed_all(config["seed"])
    torch.cuda.reset_peak_memory_stats()
    writer = ShardedTraceWriter(output_dir, _trace_metadata(config, messages, repeat))
    started = time.perf_counter()

    with writer, RouterTraceRecorder(model, sink=writer.write_step, retain_steps=True) as recorder:
        prompt_positions = range(prompt_ids.shape[1])
        prompt_roles, prompt_turns, prompt_agent_steps, prompt_tool_boundaries = prompt_annotations
        with recorder.record_step(
            phase="prefill",
            input_ids=prompt_ids,
            positions=prompt_positions,
            token_texts=_token_pieces(tokenizer, prompt_ids),
            token_roles=prompt_roles,
            conversation_turns=prompt_turns,
            agent_steps=prompt_agent_steps,
            tool_boundaries=prompt_tool_boundaries,
        ):
            with torch.inference_mode():
                output = model(
                    input_ids=prompt_ids,
                    use_cache=True,
                    logits_to_keep=1,
                    return_dict=True,
                )

        next_token = output.logits[:, -1, :].argmax(dim=-1, keepdim=True)
        generated: list[torch.Tensor] = []
        for decode_index in range(max_new_tokens):
            generated.append(next_token.detach().cpu())
            absolute_position = prompt_ids.shape[1] + decode_index
            with recorder.record_step(
                phase="decode",
                input_ids=next_token,
                positions=(absolute_position,),
                token_texts=_token_pieces(tokenizer, next_token),
                token_roles=("assistant",),
                conversation_turns=(1,),
                agent_steps=(0,),
                tool_boundaries=(False,),
            ):
                with torch.inference_mode():
                    output = model(
                        input_ids=next_token,
                        past_key_values=output.past_key_values,
                        use_cache=True,
                        logits_to_keep=1,
                        return_dict=True,
                    )
            if int(next_token.item()) == tokenizer.eos_token_id:
                break
            next_token = output.logits[:, -1, :].argmax(dim=-1, keepdim=True)

        generated_ids = torch.cat(generated, dim=1)
        torch.cuda.synchronize()
        summary = {
            "prompt_tokens": prompt_ids.shape[1],
            "generated_tokens": generated_ids.shape[1],
            "generated_token_ids": generated_ids[0].tolist(),
            "generated_text": tokenizer.decode(generated_ids[0], skip_special_tokens=True),
            "wall_seconds": round(time.perf_counter() - started, 6),
            "peak_cuda_allocated_mib": round(torch.cuda.max_memory_allocated() / 1024**2, 2),
            "peak_cuda_reserved_mib": round(torch.cuda.max_memory_reserved() / 1024**2, 2),
        }
        writer.finalize(summary)
        return generated_ids, recorder.steps, summary


def _compare_runs(
    generated_runs: list[torch.Tensor], trace_runs: list[list[RoutingStep]]
) -> dict[str, Any]:
    reference_tokens = generated_runs[0]
    token_repeatable = all(torch.equal(reference_tokens, tokens) for tokens in generated_runs[1:])
    reference_trace = trace_runs[0]
    structure_repeatable = all(len(reference_trace) == len(trace) for trace in trace_runs[1:])
    max_abs_router_diff = 0.0
    top_k_ids_repeatable = True
    top_k_weights_repeatable = True

    if structure_repeatable:
        for trace in trace_runs[1:]:
            for reference_step, step in zip(reference_trace, trace, strict=True):
                if reference_step.metadata.token_ids != step.metadata.token_ids:
                    token_repeatable = False
                difference = (reference_step.router_logits.float() - step.router_logits.float()).abs().max()
                max_abs_router_diff = max(max_abs_router_diff, float(difference.item()))
                top_k_ids_repeatable &= torch.equal(reference_step.top_k_ids, step.top_k_ids)
                top_k_weights_repeatable &= torch.equal(reference_step.top_k_weights, step.top_k_weights)

    passed = all(
        (
            token_repeatable,
            structure_repeatable,
            top_k_ids_repeatable,
            top_k_weights_repeatable,
            max_abs_router_diff <= 1e-6,
        )
    )
    return {
        "passed": passed,
        "token_repeatable": token_repeatable,
        "structure_repeatable": structure_repeatable,
        "top_k_ids_repeatable": top_k_ids_repeatable,
        "top_k_weights_repeatable": top_k_weights_repeatable,
        "max_abs_router_logit_diff": max_abs_router_diff,
        "router_logit_atol": 1e-6,
        "step_count": len(reference_trace),
    }


def main() -> int:
    args = _args()
    if args.max_new_tokens <= 0:
        raise ValueError("--max-new-tokens must be positive")
    if args.repeats < 2:
        raise ValueError("--repeats must be at least two for repeatability validation")

    config_path = args.config.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    cache_dir = (ROOT / config["cache_dir"]).resolve()
    common = {
        "revision": config["revision"],
        "cache_dir": cache_dir,
        "local_files_only": args.local_files_only,
    }
    tokenizer = AutoTokenizer.from_pretrained(config["model_id"], **common)
    load_started = time.perf_counter()
    model = AutoModelForCausalLM.from_pretrained(
        config["model_id"],
        **common,
        dtype=torch.bfloat16,
        device_map=config["device_map"],
        attn_implementation=config["attn_implementation"],
    )
    model.eval()
    torch.cuda.synchronize()
    model_load_seconds = time.perf_counter() - load_started

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
    encoded = tokenizer.apply_chat_template(messages, add_generation_prompt=True, return_tensors="pt")
    if isinstance(encoded, torch.Tensor):
        prompt_ids = encoded.to(model.device)
    else:
        prompt_ids = encoded["input_ids"].to(model.device)
    prompt_annotations = _prompt_annotations(tokenizer, messages, prompt_ids)

    run_root = args.output_dir
    if run_root is None:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_root = ROOT / "artifacts" / "routing_smoke" / timestamp
    run_root = run_root.resolve()
    run_root.mkdir(parents=True, exist_ok=False)

    generated_runs: list[torch.Tensor] = []
    trace_runs: list[list[RoutingStep]] = []
    summaries: list[dict[str, Any]] = []
    for repeat in range(args.repeats):
        generated, trace, summary = _capture_once(
            model=model,
            tokenizer=tokenizer,
            prompt_ids=prompt_ids,
            config=config,
            messages=messages,
            output_dir=run_root / f"repeat_{repeat:02d}",
            max_new_tokens=args.max_new_tokens,
            repeat=repeat,
            prompt_annotations=prompt_annotations,
        )
        generated_runs.append(generated)
        trace_runs.append(trace)
        summaries.append(summary)

    validation = _compare_runs(generated_runs, trace_runs)
    report = {
        "model_id": config["model_id"],
        "revision": config["revision"],
        "model_load_seconds": round(model_load_seconds, 6),
        "output_dir": str(run_root),
        "runs": summaries,
        "repeatability": validation,
    }
    (run_root / "validation.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if validation["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
