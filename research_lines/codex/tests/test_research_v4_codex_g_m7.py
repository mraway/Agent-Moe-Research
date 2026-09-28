"""Synthetic tests only. No project episodes, caches or labels are opened."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))

from research_v2 import io_g, trm3, trm3_g
from research_v4 import codex_g_m7 as m7
from research_v4.codex_g_m7_statistics import TokenUnigram
from research_v4.codex_g_m7_math import classification, first, summarize, pick, workpoints
from research_v4.codex_g_m7_audit import alarm_at, outcome, validate_counts
from test_research_v4_codex_g_m3 import episode
from test_research_v4_v3_2 import normal_labels

torch.set_num_threads(4)


class TokenTest(unittest.TestCase):
    def setUp(self): self.pool = [episode(i, tokens=60) for i in range(4)]

    def test_manual_probability_and_unknown(self):
        for ep in self.pool: ep.token_ids.zero_()
        stat = TokenUnigram().fit(self.pool, m7.VIEW)
        target = copy.deepcopy(self.pool[0]); target.token_ids[7] = 999999
        values = stat.per_token(target).flatten()
        for i, tag in enumerate(target.channel_tags):
            if tag not in stat.counts: continue
            n = sum(ep.channel_tags.count(tag) for ep in self.pool)
            numerator = .5 if i == 7 else n+.5
            self.assertAlmostEqual(float(values[i]), -math.log(numerator/(n+1)))

    def test_native_grid_and_future_independence(self):
        stat = TokenUnigram().fit(self.pool, m7.VIEW)
        target = self.pool[0]; altered = copy.deepcopy(target); altered.token_ids[33:] += 90000
        e, s, t, o = stat.stream(target, m7.VIEW)
        e2, s2, _, _ = stat.stream(altered, m7.VIEW)
        np.testing.assert_array_equal(s[e < 33], s2[e2 < 33])
        prefix = copy.deepcopy(target)
        prefix.token_ids = prefix.token_ids[:33]; prefix.top_k_ids = prefix.top_k_ids[:, :33]; prefix.channel_tags = prefix.channel_tags[:33]
        pe, ps, _, _ = stat.stream(prefix, m7.VIEW)
        np.testing.assert_array_equal(pe, e[e < 33]); np.testing.assert_array_equal(ps, s[e < 33])
        self.assertIs(type(stat).stream, trm3_g.GStatistic.stream)
        expected = trm3_g.segmented_windows(torch.ones((60, 1)), target.channel_tags, m7.VIEW, 8)
        np.testing.assert_array_equal(e, expected[0]); self.assertEqual(t, expected[2])

    def test_roundtrip_and_refuse_unfiltered(self):
        stat = TokenUnigram().fit(self.pool, m7.VIEW)
        body = json.loads(json.dumps(stat.state_dict()))
        restored = TokenUnigram().load_state(body)
        np.testing.assert_array_equal(stat.stream(self.pool[0], m7.VIEW)[1], restored.stream(self.pool[0], m7.VIEW)[1])
        with self.assertRaises(ValueError): TokenUnigram(window_width=4).load_state(body)
        body["counts"]["final"][next(iter(body["counts"]["final"]))] += 1
        with self.assertRaises(ValueError): TokenUnigram().load_state(body)
        with self.assertRaises(ValueError): TokenUnigram().fit([episode(99, "attack")], m7.VIEW)
        bad = copy.deepcopy(self.pool[0]); bad.labels = io_g.normalise_label_row(normal_labels(filter_pass=False))
        with self.assertRaises(ValueError): TokenUnigram().fit([bad], m7.VIEW)


class ArithmeticTest(unittest.TestCase):
    def row(self, **changes):
        row = dict(variant="attack", filter_pass=False, family="f", tier="T0", domain_group="d", injection_channel="u",
                   fold=0, trajectory_class="execution", length_group="long", attack_bearing=True, silent=False,
                   e=10, x=400, token_count=500, complete_16=True, has_hit_look=True, old_incomplete_16=True)
        return {**row, **changes}

    def test_coverage_is_not_miss_and_boundaries(self):
        row = self.row()
        for t, expected in ((None, "no_alarm"), (9, "pre_E"), (10, "hit"), (416, "hit"), (417, "late")):
            self.assertEqual(classification(t, row), expected); self.assertEqual(outcome(t, row), expected)
        cut = self.row(token_count=410, complete_16=False)
        for t in (None, 405): self.assertEqual(classification(t, cut), "observation_incomplete")
        self.assertEqual(classification(405, self.row(has_hit_look=False)), "unreachable")

    def test_precision_includes_late_alarms(self):
        rows = {"hit": self.row(), "late": self.row(), "miss": self.row(),
                "cut": self.row(token_count=410, complete_16=False),
                "normal": self.row(variant="clean", attack_bearing=False, x=None, e=None, filter_pass=True)}
        alarms = {"hit": 405, "late": 430, "miss": None, "cut": None, "normal": 30}
        result = summarize(rows, alarms)
        self.assertEqual(result["timely_recall"]["n"], 3)
        self.assertEqual(result["timely_recall"]["count"], 1)
        self.assertAlmostEqual(result["binary_precision"]["all"]["rate"], 2/3)
        self.assertEqual(result["classification"]["observation_incomplete"], 1)
        validate_counts(rows, alarms, result)

    def test_no_alarm_grid_and_workpoint_freeze(self):
        grid = [{"alpha": 0., "measured_far": 0.}, {"alpha": .01, "measured_far": .03}, {"alpha": .1, "measured_far": .10}]
        self.assertEqual(pick(grid, .01)["alpha"], 0)
        points = workpoints({"cells": {n: {"grid": grid} for n in m7.CELLS}})
        self.assertEqual(points["matched"]["0.01"]["TU"]["alpha"], 0)
        for level in (0, .01, .05):
            self.assertEqual(first([7, 8, 9], [.5, .1, .01], level), alarm_at([7, 8, 9], [.5, .1, .01], level))


class ProtocolTest(unittest.TestCase):
    def test_complete_grid_keeps_unobserved_zero_far_positive_level(self):
        from types import SimpleNamespace
        normal = episode(tokens=70)
        key = trm3.trace_key(normal)
        cells = {n: {"_decisions": {key: SimpleNamespace(p_fused=[.5, .25], alarm_ends=lambda a: [1] if a >= .25 else [])},
                     "_fold_states": {"0": {"calibrations": {n: {"n_reference": 9}}}}} for n in m7.CELLS}
        grid = m7.complete_normal_grid(cells, [normal])
        self.assertEqual(pick(grid["cells"]["S"]["grid"], 0)["alpha"], .2)

    def test_full_calibration_includes_late_normal_maxima(self):
        def stream(i):
            scores = np.zeros(500); scores[450] = 10+i
            return trm3_g.EpisodeStream(key=f"s{i}", ends=np.arange(500), scores=scores, tags=["final"]*500, ordinals=np.arange(500))
        streams = [stream(i) for i in range(12)]
        cfg = trm3_g.config_for_g(["S"])
        full = trm3_g.calibrate_g(streams[:6], streams[6:], cfg, view=m7.VIEW, statistic="S", standardise=False, force_h=m7.FULL_H)
        old = trm3_g.calibrate_g(streams[:6], streams[6:], cfg, view=m7.VIEW, statistic="S", standardise=False, force_h=352)
        self.assertTrue((full.reference.channels["S"].path_maxima > 0).all())
        self.assertTrue((old.reference.channels["S"].path_maxima == 0).all())
        self.assertEqual(full.horizon["censored_endpoints"], 0)
        output = trm3_g.score_episode({"S": streams[0]}, full, cfg)
        self.assertEqual(sum(not o.horizon_censored for o in output), 500)

    def test_normal_then_restore_no_training(self):
        normals, target = [], []
        for i in range(9):
            a = episode(i, tokens=70, scenario=f"s{i}"); b = episode(i+30, "benign_control", tokens=70, scenario=f"s{i}")
            normals.extend((a, b)); target.extend((a, b, episode(i+60, "attack", tokens=70, scenario=f"s{i}")))
        table = {f"s{i}": i%3 for i in range(9)}
        pools = m7.harness.fold_pools(normals, table, folds=3, filtered_only=True, normals_only_eval=True)
        eval_pools = m7.harness.fold_pools(target, table, folds=3, filtered_only=True, normals_only_eval=False)
        for name in m7.CELLS:
            cell = m7.harness.run_cell_v32(name, args=m7.args_for("calibrate"), view=m7.VIEW, target_pool=normals, pools=pools, manifest_cell=None, cutpoints=(69, 71))
            state = {"folds": cell["_fold_states"]}
            with m7.restore_only():
                scored = m7.harness.run_cell_v32(name, args=m7.args_for("score"), view=m7.VIEW, target_pool=target, pools=eval_pools, manifest_cell=state, cutpoints=(69, 71))
            for key, decision in cell["_decisions"].items():
                np.testing.assert_array_equal(decision.p_fused, scored["_decisions"][key].p_fused)
            native = scored["metrics"]["positives_anchored"]
            self.assertIn("x_window", native["recall"])
            self.assertTrue(all("hit_plus_16" in row for row in native["per_episode"].values()))

    def test_metadata_covers_full_long_trace_and_phase_means(self):
        ep = episode(1, "attack", tokens=550)
        # Synthetic labels' X remains 30. Their real generated tail is fully observed.
        stat = TokenUnigram().fit([episode(2, tokens=70)], m7.VIEW)
        ends, scores, tags, ordinals = stat.stream(ep, m7.VIEW)
        key = trm3.trace_key(ep)
        stream = {"ends": ends, "raw": np.column_stack([scores]*3), "p": np.ones((len(ends), 3)),
                  "tags": np.array(tags), "ordinals": ordinals}
        old = {key: {f: v[:352] for f, v in stream.items()}}
        metadata, phases = m7.coverage_metadata([ep], {key: stream}, old, {ep.pair_group_id: 0}, (200, 400))
        self.assertTrue(metadata[key]["complete_16"])
        self.assertGreater(metadata[key]["look_count"], 352)
        self.assertEqual(phases[key]["whole"]["looks"], len(ends))
        self.assertEqual(metadata[key]["tokens_without_endpoint"], 550-len(ends))

    def test_guards_reject_sealed_and_attack_stage1(self):
        guard = m7.m6.M6AccessGuard(ROOT, "calibrate")
        for path in (ROOT/"artifacts/agent_v2/dataset_g/g_conf/trace.json", m7.RUN/"s--attack/trace.json", m7.OUT/"x.safetensors"):
            with self.assertRaises(PermissionError): guard.check_path(path)


if __name__ == "__main__": unittest.main()
