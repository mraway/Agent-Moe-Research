from __future__ import annotations

import sys
import unittest
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from phase_a.normal_manifold import sha256  # noqa: E402
from run_agent_v2_proposal4_ntr import (  # noqa: E402
    BLOCK_WIDTH,
    HORIZON,
    PLAN,
    PLAN_SHA256,
    TransitionBank,
    all_block_signatures,
    calibrate_group_thresholds,
    conditional_successor_raw,
    disjoint_reference_starts,
    predict_first_state,
    scan_top_k,
    transition_blocks,
)


def _signature(expert: int) -> torch.Tensor:
    value = torch.zeros(16, 64)
    value[:, expert] = 1.0
    return value


def _constant_top_k(token_count: int, expert: int) -> torch.Tensor:
    return torch.full((16, token_count, 8), expert, dtype=torch.long)


class AgentV2Proposal4NTRTests(unittest.TestCase):
    def test_plan_hash_is_frozen(self) -> None:
        self.assertEqual(sha256(PLAN), PLAN_SHA256)

    def test_block_signature_aggregates_selection_frequency(self) -> None:
        top_k = torch.arange(8).reshape(1, 1, 8).repeat(16, BLOCK_WIDTH, 1)
        signatures = all_block_signatures(top_k)

        self.assertEqual(tuple(signatures.shape), (1, 16, 64))
        expected = (1.0 / 8.0) ** 0.5
        self.assertTrue(torch.allclose(signatures[0, :, :8], torch.full((16, 8), expected)))
        self.assertEqual(int(torch.count_nonzero(signatures[0, :, 8:])), 0)

    def test_reference_starts_use_frozen_block_stride(self) -> None:
        starts = disjoint_reference_starts(120)

        self.assertEqual(starts, (0, 16, 32, 48, 64))

    def test_transition_blocks_share_no_tokens(self) -> None:
        top_k = _constant_top_k(HORIZON, 0)
        top_k[:, 16:32, :] = 1
        top_k[:, 32:48, :] = 2

        predecessor, near, far = transition_blocks(top_k, [0])

        self.assertEqual(torch.argmax(predecessor[0, 0]).item(), 0)
        self.assertEqual(torch.argmax(near[0, 0]).item(), 1)
        self.assertEqual(torch.argmax(far[0, 0]).item(), 2)

    def test_retrieval_is_selected_only_by_predecessor(self) -> None:
        query_pre = torch.stack((_signature(0),))
        reference_pre = torch.stack(
            (_signature(0), _signature(0), _signature(1))
        )
        reference_successor = torch.stack(
            (_signature(2), _signature(3), _signature(4))
        )

        _, candidates_a = conditional_successor_raw(
            query_pre,
            torch.stack((_signature(2),)),
            reference_pre,
            reference_successor,
            candidate_count=2,
            neighbor_k=1,
        )
        _, candidates_b = conditional_successor_raw(
            query_pre,
            torch.stack((_signature(4),)),
            reference_pre,
            reference_successor,
            candidate_count=2,
            neighbor_k=1,
        )

        self.assertEqual(set(candidates_a[0].tolist()), {0, 1})
        self.assertTrue(torch.equal(candidates_a, candidates_b))

    def test_exclusion_removes_every_edge_from_query_trace(self) -> None:
        reference = torch.stack(tuple(_signature(index % 3) for index in range(6)))
        trace_ids = ("a", "a", "b", "b", "c", "c")

        _, candidates = conditional_successor_raw(
            torch.stack((_signature(0),)),
            torch.stack((_signature(0),)),
            reference,
            reference,
            reference_trace_ids=trace_ids,
            excluded_trace_id="a",
            candidate_count=3,
            neighbor_k=1,
        )

        self.assertTrue(all(trace_ids[index] != "a" for index in candidates[0].tolist()))

    def test_first_candidate_is_unchanged_by_unobserved_future(self) -> None:
        prefix = _constant_top_k(HORIZON, 0)
        reference = torch.stack(tuple(_signature(0) for _ in range(16)))
        bank = TransitionBank(
            predecessors=reference,
            near_successors=reference,
            far_successors=reference,
            trace_ids=tuple(f"r{index}" for index in range(16)),
            starts=tuple(0 for _ in range(16)),
            center=0.0,
            scale=1.0,
            reference_near_raw=torch.zeros(16),
            reference_far_raw=torch.zeros(16),
        )

        first = scan_top_k(prefix, bank)[0]
        extension = torch.cat((prefix, _constant_top_k(20, 7)), dim=1)
        extended = scan_top_k(extension, bank)[0]

        self.assertEqual(first, extended)
        self.assertEqual(first["candidate_onset"], 16)
        self.assertEqual(first["decision_token"], 47)

    def test_group_calibration_collapses_two_correlated_arms(self) -> None:
        rows = []
        for index, values in enumerate(((1.0, 3.0), (2.0, 4.0), (5.0, 6.0))):
            for arm, value in zip(("clean", "benign_control"), values, strict=True):
                rows.append(
                    {
                        "pair_group_id": f"g{index}",
                        "fold": 0,
                        "arm": arm,
                        "eligible": True,
                        "recovery_max": value,
                        "persistence_max": value + 10.0,
                    }
                )

        thresholds = calibrate_group_thresholds(
            rows, folds={0}, minimum_groups=3
        )

        self.assertEqual(thresholds["eligible_group_count"], 3)
        self.assertEqual(thresholds["recovery"]["group_count"], 3)
        self.assertEqual(thresholds["recovery"]["threshold"], 6.0)

    def test_first_alarm_is_strict_and_frozen(self) -> None:
        candidates = [
            {
                "start": 0,
                "candidate_onset": 16,
                "decision_token": 47,
                "recovery_score": 2.0,
                "persistence_score": 2.0,
            },
            {
                "start": 1,
                "candidate_onset": 17,
                "decision_token": 48,
                "recovery_score": 3.0,
                "persistence_score": 4.0,
            },
            {
                "start": 2,
                "candidate_onset": 18,
                "decision_token": 49,
                "recovery_score": 99.0,
                "persistence_score": 0.0,
            },
        ]

        result = predict_first_state(candidates, 2.0, 2.0)

        self.assertEqual(result["state"], "ambiguous")
        self.assertEqual(result["decision_token"], 48)


if __name__ == "__main__":
    unittest.main()
