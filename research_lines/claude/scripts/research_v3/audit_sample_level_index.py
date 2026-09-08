"""Sample-level audit step 1 (read-only): compact per-trace index of the frozen outputs.

Parses the three final ``outputs.jsonl`` cells into one pickle keyed by
``(target, column, key)`` holding the endpoint arrays needed downstream:
end / p_S / p_M / p_J / p_fused / state / attribution / horizon_censored.
Nothing is recomputed from routing here; this is a pure re-read of the frozen artifact.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CELLS = {
    "b1": ROOT / "artifacts/agent_v2/research_v3/trm3/final_b1_both/outputs.jsonl",
    "b2": ROOT / "artifacts/agent_v2/research_v3/trm3/final_b2_both/outputs.jsonl",
    "h384": ROOT / "artifacts/agent_v2/research_v3/trm3/final_h384_both/outputs.jsonl",
    "c1_heldout": ROOT / "artifacts/agent_v2/research_v3/trm3/final_c1_heldout_C1/outputs.jsonl",
}
OUT = Path("/tmp/claude-1000/-home-wzh-Agent-Moe-Research--claude-worktrees-algorithm-research-proposals-427363/7e87c1f8-78bb-4b9f-9189-12780e6e8833/scratchpad/trm3_endpoint_index.pkl")


def main() -> None:
    index: dict[tuple[str, str, str], dict] = {}
    for target, path in CELLS.items():
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                cal = row["calibration"]            # "<D|C1>|<set>|half<n>"
                column, evalset, half = cal.split("|")
                slot = (target, column, evalset, row["key"])
                block = index.setdefault(
                    slot,
                    {
                        "arm": row["arm"],
                        "class": row["class"],
                        "half": int(half.replace("half", "")),
                        "end": [],
                        "p_S": [],
                        "p_M": [],
                        "p_J": [],
                        "p_fused": [],
                        "state": [],
                        "attribution": [],
                        "censored": [],
                        "regime": [],
                    },
                )
                block["end"].append(row["end"])
                block["p_S"].append(row["p_S"])
                block["p_M"].append(row["p_M"])
                block["p_J"].append(row["p_J"])
                block["p_fused"].append(row["p_fused"])
                block["state"].append(row["state"])
                block["attribution"].append(row["attribution"])
                block["censored"].append(bool(row["horizon_censored"]))
                block["regime"].append(row["regime_flag"])
        print(f"parsed {target}: {sum(1 for k in index if k[0] == target)} trace-cells", flush=True)
    for block in index.values():
        for field in ("end", "p_S", "p_M", "p_J", "p_fused"):
            block[field] = np.array(
                [np.nan if v is None else v for v in block[field]], dtype=float
            )
        block["censored"] = np.array(block["censored"], dtype=bool)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("wb") as handle:
        pickle.dump(index, handle)
    print("wrote", OUT, len(index), "trace-cells")


if __name__ == "__main__":
    main()
