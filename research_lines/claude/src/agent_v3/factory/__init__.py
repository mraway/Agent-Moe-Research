"""Dataset G scenario factory: deterministic fixtures, tasks, variants, attacks.

Entry points: :func:`agent_v3.factory.build.build_all` writes every dataset G
config, fixture and manifest; :mod:`agent_v3.factory.validate` re-checks the
written configs against the frozen design.
"""

from .allocation import (
    DATASET_ROLE,
    MarkerCounter,
    SubsetPlan,
    allowed_topics,
    build_g_cal,
    build_g_conf,
    build_g_dev,
    build_g_fit,
    build_g_medium,
    select_medium,
)
from .assemble import LEXICAL_DECOYS, build_scenario
from .attacks import ATTACK_TASKS, ATTACK_TASKS_BY_CELL, AttackTask, markers, tier_text
from .build import build_all, build_plans, sha256_file
from .constants import (
    AS_OF_DATE,
    CHANNELS,
    CORE_CELL_COUNT,
    DOMAIN_GROUPS,
    FACTORY_VERSION,
    HELD_OUT_CONF_ONLY,
    HELD_OUT_DEV_ONLY,
    R_TYPE_RATIO,
    R_TYPES,
    TIERS,
    TOPICS,
)
from .fixtures import build_fixture
from .merchants import MERCHANTS, Merchant, merchants_for
from .sessions import build_g_session
from .tasks import FixtureContext, RoutineTask, build_task

__all__ = [
    "AS_OF_DATE",
    "ATTACK_TASKS",
    "ATTACK_TASKS_BY_CELL",
    "AttackTask",
    "CHANNELS",
    "CORE_CELL_COUNT",
    "DATASET_ROLE",
    "DOMAIN_GROUPS",
    "FACTORY_VERSION",
    "FixtureContext",
    "HELD_OUT_CONF_ONLY",
    "HELD_OUT_DEV_ONLY",
    "LEXICAL_DECOYS",
    "MERCHANTS",
    "MarkerCounter",
    "Merchant",
    "R_TYPES",
    "R_TYPE_RATIO",
    "RoutineTask",
    "SubsetPlan",
    "TIERS",
    "TOPICS",
    "allowed_topics",
    "build_all",
    "build_fixture",
    "build_g_cal",
    "build_g_conf",
    "build_g_dev",
    "build_g_fit",
    "build_g_medium",
    "build_g_session",
    "build_plans",
    "build_scenario",
    "build_task",
    "markers",
    "merchants_for",
    "select_medium",
    "sha256_file",
    "tier_text",
]
