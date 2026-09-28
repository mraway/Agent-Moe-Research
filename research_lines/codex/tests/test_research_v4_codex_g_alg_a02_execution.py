"""Resource-only supplement tests, no experiment data access."""
import unittest
from unittest.mock import patch
from research_v4 import codex_g_alg_a02_execution as ex


class ExecutionTests(unittest.TestCase):
    def test_preserves_old_failed_gate_but_allows_approved_projection(self):
        p = {"support": {"unsupported_queries": []}, "projected_total_seconds": 800.02, "cost_gate_pass": False}
        r = ex.projection_gate(p)
        self.assertTrue(r["approved_gate_pass"])
        self.assertFalse(r["original_600_second_gate"])
        self.assertFalse(p["cost_gate_pass"])

    def test_does_not_remove_support_or_new_resource_gate(self):
        with self.assertRaises(ValueError):
            ex.projection_gate({"support": {"unsupported_queries": ["q"]}, "projected_total_seconds": 1, "cost_gate_pass": False})
        with self.assertRaises(RuntimeError):
            ex.projection_gate({"support": {"unsupported_queries": []}, "projected_total_seconds": 1201, "cost_gate_pass": False})

    def test_cumulative_budget_and_memory_still_enforced(self):
        with patch.object(ex.pf, "rss", return_value=1):
            ex.Budget20(800).check()
            with self.assertRaises(RuntimeError): ex.Budget20(1201).check()
        with patch.object(ex.pf, "rss", return_value=2.01):
            with self.assertRaises(RuntimeError): ex.Budget20().check()

    def test_original_600_seconds_checker_not_mutated(self):
        with self.assertRaises(RuntimeError): ex.core.Budget(601).check()
        self.assertIsNot(ex.core.Budget, ex.Budget20)

    def test_bad_source_manifest_rejected_before_any_reads(self):
        with self.assertRaises(ValueError): ex.literal_sources(b"{}", "bad")


if __name__ == "__main__": unittest.main()
