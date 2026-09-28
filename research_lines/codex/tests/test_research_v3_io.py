"""Loaders and labels for the TRM-3 (research v3) pools.

Unit tests use synthetic token pieces and synthetic ``LoadedTrace`` objects.  The
integration tests read only routine / normal material plus *labels* (never a routing
score), so they are safe to run before the TRM-3 freeze commit.  ``unittest`` style, to
match the rest of the suite; the file also runs unchanged under pytest.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase_a.normal_manifold import ManifoldTrace  # noqa: E402
from research_v2 import io as rio  # noqa: E402
from research_v2.harness import scenario_halves  # noqa: E402

torch.set_num_threads(8)

H384_AVAILABLE = (rio.DEFAULT_H384_DIR / "sample_index.jsonl").exists() and (
    rio.DEFAULT_H384_ENGAGEMENT_LABELS.exists()
)
C1_AVAILABLE = (rio.DEFAULT_C1_DIR / "sample_index.jsonl").exists()
CORE_AVAILABLE = (rio.DEFAULT_B2_DIR / "sample_index.jsonl").exists()


def _synthetic_trace(
    *,
    batch: str,
    arm: str,
    trace_id: str = "t",
    positive: bool = False,
    labels: dict | None = None,
    token_count: int = 4,
) -> rio.LoadedTrace:
    record = ManifoldTrace(
        batch=batch,
        trace_id=trace_id,
        pair_group_id="g",
        fold=0,
        arm=arm,
        workflow="knowledge_qa",
        workflow_family="knowledge_qa",
        channel="none",
        domain="None",
        positive=positive,
        completion_boundary=None,
        evidence_onset=None,
        trace_dir=Path("."),
        cache_file=Path("."),
    )
    return rio.LoadedTrace(
        record=record,
        top_k_ids=torch.zeros(16, token_count, 8, dtype=torch.long),
        token_ids=torch.zeros(token_count, dtype=torch.long),
        scenario_domain="d",
        labels=dict(labels or {}),
    )


def _legacy_arm_class(trace: rio.LoadedTrace) -> str:
    """The frozen pre-v3 implementation, kept here as the regression oracle."""

    if trace.positive:
        return "drift"
    if trace.arm == rio.ATTACK_ARM:
        return "resist"
    if trace.arm == "clean":
        return "clean"
    return "benign"


class LoadedTraceLabelsTest(unittest.TestCase):
    """(1) ``labels`` is an additive field: old constructors keep working."""

    def test_default_is_empty_and_positional_order_unchanged(self):
        trace = _synthetic_trace(batch="b2", arm="clean")
        self.assertEqual(trace.labels, {})
        legacy = rio.LoadedTrace(
            trace.record,
            torch.zeros(16, 3, 8, dtype=torch.long),
            torch.zeros(3, dtype=torch.long),
            "d",
            "present",
        )
        self.assertEqual(legacy.labels, {})
        self.assertEqual(legacy.brief_condition, "present")
        self.assertEqual(legacy.token_count, 3)

    def test_labels_are_per_instance(self):
        a = _synthetic_trace(batch="h384", arm="attack", trace_id="a")
        b = _synthetic_trace(batch="h384", arm="attack", trace_id="b")
        a.labels["engagement_class"] = "cross_domain_execution"
        self.assertEqual(b.labels, {})


class ArmClassTest(unittest.TestCase):
    """(4) batch-aware ``arm_class``; b1/b2 must be unchanged."""

    def test_b1_b2_match_the_legacy_rule(self):
        for batch in ("b1", "b2"):
            for arm, positive in (
                ("clean", False),
                ("benign_control", False),
                ("attack", False),
                ("attack", True),
            ):
                trace = _synthetic_trace(batch=batch, arm=arm, positive=positive)
                with self.subTest(batch=batch, arm=arm, positive=positive):
                    self.assertEqual(rio.arm_class(trace), _legacy_arm_class(trace))

    def test_h384_uses_the_engagement_class(self):
        cases = {
            ("clean", None): "clean",
            ("benign_control", None): "benign",
            ("attack", "cross_domain_execution"): "drift",
            ("attack", "bounded_engagement_resisted"): "resist",
            ("attack", "no_observable_engagement"): "resist",
        }
        for (arm, engagement), expected in cases.items():
            trace = _synthetic_trace(
                batch="h384",
                arm=arm,
                positive=engagement == "cross_domain_execution",
                labels={"engagement_class": engagement},
            )
            with self.subTest(arm=arm, engagement=engagement):
                self.assertEqual(rio.arm_class(trace), expected)

    def test_h384_attack_without_a_label_raises(self):
        trace = _synthetic_trace(batch="h384", arm="attack")
        with self.assertRaises(ValueError):
            rio.arm_class(trace)

    def test_c1(self):
        self.assertEqual(rio.arm_class(_synthetic_trace(batch="c1", arm="clean")), "clean")
        self.assertEqual(
            rio.arm_class(_synthetic_trace(batch="c1", arm="benign_control")), "benign"
        )
        with self.assertRaises(ValueError):
            rio.arm_class(_synthetic_trace(batch="c1", arm="attack"))

    @unittest.skipUnless(CORE_AVAILABLE, "b1/b2 artifacts absent")
    def test_core_outputs_are_identical_to_the_legacy_rule(self):
        core = rio.load_core()
        observed: dict[str, set[str]] = {}
        for batch, traces in core.items():
            for trace in traces:
                self.assertEqual(rio.arm_class(trace), _legacy_arm_class(trace))
                self.assertEqual(trace.labels, {})
                observed.setdefault(batch, set()).add(rio.arm_class(trace))
        self.assertEqual(observed["b1"], {"clean", "benign", "resist", "drift"})
        self.assertEqual(observed["b2"], {"clean", "benign", "resist", "drift"})


class EngagementOnsetMappingTest(unittest.TestCase):
    """Char-offset -> decode-token mapping, same rule as the frozen adjudication script."""

    def test_locate_char_span_tokens_synthetic(self):
        pieces = ["ab", "cde", "f", "gh"]  # offsets 0, 2, 5, 6
        self.assertEqual(rio.locate_char_span_tokens(pieces, 0, 2), (0, 0))
        self.assertEqual(rio.locate_char_span_tokens(pieces, 2, 5), (1, 1))
        self.assertEqual(rio.locate_char_span_tokens(pieces, 1, 6), (0, 2))
        self.assertEqual(rio.locate_char_span_tokens(pieces, 5, 8), (2, 3))
        # a span starting mid-token maps to the token containing its first character
        self.assertEqual(rio.locate_char_span_tokens(pieces, 3, 6), (1, 2))

    def test_locate_char_span_tokens_rejects_bad_spans(self):
        pieces = ["ab", "cd"]
        for start, end in ((-1, 2), (2, 2), (0, 99), (3, 1)):
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    rio.locate_char_span_tokens(pieces, start, end)

    def test_engagement_onset_from_tokens_round_trip(self):
        tokenizer = rio.load_tokenizer()
        text = "The quick brown fox jumps over the lazy dog and then writes a haiku."
        ids = tokenizer.encode(text, add_special_tokens=False).ids
        pieces = rio.decode_token_texts(ids)
        evidence = "writes a haiku"
        char_start = "".join(pieces).find(evidence)
        self.assertGreaterEqual(char_start, 0)
        expected = rio.locate_char_span_tokens(
            pieces, char_start, char_start + len(evidence)
        )[0]
        self.assertEqual(rio.engagement_onset_from_tokens(ids, evidence), expected)
        # first-occurrence tie-breaking, as in the frozen script
        self.assertLessEqual(rio.engagement_onset_from_tokens(ids, "he"), expected)

    def test_engagement_onset_rejects_absent_or_empty_evidence(self):
        tokenizer = rio.load_tokenizer()
        ids = tokenizer.encode("hello world", add_special_tokens=False).ids
        with self.assertRaises(ValueError):
            rio.engagement_onset_from_tokens(ids, "")
        with self.assertRaises(ValueError):
            rio.engagement_onset_from_tokens(ids, "not present at all")


class ScenarioDomainTest(unittest.TestCase):
    """(5) scenario-level domain for pools whose clean/benign arms carry no target domain."""

    def test_c1_scenario_domains_from_synthetic_rows(self):
        rows = [
            {
                "trace_id": "x--clean",
                "pair_group_id": "g1",
                "benign_family": "c1-baking-terms",
            },
            {
                "trace_id": "x--benign_control",
                "pair_group_id": "g1",
                "benign_family": "c1-baking-terms",
            },
            {
                "trace_id": "y--clean",
                "pair_group_id": "g2",
                "analysis_group_id": "c1-rhyme-terms",
            },
        ]
        self.assertEqual(
            rio._c1_scenario_domains(rows),
            {"g1": "c1-baking-terms", "g2": "c1-rhyme-terms"},
        )
        rows.append({"trace_id": "z", "pair_group_id": "g1", "benign_family": "c1-other"})
        with self.assertRaises(ValueError):
            rio._c1_scenario_domains(rows)

    def test_scenario_domain_map_detects_inconsistency(self):
        a = _synthetic_trace(batch="c1", arm="clean", trace_id="a")
        b = _synthetic_trace(batch="c1", arm="benign_control", trace_id="b")
        self.assertEqual(rio.scenario_domain_map([a, b]), {"g": "d"})
        b.scenario_domain = "other"
        with self.assertRaises(ValueError):
            rio.scenario_domain_map([a, b])


@unittest.skipUnless(H384_AVAILABLE, "h384 artifacts absent")
class H384LoaderTest(unittest.TestCase):
    """(2) the 240-trace horizon-384 replay pool.  Labels only -- no score is computed."""

    @classmethod
    def setUpClass(cls):
        cls.traces = rio.load_h384()

    def test_counts(self):
        self.assertEqual(len(self.traces), 240)
        self.assertEqual({t.batch for t in self.traces}, {"h384"})
        arms: dict[str, int] = {}
        classes: dict[str, int] = {}
        engagement: dict[str, int] = {}
        for trace in self.traces:
            arms[trace.arm] = arms.get(trace.arm, 0) + 1
            cls_ = rio.arm_class(trace)
            classes[cls_] = classes.get(cls_, 0) + 1
            if trace.arm == rio.ATTACK_ARM:
                key = trace.labels["engagement_class"]
                engagement[key] = engagement.get(key, 0) + 1
        self.assertEqual(arms, {"clean": 80, "benign_control": 80, "attack": 80})
        self.assertEqual(classes, {"clean": 80, "benign": 80, "drift": 40, "resist": 40})
        self.assertEqual(engagement, rio.H384_EXPECTED_ENGAGEMENT_COUNTS)
        self.assertEqual(sum(t.positive for t in self.traces), 40)
        self.assertEqual(len({t.trace_id for t in self.traces}), 240)

    def test_shapes_and_label_schema(self):
        for trace in self.traces:
            self.assertEqual(trace.labels["decode_token_count"], trace.token_count)
            self.assertTrue(1 <= trace.token_count <= 384)
            self.assertEqual(tuple(trace.top_k_ids.shape), (16, trace.token_count, 8))
            self.assertEqual(tuple(trace.token_ids.shape), (trace.token_count,))
            if trace.positive:
                self.assertIsNotNone(trace.labels["execution_onset"])
                self.assertEqual(trace.evidence_onset, trace.labels["execution_onset"])
                self.assertEqual(trace.completion_boundary, trace.labels["execution_onset"])
                self.assertIsNotNone(trace.labels["engagement_onset"])
                # the preregistered h384 anchor is the *end* of the evidence span
                self.assertGreaterEqual(
                    trace.labels["execution_onset"], trace.labels["engagement_onset"]
                )
            else:
                self.assertIsNone(trace.labels["execution_onset"])
                self.assertIsNone(trace.evidence_onset)
                self.assertIsNone(trace.completion_boundary)
            if trace.arm == rio.ATTACK_ARM:
                self.assertIn(trace.labels["engagement_class"], rio.ENGAGEMENT_CLASSES)
                self.assertIsInstance(trace.labels["support_resumed"], bool)
                self.assertIsNotNone(trace.labels["post192_class"])
                if trace.labels["engagement_class"] == "no_observable_engagement":
                    self.assertIsNone(trace.labels["engagement_onset"])
                else:
                    self.assertIsNotNone(trace.labels["engagement_onset"])
            else:
                self.assertIsNone(trace.labels["engagement_class"])
                self.assertIn("index_goal_plan_deviation_started", trace.labels)

    def test_onsets_agree_with_the_frozen_per_trace_adjudications(self):
        checked = 0
        for trace in self.traces:
            if trace.arm != rio.ATTACK_ARM:
                continue
            frozen = json.loads(
                (trace.record.trace_dir / "engagement_adjudication.json").read_text(
                    encoding="utf-8"
                )
            )
            span = frozen["engagement_evidence_output_token_span"]
            self.assertEqual(
                trace.labels["engagement_onset"], None if span is None else span[0]
            )
            self.assertEqual(
                trace.labels["engagement_class"], frozen["engagement_class"]
            )
            checked += 1
        self.assertEqual(checked, 80)

    def test_scenario_halves_and_domains(self):
        halves = scenario_halves(self.traces)
        self.assertEqual(len(halves), 80)
        self.assertEqual(set(halves.values()), {0, 1})
        self.assertEqual(sum(halves.values()), 40)
        self.assertEqual(len(rio.scenario_domain_map(self.traces)), 80)

    def test_probabilities_are_optional(self):
        one = rio.load_h384(with_probabilities=True)[0]
        self.assertEqual(tuple(one.probabilities().shape), (16, one.token_count, 64))

    def test_adjudication_hash_is_pinned(self):
        hashes = rio.research_v3_dataset_hashes()
        self.assertEqual(
            hashes["h384"]["engagement_adjudications_sha256"],
            rio.H384_ENGAGEMENT_LABELS_SHA256,
        )
        with self.assertRaises(ValueError):
            rio.read_h384_engagement_labels(
                labels_path=rio.DEFAULT_H384_DIR / "sample_index.jsonl"
            )


@unittest.skipUnless(C1_AVAILABLE, "c1 artifacts absent")
class C1LoaderTest(unittest.TestCase):
    """(3) the 320-trace C1 normal pool."""

    @classmethod
    def setUpClass(cls):
        cls.traces = rio.load_c1()

    def test_counts_and_folds(self):
        self.assertEqual(len(self.traces), 320)
        self.assertEqual({t.batch for t in self.traces}, {"c1"})
        self.assertFalse(any(t.positive for t in self.traces))
        self.assertEqual(len({t.pair_group_id for t in self.traces}), 160)
        arms: dict[str, int] = {}
        roles: dict[str, set[int]] = {}
        for trace in self.traces:
            arms[trace.arm] = arms.get(trace.arm, 0) + 1
            roles.setdefault(trace.labels["fold_role"], set()).add(trace.fold)
            self.assertEqual(trace.labels["preregistered_fold"], trace.fold)
            self.assertEqual(trace.labels["decode_token_count"], trace.token_count)
            self.assertEqual(tuple(trace.top_k_ids.shape), (16, trace.token_count, 8))
            self.assertIsNone(trace.evidence_onset)
            self.assertIsNone(trace.completion_boundary)
        self.assertEqual(arms, {"clean": 160, "benign_control": 160})
        self.assertEqual(
            roles,
            {
                "threshold_calibration": {0, 1, 2},
                "held_out_normal_evaluation": {3, 4},
            },
        )
        self.assertEqual({rio.arm_class(t) for t in self.traces}, {"clean", "benign"})

    def test_fold_filter_is_group_disjoint(self):
        fit = rio.load_c1(folds=[0, 1, 2])
        held = rio.load_c1(folds=[3, 4])
        self.assertEqual(len(fit), 200)
        self.assertEqual(len(held), 120)
        self.assertEqual({t.fold for t in fit}, {0, 1, 2})
        self.assertEqual(
            {t.labels["fold_role"] for t in held}, {"held_out_normal_evaluation"}
        )
        self.assertFalse(
            {t.pair_group_id for t in fit} & {t.pair_group_id for t in held}
        )
        self.assertEqual(set(scenario_halves(fit).values()), {0, 1})
        with self.assertRaises(ValueError):
            rio.load_c1(folds=[7])

    def test_scenario_domains(self):
        domains = rio.scenario_domain_map(self.traces)
        self.assertEqual(len(domains), 160)
        self.assertEqual(len(set(domains.values())), 16)


if __name__ == "__main__":
    unittest.main()
