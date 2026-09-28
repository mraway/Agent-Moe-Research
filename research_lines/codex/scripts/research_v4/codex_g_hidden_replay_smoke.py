"""Replay five fixed OPEN G-dev traces; never generate tokens or execute tools."""
from __future__ import annotations

import argparse
from collections import defaultdict
import fcntl
import gc
import hashlib
import importlib.metadata
import inspect
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

import torch
from safetensors.torch import load, load_file, save_file

from research_v4.codex_access_guard import CodexGAccessGuard
from routing import RouterTraceRecorder

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "artifacts/agent_v2/dataset_g"
OUT = ROOT / "artifacts/agent_v2/codex_g/hidden_replay_smoke_v1"
PLAN = ROOT / "docs/research_v4/codex_g_hidden_replay_smoke_plan.md"
LOCK = ROOT / "artifacts/agent_v2/gpu.lock"
REVISION = "6cee5e81ee83917806bbde320786a8fb61efebee"
KERNEL = Path("/home/wzh/.cache/huggingface/hub/kernels--kernels-community--gpt-oss-triton-kernels/snapshots/c039a37b84eb546e3e38277e011903560347024b")
SAMPLE_SPECS = (
    ("core_72_cells/g-dev-001/clean", "clean"),
    ("core_72_cells/g-dev-001/attack", "direct_user"),
    ("core_72_cells/g-dev-017/attack", "multi_turn_user"),
    ("resume_3_3/g-dev-097/attack", "tool_output"),
    ("resume_1_1/g-dev-305/clean", "legitimate_refusal"),
)
TRACE_DIRS = tuple(DATA / "g_dev" / name for name, _ in SAMPLE_SPECS)
HIDDEN_KEYS = ("router_input_hidden", "block_output_hidden", "final_norm_hidden")
ROUTE_KEYS = ("router_logits", "top_k_ids", "top_k_weights")
SOURCES = (
    PLAN, Path(__file__), ROOT / "tests/test_research_v4_codex_g_hidden_replay_smoke.py",
    ROOT / "scripts/research_v4/codex_access_guard.py", ROOT / "src/routing/capture.py",
    ROOT / "src/routing/schema.py", ROOT / "src/phase_a/generation.py",
    ROOT / "scripts/research_v4/run_agent_v3.py",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


class ReplayGuard(CodexGAccessGuard):
    """Python open audit + explicit native-reader checks; not OS isolation."""

    def check_path(self, path):
        p = super().check_path(path)
        if DATA == p or DATA in p.parents:
            if not any(r.resolve() == p or r.resolve() in p.parents for r in TRACE_DIRS):
                self.blocked_attempts += 1
                raise PermissionError("replay only permits its five fixed G-dev trace directories")
        own = OUT.parent.resolve()
        if own in p.parents and not (p == OUT.resolve() or OUT.resolve() in p.parents):
            self.blocked_attempts += 1
            raise PermissionError("replay cannot read other Codex experiment artifacts")
        return p

    def audit(self, event, args):
        super().audit(event, args)
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            p = self.check_path(args[0])
            flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC):
                if DATA in p.parents or (OUT.parent in p.parents and OUT not in p.parents):
                    self.blocked_attempts += 1
                    raise PermissionError("original traces and other artifacts are read-only")


class Delta:
    def __init__(self):
        self.n = self.unequal = 0
        self.bitwise_equal = True
        self.absolute = self.square = self.reference_square = self.maximum = 0.0

    def add(self, reference, replay):
        require(reference.shape == replay.shape, "comparison shape mismatch")
        require(torch.isfinite(reference).all() and torch.isfinite(replay).all(), "non-finite tensor")
        self.bitwise_equal &= reference.dtype == replay.dtype and torch.equal(
            reference.contiguous().view(torch.uint8), replay.contiguous().view(torch.uint8))
        a, b = reference.float(), replay.float()
        difference = b - a
        self.n += a.numel()
        self.unequal += int((a != b).sum())
        self.absolute += float(difference.abs().double().sum())
        self.square += float(difference.double().square().sum())
        self.reference_square += float(a.double().square().sum())
        self.maximum = max(self.maximum, float(difference.abs().max()))

    def result(self):
        return dict(elements=self.n, unequal_elements=self.unequal,
                    exact_element_fraction=1 - self.unequal / self.n if self.n else None,
                    bitwise_equal=self.bitwise_equal if self.n else None,
                    max_abs=self.maximum, mean_abs=self.absolute / self.n if self.n else None,
                    rmse=math.sqrt(self.square / self.n) if self.n else None,
                    relative_l2=math.sqrt(self.square / self.reference_square)
                    if self.reference_square else (0.0 if not self.square else None))


