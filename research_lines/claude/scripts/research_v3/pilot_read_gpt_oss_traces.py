#!/usr/bin/env python3
"""Routing-blind reader for the gpt-oss-20b pilot traces.

Reconstructs, per trace, the harmony analysis/final token split from
``trace.json["generation_channels"]`` and the recorded output token ids, and
prints token counts, stop reasons, wall time and the decoded per-channel text.
Reads only trace.json; never opens a routing shard, so the behavioural read is
blind to routing. No detector statistic is computed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from routing import validate_trace  # noqa: E402


def main() -> int:
    from transformers import AutoTokenizer

    run_root = Path(sys.argv[1]).resolve()
    model_config = json.loads(
        (ROOT / "configs/pilot_gpt_oss_20b_mxfp4.json").read_text(encoding="utf-8")
    )
    tokenizer = AutoTokenizer.from_pretrained(
        model_config["model_id"],
        revision=model_config["revision"],
        cache_dir=(ROOT / model_config["cache_dir"]).resolve(),
        local_files_only=True,
    )

    rows = []
    for trace_path in sorted(run_root.rglob("trace.json")):
        trace_dir = trace_path.parent
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        validation = validate_trace(trace_dir)
        events = trace["events"]
        channels = trace.get("generation_channels", {})
        steps = channels.get("steps", [])
        generations = [event for event in events if event["kind"] == "model_generation"]
        per_step = []
        for index, event in enumerate(generations):
            ids = event["output_token_ids"]
            boundaries = event.get("channel_boundaries", {})
            analysis_start = boundaries.get("analysis", -1)
            final_start = boundaries.get("final", -1)
            commentary_start = boundaries.get("commentary", -1)
            total = len(ids)
            if final_start >= 0:
                analysis_tokens = final_start + 1 - max(analysis_start + 1, 0)
                final_tokens = total - (final_start + 1)
            else:
                analysis_tokens = total - max(analysis_start + 1, 0)
                final_tokens = 0
            final_text = (
                tokenizer.decode(ids[final_start + 1 :], skip_special_tokens=True)
                if final_start >= 0
                else ""
            )
            analysis_text = tokenizer.decode(
                ids[max(analysis_start + 1, 0) : (final_start if final_start >= 0 else total)],
                skip_special_tokens=True,
            )
            per_step.append(
                {
                    "agent_step": event["agent_step"],
                    "prompt_token_count": event["prompt_token_count"],
                    "output_token_count": total,
                    "stop_reason": event["stop_reason"],
                    "channel_boundaries": boundaries,
                    "analysis_token_count": max(analysis_tokens, 0),
                    "final_token_count": max(final_tokens, 0),
                    "commentary_boundary": commentary_start,
                    "analysis_text": analysis_text,
                    "final_text": final_text,
                    "raw_with_specials": tokenizer.decode(ids, skip_special_tokens=False),
                }
            )
        rows.append(
            {
                "trace_id": trace["trace_id"],
                "arm": trace["perturbation"]["arm"],
                "channel": trace["perturbation"]["channel"],
                "dir": str(trace_dir),
                "wall_seconds": trace["summary"]["wall_seconds"],
                "generated_token_count": trace["summary"]["generated_token_count"],
                "peak_cuda_allocated_mib": trace["summary"]["peak_cuda_allocated_mib"],
                "step_count": trace["step_count"],
                "validation": validation,
                "outcome": {
                    key: trace["outcome"][key]
                    for key in (
                        "goal_plan_deviation_started",
                        "original_task_completed",
                        "attacker_goal_achieved",
                        "routine_tool_flow_completed",
                        "recoverable_protocol_error",
                        "stratum",
                        "final_message_count",
                    )
                },
                "steps": per_step,
                "router": trace["router"],
                "assistant_message": next(
                    (
                        event["content"]
                        for event in reversed(events)
                        if event["kind"] == "assistant_message" and event["actor"] == "assistant"
                    ),
                    None,
                ),
            }
        )
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
