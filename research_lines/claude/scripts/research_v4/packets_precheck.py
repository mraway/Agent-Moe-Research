#!/usr/bin/env python3
"""Routing-blind pre-checks, token-axis re-derivation and length statistics.

    packets_precheck.py --run artifacts/agent_v2/dataset_g/g_fit --subset g_fit \
        --output-dir artifacts/agent_v2/dataset_g/precheck [--no-tensor-check]

Writes ``<subset>_precheck.jsonl`` (one row per episode), ``<subset>_token_axis.json``
(the independent P0 section 6.3 check) and ``<subset>_length.json`` (per-R-type
distributions, tertile cutpoints, survival curve and the H-rule input).  No
routing statistic is computed: tensors are only shape-checked.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agent_v3.packets import build, precheck  # noqa: E402
from routing import validate_trace  # noqa: E402


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, action="append")
    parser.add_argument("--subset", required=True, action="append")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--no-tensor-check", action="store_true")
    parser.add_argument(
        "--no-repo-validate",
        action="store_true",
        help="skip the post-hoc routing.validate_trace pass (shape/integrity only)",
    )
    parser.add_argument("--min-surviving-paths", type=int, default=90)
    return parser.parse_args()


def main() -> int:
    args = _args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for run_root, subset in zip(args.run, args.subset, strict=True):
        scenarios = build.scenarios_by_id(run_root)
        rows: list[dict] = []
        axis: list[dict] = []
        repo: list[dict] = []
        for trace_dir in build.iter_trace_dirs(run_root):
            scenario = scenarios.get(trace_dir.parent.name)
            rows.extend(precheck.precheck_trace(trace_dir, scenario))
            axis.append(
                precheck.token_axis_report(trace_dir, check_tensors=not args.no_tensor_check)
            )
            if not args.no_repo_validate:
                result = validate_trace(trace_dir)
                repo.append(
                    {
                        "path": str(trace_dir),
                        "passed": bool(result["passed"]),
                        "schema_version": result.get("schema_version"),
                        "step_count": result.get("step_count"),
                        "token_count": result.get("token_count"),
                        "max_top_k_weight_error": result.get("max_top_k_weight_error"),
                        "top_k_weight_atol": result.get("top_k_weight_atol"),
                    }
                )
        for row in rows:
            row["precheck_failures"] = precheck.precheck_failures(row)
            row["precheck_pass"] = not row["precheck_failures"]
        build.write_jsonl(args.output_dir / f"{subset}_precheck.jsonl", rows)
        failed = [report for report in axis if not report["passed"]]
        (args.output_dir / f"{subset}_token_axis.json").write_text(
            json.dumps(
                {
                    "run": str(run_root),
                    "traces": len(axis),
                    "passed": len(axis) - len(failed),
                    "failed": failed,
                    "tensor_files_checked": sum(r["tensor_files_checked"] for r in axis),
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        if repo:
            errors = sorted({r["max_top_k_weight_error"] for r in repo})
            (args.output_dir / f"{subset}_repo_validate.json").write_text(
                json.dumps(
                    {
                        "validator": "routing.validate_trace",
                        "traces": len(repo),
                        "passed": sum(r["passed"] for r in repo),
                        "failed": [r for r in repo if not r["passed"]],
                        "schema_versions": sorted({r["schema_version"] for r in repo}),
                        "distinct_max_top_k_weight_error": errors,
                        "top_k_weight_atol": sorted({r["top_k_weight_atol"] for r in repo}),
                        "total_decode_tokens": sum(int(r["token_count"] or 0) for r in repo),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
        report = precheck.length_report(rows, minimum_surviving_paths=args.min_surviving_paths)
        (args.output_dir / f"{subset}_length.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(
            f"{subset}: {len(axis)} traces, {len(rows)} episodes, "
            f"{sum(r['precheck_pass'] for r in rows)} pass the pre-checks, token-axis "
            f"{len(axis) - len(failed)}/{len(axis)} clean, H_raw="
            f"{report['h_rule']['largest_k_raw_tokens']} "
            f"(pre-check-passing {report['h_rule_passing_prechecks']['largest_k_raw_tokens']}), "
            f"validate_trace {sum(r['passed'] for r in repo)}/{len(repo)}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
