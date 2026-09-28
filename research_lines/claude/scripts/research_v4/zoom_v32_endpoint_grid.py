"""EXPLORATORY (G-dev only): rebuild the UNTRUNCATED look grid of every G-dev episode
from trace.json metadata alone (channel_segments + token counts), i.e. without loading
any routing tensor.  The grid is the same object trm3_g.segmented_windows returns
(V1 channels, width 8, tag_scope=message) but with no horizon cut, so we can ask
"which look index does token T fall at" beyond the frozen H = 352.

Writes one JSON: {key: {"ends": [...], "tags": [...]}} plus per-trace metadata.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from research_v2 import io_g, trm3_g

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / (sys.argv[2] if len(sys.argv) > 2 else "artifacts/agent_v2/dataset_g/g_dev")
BATCH = RUN.name
WIDTH = 8
VIEW = trm3_g.VIEWS["V1"]


def endpoints(tags, width=WIDTH):
    ends, out_tags = [], []
    for start, stop, tag in io_g.channel_runs(tags, VIEW.channels):
        if stop - start < width:
            continue
        for e in range(start + width - 1, stop):
            ends.append(e)
            out_tags.append(tag)
    order = sorted(range(len(ends)), key=lambda i: ends[i])
    return [ends[i] for i in order], [out_tags[i] for i in order]


def main(out_path: str) -> None:
    result = {}
    paths = io_g.iter_trace_paths(RUN)
    for p in paths:
        trace = json.loads(p.read_text())
        tid = str(trace["trace_id"])
        steps_by_ep = io_g._generation_steps(trace)
        for idx in sorted(steps_by_ep):
            steps = io_g.with_token_axis(p.parent, steps_by_ep[idx])
            token_count = sum(
                int(s.get("output_token_count") or len(s.get("output_token_ids") or ()))
                for s in steps
            )
            spans = io_g.segment_spans(steps)
            tags = io_g.channel_tag_array(spans, token_count, scope="message")
            ends, etags = endpoints(tags)
            key = f"{BATCH}|{tid}#ep{idx}"
            result[key] = {
                "token_count": token_count,
                "ends": ends,
                "tags": etags,
                "pair_group_id": str(trace.get("pair_group_id", "")),
            }
    Path(out_path).write_text(json.dumps(result))
    print(f"wrote {len(result)} episodes -> {out_path}")


if __name__ == "__main__":
    main(sys.argv[1])
