#!/usr/bin/env python3
"""Re-parse saved Phase A outputs and record recoverable protocol violations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase_a import parse_assistant_output, refresh_run_summary  # noqa: E402


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    audited: list[dict[str, Any]] = []
    for trace_path in sorted(run_dir.glob("*/*/trace.json")):
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        warnings: list[dict[str, Any]] = []
        for event in trace["events"]:
            if event["kind"] != "model_generation":
                continue
            parsed = parse_assistant_output(event["content"])
            event.setdefault("parsed", {})["protocol_warning"] = parsed.protocol_warning
            if parsed.kind == "invalid" or parsed.protocol_warning:
                warnings.append(
                    {
                        "event_index": event["event_index"],
                        "agent_step": event["agent_step"],
                        "error": parsed.error,
                        "warning": parsed.protocol_warning,
                    }
                )
        has_error = bool(warnings)
        outcome = trace["outcome"]
        outcome["recoverable_protocol_error"] = has_error
        if (
            has_error
            and trace["perturbation"]["arm"] == "clean"
            and not outcome["goal_plan_deviation_started"]
        ):
            outcome["stratum"] = "clean_hard_negative"
            outcome["normal_reference_eligible"] = False
        audit = {
            "schema_version": 1,
            "recoverable_protocol_error": has_error,
            "warnings": warnings,
        }
        trace["protocol_audit"] = audit
        _write_json(trace_path, trace)
        audited.append({"trace_id": trace["trace_id"], **audit})
    refresh_run_summary(run_dir)
    print(json.dumps(audited, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
