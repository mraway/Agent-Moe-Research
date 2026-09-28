#!/usr/bin/env python3
"""Model-pilot smoke test: load one model config, generate a short reply, verify router capture.

Engineering feasibility only. Loads the model exactly the way ``run_agent_v2.py`` does,
generates ``--max-new-tokens`` tokens from the Atlas v2.5 system prompt plus a trivial
support request with a ``RouterTraceRecorder`` attached, and prints load time, resident
VRAM, tokens/s, the captured tensor shapes for one step, the router metadata, and a
per-MoE-layer check that the captured top-k ids equal the top-k of the captured logits.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from agent_v2 import load_agent_v2_definition  # noqa: E402
from phase_a import generate_routed_turn  # noqa: E402
from routing import RouterTraceRecorder  # noqa: E402

from run_agent_v2 import _channel_markers, _chat_template_kwargs, _quantization_config  # noqa: E402


def _nvidia_smi_mib() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return out.stdout.strip()
    except Exception as exc:  # pragma: no cover - diagnostics only
        return f"unavailable ({exc})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--agent-config", type=Path, default=ROOT / "configs" / "agent_v2_5_b2_support.json")
    parser.add_argument("--max-new-tokens", type=int, default=64)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    model_config: dict[str, Any] = json.loads(args.model_config.read_text(encoding="utf-8"))
    agent = load_agent_v2_definition(args.agent_config if args.agent_config.is_absolute() else ROOT / args.agent_config)

    report: dict[str, Any] = {"model_config": str(args.model_config), "model_id": model_config["model_id"],
                              "revision": model_config["revision"], "nvidia_smi_before": _nvidia_smi_mib()}
    print(f"[smoke] nvidia-smi before: {report['nvidia_smi_before']}")

    common = {"revision": model_config["revision"],
              "cache_dir": (ROOT / model_config["cache_dir"]).resolve(),
              "local_files_only": args.local_files_only}
    tokenizer = AutoTokenizer.from_pretrained(model_config["model_id"], **common)
    quantization_config = _quantization_config(model_config)
    load_kwargs: dict[str, Any] = {}
    if quantization_config is not None:
        load_kwargs["quantization_config"] = quantization_config
    torch.cuda.reset_peak_memory_stats()
    started = time.perf_counter()
    model = AutoModelForCausalLM.from_pretrained(
        model_config["model_id"], **common, dtype=torch.bfloat16,
        device_map=model_config["device_map"],
        attn_implementation=model_config["attn_implementation"], **load_kwargs,
    )
    model.eval()
    torch.cuda.synchronize()
    report["model_load_seconds"] = round(time.perf_counter() - started, 3)
    report["vram_after_load_allocated_gib"] = round(torch.cuda.memory_allocated() / 2**30, 3)
    report["vram_after_load_reserved_gib"] = round(torch.cuda.memory_reserved() / 2**30, 3)
    report["nvidia_smi_after_load"] = _nvidia_smi_mib()
    print(f"[smoke] load {report['model_load_seconds']}s  allocated {report['vram_after_load_allocated_gib']} GiB "
          f"reserved {report['vram_after_load_reserved_gib']} GiB  nvidia-smi {report['nvidia_smi_after_load']}")

    messages = [
        {"role": "system", "content": agent.system_prompt},
        {"role": "user", "content": "Hi, can you tell me what the return window is for a standard order?"},
    ]
    torch.manual_seed(int(model_config.get("seed", 1234)))
    with RouterTraceRecorder(model, router_adapter=model_config.get("router_adapter")) as recorder:
        report["router_metadata"] = recorder.router_metadata
        report["moe_layer_indices_head"] = list(recorder.moe_layer_indices[:8])
        print("[smoke] router metadata: " + json.dumps(recorder.router_metadata, ensure_ascii=False))
        gen_started = time.perf_counter()
        generated = generate_routed_turn(
            model=model, tokenizer=tokenizer, messages=messages, recorder=recorder,
            conversation_turn=0, agent_step=0, max_new_tokens=args.max_new_tokens,
            stop_on_complete_protocol_object=False, decoding_strategy="sample",
            temperature=0.8, top_p=0.9, assistant_protocol="json_action_or_text",
            chat_template_kwargs=_chat_template_kwargs(model_config),
            channel_markers=_channel_markers(model_config),
            extra_stop_token_ids=[int(v) for v in model_config.get("stop_token_ids", [])],
        )
        torch.cuda.synchronize()
        gen_seconds = time.perf_counter() - gen_started
        steps = list(recorder.steps)

    report["prompt_token_count"] = generated.prompt_token_count
    report["output_token_count"] = generated.output_token_count
    report["stop_reason"] = generated.stop_reason
    report["generate_seconds"] = round(gen_seconds, 3)
    report["tokens_per_second"] = round(generated.output_token_count / gen_seconds, 3) if gen_seconds else None
    report["vram_peak_allocated_gib"] = round(torch.cuda.max_memory_allocated() / 2**30, 3)
    report["nvidia_smi_after_generate"] = _nvidia_smi_mib()
    report["recorded_steps"] = len(steps)
    report["reply_text"] = generated.text

    decode_step = steps[1] if len(steps) > 1 else steps[0]
    report["captured_shapes_one_decode_step"] = {
        "router_logits": list(decode_step.router_logits.shape),
        "top_k_weights": list(decode_step.top_k_weights.shape),
        "top_k_ids": list(decode_step.top_k_ids.shape),
        "router_entropy": list(decode_step.router_entropy.shape),
        "dtypes": {"router_logits": str(decode_step.router_logits.dtype),
                   "top_k_weights": str(decode_step.top_k_weights.dtype),
                   "top_k_ids": str(decode_step.top_k_ids.dtype)},
    }
    report["captured_shapes_prefill_step"] = {
        "router_logits": list(steps[0].router_logits.shape),
        "top_k_ids": list(steps[0].top_k_ids.shape),
    }

    top_k = int(recorder.router_metadata["top_k"])
    mismatched: list[dict[str, Any]] = []
    checked_rows = 0
    id_disagreements = 0
    value_disagreements = 0
    tie_rows = 0
    for step in steps:
        logits = step.router_logits.float()
        ids = step.top_k_ids.long()
        reference = torch.topk(logits, top_k, dim=-1)
        ref_ids = reference.indices.sort(dim=-1).values
        got_ids = ids.sort(dim=-1).values
        row_id_bad = (ref_ids != got_ids).any(-1)
        # Ids can legitimately differ only where the k-th and (k+1)-th logits tie: the
        # module runs topk on the float32 softmax on GPU, this check runs topk on the
        # bf16 logits on CPU, and the two break exact ties differently. The capture is
        # still faithful iff the selected VALUES agree.
        ref_vals = reference.values.sort(dim=-1).values
        got_vals = torch.gather(logits, -1, ids).sort(dim=-1).values
        row_val_bad = (ref_vals != got_vals).any(-1)
        checked_rows += int(logits.shape[0] * logits.shape[1])
        id_disagreements += int(row_id_bad.sum())
        value_disagreements += int(row_val_bad.sum())
        tie_rows += int((row_id_bad & ~row_val_bad).sum())
        if bool(row_val_bad.any()):
            per_layer = row_val_bad.any(-1)
            mismatched.append({"step_index": step.metadata.step_index,
                               "layers": torch.nonzero(per_layer).reshape(-1).tolist()[:10]})
    report["topk_ids_equal_topk_of_logits"] = id_disagreements == 0
    report["topk_values_equal_topk_of_logits"] = value_disagreements == 0
    report["topk_check_rows"] = checked_rows
    report["topk_id_disagreement_rows"] = id_disagreements
    report["topk_value_disagreement_rows"] = value_disagreements
    report["topk_tie_only_rows"] = tie_rows
    report["topk_check_mismatches"] = mismatched[:5]

    print(f"[smoke] prompt {report['prompt_token_count']} tok, generated {report['output_token_count']} tok, "
          f"stop={report['stop_reason']}, {report['tokens_per_second']} tok/s, peak "
          f"{report['vram_peak_allocated_gib']} GiB, nvidia-smi {report['nvidia_smi_after_generate']}")
    print(f"[smoke] shapes one decode step: {json.dumps(report['captured_shapes_one_decode_step'])}")
    print(f"[smoke] prefill step shapes: {json.dumps(report['captured_shapes_prefill_step'])}")
    print(f"[smoke] top-k check over {checked_rows} (layer,token) rows: "
          f"ids_exact={report['topk_ids_equal_topk_of_logits']} "
          f"({id_disagreements} rows differ, {tie_rows} of them explained by exact logit ties) "
          f"values_exact={report['topk_values_equal_topk_of_logits']} "
          f"({value_disagreements} rows) mismatches={report['topk_check_mismatches']}")
    print("[smoke] reply text:\n" + generated.text)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
