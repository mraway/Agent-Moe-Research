from __future__ import annotations

import sys
import unittest
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from agent_v2 import validate_experiment_config  # noqa: E402
from build_normal_calibration_c1_config import (  # noqa: E402
    BENIGN_FAMILIES,
    COLLECTION_ARMS,
    RECORDS_PER_KIND,
    audit_bundle,
    build_bundle,
)


class NormalCalibrationC1ConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.records, cls.agent, cls.config = build_bundle()

    def test_collection_has_160_groups_and_only_two_planned_arms(self) -> None:
        self.assertEqual(len(self.config["scenarios"]), 160)
        self.assertEqual(self.config["collection_arms"], list(COLLECTION_ARMS))
        self.assertTrue(self.config["stopping_rules"]["never_run_attack_arm"])
        validate_experiment_config(self.config)

    def test_full_bundle_audit_passes(self) -> None:
        audit = audit_bundle(self.records, self.agent, self.config)
        self.assertEqual(audit["planned_trace_count"], 320)
        self.assertEqual(audit["record_ids_reused_from_b1_or_b2"], False)
        self.assertEqual(audit["attack_arm_planned_to_run"], False)

    def test_benign_families_are_fold_exclusive(self) -> None:
        folds: defaultdict[str, set[int]] = defaultdict(set)
        counts: Counter[str] = Counter()
        for row in self.config["scenarios"]:
            folds[row["analysis_group_id"]].add(row["preregistered_fold"])
            counts[row["analysis_group_id"]] += 1
        self.assertEqual(len(folds), len(BENIGN_FAMILIES))
        self.assertTrue(all(len(value) == 1 for value in folds.values()))
        self.assertEqual(set(counts.values()), {10})

    def test_split_has_100_calibration_and_60_evaluation_groups(self) -> None:
        folds = Counter(row["preregistered_fold"] for row in self.config["scenarios"])
        self.assertEqual(folds, {0: 40, 1: 30, 2: 30, 3: 30, 4: 30})
        self.assertEqual(sum(folds[index] for index in (0, 1, 2)), 100)
        self.assertEqual(sum(folds[index] for index in (3, 4)), 60)

    def test_support_records_are_new_and_complete(self) -> None:
        prefixes = {
            "orders": "ORD-C1-",
            "returns": "RET-C1-",
            "support_cases": "CASE-C1-",
            "warranties": "WAR-C1-",
            "subscriptions": "SUB-C1-",
        }
        for collection, prefix in prefixes.items():
            self.assertEqual(len(self.records[collection]), RECORDS_PER_KIND)
            self.assertTrue(all(key.startswith(prefix) for key in self.records[collection]))

    def test_agent_changes_only_support_record_snapshot(self) -> None:
        self.assertEqual(
            self.agent["support_records"],
            "data/agent_v2/support_records_calibration_c1.json",
        )
        self.assertEqual(self.agent["definition_version"], "2.5.0")

    def test_all_seeds_and_scenario_ids_are_unique(self) -> None:
        scenarios = self.config["scenarios"]
        self.assertEqual(len({row["sampling_seed"] for row in scenarios}), 160)
        self.assertEqual(len({row["base_task_id"] for row in scenarios}), 160)
        self.assertEqual(
            (min(row["sampling_seed"] for row in scenarios), max(row["sampling_seed"] for row in scenarios)),
            (51001, 51160),
        )

    def test_risk_milestones_respect_maximum_final_generation_length(self) -> None:
        milestones = self.config["normal_purity_gates"][
            "risk_milestone_by_method"
        ]
        self.assertEqual(milestones["token_endpoint_z"], 64)
        self.assertEqual(milestones["nonoverlap_token_mean8_z"], 16)
        self.assertLessEqual(8 * milestones["nonoverlap_token_mean8_z"], 192)


if __name__ == "__main__":
    unittest.main()
