from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from research_v4.codex_access_guard import CodexGAccessGuard


class CodexAccessGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.guard = CodexGAccessGuard(self.root)

    def test_rejects_routing_and_annotation_trees(self) -> None:
        for prefix in ("dataset_g/g_conf", "dataset_g/annotations/g_conf"):
            path = self.root / "artifacts/agent_v2" / prefix / "synthetic.json"
            with self.subTest(prefix=prefix), self.assertRaises(PermissionError):
                self.guard.check_path(path)

    def test_rejects_invalidated_report(self) -> None:
        with self.assertRaises(PermissionError):
            self.guard.check_path(self.root / "artifacts/agent_v2/codex_g/invalidated_smoke/report.json")

    def test_resolves_symlink_alias(self) -> None:
        alias = self.root / "alias"
        alias.symlink_to(self.root / "artifacts/agent_v2/dataset_g/annotations/g_conf")
        with self.assertRaises(PermissionError):
            self.guard.check_path(alias / "synthetic.jsonl")

    def test_resolves_parent_traversal(self) -> None:
        with self.assertRaises(PermissionError):
            self.guard.check_path(self.root / "artifacts/agent_v2/dataset_g/g_dev/../g_conf/file")

    def test_allows_open_development_and_config_metadata(self) -> None:
        for relative in (
            "artifacts/agent_v2/dataset_g/g_dev/trace.json",
            "artifacts/agent_v2/dataset_g/annotations/g_dev/final_unblinded.jsonl",
            "configs/dataset_g/g_conf.json",
            "artifacts/agent_v2/codex_g/safe_report.json",
        ):
            self.assertEqual(self.guard.check_path(self.root / relative), self.root / relative)

    def test_hook_ignores_directory_metadata_events(self) -> None:
        self.guard.audit("os.scandir", (self.root / "artifacts/agent_v2/dataset_g/g_conf",))
        self.assertEqual(self.guard.blocked_attempts, 0)

    def test_installed_hook_blocks_builtin_and_io_open(self) -> None:
        code = """
import io, sys
from pathlib import Path
from research_v4.codex_access_guard import CodexGAccessGuard
root = Path(sys.argv[1])
guard = CodexGAccessGuard(root)
guard.install()
path = root / 'artifacts/agent_v2/dataset_g/annotations/g_conf/synthetic.jsonl'
for opener in (open, io.open):
    try:
        opener(path)
    except PermissionError:
        pass
    else:
        raise AssertionError('sealed file was not refused')
assert guard.blocked_attempts == 2
"""
        import os
        env = dict(os.environ, PYTHONPATH=str(ROOT / "scripts"), PYTHONDONTWRITEBYTECODE="1")
        result = subprocess.run([sys.executable, "-B", "-c", code, str(self.root)], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
