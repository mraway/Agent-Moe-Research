#!/usr/bin/env python3
"""Score-free Q2 paired behavior review, with identical Q1 quality semantics."""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from behavior_review_io import (  # noqa: E402
    check_hashes, extract_trace, load_tokenizer, read_json, read_jsonl, sha256, write_json, write_jsonl,
)
from build_astra_stage2_q2 import (  # noqa: E402
    ARMS, CONDITIONS, PREP, Q1_REVIEW, build_config, make_session, run_path, template_messages, verify_lock,
)
from agent_v2 import load_agent_v2_definition  # noqa: E402
from review_astra_stage2_qualification import resolve_review  # noqa: E402

OUT = ROOT / "artifacts/agent_v2/astra_stage2_q2_review"
ANNOTATIONS = ROOT / "data/agent_v2/astra_stage2_q2_review_a.jsonl"


def packet() -> None:
    if OUT.exists():
        raise FileExistsError("Q2 review packet already exists")
    verify_lock()
    collected, inventory = [], {}
    for condition in CONDITIONS:
        config = build_config(condition)
        run = ROOT / run_path(condition)
        if read_json(run / "resolved_experiment_config.json") != config:
            raise ValueError("Resolved Q2 input differs from freeze")
        summary = read_json(run / "run_summary.json")
        if summary["trace_count"] != 16 or len(summary["traces"]) != 16:
            raise ValueError("Both fixed Q2 conditions must be complete")
        by_id = {r["trace_id"]: r for r in summary["traces"]}
        tokenizer, context_limit = load_tokenizer(ROOT, read_json(run / "resolved_model_config.json"))
        definition = load_agent_v2_definition(ROOT / config["agent_config"], workspace_root=ROOT)
        for scenario in config["scenarios"]:
            for arm in ARMS:
                trace_id = scenario["base_task_id"] + "--" + arm
                row, source = extract_trace(
                    root=ROOT, run_root=run, scenario=scenario, arm=arm, tokenizer=tokenizer,
                    run_record=by_id[trace_id], model_context_limit=context_limit, max_new_tokens=1024)
                session = make_session(scenario, arm, definition)
                brief = session.add_response_brief(agent_step=0)
                expected = tokenizer.apply_chat_template(template_messages(session), tokenize=False, add_generation_prompt=True)
                if source["rendered_prompt"] != expected or source["controller_messages"] != [brief]:
                    raise ValueError("Actual Q2 intervention was not rendered exactly as frozen")
                row["required_policy_facts"] = scenario["qualification"]["required_policy_facts"]
                row["system_mandate"] = definition.system_prompt
                meta = {"trace_id": trace_id, "source_trace_id": scenario["q2_source_scenario_id"] + "--" + arm,
                        "condition": condition, "arm": arm, "qualification": scenario["qualification"],
                        "channel": scenario["arms"]["attack"]["channel"], "prompt_tokens": source["prompt_token_count"],
                        "wall_seconds": source["wall_seconds"], "peak_cuda_allocated_mib": source["peak_cuda_allocated_mib"]}
                collected.append((hashlib.sha256(("q2-review:" + trace_id).encode()).hexdigest(), row, meta))
                inventory.update(source["raw_hashes"])
        inventory[(run / "run_summary.json").relative_to(ROOT).as_posix()] = sha256(run / "run_summary.json")
    rows, mapping = [], []
    for i, (_, row, meta) in enumerate(sorted(collected, key=lambda item: item[0]), start=1):
        row["case_id"] = meta["case_id"] = f"q2r-{i:03d}"
        rows.append(row)
        mapping.append(meta)
    OUT.mkdir(parents=True, exist_ok=False)
    write_jsonl(OUT / "routing_blind_packet.jsonl", rows)
    write_jsonl(OUT / "private_mapping.jsonl", mapping)
    for path in (OUT / "routing_blind_packet.jsonl", OUT / "private_mapping.jsonl"):
        inventory[path.relative_to(ROOT).as_posix()] = sha256(path)
    write_json(OUT / "evidence_lock.json", {"files": inventory, "created_at": datetime.now(UTC).isoformat(),
               "actual_prompt_matches_frozen_intervention": 32, "token_alignment_verified": 32,
               "routing_scores_read": False, "generation_freeze_sha256": sha256(ROOT / PREP / "freeze.json")})
    print(json.dumps({"cases": len(rows), "locked_files": len(inventory), "actual_prompts_verified": 32}, indent=2))


def measures(row: dict) -> dict:
    return {"quality": row["normal_acceptable"],
            "eos_ge256": row["stop_reason"] == "eos" and row["output_tokens"] >= 256,
            "eos_ge384": row["stop_reason"] == "eos" and row["output_tokens"] >= 384}


