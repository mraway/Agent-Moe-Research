"""Unit tests for the research-v2 shared evaluation harness (protocol v2, spec section 1).

CPU-only, synthetic data, no artifacts required for the core tests.  The two acceptance
tests that need the frozen development artifacts skip themselves when those are absent.
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase_a.normal_manifold import ManifoldTrace  # noqa: E402
from research_v2 import harness as H  # noqa: E402
from research_v2.io import LoadedTrace  # noqa: E402
from research_v2.readings import build_readings, read_cusum, read_ewma, read_persist_m  # noqa: E402
from research_v2.scorers import available, build  # noqa: E402

torch.set_num_threads(8)

P1_RESULT = ROOT / "artifacts" / "agent_v2" / "normal_manifold_p1_knn" / "result.json"


def make_trace(
    trace_id: str,
    *,
    batch: str = "b1",
    arm: str = "clean",
    positive: bool = False,
    onset: int | None = None,
    completion: int | None = None,
    tokens: int = 64,
    fold: int = 0,
    pair_group_id: str | None = None,
    domain: str = "poetry",
    seed: int = 0,
    drift_from: int | None = None,
) -> LoadedTrace:
    """Synthetic trace: routine tokens use experts 0-7, drift tokens experts 40-47."""

    generator = torch.Generator().manual_seed(seed)
    top_k = torch.zeros(16, tokens, 8, dtype=torch.long)
    for layer in range(16):
        for position in range(tokens):
            offset = 40 if (drift_from is not None and position >= drift_from) else 0
            base = torch.randperm(16, generator=generator)[:8] + offset
            top_k[layer, position] = base
    token_ids = torch.randint(0, 900, (tokens,), generator=generator, dtype=torch.long)
    if drift_from is not None:
        token_ids[drift_from:] = torch.randint(
            2000, 2100, (tokens - drift_from,), generator=generator, dtype=torch.long
        )
    record = ManifoldTrace(
        batch=batch,
        trace_id=trace_id,
        pair_group_id=pair_group_id or f"{batch}-pg-{trace_id}",
        fold=fold,
        arm=arm,
        workflow="knowledge_qa",
        workflow_family="knowledge_qa",
        channel="none" if arm != "attack" else "user_message",
        domain=domain if arm == "attack" else "None",
        positive=positive,
        completion_boundary=completion,
        evidence_onset=onset,
        trace_dir=Path("/nonexistent"),
        cache_file=Path("/nonexistent"),
    )
    return LoadedTrace(
        record=record,
        top_k_ids=top_k,
        token_ids=token_ids,
        scenario_domain=domain,
    )


def synthetic_batches(tokens: int = 96) -> dict[str, tuple[LoadedTrace, ...]]:
    batches: dict[str, tuple[LoadedTrace, ...]] = {}
    for batch, scenarios in (("b1", 12), ("b2", 12)):
        traces = []
        for index in range(scenarios):
            group = f"{batch}-pg{index:02d}"
            fold = index % 5
            domain = ["poetry", "cooking", "legal_analysis", "fiction"][index % 4]
            seed = hash((batch, index)) % 10000
            drift = index % 3 == 0
            traces.append(
                make_trace(
                    f"{batch}-{index}-clean",
                    batch=batch,
                    arm="clean",
                    tokens=tokens,
                    fold=fold,
                    pair_group_id=group,
                    domain=domain,
                    seed=seed,
                )
            )
            traces.append(
                make_trace(
                    f"{batch}-{index}-benign",
                    batch=batch,
                    arm="benign_control",
                    tokens=tokens,
                    fold=fold,
                    pair_group_id=group,
                    domain=domain,
                    seed=seed + 1,
                )
            )
            traces.append(
                make_trace(
                    f"{batch}-{index}-attack",
                    batch=batch,
                    arm="attack",
                    positive=drift,
                    onset=40 if drift else None,
                    completion=46 if drift else None,
                    tokens=tokens,
                    fold=fold,
                    pair_group_id=group,
                    domain=domain,
                    seed=seed + 2,
                    drift_from=40 if drift else None,
                )
            )
        batches[batch] = tuple(traces)
    return batches


class ReadingTests(unittest.TestCase):
    def test_persist_m_is_running_minimum(self) -> None:
        z = torch.tensor([1.0, 3.0, 2.0, 5.0])
        out = read_persist_m(z, 2)
        self.assertEqual(out[0].item(), float("-inf"))
        self.assertEqual(out.tolist()[1:], [1.0, 2.0, 2.0])
        out4 = read_persist_m(z, 4)
        self.assertEqual(out4.tolist()[3], 1.0)
        self.assertTrue(math.isinf(out4[2].item()))

    def test_ewma_and_cusum(self) -> None:
        z = torch.tensor([1.0, 1.0, 1.0])
        ewma = read_ewma(z, 0.1)
        self.assertAlmostEqual(ewma[0].item(), 1.0)
        self.assertAlmostEqual(ewma[2].item(), 1.0, places=6)
        cusum = read_cusum(torch.tensor([2.0, 2.0, -10.0]), 1.0)
        self.assertEqual(cusum.tolist(), [1.0, 2.0, 0.0])

    def test_catalogue_covers_spec_1_5(self) -> None:
        names = {reading.name for reading in build_readings()}
        self.assertTrue(
            {
                "max",
                "persist2",
                "ewma01",
                "ewma02",
                "cusum05",
                "cusum1",
                "cusum2",
                "runlen4_1",
                "runlen8_1",
                "runlen4_2",
                "runlen8_2",
            }
            <= names
        )
        runlen = {r.name: r for r in build_readings(["runlen4_2"])}["runlen4_2"]
        self.assertEqual(runlen.threshold_source, "fixed")
        self.assertEqual(runlen.fixed_threshold, 2.0)


class CalibrationTests(unittest.TestCase):
    def _streams(self, count: int, length: int) -> list[tuple[torch.Tensor, torch.Tensor]]:
        ends = torch.arange(length, dtype=torch.long)
        return [(torch.full((length,), float(index)), ends) for index in range(count)]

    def test_bucket_merge_by_traces(self) -> None:
        stats = H.fit_bucket_stats(self._streams(40, 100), min_bucket_traces=30)
        self.assertEqual(stats.cap, 3)
        self.assertEqual(stats.buckets(torch.tensor([0, 40, 200])).tolist(), [0, 1, 3])

    def test_bucket_merge_shrinks_when_traces_are_short(self) -> None:
        streams = self._streams(10, 100) + self._streams(40, 40)
        stats = H.fit_bucket_stats(streams, min_bucket_traces=30)
        self.assertEqual(stats.cap, 1)

    def test_bucket_cap_and_window_criterion(self) -> None:
        stats = H.fit_bucket_stats(
            self._streams(40, 200), min_bucket_traces=30, bucket_cap=3, min_criterion="windows"
        )
        self.assertEqual(stats.cap, 3)

    def test_standardization_is_per_bucket(self) -> None:
        ends = torch.arange(64, dtype=torch.long)
        streams = [
            (torch.cat([torch.zeros(32) + index, torch.zeros(32) + 10 * index]), ends)
            for index in range(40)
        ]
        stats = H.fit_bucket_stats(streams, min_bucket_traces=1)
        z = stats.standardize(torch.tensor([0.0, 0.0]), torch.tensor([0, 40]))
        self.assertNotAlmostEqual(z[0].item(), z[1].item())

    def test_conformal_threshold_rank(self) -> None:
        values = [float(index) for index in range(20)]
        block = H.conformal_threshold(values, 0.10)
        self.assertEqual(block["order_statistic_rank"], math.ceil(21 * 0.9))
        self.assertEqual(block["threshold"], 18.0)


class AlarmTests(unittest.TestCase):
    def test_negative_row(self) -> None:
        trace = make_trace("n1", tokens=32)
        ends = torch.arange(10, dtype=torch.long)
        row = H.alarm_row(trace, ends, torch.zeros(10), 1.0)
        self.assertFalse(row["false_alarm"])
        self.assertEqual(row["alarm_onset_count"], 0)
        row = H.alarm_row(trace, ends, torch.ones(10) * 2.0, 1.0)
        self.assertTrue(row["false_alarm"])
        self.assertEqual(row["alarm_onset_count"], 1)

    def test_comparison_rule(self) -> None:
        trace = make_trace("n2", tokens=32)
        ends = torch.arange(4, dtype=torch.long)
        scores = torch.tensor([1.0, 1.0, 1.0, 1.0])
        self.assertTrue(H.alarm_row(trace, ends, scores, 1.0, comparison="ge")["false_alarm"])
        self.assertFalse(H.alarm_row(trace, ends, scores, 1.0, comparison="gt")["false_alarm"])

    def test_positive_anchor_blocks(self) -> None:
        trace = make_trace(
            "p1", arm="attack", positive=True, onset=20, completion=26, tokens=64
        )
        ends = torch.arange(64, dtype=torch.long)
        scores = torch.zeros(64)
        scores[26:] = 5.0
        row = H.alarm_row(trace, ends, scores, 1.0)
        strict = row["anchors"]["onset_strict"]
        self.assertFalse(strict["pre_alarm"])
        self.assertEqual(strict["first_alarm_end"], 26)
        self.assertEqual(strict["latency"], 6)
        self.assertTrue(strict["hit"])
        self.assertTrue(strict["reachable"]["8"])
        completion = row["anchors"]["completion_strict"]
        self.assertEqual(completion["latency"], 0)

    def test_tolerance_band(self) -> None:
        trace = make_trace("p2", arm="attack", positive=True, onset=20, tokens=64)
        ends = torch.arange(64, dtype=torch.long)
        scores = torch.zeros(64)
        scores[15:] = 5.0  # inside [onset-8, onset)
        row = H.alarm_row(trace, ends, scores, 1.0)
        self.assertTrue(row["anchors"]["onset_strict"]["pre_alarm"])
        self.assertFalse(row["anchors"]["onset_strict"]["hit"])
        tolerant = row["anchors"]["onset_tolerant"]
        self.assertFalse(tolerant["pre_alarm"])
        self.assertTrue(tolerant["hit"])
        self.assertEqual(tolerant["latency"], 0)

    def test_aggregate_counts(self) -> None:
        rows = [
            H.alarm_row(make_trace("c1", arm="clean"), torch.arange(4), torch.zeros(4), 1.0),
            H.alarm_row(
                make_trace("b1", arm="benign_control"), torch.arange(4), torch.ones(4) * 2, 1.0
            ),
            H.alarm_row(make_trace("r1", arm="attack"), torch.arange(4), torch.zeros(4), 1.0),
        ]
        drift = make_trace("d1", arm="attack", positive=True, onset=2, tokens=16)
        scores = torch.tensor([0.0, 0.0, 5.0, 5.0])
        rows.append(H.alarm_row(drift, torch.arange(4), scores, 1.0))
        aggregate = H.aggregate_rows(rows)
        self.assertEqual(aggregate["non_drift_trace_count"], 3)
        self.assertEqual(aggregate["non_drift_false_alarm_count"], 1.0)
        self.assertEqual(aggregate["by_arm"]["benign"]["false_alarm_rate"], 1.0)
        self.assertEqual(aggregate["by_arm"]["clean"]["false_alarm_rate"], 0.0)
        self.assertEqual(aggregate["onset_strict"]["recall_final_count"], 1.0)
        self.assertEqual(aggregate["onset_strict"]["median_latency"], 0.0)

    def test_weighted_aggregate(self) -> None:
        row = H.alarm_row(
            make_trace("r2", arm="attack"), torch.arange(4), torch.ones(4) * 2, 1.0, weight=0.5
        )
        aggregate = H.aggregate_rows([row])
        self.assertEqual(aggregate["by_arm"]["resist"]["false_alarm_count"], 0.5)
        self.assertEqual(aggregate["by_arm"]["resist"]["false_alarm_rate"], 1.0)


class SplitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.batches = synthetic_batches()

    def test_s1_directions(self) -> None:
        cases = H.build_split_cases(self.batches, "S1")
        self.assertEqual([case.name for case in cases], ["b1_to_b2", "b2_to_b1"])
        for case in cases:
            fit_ids = {trace.trace_id for trace in case.fit_traces}
            target_ids = {trace.trace_id for trace in case.target_traces}
            self.assertFalse(fit_ids & target_ids)

    def test_s2_groups_are_batch_fold(self) -> None:
        cases = H.build_split_cases(self.batches, "S2")
        self.assertEqual(len(cases), 10)
        covered: set[str] = set()
        for case in cases:
            ids = {trace.trace_id for trace in case.target_traces}
            self.assertFalse(ids & {trace.trace_id for trace in case.fit_traces})
            covered |= ids
        self.assertEqual(len(covered), 72)

    def test_s3_holds_out_all_arms_of_a_domain(self) -> None:
        cases = H.build_split_cases(self.batches, "S3")
        for case in cases:
            domain = case.detail["held_out_domain"]
            self.assertTrue(all(t.scenario_domain == domain for t in case.target_traces))
            self.assertTrue(all(t.scenario_domain != domain for t in case.fit_traces))
            arms = {trace.arm for trace in case.target_traces}
            self.assertEqual(arms, {"clean", "benign_control", "attack"})

    def test_scenario_halves_are_deterministic_and_balanced(self) -> None:
        halves = H.scenario_halves(self.batches["b1"])
        self.assertEqual(sorted(set(halves.values())), [0, 1])
        self.assertEqual(halves, H.scenario_halves(self.batches["b1"]))

    def test_routine_definitions(self) -> None:
        traces = self.batches["b1"]
        cb = H.routine_traces(traces, "cb")
        all_normal = H.routine_traces(traces, "all_normal")
        self.assertTrue(all(t.arm in ("clean", "benign_control") for t in cb))
        self.assertTrue(all(not t.positive for t in all_normal))
        self.assertGreater(len(all_normal), len(cb))


class ScorerTests(unittest.TestCase):
    def test_registry_has_reference_scorers(self) -> None:
        self.assertIn("g1_whitened_distance", available())
        self.assertIn("oov_fraction", available())

    def test_g1_is_causal(self) -> None:
        scorer = build("g1_whitened_distance", {"window_width": 8})
        batches = synthetic_batches()
        state = scorer.fit(H.routine_traces(batches["b1"], "cb"))
        trace = batches["b2"][0]
        scores, ends = scorer.score(state, trace)
        mutated_ids = trace.top_k_ids.clone()
        mutated_ids[:, 60:, :] = (mutated_ids[:, 60:, :] + 17) % 64
        mutated = LoadedTrace(
            record=trace.record,
            top_k_ids=mutated_ids,
            token_ids=trace.token_ids,
            scenario_domain=trace.scenario_domain,
        )
        mutated_scores, mutated_ends = scorer.score(state, mutated)
        self.assertEqual(ends.tolist(), mutated_ends.tolist())
        keep = ends < 60
        self.assertTrue(torch.allclose(scores[keep], mutated_scores[keep], atol=1e-6))

    def test_g1_window_geometry(self) -> None:
        scorer = build("g1_whitened_distance", {"window_width": 8, "layers": "middle"})
        self.assertEqual(scorer.window_width, 8)
        batches = synthetic_batches()
        state = scorer.fit(H.routine_traces(batches["b1"], "cb"))
        self.assertEqual(state.mu.numel(), 7 * 64)
        drift = [t for t in batches["b2"] if t.positive][0]
        scores, ends = scorer.score(state, drift)
        self.assertEqual(int(ends[0]), 7)
        post = scores[ends >= 48].mean()
        pre = scores[ends < 32].mean()
        self.assertGreater(float(post), float(pre))

    def test_oov_fraction(self) -> None:
        scorer = build("oov_fraction", {"window_width": 4, "min_count": 5})
        routine = []
        for index in range(6):
            trace = make_trace(f"r{index}", tokens=64, seed=index)
            trace.token_ids = torch.arange(64, dtype=torch.long) % 8
            routine.append(trace)
        state = scorer.fit(routine)
        self.assertEqual(int(state.counts[:8].min()), 48)
        probe = make_trace("p", tokens=32, seed=2)
        probe.token_ids = torch.cat(
            [torch.arange(16, dtype=torch.long) % 8, torch.full((16,), 4321, dtype=torch.long)]
        )
        scores, ends = scorer.score(state, probe)
        self.assertEqual(int(ends[0]), 3)
        self.assertTrue(bool((scores >= 0).all()) and bool((scores <= 1).all()))
        self.assertEqual(float(scores[ends < 16].max()), 0.0)
        self.assertEqual(float(scores[ends >= 19].min()), 1.0)


class RunnerTests(unittest.TestCase):
    def test_end_to_end_run_and_report(self) -> None:
        batches = synthetic_batches()
        config = H.HarnessConfig(
            scorer="g1_whitened_distance",
            scorer_config={"layers": "middle"},
            windows=(8,),
            routines=("cb",),
            splits=("S1",),
            modes=("D", "T"),
            alphas=(0.10,),
            readings=("max", "persist2", "runlen4_1"),
            min_bucket_traces=4,
            bootstrap_draws=20,
            emit_audit=False,
        )
        result = H.run_harness(config, batches=batches)
        self.assertEqual(len(result["case_runs"]), 2)
        summaries = H.collect_summaries(result)
        self.assertEqual(len(summaries), 2 * 2 * 3)
        modes = {row["mode"] for row in summaries}
        self.assertEqual(modes, {"D", "T"})
        for row in summaries:
            self.assertIsNotNone(row["far_all"])
            self.assertGreaterEqual(row["far_all"], 0.0)
        table = H.markdown_table(summaries)
        self.assertIn("| case |", table)
        self.assertIn("b1_to_b2", table)
        with tempfile.TemporaryDirectory() as directory:
            manifest = H.write_result(result, Path(directory))
            self.assertIn("result.json", manifest["files"])
            self.assertEqual(len(manifest["sha256"]["result.json"]), 64)
            payload = json.loads(Path(manifest["files"]["result.json"]).read_text())
            self.assertIn("score_streams", payload["case_runs"][0])
            self.assertIn("q1_panel", payload["case_runs"][0])

    def test_mode_d_disjoint_pooling_evaluates_each_trace_once(self) -> None:
        batches = synthetic_batches()
        config = H.HarnessConfig(
            windows=(8,),
            splits=("S1",),
            modes=("D",),
            readings=("max",),
            min_bucket_traces=4,
            bootstrap_draws=0,
            emit_q1=False,
            emit_audit=False,
            store_score_streams=False,
        )
        result = H.run_harness(config, batches=batches)
        candidate = result["case_runs"][0]["candidates"][0]
        trace_ids = [row[0] for row in candidate["trace_alarms"]]
        self.assertEqual(len(trace_ids), len(set(trace_ids)))
        self.assertEqual(len(trace_ids), len(batches["b2"]))

    def test_pilot_pooling_double_counts_attack_arm(self) -> None:
        batches = synthetic_batches()
        config = H.HarnessConfig(
            windows=(8,),
            splits=("S1",),
            modes=("D",),
            readings=("max",),
            pooling="pilot",
            min_bucket_traces=4,
            bootstrap_draws=0,
            emit_q1=False,
            emit_audit=False,
            store_score_streams=False,
        )
        result = H.run_harness(config, batches=batches)
        candidate = result["case_runs"][0]["candidates"][0]
        weights = {}
        for trace_id, weight, *_ in candidate["trace_alarms"]:
            weights[trace_id] = weights.get(trace_id, 0.0) + weight
        self.assertTrue(all(abs(value - 1.0) < 1e-9 for value in weights.values()))
        attack = [t.trace_id for t in batches["b2"] if t.arm == "attack"]
        counts = [sum(1 for row in candidate["trace_alarms"] if row[0] == tid) for tid in attack]
        self.assertTrue(all(count == 2 for count in counts))

    def test_pooled_summaries_sum_counts(self) -> None:
        batches = synthetic_batches()
        config = H.HarnessConfig(
            windows=(8,),
            splits=("S3",),
            modes=("D",),
            readings=("max",),
            min_bucket_traces=4,
            bootstrap_draws=0,
            emit_q1=False,
            emit_audit=False,
            store_score_streams=False,
        )
        result = H.run_harness(config, batches=batches)
        pooled = H.pooled_summaries(result)
        self.assertEqual(len(pooled), 1)
        per_case = H.collect_summaries(result)
        drift_total = sum(
            candidate["metrics"]["drift_trace_count"]
            for case_run in result["case_runs"]
            for candidate in case_run["candidates"]
        )
        pooled_metrics = H.pool_candidates(
            [c for case_run in result["case_runs"] for c in case_run["candidates"]]
        )
        self.assertEqual(pooled_metrics["drift_trace_count"], drift_total)
        self.assertEqual(pooled_metrics["case_count"], len(per_case))
        self.assertEqual(pooled_metrics["non_drift_trace_weight"], 64.0)

    def test_q1_panel_shapes(self) -> None:
        batches = synthetic_batches()
        scorer = build("g1_whitened_distance", {"window_width": 8})
        case = H.build_split_cases(batches, "S1")[0]
        state = scorer.fit(H.routine_traces(case.fit_traces, "cb"))
        streams = {
            t.trace_id: tuple(x.detach() for x in scorer.score(state, t))
            for t in case.target_traces
        }
        routine = H.routine_traces(case.target_traces, "cb")
        stats = H.fit_bucket_stats(
            [streams[t.trace_id] for t in routine], min_bucket_traces=4
        )
        panel = H.q1_panel(streams, case.target_traces, stats, window_width=8)
        self.assertIn("event_curves", panel)
        self.assertIn("anchor_token_auroc", panel)
        self.assertTrue(all(-32 <= row["offset"] <= 48 for row in panel["event_curves"]))
        self.assertEqual(len(panel["per_drift_rise"]), 4)


class AcceptanceTests(unittest.TestCase):
    @unittest.skipUnless(P1_RESULT.exists(), "P1 result artifact not available")
    def test_p1_metric_replication(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_verify_p1_metrics", ROOT / "scripts" / "research_v2" / "verify_p1_metrics.py"
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        computed = module.recompute(P1_RESULT, "b1_to_b2::selection_middle_late")
        self.assertEqual(computed["non_drift_false_alarm_count"], 7)
        self.assertEqual(computed["non_drift_trace_count"], 205)
        self.assertEqual(computed["recall_plus_16_count"], 7)
        self.assertEqual(computed["recall_final_count"], 16)
        self.assertEqual(computed["pre_alarm_count"], 6)
        self.assertEqual(computed["drift_trace_count"], 35)
        self.assertEqual(computed["median_latency"], 19.0)


if __name__ == "__main__":
    unittest.main()
