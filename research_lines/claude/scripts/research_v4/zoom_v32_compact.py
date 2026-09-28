"""Compact the v3.2 per-look ``outputs.jsonl`` dump into an npz for post-hoc fusion work.

EXPLORATORY / G-dev ONLY.  Reads one ``--outputs all`` run directory produced by the
FROZEN harness (``run_detectors_g.py --stage score`` against the a2_verify stage-1 manifest)
and keeps, per statistic and per episode, the in-horizon look grid with its running-max
conformal p and its instantaneous p.  Nothing here re-decides anything: it only stores the
numbers the harness already computed so a rule can be re-thresholded without re-scoring.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    keys: list[str] = []
    key_index: dict[str, int] = {}
    stat_index: dict[str, int] = {}
    stats: list[str] = []

    ki: list[int] = []
    si: list[int] = []
    ends: list[int] = []
    looks: list[int] = []
    p_run: list[float] = []
    p_inst: list[float] = []
    chan_index: dict[str, int] = {}
    chans: list[str] = []
    ci: list[int] = []

    with (run_dir / "outputs.jsonl").open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("horizon_censored"):
                continue
            key = row["key"]
            if key not in key_index:
                key_index[key] = len(keys)
                keys.append(key)
            stat = row["statistic"]
            if stat not in stat_index:
                stat_index[stat] = len(stats)
                stats.append(stat)
            channel = row.get("channel") or ""
            if channel not in chan_index:
                chan_index[channel] = len(chans)
                chans.append(channel)
            ki.append(key_index[key])
            si.append(stat_index[stat])
            ci.append(chan_index[channel])
            ends.append(int(row["end"]))
            looks.append(int(row["k"]))
            p_run.append(float(row["p_fused"]))
            pv = row.get("p_inst")
            p_inst.append(float("nan") if pv is None else float(pv))

    np.savez_compressed(
        args.out,
        keys=np.array(keys, dtype=object),
        statistics=np.array(stats, dtype=object),
        channels=np.array(chans, dtype=object),
        key_index=np.array(ki, dtype=np.int32),
        stat_index=np.array(si, dtype=np.int8),
        chan_index=np.array(ci, dtype=np.int8),
        end=np.array(ends, dtype=np.int32),
        look=np.array(looks, dtype=np.int32),
        p=np.array(p_run, dtype=np.float32),
        p_inst=np.array(p_inst, dtype=np.float32),
    )
    print(f"rows={len(ki)} episodes={len(keys)} statistics={stats} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
