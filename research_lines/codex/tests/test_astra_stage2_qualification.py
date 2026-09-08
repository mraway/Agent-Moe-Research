from __future__ import annotations

import json
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_astra_stage2_qualification import (  # noqa: E402
    BASE_AGENT, MAX_NEW_TOKENS, THEMES, audit_bundle, build_bundle, prompt_token_count, write_exclusive,
)


class AstraStage2QualificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records, cls.kb, cls.agent, cls.config = build_bundle()

    def test_bundle_is_deterministic(self):
        self.assertEqual(build_bundle(), build_bundle())

    def test_fixed_triplets_unique_seeds_and_ids(self):
        scenarios = self.config["scenarios"]
        self.assertEqual(len(scenarios), 12)
        self.assertEqual({s["sampling_seed"] for s in scenarios}, set(range(73001, 73013)))
        self.assertEqual(len({s["pair_group_id"] for s in scenarios}), 12)
        for scenario in scenarios:
            self.assertEqual(set(scenario["arms"]), {"clean", "benign_control", "attack"})

    def test_factor_counts(self):
        audit = audit_bundle(self.records, self.kb, self.agent, self.config)
        self.assertEqual(audit["trace_count"], 36)
        self.assertEqual(audit["channels"], {"direct_user": 4, "tool_output": 8})
        self.assertEqual(audit["positions"], {"before": 4, "middle": 4, "after": 4})
        self.assertEqual(audit["length_design"], {"short": 4, "long_policy": 4, "long_status_policy": 4})
        self.assertFalse(audit["sentinel_model_visible"])

    def test_exact_unchanged_mandate_and_tools(self):
        base = json.loads((ROOT / BASE_AGENT).read_text())
        changed = {key for key in base if base[key] != self.agent[key]}
        self.assertEqual(changed, {"knowledge_base", "support_records"})

    def test_new_record_ids_all_collections_valid(self):
        for collection in ("orders", "returns", "support_cases", "warranties", "subscriptions"):
            self.assertEqual(len(self.records[collection]), 1)
            self.assertTrue(all("-AS2Q1-" in key for key in self.records[collection]))

    def test_fact_coverage_is_explicit(self):
        for scenario in self.config["scenarios"]:
            review = scenario["qualification"]
            self.assertTrue(review["required_policy_facts"])
            if review["length_design"] != "short":
                self.assertGreaterEqual(len(review["required_policy_facts"]), 9)
        self.assertEqual(len(THEMES), 4)

    def test_no_response_brief_or_forced_output_length(self):
        self.assertEqual(MAX_NEW_TOKENS, 1024)
        self.assertEqual(self.config["decoding"]["max_new_tokens_per_turn"], 1024)
        self.assertNotIn("min_new_tokens", self.config["decoding"])
        self.assertFalse(self.config["decoding"]["stop_on_complete_protocol_object"])
        for scenario in self.config["scenarios"]:
            self.assertNotIn("response_brief", scenario["task"])
            self.assertNotIn("word count", scenario["task"]["user_request"].lower())

    def test_shared_themes_not_independent_topic_claim(self):
        counts = Counter(s["analysis_group_id"] for s in self.config["scenarios"])
        self.assertEqual(len(counts), 4)
        self.assertEqual(set(counts.values()), {3})

    def test_original_task_identical_across_injections(self):
        for scenario in self.config["scenarios"]:
            request = scenario["task"]["user_request"]
            for arm in scenario["arms"].values():
                self.assertNotIn("task", arm)
                if "user_request" in arm:
                    self.assertTrue(arm["user_request"].startswith(request + "\n\n"))

    def test_exclusive_writer_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new.json"
            write_exclusive(path, {"a": 1})
            with self.assertRaises(FileExistsError):
                write_exclusive(path, {"a": 2})
            self.assertEqual(json.loads(path.read_text()), {"a": 1})

    def test_prompt_count_uses_tensor_not_batchencoding_length(self):
        import torch
        class Tokenizer:
            def apply_chat_template(self, messages, **kwargs):
                return {"input_ids": torch.zeros((1, 1253), dtype=torch.long), "attention_mask": torch.ones((1, 1253))}
        self.assertEqual(prompt_token_count(Tokenizer(), []), 1253)

    def test_prompt_count_also_accepts_direct_tensor(self):
        import torch
        class Tokenizer:
            def apply_chat_template(self, messages, **kwargs):
                return torch.zeros((1, 957), dtype=torch.long)
        self.assertEqual(prompt_token_count(Tokenizer(), []), 957)


if __name__ == "__main__":
    unittest.main()
