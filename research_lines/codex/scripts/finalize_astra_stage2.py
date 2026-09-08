#!/usr/bin/env python3
"""Score-free Q2/C1 closeout: recompute descriptive summaries and verify evidence."""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from behavior_review_io import check_hashes, read_json, read_jsonl, sha256, write_json  # noqa: E402

C1 = ROOT / "artifacts/agent_v2/c1_behavior_axis_audit_v1"
Q2 = ROOT / "artifacts/agent_v2/astra_stage2_q2_review"
OUT = ROOT / "artifacts/agent_v2/astra_stage2_closeout_v1"


def metrics(rows: list[dict]) -> dict:
    return {"n": len(rows),
            "on_task": sum(r["behavior"] == "on_task" for r in rows),
            "deviation": sum(r["behavior"] in {"mixed_task", "task_replaced"} for r in rows),
            "unclear": sum(r["behavior"] == "unclear" for r in rows),
            "answer_missing": sum(r["behavior"] == "answer_missing" for r in rows),
            "with_material_error": sum(bool(r["material_errors"]) for r in rows),
            "on_task_with_material_error": sum(r["behavior"] == "on_task" and bool(r["material_errors"]) for r in rows),
            "with_unavailable_action": sum(bool(r["unavailable_actions"]) for r in rows),
            "quality_ok": sum(r["observed_answer_quality_ok"] for r in rows),
            "quality_ok_eos": sum(r["observed_answer_quality_ok"] and not r["right_censored"] for r in rows),
            "right_censored": sum(r["right_censored"] for r in rows)}


def family(workflow: str) -> str:
    if workflow.endswith("_and_knowledge"):
        return "status_plus_knowledge"
    if workflow == "knowledge_qa":
        return "knowledge_only"
    if workflow in {"order_status", "return_status", "support_case_status", "subscription_status", "warranty_status"}:
        return "status_only"
    raise ValueError(f"Unknown workflow {workflow}")


def paired(rows: list[dict]) -> dict:
    groups = defaultdict(dict)
    for row in rows:
        group = groups[row["pair_group_id"]]
        if row["arm"] in group:
            raise ValueError("Duplicate paired arm")
        group[row["arm"]] = row
    if any(set(g) != {"clean", "benign_control"} for g in groups.values()):
        raise ValueError("Incomplete paired group")
    functions = {"answer_quality_ok": lambda r: r["observed_answer_quality_ok"],
                 "with_unavailable_action": lambda r: bool(r["unavailable_actions"]),
                 "with_material_error": lambda r: bool(r["material_errors"])}
    result = {"n_groups": len(groups)}
    for name, fn in functions.items():
        counts = Counter((bool(fn(g["clean"])), bool(fn(g["benign_control"]))) for g in groups.values())
        result[name] = {"both": counts[True, True], "clean_only": counts[True, False],
                        "benign_only": counts[False, True], "neither": counts[False, False]}
    result["median_benign_minus_clean_tokens"] = statistics.median(
        g["benign_control"]["output_tokens"] - g["clean"]["output_tokens"] for g in groups.values())
    return result


def source_flags(packets: list[dict]) -> dict:
    broad, annual = [], []
    for row in packets:
        bases = [tool["verified_base_result"] for tool in row["tool_results"]]
        if row["authorized_request"].startswith("I need the corresponding") and any("hits" in b for b in bases):
            broad.append(row["case_id"])
        if any("Annual" in b.get("record", {}).get("plan", "")
               and b.get("record", {}).get("status") == "paused" for b in bases):
            annual.append(row["case_id"])
    return {"broad_identifier_request_with_background_kb": broad,
            "annual_plan_with_paused_record": annual,
            "interpretation": "Source-contract review flags only; no automatic label changes, exclusions, or scoring."}


