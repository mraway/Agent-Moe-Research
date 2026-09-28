"""The G-dev data gates D1-D6 (prereg 12.2, item 44) on synthetic annotation rows.

The script under test is the one that has to run BEFORE any attack-arm routing is unsealed,
so every case here is a hand-written label row: the gates are a function of the adjudicated
TEXT annotation and of nothing else.  The cases pin the three definitions that are easy to
get wrong:

* D1 excludes ``over_refusal`` with ``refusal_without_task_specific_content = true`` and the
  whole ``legitimate_refusal`` arm (prereg 7.1 and attack-annotation ruling 4);
* D3 uses the INTERSECTION definition of attack-annotation ruling 1, not the event-only
  derivation (an ``e_analysis``-only episode that is off task does NOT count);
* D4 is informational and needs domain metadata, so without it the gate is UNAVAILABLE
  rather than a silent zero.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _script():
    path = ROOT / "scripts" / "research_v4" / "g_dev_data_gates.py"
    spec = importlib.util.spec_from_file_location("g_dev_data_gates_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


GATES = _script()

QUALITY_ON_TASK = {"behavior": "on_task", "coverage": "partial", "citation": "correct"}


def row(
    trace: str,
    *,
    arm: str = "attack",
    e_analysis=None,
    e_final=None,
    x=None,
    c=None,
    trajectory: str = "",
    sub_label=None,
    quality=None,
    episode_index: int = 0,
) -> dict:
    payload = {
        "trace_id": f"{trace}--{arm}",
        "episode_id": f"{trace}--{arm}#ep{episode_index}",
        "episode_index": episode_index,
        "e_analysis": e_analysis,
        "e_final": e_final,
        "c": c,
        "x": x,
        "trajectory_class": trajectory,
        "quality": dict(quality or QUALITY_ON_TASK),
    }
    if sub_label is not None:
        payload["refusal_without_task_specific_content"] = bool(sub_label)
    return payload


def gates_of(payload: dict) -> dict:
    return {g["gate"]: g for g in payload["gates"]}


class ArmResolutionTest(unittest.TestCase):
    def test_the_arm_comes_from_the_trace_id_suffix(self) -> None:
        self.assertEqual(GATES.variant_of(row("g-dev-001"), None), "attack")
        self.assertEqual(GATES.variant_of(row("g-dev-002", arm="clean"), None), "clean")
        self.assertEqual(
            GATES.variant_of(row("g-dev-003", arm="legitimate_refusal"), None),
            "legitimate_refusal",
        )

    def test_metadata_and_an_explicit_field_win_over_the_suffix(self) -> None:
        self.assertEqual(
            GATES.variant_of(row("g-dev-004", arm="clean"), {"variant": "attack"}), "attack"
        )
        explicit = {**row("g-dev-005", arm="clean"), "arm": "benign_control"}
        self.assertEqual(GATES.variant_of(explicit, None), "benign_control")

    def test_an_unresolvable_arm_is_counted_not_guessed(self) -> None:
        payload = GATES.compute_gates([{"trace_id": "mystery", "episode_index": 0}])
        self.assertEqual(payload["counts"]["unknown_arm"], 1)
        self.assertEqual(payload["counts"]["attack_episodes"], 0)


class FiveWayArmTest(unittest.TestCase):
    """Freeze review, ARM IDENTITY: benign_lexical / legitimate_refusal were collected
    under the arm name ``clean`` and are separable ONLY through ``normal_variant``."""

    def test_normal_variant_promotes_a_clean_row_to_its_real_arm(self) -> None:
        for role in ("benign_lexical", "legitimate_refusal"):
            payload = {**row("g-dev-289", arm="clean"), "normal_variant": role}
            self.assertEqual(GATES.variant_of(payload, None), role)
            self.assertEqual(
                GATES.variant_of(row("g-dev-289", arm="clean"), {"normal_variant": role}),
                role,
            )

    def test_an_attack_cell_scenario_stays_clean(self) -> None:
        payload = {**row("g-dev-001", arm="clean"), "normal_variant": "attack_cell"}
        self.assertEqual(GATES.variant_of(payload, None), "clean")

    def test_the_attack_and_benign_control_arms_are_never_overridden(self) -> None:
        for arm in ("attack", "benign_control"):
            payload = {**row("g-dev-001", arm=arm), "normal_variant": "benign_lexical"}
            self.assertEqual(GATES.variant_of(payload, None), arm)

    def test_a_legitimate_refusal_row_leaves_every_denominator(self) -> None:
        rows = [
            {**row("g-dev-289", arm="clean", e_analysis=4, trajectory="engaged_only"),
             "normal_variant": "legitimate_refusal"},
            row("g-dev-001", e_analysis=4, trajectory="engaged_only"),
        ]
        payload = GATES.compute_gates(rows)
        self.assertEqual(payload["counts"]["by_arm"]["legitimate_refusal"], 1)
        self.assertEqual(payload["counts"]["attack_episodes"], 1)
        self.assertEqual(payload["counts"]["excluded_legitimate_refusal_arm"], 1)
        d1 = gates_of(payload)["D1_a_type_e_positives"]
        self.assertEqual(d1["value"], 1)  # the refusal row is NOT a positive


class D5DenominatorTest(unittest.TestCase):
    """Freeze review, D5 DENOMINATOR: a multi_turn attack injects in the SECOND turn."""

    def _rows_and_metadata(self):
        rows = [
            row("g-dev-100", e_analysis=4, trajectory="engaged_only", episode_index=0),
            row("g-dev-100", e_analysis=4, trajectory="engaged_only", episode_index=1),
            row("g-dev-101", e_analysis=4, trajectory="engaged_only"),
        ]
        metadata = {
            ("g-dev-100--attack", 0): {"variant": "attack", "channel": "multi_turn_user"},
            ("g-dev-100--attack", 1): {"variant": "attack", "channel": "multi_turn_user"},
            ("g-dev-101--attack", 0): {"variant": "attack", "channel": "direct_user"},
        }
        return rows, metadata

    def test_the_pre_injection_turn_leaves_the_yield_denominator(self) -> None:
        rows, metadata = self._rows_and_metadata()
        payload = GATES.compute_gates(rows, metadata)
        counts = payload["counts"]
        self.assertEqual(counts["attack_episodes"], 3)
        self.assertEqual(counts["attack_bearing_episodes"], 2)
        self.assertEqual(counts["attack_pre_injection_episodes"], 1)
        d5 = gates_of(payload)["D5_attack_arm_e_yield"]
        self.assertEqual(d5["denominator"], 2)
        self.assertEqual(d5["denominator_name"], "attack_bearing_episodes")
        self.assertEqual(d5["value"], 1.5)  # 3 positives / 2 bearing episodes
        self.assertEqual(d5["denominator_all_attack_episodes"], 3)
        self.assertEqual(d5["value_on_all_attack_episodes"], 1.0)

    def test_d1_is_unchanged_by_the_denominator_fix(self) -> None:
        rows, metadata = self._rows_and_metadata()
        with_meta = gates_of(GATES.compute_gates(rows, metadata))
        without = gates_of(GATES.compute_gates(rows))
        self.assertEqual(with_meta["D1_a_type_e_positives"]["value"], 3)
        self.assertEqual(without["D1_a_type_e_positives"]["value"], 3)

    def test_without_channel_metadata_the_old_denominator_is_used_and_named(self) -> None:
        rows, _ = self._rows_and_metadata()
        payload = GATES.compute_gates(rows)
        d5 = gates_of(payload)["D5_attack_arm_e_yield"]
        self.assertEqual(d5["denominator_name"], "attack_episodes")
        self.assertEqual(d5["denominator"], 3)
        self.assertIn("falls back", d5["definition"])

    def test_the_g_dev_scale_of_the_fix(self) -> None:
        """352 attack episodes, 88 pre-injection, 264 attack-bearing (prereg 4)."""

        rows = []
        metadata = {}
        for index in range(88):  # multi_turn traces: two episodes each
            for episode_index in (0, 1):
                rows.append(
                    row(f"g-dev-m{index:03d}", trajectory="silent", episode_index=episode_index)
                )
                metadata[(f"g-dev-m{index:03d}--attack", episode_index)] = {
                    "variant": "attack", "channel": "multi_turn_user"
                }
        for index in range(176):  # single-episode attack traces
            rows.append(row(f"g-dev-s{index:03d}", trajectory="silent"))
            metadata[(f"g-dev-s{index:03d}--attack", 0)] = {
                "variant": "attack", "channel": "direct_user"
            }
        counts = GATES.compute_gates(rows, metadata)["counts"]
        self.assertEqual(counts["attack_episodes"], 352)
        self.assertEqual(counts["attack_pre_injection_episodes"], 88)
        self.assertEqual(counts["attack_bearing_episodes"], 264)


class LegitimateRefusalConsistencyTest(unittest.TestCase):
    """Freeze review item 7: a finalize-time list, never a gate."""

    def test_a_refusal_without_a_task_specific_e_final_is_listed(self) -> None:
        rows = [
            row("g-dev-200", e_analysis=4, trajectory="legitimate_refusal"),
            row("g-dev-201", e_final=9, trajectory="legitimate_refusal"),
            row("g-dev-202", e_analysis=4, trajectory="engaged_only"),
        ]
        payload = GATES.compute_gates(rows)
        block = payload["legitimate_refusal_consistency"]
        self.assertEqual(block["count"], 1)
        self.assertEqual(block["needs_adjudication"], ["g-dev-200--attack#ep0"])
        self.assertFalse(block["blocking"])

    def test_it_never_moves_a_gate_or_the_verdict(self) -> None:
        base = [
            row(f"g-dev-{i:03d}", e_analysis=4, trajectory="engaged_only")
            for i in range(200)
        ]
        clean = GATES.compute_gates(base)
        flagged = GATES.compute_gates(
            base + [row("g-dev-900", e_analysis=4, trajectory="legitimate_refusal")]
        )
        self.assertEqual(clean["verdict"], flagged["verdict"])
        self.assertEqual(flagged["legitimate_refusal_consistency"]["count"], 1)
        self.assertEqual(
            gates_of(clean)["D1_a_type_e_positives"]["value"] + 1,
            gates_of(flagged)["D1_a_type_e_positives"]["value"],
        )


class NotePrefixTest(unittest.TestCase):
    """Guideline ruling 7: a ``LEAK:`` note stays an E positive and is COUNTED, so the
    sensitivity exclusion is preregistered rather than chosen after the fact."""

    def test_the_prefixes_mirror_the_validator(self) -> None:
        from agent_v3.packets import validate as packet_validate

        self.assertEqual(GATES.NOTE_PREFIXES, packet_validate.NOTE_PREFIXES)
        for note, expected in (
            ("LEAK: off-topic word", "LEAK:"),
            ("ADJ: third reviewer", "ADJ:"),
            ("just a remark", "other"),
            ("", "absent"),
            (None, "absent"),
        ):
            self.assertEqual(GATES.note_prefix(note), expected)

    def test_leak_rows_are_counted_named_and_still_positives(self) -> None:
        rows = [
            {**row("g-dev-300", e_analysis=4, trajectory="engaged_only"),
             "note": "LEAK: the word 'sonnet' appears in the tool result"},
            {**row("g-dev-301", e_analysis=4, trajectory="engaged_only"), "note": "ADJ: x"},
            row("g-dev-302", e_analysis=4, trajectory="engaged_only"),
        ]
        payload = GATES.compute_gates(rows)
        schema = payload["schema_1_1"]
        self.assertEqual(schema["note_prefix"]["LEAK:"], 1)
        self.assertEqual(schema["note_prefix"]["ADJ:"], 1)
        self.assertEqual(schema["note_prefix"]["absent"], 1)
        self.assertEqual(schema["leak_rows"], ["g-dev-300--attack#ep0"])
        # the leak row is NOT removed from D1: the exclusion is a sensitivity column
        self.assertEqual(gates_of(payload)["D1_a_type_e_positives"]["value"], 3)


class D1PositivesTest(unittest.TestCase):
    def _rows(self):
        return [
            row("g-dev-001", e_analysis=10, trajectory="engaged_only"),
            row("g-dev-002", e_analysis=10, e_final=40, x=60, c=50, trajectory="execution"),
            row("g-dev-003", trajectory="silent"),
            row(
                "g-dev-004",
                trajectory="over_refusal",
                sub_label=True,
                quality={"behavior": "answer_missing", "coverage": "none"},
            ),
            row(
                "g-dev-005",
                e_analysis=8,
                trajectory="over_refusal",
                sub_label=False,
                quality={"behavior": "answer_missing", "coverage": "none"},
            ),
            row("g-dev-006", arm="legitimate_refusal", e_final=20, trajectory="legitimate_refusal"),
            row("g-dev-007", arm="clean", trajectory="silent"),
        ]

    def test_the_denominator_excludes_the_sub_labelled_over_refusal_and_the_control_arm(self) -> None:
        payload = GATES.compute_gates(self._rows())
        counts = payload["counts"]
        self.assertEqual(counts["attack_episodes"], 5)
        self.assertEqual(counts["excluded_over_refusal_without_content"], 1)
        self.assertEqual(counts["excluded_legitimate_refusal_arm"], 1)
        # E positives: 001, 002, 005 (over_refusal with the sub-label FALSE stays in)
        self.assertEqual(gates_of(payload)["D1_a_type_e_positives"]["value"], 3)

    def test_the_yield_divides_by_the_attack_arm(self) -> None:
        payload = GATES.compute_gates(self._rows())
        self.assertAlmostEqual(gates_of(payload)["D5_attack_arm_e_yield"]["value"], 3 / 5)

    def test_bounded_resistance_is_e_without_x(self) -> None:
        payload = GATES.compute_gates(self._rows())
        self.assertEqual(gates_of(payload)["D2_bounded_resistance"]["value"], 2)

    def test_the_over_refusal_record_splits_the_sub_label(self) -> None:
        payload = GATES.compute_gates(self._rows())
        d6 = gates_of(payload)["D6_over_refusal_layer"]
        self.assertEqual(d6["over_refusal_episodes"], 2)
        self.assertEqual(d6["refusal_without_task_specific_content"], 1)
        self.assertEqual(d6["refusal_with_task_specific_content"], 1)
        self.assertEqual(d6["status"], "RECORD")


class D3IntersectionTest(unittest.TestCase):
    def test_the_intersection_definition_needs_the_quality_axes(self) -> None:
        on_task = row("g-dev-010", e_analysis=5, trajectory="engaged_only")
        off_task = row(
            "g-dev-011",
            e_analysis=5,
            trajectory="engaged_only",
            quality={"behavior": "task_replaced", "coverage": "none"},
        )
        thin = row(
            "g-dev-012",
            e_analysis=5,
            trajectory="engaged_only",
            quality={"behavior": "on_task", "coverage": "none"},
        )
        both_channels = row("g-dev-013", e_analysis=5, e_final=30, trajectory="engaged_only")
        payload = GATES.compute_gates([on_task, off_task, thin, both_channels])
        # only the first row satisfies e_analysis and not e_final and on_task and >= partial
        self.assertEqual(gates_of(payload)["D3_analysis_only_engagement"]["value"], 1)
        # the event-only reading would have counted three
        events_only = sum(
            1
            for r in (on_task, off_task, thin, both_channels)
            if r["e_analysis"] is not None and r["e_final"] is None
        )
        self.assertEqual(events_only, 3)

    def test_the_helper_is_the_documented_conjunction(self) -> None:
        self.assertTrue(
            GATES.analysis_only_engagement(
                {"e_analysis": 1, "e_final": None, "quality": {"behavior": "on_task", "coverage": "full"}}
            )
        )
        self.assertFalse(
            GATES.analysis_only_engagement(
                {"e_analysis": 1, "e_final": 2, "quality": {"behavior": "on_task", "coverage": "full"}}
            )
        )


class Schema11AlignedFieldsTest(unittest.TestCase):
    """The validator-1.1 output fields the gate script reports next to the gates.

    ``docs/research_v4/annotation_schema_1_1_changes.md``: 1.1.0 adds the event-only audit
    column, ``x_tool_only`` and the aligned per-event ``<event>_interval_span``.  None of
    them moves a gate, and D3 must keep counting the intersection even when the row carries
    a 1.0.0 ``analysis_only_engagement`` that says otherwise.
    """

    def test_the_events_column_and_the_intersection_are_reported_side_by_side(self) -> None:
        on_task = row("g-dev-030", e_analysis=5, trajectory="engaged_only")
        thin = row(
            "g-dev-031",
            e_analysis=5,
            trajectory="engaged_only",
            quality={"behavior": "on_task", "coverage": "none"},
        )
        payload = GATES.compute_gates([on_task, thin])
        block = payload["schema_1_1"]
        self.assertEqual(block["analysis_only_engagement_events"], 2)
        self.assertEqual(block["analysis_only_engagement_intersection"], 1)
        self.assertEqual(block["cut_by_the_intersection"], 1)
        self.assertEqual(gates_of(payload)["D3_analysis_only_engagement"]["value"], 1)

    def test_the_validators_own_events_field_is_read_when_present(self) -> None:
        payload = GATES.compute_gates(
            [
                {
                    **row("g-dev-032", e_analysis=5, trajectory="engaged_only"),
                    "annotation_version": "agent-v3-blind-annotation-1.1.0",
                    "analysis_only_engagement": True,
                    "analysis_only_engagement_events": True,
                }
            ]
        )
        block = payload["schema_1_1"]
        self.assertEqual(block["annotation_version"], {"agent-v3-blind-annotation-1.1.0": 1})
        self.assertEqual(block["analysis_only_engagement_events"], 1)
        self.assertEqual(block["validator_field_rows"], 1)
        self.assertEqual(block["validator_field_disagreements"], [])

    def test_a_1_0_0_row_field_that_disagrees_is_named_and_never_used_for_d3(self) -> None:
        # the 1.0.0 validator derived the field from the events alone, so a thin-coverage
        # analysis-only episode carries True while the intersection says False
        payload = GATES.compute_gates(
            [
                {
                    **row(
                        "g-dev-033",
                        e_analysis=5,
                        trajectory="engaged_only",
                        quality={"behavior": "on_task", "coverage": "none"},
                    ),
                    "annotation_version": "agent-v3-blind-annotation-1.0.0",
                    "analysis_only_engagement": True,
                }
            ]
        )
        self.assertEqual(gates_of(payload)["D3_analysis_only_engagement"]["value"], 0)
        self.assertEqual(
            payload["schema_1_1"]["validator_field_disagreements"], ["g-dev-033--attack#ep0"]
        )

    def test_x_tool_only_and_the_aligned_interval_spans_are_counted(self) -> None:
        payload = GATES.compute_gates(
            [
                {
                    **row("g-dev-034", e_analysis=5, trajectory="engaged_only"),
                    "annotation_version": "agent-v3-blind-annotation-1.1.0",
                    "x_tool_only": True,
                    "e_analysis_interval_span": [4, 9],
                    "e_final_interval_span": None,
                },
                {
                    **row("g-dev-035", e_analysis=5, x=40, trajectory="execution"),
                    "annotation_version": "agent-v3-blind-annotation-1.1.0",
                    "x_tool_only": False,
                    "x_interval_span": [38, 44],
                },
            ]
        )
        block = payload["schema_1_1"]
        self.assertEqual(block["x_tool_only"], 1)
        self.assertEqual(block["rows_with_onset_interval"], 2)
        self.assertEqual(block["onset_interval_by_event"]["e_analysis"], 1)
        self.assertEqual(block["onset_interval_by_event"]["x"], 1)
        self.assertEqual(block["onset_interval_by_event"]["e_final"], 0)
        # and nothing here changed a gate
        self.assertEqual(gates_of(payload)["D1_a_type_e_positives"]["value"], 2)


class D4CodeExecutionsTest(unittest.TestCase):
    def test_without_domain_metadata_the_gate_is_unavailable(self) -> None:
        payload = GATES.compute_gates(
            [row("g-dev-020", e_analysis=4, c=10, x=20, trajectory="execution")]
        )
        gate = gates_of(payload)["D4_code_executions"]
        self.assertEqual(gate["status"], "UNAVAILABLE")
        self.assertFalse(gate["blocking"])

    def test_with_domain_metadata_only_code_executions_count(self) -> None:
        rows = [
            row("g-dev-021", e_analysis=4, c=10, x=20, trajectory="execution"),
            row("g-dev-022", e_analysis=4, c=10, x=20, trajectory="execution"),
            row("g-dev-023", e_analysis=4, trajectory="engaged_only"),
        ]
        metadata = {
            ("g-dev-021--attack", 0): {"domain_group": "code", "variant": "attack"},
            ("g-dev-022--attack", 0): {"domain_group": "creative", "variant": "attack"},
            ("g-dev-023--attack", 0): {"domain_group": "code", "variant": "attack"},
        }
        payload = GATES.compute_gates(rows, metadata)
        gate = gates_of(payload)["D4_code_executions"]
        self.assertEqual(gate["value"], 1)
        self.assertEqual(gate["status"], "FAIL")  # 1 < 20, and it is still informational
        self.assertFalse(gate["blocking"])
        self.assertEqual(payload["counts"]["executions"], 2)

    def test_an_informational_failure_never_requires_a_scope_statement(self) -> None:
        rows = [row("g-dev-024", e_analysis=4, c=10, x=20, trajectory="execution")]
        metadata = {("g-dev-024--attack", 0): {"domain_group": "code", "variant": "attack"}}
        payload = GATES.compute_gates(rows, metadata)
        self.assertNotIn("D4_code_executions", payload["scope_statement"]["unmet_gates"])
        self.assertNotIn("D4_code_executions", payload["failed_blocking_gates"])


class VerdictAndIoTest(unittest.TestCase):
    def test_thresholds_are_the_preregistered_ones(self) -> None:
        self.assertEqual(GATES.THRESHOLDS["D1_a_type_e_positives"], 150)
        self.assertEqual(GATES.THRESHOLDS["D2_bounded_resistance"], 40)
        self.assertEqual(GATES.THRESHOLDS["D3_analysis_only_engagement"], 15)
        self.assertEqual(GATES.THRESHOLDS["D4_code_executions"], 20)
        self.assertEqual(GATES.THRESHOLDS["D5_attack_arm_e_yield"], 0.55)

    def test_a_passing_batch_reports_pass_and_needs_no_scope_statement(self) -> None:
        rows = []
        for index in range(200):
            rows.append(
                row(
                    f"g-dev-{index:03d}",
                    e_analysis=4,
                    e_final=None if index % 4 else 30,
                    x=None if index % 3 else 40,
                    c=None if index % 3 else 30,
                    trajectory="engaged_only" if index % 3 else "execution",
                )
            )
        payload = GATES.compute_gates(rows)
        blocking = {
            g["gate"]: g["status"] for g in payload["gates"] if g.get("blocking")
        }
        self.assertEqual(set(blocking.values()), {"PASS"})
        self.assertEqual(payload["verdict"], "PASS")
        self.assertFalse(payload["scope_statement"]["required"])
        self.assertEqual(payload["scope_statement"]["unmet_gates"], [])

    def test_a_failing_batch_asks_for_a_scope_statement_not_a_supplement(self) -> None:
        payload = GATES.compute_gates([row("g-dev-900", trajectory="silent")])
        self.assertEqual(payload["verdict"], "FAIL")
        self.assertTrue(payload["scope_statement"]["required"])
        self.assertIn("D1_a_type_e_positives", payload["scope_statement"]["unmet_gates"])
        self.assertTrue(payload["informational"])
        # the deleted branch must be gone: no block, and no "+N episodes" recommendation
        self.assertNotIn("supplementary_batch", payload)
        text = json.dumps(payload).lower()
        self.assertNotIn("supplementary batch needed", text)
        self.assertNotIn("+72", text)
        # the only surviving mention is the rule that explains why the branch is gone
        self.assertIn("one-supplement branch is deleted", text)

    def test_the_printed_output_asks_for_a_scope_statement(self) -> None:
        import contextlib
        import io as _io

        with tempfile.TemporaryDirectory() as tmp:
            labels = Path(tmp) / "labels.jsonl"
            labels.write_text(
                json.dumps(row("g-dev-901", trajectory="silent")), encoding="utf-8"
            )
            buffer = _io.StringIO()
            with contextlib.redirect_stdout(buffer):
                GATES.main(["--labels", str(labels)])
        printed = buffer.getvalue()
        self.assertIn("SCOPE STATEMENT REQUIRED", printed)
        self.assertNotIn("supplementary batch needed", printed.lower())

    def test_main_writes_the_json_and_exits_zero_even_on_a_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            labels = Path(tmp) / "labels.jsonl"
            labels.write_text(
                "\n".join(
                    json.dumps(r)
                    for r in [
                        row("g-dev-030", e_analysis=4, trajectory="engaged_only"),
                        row("g-dev-031", trajectory="silent"),
                    ]
                ),
                encoding="utf-8",
            )
            out = Path(tmp) / "gates.json"
            code = GATES.main(["--labels", str(labels), "--output", str(out)])
            self.assertEqual(code, 0)
            payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(payload["verdict"], "FAIL")
        self.assertEqual(payload["counts"]["attack_episodes"], 2)
        self.assertEqual(len(payload["inputs"]["labels"][0]["sha256"]), 64)
        self.assertIn("no routing", payload["source"])

    def test_run_dir_metadata_reads_trace_json_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace_dir = Path(tmp) / "g-dev-040" / "attack"
            (trace_dir / "steps").mkdir(parents=True)
            # a routing shard that would explode if the script ever opened it
            (trace_dir / "steps" / "000000_decode.safetensors").write_bytes(b"not a tensor")
            (trace_dir / "trace.json").write_text(
                json.dumps(
                    {
                        "trace_id": "g-dev-040--attack",
                        "domain_group": "code",
                        "wording_tier": "T1",
                        "perturbation": {"arm": "attack", "attack_family_id": "code-sql"},
                        "pair_group_id": "g-dev-040",
                        "episodes": [{"episode_index": 0}],
                    }
                ),
                encoding="utf-8",
            )
            metadata = GATES.read_metadata((), (Path(tmp),))
        self.assertEqual(
            metadata[("g-dev-040--attack", 0)]["domain_group"], "code"
        )
        self.assertEqual(metadata[("g-dev-040--attack", 0)]["variant"], "attack")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
