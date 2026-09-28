"""Verify that the lens' refit reproduces the frozen CAND-A g1 score streams exactly."""
from __future__ import annotations
import json, sys
from pathlib import Path
import torch
sys.path.insert(0, str(Path(__file__).resolve().parent))
from whitening_common import CACHE, REPO, VAR_FLOOR, load_all, windows_of  # noqa: E402
from research_v2 import io as rio  # noqa: E402

FROZEN = REPO / "artifacts/agent_v2/research_v2/wgm/c2_g1_middle_late/result.json"


def main() -> None:
    d = json.loads(FROZEN.read_text())
    batches = load_all()
    by_id = {t.trace_id: t for bl in batches.values() for t in bl}
    report = {"frozen": str(FROZEN), "runs": []}
    for run in d["case_runs"]:
        fb = run["case"].split("_to_")[0]
        rt = [t for t in batches[fb] if rio.arm_class(t) in ("clean", "benign")]
        assert len(rt) == run["fit_trace_count"], (len(rt), run["fit_trace_count"])
        M = torch.cat([windows_of(t)[1] for t in rt])
        mu, sd = M.mean(0), M.std(0) + VAR_FLOOR
        centre = ((M - mu) / sd).mean(0)
        maxdiff, maxrel, n = 0.0, 0.0, 0
        for tid, st in run["score_streams"].items():
            tr = by_id[tid]
            ends, w = windows_of(tr)
            assert ends.tolist() == st["ends"]
            mine = (((w - mu) / sd - centre) ** 2).sum(1)
            v = torch.tensor(st["scores"], dtype=torch.float32)
            diff = (v - mine).abs()
            maxdiff = max(maxdiff, float(diff.max()))
            maxrel = max(maxrel, float((diff / v.clamp_min(1e-6)).max()))
            n += 1
        print(f"case={run['case']} fit_batch={fb} traces={n} max_abs_diff={maxdiff:.6g} max_rel_diff={maxrel:.3g}")
        report["runs"].append({"case": run["case"], "fit_batch": fb, "traces": n,
                               "max_abs_diff": maxdiff, "max_rel_diff": maxrel})
    (CACHE / "verify_reproduction.json").write_text(json.dumps(report, indent=2))
    print("wrote", CACHE / "verify_reproduction.json")


if __name__ == "__main__":
    main()
