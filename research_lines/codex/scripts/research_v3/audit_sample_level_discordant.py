"""Sample-level audit (read-only): enumerate the P1 discordant pairs.

Recomputes, from the frozen final result.json cells only, which primary-event positives
TRM-3 hits within +8 that B-M does not (and vice versa), at BOTH the matched effective
alpha and the nominal alpha, for every calibration column.  Writes JSON to stdout.
Development-set evidence (B1/B2, h384 replay, C1); B3 unused.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CELLS = {
    "b1": ROOT / "artifacts/agent_v2/research_v3/trm3/final_b1_both/result.json",
    "b2": ROOT / "artifacts/agent_v2/research_v3/trm3/final_b2_both/result.json",
    "h384": ROOT / "artifacts/agent_v2/research_v3/trm3/final_h384_both/result.json",
}


def discordance(hits_a, hits_b):
    keys = sorted(set(hits_a) & set(hits_b))
    return {
        "only_trm3": [k for k in keys if hits_a[k] and not hits_b[k]],
        "only_bm": [k for k in keys if hits_b[k] and not hits_a[k]],
        "both": [k for k in keys if hits_a[k] and hits_b[k]],
        "neither": [k for k in keys if not hits_a[k] and not hits_b[k]],
        "pair_count": len(keys),
    }


def main() -> None:
    out = {}
    for target, path in CELLS.items():
        payload = json.loads(path.read_text(encoding="utf-8"))
        out[target] = {}
        for column, cell in payload["columns"].items():
            block = {}
            # nominal alpha = 0.10 column, straight from the stored per-variant hits
            trm3_nom = cell["variants"]["trm3"]["sets"]["target"]["primary_event_hits_plus_8"]
            bm_nom = cell["variants"]["m_only"]["sets"]["target"]["primary_event_hits_plus_8"]
            block["nominal"] = discordance(trm3_nom, bm_nom)
            block["nominal"]["alpha"] = payload["alpha"]
            p1 = cell.get("p1_matched_alpha") or {}
            if p1.get("status") == "evaluated":
                am = p1["alpha_matched"]
                key = f"{float(am):g}"
                bm_m = p1["b_m_at_matched_alpha"][key]["primary_event_hits_plus_8"]
                # TRM-3's decision is alpha-free (p_fused <= 0.10), so its matched-column
                # hits are its nominal hits; only B-M is re-thresholded.
                trm3_m = trm3_nom
                block["matched"] = discordance(trm3_m, bm_m)
                block["matched"]["alpha"] = am
                block["matched"]["stored_mcnemar"] = p1["mcnemar_matched"]
            # also record every trace's anchor-hit detail for trm3 and m_only
            out[target][column] = block
    json.dump(out, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