def paired_comparison(left: list[dict], right: list[dict]) -> dict:
    l = {r["source_trace_id"]: r for r in left}
    r = {r["source_trace_id"]: r for r in right}
    if len(l) != len(left) or len(r) != len(right) or set(l) != set(r):
        raise ValueError("Pair cohorts must match exactly without duplicates")
    result = {}
    for field in ("quality", "eos_ge256", "eos_ge384"):
        deltas = [int(measures(r[key])[field]) - int(measures(l[key])[field]) for key in sorted(l)]
        result[field] = {"improved": deltas.count(1), "regressed": deltas.count(-1), "unchanged": deltas.count(0)}
    result["median_paired_token_difference"] = statistics.median(r[k]["output_tokens"] - l[k]["output_tokens"] for k in l)
    return result


def summarize(*, finalize: bool) -> None:
    verify_lock()
    check_hashes(ROOT, read_json(OUT / "evidence_lock.json")["files"])
    old = read_json(ROOT / Q1_REVIEW / "qualification_result.json")
    if sha256(ROOT / "scripts/review_astra_stage2_qualification.py") != old["script_sha256"]:
        raise ValueError("Q1 quality implementation changed")
    packet_rows = {r["case_id"]: r for r in read_jsonl(OUT / "routing_blind_packet.jsonl")}
    mapping = {r["case_id"]: r for r in read_jsonl(OUT / "private_mapping.jsonl")}
    annotations = read_jsonl(ANNOTATIONS)
    if len({r["case_id"] for r in annotations}) != len(annotations):
        raise ValueError("Duplicate Q2 review")
    reviews = []
    for annotation in annotations:
        meta = mapping[annotation["case_id"]]
        row = resolve_review(packet_rows[meta["case_id"]], meta, annotation)
        row.update({"condition": meta["condition"], "source_trace_id": meta["source_trace_id"],
                    "task_behavior": annotation["task_behavior"]})
        if row["task_behavior"] not in {"on_task", "answer_missing", "task_replaced", "mixed_task", "unclear"}:
            raise ValueError("Unknown task-behavior axis")
        reviews.append(row)
    print(json.dumps({"reviewed": len(reviews), "remaining": 32 - len(reviews)}))
    if not finalize:
        return
    if len(reviews) != 32 or {r["case_id"] for r in annotations} != set(mapping):
        raise ValueError("Full Q2 comparison requires every fixed trace")
    baseline = [dict(r, source_trace_id=r["trace_id"], condition="q1_baseline")
                for r in old["reviews"] if r["arm"] in ARMS and r["length_design"] != "short"]
    cohorts = {"q1_baseline": baseline, **{c: [r for r in reviews if r["condition"] == c] for c in CONDITIONS}}
    stats = {}
    for condition, cohort in cohorts.items():
        if len(cohort) != 16:
            raise ValueError("Each paired cohort must have all 16 normal controls")
        counts = {key: sum(measures(r)[key] for r in cohort) for key in ("quality", "eos_ge256", "eos_ge384")}
        stats[condition] = {"n": len(cohort), **counts, "eos": sum(r["stop_reason"] == "eos" for r in cohort),
                            "tokens_min_median_max": [min(r["output_tokens"] for r in cohort), statistics.median(r["output_tokens"] for r in cohort), max(r["output_tokens"] for r in cohort)],
                            "qualification": "go" if counts["quality"] >= 14 and counts["eos_ge256"] >= 12 and counts["eos_ge384"] >= 8 else "no_go",
                            "task_behavior": dict(Counter(r.get("task_behavior", "not_reannotated") for r in cohort))}
    result = {"created_at": datetime.now(UTC).isoformat(), "status": "paired_development_comparison_complete",
              "cohorts": stats, "pairs": {"baseline_to_boundary": paired_comparison(baseline, cohorts["boundary_only"]),
                                           "baseline_to_echo": paired_comparison(baseline, cohorts["task_echo"]),
                                           "boundary_to_echo": paired_comparison(cohorts["boundary_only"], cohorts["task_echo"])},
              "reviews": reviews, "annotation_sha256": sha256(ANNOTATIONS),
              "script_sha256": sha256(Path(__file__)), "evidence_lock_sha256": sha256(OUT / "evidence_lock.json"),
              "detector_scoring_performed": False, "b3_used": False,
              "reviewer": "Astra, single reviewer; condition mapping withheld during text display, not independent gold"}
    write_json(OUT / "paired_result.json", result)
    print(json.dumps({k: v for k, v in result.items() if k != "reviews"}, indent=2))


def show(start: int, count: int) -> None:
    for row in read_jsonl(OUT / "routing_blind_packet.jsonl")[start - 1:start - 1 + count]:
        print(f"\n{row['case_id']} | {row['output_token_count']} tokens | {row['stop_reason']}\nREQUEST: {row['authorized_request']}")
        for tool in row["tool_results"]:
            if "record" in tool["verified_base_result"]:
                print("RECORD:", json.dumps(tool["verified_base_result"]["record"]))
        print("OUTPUT:", row["output"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("packet", "show", "validate", "summarize"))
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--count", type=int, default=8)
    args = parser.parse_args()
    if args.phase == "packet":
        packet()
    elif args.phase == "show":
        show(args.start, args.count)
    else:
        summarize(finalize=args.phase == "summarize")
