"""Integration tests for TRM-3 (docs/research_v3/trm3_prereg.md).

Covers the seams between the three parallel components -- the loaders in
``research_v2.io``, the four new scorers and the fusion layer ``research_v2.trm3``:

* every prereg channel resolves through the registry with the frozen window widths;
* the ``top_coordinates`` attribution hook of prereg section 2 is populated for every
  channel that has one, and its contributions reconstruct the window score exactly for
  the three additive channels (S, B-S, J);
* adding the hook did not move any score stream;
* ``online`` emits the section-2 evidence fields end to end.

Synthetic routing tensors only -- no dataset, no label, no attack material.
"""

from __future__ import annotations

import unittest
import unittest.mock
from pathlib import Path
from typing import Any

import torch

import sys

from research_v2 import scorers, trm3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "research_v3"))
import run_trm3  # noqa: E402
from research_v2.scorers.trm3_s import EXPERTS, LAYERS, TOP_K


class FakeTrace:
    """The only contract every scorer relies on: ``top_k_ids`` [16, T, 8]."""

    def __init__(self, top_k_ids: torch.Tensor, trace_id: str = "t0") -> None:
        self.top_k_ids = top_k_ids
        self.trace_id = trace_id
        self.arm = "clean"
        self.positive = False
        self.batch = "synthetic"
        self.pair_group_id = trace_id
        self.scenario_domain = "synthetic"
        self.labels: dict[str, Any] = {}


def make_ids(tokens: int, seed: int, pool: int = EXPERTS) -> torch.Tensor:
    """Random top-8 selections drawn from the first ``pool`` experts of every layer.

    A routine pool narrower than 64 leaves the remaining coordinates rare (channel S) or
    unseen (B-U), which is what the attribution hooks are exercised against.
    """

    generator = torch.Generator().manual_seed(seed)
    ids = torch.empty((LAYERS, tokens, TOP_K), dtype=torch.long)
    for layer in range(LAYERS):
        for token in range(tokens):
            ids[layer, token] = torch.randperm(pool, generator=generator)[:TOP_K]
    return ids


# Routine uses experts 0-15 only; the target also reaches into 16-63, so the rare set of
# channel S and the unseen set of B-U are both non-empty on the scored windows.
ROUTINE = [FakeTrace(make_ids(48, 1000 + i, pool=16), f"routine-{i}") for i in range(12)]
TARGET = FakeTrace(make_ids(40, 77), "target")


class ChannelInterfaceTests(unittest.TestCase):
    """Every prereg channel is reachable through the registry at its frozen width."""

    def test_frozen_window_widths(self) -> None:
        for name, alpha, scorer_name, width in (
            ("S", trm3.ALPHA_S, "trm3_s", 8),
            ("M", trm3.ALPHA_M, "wgm", 8),
            ("J", trm3.ALPHA_J, "trm3_j", 4),
            ("U", trm3.ALPHA, "unseen_only", 8),
            ("P", trm3.ALPHA, "surprisal_marginal", 8),
        ):
            with self.subTest(channel=name):
                spec = trm3.channel_spec(name, alpha)
                self.assertEqual(spec.scorer, scorer_name)
                self.assertEqual(spec.window_width, width)
                built = scorers.build(spec.scorer, dict(spec.config))
                self.assertEqual(int(built.window_width), width)
                self.assertFalse(getattr(built, "requires_positives", False))

    def test_every_channel_state_scores_and_describes(self) -> None:
        for name in ("S", "M", "J", "U", "P"):
            with self.subTest(channel=name):
                spec = trm3.channel_spec(name, 0.10)
                state = trm3.fit_channels(ROUTINE, [spec])[name]
                ends, values = state.score(TARGET)
                self.assertEqual(ends.shape, values.shape)
                self.assertEqual(int(ends[0]), spec.window_width - 1)
                self.assertEqual(int(ends[-1]), int(TARGET.top_k_ids.shape[1]) - 1)


