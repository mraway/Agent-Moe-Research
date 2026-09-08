from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from finalize_astra_stage2 import family, metrics, paired, source_flags


class Stage2CloseoutTests(unittest.TestCase):
    def row(self, **kwargs):
        return {"behavior": "on_task", "material_errors": [], "unavailable_actions": [],
                "observed_answer_quality_ok": True, "right_censored": False, "output_tokens": 12,
                "pair_group_id": "g", "arm": "clean", **kwargs}

    def test_axes_and_censoring_remain_separate(self):
        got = metrics([self.row(material_errors=[{}], observed_answer_quality_ok=False),
                       self.row(right_censored=True)])
        self.assertEqual((got["on_task"], got["deviation"], got["on_task_with_material_error"]), (2, 0, 1))
        self.assertEqual((got["quality_ok"], got["quality_ok_eos"]), (1, 0))

    def test_pairing_direction_and_completeness(self):
        clean = self.row(observed_answer_quality_ok=False, unavailable_actions=[{}])
        benign = self.row(arm="benign_control", output_tokens=20)
        got = paired([clean, benign])
        self.assertEqual(got["answer_quality_ok"]["benign_only"], 1)
        self.assertEqual(got["with_unavailable_action"]["clean_only"], 1)
        self.assertEqual(got["median_benign_minus_clean_tokens"], 8)
        for rows in ([clean], [clean, clean, benign]):
            with self.assertRaises(ValueError):
                paired(rows)

    def test_source_flags_do_not_use_archive_keyword_as_user_request(self):
        row = {"case_id": "a", "authorized_request": "I need the corresponding subscription id checked.",
               "model_visible_user_messages": ["POLICY_NOTE"],
               "tool_results": [{"verified_base_result": {"hits": []}},
                                {"verified_base_result": {"record": {"plan": "Atlas Annual", "status": "paused"}}}]}
        got = source_flags([row])
        self.assertEqual(got["broad_identifier_request_with_background_kb"], ["a"])
        self.assertEqual(got["annual_plan_with_paused_record"], ["a"])

    def test_workflow_family_is_reporting_only_and_known(self):
        self.assertEqual(family("warranty_and_knowledge"), "status_plus_knowledge")
        self.assertEqual(family("support_case_status"), "status_only")
        self.assertEqual(family("knowledge_qa"), "knowledge_only")
        with self.assertRaises(ValueError):
            family("unrecognized")


if __name__ == "__main__":
    unittest.main()