class RouteDelta:
    def __init__(self):
        self.logits, self.weights, self.probabilities = Delta(), Delta(), Delta()
        self.rows = self.same_set = self.same_order = self.tie_only = 0

    def add(self, old, new):
        a, b = old["router_logits"], new["router_logits"]
        self.logits.add(a, b)
        self.weights.add(old["top_k_weights"], new["top_k_weights"])
        self.probabilities.add(a.float().softmax(-1), b.float().softmax(-1))
        x, y = old["top_k_ids"].long(), new["top_k_ids"].long()
        require(x.shape == y.shape == (*a.shape[:2], 4), "top-k shape mismatch")
        same_set = (x.sort(-1).values == y.sort(-1).values).all(-1)
        same_order = (x == y).all(-1)
        same_a = (a.gather(-1, x).sort(-1).values == a.gather(-1, y).sort(-1).values).all(-1)
        same_b = (b.gather(-1, x).sort(-1).values == b.gather(-1, y).sort(-1).values).all(-1)
        self.rows += same_set.numel()
        self.same_set += int(same_set.sum())
        self.same_order += int(same_order.sum())
        self.tie_only += int((~same_set & same_a & same_b).sum())

    def result(self):
        return dict(logits=self.logits.result(), weights=self.weights.result(),
                    probabilities=self.probabilities.result(), layer_token_rows=self.rows,
                    same_set_rows=self.same_set, same_order_rows=self.same_order,
                    same_set_fraction=self.same_set / self.rows if self.rows else None,
                    changed_sets_explained_by_exact_ties=self.tie_only,
                    changed_sets_not_explained_by_exact_ties=self.rows - self.same_set - self.tie_only)

    def exact(self):
        return bool(self.rows and self.logits.bitwise_equal and self.weights.bitwise_equal
                    and self.same_order == self.rows)


def read_tensor(path, guard, hashes):
    path = guard.check_path(path)
    body = path.read_bytes()
    relative = str(path.relative_to(ROOT))
    actual = sha(body)
    require(relative not in hashes or hashes[relative] == actual, "source changed while reading")
    hashes[relative] = actual
    return load(body)


