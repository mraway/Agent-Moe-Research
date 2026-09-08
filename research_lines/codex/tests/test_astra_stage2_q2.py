from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_astra_stage2_q2 import (  # noqa: E402
    ARMS, BOUNDARY, CONDITIONS, Q1_CONFIG, build_config, config_path,
    make_session, run_path, template_messages,
)
from agent_v2 import load_agent_v2_definition  # noqa: E402
from run_astra_stage2_q2 import runner_arguments  # noqa: E402


class AstraQ2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.q1 = json.loads((ROOT / Q1_CONFIG).read_text())
        cls.source = {s["base_task_id"]: s for s in cls.q1["scenarios"]}
        cls.definition = load_agent_v2_definition(ROOT / cls.q1["agent_config"], workspace_root=ROOT)

    def test_exactly_eight_long_pairs_per_condition(self):
        for condition in CONDITIONS:
            config = build_config(condition)
            self.assertEqual(len(config["scenarios"]), 8)
            self.assertEqual(config["collection_arms"], list(ARMS))
            self.assertTrue(all(s["qualification"]["length_design"] != "short" for s in config["scenarios"]))

    def test_source_seed_and_fields_unchanged(self):
        for condition in CONDITIONS:
            for row in build_config(condition)["scenarios"]:
                old = self.source[row["q2_source_scenario_id"]]
                self.assertEqual(row["sampling_seed"], old["sampling_seed"])
                self.assertEqual(row["arms"], old["arms"])
                self.assertEqual(row["qualification"], old["qualification"])
                task = copy.deepcopy(row["task"])
                task.pop("response_brief")
                self.assertEqual(task, old["task"])

    def test_exact_intervention_body(self):
        for condition in CONDITIONS:
            for row in build_config(condition)["scenarios"]:
                expected = BOUNDARY
                if condition == "task_echo":
                    expected += "\n\nOriginal authorized customer request:\n" + row["task"]["user_request"]
                self.assertEqual(row["task"]["response_brief"], expected)

    def test_pre_brief_messages_match_each_q1_arm(self):
        for condition in CONDITIONS:
            for row in build_config(condition)["scenarios"]:
                old = self.source[row["q2_source_scenario_id"]]
                for arm in ARMS:
                    session = make_session(row, arm, self.definition)
                    baseline = make_session(old, arm, self.definition)
                    self.assertEqual(template_messages(session), template_messages(baseline))
                    before = template_messages(session)
                    rendered = session.add_response_brief(agent_step=0)
                    self.assertEqual(template_messages(session)[:-1], before)
                    self.assertEqual(template_messages(session)[-1], {"role": "user", "content": rendered})
                    self.assertTrue(rendered.startswith("TRUSTED RESPONSE BRIEF: " + BOUNDARY))

    def test_attack_arm_rejected_by_session(self):
        with self.assertRaises(ValueError):
            make_session(build_config("task_echo")["scenarios"][0], "attack", self.definition)

    def test_narrow_launcher_cannot_collect_attack(self):
        for condition in CONDITIONS:
            args = runner_arguments(condition)
            self.assertEqual(args[args.index("--arms") + 1:], ["clean", "benign_control"])
            self.assertIn("--local-files-only", args)
            self.assertNotIn("attack", args)

    def test_task_echo_excludes_archive_labels(self):
        for row in build_config("task_echo")["scenarios"]:
            self.assertNotIn("Archived topic label", row["task"]["response_brief"])
            self.assertNotIn("Additional requested deliverable", row["task"]["response_brief"])

    def test_no_model_agent_decoding_change(self):
        for condition in CONDITIONS:
            config = build_config(condition)
            for key in ("agent_config", "model_config", "decoding"):
                self.assertEqual(config[key], self.q1[key])

    def test_new_nonoverlapping_paths(self):
        self.assertNotEqual(run_path("boundary_only"), run_path("task_echo"))
        for condition in CONDITIONS:
            self.assertNotIn(".claude", run_path(condition))
            self.assertNotEqual(config_path(condition), Q1_CONFIG)

    def test_unknown_condition_refused(self):
        for function in (run_path, config_path, build_config, runner_arguments):
            with self.assertRaises(ValueError):
                function("attack")

    def test_quality_and_length_gates_unchanged(self):
        for condition in CONDITIONS:
            self.assertEqual(build_config(condition)["qualification_gates"], {
                "normal_acceptable_min": 14, "normal_eos_ge256_min": 12, "normal_eos_ge384_min": 8})


if __name__ == "__main__":
    unittest.main()
