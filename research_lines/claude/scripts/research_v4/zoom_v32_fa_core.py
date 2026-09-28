"""EXPLORATORY (G-dev only): shared helpers for the v3.2 false-alarm / channel-transition zoom.

Rebuilds the frozen v3.2 primary decision layer POST HOC from stored per-look scores
(``desc_V1_channel_dump/outputs.jsonl``) and the frozen per-fold thresholds of
``v3_2_a2_verify``.  Two identities make this exact and are asserted by
``zoom_v32_fa_report.py``:

* ``p(k) == min_{j<=k} p_inst(j)`` on the in-horizon looks (running max of z <-> running
  min of the instantaneous conformal p on the SAME reference set, ``trm3_g.instantaneous_p``);
* alarm(k) iff ``p(k) <= alpha_eff(fold)``, alarms restricted to non-censored looks.

Nothing here reads G-conf.  Read-only over existing artefacts; the harness is never re-run.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
G = ROOT / "artifacts/agent_v2/dataset_g"
PRIMARY = G / "v3_2_a2_verify/stage2/result.json"
STAGE1 = G / "v3_2_a2_verify/stage1/threshold_manifest.json"
LABELS = G / "annotations/g_dev/final_unblinded.jsonl"
MAPPING = G / "private/g_dev/case_mapping.jsonl"
CHANNELS = ("analysis", "commentary", "final")
NORMAL_ARMS = ("clean", "benign_control", "benign_lexical", "legitimate_refusal")
#: the FAR denominators of prereg 7.4 / 4: `all` = the normal union, `filtered` = the
#: quality-filtered union with legitimate_refusal removed (the matched-FAR denominator).
FILTERED_ARMS = ("clean", "benign_control", "benign_lexical")


def load_store(path: str | Path) -> dict:
    with open(path, "rb") as handle:
        return pickle.load(handle)


def load_primary() -> dict:
    with PRIMARY.open() as handle:
        return json.load(handle)


def load_labels() -> dict[str, dict]:
    out = {}
    with LABELS.open() as handle:
        for line in handle:
            d = json.loads(line)
            out[d["episode_id"]] = d
    return out


def load_mapping() -> dict[str, dict]:
    out = {}
    with MAPPING.open() as handle:
        for line in handle:
            d = json.loads(line)
            out[d["episode_id"]] = d
    return out


def episode_id(key: str) -> str:
    """``g_dev|g-dev-001--attack#ep0`` -> ``g-dev-001--attack#ep0``."""

    return key.split("|", 1)[1]


def run_structure(chan: np.ndarray, end: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(run_id, pos_in_run, chan_ordinal)`` for one episode's look sequence.

    A new ``channel_runs`` run starts when the channel label changes OR when the endpoint
    token index jumps by more than 1: inside one run ``segmented_windows`` emits endpoints
    at consecutive token indices, so a jump means an intervening run that was shorter than
    the window width and therefore emitted no endpoint at all.  ``chan_ordinal`` is the
    frozen position-bucket index -- the endpoint ordinal WITHIN that channel of the
    episode, cumulative across runs (``trm3_g.segmented_windows`` ``seen[tag]``).
    """

    n = len(chan)
    run_id = np.zeros(n, dtype=np.int32)
    pos = np.zeros(n, dtype=np.int32)
    ordinal = np.zeros(n, dtype=np.int32)
    seen: dict[int, int] = {}
    r = 0
    for i in range(n):
        if i > 0 and (chan[i] != chan[i - 1] or int(end[i]) != int(end[i - 1]) + 1):
            r += 1
        run_id[i] = r
        pos[i] = 0 if (i == 0 or run_id[i] != run_id[i - 1]) else pos[i - 1] + 1
        c = int(chan[i])
        ordinal[i] = seen.get(c, 0)
        seen[c] = ordinal[i] + 1
    return run_id, pos, ordinal


def prev_channel(chan: np.ndarray, run_id: np.ndarray) -> np.ndarray:
    """Channel of the previous VISIBLE run for every look; ``-1`` inside the first run."""

    out = np.full(len(chan), -1, dtype=np.int8)
    last = -1
    for i in range(len(chan)):
        if i > 0 and run_id[i] != run_id[i - 1]:
            last = int(chan[i - 1])
        out[i] = last
    return out


def masked_running_p(p_inst: np.ndarray, keep: np.ndarray) -> np.ndarray:
    """Running conformal p when only ``keep`` looks may update the running max.

    Masked looks inherit the running value; a look before the first kept one has ``p = 1``.
    This is the frozen threshold applied to a masked running max, i.e. the CONSERVATIVE
    post-hoc reading: the honest variant would also recompute Z^g on the calibration paths
    under the same mask, which lowers the reference and therefore raises both the FAR and
    the hit rate relative to what this function reports.
    """

    out = np.ones(len(p_inst), dtype=np.float64)
    cur = 1.0
    for i in range(len(p_inst)):
        if keep[i] and not np.isnan(p_inst[i]) and p_inst[i] < cur:
            cur = float(p_inst[i])
        out[i] = cur
    return out


def first_alarm(end: np.ndarray, p: np.ndarray, live: np.ndarray, alpha: float) -> int | None:
    m = live & (p <= alpha + 1e-12)
    if not m.any():
        return None
    return int(end[m].min())
