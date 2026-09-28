#!/usr/bin/env python3
"""sha256 manifest of the FCM output directory."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUT  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    entries = {}
    for path in sorted(OUT.rglob("*")):
        if path.is_file() and path.name != "manifest_fcm.json":
            entries[str(path.relative_to(OUT))] = {"sha256": sha256(path), "bytes": path.stat().st_size}
    (OUT / "manifest_fcm.json").write_text(json.dumps(entries, indent=1), encoding="utf-8")
    print(f"{len(entries)} files")
    for name in sorted(entries):
        if name.count("/") == 0 or name.endswith("result.json"):
            print(f"{entries[name]['sha256']}  {name}")


if __name__ == "__main__":
    main()
