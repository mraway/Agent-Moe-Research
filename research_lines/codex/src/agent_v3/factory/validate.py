"""Validators for the generated dataset G configs.

Each check returns a :class:`CheckResult`; ``run_all`` runs them over the written
configs. The checks are deliberately independent of the builders where that is
possible: material answerability is re-derived by *executing the read-only tools*
against the fixture through the real ``SupportToolEnvironment``, and the tier
wordings are re-derived from the frozen pilot builder and compared byte for byte.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from agent_v2 import apply_tool_result_injection
from agent_v2.runtime import SupportToolEnvironment

from ..experiment import ARM_NAMES, user_turns, validate_agent_v3_experiment
from ..tools import READ_ONLY_TOOLS, RESTRICTED_TOOLS
from .attacks import TOOL_OUTPUT_PREFIX, ATTACK_TASKS, tier_text
from .constants import (
    CHANNELS,
    CORE_CELL_COUNT,
    DOMAIN_GROUPS,
    HELD_OUT_CONF_ONLY,
    HELD_OUT_DEV_ONLY,
    MAX_AGENT_STEPS,
    MAX_NEW_TOKENS_PER_TURN,
    MODEL_CONTEXT_TOKENS,
    PROMPT_MATERIAL_TOKEN_BUDGET,
    PROMPT_TOKEN_BUDGET,
    R_TYPE_RATIO,
    TIERS,
)


ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = ROOT / "configs" / "dataset_g"
SUBSETS = ("g_fit", "g_cal", "g_dev", "g_session", "g_medium", "g_conf")

_HYPHENS = dict.fromkeys(
    [0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212, 0x00AD], "-"
)
_QUOTES = {0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"'}
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_LIKE_FIELD = re.compile(
    r"(_on|_date|_by|_end|_start|_since|_update|_deadline|_delivery|_as_of)$"
)


def normalize(text: str) -> str:
    """Design 15.1: Unicode hyphen normalisation is mandatory before matching."""

    text = unicodedata.normalize("NFKC", str(text))
    text = text.translate(_HYPHENS).translate(_QUOTES)
    return re.sub(r"\s+", " ", text).strip().lower()


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str
    failures: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "check": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "failure_count": len(self.failures),
            "failures": self.failures[:20],
        }


def load_configs(config_dir: Path = CONFIG_DIR) -> dict[str, dict[str, Any]]:
    return {
        subset: json.loads((config_dir / f"{subset}.json").read_text(encoding="utf-8"))
        for subset in SUBSETS
    }


def environment_for(config: dict[str, Any]) -> SupportToolEnvironment:
    agent = json.loads((ROOT / config["agent_config"]).read_text(encoding="utf-8"))
    return SupportToolEnvironment.from_files(
        records_path=ROOT / agent["support_records"],
        knowledge_path=ROOT / agent["knowledge_base"],
        available_actions=frozenset(READ_ONLY_TOOLS),
    )


def _requirement_results(
    environment: SupportToolEnvironment,
    requirements: list[dict[str, Any]],
    injection: dict[str, Any] | None,
) -> list[tuple[str, dict[str, Any]]]:
    results: list[tuple[str, dict[str, Any]]] = []
    for requirement in requirements:
        action = requirement["action"]
        if action == "search_support_kb":
            result = environment.search_support_kb(requirement.get("query_hint", ""), 3)
        else:
            result = getattr(environment, action)(*requirement["arguments"].values())
        if injection is not None:
            result = apply_tool_result_injection(
                action_name=action, result=json.loads(json.dumps(result)), injection=injection
            )
        results.append((action, result))
    return results


def visible_material(
    environment: SupportToolEnvironment, scenario: dict[str, Any], arm: str
) -> str:
    injection = scenario["arms"][arm].get("tool_result_injection")
    results = _requirement_results(
        environment, scenario["task"]["requirements"], injection
    )
    return json.dumps([result for _, result in results], ensure_ascii=False)


# ------------------------------------------------------------------ checks ---


def check_schema(configs: dict[str, dict[str, Any]]) -> CheckResult:
    failures = []
    for subset, config in configs.items():
        try:
            validate_agent_v3_experiment(config)
        except Exception as error:  # noqa: BLE001 - report, do not raise
            failures.append(f"{subset}: {error}")
    return CheckResult(
        "frozen_agent_v3_schema",
        not failures,
        f"validate_agent_v3_experiment over {len(configs)} configs",
        failures,
    )


def check_id_uniqueness(configs: dict[str, dict[str, Any]]) -> CheckResult:
    seen: dict[str, str] = {}
    failures = []
    for subset, config in configs.items():
        for scenario in config["scenarios"]:
            for key in ("base_task_id", "pair_group_id"):
                value = scenario[key]
                if value in seen and seen[value] != f"{subset}:{scenario['base_task_id']}:{key}":
                    if key == "pair_group_id" and seen[value].endswith("base_task_id") and (
                        seen[value].split(":")[1] == scenario["base_task_id"]
                    ):
                        continue
                    failures.append(f"{value} reused ({seen[value]} and {subset})")
                seen[value] = f"{subset}:{scenario['base_task_id']}:{key}"
    total = sum(len(config["scenarios"]) for config in configs.values())
    return CheckResult(
        "scenario_id_uniqueness",
        not failures,
        f"{total} scenarios across {len(configs)} subsets, ids unique in all of G",
        failures,
    )


def check_seed_uniqueness(configs: dict[str, dict[str, Any]]) -> CheckResult:
    failures = []
    for subset, config in configs.items():
        seeds: dict[int, str] = {}
        for scenario in config["scenarios"]:
            seed = scenario["sampling_seed"]
            if seed in seeds:
                failures.append(f"{subset}: seed {seed} on {seeds[seed]} and {scenario['base_task_id']}")
            seeds[seed] = scenario["base_task_id"]
    return CheckResult(
        "fixed_seed_per_scenario",
        not failures,
        "one fixed seed per scenario, unique inside each subset "
        "(G-medium deliberately repeats its G-dev partner's seed)",
        failures,
    )


def check_fixture_disjointness(configs: dict[str, dict[str, Any]]) -> CheckResult:
    used = {
        subset: {scenario["factory"]["fixture_id"] for scenario in config["scenarios"]}
        for subset, config in configs.items()
    }
    failures = []
    subsets = [s for s in SUBSETS if s != "g_medium"]
    for i, left in enumerate(subsets):
        for right in subsets[i + 1 :]:
            overlap = used[left] & used[right]
            if overlap:
                failures.append(f"{left} and {right} share fixtures {sorted(overlap)}")
    if not used["g_medium"] <= used["g_dev"]:
        failures.append("g_medium uses fixtures outside g_dev")
    return CheckResult(
        "fixture_disjointness",
        not failures,
        "; ".join(f"{subset}={sorted(codes)}" for subset, codes in used.items()),
        failures,
    )


def check_cell_balance(configs: dict[str, dict[str, Any]]) -> CheckResult:
    failures = []
    detail = []
    for subset in ("g_dev", "g_conf"):
        core = [
            scenario
            for scenario in configs[subset]["scenarios"]
            if scenario["factory"]["scenario_role"] == "core"
        ]
        cells: dict[str, int] = {}
        for scenario in core:
            cells[scenario["factory"]["cell_id"]] = cells.get(scenario["factory"]["cell_id"], 0) + 1
        expected = {
            f"{group}|{channel}|{tier}|slot{slot}"
            for group in DOMAIN_GROUPS
            for channel in CHANNELS
            for tier in TIERS
            for slot in (0, 1)
        }
        if set(cells) != expected:
            failures.append(f"{subset}: cell set differs from the 72-cell design")
        bad = {cell: count for cell, count in cells.items() if count != 2}
        if bad:
            failures.append(f"{subset}: cells not filled with exactly 2 scenarios: {bad}")
        if len(core) != CORE_CELL_COUNT * 2:
            failures.append(f"{subset}: {len(core)} core scenarios, expected 144")
        detail.append(f"{subset}: {len(cells)} cells x 2 = {len(core)}")
    return CheckResult("cell_balance_72", not failures, "; ".join(detail), failures)


def check_channel_shares(configs: dict[str, dict[str, Any]]) -> CheckResult:
    failures = []
    detail = []
    for subset in ("g_dev", "g_conf"):
        scenarios = [
            scenario
            for scenario in configs[subset]["scenarios"]
            if "attack" in scenario["factory"]["collected_arms"]
        ]
        counts: dict[str, int] = {}
        for scenario in scenarios:
            channel = scenario["arms"]["attack"]["channel"]
            counts[channel] = counts.get(channel, 0) + 1
        core = [s for s in scenarios if s["factory"]["scenario_role"] == "core"]
        core_counts: dict[str, int] = {}
        for scenario in core:
            channel = scenario["arms"]["attack"]["channel"]
            core_counts[channel] = core_counts.get(channel, 0) + 1
        if set(core_counts.values()) != {48}:
            failures.append(f"{subset}: core channels not 48/48/48: {core_counts}")
        share = counts.get("tool_output", 0) / max(1, len(scenarios))
        if subset == "g_dev" and not 0.20 <= share <= 0.30:
            failures.append(f"g_dev tool_output share {share:.3f} outside 1/4 +/- 0.05")
        detail.append(f"{subset}: core={core_counts} all={counts} tool_output={share:.3f}")
    return CheckResult("channel_shares", not failures, "; ".join(detail), failures)


def check_r_type_ratio(configs: dict[str, dict[str, Any]]) -> CheckResult:
    failures = []
    detail = []
    total_ratio = sum(R_TYPE_RATIO.values())
    for subset in ("g_fit", "g_cal", "g_conf"):
        counts: dict[str, int] = {}
        normals = [
            scenario
            for scenario in configs[subset]["scenarios"]
            if scenario["factory"]["scenario_role"] == "normal"
        ]
        for scenario in normals:
            r_type = scenario["factory"]["r_type"]
            counts[r_type] = counts.get(r_type, 0) + 1
        for r_type, weight in R_TYPE_RATIO.items():
            expected = len(normals) * weight / total_ratio
            if abs(counts.get(r_type, 0) - expected) > 1.5:
                failures.append(
                    f"{subset}: {r_type}={counts.get(r_type, 0)}, expected ~{expected:.1f}"
                )
        detail.append(f"{subset}: {counts}")
    return CheckResult(
        "r_type_ratio_2_2_3_3", not failures, "; ".join(detail), failures
    )


def check_material_answerability(configs: dict[str, dict[str, Any]]) -> CheckResult:
    failures = []
    checked = 0
    for subset, config in configs.items():
        environment = environment_for(config)
        for scenario in config["scenarios"]:
            for arm in scenario["factory"]["collected_arms"]:
                if arm == "attack" and scenario["arms"]["attack"].get("tool_result_injection"):
                    injection_arm = "attack"
                else:
                    injection_arm = arm
                results = _requirement_results(
                    environment,
                    scenario["task"]["requirements"],
                    scenario["arms"][injection_arm].get("tool_result_injection"),
                )
                for action, result in results:
                    if not result.get("ok"):
                        failures.append(
                            f"{subset}/{scenario['base_task_id']}: {action} returned {result}"
                        )
                material = normalize(json.dumps([r for _, r in results], ensure_ascii=False))
                for item in scenario["task"]["completion_evidence"]:
                    alternatives = item if isinstance(item, list) else [item]
                    if not any(normalize(value) in material for value in alternatives):
                        failures.append(
                            f"{subset}/{scenario['base_task_id']}/{arm}: "
                            f"completion evidence {alternatives!r} not in the tool material"
                        )
                for requirement in scenario["task"]["requirements"]:
                    for article_id in requirement.get("expected_article_ids", []):
                        if normalize(article_id) not in material:
                            failures.append(
                                f"{subset}/{scenario['base_task_id']}: expected article "
                                f"{article_id} not retrieved by the declared query hint"
                            )
                checked += 1
    return CheckResult(
        "completion_evidence_answerable_from_fixture",
        not failures,
        f"{checked} (scenario, collected arm) pairs re-derived by executing the "
        f"read-only tools against the fixture",
        failures,
    )


def check_session_material(configs: dict[str, dict[str, Any]]) -> CheckResult:
    config = configs["g_session"]
    environment = environment_for(config)
    failures = []
    turns_checked = 0
    for scenario in config["scenarios"]:
        turns = scenario["factory"]["session_turns"]
        if not 3 <= len(turns) <= 5:
            failures.append(f"{scenario['base_task_id']}: {len(turns)} turns, expected 3-5")
        for turn in turns:
            results = _requirement_results(environment, turn["requirements"], None)
            material = normalize(json.dumps([r for _, r in results], ensure_ascii=False))
            for item in turn["completion_evidence"]:
                alternatives = item if isinstance(item, list) else [item]
                if not any(normalize(value) in material for value in alternatives):
                    failures.append(
                        f"{scenario['base_task_id']} turn {turn['turn_index']}: "
                        f"{alternatives!r} not answerable"
                    )
            turns_checked += 1
        first = turns[0]
        if first["user_message"] != scenario["task"]["user_request"]:
            failures.append(f"{scenario['base_task_id']}: turn 1 differs from task.user_request")
        second = turns[1]
        collected = scenario["factory"]["collected_arms"][0]
        if second["user_message"] != scenario["arms"][collected]["clarification_response"]:
            failures.append(
                f"{scenario['base_task_id']}: turn 2 differs from the collected arm's "
                f"clarification_response"
            )
    return CheckResult(
        "session_turns_answerable",
        not failures,
        f"{len(config['scenarios'])} sessions, {turns_checked} turns; turns 1-2 match "
        f"the frozen two-turn fields",
        failures,
    )


def check_dates(configs: dict[str, dict[str, Any]]) -> CheckResult:
    from datetime import date

    failures = []
    records_checked = 0
    seen_files: set[Path] = set()
    for config in configs.values():
        agent = json.loads((ROOT / config["agent_config"]).read_text(encoding="utf-8"))
        path = ROOT / agent["support_records"]
        if path in seen_files:
            continue
        seen_files.add(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        for kind in ("orders", "returns", "support_cases", "warranties", "subscriptions"):
            for record_id, record in payload[kind].items():
                if "record_as_of" not in record:
                    failures.append(f"{record_id}: no record_as_of")
                for key, value in record.items():
                    if value is None or not isinstance(value, str):
                        continue
                    if _DATE_LIKE_FIELD.search(key) and not _ISO_DATE.match(value):
                        failures.append(f"{record_id}.{key} = {value!r} is not ISO 8601")
                records_checked += 1
    for subset, config in configs.items():
        for scenario in config["scenarios"]:
            for item in scenario["task"]["completion_evidence"]:
                if not isinstance(item, list):
                    continue
                try:
                    parsed = {date.fromisoformat(item[0])}
                except ValueError:
                    continue
                for alternative in item[1:]:
                    for fmt in ("%B %d, %Y", "%d %B %Y"):
                        try:
                            from datetime import datetime

                            parsed.add(datetime.strptime(alternative, fmt).date())
                            break
                        except ValueError:
                            continue
                if len(parsed) != 1:
                    failures.append(
                        f"{subset}/{scenario['base_task_id']}: date alternatives {item} disagree"
                    )
    return CheckResult(
        "iso_dates_with_as_of",
        not failures,
        f"{records_checked} records: every date field ISO 8601, every record carries "
        f"record_as_of; date evidence alternatives denote one day",
        failures,
    )


def check_identifier_namespaces(configs: dict[str, dict[str, Any]]) -> CheckResult:
    failures = []
    owner: dict[str, str] = {}
    numbers: dict[str, str] = {}
    seen_files: set[Path] = set()
    for config in configs.values():
        agent = json.loads((ROOT / config["agent_config"]).read_text(encoding="utf-8"))
        records_path = ROOT / agent["support_records"]
        kb_path = ROOT / agent["knowledge_base"]
        if records_path in seen_files:
            continue
        seen_files.add(records_path)
        payload = json.loads(records_path.read_text(encoding="utf-8"))
        for kind in ("orders", "returns", "support_cases", "warranties", "subscriptions"):
            for record_id, record in payload[kind].items():
                if record_id in owner:
                    failures.append(f"{record_id} appears in {owner[record_id]} and {kind}")
                owner[record_id] = kind
                digits = record_id.split("-")[-1]
                if digits in numbers and numbers[digits] != kind:
                    failures.append(
                        f"identifier number {digits} used by {numbers[digits]} and {kind}"
                    )
                numbers[digits] = kind
                for key in ("carrier_reference", "serial_number", "pickup_code"):
                    value = record.get(key)
                    if isinstance(value, str):
                        tail = value.split("-")[-1]
                        if tail in numbers and numbers[tail] != key:
                            failures.append(
                                f"identifier number {tail} used by {numbers[tail]} and {key}"
                            )
                        numbers[tail] = key
        articles = json.loads(kb_path.read_text(encoding="utf-8"))["articles"]
        for article in articles:
            if article["article_id"] in owner:
                failures.append(f"{article['article_id']} collides with a record id")
            owner[article["article_id"]] = "article"
    return CheckResult(
        "identifier_namespaces_disjoint",
        not failures,
        f"{len(owner)} identifiers across all fixtures; no number is reused by a "
        f"second entity kind and no article id collides with a record id",
        failures,
    )


def _injected_text(scenario: dict[str, Any], arm: str) -> str | None:
    payload = scenario["arms"][arm]
    channel = scenario["arms"]["attack"]["channel"]
    if channel == "tool_output":
        injection = payload.get("tool_result_injection")
        return None if injection is None else injection["content"]
    if channel == "direct_user":
        return payload.get("user_request")
    return payload.get("clarification_response")


def check_tier_wordings(configs: dict[str, dict[str, Any]]) -> CheckResult:
    by_family = {task.family: task for task in ATTACK_TASKS}
    failures = []
    checked = 0
    for subset, config in configs.items():
        for scenario in config["scenarios"]:
            factory = scenario["factory"]
            if "attack" not in factory["collected_arms"]:
                continue
            attack = by_family[scenario["arms"]["attack"]["attack_family_id"]]
            suffix = factory["marker_suffix"]
            tier = scenario["wording_tier"]
            channel = scenario["arms"]["attack"]["channel"]
            if subset == "g_session":
                turn = factory["session_turns"][factory["injection_turn_index"] - 1]
                actual = turn["user_message"]
                prefix = f"{turn['user_message_clean']} "
            elif channel == "tool_output":
                actual = scenario["arms"]["attack"]["tool_result_injection"]["content"]
                prefix = TOOL_OUTPUT_PREFIX
            elif channel == "direct_user":
                actual = scenario["arms"]["attack"]["user_request"]
                prefix = f"{factory['routine_user_request']} "
            else:
                actual = scenario["arms"]["attack"]["clarification_response"]
                prefix = f"{scenario['arms']['clean']['clarification_response']} "
            expected = tier_text(attack, suffix, tier, prefix=prefix)
            if actual != expected:
                failures.append(f"{subset}/{scenario['base_task_id']}: tier {tier} text differs")
            checked += 1
    return CheckResult(
        "tier_wordings_are_the_frozen_ones",
        not failures,
        f"{checked} attack arms re-derived from "
        f"scripts/research_v3/pilot_build_batch.py::_variant_texts and compared byte "
        f"for byte",
        failures,
    )


def check_markers_unique(configs: dict[str, dict[str, Any]]) -> CheckResult:
    seen: dict[str, str] = {}
    failures = []
    for subset, config in configs.items():
        if subset == "g_medium":
            # a paired re-run of the same scenario: identical markers are required
            continue
        for scenario in config["scenarios"]:
            for marker in scenario["arms"]["attack"]["attack_goal"]["required_substrings"]:
                key = normalize(marker)
                if key in seen:
                    failures.append(f"{marker} on {seen[key]} and {subset}/{scenario['base_task_id']}")
                seen[key] = f"{subset}/{scenario['base_task_id']}"
    source = {s["base_task_id"]: s for s in configs["g_dev"]["scenarios"]}
    for scenario in configs["g_medium"]["scenarios"]:
        origin = source[scenario["factory"]["source_scenario_id"]]
        if (
            scenario["arms"]["attack"]["attack_goal"]["required_substrings"]
            != origin["arms"]["attack"]["attack_goal"]["required_substrings"]
        ):
            failures.append(
                f"g_medium/{scenario['base_task_id']}: markers differ from the G-dev partner"
            )
    return CheckResult(
        "markers_unique_per_scenario",
        not failures,
        f"{len(seen)} distinct required-substring markers over the five independent "
        f"subsets; G-medium repeats its G-dev partner's markers by construction",
        failures,
    )


def check_benign_and_refusal_arms(configs: dict[str, dict[str, Any]]) -> CheckResult:
    failures = []
    counts = {"benign_lexical": 0, "legitimate_refusal": 0}
    for scenario in configs["g_dev"]["scenarios"]:
        role = scenario["factory"]["scenario_role"]
        if role in counts:
            counts[role] += 1
    for role, count in counts.items():
        if count < 24:
            failures.append(f"g_dev has {count} {role} scenarios, need >= 24")
    for subset, config in configs.items():
        for scenario in config["scenarios"]:
            factory = scenario["factory"]
            benign = scenario["arms"]["benign_control"]
            if "benign_control" in factory["collected_arms"]:
                text = (
                    benign.get("user_request")
                    or benign.get("clarification_response")
                    or benign.get("tool_result_injection", {}).get("content", "")
                )
                if not text:
                    failures.append(f"{subset}/{scenario['base_task_id']}: empty benign control")
                lowered = text.lower()
                for banned in ("you should not", "do not create", "please write", "must "):
                    if banned in lowered:
                        failures.append(
                            f"{subset}/{scenario['base_task_id']}: benign control is "
                            f"instructional ({banned!r})"
                        )
            if factory["scenario_role"] == "legitimate_refusal":
                request = scenario["task"]["user_request"]
                markers = scenario["arms"]["attack"]["attack_goal"]["required_substrings"]
                if any(marker in request for marker in markers):
                    failures.append(
                        f"{subset}/{scenario['base_task_id']}: legitimate refusal carries "
                        f"a marker contract"
                    )
                if "SUPPORT PLATFORM NOTICE" in request or "response_format_required" in request:
                    failures.append(
                        f"{subset}/{scenario['base_task_id']}: legitimate refusal carries "
                        f"an injection frame"
                    )
            if factory["scenario_role"] == "benign_lexical":
                decoy = factory["lexical_decoy"].lower()
                if decoy not in scenario["task"]["user_request"].lower():
                    failures.append(
                        f"{subset}/{scenario['base_task_id']}: lexical decoy {decoy!r} missing"
                    )
    return CheckResult(
        "normal_variants",
        not failures,
        f"g_dev benign_lexical={counts['benign_lexical']}, "
        f"legitimate_refusal={counts['legitimate_refusal']}; benign controls are "
        f"non-instructional mentions",
        failures,
    )


def check_held_out_workflow(configs: dict[str, dict[str, Any]]) -> CheckResult:
    failures = []
    conf_type = ":".join(HELD_OUT_CONF_ONLY)
    dev_type = ":".join(HELD_OUT_DEV_ONLY)
    counts: dict[str, dict[str, int]] = {}
    for subset, config in configs.items():
        found: dict[str, int] = {}
        for scenario in config["scenarios"]:
            workflow = scenario["factory"]["workflow_type"]
            if workflow in (conf_type, dev_type):
                found[workflow] = found.get(workflow, 0) + 1
        counts[subset] = found
        if conf_type in found and subset != "g_conf":
            failures.append(f"{conf_type} appears in {subset}, it is G-conf only")
        if dev_type in found and subset not in {"g_dev", "g_medium"}:
            failures.append(f"{dev_type} appears in {subset}, it is G-dev only")
    if conf_type not in counts["g_conf"]:
        failures.append(f"{conf_type} is missing from g_conf")
    if dev_type not in counts["g_dev"]:
        failures.append(f"{dev_type} is missing from g_dev")
    return CheckResult(
        "held_out_workflow_types",
        not failures,
        f"held out for G-conf: {conf_type} ({counts['g_conf'].get(conf_type, 0)} "
        f"scenarios); held out for G-dev: {dev_type} "
        f"({counts['g_dev'].get(dev_type, 0)} scenarios)",
        failures,
    )


def check_restricted_tools(configs: dict[str, dict[str, Any]]) -> CheckResult:
    failures = []
    agent_paths = {config["agent_config"] for config in configs.values()}
    for relative in sorted(agent_paths):
        agent = json.loads((ROOT / relative).read_text(encoding="utf-8"))
        classes = {name: spec["tool_class"] for name, spec in agent["tools"].items()}
        restricted = {name for name, value in classes.items() if value == "restricted"}
        if restricted != set(RESTRICTED_TOOLS):
            failures.append(f"{relative}: restricted stubs {sorted(restricted)}")
        if agent["runtime"]["tool_execution"] != "sandboxed_read_only":
            failures.append(f"{relative}: tool execution is not sandboxed_read_only")
    for subset, config in configs.items():
        for scenario in config["scenarios"]:
            goal = scenario["arms"]["attack"]["attack_goal"]
            if goal["type"] != "user_visible_text":
                failures.append(f"{subset}/{scenario['base_task_id']}: not a type-A goal")
            text = json.dumps(scenario["arms"], ensure_ascii=False).lower()
            for name in RESTRICTED_TOOLS:
                if name in text:
                    failures.append(
                        f"{subset}/{scenario['base_task_id']}: mentions restricted tool {name}"
                    )
    return CheckResult(
        "type_a_only_restricted_stubs_present",
        not failures,
        "every attack is a type-A content deliverable; the three restricted stubs "
        "stay in the agent schema and no scenario ever asks for them",
        failures,
    )


def check_collection_plan(configs: dict[str, dict[str, Any]]) -> CheckResult:
    failures = []
    detail = []
    for subset, config in configs.items():
        ids = {scenario["base_task_id"] for scenario in config["scenarios"]}
        planned: dict[str, list[str]] = {}
        traces = 0
        for group in config["collection_plan"]:
            unknown = set(group["scenario_ids"]) - ids
            if unknown:
                failures.append(f"{subset}/{group['group']}: unknown scenarios {sorted(unknown)[:3]}")
            if len(group["scenario_ids"]) != group["scenario_count"]:
                failures.append(f"{subset}/{group['group']}: scenario_count mismatch")
            if group["trace_count"] != len(group["arms"]) * group["scenario_count"]:
                failures.append(f"{subset}/{group['group']}: trace_count mismatch")
            traces += group["trace_count"]
            for scenario_id in group["scenario_ids"]:
                planned.setdefault(scenario_id, []).extend(group["arms"])
        for scenario in config["scenarios"]:
            declared = scenario["factory"]["collected_arms"]
            if sorted(planned.get(scenario["base_task_id"], [])) != sorted(declared):
                failures.append(
                    f"{subset}/{scenario['base_task_id']}: collection plan and "
                    f"collected_arms disagree"
                )
        if traces != config["allocation"]["trace_count"]:
            failures.append(f"{subset}: allocation trace_count disagrees with the plan")
        detail.append(f"{subset}={traces}")
    return CheckResult(
        "collection_plan_consistent",
        not failures,
        "planned traces " + ", ".join(detail),
        failures,
    )


def check_medium_pairing(configs: dict[str, dict[str, Any]]) -> CheckResult:
    source = {
        scenario["base_task_id"]: scenario for scenario in configs["g_dev"]["scenarios"]
    }
    failures = []
    for scenario in configs["g_medium"]["scenarios"]:
        origin = source.get(scenario["factory"].get("source_scenario_id"))
        if origin is None:
            failures.append(f"{scenario['base_task_id']}: source scenario not in g_dev")
            continue
        for key in ("task", "arms", "manual_review_markers", "sampling_seed", "domain_group",
                    "wording_tier", "routine_template_id"):
            if scenario[key] != origin[key]:
                failures.append(f"{scenario['base_task_id']}: {key} differs from its G-dev partner")
        if origin["factory"]["scenario_role"] != "core":
            failures.append(f"{scenario['base_task_id']}: partner is not a core scenario")
    model = configs["g_medium"]["model_config"]
    payload = json.loads((ROOT / model).read_text(encoding="utf-8"))
    if payload["chat_template_kwargs"].get("reasoning_effort") != "medium":
        failures.append("g_medium model config does not set reasoning_effort=medium")
    return CheckResult(
        "medium_paired_rerun",
        not failures,
        f"{len(configs['g_medium']['scenarios'])} scenarios re-run at reasoning "
        f"effort medium with identical text, fixture and seed",
        failures,
    )


def check_manifest(config_dir: Path = CONFIG_DIR) -> CheckResult:
    manifest = json.loads((config_dir / "manifest.json").read_text(encoding="utf-8"))
    from .build import sha256_file

    failures = []
    for relative, digest in manifest["files"].items():
        path = ROOT / relative
        if not path.exists():
            failures.append(f"{relative} missing")
            continue
        actual = sha256_file(path)
        if actual != digest:
            failures.append(f"{relative}: sha256 {actual} != manifest {digest}")
    return CheckResult(
        "manifest_sha256",
        not failures,
        f"{len(manifest['files'])} files hashed in configs/dataset_g/manifest.json",
        failures,
    )


def check_prompt_budget(
    configs: dict[str, dict[str, Any]], tokenizer: Any
) -> CheckResult:
    failures = []
    worst_material = 0
    worst_material_id = ""
    worst_total = 0
    worst_total_id = ""
    for subset, config in configs.items():
        agent = json.loads((ROOT / config["agent_config"]).read_text(encoding="utf-8"))
        environment = environment_for(config)
        overhead = len(
            tokenizer(agent["system_prompt"], add_special_tokens=False)["input_ids"]
        ) + len(
            tokenizer(
                json.dumps(agent["tools"], ensure_ascii=False), add_special_tokens=False
            )["input_ids"]
        )
        for scenario in config["scenarios"]:
            for arm in scenario["factory"]["collected_arms"]:
                if subset == "g_session":
                    turns = [
                        turn["user_message"]
                        for turn in scenario["factory"]["session_turns"]
                    ]
                    results = [
                        item
                        for turn in scenario["factory"]["session_turns"]
                        for item in _requirement_results(
                            environment, turn["requirements"], None
                        )
                    ]
                else:
                    turns = user_turns(scenario, arm)
                    results = _requirement_results(
                        environment,
                        scenario["task"]["requirements"],
                        scenario["arms"][arm].get("tool_result_injection"),
                    )
                text = "\n".join(turns) + "\n" + json.dumps(
                    [result for _, result in results], ensure_ascii=False
                )
                material = overhead + len(
                    tokenizer(text, add_special_tokens=False)["input_ids"]
                )
                total = material + MAX_AGENT_STEPS * MAX_NEW_TOKENS_PER_TURN
                label = f"{subset}/{scenario['base_task_id']}/{arm}"
                if material > worst_material:
                    worst_material, worst_material_id = material, label
                if total > worst_total:
                    worst_total, worst_total_id = total, label
                if material > PROMPT_MATERIAL_TOKEN_BUDGET:
                    failures.append(
                        f"{label}: {material} material tokens over the "
                        f"{PROMPT_MATERIAL_TOKEN_BUDGET} budget"
                    )
                if total > PROMPT_TOKEN_BUDGET or total > MODEL_CONTEXT_TOKENS:
                    failures.append(
                        f"{label}: {total} worst-case tokens over the "
                        f"{PROMPT_TOKEN_BUDGET} budget"
                    )
    return CheckResult(
        "prompt_token_budget",
        not failures,
        f"worst prompt material {worst_material} tokens ({worst_material_id}); worst "
        f"case with the whole {MAX_AGENT_STEPS} x {MAX_NEW_TOKENS_PER_TURN} generation "
        f"allowance {worst_total} tokens ({worst_total_id}); budgets "
        f"{PROMPT_MATERIAL_TOKEN_BUDGET} / {PROMPT_TOKEN_BUDGET}, model context "
        f"{MODEL_CONTEXT_TOKENS}",
        failures,
    )


CHECKS: tuple[Callable[[dict[str, dict[str, Any]]], CheckResult], ...] = (
    check_schema,
    check_id_uniqueness,
    check_seed_uniqueness,
    check_fixture_disjointness,
    check_cell_balance,
    check_channel_shares,
    check_r_type_ratio,
    check_material_answerability,
    check_session_material,
    check_dates,
    check_identifier_namespaces,
    check_tier_wordings,
    check_markers_unique,
    check_benign_and_refusal_arms,
    check_held_out_workflow,
    check_restricted_tools,
    check_collection_plan,
    check_medium_pairing,
)


def run_all(
    config_dir: Path = CONFIG_DIR, *, tokenizer: Any | None = None
) -> list[CheckResult]:
    configs = load_configs(config_dir)
    results = [check(configs) for check in CHECKS]
    results.append(check_manifest(config_dir))
    if tokenizer is not None:
        results.append(check_prompt_budget(configs, tokenizer))
    return results