def load_sources(guard):
    samples, hashes = [], {}
    for directory, (_, role) in zip(TRACE_DIRS, SAMPLE_SPECS, strict=True):
        trace_path, manifest_path = directory / "trace.json", directory / "manifest.jsonl"
        trace = json.loads(guard.check_path(trace_path).read_text())
        rows = [json.loads(line) for line in guard.check_path(manifest_path).read_text().splitlines()]
        for path in (trace_path, manifest_path):
            hashes[str(path.relative_to(ROOT))] = digest(path)
        require(trace["complete"] is True, "incomplete trace")
        require(trace["model_revision"] == trace["tokenizer_revision"] == REVISION, "revision differs")
        require(trace["model_id"] == "openai/gpt-oss-20b", "model differs")
        require(trace["attention_implementation"] == "eager" and trace["dtype"] == "bfloat16", "backend differs")
        require(trace["quantization"]["method"] == "mxfp4", "quantization differs")
        events = [e for e in trace["events"] if e["kind"] == "model_generation"]
        require(len(events) == len(trace["token_axis"]["steps"]), "event/axis step mismatch")
        require([r["step_index"] for r in rows] == list(range(len(rows))), "manifest indices not contiguous")
        require(len(rows) == trace["step_count"], "manifest count differs")
        expected_indices, axis_by_row = [], {}
        for event in events:
            p, first = event["routing_step_index_prefill"], event["routing_step_index_first_decode"]
            require(first == p + 1, "prefill/decode gap")
            expected = event["output_token_ids"]
            require(len(expected) == event["output_token_count"], "output count mismatch")
            indices = [p] + list(range(first, first + len(expected)))
            expected_indices.extend(indices)
            for i in indices:
                axis_by_row[i] = event
            pre = rows[p]
            require(pre["phase"] == "prefill", "missing prefill")
            require(len(pre["token_ids"]) == event["prompt_token_count"], "prompt count mismatch")
            require(pre["positions"] == list(range(len(pre["token_ids"]))), "prefill positions mismatch")
            for offset, token in enumerate(expected):
                row = rows[first + offset]
                require(row["phase"] == "decode" and row["token_ids"] == [token], "decode token mismatch")
                require(row["positions"] == [event["prompt_token_count"] + offset], "decode position mismatch")
        require(expected_indices == list(range(len(rows))), "step partition does not cover manifest")
        for row in rows:
            tensor_path = guard.check_path(directory / row["tensor_file"])
            require(directory.resolve() in tensor_path.parents, "shard escaped its source trace")
            data = read_tensor(tensor_path, guard, hashes)
            require(data["token_ids"].tolist() == row["token_ids"], "disk token IDs differ")
            require(data["positions"].tolist() == row["positions"], "disk positions differ")
            require(data["router_logits"].shape == (24, len(row["token_ids"]), 32), "routing shape differs")
        samples.append(dict(directory=directory, trace=trace, rows=rows, axis=axis_by_row, role=role))
    require(sum(sum(len(e["output_token_ids"]) for e in s["trace"]["events"] if e["kind"] == "model_generation")
                for s in samples) == 760, "frozen sample token count changed")
    return samples, hashes


class HiddenCapture:
    """Read-only hooks. All tensors are native dtype CPU copies, last position only."""

    def __init__(self, model):
        self.model, self.handles = model, []
        self.current = {}

    def _capture(self, key, layer, value):
        require((key, layer) not in self.current, "hidden hook fired twice")
        require(value.ndim == 3 and value.shape[0] == 1, "hidden shape/batch differs")
        value = value[:, -1:, :].detach().to("cpu").clone()
        require(torch.isfinite(value).all(), "non-finite hidden")
        self.current[key, layer] = value

    def __enter__(self):
        for i, layer in enumerate(self.model.model.layers):
            def before(module, inputs, index=i):
                self._capture("router_input_hidden", index, inputs[0])

            def after(module, inputs, output, index=i):
                self._capture("block_output_hidden", index, output)

            self.handles.append(layer.mlp.register_forward_pre_hook(before))
            self.handles.append(layer.register_forward_hook(after))
        self.handles.append(self.model.model.norm.register_forward_hook(
            lambda module, inputs, output: self._capture("final_norm_hidden", 0, output)))
        return self

    def take(self):
        require(len(self.current) == 49, "not all 24+24+1 hidden hooks fired")
        result = {key: torch.cat([self.current[key, i] for i in range(1 if key == "final_norm_hidden" else 24)], dim=0)
                  for key in HIDDEN_KEYS}
        self.current = {}
        return result

    def __exit__(self, *args):
        for handle in self.handles:
            handle.remove()
        self.handles.clear()
        self.current.clear()


