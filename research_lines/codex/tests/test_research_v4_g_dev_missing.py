"""Tests for the subset-agnostic resume tooling of dataset G.

Two scripts are covered: ``scripts/research_v4/g_dev_missing.py`` (the gap
report) and ``scripts/research_v4/run_g_dev_resume.sh`` (the driver that
consumes it).  Both take a ``--subset`` and default to ``g_dev``, so the tests
pin both the generalised behaviour and the unchanged G-dev behaviour.

No model, no tokenizer, no real artifact directory is written: every test builds
its own tiny subset-shaped tree in a temporary directory, and the one driver
test that runs the shell script hands it a subset that is already complete, so
it exits before it can reach ``run_agent_v3.py``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "g_dev_missing_under_test", ROOT / "scripts" / "research_v4" / "g_dev_missing.py"
)
g_dev_missing = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(g_dev_missing)


CONFIG = {
    "collection_plan": [
        {
            "group": "core",
            "arms": ["clean", "benign_control", "attack"],
            "scenario_ids": ["s-001", "s-002", "s-003"],
        },
        {"group": "supplement", "arms": ["attack"], "scenario_ids": ["s-004", "s-005"]},
        {"group": "lexical", "arms": ["clean"], "scenario_ids": ["s-006"]},
    ]
}


def make_run(root: Path, name: str) -> Path:
    run = root / name
    run.mkdir(parents=True)
    (run / "resolved_experiment_config.json").write_text("{}", encoding="utf-8")
    return run


def make_trace(run: Path, scenario: str, arm: str, *, complete: bool = True) -> Path:
    trace_dir = run / scenario / arm
    (trace_dir / "steps").mkdir(parents=True)
    (trace_dir / "manifest.jsonl").write_text("", encoding="utf-8")
    (trace_dir / "trace.json").write_text(
        json.dumps({"trace_id": f"{scenario}--{arm}", "complete": complete}), encoding="utf-8"
    )
    return trace_dir


class RequiredPairsTest(unittest.TestCase):
    def test_arms_are_collected_per_scenario(self) -> None:
        required = g_dev_missing.required_pairs(CONFIG)
        self.assertEqual(len(required), 6)
        self.assertEqual(required["s-001"], ["clean", "benign_control", "attack"])
        self.assertEqual(required["s-004"], ["attack"])
        self.assertEqual(required["s-006"], ["clean"])

    def test_total_trace_count(self) -> None:
        required = g_dev_missing.required_pairs(CONFIG)
        self.assertEqual(sum(len(v) for v in required.values()), 3 * 3 + 2 + 1)

    def test_a_scenario_listed_in_two_groups_unions_its_arms(self) -> None:
        config = {
            "collection_plan": [
                {"group": "a", "arms": ["clean"], "scenario_ids": ["s-001"]},
                {"group": "b", "arms": ["clean", "attack"], "scenario_ids": ["s-001"]},
            ]
        }
        self.assertEqual(g_dev_missing.required_pairs(config), {"s-001": ["clean", "attack"]})


class ScanTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_only_directories_with_a_resolved_config_are_run_dirs(self) -> None:
        make_run(self.root, "core")
        (self.root / "not_a_run").mkdir()
        (self.root / "core.run.log").write_text("x", encoding="utf-8")
        self.assertEqual([p.name for p in g_dev_missing.find_run_dirs(self.root)], ["core"])

    def test_quarantine_directories_are_never_scanned(self) -> None:
        make_run(self.root, "core")
        for name in ("quarantine", "_quarantine"):
            make_run(self.root, name)
        self.assertEqual([p.name for p in g_dev_missing.find_run_dirs(self.root)], ["core"])

    def test_run_dirs_are_sorted_so_originals_precede_resume_dirs(self) -> None:
        for name in ("resume_1_0", "core_72_cells", "attack_supplement"):
            make_run(self.root, name)
        self.assertEqual(
            [p.name for p in g_dev_missing.find_run_dirs(self.root)],
            ["attack_supplement", "core_72_cells", "resume_1_0"],
        )

    def test_complete_and_incomplete_are_separated(self) -> None:
        run = make_run(self.root, "core")
        make_trace(run, "s-001", "clean", complete=True)
        make_trace(run, "s-001", "attack", complete=False)
        complete, incomplete = g_dev_missing.scan(self.root)
        self.assertEqual(sorted(complete), [("s-001", "clean")])
        self.assertEqual(sorted(incomplete), [("s-001", "attack")])

    def test_a_trace_dir_without_trace_json_counts_as_incomplete(self) -> None:
        run = make_run(self.root, "core")
        trace_dir = run / "s-001" / "clean"
        (trace_dir / "steps").mkdir(parents=True)
        (trace_dir / "manifest.jsonl").write_text("", encoding="utf-8")
        complete, incomplete = g_dev_missing.scan(self.root)
        self.assertEqual(complete, {})
        self.assertEqual(sorted(incomplete), [("s-001", "clean")])

    def test_a_bare_scenario_directory_is_ignored(self) -> None:
        run = make_run(self.root, "core")
        (run / "s-001" / "clean").mkdir(parents=True)
        complete, incomplete = g_dev_missing.scan(self.root)
        self.assertEqual((complete, incomplete), ({}, {}))

    def test_unparsable_trace_json_counts_as_incomplete(self) -> None:
        run = make_run(self.root, "core")
        trace_dir = make_trace(run, "s-001", "clean", complete=True)
        (trace_dir / "trace.json").write_text("{ truncated", encoding="utf-8")
        complete, incomplete = g_dev_missing.scan(self.root)
        self.assertEqual(complete, {})
        self.assertEqual(sorted(incomplete), [("s-001", "clean")])

    def test_complete_false_is_not_complete(self) -> None:
        run = make_run(self.root, "core")
        make_trace(run, "s-001", "clean", complete=False)
        self.assertFalse(g_dev_missing.trace_is_complete(run / "s-001" / "clean"))

    def test_missing_root_scans_clean(self) -> None:
        self.assertEqual(g_dev_missing.find_run_dirs(self.root / "nope"), [])


class PlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_empty_root_reports_every_pair_missing(self) -> None:
        report = g_dev_missing.plan(CONFIG, self.root)
        self.assertEqual(report["summary"]["missing_trace_count"], 12)
        self.assertEqual(report["summary"]["complete_trace_count"], 0)
        self.assertEqual(
            [(tuple(g["arms"]), g["scenarios"]) for g in report["groups"]],
            [
                (("attack",), ["s-004", "s-005"]),
                (("clean",), ["s-006"]),
                (("clean", "benign_control", "attack"), ["s-001", "s-002", "s-003"]),
            ],
        )

    def test_a_fully_collected_subset_reports_nothing_missing(self) -> None:
        run = make_run(self.root, "core")
        for scenario in ("s-001", "s-002", "s-003"):
            for arm in ("clean", "benign_control", "attack"):
                make_trace(run, scenario, arm)
        for scenario in ("s-004", "s-005"):
            make_trace(run, scenario, "attack")
        make_trace(run, "s-006", "clean")
        report = g_dev_missing.plan(CONFIG, self.root)
        self.assertEqual(report["groups"], [])
        self.assertEqual(report["summary"]["missing_trace_count"], 0)
        self.assertEqual(report["summary"]["complete_trace_count"], 12)
        self.assertEqual(report["summary"]["quarantined_count"], 0)

    def test_partial_arms_of_one_scenario_form_their_own_arm_set(self) -> None:
        run = make_run(self.root, "core")
        make_trace(run, "s-001", "clean")
        make_trace(run, "s-001", "benign_control")
        report = g_dev_missing.plan(CONFIG, self.root)
        groups = {tuple(g["arms"]): g["scenarios"] for g in report["groups"]}
        self.assertEqual(groups[("attack",)], ["s-001", "s-004", "s-005"])
        self.assertEqual(groups[("clean", "benign_control", "attack")], ["s-002", "s-003"])

    def test_trace_count_per_arm_set_is_scenarios_times_arms(self) -> None:
        report = g_dev_missing.plan(CONFIG, self.root)
        for group in report["groups"]:
            self.assertEqual(group["trace_count"], len(group["scenarios"]) * len(group["arms"]))
        self.assertEqual(
            sum(g["trace_count"] for g in report["groups"]),
            report["summary"]["missing_trace_count"],
        )

    def test_incomplete_trace_is_quarantined_and_reported_missing(self) -> None:
        run = make_run(self.root, "core")
        make_trace(run, "s-006", "clean", complete=False)
        report = g_dev_missing.plan(CONFIG, self.root)
        self.assertFalse((run / "s-006" / "clean").exists())
        moved = self.root / "quarantine" / "core" / "s-006" / "clean"
        self.assertTrue((moved / "trace.json").is_file())
        self.assertEqual(report["summary"]["quarantined_count"], 1)
        self.assertEqual(report["summary"]["quarantined"][0]["reason"], "incomplete")
        groups = {tuple(g["arms"]): g["scenarios"] for g in report["groups"]}
        self.assertIn("s-006", groups[("clean",)])

    def test_duplicate_complete_pair_keeps_the_first_run_dir_and_quarantines_the_rest(self) -> None:
        first = make_run(self.root, "core")
        second = make_run(self.root, "resume_1_0")
        make_trace(first, "s-006", "clean")
        make_trace(second, "s-006", "clean")
        report = g_dev_missing.plan(CONFIG, self.root)
        self.assertTrue((first / "s-006" / "clean" / "trace.json").is_file())
        self.assertFalse((second / "s-006" / "clean").exists())
        self.assertTrue((self.root / "quarantine" / "resume_1_0" / "s-006" / "clean").is_dir())
        entry = report["summary"]["quarantined"][0]
        self.assertEqual(entry["reason"], "duplicate")
        self.assertEqual(entry["kept"], str(first / "s-006" / "clean"))
        self.assertEqual(report["summary"]["complete_trace_count"], 1)

    def test_incomplete_duplicate_of_a_complete_pair_is_quarantined_not_missing(self) -> None:
        first = make_run(self.root, "core")
        second = make_run(self.root, "resume_1_0")
        make_trace(first, "s-006", "clean", complete=True)
        make_trace(second, "s-006", "clean", complete=False)
        report = g_dev_missing.plan(CONFIG, self.root)
        self.assertTrue((first / "s-006" / "clean" / "trace.json").is_file())
        self.assertFalse((second / "s-006" / "clean").exists())
        self.assertEqual(report["summary"]["quarantined"][0]["reason"], "duplicate_incomplete")
        groups = {tuple(g["arms"]): g["scenarios"] for g in report["groups"]}
        self.assertNotIn("s-006", groups.get(("clean",), []))

    def test_a_complete_trace_in_a_resume_dir_satisfies_the_pair(self) -> None:
        make_run(self.root, "core")
        resume = make_run(self.root, "resume_1_0")
        make_trace(resume, "s-006", "clean")
        report = g_dev_missing.plan(CONFIG, self.root)
        groups = {tuple(g["arms"]): g["scenarios"] for g in report["groups"]}
        self.assertNotIn(("clean",), groups)
        self.assertEqual(report["summary"]["complete_trace_count"], 1)

    def test_quarantined_traces_are_not_rescanned_on_the_next_pass(self) -> None:
        run = make_run(self.root, "core")
        make_trace(run, "s-006", "clean", complete=False)
        g_dev_missing.plan(CONFIG, self.root)
        second = g_dev_missing.plan(CONFIG, self.root)
        self.assertEqual(second["summary"]["quarantined_count"], 0)
        self.assertEqual(second["summary"]["missing_trace_count"], 12)

    def test_a_second_incomplete_attempt_does_not_overwrite_the_first_quarantine(self) -> None:
        run = make_run(self.root, "core")
        make_trace(run, "s-006", "clean", complete=False)
        g_dev_missing.plan(CONFIG, self.root)
        make_trace(run, "s-006", "clean", complete=False)
        g_dev_missing.plan(CONFIG, self.root)
        self.assertTrue((self.root / "quarantine" / "core" / "s-006" / "clean").is_dir())
        self.assertTrue((self.root / "quarantine" / "core" / "s-006" / "clean.1").is_dir())

    def test_dry_run_moves_nothing_but_still_reports(self) -> None:
        run = make_run(self.root, "core")
        make_trace(run, "s-006", "clean", complete=False)
        report = g_dev_missing.plan(CONFIG, self.root, dry_run=True)
        self.assertTrue((run / "s-006" / "clean" / "trace.json").is_file())
        self.assertFalse((self.root / "quarantine").exists())
        self.assertEqual(report["summary"]["quarantined_count"], 1)
        self.assertTrue(report["summary"]["dry_run"])

    def test_an_explicit_quarantine_dir_is_honoured(self) -> None:
        run = make_run(self.root, "core")
        make_trace(run, "s-006", "clean", complete=False)
        elsewhere = self.root / "held"
        g_dev_missing.plan(CONFIG, self.root, quarantine_root=elsewhere)
        self.assertTrue((elsewhere / "core" / "s-006" / "clean" / "trace.json").is_file())

    def test_pairs_outside_the_collection_plan_are_reported_not_quarantined(self) -> None:
        run = make_run(self.root, "core")
        make_trace(run, "s-006", "attack")
        report = g_dev_missing.plan(CONFIG, self.root)
        self.assertEqual(report["summary"]["unexpected_pairs"], ["s-006/attack"])
        self.assertTrue((run / "s-006" / "attack" / "trace.json").is_file())

    def test_summary_lists_the_scanned_run_dirs(self) -> None:
        make_run(self.root, "core")
        make_run(self.root, "resume_1_0")
        report = g_dev_missing.plan(CONFIG, self.root)
        self.assertEqual(
            report["summary"]["run_dirs"],
            [str(self.root / "core"), str(self.root / "resume_1_0")],
        )

    def test_required_trace_count_matches_the_plan(self) -> None:
        report = g_dev_missing.plan(CONFIG, self.root)
        self.assertEqual(report["summary"]["required_trace_count"], 12)


class CliTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_cli_prints_json_lines_then_a_summary(self) -> None:
        config_path = self.root / "config.json"
        config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
        run = make_run(self.root / "runs", "core")
        make_trace(run, "s-006", "clean")
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "research_v4" / "g_dev_missing.py"),
                "--config",
                str(config_path),
                "--root",
                str(self.root / "runs"),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        rows = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
        self.assertEqual([row["kind"] for row in rows], ["arm_set", "arm_set", "summary"])
        self.assertEqual(rows[-1]["missing_trace_count"], 11)
        self.assertEqual(rows[-1]["complete_trace_count"], 1)

    def test_real_g_dev_config_parses_into_600_required_traces(self) -> None:
        config = json.loads(
            (ROOT / "configs" / "dataset_g" / "g_dev.json").read_text(encoding="utf-8")
        )
        required = g_dev_missing.required_pairs(config)
        self.assertEqual(len(required), 312)
        self.assertEqual(sum(len(arms) for arms in required.values()), 600)


class SubsetPathTest(unittest.TestCase):
    """``--subset`` is shorthand for the config/root pair, nothing more."""

    def test_default_subset_is_g_dev(self) -> None:
        self.assertEqual(g_dev_missing.DEFAULT_SUBSET, "g_dev")

    def test_config_path_is_derived_from_the_subset_name(self) -> None:
        self.assertEqual(
            g_dev_missing.subset_config_path("g_session", Path("/x")),
            Path("/x/configs/dataset_g/g_session.json"),
        )

    def test_run_root_is_derived_from_the_subset_name(self) -> None:
        self.assertEqual(
            g_dev_missing.subset_run_root("g_conf", Path("/x")),
            Path("/x/artifacts/agent_v2/dataset_g/g_conf"),
        )

    def test_the_g_dev_defaults_are_unchanged(self) -> None:
        self.assertEqual(
            g_dev_missing.subset_config_path(g_dev_missing.DEFAULT_SUBSET),
            g_dev_missing.ROOT / "configs" / "dataset_g" / "g_dev.json",
        )
        self.assertEqual(
            g_dev_missing.subset_run_root(g_dev_missing.DEFAULT_SUBSET),
            g_dev_missing.ROOT / "artifacts" / "agent_v2" / "dataset_g" / "g_dev",
        )

    def test_summary_records_the_subset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = g_dev_missing.plan(CONFIG, Path(tmp), subset="g_medium")
        self.assertEqual(report["summary"]["subset"], "g_medium")

    def test_summary_subset_is_none_when_not_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report = g_dev_missing.plan(CONFIG, Path(tmp))
        self.assertIsNone(report["summary"]["subset"])


class RealSubsetConfigTest(unittest.TestCase):
    """The three remaining subsets parse into the trace counts design 5 budgets."""

    def _required(self, subset: str) -> dict[str, list[str]]:
        config = json.loads(
            g_dev_missing.subset_config_path(subset).read_text(encoding="utf-8")
        )
        return g_dev_missing.required_pairs(config)

    def test_g_session_is_100_scenarios_and_100_traces(self) -> None:
        required = self._required("g_session")
        self.assertEqual(len(required), 100)
        self.assertEqual(sum(len(arms) for arms in required.values()), 100)

    def test_g_medium_is_40_scenarios_at_three_arms(self) -> None:
        required = self._required("g_medium")
        self.assertEqual(len(required), 40)
        self.assertEqual(sum(len(arms) for arms in required.values()), 120)
        self.assertEqual(
            sorted(set(tuple(arms) for arms in required.values())),
            [("clean", "benign_control", "attack")],
        )

    def test_g_conf_is_280_scenarios_and_720_traces(self) -> None:
        required = self._required("g_conf")
        self.assertEqual(len(required), 280)
        self.assertEqual(sum(len(arms) for arms in required.values()), 720)


class SubsetCliTest(unittest.TestCase):
    SCRIPT = ROOT / "scripts" / "research_v4" / "g_dev_missing.py"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _run(self, *argv: str) -> list[dict]:
        completed = subprocess.run(
            [sys.executable, str(self.SCRIPT), *argv],
            capture_output=True,
            text=True,
            check=True,
        )
        return [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]

    def test_subset_flag_reaches_the_summary(self) -> None:
        config_path = self.root / "config.json"
        config_path.write_text(json.dumps(CONFIG), encoding="utf-8")
        rows = self._run(
            "--subset", "g_session",
            "--config", str(config_path),
            "--root", str(self.root / "runs"),
        )
        self.assertEqual(rows[-1]["subset"], "g_session")
        self.assertEqual(rows[-1]["missing_trace_count"], 12)

    def test_subset_alone_resolves_the_real_config_and_root(self) -> None:
        rows = self._run("--subset", "g_medium", "--dry-run")
        summary = rows[-1]
        self.assertEqual(summary["subset"], "g_medium")
        self.assertEqual(summary["required_trace_count"], 120)
        self.assertTrue(summary["root"].endswith("/artifacts/agent_v2/dataset_g/g_medium"))
        self.assertTrue(summary["dry_run"])

    def test_no_flags_at_all_still_means_g_dev(self) -> None:
        rows = self._run("--dry-run")
        summary = rows[-1]
        self.assertEqual(summary["subset"], "g_dev")
        self.assertEqual(summary["required_trace_count"], 600)
        self.assertTrue(summary["root"].endswith("/artifacts/agent_v2/dataset_g/g_dev"))

    def test_an_unknown_subset_fails_loudly(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(self.SCRIPT), "--subset", "g_nope", "--dry-run"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)


class ResumeDriverArgumentTest(unittest.TestCase):
    """The shell driver's subset plumbing, without ever loading a model.

    Every case here hands the driver a subset whose traces are already on disk,
    so ``g_dev_missing.py`` reports zero missing and the driver exits 0 before
    it can reach ``flock``/``run_agent_v3.py``.
    """

    SCRIPT = ROOT / "scripts" / "research_v4" / "run_g_dev_resume.sh"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _complete_subset(self) -> tuple[Path, Path]:
        config = {"collection_plan": [{"group": "g", "arms": ["clean"], "scenario_ids": ["s-001"]}]}
        config_path = self.root / "subset.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        out_root = self.root / "runs"
        make_trace(make_run(out_root, "run_1"), "s-001", "clean")
        return config_path, out_root

    def _run(self, *argv: str):
        return subprocess.run(
            ["bash", str(self.SCRIPT), *argv], capture_output=True, text=True
        )

    def test_help_exits_zero(self) -> None:
        completed = self._run("--help")
        self.assertEqual(completed.returncode, 0)
        self.assertIn("--subset", completed.stdout)

    def test_unknown_argument_is_rejected(self) -> None:
        completed = self._run("--nope")
        self.assertEqual(completed.returncode, 2)

    def test_unknown_subset_is_rejected_before_any_work(self) -> None:
        completed = self._run("--subset", "g_nope")
        self.assertEqual(completed.returncode, 2)
        self.assertIn("no such subset config", completed.stderr)

    def test_a_complete_subset_exits_zero_without_running_a_model(self) -> None:
        config_path, out_root = self._complete_subset()
        completed = self._run(
            "--subset", "g_session", "--config", str(config_path), "--out-root", str(out_root)
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("subset g_session", completed.stdout)
        self.assertIn("0 traces still missing", completed.stdout)
        self.assertIn("nothing missing", completed.stdout)
        self.assertNotIn("run 1:", completed.stdout)

    def test_equals_form_of_every_flag_is_accepted(self) -> None:
        config_path, out_root = self._complete_subset()
        completed = self._run(
            f"--subset=g_conf", f"--config={config_path}", f"--out-root={out_root}"
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("subset g_conf", completed.stdout)

    def test_the_date_pin_and_allocator_levers_are_still_in_the_driver(self) -> None:
        text = self.SCRIPT.read_text(encoding="utf-8")
        self.assertIn('export TZ="${TZ_PIN:-XXX24}"', text)
        self.assertIn("PYTORCH_CUDA_ALLOC_CONF", text)
        self.assertIn("expandable_segments:True", text)
        self.assertIn("SCENARIOS_PER_RUN:-40", text)
        self.assertIn("flock -w 36000", text)

    def test_the_date_pin_defaults_to_xxx24_and_is_logged(self) -> None:
        """TZ_PIN only chooses *which* pin; unset must still mean XXX24.

        glibc clamps a POSIX TZ offset at 24 h, so XXX24 stops rendering
        2026-09-06 at 2026-09-08T00:00Z.  A subset collected across that
        boundary needs a compiled TZif with a larger offset, which is what
        TZ_PIN is for -- but an argument-less, environment-less call has to
        keep behaving exactly as it did for G-dev.
        """

        config_path, out_root = self._complete_subset()
        completed = subprocess.run(
            ["bash", str(self.SCRIPT), "--subset", "g_conf",
             "--config", str(config_path), "--out-root", str(out_root)],
            capture_output=True,
            text=True,
            env={k: v for k, v in os.environ.items() if k != "TZ_PIN"},
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("date pin TZ=XXX24 ->", completed.stdout)

    def test_tz_pin_overrides_the_date_pin(self) -> None:
        config_path, out_root = self._complete_subset()
        env = dict(os.environ)
        env["TZ_PIN"] = "UTC"
        completed = subprocess.run(
            ["bash", str(self.SCRIPT), "--subset", "g_conf",
             "--config", str(config_path), "--out-root", str(out_root)],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("date pin TZ=UTC ->", completed.stdout)

    def test_default_subset_paths_are_the_g_dev_ones(self) -> None:
        text = self.SCRIPT.read_text(encoding="utf-8")
        self.assertIn('SUBSET="${SUBSET:-g_dev}"', text)
        self.assertIn('configs/dataset_g/${SUBSET}.json', text)
        self.assertIn('artifacts/agent_v2/dataset_g/${SUBSET}', text)


if __name__ == "__main__":
    unittest.main()
