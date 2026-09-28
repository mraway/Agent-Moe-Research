"""Decoded text of every non-drift false-alarm excursion (clean / benign / resist)."""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np, torch
torch.set_num_threads(6)
import research_v2.io as rio

OUT = Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/research_v2/zoom/topic_vs_task")
D = json.load(open(OUT / "records.json")); Z = np.load(OUT / "zstreams.npz")
W = {"A": 8, "B": 4}
batches = rio.load_core(); by_id = {t.trace_id: t for b in batches.values() for t in b}
rows = []
for r in D["records"]:
    if r["positive"] or not r["alarm"]:
        continue
    t = by_id[r["trace_id"]]; ids = t.token_ids.tolist(); w = W[r["cand"]]
    key = f'{r["case"]}::{r["cand"]}::{r["trace_id"]}'
    st = Z[key + "::stat"]; ends = Z[key + "::ends"]
    i = int(np.argmax(np.where(np.isfinite(st), st, -1e18)))
    pe = int(ends[i]); s = max(0, pe - w + 1)
    rows.append({**{k: r[k] for k in ("cand", "case", "trace_id", "arm", "domain", "channel",
                                      "workflow", "token_count", "threshold", "peak_stat",
                                      "peak_end", "max_run_windows", "max_run_blocks",
                                      "first_alarm_end", "peak_layer_frac", "peak_late_mid")},
                  "peak_window_text": rio.decode_text(ids[s:pe + 1]),
                  "before32": rio.decode_text(ids[max(0, s - 32):s]),
                  "after32": rio.decode_text(ids[pe + 1:min(len(ids), pe + 33)])})
json.dump(rows, open(OUT / "false_alarm_text.json", "w"), indent=1)
rows.sort(key=lambda r: (r["cand"], r["case"], r["arm"], -r["peak_stat"]))
print("n false-alarm traces:", len(rows))
for r in rows:
    print("=" * 100)
    print(f"[{r['cand']}] {r['case']} {r['trace_id']} arm={r['arm']} dom={r['domain']} ch={r['channel']} len={r['token_count']}")
    print(f"  thr={r['threshold']:.2f} peak={r['peak_stat']:.2f}@{r['peak_end']} run={r['max_run_windows']}win/{r['max_run_blocks']}blk lf={r['peak_layer_frac']:.2f} lm={r['peak_late_mid']:.2f} first={r['first_alarm_end']}")
    print(f"  BEFORE: ...{r['before32']!r}")
    print(f"  PEAKWIN: {r['peak_window_text']!r}")
    print(f"  AFTER : {r['after32']!r}...")
