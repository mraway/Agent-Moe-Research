from __future__ import annotations

import sys
import unittest
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase_a import select_next_token  # noqa: E402


class PhaseAGenerationTest(unittest.TestCase):
    def test_greedy_selects_argmax(self) -> None:
        logits = torch.tensor([[0.1, 2.0, 0.5], [3.0, 1.0, 2.0]])

        selected = select_next_token(logits, strategy="greedy")

        self.assertEqual(selected.tolist(), [[1], [0]])

    def test_sampling_is_reproducible_from_torch_seed(self) -> None:
        logits = torch.tensor([[0.0, 0.0, 0.0, 0.0]])
        torch.manual_seed(4101)
        first = select_next_token(logits, strategy="sample", temperature=0.7, top_p=0.9)
        torch.manual_seed(4101)
        second = select_next_token(logits, strategy="sample", temperature=0.7, top_p=0.9)

        self.assertTrue(torch.equal(first, second))

    def test_top_p_retains_at_least_the_most_likely_token(self) -> None:
        logits = torch.tensor([[10.0, 0.0, -1.0]])

        selected = select_next_token(
            logits,
            strategy="sample",
            temperature=1.0,
            top_p=0.01,
        )

        self.assertEqual(selected.item(), 0)

    def test_sampling_rejects_invalid_parameters(self) -> None:
        logits = torch.tensor([[0.0, 1.0]])
        with self.assertRaises(ValueError):
            select_next_token(logits, strategy="sample", temperature=0)
        with self.assertRaises(ValueError):
            select_next_token(logits, strategy="sample", top_p=0)
        with self.assertRaises(ValueError):
            select_next_token(logits, strategy="unknown")


if __name__ == "__main__":
    unittest.main()