def replay_trace(model, sample, guard, hashes, pass_index):
    directory, trace, rows = sample["directory"], sample["trace"], sample["rows"]
    trace_out = OUT / trace["trace_id"]
    if pass_index == 1:
        trace_out.mkdir()
    repeated = {}
    if pass_index == 2:
        for phase in ("prefill", "decode"):
            repeated[phase] = load_file(str(guard.check_path(trace_out / f"{phase}.safetensors")), device="cpu")
    collected = {phase: defaultdict(list) for phase in ("prefill", "decode")}
    counters = dict(prefill=0, decode=0)
    routes = {phase: RouteDelta() for phase in counters}
    repetition = {phase: {key: Delta() for key in (*HIDDEN_KEYS, *ROUTE_KEYS)} for phase in counters}
    latest, forward_rows = [], []
    cache = None
    torch.manual_seed(int(trace["seed"]))
    torch.cuda.manual_seed_all(int(trace["seed"]))
    started = time.perf_counter()
    with HiddenCapture(model) as hidden, RouterTraceRecorder(
            model, sink=latest.append, retain_steps=False, router_adapter="gpt_oss") as recorder:
        require(recorder.router_metadata["router_hidden_dim"] == 2880, "hidden dimension differs")
        for row in rows:
            phase, index = row["phase"], row["step_index"]
            old = read_tensor(directory / row["tensor_file"], guard, hashes)
            require(recorder.next_step_index == index, "recorder index drift")
            if phase == "prefill":
                cache = None
            else:
                require(cache is not None and cache.get_seq_length() == row["positions"][0], "KV position drift")
            input_ids = old["token_ids"].unsqueeze(0).to(model.device)
            record_kwargs = {key: row[key] for key in (
                "phase", "positions", "token_texts", "token_roles", "conversation_turns", "agent_steps", "tool_boundaries")}
            forward_kwargs = dict(input_ids=input_ids, use_cache=True, logits_to_keep=1, return_dict=True)
            if phase == "decode":
                forward_kwargs["past_key_values"] = cache
            with recorder.record_step(input_ids=input_ids, **record_kwargs), torch.inference_mode():
                output = model(**forward_kwargs)
            cache = output.past_key_values
            del output, forward_kwargs, input_ids
            require(len(latest) == 1, "missing/repeated router capture")
            route = latest.pop()
            new = {key: getattr(route, key) for key in ROUTE_KEYS}
            routes[phase].add(old, new)
            tensor_values = hidden.take()
            tensor_values.update({key: value[:, -1:, :].contiguous() for key, value in new.items()})
            require(all(value.shape[-1] == 2880 for key, value in tensor_values.items() if key in HIDDEN_KEYS), "hidden width differs")
            event = sample["axis"][index]
            position_metadata = dict(token_ids=row["token_ids"][-1], positions=row["positions"][-1],
                                     routing_step_index=index, episode_index=event["episode_index"],
                                     agent_step=event["agent_step"])
            if pass_index == 1:
                for key, value in tensor_values.items():
                    collected[phase][key].append(value)
                for key, value in position_metadata.items():
                    collected[phase][key].append(torch.tensor([value], dtype=torch.int64))
            else:
                t = counters[phase]
                for key, value in tensor_values.items():
                    repetition[phase][key].add(repeated[phase][key][:, t:t+1, :], value)
                for key, value in position_metadata.items():
                    require(int(repeated[phase][key][t]) == value, "repeat axis drift")
            counters[phase] += 1
            forward_rows.append(dict(step_index=index, phase=phase, tokens=len(row["token_ids"]),
                                     logits_bitwise_equal=torch.equal(old["router_logits"].view(torch.uint8), new["router_logits"].view(torch.uint8))))
            if phase == "prefill" or counters["decode"] % 64 == 0:
                print(f"pass={pass_index} trace={trace['trace_id']} step={index} phase={phase} decoded={counters['decode']}", flush=True)
            del old, new, route, tensor_values
    del cache
    files = {}
    if pass_index == 1:
        for phase, tensors in collected.items():
            merged = {key: torch.cat(values, dim=0 if values[0].ndim == 1 else 1).contiguous()
                      for key, values in tensors.items()}
            path = guard.check_path(trace_out / f"{phase}.safetensors")
            require(not path.exists(), "refuse to overwrite replay tensors")
            save_file(merged, str(path), metadata={"schema": "codex-g-hidden-replay-smoke-1.0.0", "source_trace_id": trace["trace_id"],
                                                  "phase": phase, "prefill_scope": "last_position_only", "timing": "after_input_token_forward"})
            files[str(path.relative_to(ROOT))] = dict(sha256=digest(path), bytes=path.stat().st_size,
                                                      tensors={k: dict(shape=list(v.shape), dtype=str(v.dtype)) for k, v in merged.items()})
            del merged
    torch.cuda.synchronize()
    result = dict(trace_id=trace["trace_id"], source=str(directory.relative_to(ROOT)), role=sample["role"],
                  pass_index=pass_index, counts=counters, wall_seconds=time.perf_counter()-started,
                  old_routing={p: d.result() for p, d in routes.items()},
                  old_routing_exact=all(d.exact() for d in routes.values()), files=files,
                  repeat_at_saved_positions={p: {k: d.result() for k, d in ds.items()} for p, ds in repetition.items()} if pass_index == 2 else None,
                  repetition_exact=all(d.n and d.bitwise_equal for ds in repetition.values() for d in ds.values()) if pass_index == 2 else None,
                  forward_rows=forward_rows)
    write_json(trace_out / f"pass_{pass_index}.json", result)
    print(f"finished pass={pass_index} trace={trace['trace_id']} old_routing_exact={result['old_routing_exact']} repetition_exact={result['repetition_exact']} seconds={result['wall_seconds']:.2f}", flush=True)
    return result


