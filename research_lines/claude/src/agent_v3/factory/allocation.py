"""Per-subset scenario allocation for dataset G (design sections 5 and 15.1).

    subset      scenarios  collected traces  composition
    ---------   ---------  ----------------  -----------------------------------
    G-fit         150            300         normals, clean + benign_control
    G-cal         150            300         normals, disjoint fixtures/scenarios
    G-dev         312            600         144 core x 3 arms, 120 attack-only
                                             supplement, 24 benign_lexical,
                                             24 legitimate_refusal
    G-session     100            100         70 normal + 30 T1 user-side sessions
    G-medium       40            120         paired re-run of 40 G-dev core
    G-conf        280            720         160 x 3 arms + 120 normals x 2 arms
    G-conf-2      280            720         same shape as G-conf, own fixtures
                                             (append-only extension, see
                                             docs/research_v4/g_conf2_build_log.md)

The 72 cells are ``domain_group x channel x tier x target_slot``; the target slot
replaces the type-B factor of design section 3.3 (deferred by user decision
section 14.2) and keeps the cell count and the 2-per-cell minimum intact.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterator

from .assemble import LEXICAL_DECOYS, build_scenario
from .attacks import ATTACK_TASKS, ATTACK_TASKS_BY_CELL, AttackTask
from .constants import (
    CHANNELS,
    DOMAIN_GROUPS,
    HELD_OUT_CONF_ONLY,
    HELD_OUT_DEV_ONLY,
    SUBSET_ID_PREFIX,
    SUBSET_SEED_BASE,
    TARGET_SLOTS,
    TIERS,
    TOPICS,
)
from .merchants import merchants_for
from .tasks import FixtureContext, build_task


DATASET_ROLE = {
    "g_fit": "fit_normal_reference",
    "g_cal": "deployment_calibration",
    "g_dev": "development_evaluation",
    "g_session": "session_level_evaluation",
    "g_medium": "reasoning_effort_sensitivity_paired",
    "g_conf": "sealed_confirmation",
    "g_conf2": "sealed_confirmation",
}

#: Subsets that carry the G-conf-only held-out workflow type. G-conf-2 is a second
#: sealed confirmation batch of the same shape (docs/research_v4/g_conf2_build_log.md),
#: so ``R4:warranty`` is held out of the fit / calibration / development pools exactly
#: as before; it is now shared by the two confirmation batches and by nothing else.
CONFIRMATION_SUBSETS = ("g_conf", "g_conf2")

#: One block of ten positions realising R1:R2:R3:R4 = 2:2:3:3 (design 15.1).
R_TYPE_CYCLE = ("R1", "R2", "R3", "R4", "R3", "R4", "R1", "R2", "R3", "R4")


class MarkerCounter:
    """Global, build-order marker suffix so no two scenarios share a marker."""

    def __init__(self) -> None:
        self._next = 1

    def take(self) -> str:
        value = f"{self._next:04d}"
        self._next += 1
        return value


def allowed_topics(subset: str, r_type: str) -> tuple[str, ...]:
    """Topics for one R-type, honouring the two held-out workflow types."""

    topics = list(TOPICS)
    if r_type == HELD_OUT_CONF_ONLY[0]:
        if subset not in set(CONFIRMATION_SUBSETS) and HELD_OUT_CONF_ONLY[1] in topics:
            topics.remove(HELD_OUT_CONF_ONLY[1])
    if r_type == HELD_OUT_DEV_ONLY[0]:
        if subset not in {"g_dev", "g_medium"} and HELD_OUT_DEV_ONLY[1] in topics:
            topics.remove(HELD_OUT_DEV_ONLY[1])
    return tuple(topics)


def _r_type_sequence(count: int) -> list[str]:
    return [R_TYPE_CYCLE[i % len(R_TYPE_CYCLE)] for i in range(count)]


@dataclass
class SubsetPlan:
    subset: str
    scenarios: list[dict[str, Any]] = field(default_factory=list)
    collection_groups: list[dict[str, Any]] = field(default_factory=list)

    @property
    def trace_count(self) -> int:
        return sum(
            len(group["arms"]) * len(group["scenario_ids"])
            for group in self.collection_groups
        )

    @property
    def arms(self) -> list[str]:
        seen: list[str] = []
        for group in self.collection_groups:
            for arm in group["arms"]:
                if arm not in seen:
                    seen.append(arm)
        return [arm for arm in ("clean", "benign_control", "attack") if arm in seen]


class SubsetBuilder:
    def __init__(self, subset: str, markers: MarkerCounter) -> None:
        self.subset = subset
        self.markers = markers
        self.merchants = merchants_for(subset)
        self.contexts = [FixtureContext(merchant) for merchant in self.merchants]
        self.plan = SubsetPlan(subset=subset)
        self._index = 0

    def _next_id(self) -> tuple[str, int]:
        self._index += 1
        return (
            f"{SUBSET_ID_PREFIX[self.subset]}-{self._index:03d}",
            SUBSET_SEED_BASE[self.subset] + self._index,
        )

    def context(self, index: int) -> FixtureContext:
        return self.contexts[index % len(self.contexts)]

    def add(self, scenario: dict[str, Any]) -> dict[str, Any]:
        self.plan.scenarios.append(scenario)
        return scenario

    def group(self, name: str, arms: tuple[str, ...], scenarios: list[dict[str, Any]]) -> None:
        self.plan.collection_groups.append(
            {
                "group": name,
                "arms": list(arms),
                "scenario_count": len(scenarios),
                "trace_count": len(arms) * len(scenarios),
                "scenario_ids": [scenario["base_task_id"] for scenario in scenarios],
            }
        )


def _normal_scenarios(
    builder: SubsetBuilder,
    *,
    count: int,
    scenario_role: str,
    normal_variant: str | None,
    channel: str,
    collected_arms: tuple[str, ...],
) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    r_types = _r_type_sequence(count)
    topic_cursor: dict[str, int] = {}
    for position, r_type in enumerate(r_types):
        topics = allowed_topics(builder.subset, r_type)
        cursor = topic_cursor.get(r_type, 0)
        topic_cursor[r_type] = cursor + 1
        topic = topics[cursor % len(topics)]
        context = builder.context(position)
        task = build_task(context, r_type, topic, position)
        attack = ATTACK_TASKS[position % len(ATTACK_TASKS)]
        scenario_id, seed = builder._next_id()
        scenarios.append(
            builder.add(
                build_scenario(
                    scenario_id=scenario_id,
                    subset=builder.subset,
                    dataset_role=DATASET_ROLE[builder.subset],
                    context=context,
                    task=task,
                    seed=seed,
                    attack=attack,
                    marker_suffix=builder.markers.take(),
                    channel=channel,
                    tier="T0",
                    target_slot=attack.slot,
                    fold=position % 5,
                    collected_arms=collected_arms,
                    scenario_role=scenario_role,
                    cell_index=position,
                    normal_variant=normal_variant,
                    extra_factory=(
                        {
                            "lexical_decoy": LEXICAL_DECOYS[position % len(LEXICAL_DECOYS)][0],
                        }
                        if normal_variant == "benign_lexical"
                        else (
                            {
                                "off_domain_request": {
                                    "domain_group": attack.domain_group,
                                    "target_domain": attack.target_domain,
                                    "requested_task": attack.task,
                                },
                                "third_outcome_class": "legitimate_refusal",
                            }
                            if normal_variant == "legitimate_refusal"
                            else None
                        )
                    ),
                )
            )
        )
    return scenarios


def _core_cells() -> Iterator[tuple[str, str, str, int, int, AttackTask]]:
    """The 72 cells, two scenarios each, in a fixed order."""

    for domain_group in DOMAIN_GROUPS:
        for channel in CHANNELS:
            for tier in TIERS:
                for slot in TARGET_SLOTS:
                    tasks = ATTACK_TASKS_BY_CELL[(domain_group, slot)]
                    for k in range(2):
                        yield domain_group, channel, tier, slot, k, tasks[k]


def _core_scenarios(builder: SubsetBuilder, *, scenario_role: str) -> list[dict[str, Any]]:
    cells = list(_core_cells())
    r_types = _r_type_sequence(len(cells))
    scenarios: list[dict[str, Any]] = []
    topic_cursor: dict[str, int] = {}
    for position, (domain_group, channel, tier, slot, k, attack) in enumerate(cells):
        r_type = r_types[position]
        topics = allowed_topics(builder.subset, r_type)
        cursor = topic_cursor.get(r_type, 0)
        topic_cursor[r_type] = cursor + 1
        topic = topics[cursor % len(topics)]
        context = builder.context(position)
        task = build_task(context, r_type, topic, position)
        scenario_id, seed = builder._next_id()
        scenarios.append(
            builder.add(
                build_scenario(
                    scenario_id=scenario_id,
                    subset=builder.subset,
                    dataset_role=DATASET_ROLE[builder.subset],
                    context=context,
                    task=task,
                    seed=seed,
                    attack=attack,
                    marker_suffix=builder.markers.take(),
                    channel=channel,
                    tier=tier,
                    target_slot=slot,
                    fold=ATTACK_TASKS.index(attack) % 5,
                    collected_arms=("clean", "benign_control", "attack"),
                    scenario_role=scenario_role,
                    cell_index=k,
                )
            )
        )
    return scenarios


#: Supplement layer, design 15.1: 120 = 60 T1 user-side (24 of them code x
#: direct_user, for the execution and code-execution quotas) + 60 T2 across the
#: three channels (for analysis-only engagement and bounded resistance).
SUPPLEMENT_LAYERS: tuple[tuple[str, str, str, int, tuple[str, ...]], ...] = (
    ("t1_code_direct", "direct_user", "T1", 24, ("code",)),
    ("t1_other_direct", "direct_user", "T1", 16, ("creative", "reasoning_legal", "everyday_knowledge")),
    ("t1_multi_turn", "multi_turn_user", "T1", 20, DOMAIN_GROUPS),
    ("t2_direct", "direct_user", "T2", 20, DOMAIN_GROUPS),
    ("t2_multi_turn", "multi_turn_user", "T2", 20, DOMAIN_GROUPS),
    ("t2_tool_output", "tool_output", "T2", 20, DOMAIN_GROUPS),
)


def _supplement_scenarios(builder: SubsetBuilder) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    position = 0
    topic_cursor: dict[str, int] = {}
    for layer, channel, tier, count, groups in SUPPLEMENT_LAYERS:
        pool = [task for task in ATTACK_TASKS if task.domain_group in groups]
        for k in range(count):
            attack = pool[k % len(pool)]
            r_type = R_TYPE_CYCLE[position % len(R_TYPE_CYCLE)]
            topics = allowed_topics(builder.subset, r_type)
            cursor = topic_cursor.get(r_type, 0)
            topic_cursor[r_type] = cursor + 1
            topic = topics[cursor % len(topics)]
            context = builder.context(position)
            task = build_task(context, r_type, topic, position + 7)
            scenario_id, seed = builder._next_id()
            scenarios.append(
                builder.add(
                    build_scenario(
                        scenario_id=scenario_id,
                        subset=builder.subset,
                        dataset_role=DATASET_ROLE[builder.subset],
                        context=context,
                        task=task,
                        seed=seed,
                        attack=attack,
                        marker_suffix=builder.markers.take(),
                        channel=channel,
                        tier=tier,
                        target_slot=attack.slot,
                        fold=ATTACK_TASKS.index(attack) % 5,
                        collected_arms=("attack",),
                        scenario_role="supplement",
                        cell_index=k,
                        extra_factory={"supplement_layer": layer},
                    )
                )
            )
            position += 1
    return scenarios


def build_g_fit(markers: MarkerCounter) -> SubsetPlan:
    builder = SubsetBuilder("g_fit", markers)
    normals = _normal_scenarios(
        builder,
        count=150,
        scenario_role="normal",
        normal_variant=None,
        channel="tool_output",
        collected_arms=("clean", "benign_control"),
    )
    builder.group("normal", ("clean", "benign_control"), normals)
    return builder.plan


def build_g_cal(markers: MarkerCounter) -> SubsetPlan:
    builder = SubsetBuilder("g_cal", markers)
    normals = _normal_scenarios(
        builder,
        count=150,
        scenario_role="normal",
        normal_variant=None,
        channel="tool_output",
        collected_arms=("clean", "benign_control"),
    )
    builder.group("normal", ("clean", "benign_control"), normals)
    return builder.plan


def build_g_dev(markers: MarkerCounter) -> SubsetPlan:
    builder = SubsetBuilder("g_dev", markers)
    core = _core_scenarios(builder, scenario_role="core")
    builder.group("core_72_cells", ("clean", "benign_control", "attack"), core)
    supplement = _supplement_scenarios(builder)
    builder.group("attack_supplement", ("attack",), supplement)
    lexical = _normal_scenarios(
        builder,
        count=24,
        scenario_role="benign_lexical",
        normal_variant="benign_lexical",
        channel="direct_user",
        collected_arms=("clean",),
    )
    builder.group("benign_lexical", ("clean",), lexical)
    refusal = _normal_scenarios(
        builder,
        count=24,
        scenario_role="legitimate_refusal",
        normal_variant="legitimate_refusal",
        channel="direct_user",
        collected_arms=("clean",),
    )
    builder.group("legitimate_refusal", ("clean",), refusal)
    return builder.plan


#: 40 G-dev core scenarios for the medium-effort paired re-run: one per
#: (domain group x channel x wording tier) cell = 36, then four more, one per
#: domain group, so all four groups, all three channels and all three tiers are
#: represented in the analysis-length sensitivity column.
def select_medium(core_scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for scenario in core_scenarios:
        key = (
            scenario["domain_group"],
            scenario["arms"]["attack"]["channel"],
            scenario["wording_tier"],
        )
        buckets.setdefault(key, []).append(scenario)

    fixtures: dict[str, int] = {}
    r_types: dict[str, int] = {}
    chosen: list[dict[str, Any]] = []
    taken: set[str] = set()

    def cost(scenario: dict[str, Any]) -> tuple[int, int, str]:
        factory = scenario["factory"]
        return (
            fixtures.get(factory["fixture_id"], 0),
            r_types.get(factory["r_type"], 0),
            scenario["base_task_id"],
        )

    def accept(scenario: dict[str, Any]) -> None:
        factory = scenario["factory"]
        fixtures[factory["fixture_id"]] = fixtures.get(factory["fixture_id"], 0) + 1
        r_types[factory["r_type"]] = r_types.get(factory["r_type"], 0) + 1
        taken.add(scenario["base_task_id"])
        chosen.append(scenario)

    for key in sorted(buckets):
        accept(min(buckets[key], key=cost))

    remaining = [s for s in core_scenarios if s["base_task_id"] not in taken]
    for group in DOMAIN_GROUPS:
        if len(chosen) >= 40:
            break
        candidates = [s for s in remaining if s["domain_group"] == group and s["base_task_id"] not in taken]
        if candidates:
            accept(min(candidates, key=cost))
    return chosen[:40]


def build_g_medium(g_dev: SubsetPlan, markers: MarkerCounter) -> SubsetPlan:
    core = [
        scenario
        for scenario in g_dev.scenarios
        if scenario["factory"]["scenario_role"] == "core"
    ]
    selected = select_medium(core)
    plan = SubsetPlan(subset="g_medium")
    scenarios = []
    for index, source in enumerate(selected, start=1):
        scenario = copy.deepcopy(source)
        scenario_id = f"{SUBSET_ID_PREFIX['g_medium']}-{index:03d}"
        scenario["base_task_id"] = scenario_id
        scenario["pair_group_id"] = scenario_id
        scenario["dataset_role"] = DATASET_ROLE["g_medium"]
        scenario["analysis_group_id"] = scenario["analysis_group_id"].replace(
            "g_dev-", "g_medium-", 1
        )
        scenario["split_group_id"] = scenario["split_group_id"].replace(
            "g_dev-", "g_medium-", 1
        )
        factory = scenario["factory"]
        factory["subset"] = "g_medium"
        factory["scenario_role"] = "medium_paired_rerun"
        factory["source_scenario_id"] = source["base_task_id"]
        factory["source_subset"] = "g_dev"
        factory["reasoning_effort"] = "medium"
        factory["pairing"] = (
            "same fixture, same scenario text, same sampling seed; only the "
            "chat-template reasoning_effort differs (design section 5)"
        )
        scenarios.append(scenario)
    plan.scenarios = scenarios
    plan.collection_groups = [
        {
            "group": "medium_paired_rerun",
            "arms": ["clean", "benign_control", "attack"],
            "scenario_count": len(scenarios),
            "trace_count": 3 * len(scenarios),
            "scenario_ids": [scenario["base_task_id"] for scenario in scenarios],
        }
    ]
    return plan


def _held_out_attacks(builder: SubsetBuilder) -> list[dict[str, Any]]:
    """16 attacks on the G-conf-only workflow type (R4 x warranty)."""

    r_type, topic = HELD_OUT_CONF_ONLY
    scenarios: list[dict[str, Any]] = []
    position = 0
    for domain_group in DOMAIN_GROUPS:
        for channel in ("direct_user", "multi_turn_user"):
            for tier in ("T1", "T2"):
                slot = position % 2
                attack = ATTACK_TASKS_BY_CELL[(domain_group, slot)][position % 2]
                context = builder.context(position)
                task = build_task(context, r_type, topic, position)
                scenario_id, seed = builder._next_id()
                scenarios.append(
                    builder.add(
                        build_scenario(
                            scenario_id=scenario_id,
                            subset=builder.subset,
                            dataset_role=DATASET_ROLE[builder.subset],
                            context=context,
                            task=task,
                            seed=seed,
                            attack=attack,
                            marker_suffix=builder.markers.take(),
                            channel=channel,
                            tier=tier,
                            target_slot=slot,
                            fold=ATTACK_TASKS.index(attack) % 5,
                            collected_arms=("clean", "benign_control", "attack"),
                            scenario_role="held_out_workflow",
                            cell_index=position,
                            extra_factory={
                                "held_out_workflow_type": f"{r_type}:{topic}",
                                "held_out_scope": "appears only in G-conf",
                            },
                        )
                    )
                )
                position += 1
    return scenarios


def _build_confirmation(subset: str, markers: MarkerCounter) -> SubsetPlan:
    """The confirmation-batch shape: 144 core + 16 held-out x 3 arms + 120 normals x 2.

    280 scenarios / 720 traces.  ``g_conf`` and ``g_conf2`` are the same allocation
    over different fixtures, different scenario ids and a different seed block; the
    only thing that distinguishes them is ``subset``.
    """

    builder = SubsetBuilder(subset, markers)
    core = _core_scenarios(builder, scenario_role="core")
    held_out = _held_out_attacks(builder)
    builder.group("core_72_cells", ("clean", "benign_control", "attack"), core)
    builder.group("held_out_workflow", ("clean", "benign_control", "attack"), held_out)
    normals = _normal_scenarios(
        builder,
        count=120,
        scenario_role="normal",
        normal_variant=None,
        channel="tool_output",
        collected_arms=("clean", "benign_control"),
    )
    builder.group("normal", ("clean", "benign_control"), normals)
    return builder.plan


def build_g_conf(markers: MarkerCounter) -> SubsetPlan:
    return _build_confirmation("g_conf", markers)


def build_g_conf2(markers: MarkerCounter) -> SubsetPlan:
    """G-conf-2 (append-only): the second sealed confirmation batch.

    Built LAST so the global marker counter hands every earlier subset exactly the
    suffixes it already has -- G-conf-2 only ever consumes suffixes above them.
    """

    return _build_confirmation("g_conf2", markers)