def audit() -> dict:
    inventories = {
        "historical_main_inputs": read_json(ROOT / "artifacts/agent_v2/astra_stage1_v1/freeze.json")["hashes"],
        "q2_frozen_inputs": read_json(ROOT / "artifacts/agent_v2/astra_stage2_q2_prep/freeze.json")["files"],
        "q1_raw_outputs": read_json(ROOT / "artifacts/agent_v2/astra_stage2_qualification_q1_review/raw_output_hashes.json"),
        "q2_raw_and_packet": read_json(Q2 / "evidence_lock.json")["files"],
        "c1_raw_packet_and_rubric": read_json(C1 / "freeze.json")["files"],
    }
    merged = {}
    for inventory in inventories.values():
        for path, digest in inventory.items():
            if path in merged and merged[path] != digest:
                raise ValueError(f"Conflicting frozen inventories: {path}")
            merged[path] = digest
    check_hashes(ROOT, merged)
    c1, q2 = read_json(C1 / "audit_result.json"), read_json(Q2 / "paired_result.json")
    for result, filename in ((c1, "c1_behavior_axis_review_a.jsonl"), (q2, "astra_stage2_q2_review_a.jsonl")):
        if result["annotation_sha256"] != sha256(ROOT / "data/agent_v2" / filename):
            raise ValueError("Post-summary annotation change")
    if q2["script_sha256"] != sha256(ROOT / "scripts/review_astra_stage2_q2.py"):
        raise ValueError("Post-summary Q2 review implementation change")
    rows = c1["reviews"]
    if len(rows) != 320 or {r["case_id"] for r in rows} != {f"c1a-{i:03d}" for i in range(1, 321)}:
        raise ValueError("Incomplete C1 review cohort")
    for key, expected in c1["summaries"]["all"].items():
        actual = (dict(Counter(r[key] for r in rows)) if key in {"behavior", "engagement", "coverage", "citation"}
                  else len(rows) if key == "n"
                  else sum(bool(r["material_errors"]) for r in rows) if key == "with_material_error"
                  else sum(bool(r["unavailable_actions"]) for r in rows) if key == "with_unavailable_action"
                  else sum(r[key] for r in rows))
        if actual != expected:
            raise ValueError(f"C1 aggregate mismatch: {key}")
    if q2["detector_scoring_performed"] or q2["b3_used"] or c1["detector_analysis_performed"]:
        raise ValueError("Qualification scope violation")
    if any(q2["cohorts"][c]["n"] != 16 for c in ("q1_baseline", "boundary_only", "task_echo")):
        raise ValueError("Q2 cohort loss")
    role = lambda r: "fit" if r["fold"] == 0 else "calibration" if r["fold"] in (1, 2) else "held_out"
    roles = {name: [r for r in rows if role(r) == name] for name in ("fit", "calibration", "held_out")}
    group_sets = {name: {r["pair_group_id"] for r in cohort} for name, cohort in roles.items()}
    if any(group_sets[a] & group_sets[b] for a, b in (("fit", "calibration"), ("fit", "held_out"), ("calibration", "held_out"))):
        raise ValueError("Cross-role paired-group leakage")
    return {"verified_inventory_counts": {k: len(v) for k, v in inventories.items()},
            "unique_protected_files_verified": len(merged),
            "all": metrics(rows),
            "by_workflow_family": {f: metrics([r for r in rows if family(r["workflow"]) == f])
                                   for f in ("status_only", "knowledge_only", "status_plus_knowledge")},
            "by_reference_role": {name: {**metrics(cohort), "groups": len(group_sets[name])}
                                  for name, cohort in roles.items()},
            "paired_clean_benign": paired(rows),
            "source_flags": source_flags(read_jsonl(C1 / "routing_blind_packet.jsonl")),
            "behavior_unclear_case_ids": [r["case_id"] for r in rows if r["behavior"] == "unclear"],
            "routing_analysis_performed": False, "original_denominators_unchanged": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("check", "create", "verify"))
    args = parser.parse_args()
    if args.phase == "verify":
        saved = read_json(OUT / "closeout.json")
        check_hashes(ROOT, saved["delivery_file_hashes"])
        if audit() != saved["audit"]:
            raise ValueError("Closeout recomputation mismatch")
        print(json.dumps({"verified": True, "protected_files": saved["audit"]["unique_protected_files_verified"]}))
        return
    if args.phase == "create" and OUT.exists():
        raise FileExistsError("Preserve existing closeout")
    result = audit()
    if args.phase == "check":
        print(json.dumps(result, indent=2))
        return
    test = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"], cwd=ROOT,
                          capture_output=True, text=True, check=True, timeout=60)
    paths = [
        "docs/astra_stage2_q2_report.md", "docs/c1_behavior_axis_audit_report.md",
        "docs/c1_behavior_axis_reviewer_handoff.md", "docs/astra_stage2_closeout_decision.md",
        "scripts/finalize_astra_stage2.py", "tests/test_astra_stage2_closeout.py",
        "scripts/review_astra_stage2_q2.py", "tests/test_astra_stage2_q2_review.py",
        "data/agent_v2/astra_stage2_q2_review_a.jsonl", "data/agent_v2/c1_behavior_axis_review_a.jsonl",
        "artifacts/agent_v2/astra_stage2_q2_review/paired_result.json",
        "artifacts/agent_v2/c1_behavior_axis_audit_v1/audit_result.json",
    ]
    delivery = {p: sha256(ROOT / p) for p in paths}
    OUT.mkdir(parents=True, exist_ok=False)
    write_json(OUT / "closeout.json", {"created_at": datetime.now(UTC).isoformat(), "audit": result,
               "tests": {"command": test.args, "returncode": test.returncode, "stdout": test.stdout, "stderr": test.stderr},
               "delivery_file_hashes": delivery, "status": "q2_and_c1_execution_complete_qualification_no_go",
               "not_claimed": ["entire_research_project_complete", "qualified_long_normal_bank", "independent_review", "B3_confirmation"]})
    print(json.dumps({"closeout": str(OUT / "closeout.json"), "sha256": sha256(OUT / "closeout.json"),
                      "protected_files": result["unique_protected_files_verified"], "tests_exit_code": test.returncode}))


if __name__ == "__main__":
    main()
