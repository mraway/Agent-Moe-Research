from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
from research_v4.codex_g_m2a import M2AccessGuard, archive_members
from research_v4.codex_g_m2a_math import (
    cluster_grid, donor_candidates, donor_influence, look_geometry, match_footprint,
    paired_fields, query_groups,
)


def record(key, *, variant="clean", scenario="s1", fold=0, quality=True, episode=0, coordinates=None):
    coordinates = coordinates or [("analysis", 0, 0, t) for t in range(7, 30)]
    return {"key": key, "variant": variant, "scenario": scenario, "fold": fold, "filter_pass": quality,
            "episode_index": episode, "fixture": "f1", "routine_template_id": "r1", "coordinates": coordinates,
            "coordinate_lookup": {c: i for i, c in enumerate(coordinates)},
            "anchor": {"anchor": 10, "x": 30}, "h_end": 29}


class GeometryTest(unittest.TestCase):
    def test_look_geometry_resets_at_step_boundary(self):
        steps = [{"agent_step": 4, "global_token_offset": 0, "output_token_count": 10},
                 {"agent_step": 5, "global_token_offset": 10, "output_token_count": 10}]
        coords = look_geometry(["analysis"] * 20, steps, np.array([7, 9, 10, 16, 17, 19]))
        self.assertEqual(coords, [("analysis", 0, 0, 7), ("analysis", 0, 0, 9), None, None,
                                  ("analysis", 1, 0, 7), ("analysis", 1, 0, 9)])

    def test_repeated_channel_gets_distinct_run_number(self):
        tags = ["analysis"] * 10 + ["commentary"] * 8 + ["analysis"] * 10
        step = [{"agent_step": 0, "global_token_offset": 0, "output_token_count": 28}]
        self.assertEqual(look_geometry(tags, step, np.array([7, 17, 25])),
                         [("analysis", 0, 0, 7), ("commentary", 0, 0, 7), ("analysis", 0, 1, 7)])

    def test_bad_step_geometry_rejected(self):
        with self.assertRaises(ValueError):
            look_geometry(["analysis"] * 10, [{"agent_step": 0, "global_token_offset": 1,
                                             "output_token_count": 10}], np.array([7]))

    def test_horizon_is_already_a_look_grid_not_token_limit(self):
        row = record("attack", variant="attack")
        row.update(anchor={"anchor": 400, "x": 420}, h_end=430)
        groups = query_groups(row, np.arange(390, 431), "X_level")
        self.assertEqual(len(groups["groups"]["post"]), 11)
        self.assertTrue(groups["horizon_cut"])
        self.assertEqual(groups["status"], "eligible")

    def test_empty_and_missing_events(self):
        row = record("attack", variant="attack")
        row["anchor"]["x"] = 12
        result = query_groups(row, np.arange(7, 30), "EMX_complete")
        self.assertEqual(result["status"], "empty_interval:middle")
        row["anchor"]["x"] = None
        self.assertEqual(query_groups(row, np.arange(7, 30), "X_level")["status"], "missing_event")
        row["anchor"]["anchor"] = 2
        self.assertEqual(query_groups(row, np.arange(7, 30), "E_did")["status"], "no_look:pre")


