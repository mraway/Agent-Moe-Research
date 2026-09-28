#!/usr/bin/env python3
"""Run the shared onboarding smoke without reading G-conf text labels.

The shared smoke is imported unchanged. Its label subset list is narrowed in
this process only, and a file-open audit hook also rejects both sealed routing
and sealed annotation paths (including symlink aliases).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
for directory in (ROOT / "scripts", ROOT / "src"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from research_v4.codex_access_guard import CodexGAccessGuard

OPEN_LABEL_SUBSETS = ("g_fit", "g_cal", "g_dev")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", type=Path,
        default=ROOT / "artifacts/agent_v2/codex_g/onboarding_guarded_smoke.json",
    )
    args = parser.parse_args()
    output = args.json.resolve()
    output_root = (ROOT / "artifacts/agent_v2/codex_g").resolve()
    if output_root not in output.parents:
        parser.error("smoke output must be inside artifacts/agent_v2/codex_g/")
    if output.exists():
        parser.error("output already exists; choose a new filename rather than overwriting evidence")
    guard = CodexGAccessGuard(ROOT)
    guard.check_path(output)
    guard.install()

    from research_v4 import codex_smoke_g as shared

    shared.LABEL_SUBSETS = OPEN_LABEL_SUBSETS
    original_check = shared._refuse_if_sealed

    def checked_path(path: Path) -> Path:
        return original_check(guard.check_path(path))

    shared._refuse_if_sealed = checked_path
    status = shared.main(["--subset", "g_dev", "--json", str(output)])
    report = json.loads(output.read_text(encoding="utf-8"))
    assert set(report["labels"]) == set(OPEN_LABEL_SUBSETS)
    expected_arms = {
        "attack": 352, "benign_control": 192, "clean": 192,
        "benign_lexical": 24, "legitimate_refusal": 24,
    }
    assert report["labels"]["g_dev"]["rows"] == 784
    assert report["labels"]["g_dev"]["arms"] == expected_arms
    shapes = report["routing_probe"]["shapes"]
    assert shapes["top_k_ids"][0::2] == [24, 4]
    assert shapes["router_logits"] == [24, shapes["top_k_ids"][1], 32]
    assert guard.blocked_attempts == 0
    report["codex_access_guard"] = {
        **guard.summary(),
        "label_subsets_read": list(OPEN_LABEL_SUBSETS),
        "g_conf_labels_read": False,
        "g_conf_routing_read": False,
        "source_smoke_modified_on_disk": False,
    }
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("Codex guarded smoke: exact G-dev census/shapes passed; G-conf labels excluded.")
    print(json.dumps(report["codex_access_guard"], ensure_ascii=False))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
