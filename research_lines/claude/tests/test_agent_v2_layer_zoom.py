from __future__ import annotations

import sys
import unittest
from pathlib import Path

import torch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from analyze_agent_v2_layer_zoom import (  # noqa: E402
    EVENT_OFFSETS,
    build_layer_reference,
    event_curve,
    layer_hellinger_distances,
    probability_jsd,
    recovery_delta,
    robust_layer_location_scale,
)


class AgentV2LayerZoomTests(unittest.TestCase):
    def test_layer_hellinger_is_zero_for_identity(self) -> None:
        values = torch.zeros(1, 16, 64)
        values[:, :, 0] = 1.0
        distance = layer_hellinger_distances(values, values)
        self.assertTrue(torch.equal(distance, torch.zeros(1, 1, 16)))

    def test_layer_hellinger_is_one_for_disjoint_support(self) -> None:
        left = torch.zeros(1, 16, 64)
        right = torch.zeros(1, 16, 64)
        left[:, :, 0] = 1.0
        right[:, :, 1] = 1.0
        distance = layer_hellinger_distances(left, right)
        self.assertTrue(torch.allclose(distance, torch.ones(1, 1, 16)))

    def test_robust_scale_is_positive_for_constant_layers(self) -> None:
        center, scale = robust_layer_location_scale(torch.ones(8, 16))
        self.assertTrue(torch.equal(center, torch.ones(16)))
        self.assertTrue(bool((scale > 0).all()))

    def test_recovery_delta_uses_disjoint_frozen_windows(self) -> None:
        scores = torch.zeros(64, 16)
        scores[0:16] = 4.0
        scores[16:32] = 99.0
        scores[32:64] = 1.0
        delta = recovery_delta(scores, 0)
        assert delta is not None
        self.assertTrue(torch.equal(delta, torch.full((16,), -3.0)))

    def test_recovery_delta_censors_short_future(self) -> None:
        self.assertIsNone(recovery_delta(torch.zeros(63, 16), 0))

    def test_probability_jsd_identity_is_zero(self) -> None:
        values = torch.full((16, 64), 1.0 / 64.0)
        self.assertTrue(torch.allclose(probability_jsd(values, values), torch.zeros(16)))

    def test_event_curve_is_pre16_referenced_and_causal_by_offset(self) -> None:
        probabilities = torch.zeros(16, 50, 64)
        probabilities[:, :, 0] = 1.0
        probabilities[:, 20:, 0] = 0.0
        probabilities[:, 20:, 1] = 1.0
        curve = event_curve(probabilities, 20)
        assert curve is not None
        self.assertEqual(set(curve), {offset for offset in EVENT_OFFSETS if 0 <= 20 + offset < 50})
        self.assertTrue(torch.equal(curve[-1], torch.zeros(16)))
        self.assertTrue(bool((curve[0] > 0).all()))

    def test_reference_bank_excludes_every_anchor_from_same_trace(self) -> None:
        rows = []
        for trace in range(26):
            ids = torch.full((16, 8, 8), trace % 64, dtype=torch.long)
            rows.append((f"trace-{trace}", ids))
        bank = build_layer_reference(rows)
        self.assertEqual(tuple(bank["features"].shape), (208, 16, 64))
        self.assertEqual(tuple(bank["raw"].shape), (208, 16))
        self.assertTrue(bool(torch.isfinite(bank["raw"]).all()))


if __name__ == "__main__":
    unittest.main()
