"""Re-audit every STRICT pre-onset alarm of both frozen candidates with +/-32 tokens of context."""
from __future__ import annotations
import json
from pathlib import Path
import torch
torch.set_num_threads(6)
import research_v2.io as rio

OUT = Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/research_v2/zoom/topic_vs_task")
D = json.load(open(OUT / "records.json"))
W = {"A": 8, "B": 4}
CTX = 32
batches = rio.load_core()
by_id = {t.trace_id: t for b in batches.values() for t in b}

rows = []
for r in D["records"]:
    if not r["positive"]:
        continue
    onset = r["evidence_onset"]
    pre = [run for run in r["runs"] if run[0] < onset]
    if not pre:
        continue
    t = by_id[r["trace_id"]]
    ids = t.token_ids.tolist()
    w = W[r["cand"]]
    for start_end, k, blocks in pre:
        s = max(0, start_end - w + 1)
        rows.append({
            "cand": r["cand"], "case": r["case"], "trace_id": r["trace_id"],
            "arm": r["arm"], "domain": r["domain"], "channel": r["channel"],
            "workflow": r["workflow"], "token_count": r["token_count"],
            "evidence_onset": onset, "completion_boundary": r["completion_boundary"],
            "alarm_end": start_end, "offset_to_onset": start_end - onset,
            "run_windows": k, "run_blocks": blocks,
            "window_text": rio.decode_text(ids[s:start_end + 1]),
            "before32": rio.decode_text(ids[max(0, s - CTX):s]),
            "after32": rio.decode_text(ids[start_end + 1:min(len(ids), start_end + 1 + CTX)]),
            "onset_ctx": rio.decode_text(ids[max(0, onset - 16):min(len(ids), onset + 32)]),
            "prefix_before_onset": rio.decode_text(ids[:onset]),
        })
json.dump(rows, open(OUT / "pre_onset_audit_ctx32.json", "w"), indent=1)
print("pre-onset alarm onsets:", len(rows))
for r in rows:
    print("=" * 100)
    print(f"[{r['cand']}] {r['case']} {r['trace_id']} arm={r['arm']} domain={r['domain']} chan={r['channel']} wf={r['workflow']}")
    print(f"  len={r['token_count']} onset={r['evidence_onset']} cb={r['completion_boundary']} alarm_end={r['alarm_end']} off={r['offset_to_onset']} run={r['run_windows']}win/{r['run_blocks']}blk")
    print(f"  BEFORE32: ...{r['before32']!r}")
    print(f"  WINDOW  : {r['window_text']!r}")
    print(f"  AFTER32 : {r['after32']!r}...")
    print(f"  AT-ONSET: {r['onset_ctx']!r}")