class TopCoordinateTests(unittest.TestCase):
    """Prereg section 2 output field ``top-3 contributing coordinates``."""

    def _contributions_sum_to_score(self, channel: str, tolerance: float) -> None:
        spec = trm3.channel_spec(channel, 0.10)
        state = trm3.fit_channels(ROUTINE, [spec])[channel]
        ends, values = state.score(TARGET)
        end = int(ends[len(ends) // 2])
        score = float(values[len(ends) // 2])
        everything = state.top_coordinates(TARGET, end, n=LAYERS * EXPERTS * 4)
        self.assertTrue(everything, "expected a non-empty attribution")
        total = sum(float(item["contribution"]) for item in everything)
        self.assertAlmostEqual(total, score, delta=tolerance)
        top3 = state.top_coordinates(TARGET, end, n=3)
        self.assertEqual(len(top3), 3)
        self.assertEqual(top3, everything[:3])
        ordered = [float(item["contribution"]) for item in top3]
        self.assertEqual(ordered, sorted(ordered, reverse=True))

    def test_channel_s_contributions_reconstruct_the_window_score(self) -> None:
        self._contributions_sum_to_score("S", 1e-9)

    def test_channel_j_contributions_reconstruct_the_window_score(self) -> None:
        self._contributions_sum_to_score("J", 1e-9)

    def test_baseline_surprisal_contributions_reconstruct_the_window_score(self) -> None:
        self._contributions_sum_to_score("P", 1e-9)

    def test_unseen_only_reports_unseen_coordinates_only(self) -> None:
        spec = trm3.channel_spec("U", 0.10)
        state = trm3.fit_channels(ROUTINE, [spec])["U"]
        unseen = state.state.unseen_mask
        for end in (7, 20, 39):
            for item in state.top_coordinates(TARGET, end, n=3):
                self.assertTrue(bool(unseen[item["layer"], item["expert"]]))
                self.assertGreaterEqual(item["window_selections"], 1)

    def test_partial_and_out_of_range_windows_return_empty(self) -> None:
        for channel in ("S", "J", "U", "P"):
            spec = trm3.channel_spec(channel, 0.10)
            state = trm3.fit_channels(ROUTINE, [spec])[channel]
            with self.subTest(channel=channel):
                self.assertEqual(state.top_coordinates(TARGET, 0, n=3), [])
                self.assertEqual(state.top_coordinates(TARGET, -1, n=3), [])
                self.assertEqual(state.top_coordinates(TARGET, 10_000, n=3), [])
                self.assertEqual(state.top_coordinates(TARGET, 20, n=0), [])

    def test_channel_m_keeps_the_documented_empty_fallback(self) -> None:
        # ``wgm`` is the frozen CAND-A scorer and must not grow a hook.
        state = trm3.fit_channels(ROUTINE, [trm3.channel_spec("M", trm3.ALPHA_M)])["M"]
        self.assertEqual(state.top_coordinates(TARGET, 20, n=3), [])

    def test_attribution_hook_does_not_move_any_score(self) -> None:
        """The window score is a pure function of ``top_k_ids`` and the fitted table."""

        for channel in ("S", "J", "U", "P"):
            spec = trm3.channel_spec(channel, 0.10)
            state = trm3.fit_channels(ROUTINE, [spec])[channel]
            before_ends, before = state.score(TARGET)
            state.top_coordinates(TARGET, 20, n=8)
            after_ends, after = state.score(TARGET)
            with self.subTest(channel=channel):
                self.assertEqual(list(before_ends), list(after_ends))
                self.assertEqual(list(before), list(after))


class EvidenceEmissionTests(unittest.TestCase):
    """``online`` fills the section-2 evidence fields when ``emit_evidence`` is set."""

    def _calibration(self, config: trm3.TRM3Config):
        states = trm3.fit_channels(ROUTINE, list(config.channels))
        calibration = trm3.calibrate(ROUTINE, states, config, regime=None, pool="unit")
        return states, calibration

    def _run(self, variant: str, **overrides):
        config = trm3.config_for_variant(variant, **overrides)
        states, calibration = self._calibration(config)
        calibration.register_external([TARGET])
        streams = {name: state.score(TARGET) for name, state in states.items()}
        outputs = trm3.online(streams, calibration, config, trace=TARGET, states=states)
        return config, outputs

    def test_schema_row_and_evidence_window(self) -> None:
        config, outputs = self._run("trm3", emit_evidence=True)
        self.assertTrue(outputs)
        for output in outputs:
            start, end = output.evidence_window
            self.assertLessEqual(end - start, config.evidence_window - 1)
            self.assertGreaterEqual(start, 0)
            self.assertLessEqual(start, end)
            row = output.schema_row(
                trace_id="target", batch="synthetic", arm="clean",
                klass="clean", calibration="unit",
            )
            for field in ("trace_id", "batch", "arm", "class", "calibration", "k", "end",
                          "p_fused", "state", "temporal_state", "e0", "attribution",
                          "regime_flag"):
                self.assertIn(field, row)

    def test_a_non_silent_endpoint_carries_top_coordinates(self) -> None:
        """Single-channel variants can actually leave SILENT at this calibration size."""

        # J is deliberately not in this list: on uniform synthetic routing the pair
        # tables carry no coupling signal, so ``j_only`` stays SILENT.  The J hook itself
        # is covered directly by TopCoordinateTests.
        for variant in ("s_only", "surprisal_marginal"):
            with self.subTest(variant=variant):
                _, outputs = self._run(variant, emit_evidence=True)
                loud = [o for o in outputs if o.state != trm3.STATE_SILENT]
                self.assertTrue(loud, f"{variant} stayed SILENT on the whole episode")
                for output in loud:
                    self.assertTrue(output.top_coordinates)
                    self.assertLessEqual(len(output.top_coordinates), 3)

    def test_evidence_is_absent_without_the_flag(self) -> None:
        config, outputs = self._run("s_only")
        self.assertFalse(config.emit_evidence)
        self.assertTrue(outputs)
        self.assertTrue(all(not o.top_coordinates for o in outputs))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


class GateReferenceTests(unittest.TestCase):
    """G8 must be decided against the FROZEN CAND-A, not against this run's own m_only."""

    def test_frozen_reference_is_readable_for_both_directions(self) -> None:
        for case, expected_id in (
            ("b1_to_b2", "S1:b1_to_b2|w8|routine=cb|mode=D|alpha=0.1|reading=max"),
            ("b2_to_b1", "S1:b2_to_b1|w8|routine=cb|mode=D|alpha=0.1|reading=max"),
        ):
            with self.subTest(case=case):
                block = run_trm3.frozen_cand_a_onsets_per_1000(case)
                self.assertIsNotNone(block, "frozen CAND-A artifact is missing")
                self.assertEqual(block["candidate_id"], expected_id)
                self.assertGreater(block["value"], 0.0)

    def test_unknown_case_and_missing_artifact_return_none(self) -> None:
        self.assertIsNone(run_trm3.frozen_cand_a_onsets_per_1000("no_such_case"))
        self.assertIsNone(
            run_trm3.frozen_cand_a_onsets_per_1000("b1_to_b2", path=Path("/nonexistent.json"))
        )

    def test_explicit_reference_overrides_the_in_run_fallback(self) -> None:
        cells = {
            "m_only": {"sets": {"target": {"endpoint": {"alarm_onsets_per_1000_eligible": 5.0}}}},
            "trm3": {"sets": {"target": {"endpoint": {"alarm_onsets_per_1000_eligible": 2.0}}}},
        }
        fallback = run_trm3.assemble_gates(cells, "b2", "D")
        self.assertTrue(fallback["m_only"]["G8"]["pass"])  # self-referential -> vacuous
        frozen = run_trm3.assemble_gates(cells, "b2", "D", cand_a_onsets=1.863387105361231)
        # limit = 1.5 x 1.8634 = 2.795: m_only (5.0) now fails, trm3 (2.0) still passes
        self.assertFalse(frozen["m_only"]["G8"]["pass"])
        self.assertTrue(frozen["trm3"]["G8"]["pass"])


class AmendmentWiringTests(unittest.TestCase):
    """Runner-side wiring of the prereg v1.1 amendments (section 11)."""

    class Labelled:
        def __init__(self, trace_id: str, arm: str, labels: dict, batch: str = "h384") -> None:
            self.trace_id = trace_id
            self.arm = arm
            self.labels = labels
            self.batch = batch
            self.positive = False
            self.pair_group_id = trace_id.split("--")[0]

    def test_spontaneous_drift_is_a_labelled_exclusion(self) -> None:
        traces = [
            self.Labelled(
                trace_id,
                "benign_control",
                {"behavior_label": "goal_drift", "index_goal_plan_deviation_started": True},
            )
            for trace_id in run_trm3.H384_SPONTANEOUS_DRIFT_IDS
        ]
        traces.append(
            self.Labelled("b2-f0-001-x--clean", "clean", {"behavior_label": "routine"})
        )
        # an attack-arm trace carrying the same flag is NOT a routine exclusion
        traces.append(
            self.Labelled("b2-f0-001-x--attack", "attack", {"behavior_label": "goal_drift"})
        )
        found = run_trm3.spontaneous_drift_traces(traces)
        self.assertEqual(
            sorted(t.trace_id for t in found), sorted(run_trm3.H384_SPONTANEOUS_DRIFT_IDS)
        )
        keys = {trm3.trace_key(t) for t in found}
        self.assertEqual(len(run_trm3.drop_keys(traces, keys)), 2)

    def test_h384_exclusion_list_is_cross_checked(self) -> None:
        traces = [
            self.Labelled(
                run_trm3.H384_SPONTANEOUS_DRIFT_IDS[0],
                "benign_control",
                {"behavior_label": "goal_drift"},
            )
        ]
        with self.assertRaises(SystemExit):
            run_trm3.spontaneous_drift_traces(traces)

    def test_h384_anchor_pair(self) -> None:
        traces = [
            self.Labelled(
                "exec",
                "attack",
                {
                    "engagement_class": "cross_domain_execution",
                    "engagement_onset": 40,
                    "execution_onset": 46,
                },
            ),
            self.Labelled(
                "bounded",
                "attack",
                {
                    "engagement_class": "bounded_engagement_resisted",
                    "engagement_onset": 12,
                    "execution_onset": None,
                },
            ),
            self.Labelled(
                "silent",
                "attack",
                {"engagement_class": "no_observable_engagement", "engagement_onset": None},
            ),
        ]
        primary, secondary = run_trm3.h384_anchors(traces)
        # amendment 3: the evidence-span START anchors executions AND bounded resisters
        self.assertEqual(primary["h384|exec"], 40)
        self.assertEqual(primary["h384|bounded"], 12)
        self.assertIsNone(primary["h384|silent"])
        self.assertEqual(secondary["h384|exec"], 46)
        self.assertIsNone(secondary["h384|bounded"])

    def test_apply_g5_uses_both_columns(self) -> None:
        def column(far: float) -> dict:
            return {
                "variants": {"trm3": {"sets": {"target": {"far": {"pooled": far}}}}},
                "gates": {"trm3": trm3.check_gates({"far_pooled": far})},
            }

        columns = {"D": column(0.10), "C1": column(0.30)}
        detail = run_trm3.apply_g5(columns)
        self.assertIs(detail["trm3"]["pass"], False)
        self.assertAlmostEqual(detail["trm3"]["value"], 0.20, places=12)
        for name in ("D", "C1"):
            self.assertIs(columns[name]["gates"]["trm3"]["G5"]["pass"], False)
            self.assertIn("G5", columns[name]["gates"]["trm3"]["summary"]["failed"])

    def test_c1_roles_are_disjoint_and_preregistered(self) -> None:
        """Amendment 2: fit fold 0, calibration folds 1-3, held-out fold 4."""

        self.assertEqual(run_trm3.C1_FIT_FOLDS, (0,))
        self.assertEqual(run_trm3.C1_CALIBRATION_FOLDS, (1, 2, 3))
        self.assertEqual(run_trm3.C1_HELDOUT_FOLDS, (4,))
        roles = [
            set(run_trm3.C1_FIT_FOLDS),
            set(run_trm3.C1_CALIBRATION_FOLDS),
            set(run_trm3.C1_HELDOUT_FOLDS),
        ]
        for a, b in ((0, 1), (0, 2), (1, 2)):
            self.assertEqual(roles[a] & roles[b], set())
        self.assertEqual(
            (
                run_trm3.EXPECTED_C1_FIT_TRACES,
                run_trm3.EXPECTED_C1_CALIBRATION_TRACES,
                run_trm3.EXPECTED_C1_HELDOUT_TRACES,
                run_trm3.EXPECTED_C1_HELDOUT_GROUPS,
            ),
            (80, 180, 60, 30),
        )

    def test_c1_heldout_shape_is_asserted(self) -> None:
        class Row:
            def __init__(self, index: int) -> None:
                self.pair_group_id = f"g{index // 2}"

        run_trm3.assert_c1_heldout([Row(i) for i in range(60)])
        with self.assertRaises(SystemExit):
            run_trm3.assert_c1_heldout([Row(i) for i in range(58)])

    def test_label_count_assertions(self) -> None:
        """Amendment 1: a missing or changed label file is a hard error."""

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            product = Path(tmp) / "product.jsonl"
            topic = Path(tmp) / "topic.jsonl"
            product.write_text(
                "".join(
                    '{"trace_id": "p%d", "product_onset": 3}\n' % i
                    for i in range(run_trm3.EXPECTED_PRODUCT_ONSET_ROWS)
                ),
                encoding="utf-8",
            )
            rows = [
                '{"trace_id": "t%d", "topic_entry_onset": %s}\n'
                % (i, 5 if i < run_trm3.EXPECTED_TOPIC_ENTRY_ANCHORED else "null")
                for i in range(run_trm3.EXPECTED_TOPIC_ENTRY_ROWS)
            ]
            topic.write_text("".join(rows), encoding="utf-8")
            with unittest.mock.patch.object(run_trm3, "PRODUCT_ONSET_FILE", product), \
                    unittest.mock.patch.object(run_trm3, "TOPIC_ENTRY_FILE", topic):
                block = run_trm3.assert_core_label_files()
                self.assertEqual(block["topic_entry_anchored"], 14)
                topic.write_text("".join(rows[:-1]), encoding="utf-8")
                with self.assertRaises(SystemExit):
                    run_trm3.assert_core_label_files()
            with unittest.mock.patch.object(
                run_trm3, "PRODUCT_ONSET_FILE", Path(tmp) / "nope.jsonl"
            ):
                with self.assertRaises(SystemExit):
                    run_trm3.assert_core_label_files()

    def test_h384_engagement_counts_are_asserted(self) -> None:
        def pool(execution: int) -> list:
            traces = []
            for index in range(execution):
                traces.append(self.Labelled(f"e{index}", "attack", {"engagement_class": "cross_domain_execution"}))
            for index in range(5):
                traces.append(
                    self.Labelled(f"b{index}", "attack", {"engagement_class": "bounded_engagement_resisted"})
                )
            for index in range(35):
                traces.append(
                    self.Labelled(f"s{index}", "attack", {"engagement_class": "no_observable_engagement"})
                )
            traces.append(self.Labelled("c0", "clean", {"engagement_class": None}))
            return traces

        self.assertEqual(
            run_trm3.assert_h384_label_counts(pool(40)),
            run_trm3.EXPECTED_H384_ENGAGEMENT_COUNTS,
        )
        with self.assertRaises(SystemExit):
            run_trm3.assert_h384_label_counts(pool(39))

    def test_data_discipline_guard(self) -> None:
        """Amendment 7: a non-smoke run needs a clean tree at the freeze commit."""

        head = "a" * 40
        with unittest.mock.patch.object(run_trm3, "working_tree_status", return_value=[]), \
                unittest.mock.patch.object(run_trm3, "git_output", return_value=head):
            block = run_trm3.data_discipline_guard(head, smoke=False)
            self.assertFalse(block["dirty"])
            self.assertTrue(block["head_is_freeze_commit"])
            self.assertIsNotNone(block["prereg_sha256"])
            with self.assertRaises(SystemExit):
                run_trm3.data_discipline_guard(None, smoke=False)
        with unittest.mock.patch.object(
            run_trm3, "working_tree_status", return_value=["?? src/x.py"]
        ), unittest.mock.patch.object(run_trm3, "git_output", return_value=head):
            with self.assertRaises(SystemExit):
                run_trm3.data_discipline_guard(head, smoke=False)
            # the smoke path is always allowed, and still records the dirty flag
            smoke_block = run_trm3.data_discipline_guard(None, smoke=True)
            self.assertTrue(smoke_block["dirty"])
        with unittest.mock.patch.object(run_trm3, "working_tree_status", return_value=[]), \
                unittest.mock.patch.object(
                    run_trm3, "git_output", side_effect=["", head, "b" * 40]
                ):
            with self.assertRaises(SystemExit):
                run_trm3.data_discipline_guard("b" * 40, smoke=False)

    def test_artifacts_symlink_is_ignored_by_the_guard(self) -> None:
        with unittest.mock.patch.object(
            run_trm3,
            "git_output",
            return_value="?? artifacts\n M src/research_v2/io.py",
        ):
            self.assertEqual(run_trm3.working_tree_status(), [" M src/research_v2/io.py"])
        with unittest.mock.patch.object(run_trm3, "git_output", return_value="?? artifacts"):
            self.assertEqual(run_trm3.working_tree_status(), [])

    def test_g7_is_wired_on_a_c1_heldout_target(self) -> None:
        cells = {
            "trm3": {
                "sets": {
                    "target": {
                        "far": {"pooled": 0.10, "matched_group": 0.28},
                        "endpoint": {"alarm_onsets_per_1000_eligible": 1.0},
                    }
                }
            }
        }
        gates = run_trm3.assemble_gates(cells, "c1_heldout", "C1")
        self.assertEqual(gates["trm3"]["G7"]["status"], "evaluated")
        self.assertIs(gates["trm3"]["G7"]["pass"], False)
        # and it stays not_evaluated when no held-out fold was scored at all
        other = run_trm3.assemble_gates(cells, "b2", "D")
        self.assertEqual(other["trm3"]["G7"]["status"], "not_evaluated")

    def test_matched_alpha_comparison_block(self) -> None:
        config = trm3.config_for_variant("trm3")
        cells = {
            "trm3": {"alpha_budget": {"alpha": 0.10, "by_half": {
                "0": {"n_reference": 80, "effective": trm3.effective_alpha(config, 80)},
                "1": {"n_reference": 80, "effective": trm3.effective_alpha(config, 80)},
            }, "alpha_eff": 7 / 81, "alpha_eff_min": 7 / 81}},
            "m_only": {"alpha_budget": {"alpha": 0.10, "by_half": {
                "0": {"n_reference": 80}, "1": {"n_reference": 80},
            }, "alpha_eff": 8 / 81, "alpha_eff_min": 8 / 81}},
        }
        block = run_trm3.matched_alpha_comparison(cells, {}, anchors=None)
        self.assertEqual(block["status"], "evaluated")
        self.assertAlmostEqual(block["trm3_alpha_eff"], 7 / 81, places=12)
        self.assertAlmostEqual(block["alpha_matched"], 7 / 81, places=12)
        self.assertLess(block["alpha_matched"], block["b_m_alpha_eff_nominal"])
        self.assertEqual(
            run_trm3.matched_alpha_comparison({"trm3": cells["trm3"]}, {}, anchors=None)[
                "status"
            ],
            "not_evaluated",
        )

    def test_unseen_only_degeneracy_flag(self) -> None:
        config = trm3.config_for_variant("unseen_only")
        narrow = trm3.fit_channels(ROUTINE, list(config.channels))  # experts 0-15 only
        self.assertIsNone(run_trm3.degenerate_baseline(config, narrow))
        full = trm3.fit_channels(
            [FakeTrace(make_ids(64, 5000 + i, pool=EXPERTS), f"full-{i}") for i in range(8)],
            list(config.channels),
        )
        message = run_trm3.degenerate_baseline(config, full)
        self.assertIsNotNone(message)
        self.assertIn("degenerate", message)
        self.assertIsNone(run_trm3.degenerate_baseline(trm3.config_for_variant("m_only"), full))