class MatchTest(unittest.TestCase):
    def setUp(self):
        self.target = record("a", variant="attack")
        self.query = {"status": "eligible", "groups": {"post": [0, 1, 2]}}

    def test_same_task_fold_turn_and_normal_eligibility(self):
        rows = {k: record(k, **kwargs) for k, kwargs in {
            "ok": {}, "wrong_task": {"scenario": "s2"}, "wrong_fold": {"fold": 1},
            "wrong_turn": {"episode": 1}, "attack": {"variant": "attack"},
            "refusal": {"variant": "legitimate_refusal"}}.items()}
        self.assertEqual(donor_candidates(self.target, rows, "S", "filtered"), ["ok"])

    def test_quality_primary_and_all_sensitivity_remain_separate(self):
        rows = {"yes": record("yes"), "no": record("no", quality=False), "unknown": record("unknown", quality=None)}
        self.assertEqual(donor_candidates(self.target, rows, "S", "filtered"), ["yes"])
        self.assertEqual(len(donor_candidates(self.target, rows, "S", "all")), 3)

    def test_full_footprint_and_no_partial_match(self):
        rows = {"short": record("short", coordinates=self.target["coordinates"][:2]), "full": record("full")}
        result = match_footprint(self.target, self.query, rows, "S", "filtered")
        self.assertEqual(result["status"], "matched")
        self.assertEqual([d["key"] for d in result["donors"]], ["full"])
        self.assertEqual(result["donors"][0]["groups"]["post"], [0, 1, 2])

    def test_reordered_channels_cannot_fake_a_transition(self):
        coordinates = [("analysis", 0, 0, 7), ("final", 0, 0, 7)]
        target = record("a", variant="attack", coordinates=coordinates)
        donor = record("d", coordinates=list(reversed(coordinates)))
        query = {"status": "eligible", "groups": {"post": [0, 1]}}
        self.assertEqual(match_footprint(target, query, {"d": donor}, "S", "filtered")["status"], "no_complete_donor")

    def test_template_scope_requires_three_distinct_scenarios(self):
        rows = {"a": record("a"), "b": record("b", variant="benign_control"), "c": record("c", scenario="s2")}
        self.assertEqual(match_footprint(self.target, self.query, rows, "F", "filtered")["status"], "insufficient_donor_scenarios")
        rows["d"] = record("d", scenario="s3")
        self.assertEqual(match_footprint(self.target, self.query, rows, "F", "filtered")["status"], "matched")
        rows["d"]["routine_template_id"] = "different"
        self.assertEqual(match_footprint(self.target, self.query, rows, "F", "filtered")["status"], "insufficient_donor_scenarios")

    def test_matching_ignores_attached_scores_and_outcomes(self):
        rows = {"d": record("d")}
        original = match_footprint(self.target, self.query, rows, "S", "filtered")
        self.target["scores"] = np.ones((3, 4)) * 99
        rows["d"]["scores"] = np.zeros((3, 4))
        rows["d"]["trajectory_class"] = "arbitrary"
        self.assertEqual(original, match_footprint(self.target, self.query, rows, "S", "filtered"))

    def test_step_crossing_target_is_not_silently_dropped(self):
        self.target["coordinates"][1] = None
        result = match_footprint(self.target, self.query, {"d": record("d")}, "S", "filtered")
        self.assertEqual(result["status"], "target_cross_step_window")


class ArithmeticTest(unittest.TestCase):
    def test_donor_and_episode_weighting(self):
        target = {"pre": np.array([[1.], [1.]]), "post": np.array([[3.], [3.], [3.]])}
        donors = [{"pre": np.array([[0.]]), "post": np.array([[1.]])},
                  {"pre": np.zeros((10, 1)), "post": np.full((10, 1), 3.)}]
        fields, _ = paired_fields(target, donors)
        self.assertEqual(float(fields["post_control"][0]), 2.)
        self.assertEqual(float(fields["change_attack"][0]), 2.)
        self.assertEqual(float(fields["did"][0]), 0.)

    def test_cluster_grid_reproducibility_and_episode_equal_mean(self):
        values = np.array([0., 0., 1.])[:, None, None] * np.ones((3, 3, 4))
        result = cluster_grid(values, ["a", "a", "b"])
        self.assertEqual(result, cluster_grid(values, ["a", "a", "b"]))
        np.testing.assert_allclose(result["mean"], 1 / 3)
        self.assertEqual(result["cluster_sizes"], {"a": 2, "b": 1})
        self.assertIsNone(cluster_grid(np.ones((1, 3, 4)), ["a"])["ci"])

    def test_donor_influence_reports_target_dropout(self):
        result = donor_influence([np.array([1.]), np.array([2.])],
                                 [[np.array([0.])], [np.array([0.]), np.array([1.])]], [["s1"], ["s1", "s2"]])
        self.assertEqual(result["n_range"], [1, 2])
        self.assertEqual(result["scenarios"], 2)

    def test_inventory_archive_reader_does_not_decode_score_members(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "synthetic.npz"
            np.savez(path, keys=np.array(["a"]), ends=np.array([7]), raw=np.array([{"must_not_decode": True}], dtype=object))
            result = archive_members(path, ("keys", "ends"))
            self.assertEqual(result["ends"].tolist(), [7])
            with self.assertRaises(ValueError):
                archive_members(path, ("raw",))

    def test_m2_guard_forbids_raw_routing_even_in_open_pool(self):
        guard = M2AccessGuard(ROOT)
        with self.assertRaises(PermissionError):
            guard.check_path(ROOT / "artifacts/agent_v2/dataset_g/g_dev/synthetic.safetensors")
        with self.assertRaises(PermissionError):
            guard.check_path(ROOT / "artifacts/agent_v2/dataset_g/annotations/g_conf/synthetic.jsonl")
        self.assertEqual(guard.check_path(ROOT / "artifacts/agent_v2/codex_g/scores.npz").suffix, ".npz")


if __name__ == "__main__":
    unittest.main()