def check_lock():
    with LOCK.open("rb") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # launch command must still be the approved parent flock
        fcntl.flock(stream, fcntl.LOCK_UN)
    raise RuntimeError("shared GPU lock is not held; launch with the documented flock command")


def nvidia_state():
    return subprocess.check_output(["nvidia-smi"], text=True)


def runtime_sources(model):
    from transformers.integrations import mxfp4
    objects = [type(model), type(model.model.layers[0]), mxfp4, mxfp4.triton_kernels_hub]
    result = {}
    for obj in objects:
        path = inspect.getfile(obj)
        result[path] = digest(path)
    for module in (mxfp4.triton_kernels_hub.routing, mxfp4.triton_kernels_hub.matmul_ogs):
        path = inspect.getfile(module)
        result[path] = digest(path)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="load model; requires parent flock")
    parser.add_argument("--plan-sha256", required=True)
    args = parser.parse_args()
    require(digest(PLAN) == args.plan_sha256, "plan hash mismatch")
    torch.set_num_threads(1)
    guard = ReplayGuard(ROOT)
    guard.install()
    samples, hashes = load_sources(guard)
    sources = {str(p.relative_to(ROOT)): digest(p) for p in SOURCES}
    preflight = dict(plan_sha256=args.plan_sha256, source_sha256=sources,
                     input_sha256=hashes, trace_count=len(samples), generation_steps=10, generated_tokens=760,
                     no_labels_opened=True, no_sealed_pool_opened=True,
                     versions={p: importlib.metadata.version(p) for p in ("torch", "transformers", "triton", "kernels", "safetensors", "accelerate")},
                     git_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                     tracked_diff=subprocess.check_output(["git", "diff", "HEAD", "--name-only"], cwd=ROOT, text=True).splitlines())
    kernel_files = sorted((KERNEL / "build/torch-cuda").rglob("*.py")) + [KERNEL / "build/torch-cuda/metadata.json"]
    require(len(kernel_files) > 20, "incomplete local kernel snapshot")
    preflight["kernel_source_sha256"] = {str(p): digest(p) for p in kernel_files}
    preflight["local_kernel_snapshot"] = str(KERNEL)
    print(json.dumps({k: v for k, v in preflight.items() if k not in ("input_sha256", "source_sha256", "kernel_source_sha256")}, indent=2), flush=True)
    if not args.run:
        print("CPU_PREFLIGHT_PASS", len(hashes), "original files hashed; no model loaded", flush=True)
        return 0
    check_lock()
    require(os.environ.get("LOCAL_KERNELS") == f"kernels-community/gpt-oss-triton-kernels={KERNEL}", "pin the local kernel snapshot in the launch environment")
    require(os.environ.get("HF_HUB_OFFLINE") == os.environ.get("TRANSFORMERS_OFFLINE") == "1", "offline mode required")
    require(not OUT.exists(), "output directory exists; refuse to overwrite")
    require(not preflight["tracked_diff"], "tracked worktree must be clean before smoke")
    OUT.mkdir()
    write_json(OUT / "preflight.json", preflight)
    started, model, runs = time.perf_counter(), None, []
    status = "FAILED"
    report = dict(scope="engineering_smoke_on_five_OPEN_G_dev_traces_not_detector_evidence", checkpoint_revision=REVISION,
                  old_hidden_states_were_not_saved=True, claim_old_hidden_bitwise_recovery=False,
                  no_free_generation=True, no_tool_execution=True, runs=runs)
    try:
        before = nvidia_state()
        print(before, flush=True)
        report["nvidia_before"] = before
        require(torch.cuda.is_available(), "CUDA unavailable")
        processes = subprocess.check_output(["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"], text=True).strip()
        other_pids = [line.strip() for line in processes.splitlines() if line.strip().isdigit() and int(line.strip()) != os.getpid()]
        require(not other_pids, f"another compute process is resident: {other_pids}")
        free, total = torch.cuda.mem_get_info()
        require(free >= 27 * 2**30, "less than 27 GiB free; do not risk overlapping residency/OOM")
        report["cuda_before"] = dict(free=free, total=total, name=torch.cuda.get_device_name(0), capability=torch.cuda.get_device_capability(0))
        from transformers import AutoModelForCausalLM, Mxfp4Config
        torch.cuda.reset_peak_memory_stats()
        load_started = time.perf_counter()
        model = AutoModelForCausalLM.from_pretrained(
            "openai/gpt-oss-20b", revision=REVISION, cache_dir=ROOT / "artifacts/hf_cache",
            local_files_only=True, dtype=torch.bfloat16, device_map="cuda", attn_implementation="eager",
            quantization_config=Mxfp4Config(dequantize=False, modules_to_not_convert=None))
        model.eval()
        torch.cuda.synchronize()
        require(not bool(getattr(model, "use_kernels", False)), "unexpected fused MoE path")
        quant = model.config.quantization_config
        require(not (quant.get("dequantize", False) if isinstance(quant, dict) else getattr(quant, "dequantize", False)), "MXFP4 dequantized fallback")
        require(all(type(layer.mlp.experts).__name__ == "Mxfp4GptOssExperts" for layer in model.model.layers), "native MXFP4 experts not present")
        report["model_load_seconds"] = time.perf_counter() - load_started
        report["runtime_source_sha256"] = runtime_sources(model)
        report["cuda_math"] = dict(matmul_precision=torch.get_float32_matmul_precision(),
                                    matmul_allow_tf32=torch.backends.cuda.matmul.allow_tf32,
                                    deterministic_algorithms=torch.are_deterministic_algorithms_enabled(),
                                    allocator_config=os.environ.get("PYTORCH_CUDA_ALLOC_CONF"), cpu_threads=torch.get_num_threads())
        print(f"model_loaded seconds={report['model_load_seconds']:.2f}", flush=True)
        for pass_index in (1, 2):
            for sample in samples:
                runs.append(replay_trace(model, sample, guard, hashes, pass_index))
        report["source_files_unchanged"] = all(digest(ROOT / p) == value for p, value in hashes.items())
        report["source_code_unchanged"] = all(digest(ROOT / p) == value for p, value in sources.items())
        report["kernel_source_unchanged"] = all(digest(p) == value for p, value in preflight["kernel_source_sha256"].items())
        require(report["source_files_unchanged"] and report["source_code_unchanged"] and report["kernel_source_unchanged"], "original input or frozen code changed")
        status = "EXACT_REPLAY_ON_SAMPLED_TRACES" if all(r["old_routing_exact"] and (r["pass_index"] == 1 or r["repetition_exact"]) for r in runs) else "NUMERICAL_DIFFERENCE_REQUIRES_REVIEW"
        report["cuda_peak_allocated_bytes"] = torch.cuda.max_memory_allocated()
        report["cuda_peak_reserved_bytes"] = torch.cuda.max_memory_reserved()
    except BaseException as exc:
        report["error"] = repr(exc)
        report["traceback"] = traceback.format_exc()
        raise
    finally:
        model = None
        gc.collect()
        if torch.cuda.is_initialized():
            torch.cuda.empty_cache()
        report["status"] = status
        report["wall_seconds"] = time.perf_counter() - started
        report["guard"] = guard.summary()
        report["nvidia_after_release"] = nvidia_state()
        write_json(OUT / "result.json", report)
        print(report["nvidia_after_release"], flush=True)
        print(json.dumps(dict(status=status, wall_seconds=report["wall_seconds"], result=str(OUT / "result.json"))), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
