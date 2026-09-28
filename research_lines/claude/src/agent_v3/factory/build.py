"""Assemble and write the dataset G fixtures, agent configs and subset configs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .allocation import (
    DATASET_ROLE,
    MarkerCounter,
    SubsetPlan,
    build_g_cal,
    build_g_conf,
    build_g_conf2,
    build_g_dev,
    build_g_fit,
    build_g_medium,
)
from .constants import (
    AS_OF_DATE,
    FACTORY_VERSION,
    MAX_AGENT_STEPS,
    MAX_NEW_TOKENS_PER_TURN,
    MODEL_CONFIG,
    MODEL_CONFIG_MEDIUM,
    PHASE,
    SCHEMA_VERSION,
    SOURCE_AGENT_CONFIG,
    TEMPERATURE,
    TIER_DESCRIPTION,
    TOP_P,
)
from .fixtures import build_fixture
from .merchants import merchants_for
from .sessions import build_g_session
from .tasks import FixtureContext


ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = ROOT / "configs" / "dataset_g"
FIXTURE_DIR = OUTPUT_DIR / "fixtures"

SUBSET_ORDER = ("g_fit", "g_cal", "g_dev", "g_session", "g_medium", "g_conf", "g_conf2")
FIXTURE_SUBSETS = ("g_fit", "g_cal", "g_dev", "g_session", "g_conf", "g_conf2")

#: Which subset "owns" each generated file, for the append-only ``only=`` mode of
#: :func:`build_all`.  The manifest is owned by nobody: it always names every file.
def file_owner(relative_name: str) -> str | None:
    """The subset a generated file belongs to, or ``None`` for the manifest."""

    if relative_name == "manifest.json":
        return None
    if relative_name == "model_gpt_oss_20b_medium.json":
        return "g_medium"
    stem = relative_name.removesuffix(".json")
    for prefix in ("fixtures/kb_", "fixtures/records_", "agent_"):
        if stem.startswith(prefix):
            return stem[len(prefix) :]
    return stem

PURPOSE = {
    "g_fit": (
        "Normal reference pool (design section 5, G-fit): fits the per-channel "
        "normal routing reference. Clean and benign_control arms only; no attack "
        "arm is collected."
    ),
    "g_cal": (
        "Deployment-side calibration pool (design section 5, G-cal): whole-pool "
        "conformal calibration, scenario- and fixture-disjoint from G-fit."
    ),
    "g_dev": (
        "Development evaluation (design sections 3.3 and 15.1): 144 core scenarios "
        "filling the 72-cell balanced design at three arms each, a 120-scenario "
        "attack-only supplement layer, and the two difficult normal variants "
        "benign_lexical and legitimate_refusal."
    ),
    "g_session": (
        "Session-level evaluation (design section 5, G-session): 100 sessions of "
        "3-5 turns, 70 normal and 30 with a T1 user-side injection in turn 2 or 3."
    ),
    "g_medium": (
        "Analysis-length sensitivity (design section 5, G-medium): the same 40 "
        "G-dev core scenarios, same fixtures, same seeds, re-run at reasoning "
        "effort medium. Exempt from the fixture-disjointness rule by design."
    ),
    "g_conf": (
        "Sealed confirmation batch (design section 5, G-conf, 720 traces): its own "
        "fixtures, 160 attack scenarios at three arms and 120 normal scenarios at "
        "two, including the held-out workflow type that appears nowhere else."
    ),
    "g_conf2": (
        "Second sealed confirmation batch (G-conf-2, 720 traces), built by an "
        "append-only extension of the frozen factory: the same 280-scenario / "
        "720-trace allocation as G-conf over three new fixtures appended at the end "
        "of the merchant table, a new id prefix (g-cf2) and a new seed block, so "
        "every earlier subset's files are byte identical. See "
        "docs/research_v4/g_conf2_build_log.md."
    ),
}

RUN_PROTOCOL = (
    "Collect exactly the (scenario, arm) pairs in collection_plan. Each group is "
    "one run_agent_v3.py invocation with --arms <group arms> and one --scenario "
    "per scenario id (scripts/research_v4/factory_run_plan.py prints the argv). "
    "The top-level arms field is the union over the groups and is present so the "
    "frozen schema validates; running the config without the plan would collect "
    "arms this subset does not budget for."
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def render_json(value: Any) -> bytes:
    """The exact bytes :func:`write_json` would write."""

    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return sha256_file(path)


class UnexpectedRewrite(RuntimeError):
    """A file outside ``only=`` would have changed: the build is not append-only."""


# ------------------------------------------------------------- fixtures -----


def fixture_payloads(subset: str) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    articles: list[dict[str, Any]] = []
    records: dict[str, dict[str, Any]] = {
        "orders": {},
        "returns": {},
        "support_cases": {},
        "warranties": {},
        "subscriptions": {},
    }
    summary: list[dict[str, Any]] = []
    for merchant in merchants_for(subset):
        fixture = build_fixture(merchant)
        articles.extend(fixture["articles"])
        for kind, collection in fixture["records"].items():
            records[kind].update(collection)
        summary.append(
            {
                "fixture_id": merchant.code,
                "brand": merchant.brand,
                "brand_token": merchant.brand_token,
                "trade": merchant.trade,
                "article_count": len(fixture["articles"]),
                "record_counts": {
                    kind: len(collection) for kind, collection in fixture["records"].items()
                },
                "subject_phrases": fixture["merchant"]["subject_phrases"],
                "policy": fixture["merchant"]["policy"],
            }
        )
    knowledge = {
        "schema_version": 1,
        "kb_version": f"dataset-g-{subset.replace('_', '-')}-kb-{AS_OF_DATE}",
        "_note": (
            "Generated by src/agent_v3/factory. The model only ever sees article_id, "
            "title, revision, topics and content; the _factory block is annotation "
            "material (numbered-fact index and evidence substrings) and is not part "
            "of any tool result."
        ),
        "articles": articles,
    }
    support = {
        "schema_version": 1,
        "records_version": f"dataset-g-{subset.replace('_', '-')}-records-{AS_OF_DATE}",
        "_note": (
            "Every record carries record_as_of; every date field is ISO 8601 and is "
            "stated as of that date. Identifier numbers are unique across entity "
            "kinds and merchants."
        ),
        **records,
    }
    return knowledge, support, summary


def agent_config(subset: str) -> dict[str, Any]:
    source = json.loads((ROOT / SOURCE_AGENT_CONFIG).read_text(encoding="utf-8"))
    config = dict(source)
    config["knowledge_base"] = f"configs/dataset_g/fixtures/kb_{subset}.json"
    config["support_records"] = f"configs/dataset_g/fixtures/records_{subset}.json"
    config["_derived_from"] = SOURCE_AGENT_CONFIG
    config["_note"] = (
        "System prompt, tool schemas (read-only, escalation and the three restricted "
        "stubs) and runtime are byte-identical to configs/agent_v3_support.json; only "
        "the fixture paths differ, so every dataset G subset shares one routine "
        "manifold and one restricted-tool boundary."
    )
    return config


def medium_model_config() -> dict[str, Any]:
    source = json.loads((ROOT / MODEL_CONFIG).read_text(encoding="utf-8"))
    config = dict(source)
    config["chat_template_kwargs"] = dict(source["chat_template_kwargs"])
    config["chat_template_kwargs"]["reasoning_effort"] = "medium"
    config["_derived_from"] = MODEL_CONFIG
    config["_note"] = (
        "G-medium only (design section 5): identical to the frozen pilot model "
        "config except reasoning_effort, which is the single manipulated variable "
        "of the paired re-run."
    )
    return config


# -------------------------------------------------------------- configs -----


def allocation_summary(plan: SubsetPlan) -> dict[str, Any]:
    roles: dict[str, int] = {}
    variants: dict[str, int] = {}
    r_types: dict[str, int] = {}
    channels: dict[str, int] = {}
    tiers: dict[str, int] = {}
    groups: dict[str, int] = {}
    cells: dict[str, int] = {}
    workflow_types: dict[str, int] = {}
    fixtures: dict[str, int] = {}
    for scenario in plan.scenarios:
        factory = scenario["factory"]
        roles[factory["scenario_role"]] = roles.get(factory["scenario_role"], 0) + 1
        variants[factory["normal_variant"]] = variants.get(factory["normal_variant"], 0) + 1
        r_types[factory["r_type"]] = r_types.get(factory["r_type"], 0) + 1
        workflow_types[factory["workflow_type"]] = (
            workflow_types.get(factory["workflow_type"], 0) + 1
        )
        fixtures[factory["fixture_id"]] = fixtures.get(factory["fixture_id"], 0) + 1
        if "attack" in factory["collected_arms"]:
            channel = scenario["arms"]["attack"]["channel"]
            channels[channel] = channels.get(channel, 0) + 1
            tiers[scenario["wording_tier"]] = tiers.get(scenario["wording_tier"], 0) + 1
            groups[scenario["domain_group"]] = groups.get(scenario["domain_group"], 0) + 1
            cells[factory["cell_id"]] = cells.get(factory["cell_id"], 0) + 1
    return {
        "scenario_count": len(plan.scenarios),
        "trace_count": plan.trace_count,
        "scenarios_by_role": dict(sorted(roles.items())),
        "scenarios_by_variant": dict(sorted(variants.items())),
        "scenarios_by_r_type": dict(sorted(r_types.items())),
        "scenarios_by_workflow_type": dict(sorted(workflow_types.items())),
        "scenarios_by_fixture": dict(sorted(fixtures.items())),
        "attack_scenarios_by_channel": dict(sorted(channels.items())),
        "attack_scenarios_by_tier": dict(sorted(tiers.items())),
        "attack_scenarios_by_domain_group": dict(sorted(groups.items())),
        "attack_cells_filled": len(cells),
        "attack_scenarios_per_cell": dict(sorted(cells.items())),
    }


def subset_config(
    plan: SubsetPlan,
    *,
    fixture_summary: list[dict[str, Any]],
    fixture_hashes: dict[str, str],
) -> dict[str, Any]:
    subset = plan.subset
    fixture_subset = "g_dev" if subset == "g_medium" else subset
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "experiment_id": f"dataset_{subset}",
        "agent_config": f"configs/dataset_g/agent_{fixture_subset}.json",
        "model_config": MODEL_CONFIG_MEDIUM if subset == "g_medium" else MODEL_CONFIG,
        "dataset_role": DATASET_ROLE[subset],
        "purpose": PURPOSE[subset],
        "design_document": "docs/research_v4/agent_v3_dataset_design.md",
        "factory": {
            "version": FACTORY_VERSION,
            "builder": "scripts/research_v4/factory_build_dataset_g.py",
            "validator": "scripts/research_v4/factory_validate.py",
            "as_of_date": AS_OF_DATE,
            "reproducible": (
                "every date and seed is a literal; rebuilding the config reproduces "
                "it byte for byte"
            ),
            "type_b_attacks": (
                "not generated (user decision, design section 14.2); the restricted "
                "tool stubs stay in the agent schema in every arm"
            ),
        },
        "fixture_ids": [item["fixture_id"] for item in fixture_summary],
        "fixtures": fixture_summary,
        "fixture_files": {
            "knowledge_base": f"configs/dataset_g/fixtures/kb_{fixture_subset}.json",
            "knowledge_base_sha256": fixture_hashes[f"kb_{fixture_subset}"],
            "support_records": f"configs/dataset_g/fixtures/records_{fixture_subset}.json",
            "support_records_sha256": fixture_hashes[f"records_{fixture_subset}"],
            "agent_config_sha256": fixture_hashes[f"agent_{fixture_subset}"],
        },
        "manifest": "configs/dataset_g/manifest.json",
        "decoding": {
            "strategy": "sample",
            "temperature": TEMPERATURE,
            "top_p": TOP_P,
            "max_new_tokens_per_turn": MAX_NEW_TOKENS_PER_TURN,
            "max_agent_steps": MAX_AGENT_STEPS,
            "assistant_protocol": "harmony_tool_calls",
        },
        "arms": plan.arms,
        "run_protocol": RUN_PROTOCOL,
        "collection_plan": plan.collection_groups,
        "allocation": allocation_summary(plan),
        "wording_tiers": TIER_DESCRIPTION,
        "suggested_output_root": f"artifacts/agent_v2/dataset_g/{subset}",
        "scenarios": plan.scenarios,
    }


def build_plans() -> dict[str, SubsetPlan]:
    markers = MarkerCounter()
    plans: dict[str, SubsetPlan] = {}
    plans["g_fit"] = build_g_fit(markers)
    plans["g_cal"] = build_g_cal(markers)
    plans["g_dev"] = build_g_dev(markers)
    plans["g_session"] = build_g_session(markers)
    plans["g_medium"] = build_g_medium(plans["g_dev"], markers)
    plans["g_conf"] = build_g_conf(markers)
    # APPEND-ONLY: g_conf2 is built LAST so every earlier subset draws exactly the
    # marker suffixes it already has from the shared counter.
    plans["g_conf2"] = build_g_conf2(markers)
    return plans


def build_all(
    output_dir: Path = OUTPUT_DIR, *, only: tuple[str, ...] | None = None
) -> dict[str, Any]:
    """Write the dataset G configs. ``only`` restricts *writing* to those subsets.

    With ``only=None`` (the default) this is the original whole-dataset build.

    With ``only=("g_conf2",)`` every file is still rendered in memory, but a file
    owned by another subset is NOT written: instead its bytes on disk are compared
    against what the build would have produced, and :class:`UnexpectedRewrite` is
    raised if they differ.  That makes an append-only extension *provably* additive
    -- the frozen files are verified rather than touched -- while the manifest still
    names every file (it gains the new subset's entries and nothing else changes).
    """

    keep = None if only is None else set(only)
    output_dir.mkdir(parents=True, exist_ok=True)
    fixture_dir = output_dir / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    verified: list[str] = []

    def emit(relative: str, value: Any) -> str:
        path = output_dir / relative
        owner = file_owner(relative)
        if keep is None or owner is None or owner in keep:
            written.append(relative)
            return write_json(path, value)
        expected = render_json(value)
        if not path.is_file():
            raise UnexpectedRewrite(f"{relative} is outside only={sorted(keep)} but absent")
        actual = path.read_bytes()
        if actual != expected:
            raise UnexpectedRewrite(
                f"{relative} is outside only={sorted(keep)} but the build would change it"
            )
        verified.append(relative)
        return hashlib.sha256(actual).hexdigest()

    hashes: dict[str, str] = {}
    summaries: dict[str, list[dict[str, Any]]] = {}
    for subset in FIXTURE_SUBSETS:
        knowledge, support, summary = fixture_payloads(subset)
        hashes[f"kb_{subset}"] = emit(f"fixtures/kb_{subset}.json", knowledge)
        hashes[f"records_{subset}"] = emit(f"fixtures/records_{subset}.json", support)
        hashes[f"agent_{subset}"] = emit(f"agent_{subset}.json", agent_config(subset))
        summaries[subset] = summary
    hashes["model_gpt_oss_20b_medium"] = emit(
        "model_gpt_oss_20b_medium.json", medium_model_config()
    )

    plans = build_plans()
    configs: dict[str, dict[str, Any]] = {}
    for subset in SUBSET_ORDER:
        fixture_subset = "g_dev" if subset == "g_medium" else subset
        config = subset_config(
            plans[subset],
            fixture_summary=summaries[fixture_subset],
            fixture_hashes=hashes,
        )
        hashes[subset] = emit(f"{subset}.json", config)
        configs[subset] = config

    manifest = {
        "manifest_version": 1,
        "factory_version": FACTORY_VERSION,
        "design_document": "docs/research_v4/agent_v3_dataset_design.md",
        "as_of_date": AS_OF_DATE,
        "note": (
            "sha256 of every generated dataset G file. Scenario text, fixtures, seeds "
            "and injection wordings are frozen by these digests (design section 15.3)."
        ),
        "totals": {
            "scenarios": sum(len(plan.scenarios) for plan in plans.values()),
            "planned_traces": sum(plan.trace_count for plan in plans.values()),
            "fixtures": sum(len(summaries[s]) for s in FIXTURE_SUBSETS),
        },
        "subsets": {
            subset: {
                "config": f"configs/dataset_g/{subset}.json",
                "sha256": hashes[subset],
                "dataset_role": DATASET_ROLE[subset],
                "scenario_count": len(plans[subset].scenarios),
                "planned_trace_count": plans[subset].trace_count,
                "fixture_ids": configs[subset]["fixture_ids"],
                "arms": plans[subset].arms,
            }
            for subset in SUBSET_ORDER
        },
        "files": {
            **{f"configs/dataset_g/{subset}.json": hashes[subset] for subset in SUBSET_ORDER},
            **{
                f"configs/dataset_g/fixtures/kb_{subset}.json": hashes[f"kb_{subset}"]
                for subset in FIXTURE_SUBSETS
            },
            **{
                f"configs/dataset_g/fixtures/records_{subset}.json": hashes[
                    f"records_{subset}"
                ]
                for subset in FIXTURE_SUBSETS
            },
            **{
                f"configs/dataset_g/agent_{subset}.json": hashes[f"agent_{subset}"]
                for subset in FIXTURE_SUBSETS
            },
            "configs/dataset_g/model_gpt_oss_20b_medium.json": hashes[
                "model_gpt_oss_20b_medium"
            ],
        },
    }
    write_json(output_dir / "manifest.json", manifest)
    written.append("manifest.json")
    return {
        "plans": plans,
        "configs": configs,
        "manifest": manifest,
        "written": written,
        "verified_unchanged": verified,
    }
