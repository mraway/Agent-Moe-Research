"""Additional Python file-open guard for Codex's OPEN-only G development.

This is not an operating-system sandbox. In particular it must not be used as
permission to run arbitrary native readers against a sealed pool.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
from typing import Any


class CodexGAccessGuard:
    def __init__(self, repo_root: Path) -> None:
        self.repo_root = Path(repo_root).resolve()
        base = self.repo_root / "artifacts/agent_v2"
        self.blocked_roots = tuple(path.resolve() for path in (
            base / "dataset_g/g_conf",
            base / "dataset_g/annotations/g_conf",
            base / "codex_g/invalidated_smoke",
        ))
        self.audited_open_count = 0
        self.blocked_attempts = 0

    def check_path(self, path: Any) -> Path:
        resolved = Path(os.fsdecode(path)).resolve()
        if any(resolved == root or root in resolved.parents for root in self.blocked_roots):
            self.blocked_attempts += 1
            raise PermissionError(f"Codex G access REFUSED: {resolved}")
        return resolved

    def audit(self, event: str, args: tuple[Any, ...]) -> None:
        if event != "open" or not args:
            return
        path = args[0]
        # Existing file descriptors do not reveal a portable pathname. This
        # wrapper never opens sealed files before installing the hook.
        if isinstance(path, (str, bytes, os.PathLike)):
            self.audited_open_count += 1
            self.check_path(path)

    def install(self) -> None:
        sys.addaudithook(self.audit)

    def summary(self) -> dict[str, Any]:
        return {
            "scope": "Python open audit plus explicit source-smoke path checks; not OS isolation",
            "blocked_roots": [str(path.relative_to(self.repo_root)) for path in self.blocked_roots],
            "audited_open_count": self.audited_open_count,
            "blocked_attempts": self.blocked_attempts,
        }
