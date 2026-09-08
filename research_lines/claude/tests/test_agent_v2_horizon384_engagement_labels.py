from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_agent_v2_horizon384_engagement_labels import (  # noqa: E402
    BOUNDED_ENGAGEMENT,
    EXECUTION_OVERRIDES,
    POST192_CONTINUATION,
    SUPPORT_RESUMPTION,
)


class Horizon384EngagementLabelTests(unittest.TestCase):
    def test_manual_behavior_decisions_are_disjoint(self) -> None:
        self.assertFalse(set(BOUNDED_ENGAGEMENT) & set(EXECUTION_OVERRIDES))
        self.assertEqual(len(BOUNDED_ENGAGEMENT), 5)
        self.assertEqual(len(EXECUTION_OVERRIDES), 5)

    def test_all_parent_length_stops_have_a_continuation_decision(self) -> None:
        self.assertEqual(len(POST192_CONTINUATION), 40)
        self.assertEqual(
            sum(value == "bounded_engagement" for value in POST192_CONTINUATION.values()),
            4,
        )
        self.assertEqual(
            sum(value == "continued_execution" for value in POST192_CONTINUATION.values()),
            26,
        )

    def test_support_resumption_is_a_subset_of_engaged_traces(self) -> None:
        engaged = set(BOUNDED_ENGAGEMENT) | set(EXECUTION_OVERRIDES)
        # The remaining resumption is a parent-positive execution trace.
        self.assertLessEqual(len(set(SUPPORT_RESUMPTION) - engaged), 1)


if __name__ == "__main__":
    unittest.main()
