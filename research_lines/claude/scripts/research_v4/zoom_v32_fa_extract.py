"""EXPLORATORY (G-dev only): compact per-look store from the V1 channel dump.

Streams ``desc_V1_channel_dump/outputs.jsonl`` line by line (162 MB) and writes one
pickle holding, per episode key, the in-order look arrays the false-alarm zoom needs:
look index ``k``, endpoint token ``end``, harmony ``channel``, running conformal ``p``
(``p_S``), instantaneous ``p_inst`` and the horizon-censored flag.

Nothing here reads G-conf.  Read-only over existing artefacts; no harness re-run.
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DUMP = ROOT / "artifacts/agent_v2/dataset_g/v3_2_dev_descriptive/desc_V1_channel_dump/outputs.jsonl"
CHANNELS = ("analysis", "commentary", "final")
CHAN_ID = {c: i for i, c in enumerate(CHANNELS)}


def main(out_path: str) -> None:
    rows: dict[str, list[tuple[int, int, int, float, float, bool]]] = {}
    meta: dict[str, dict[str, object]] = {}
    n = 0
    with DUMP.open() as handle:
        for line in handle:
            d = json.loads(line)
            key = d["key"]
            bucket = rows.get(key)
            if bucket is None:
                bucket = rows[key] = []
                meta[key] = {
                    "arm": d["arm"],
                    "fold": int(d["fold"]),
                    "class": d["class"],
                    "session_id": d["session_id"],
                    "episode_index": int(d["episode_index"]),
                    "trace_id": d["trace_id"],
                }
            pi = d.get("p_inst")
            bucket.append(
                (
                    int(d["k"]),
                    int(d["end"]),
                    CHAN_ID[d["channel"]],
                    float(d["p_S"]),
                    float("nan") if pi is None else float(pi),
                    bool(d["horizon_censored"]),
                )
            )
            n += 1
    store: dict[str, dict[str, np.ndarray]] = {}
    for key, bucket in rows.items():
        bucket.sort(key=lambda r: r[0])
        store[key] = {
            "k": np.array([r[0] for r in bucket], dtype=np.int32),
            "end": np.array([r[1] for r in bucket], dtype=np.int32),
            "chan": np.array([r[2] for r in bucket], dtype=np.int8),
            "p": np.array([r[3] for r in bucket], dtype=np.float64),
            "p_inst": np.array([r[4] for r in bucket], dtype=np.float64),
            "cens": np.array([r[5] for r in bucket], dtype=bool),
        }
    with open(out_path, "wb") as handle:
        pickle.dump({"store": store, "meta": meta, "channels": CHANNELS, "rows": n}, handle, protocol=4)
    print(f"rows={n} episodes={len(store)} -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1])
