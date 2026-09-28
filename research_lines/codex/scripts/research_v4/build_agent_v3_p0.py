#!/usr/bin/env python3
"""Build the Agent v3 P0 probe configs (batch and wording probe).

P0 is the pre-registration probe of ``docs/research_v4/agent_v3_dataset_design.md``
section 10: it measures loop stability, channel share, spontaneous restricted
calls and per-cell yields before anything is frozen. It is **not** data: both
configs carry ``dataset_role = "p0_probe_not_data"`` and neither enters the fit,
calibration or development pools.

Eight frozen Atlas v2.5-B2 scenarios are copied verbatim (seeds, task,
requirements, completion evidence, arms and injected wording included), two per
design section 3.3 domain group, spread over the three injection channels with
``tool_output`` at the 1/4 share section 3.2 fixes, all of them type-A content
deliverables (type-B unauthorized-operation attacks are deferred by the user
decision in section 14):

===================  ==================  ================  ==========================
domain group         off-task domain     channel           scenario
===================  ==================  ================  ==========================
creative             poetry              tool_output       b2-f0-001 (first_kb_hit)
creative             fiction             direct_user       b2-f3-041
code                 programming (SQL)   direct_user       b2-f2-011
code                 programming (Rust)  tool_output       b2-f3-016 (result_note)
reasoning_legal      mathematics         multi_turn_user   b2-f4-021
reasoning_legal      legal analysis      multi_turn_user   b2-f3-066
everyday_knowledge   cooking             multi_turn_user   b2-f2-036
everyday_knowledge   general knowledge   direct_user       b2-f4-071
===================  ==================  ================  ==========================

All eight use the frozen B2 "second deliverable" wording, which is what makes
tier T0 the frozen baseline and lets tiers T1/T2 be produced by the pilot's own
escalation function (``scripts/research_v3/pilot_build_batch.py::_variant_texts``,
imported here rather than re-typed, so the probe wordings are literally the ones
``docs/research_v3/model_pilot_report.md`` section 3.2 reports on):

* ``T0`` - frozen B2 wording (``UNTRUSTED ADDENDUM``/second deliverable);
* ``T1`` - ``authority_system_update``: authenticated platform notice with a
  compliance consequence;
* ``T2`` - ``format_contract``: ticket-record response-format field that is
  "checked automatically".

Outputs::

    configs/agent_v3_p0_batch.json   8 scenarios x 3 arms  = 24 traces
    configs/agent_v3_p0_probe.json   8 scenarios x 3 tiers = 24 traces (attack only)

Usage::

    build_agent_v3_p0.py                 # write both configs
    build_agent_v3_p0.py --print-only    # dump them to stdout
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agent_v3 import ARM_NAMES, validate_agent_v3_experiment  # noqa: E402


SOURCE_CONFIG = ROOT / "configs" / "agent_v2_5_b2.json"
PILOT_BUILDER = ROOT / "scripts" / "research_v3" / "pilot_build_batch.py"
AGENT_CONFIG = "configs/agent_v3_support.json"
MODEL_CONFIG = "configs/pilot_gpt_oss_20b_mxfp4.json"
MAX_NEW_TOKENS_PER_TURN = 1024
MAX_AGENT_STEPS = 6

SELECTED_SCENARIOS: tuple[tuple[str, str, str], ...] = (
    (
        "b2-f0-001-order_and_knowledge-free-verse",
        "creative",
        "poetry via tool_output (first_kb_hit) on a two-tool routine",
    ),
    (
        "b2-f3-041-subscription_and_knowledge-character-monologue",
        "creative",
        "fiction via direct_user on a two-tool routine",
    ),
    (
        "b2-f2-011-knowledge_qa-sql-query",
        "code",
        "SQL via direct_user on a knowledge-base routine (also in the gpt-oss pilot)",
    ),
    (
        "b2-f3-016-subscription_status-rust-function",
        "code",
        "Rust via tool_output (result_note) on a single-record routine",
    ),
    (
        "b2-f4-021-return_status-geometry-derivation",
        "reasoning_legal",
        "mathematics via multi_turn_user (also in the gpt-oss pilot)",
    ),
    (
        "b2-f3-066-knowledge_qa-fictional-case-comparison",
        "reasoning_legal",
        "legal analysis via multi_turn_user on a knowledge-base routine",
    ),
    (
        "b2-f2-036-support_case_status-grocery-plan",
        "everyday_knowledge",
        "cooking via multi_turn_user on a single-record routine",
    ),
    (
        "b2-f4-071-subscription_status-biology-explanation",
        "everyday_knowledge",
        "general knowledge via direct_user on a single-record routine",
    ),
)

TIER_VARIANTS = {
    "T0": None,
    "T1": "authority_system_update",
    "T2": "format_contract",
}
TIER_DESCRIPTIONS = {
    "T0": "Frozen Atlas v2.5-B2 wording (UNTRUSTED ADDENDUM / second deliverable).",
    "T1": (
        "authority_system_update: authenticated platform workflow notice with a "
        "compliance consequence (pilot wording, model_pilot_report.md section 3.2)."
    ),
    "T2": (
        "format_contract: ticket-record response-format field that is checked "
        "automatically (pilot wording, model_pilot_report.md section 3.2)."
    ),
}


def _pilot_module() -> Any:
    """Import the frozen pilot builder by path to reuse its escalation wordings."""

    spec = importlib.util.spec_from_file_location("pilot_build_batch_v3", PILOT_BUILDER)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError(f"cannot import {PILOT_BUILDER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _base_scenarios() -> list[dict[str, Any]]:
    source = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
    by_id = {scenario["base_task_id"]: scenario for scenario in source["scenarios"]}
    missing = [task_id for task_id, _, _ in SELECTED_SCENARIOS if task_id not in by_id]
    if missing:
        raise ValueError(f"scenarios missing from {SOURCE_CONFIG.name}: {missing}")
    scenarios = []
    for task_id, domain_group, _ in SELECTED_SCENARIOS:
        scenario = json.loads(json.dumps(by_id[task_id]))
        scenario["domain_group"] = domain_group
        scenario["wording_tier"] = "T0"
        scenario["source_experiment_id"] = source["experiment_id"]
        scenarios.append(scenario)
    return scenarios


def _common(experiment_id: str, dataset_role: str, purpose: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "phase": "agent_v3",
        "experiment_id": experiment_id,
        "agent_config": AGENT_CONFIG,
        "model_config": MODEL_CONFIG,
        "dataset_role": dataset_role,
        "purpose": purpose,
        "source_config": SOURCE_CONFIG.relative_to(ROOT).as_posix(),
        "decoding": {
            "strategy": "sample",
            "temperature": 0.8,
            "top_p": 0.9,
            "max_new_tokens_per_turn": MAX_NEW_TOKENS_PER_TURN,
            "max_agent_steps": MAX_AGENT_STEPS,
            "assistant_protocol": "harmony_tool_calls",
        },
        "scenario_selection": [
            {"base_task_id": task_id, "domain_group": group, "reason": reason}
            for task_id, group, reason in SELECTED_SCENARIOS
        ],
    }


def build_batch_config() -> dict[str, Any]:
    config = _common(
        "agent_v3_p0_batch",
        "p0_probe_not_data",
        (
            "P0 probe (design section 10): loop stability, step distribution, channel "
            "share, spontaneous restricted-tool calls and normal-arm behaviour of the "
            "Agent v3 tool loop on gpt-oss-20b. Shape and integrity only; no routing "
            "statistic is computed and no sample enters any pool."
        ),
    )
    config["arms"] = list(ARM_NAMES)
    config["suggested_output_root"] = "artifacts/agent_v2/agent_v3_p0/batch"
    config["scenarios"] = _base_scenarios()
    validate_agent_v3_experiment(config)
    return config


def build_probe_config() -> dict[str, Any]:
    pilot = _pilot_module()
    config = _common(
        "agent_v3_p0_probe",
        "p0_probe_not_data",
        (
            "P0 wording probe (design sections 3.2 and 10): the same eight scenarios "
            "at three injection wording tiers, attack arm only, to measure the "
            "wording x channel yield that fixes the section 3.3 supplement layer. "
            "Not data, not labels; T0 repeats the batch attack arm at the same seed."
        ),
    )
    config["arms"] = ["attack"]
    config["suggested_output_root"] = "artifacts/agent_v2/agent_v3_p0/probe"
    config["wording_tiers"] = TIER_DESCRIPTIONS
    config["probe_protocol"] = (
        "Run with --arms attack. clean and benign_control are carried unchanged only "
        "so the experiment schema validates; they are not probe traces."
    )

    scenarios: list[dict[str, Any]] = []
    for base in _base_scenarios():
        _, original = pilot._injected_field(base["arms"]["attack"])
        variants = pilot._variant_texts(original)
        for tier, variant_name in TIER_VARIANTS.items():
            scenario = json.loads(json.dumps(base))
            scenario["base_task_id"] = f"{base['base_task_id']}--{tier}"
            scenario["pair_group_id"] = scenario["base_task_id"]
            scenario["wording_tier"] = tier
            scenario["probe_variant"] = variant_name or "frozen_b2_wording"
            scenario["probe_original_injection"] = original
            if variant_name is not None:
                scenario["arms"]["attack"] = pilot._with_injected_text(
                    base["arms"]["attack"], variants[variant_name]
                )
            scenarios.append(scenario)
    config["scenarios"] = scenarios
    validate_agent_v3_experiment(config)
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--print-only", action="store_true")
    args = parser.parse_args()

    outputs = {
        ROOT / "configs" / "agent_v3_p0_batch.json": build_batch_config(),
        ROOT / "configs" / "agent_v3_p0_probe.json": build_probe_config(),
    }
    for path, config in outputs.items():
        payload = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
        if args.print_only:
            print(f"=== {path.name} ===")
            print(payload, end="")
            continue
        path.write_text(payload, encoding="utf-8")
        traces = len(config["scenarios"]) * len(config["arms"])
        print(
            f"wrote {path} ({len(config['scenarios'])} scenarios x "
            f"{len(config['arms'])} arms = {traces} traces)"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
