"""Unit tests for the frozen routine-expert support analysis."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import torch
from safetensors.torch import save_file


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_agent_v2_routine_expert_support import (  # noqa: E402
    evaluate_attacks,
    first_alarm,
    _window_feature,
    rolling_mean,
    routine_counts,
    token_signals,
)
from phase_a.normal_manifold import ManifoldTrace  # noqa: E402


class RoutineExpertSupportTests(unittest.TestCase):
    def test_token_signals_keep_layer_identity(self) -> None:
        counts = torch.full((16, 64), 10, dtype=torch.int64)
        counts[0, 3] = 0
        counts[1, 3] = 100
        ids = torch.zeros((16, 2, 8), dtype=torch.long)
        ids[:, :, :] = 1
        ids[0, 0, 0] = 3
        ids[1, 0, 0] = 3

        signals = token_signals(ids, counts)

        self.assertAlmostEqual(float(signals["unseen"][0]), 1.0 / 128.0)
        self.assertEqual(float(signals["unseen"][1]), 0.0)
        self.assertGreater(float(signals["surprisal"][0]), 0.0)

    def test_rolling_mean_is_causal_and_aligned_to_endpoint(self) -> None:
        ends, scores = rolling_mean(torch.arange(10, dtype=torch.float64), width=3)
        self.assertEqual(ends.tolist(), list(range(2, 10)))
        self.assertEqual(scores.tolist(), [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])

    def test_first_alarm_uses_strict_threshold(self) -> None:
        row = {
            "endpoints": [7, 8, 9],
            "scores": {"unseen8": [0.0, 0.0, 0.25]},
        }
        self.assertEqual(first_alarm(row, "unseen8", 0.0)["endpoint"], 9)
        self.assertIsNone(first_alarm(row, "unseen8", 0.25))

    def test_attribution_window_uses_raw_tokens_not_rolling_scores(self) -> None:
        row = {
            "token_count": 10,
            "endpoints": [7, 8, 9],
            "scores": {
                "unseen8": [0.125, 0.0, 0.0],
                "surprisal8": [9.0, 9.0, 9.0],
            },
            "token_signals": {
                "unseen": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                "surprisal": [9.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
            },
        }

        feature = _window_feature(row, 7, 9)

        self.assertFalse(feature["any_unseen"])
        self.assertEqual(feature["eligible_token_count"], 3)
        self.assertEqual(feature["mean_token_surprisal"], 1.0)
        self.assertIsNone(feature["maximum_contained_surprisal8"])

    def test_attack_evaluation_rejects_pre_onset_as_clean_detection(self) -> None:
        rows = [
            {
                "trace_id": "pre",
                "pair_group_id": "g1",
                "arm": "attack",
                "token_count": 12,
                "endpoints": [7, 8, 9, 10, 11],
                "scores": {"unseen8": [1.0, 0.0, 0.0, 0.0, 0.0]},
            },
            {
                "trace_id": "post",
                "pair_group_id": "g2",
                "arm": "attack",
                "token_count": 12,
                "endpoints": [7, 8, 9, 10, 11],
                "scores": {"unseen8": [0.0, 0.0, 1.0, 0.0, 0.0]},
            },
        ]
        metadata = {
            trace_id: {
                "engagement_class": "cross_domain_execution",
                "engagement_onset": 8,
            }
            for trace_id in ("pre", "post")
        }

        result = evaluate_attacks(rows, metadata, "unseen8", 0.0)

        by_id = {row["trace_id"]: row for row in result["trace_rows"]}
        self.assertTrue(by_id["pre"]["pre_onset"])
        self.assertFalse(by_id["pre"]["clean_post_onset"])
        self.assertTrue(by_id["post"]["clean_post_onset"])
        self.assertEqual(by_id["post"]["post_onset_latency"], 1)

    def test_routine_counts_are_layer_specific(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "trace.safetensors"
            ids = torch.zeros((16, 2, 8), dtype=torch.int16)
            ids[1] = 2
            save_file(
                {
                    "top_k_ids": ids,
                    "probabilities": torch.full((16, 2, 64), 1.0 / 64.0),
                    "token_ids": torch.tensor([1, 2], dtype=torch.int32),
                },
                cache,
            )
            record = ManifoldTrace(
                batch="test",
                trace_id="trace",
                pair_group_id="group",
                fold=0,
                arm="clean",
                workflow="knowledge_qa",
                workflow_family="knowledge_qa",
                channel="none",
                domain="none",
                positive=False,
                completion_boundary=None,
                evidence_onset=None,
                trace_dir=root,
                cache_file=cache,
            )

            counts = routine_counts([record])

        self.assertEqual(int(counts[0, 0]), 16)
        self.assertEqual(int(counts[1, 2]), 16)
        self.assertEqual(int(counts[1, 0]), 0)


if __name__ == "__main__":
    unittest.main()
