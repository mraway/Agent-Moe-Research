"""Tests for scripts/research_v4/g_conf_seal.py on a synthetic subset tree.

Sealing is irreversible in practice (it chmods every file of a 720-trace subset
read-only and the seal is the record that it happened), so the behaviour is
pinned here on a throwaway tree rather than discovered on the real one.  No
model, no routing tensor and no real artifact directory is touched.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "g_conf_seal_under_test", ROOT / "scripts" / "research_v4" / "g_conf_seal.py"
)
seal_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(seal_mod)


def make_trace(root: Path, run: str, scenario: str, arm: str, *, complete: bool = True) -> Path:
    trace_dir = root / run / scenario / arm
    (trace_dir / "steps").mkdir(parents=True, exist_ok=True)
    (trace_dir / "steps" / "step_000.safetensors").write_bytes(b"\0\1\2")
    (trace_dir / "trace.json").write_text(
        json.dumps({"trace_id": f"{scenario}--{arm}", "complete": complete}), encoding="utf-8"
    )
    (trace_dir / "manifest.jsonl").write_text('{"shard": 0}\n', encoding="utf-8")
    (root / run / "resolved_experiment_config.json").write_text("{}", encoding="utf-8")
    return trace_dir


class _Tree(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "g_conf"
        self.root.mkdir()
        make_trace(self.root, "run_a", "g-conf-001", "clean")
        make_trace(self.root, "run_a", "g-conf-001", "attack")
        make_trace(self.root, "run_b", "g-conf-002", "clean")
        self.addCleanup(self._restore_and_cleanup)

    def _restore_and_cleanup(self) -> None:
        for current, dir_names, file_names in os.walk(self.root):
            for name in dir_names + file_names:
                path = Path(current) / name
                try:
                    path.chmod(path.stat().st_mode | stat.S_IWUSR)
                except OSError:
                    pass
        try:
            self.root.chmod(self.root.stat().st_mode | stat.S_IWUSR)
        except OSError:
            pass
        self._tmp.cleanup()

    def _seal(self, **kwargs) -> dict:
        return seal_mod.build_seal(
            self.root,
            subset="g_conf",
            config=None,
            manifest=None,
            packet=None,
            mapping=None,
            extra=[],
            **kwargs,
        )


class SealContentTest(_Tree):
    def test_every_trace_json_and_manifest_is_hashed(self) -> None:
        seal = self._seal()
        self.assertEqual(seal["traces"]["trace_count"], 3)
        for record in seal["traces"]["traces"]:
            self.assertEqual(len(record["trace_json_sha256"]), 64)
            self.assertEqual(len(record["manifest_jsonl_sha256"]), 64)
            self.assertEqual(record["shard_files"], 1)

    def test_the_set_digests_are_order_independent_and_content_sensitive(self) -> None:
        first = self._seal()["traces"]["trace_json_set_sha256"]
        again = self._seal()["traces"]["trace_json_set_sha256"]
        self.assertEqual(first, again)
        path = self.root / "run_a" / "g-conf-001" / "clean" / "trace.json"
        path.write_text(json.dumps({"trace_id": "changed", "complete": True}), encoding="utf-8")
        self.assertNotEqual(self._seal()["traces"]["trace_json_set_sha256"], first)

    def test_incomplete_traces_are_named(self) -> None:
        make_trace(self.root, "run_b", "g-conf-003", "clean", complete=False)
        seal = self._seal()
        self.assertEqual(seal["traces"]["incomplete"], ["run_b/g-conf-003/clean"])

    def test_run_configs_are_hashed(self) -> None:
        seal = self._seal()
        self.assertEqual(
            sorted(record["path"] for record in seal["run_configs"]),
            ["run_a/resolved_experiment_config.json", "run_b/resolved_experiment_config.json"],
        )

    def test_the_prereg_sentence_is_carried_verbatim(self) -> None:
        seal = self._seal()
        self.assertEqual(
            seal["prereg_rule"]["sentence"],
            "opened once, primary cell only, after the label-freeze commit",
        )
        self.assertTrue(seal["prereg_rule"]["no_detector_has_been_run"])
        self.assertIn("section 13", seal["prereg_rule"]["source"])

    def test_the_timestamp_is_utc(self) -> None:
        self.assertTrue(self._seal()["sealed_at_utc"].endswith("Z"))


class ReadOnlyTest(_Tree):
    def test_chmod_makes_traces_unwritable_and_records_the_counts(self) -> None:
        seal = self._seal()
        seal_path = self.root / "SEALED.json"
        seal_path.write_text(json.dumps(seal), encoding="utf-8")
        record = seal_mod.make_read_only(self.root, seal_path)

        self.assertTrue(record["applied"])
        self.assertGreaterEqual(record["files_made_read_only"], 3 * 3 + 2 + 1)
        self.assertGreaterEqual(record["directories_made_read_only"], 1)
        self.assertIn("chmod a-w", record["command"])

        trace = self.root / "run_a" / "g-conf-001" / "clean" / "trace.json"
        self.assertFalse(trace.stat().st_mode & stat.S_IWUSR)
        self.assertFalse(seal_path.stat().st_mode & stat.S_IWUSR)
        self.assertFalse(self.root.stat().st_mode & stat.S_IWUSR)
        with self.assertRaises(PermissionError):
            trace.open("w")
        with self.assertRaises(PermissionError):
            (self.root / "run_a" / "g-conf-001" / "clean" / "new.txt").open("w")

    def test_the_tensor_shards_are_read_only_too(self) -> None:
        seal_path = self.root / "SEALED.json"
        seal_path.write_text("{}", encoding="utf-8")
        seal_mod.make_read_only(self.root, seal_path)
        shard = self.root / "run_a" / "g-conf-001" / "clean" / "steps" / "step_000.safetensors"
        self.assertFalse(shard.stat().st_mode & stat.S_IWUSR)


class VerifyTest(_Tree):
    def _write_seal(self) -> Path:
        seal = self._seal()
        seal["root"] = str(self.root)
        seal_path = self.root / "SEALED.json"
        seal_path.write_text(json.dumps(seal), encoding="utf-8")
        return seal_path

    def test_verify_passes_on_an_untouched_tree(self) -> None:
        original = seal_mod.ROOT
        try:
            seal_mod.ROOT = Path("/")
            self.assertEqual(seal_mod.verify(self._write_seal()), 0)
        finally:
            seal_mod.ROOT = original

    def test_verify_fails_after_a_single_byte_changes(self) -> None:
        seal_path = self._write_seal()
        original = seal_mod.ROOT
        try:
            seal_mod.ROOT = Path("/")
            (self.root / "run_b" / "g-conf-002" / "clean" / "manifest.jsonl").write_text(
                '{"shard": 1}\n', encoding="utf-8"
            )
            self.assertEqual(seal_mod.verify(seal_path), 1)
        finally:
            seal_mod.ROOT = original


if __name__ == "__main__":
    unittest.main()
