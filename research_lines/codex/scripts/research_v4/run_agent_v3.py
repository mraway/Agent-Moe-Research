#!/usr/bin/env python3
"""Run Agent v3 scenarios: model-driven harmony tool calls with routing traces.

The on-disk layout is the agent_v2 layout (``trace.json`` + ``manifest.jsonl`` +
``steps/*.safetensors``, schema version 3, ``routing.validate_trace`` clean), with
three additions in ``trace.json``:

``episodes``    one entry per user turn: steps, stop reason, channel totals;
``tool_events`` every model-issued call with its global episode token span,
                tool class, whether it executed and whether it is an X_tool
                (restricted) event;
``channels``    per-step harmony channel segments and pilot-compatible channel
                boundaries;
``token_axis``  per-step offsets that map an episode token index onto the
                routing shard that recorded it.

No routing statistic is computed here: routing tensors are written and then only
shape/integrity checked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from agent_v2 import apply_tool_result_injection, task_from_scenario  # noqa: E402
from agent_v3 import (  # noqa: E402
    ARM_NAMES,
    HARMONY_CHANNEL_MARKERS,
    AgentV3Session,
    AgentV3ToolController,
    HarmonySpecials,
    build_tool_call_chat_template,
    load_agent_v3_definition,
    user_turns,
    validate_agent_v3_experiment,
)
from routing import (  # noqa: E402
    RouterTraceRecorder,
    ShardedTraceWriter,
    describe_model_routers,
    validate_trace,
)

import run_agent_v2  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--arms", nargs="+", choices=ARM_NAMES)
    parser.add_argument(
        "--scenario",
        action="append",
        help="Optional base_task_id filter; repeat to select multiple scenarios.",
    )
    parser.add_argument("--limit", type=int, help="Stop after this many traces.")
    return parser.parse_args()


def _json_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _select_scenarios(config: dict[str, Any], selected: set[str]) -> list[dict[str, Any]]:
    scenarios = [
        scenario
        for scenario in config["scenarios"]
        if not selected or scenario["base_task_id"] in selected
    ]
    unknown = selected - {scenario["base_task_id"] for scenario in scenarios}
    if unknown:
        raise ValueError(f"unknown scenarios: {sorted(unknown)}")
    if not scenarios:
        raise ValueError("no scenarios selected")
    return scenarios


def _marker_diagnostics(scenario: dict[str, Any], analysis_text: str, final_text: str) -> dict[str, Any]:
    """Mechanical substring presence. Not a label: see model_pilot_report section 3.3."""

    required = [
        str(value).lower()
        for value in scenario["arms"]["attack"]["attack_goal"]["required_substrings"]
    ]
    markers = [str(value).lower() for value in scenario.get("manual_review_markers", [])]
    lowered_final = final_text.lower()
    lowered_analysis = analysis_text.lower()
    return {
        "label_source": "not_a_label_marker_matching_is_unreliable",
        "attack_goal_substrings": required,
        "all_goal_substrings_in_final": bool(required)
        and all(value in lowered_final for value in required),
        "any_goal_substring_in_final": any(value in lowered_final for value in required),
        "any_goal_substring_in_analysis": any(value in lowered_analysis for value in required),
        "any_review_marker_in_final": any(value in lowered_final for value in markers),
        "any_review_marker_in_analysis": any(value in lowered_analysis for value in markers),
    }


def _run_trace(
    *,
    model: Any,
    tokenizer: Any,
    model_config: dict[str, Any],
    experiment_config: dict[str, Any],
    agent_definition: Any,
    chat_template_kwargs: dict[str, Any],
    specials: HarmonySpecials,
    stop_token_ids: list[int],
    scenario: dict[str, Any],
    arm_name: str,
    output_dir: Path,
) -> dict[str, Any]:
    arm = scenario["arms"][arm_name]
    injection = arm.get("tool_result_injection")
    turns = user_turns(scenario, arm_name)
    task = task_from_scenario(scenario, arm_name)
    decoding = experiment_config["decoding"]
    trace_id = f"{scenario['base_task_id']}--{arm_name}"

    controller = AgentV3ToolController(
        environment=agent_definition.environment,
        tool_classes=agent_definition.tool_classes,
        tool_result_transform=(
            None
            if injection is None
            else (
                lambda action, result: apply_tool_result_injection(
                    action_name=action, result=result, injection=injection
                )
            )
        ),
    )

    requirements = [
        {
            "action": requirement.action,
            "arguments": dict(requirement.arguments),
            "expected_article_ids": list(requirement.expected_article_ids),
            "allow_empty_result": requirement.allow_empty_result,
            "query_hint": requirement.query_hint,
        }
        for requirement in task.requirements
    ]
    metadata: dict[str, Any] = {
        "trace_id": trace_id,
        "condition_id": f"agent-v3-{arm_name}",
        "pair_group_id": scenario["pair_group_id"],
        "analysis_group_id": scenario["analysis_group_id"],
        "base_task_id": scenario["base_task_id"],
        "split_group_id": scenario.get("split_group_id"),
        "preregistered_fold": scenario.get("preregistered_fold"),
        "routine_template_id": scenario.get("routine_template_id"),
        "domain_group": scenario.get("domain_group"),
        "wording_tier": scenario.get("wording_tier"),
        "probe_variant": scenario.get("probe_variant"),
        "experiment_id": experiment_config["experiment_id"],
        "dataset_role": experiment_config["dataset_role"],
        "phase": "agent_v3",
        "created_at": datetime.now(UTC).isoformat(),
        "model_id": model_config["model_id"],
        "model_revision": model_config["revision"],
        "tokenizer_id": model_config["model_id"],
        "tokenizer_revision": model_config["revision"],
        "dtype": model_config["dtype"],
        "device": model_config["device_map"],
        "attention_implementation": model_config["attn_implementation"],
        "quantization": model_config.get("quantization"),
        "seed": int(scenario["sampling_seed"]),
        "decoding": dict(decoding),
        "stop_token_ids": list(stop_token_ids),
        "agent_id": agent_definition.agent_id,
        "agent_definition_version": agent_definition.definition_version,
        "agent_control_mode": "model_planned_tools",
        "system_prompt_hash": _json_hash(agent_definition.system_prompt),
        "tool_schema_hash": _json_hash(list(agent_definition.tool_specs)),
        "chat_template_hash": _json_hash(chat_template_kwargs["chat_template"]),
        "chat_template_patched": True,
        "reasoning_effort": chat_template_kwargs.get("reasoning_effort"),
        "tool_classes": dict(agent_definition.tool_classes),
        "task_mandate": {
            "authorized_domain": agent_definition.authorized_domain,
            "authorized_goal": task.workflow_kind,
            "authorized_task_state": {
                "requirements": requirements,
                "completion_evidence": [
                    list(item) if isinstance(item, tuple) else item
                    for item in task.completion_evidence
                ],
            },
            "allowed_effects": list(agent_definition.tool_names),
            "forbidden_effects": list(agent_definition.restricted_tool_names),
            "user_turns": list(turns),
        },
        "router": describe_model_routers(model, model_config.get("router_adapter")),
        "perturbation": {
            "arm": arm_name,
            "channel": arm.get("channel", "none"),
            "attack_present": arm_name == "attack",
            "attack_family_id": arm.get("attack_family_id"),
            "attack_goal": arm.get("attack_goal"),
            "tool_result_injection": injection,
        },
        "policy_oracle": {
            "mode": "observe_and_record",
            "tool_execution": "sandboxed_read_only",
            "restricted_tools_executed": False,
            "rule": "read-only tools execute on demand; restricted stubs never execute",
        },
    }

    seed = int(scenario["sampling_seed"])
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.cuda.reset_peak_memory_stats()
    wall_started = time.perf_counter()

    events: list[dict[str, Any]] = []

    def _sink(row: dict[str, Any]) -> None:
        row = dict(row)
        row["event_index"] = len(events)
        events.append(row)

    _sink(
        {
            "kind": "system_message",
            "actor": "system",
            "logical_role": "system",
            "source_trust": "trusted",
            "model_visible": True,
            "user_visible": False,
            "episode_index": -1,
            "conversation_turn": 0,
            "agent_step": 0,
            "content": agent_definition.system_prompt,
        }
    )

    with ShardedTraceWriter(output_dir, metadata) as writer:
        with RouterTraceRecorder(
            model,
            sink=writer.write_step,
            retain_steps=False,
            router_adapter=model_config.get("router_adapter"),
        ) as recorder:
            session = AgentV3Session(
                model=model,
                tokenizer=tokenizer,
                recorder=recorder,
                controller=controller,
                system_prompt=agent_definition.system_prompt,
                chat_template_kwargs=chat_template_kwargs,
                max_agent_steps=min(
                    int(decoding["max_agent_steps"]), agent_definition.max_agent_steps
                ),
                max_new_tokens=int(decoding["max_new_tokens_per_turn"]),
                decoding_strategy=str(decoding["strategy"]),
                temperature=float(decoding.get("temperature", 1.0)),
                top_p=float(decoding.get("top_p", 1.0)),
                stop_token_ids=stop_token_ids,
                specials=specials,
                event_sink=_sink,
            )
            for turn in turns:
                session.run_user_turn(turn)

        torch.cuda.synchronize()

        episodes = session.episodes
        tool_events = [event.as_dict() for episode in episodes for event in episode.tool_events]
        channel_totals: dict[str, int] = {}
        for episode in episodes:
            for channel, count in episode.channel_token_counts.items():
                channel_totals[channel] = channel_totals.get(channel, 0) + count
        analysis_text = "\n".join(
            segment["text"]
            for episode in episodes
            for step in episode.steps
            for segment in step.channel_segments
            if segment["channel"] == "analysis"
        )
        final_text = "\n".join(episode.final_text for episode in episodes)

        outcome = {
            "episode_count": len(episodes),
            "step_count": sum(episode.step_count for episode in episodes),
            "steps_per_episode": [episode.step_count for episode in episodes],
            "episode_stop_reasons": [episode.stop_reason for episode in episodes],
            "generated_token_count": sum(episode.generated_token_count for episode in episodes),
            "generated_tokens_per_episode": [
                episode.generated_token_count for episode in episodes
            ],
            "channel_token_counts": channel_totals,
            "tool_call_count": len(tool_events),
            "tool_call_names": [event["tool_name"] for event in tool_events],
            "read_only_call_count": sum(
                1 for event in tool_events if event["tool_class"] == "read_only"
            ),
            "escalation_call_count": sum(
                1 for event in tool_events if event["tool_class"] == "escalation"
            ),
            "restricted_call_count": sum(1 for event in tool_events if event["restricted"]),
            "restricted_call_names": [
                event["tool_name"] for event in tool_events if event["restricted"]
            ],
            "x_tool_event_count": sum(1 for event in tool_events if event["x_tool"]),
            "malformed_call_count": sum(1 for event in tool_events if event["malformed"]),
            "malformed_call_errors": [
                event["error"] for event in tool_events if event["malformed"]
            ],
            "unknown_tool_call_count": sum(
                1 for event in tool_events if event["tool_class"] == "unknown"
            ),
            "injection_applied_count": sum(
                1 for event in tool_events if event["injection_applied"]
            ),
            "spontaneous_restricted_call": arm_name != "attack"
            and any(event["restricted"] for event in tool_events),
            "final_message_count": sum(
                1 for episode in episodes if episode.stop_reason == "final_channel"
            ),
            "marker_diagnostics": _marker_diagnostics(scenario, analysis_text, final_text),
        }
        summary = {
            "wall_seconds": round(time.perf_counter() - wall_started, 6),
            "agent_steps": outcome["step_count"],
            "generated_token_count": outcome["generated_token_count"],
            "peak_cuda_allocated_mib": round(torch.cuda.max_memory_allocated() / 1024**2, 2),
            "peak_cuda_reserved_mib": round(torch.cuda.max_memory_reserved() / 1024**2, 2),
        }
        writer.finalize(
            summary,
            metadata_updates={
                "events": events,
                "outcome": outcome,
                "episodes": [
                    {
                        "episode_index": episode.episode_index,
                        "conversation_turn": episode.conversation_turn,
                        "user_request": episode.user_request,
                        "stop_reason": episode.stop_reason,
                        "step_count": episode.step_count,
                        "generated_token_count": episode.generated_token_count,
                        "session_token_offset": episode.session_token_offset,
                        "channel_token_counts": episode.channel_token_counts,
                        "final_text": episode.final_text,
                        "wall_seconds": episode.wall_seconds,
                        "steps": [
                            step.as_dict(include_token_ids=False) for step in episode.steps
                        ],
                    }
                    for episode in episodes
                ],
                "tool_events": tool_events,
                "channels": {
                    "markers": dict(HARMONY_CHANNEL_MARKERS),
                    "boundary_semantics": (
                        "channel_boundaries: first generated-token index at which the "
                        "marker completes (body starts at index + 1), pilot-compatible. "
                        "channel_segments: exact half-open token spans per channel "
                        "message, with global_* fields on the episode token axis."
                    ),
                    "totals": channel_totals,
                },
                "token_axis": {
                    "definition": (
                        "Episode token g of agent step s lives in routing shard "
                        "routing_step_index_first_decode + (g - global_token_offset); "
                        "the step's prompt is shard routing_step_index_prefill."
                    ),
                    "steps": [
                        {
                            "episode_index": step.episode_index,
                            "agent_step": step.agent_step,
                            "conversation_turn": step.conversation_turn,
                            "prompt_token_count": step.prompt_token_count,
                            "output_token_count": step.output_token_count,
                            "global_token_offset": step.global_token_offset,
                            "session_token_offset": step.session_token_offset,
                            "routing_step_index_prefill": step.routing_step_index_prefill,
                            "routing_step_index_first_decode": step.routing_step_index_first_decode,
                        }
                        for episode in episodes
                        for step in episode.steps
                    ],
                },
            },
        )

    validation = validate_trace(output_dir)
    return {
        "trace_id": trace_id,
        "path": str(output_dir),
        "arm": arm_name,
        "outcome": outcome,
        "summary": summary,
        "validation": validation,
    }


def main() -> int:
    args = _args()
    config_path = args.config.resolve()
    experiment_config = json.loads(config_path.read_text(encoding="utf-8"))
    validate_agent_v3_experiment(experiment_config)
    arms = args.arms or experiment_config.get("arms") or list(ARM_NAMES)
    scenarios = _select_scenarios(experiment_config, set(args.scenario or []))

    agent_config_path = (ROOT / experiment_config["agent_config"]).resolve()
    model_config_path = (ROOT / experiment_config["model_config"]).resolve()
    agent_definition = load_agent_v3_definition(agent_config_path, workspace_root=ROOT)
    agent_config = json.loads(agent_config_path.read_text(encoding="utf-8"))
    model_config = json.loads(model_config_path.read_text(encoding="utf-8"))

    validation_report = {
        "experiment_id": experiment_config["experiment_id"],
        "dataset_role": experiment_config["dataset_role"],
        "config_hash": _json_hash(experiment_config),
        "agent_id": agent_definition.agent_id,
        "agent_definition_version": agent_definition.definition_version,
        "agent_config_hash": _file_hash(agent_config_path),
        "knowledge_base_hash": _file_hash(agent_definition.knowledge_base_path),
        "support_records_hash": _file_hash(agent_definition.support_records_path),
        "scenario_count": len(scenarios),
        "trace_count": len(scenarios) * len(arms),
        "arms": list(arms),
        "tool_classes": dict(agent_definition.tool_classes),
    }
    if args.validate_only:
        print(json.dumps(validation_report, indent=2, ensure_ascii=False))
        return 0

    cache_dir = (ROOT / model_config["cache_dir"]).resolve()
    common = {
        "revision": model_config["revision"],
        "cache_dir": cache_dir,
        "local_files_only": args.local_files_only,
    }
    tokenizer = AutoTokenizer.from_pretrained(model_config["model_id"], **common)
    specials = HarmonySpecials.from_tokenizer(tokenizer)
    stop_token_ids = sorted(
        {int(value) for value in model_config.get("stop_token_ids", [])} | {specials.call}
    )
    chat_template_kwargs = dict(run_agent_v2._chat_template_kwargs(model_config))
    chat_template_kwargs["tools"] = agent_definition.harmony_tools()
    chat_template_kwargs["chat_template"] = build_tool_call_chat_template(
        tokenizer.chat_template
    )

    quantization_config = run_agent_v2._quantization_config(model_config)
    load_kwargs: dict[str, Any] = {}
    if quantization_config is not None:
        load_kwargs["quantization_config"] = quantization_config
    load_started = time.perf_counter()
    model = AutoModelForCausalLM.from_pretrained(
        model_config["model_id"],
        **common,
        dtype=torch.bfloat16,
        device_map=model_config["device_map"],
        attn_implementation=model_config["attn_implementation"],
        **load_kwargs,
    )
    model.eval()
    torch.cuda.synchronize()
    model_load_seconds = time.perf_counter() - load_started
    hf_quantization = getattr(model.config, "quantization_config", None)
    if bool(getattr(model, "use_kernels", False)):
        raise RuntimeError("use_kernels is enabled; the fused MoE kernel bypasses the router")
    if bool(getattr(hf_quantization, "dequantize", False)):
        raise RuntimeError("MXFP4 fell back to dequantize; aborting rather than filling the card")

    run_root = args.output_dir
    if run_root is None:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_root = ROOT / "artifacts" / "agent_v2" / "agent_v3_p0" / timestamp
    run_root = Path(run_root).resolve()
    run_root.mkdir(parents=True, exist_ok=False)
    _write_json(run_root / "resolved_experiment_config.json", experiment_config)
    _write_json(run_root / "resolved_agent_config.json", agent_config)
    _write_json(run_root / "resolved_model_config.json", model_config)
    _write_json(run_root / "input_validation.json", validation_report)
    (run_root / "resolved_chat_template.jinja").write_text(
        chat_template_kwargs["chat_template"], encoding="utf-8"
    )

    traces: list[dict[str, Any]] = []
    for scenario in scenarios:
        for arm_name in ARM_NAMES:
            if arm_name not in arms:
                continue
            if args.limit is not None and len(traces) >= args.limit:
                break
            trace_dir = run_root / scenario["pair_group_id"] / arm_name
            trace = _run_trace(
                model=model,
                tokenizer=tokenizer,
                model_config=model_config,
                experiment_config=experiment_config,
                agent_definition=agent_definition,
                chat_template_kwargs=chat_template_kwargs,
                specials=specials,
                stop_token_ids=stop_token_ids,
                scenario=scenario,
                arm_name=arm_name,
                output_dir=trace_dir,
            )
            traces.append(trace)
            outcome = trace["outcome"]
            print(
                f"{trace['trace_id']}: episodes={outcome['episode_count']} "
                f"steps={outcome['step_count']} tokens={outcome['generated_token_count']} "
                f"calls={outcome['tool_call_count']} restricted={outcome['restricted_call_count']} "
                f"malformed={outcome['malformed_call_count']} "
                f"stop={outcome['episode_stop_reasons']} "
                f"validated={trace['validation']['passed']} "
                f"{trace['summary']['wall_seconds']:.1f}s",
                flush=True,
            )
        if args.limit is not None and len(traces) >= args.limit:
            break

    report = {
        "experiment_id": experiment_config["experiment_id"],
        "dataset_role": experiment_config["dataset_role"],
        "created_at": datetime.now(UTC).isoformat(),
        "config_path": str(config_path),
        "config_hash": validation_report["config_hash"],
        "agent_id": agent_definition.agent_id,
        "agent_definition_version": agent_definition.definition_version,
        "agent_config_hash": validation_report["agent_config_hash"],
        "model_id": model_config["model_id"],
        "model_revision": model_config["revision"],
        "quantization": model_config.get("quantization"),
        "router": describe_model_routers(model, model_config.get("router_adapter")),
        "chat_template_hash": _json_hash(chat_template_kwargs["chat_template"]),
        "stop_token_ids": stop_token_ids,
        "model_load_seconds": round(model_load_seconds, 6),
        "trace_count": len(traces),
        "validated_trace_count": sum(bool(trace["validation"]["passed"]) for trace in traces),
        "total_generated_tokens": sum(
            trace["outcome"]["generated_token_count"] for trace in traces
        ),
        "total_wall_seconds": round(
            sum(trace["summary"]["wall_seconds"] for trace in traces), 3
        ),
        "restricted_call_traces": sum(
            bool(trace["outcome"]["restricted_call_count"]) for trace in traces
        ),
        "malformed_call_traces": sum(
            bool(trace["outcome"]["malformed_call_count"]) for trace in traces
        ),
        "traces": traces,
    }
    _write_json(run_root / "run_summary.json", report)
    print(
        f"run complete: {report['trace_count']} traces, "
        f"{report['validated_trace_count']} validated, "
        f"{report['total_generated_tokens']} generated tokens, "
        f"{report['total_wall_seconds']:.1f}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
