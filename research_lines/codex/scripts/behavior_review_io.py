"""Read-only, routing-value-free evidence extraction shared by behavior audits."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def visible_prefix_ends(tokenizer, ids: list[int], content: str) -> list[int]:
    """Generic partial-Unicode handling; positions refer to consumed y_t."""
    ends = []
    for count in range(1, len(ids) + 1):
        prefix = tokenizer.decode(ids[:count], skip_special_tokens=True, clean_up_tokenization_spaces=False)
        n = 0
        while n < min(len(prefix), len(content)) and prefix[n] == content[n]:
            n += 1
        if ends and n < ends[-1]:
            raise ValueError("Non-monotone visible prefix")
        ends.append(n)
    if not ends or ends[-1] != len(content) or tokenizer.decode(ids, skip_special_tokens=True, clean_up_tokenization_spaces=False) != content:
        raise ValueError("Output text/token mismatch")
    return ends


def extract_trace(*, root: Path, run_root: Path, scenario: dict, arm: str, tokenizer,
                  run_record: dict, model_context_limit: int, max_new_tokens: int) -> tuple[dict, dict]:
    """No outcomes or router tensors are exposed to the reviewer.

    Runner validation already covers router consistency. Here only token IDs
    are opened from safetensors; file hashes protect all remaining bytes.
    """
    from safetensors import safe_open
    trace_dir = (run_root / scenario["pair_group_id"] / arm).resolve()
    if run_root.resolve() not in trace_dir.parents:
        raise ValueError("Trace path escapes its run root")
    if Path(run_record["path"]).resolve() != trace_dir or not run_record["validation"]["passed"]:
        raise ValueError("Run record path/integrity mismatch")
    trace = read_json(trace_dir / "trace.json")
    if not trace["complete"]:
        raise ValueError("Trace is incomplete")
    generations = [e for e in trace["events"] if e["kind"] == "model_generation"]
    if len(generations) != 1:
        raise ValueError("Expected one final model response")
    event = generations[0]
    if event["prompt_token_count"] + max_new_tokens > model_context_limit:
        raise ValueError("Context limit violated")
    manifest = read_jsonl(trace_dir / "manifest.jsonl")
    manifest_ids, shard_ids, inventory = [], [], {}
    for index, step in enumerate(manifest):
        if step["step_index"] != index:
            raise ValueError("Manifest order mismatch")
        shard_path = (trace_dir / step["tensor_file"]).resolve()
        if trace_dir not in shard_path.parents:
            raise ValueError("Shard path escapes trace")
        if step["phase"] == "decode":
            manifest_ids.extend(step["token_ids"])
            with safe_open(shard_path, framework="pt", device="cpu") as shard:
                shard_ids.extend(shard.get_tensor("token_ids").tolist())
        inventory[shard_path.relative_to(root).as_posix()] = sha256(shard_path)
    ids = event["output_token_ids"]
    if ids != manifest_ids or ids != shard_ids or len(ids) != event["output_token_count"]:
        raise ValueError("Generation/manifest/shard token mismatch")
    if event["stop_reason"] == "eos":
        if ids[-1] != tokenizer.eos_token_id:
            raise ValueError("EOS metadata inconsistent")
    elif event["stop_reason"] == "length":
        if len(ids) != max_new_tokens:
            raise ValueError("Cap metadata inconsistent")
    else:
        raise ValueError("Unexpected generation stopping rule")
    tools = [e for e in trace["events"] if e["kind"] == "tool_result"]
    if len(tools) != len(scenario["task"]["requirements"]):
        raise ValueError("Required tool count mismatch")
    for tool, requirement in zip(tools, scenario["task"]["requirements"]):
        if tool["tool_name"] != requirement["action"] or not tool["base_result"]["ok"]:
            raise ValueError("Required tool flow mismatch")
    packet = {
        "authorized_request": scenario["task"]["user_request"],
        "model_visible_user_messages": [e["content"] for e in trace["events"] if e["kind"] == "user_message"],
        "tool_results": [{"tool": e["tool_name"], "verified_base_result": e["base_result"],
                          "model_visible_result": e["result"]} for e in tools],
        "output": event["content"], "output_token_ids": ids,
        "prefix_character_ends": visible_prefix_ends(tokenizer, ids, event["content"]),
        "output_token_count": len(ids), "visible_token_count": sum(i not in tokenizer.all_special_ids for i in ids),
        "stop_reason": event["stop_reason"], "right_censored": event["stop_reason"] == "length",
    }
    for name in ("trace.json", "manifest.jsonl"):
        inventory[(trace_dir / name).relative_to(root).as_posix()] = sha256(trace_dir / name)
    provenance = {"raw_hashes": inventory, "prompt_token_count": event["prompt_token_count"],
                  "rendered_prompt": event["rendered_prompt"],
                  "controller_messages": [e["content"] for e in trace["events"] if e["kind"] == "controller_message"],
                  "wall_seconds": trace["summary"]["wall_seconds"],
                  "peak_cuda_allocated_mib": trace["summary"]["peak_cuda_allocated_mib"]}
    return packet, provenance


def load_tokenizer(root: Path, model: dict):
    from transformers import AutoConfig, AutoTokenizer
    common = {"revision": model["revision"], "cache_dir": root / model["cache_dir"], "local_files_only": True}
    return (AutoTokenizer.from_pretrained(model["model_id"], **common),
            AutoConfig.from_pretrained(model["model_id"], **common).max_position_embeddings)


def check_hashes(root: Path, inventory: dict[str, str]) -> None:
    for path, expected in inventory.items():
        if sha256(root / path) != expected:
            raise ValueError(f"Frozen file changed: {path}")
