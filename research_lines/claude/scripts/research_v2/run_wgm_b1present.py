#!/usr/bin/env python3
"""WGM sensitivity run: add the B1 brief=present ROUTINE traces to the mode-D calibration pool.

Spec 1.6 makes this the main-table option for the B2->B1 direction ("n=100 per half").
``research_v2.io.load_b1_present`` refuses the pool because spec 1.1's claim that all 120
brief=present traces are behaviourally normal is false: 3 of the 40 attack-arm traces have
``goal_plan_deviation_started = true``.  This script therefore builds the pool itself and keeps
only the clean + benign_control arms (80 traces), which is exactly the ``cb`` routine definition
used by the main tables; no drift and no resisted-attack trace enters the calibration pool.

Everything else (scorer, protocol, readings, alphas) is identical to the main C1 run.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import torch
from safetensors.torch import load_file, save_file

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from phase_a.normal_manifold import ManifoldTrace, workflow_family  # noqa: E402
from research_v2 import io as rio  # noqa: E402
from research_v2.harness import HarnessConfig, run_harness, write_result  # noqa: E402

ROUTINE_ARMS = ("clean", "benign_control")


def load_b1_present_routine() -> tuple[rio.LoadedTrace, ...]:
    index_path = rio.DEFAULT_B1_DIR / "sample_index.jsonl"
    actual = rio.sha256(index_path)
    if actual != rio.B1_INDEX_SHA256:
        raise ValueError(f"b1 sample-index hash mismatch: {actual}")
    rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line]
    rows = [row for row in rows if row["response_brief_condition"] == "present"]
    if len(rows) != 120:
        raise ValueError(f"expected 120 b1 brief=present traces, found {len(rows)}")
    rows = [row for row in rows if row["arm"] in ROUTINE_ARMS]
    if any(row["goal_plan_deviation_started"] for row in rows):
        raise ValueError("routine pool must contain no drift trace")
    cache_dir = rio.EXTRA_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    traces: list[rio.LoadedTrace] = []
    for row in rows:
        record = ManifoldTrace(
            batch="b1",
            trace_id=str(row["trace_id"]),
            pair_group_id=str(row["pair_group_id"]),
            fold=int(row["preregistered_fold"]),
            arm=str(row["arm"]),
            workflow=str(row["workflow"]),
            workflow_family=workflow_family(str(row["workflow"])),
            channel=str(row["attack_channel"]),
            domain=str(row["target_domain"]),
            positive=False,
            completion_boundary=None,
            evidence_onset=None,
            trace_dir=(rio.DEFAULT_B1_DIR / row["relative_path"]).resolve(),
            cache_file=(cache_dir / "b1_present" / f"{row['trace_id']}.safetensors").resolve(),
        )
        if not record.cache_file.exists():
            top_k_ids, probabilities, token_ids = rio._decode_tensors(record.trace_dir)
            record.cache_file.parent.mkdir(parents=True, exist_ok=True)
            save_file(
                {
                    "top_k_ids": top_k_ids.contiguous(),
                    "probabilities": probabilities.contiguous(),
                    "token_ids": token_ids.contiguous(),
                },
                record.cache_file,
            )
        cached = load_file(record.cache_file)
        traces.append(
            rio.LoadedTrace(
                record=record,
                top_k_ids=cached["top_k_ids"].long(),
                token_ids=cached["token_ids"].long(),
                scenario_domain=str(row["target_domain"]),
                brief_condition="present",
            )
        )
    return tuple(traces)


def main() -> None:
    torch.set_num_threads(8)
    start = time.time()
    extra = load_b1_present_routine()
    print(f"b1 brief=present routine calibration pool: {len(extra)} traces", flush=True)
    batches = rio.load_core()
    config = HarnessConfig(
        scorer="wgm",
        scorer_config={"metric": "g1", "layers": "middle"},
        windows=(8,),
        routines=("cb",),
        splits=("S1",),
        modes=("D",),
        alphas=(0.05, 0.10),
        bootstrap_draws=500,
        b1_present_calibration=True,
        store_score_streams=False,
    )
    result = run_harness(
        config, batches=batches, extra_calibration=extra, progress=lambda t: print(f"  {t}", flush=True)
    )
    result["run_name"] = "wgm/sens_b1_present"
    result["b1_present_pool"] = {
        "arms": list(ROUTINE_ARMS),
        "trace_count": len(extra),
        "note": (
            "spec 1.1 claims all 120 b1 brief=present traces are behaviourally normal; 3 of the 40 "
            "attack-arm traces actually drift, so the pool is restricted to clean + benign_control"
        ),
    }
    result["wall_clock_seconds"] = round(time.time() - start, 2)
    output = ROOT / "artifacts" / "agent_v2" / "research_v2" / "wgm" / "sens_b1_present"
    manifest = write_result(result, output)
    print(json.dumps(manifest["sha256"], indent=2), flush=True)


if __name__ == "__main__":
    main()
