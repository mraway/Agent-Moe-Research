"""CPU-only, independent artifact/axis/hash audit of the fixed replay smoke."""
from __future__ import annotations

import json
from pathlib import Path

import torch
from safetensors.torch import load_file

from research_v4.codex_g_hidden_replay_smoke import (
    HIDDEN_KEYS, OUT, PLAN, ROOT, ROUTE_KEYS, TRACE_DIRS, ReplayGuard, digest, require, write_json,
)


def main():
    torch.set_num_threads(1)
    guard = ReplayGuard(ROOT)
    guard.install()
    report = json.loads((OUT / "result.json").read_text())
    frozen = json.loads((OUT / "preflight.json").read_text())
    require(report["status"] in ("EXACT_REPLAY_ON_SAMPLED_TRACES", "NUMERICAL_DIFFERENCE_REQUIRES_REVIEW"), "incomplete run")
    require(digest(PLAN) == frozen["plan_sha256"], "plan changed")
    for group in ("input_sha256", "source_sha256", "kernel_source_sha256"):
        for name, expected in frozen[group].items():
            require(digest(guard.check_path(ROOT / name)) == expected, f"hash changed: {name}")
    require(len(report["runs"]) == 10, "missing replay")
    checks, total_bytes = [], 0
    for directory in TRACE_DIRS:
        trace = json.loads((directory / "trace.json").read_text())
        rows = [json.loads(line) for line in (directory / "manifest.jsonl").read_text().splitlines()]
        events = [e for e in trace["events"] if e["kind"] == "model_generation"]
        axis = {}
        for event in events:
            first = event["routing_step_index_prefill"]
            for i in range(first, first + 1 + event["output_token_count"]):
                axis[i] = event
        runs = [r for r in report["runs"] if r["trace_id"] == trace["trace_id"]]
        require([r["pass_index"] for r in runs] == [1, 2], "pass ordering differs")
        for phase in ("prefill", "decode"):
            path = guard.check_path(OUT / trace["trace_id"] / f"{phase}.safetensors")
            meta = runs[0]["files"][str(path.relative_to(ROOT))]
            require(path.stat().st_size == meta["bytes"] and digest(path) == meta["sha256"], "replay tensor hash changed")
            total_bytes += path.stat().st_size
            data = load_file(str(path), device="cpu")
            selected = [r for r in rows if r["phase"] == phase]
            require(data["token_ids"].shape == (len(selected),), "saved axis length differs")
            for key in HIDDEN_KEYS:
                require(data[key].shape == (1 if key == "final_norm_hidden" else 24, len(selected), 2880), "hidden shape differs")
                require(data[key].dtype == torch.bfloat16 and torch.isfinite(data[key]).all(), "hidden dtype or finiteness differs")
            same_routes = {key: True for key in ROUTE_KEYS}
            for t, row in enumerate(selected):
                old = load_file(str(guard.check_path(directory / row["tensor_file"])), device="cpu")
                for key, expected in dict(token_ids=row["token_ids"][-1], positions=row["positions"][-1],
                                          routing_step_index=row["step_index"], episode_index=axis[row["step_index"]]["episode_index"],
                                          agent_step=axis[row["step_index"]]["agent_step"]).items():
                    require(int(data[key][t]) == expected, f"axis differs: {key}")
                for key in ROUTE_KEYS:
                    a, b = old[key][:, -1:, :].contiguous(), data[key][:, t:t+1, :].contiguous()
                    same_routes[key] &= a.dtype == b.dtype and torch.equal(a.view(torch.uint8), b.view(torch.uint8))
            checks.append(dict(trace_id=trace["trace_id"], phase=phase, positions=len(selected),
                               saved_routes_bitwise_match_old=same_routes))
            if report["status"] == "EXACT_REPLAY_ON_SAMPLED_TRACES":
                require(all(same_routes.values()), "exact replay claim not borne out by saved tensors")
                require(all(d["elements"] > 0 and d["bitwise_equal"] and d["max_abs"] == 0
                            for d in runs[1]["repeat_at_saved_positions"][phase].values()), "repeat exact claim inconsistent")
    require(sum(r["positions"] for r in checks if r["phase"] == "decode") == 760, "decode coverage differs")
    require(sum(r["positions"] for r in checks if r["phase"] == "prefill") == 10, "prefill coverage differs")
    result = dict(status="PASS", replay_status=report["status"], original_files_verified=len(frozen["input_sha256"]),
                  saved_tensor_bytes=total_bytes, saved_tensor_MiB=total_bytes / 2**20, checks=checks,
                  note="Stored replay tensors independently rejoined to originals; second pass hidden equality audited from recorded comparison metrics, not re-executed.",
                  input_result_sha256=digest(OUT / "result.json"), audit_script_sha256=digest(Path(__file__)), guard=guard.summary())
    write_json(OUT / "audit.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
