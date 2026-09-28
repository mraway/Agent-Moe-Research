#!/usr/bin/env python3
"""Validate the generated dataset G configs against the frozen design.

Checks (``src/agent_v3/factory/validate.py``):

* the frozen Agent v3 experiment schema still accepts every config;
* scenario ids are unique across all of G and seeds are fixed per scenario;
* fixtures are disjoint across subsets (G-medium excepted, design section 5);
* the 72-cell design is filled with exactly two scenarios per cell, and the
  channel shares are the frozen 48/48/48 core with tool_output at 1/4 overall;
* the normal task ratio is R1:R2:R3:R4 = 2:2:3:3;
* every completion-evidence item is answerable from the fixture -- re-derived by
  executing the read-only tools through the real ``SupportToolEnvironment``;
* all record dates are ISO 8601 and every record carries ``record_as_of``;
* no identifier number is reused by a second entity anywhere in G;
* the T0/T1/T2 injected texts are byte-identical to the frozen pilot wordings;
* attack markers are unique per scenario;
* benign controls are non-instructional, legitimate refusals carry no contract;
* the held-out workflow types appear in exactly one subset;
* only type-A attacks exist and the restricted stubs stay in the schema;
* the collection plan and the scenario-level collected arms agree;
* G-medium is a true paired re-run;
* the manifest sha256 of every generated file still matches;
* the prompt token budget, measured with the local gpt-oss tokenizer (tokenizer
  only, CPU, no model is loaded).

Usage::

    factory_validate.py                     # everything, tokenizer included
    factory_validate.py --skip-tokenizer    # no transformers import
    factory_validate.py --json report.json  # machine-readable report
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agent_v3.factory import validate as factory_validate  # noqa: E402


def _tokenizer() -> Any:
    from transformers import AutoTokenizer

    model_config = json.loads(
        (ROOT / "configs" / "pilot_gpt_oss_20b_mxfp4.json").read_text(encoding="utf-8")
    )
    return AutoTokenizer.from_pretrained(
        model_config["model_id"],
        revision=model_config["revision"],
        cache_dir=ROOT / model_config["cache_dir"],
        local_files_only=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=ROOT / "configs" / "dataset_g")
    parser.add_argument("--skip-tokenizer", action="store_true")
    parser.add_argument("--json", type=Path, help="write the full report here")
    args = parser.parse_args()

    tokenizer = None if args.skip_tokenizer else _tokenizer()
    results = factory_validate.run_all(args.config_dir, tokenizer=tokenizer)

    width = max(len(result.name) for result in results)
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] {result.name.ljust(width)}  {result.detail}")
        for failure in result.failures[:10]:
            print(f"         - {failure}")
        if len(result.failures) > 10:
            print(f"         - ... and {len(result.failures) - 10} more")

    passed = sum(result.passed for result in results)
    print(f"\n{passed}/{len(results)} checks passed")
    if args.json is not None:
        args.json.write_text(
            json.dumps(
                {
                    "checks_passed": passed,
                    "checks_total": len(results),
                    "results": [result.as_dict() for result in results],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
