#!/usr/bin/env python3
"""Per-trace evidence for the compute/minimal-representation audit.

For each contrast (full band vs reduced layers, top-8 vs top-1, float vs uint8 tables) it finds
traces whose alarm outcome differs, and dumps trace metadata, evidence onset, the decoded text at
the deciding window, and the standardized score trajectory around the onset under both configs.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
torch.set_num_threads(6)

from research_v2 import io as rio  # noqa: E402
from research_v2.harness import (  # noqa: E402
    HarnessConfig, build_split_cases, routine_traces, run_case,
)
from research_v2.readings import build_readings  # noqa: E402
from repr_variants import GeomScorer, PdmVariant  # noqa: E402
from layer_sweep import WGMPrecomputed, WGMSubsetScorer  # noqa: E402

OUT = ROOT / "artifacts" / "agent_v2" / "research_v2" / "zoom" / "compute"
CFG = HarnessConfig(
    scorer="zoom", windows=(8,), routines=("cb",), splits=("S1",), modes=("D",),
    alphas=(0.10,), readings=("persist2",), bootstrap_draws=0,
    emit_q1=False, emit_audit=False, emit_ranking=False, store_score_streams=True,
)


def run(case, scorer, width):
    res = run_case(case, scorer, CFG, "cb", width, list(build_readings(("persist2",))))
    cand = res["candidates"][0]
    # compact alarms: [trace_id, weight, first_alarm_end, tolerant_first_alarm_end, n_onsets]
    alarms = {a[0]: a for a in cand["trace_alarms"]}
    thr = res["calibration"]["D"]
    return alarms, res["score_streams"], thr


def snippet(trace, lo, hi):
    ids = trace.token_ids[max(0, lo): hi].tolist()
    return rio.decode_text(ids).replace("\n", "\\n")[:200]


def main() -> None:
    batches = rio.load_core()
    cases = {c.name: c for c in build_split_cases(batches, "S1")}
    records = []

    def contrast(case_name, label, base_factory, base_w, red_label, red_factory, red_w, n=3):
        case = cases[case_name]
        ab, sb, _ = run(case, base_factory(case), base_w)
        ar, sr, _ = run(case, red_factory(case), red_w)
        lookup = {t.trace_id: t for t in case.target_traces}
        picked = []
        for tid, t in lookup.items():
            if t.trace_id not in ab or t.trace_id not in ar:
                continue
            onset = t.evidence_onset
            fb, fr = ab[tid][2], ar[tid][2]
            if t.positive and onset is not None:
                hit_b = fb is not None and onset <= fb <= onset + 8
                hit_r = fr is not None and onset <= fr <= onset + 8
                if hit_b and not hit_r:
                    kind = "drift: base hits +8, reduced misses"
                elif hit_r and not hit_b:
                    kind = "drift: reduced hits +8, base misses"
                else:
                    continue
            else:
                alarm_b = fb is not None
                alarm_r = fr is not None
                if alarm_r and not alarm_b:
                    kind = "routine: reduced false-alarms, base clean"
                elif alarm_b and not alarm_r:
                    kind = "routine: base false-alarms, reduced clean"
                else:
                    continue
            picked.append((kind, t, fb, fr))
        picked.sort(key=lambda x: x[0])
        seen: dict[str, int] = {}
        for kind, t, fb, fr in picked:
            if seen.get(kind, 0) >= n:
                continue
            seen[kind] = seen.get(kind, 0) + 1
            anchor = t.evidence_onset if t.positive and t.evidence_onset is not None else (fr or fb or 0)
            def traj(streams):
                s = streams[t.trace_id]
                d = dict(zip(s["ends"], s["scores"]))
                return {str(e): d.get(e) for e in range(anchor - 4, anchor + 13, 2) if e in d}
            records.append({
                "contrast": f"{case_name}: {label} vs {red_label}",
                "kind": kind,
                "trace_id": t.trace_id,
                "arm": t.arm, "domain": t.scenario_domain, "channel": t.channel,
                "workflow": t.workflow, "decode_len": int(t.token_count),
                "evidence_onset": t.evidence_onset,
                "first_alarm_base": fb, "first_alarm_reduced": fr,
                "text_at_anchor": snippet(t, max(0, anchor - 4), anchor + 12),
                "raw_score_base": traj(sb),
                "raw_score_reduced": traj(sr),
            })

    LATE = tuple(range(5, 16))
    contrast("b1_to_b2", "CAND-A L5-15 w8",
             lambda c: GeomScorer("top8", LATE, 8), 8,
             "A-reduced L9,10 w8",
             lambda c: WGMSubsetScorer(WGMPrecomputed(routine_traces(c.fit_traces, "cb"), 8), (9, 10)), 8)
    contrast("b1_to_b2", "CAND-A top8 w8",
             lambda c: GeomScorer("top8", LATE, 8), 8,
             "A top1-only w8",
             lambda c: GeomScorer("top1", LATE, 8), 8)
    contrast("b2_to_b1", "CAND-A L5-15 w8",
             lambda c: GeomScorer("top8", LATE, 8), 8,
             "A-reduced L12,15 w8",
             lambda c: WGMSubsetScorer(WGMPrecomputed(routine_traces(c.fit_traces, "cb"), 8), (12, 15)), 8)
    contrast("b1_to_b2", "CAND-B float64 tables w4",
             lambda c: PdmVariant(model="d1", layers="middle", window_width=4), 4,
             "CAND-B uint8 tables w4",
             lambda c: PdmVariant(model="d1", layers="middle", window_width=4, table_bits=8), 4)
    contrast("b1_to_b2", "CAND-B L5-11 w4",
             lambda c: PdmVariant(model="d1", layers="middle", window_width=4), 4,
             "CAND-B L5,9 w4",
             lambda c: __import__("layer_sweep").PDMSubsetScorer(
                 __import__("layer_sweep").PDMPrecomputed(routine_traces(c.fit_traces, "cb"), 4), (5, 9)), 4)

    (OUT / "case_study.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    for r in records:
        print(f"\n[{r['contrast']}] {r['kind']}")
        print(f"  {r['trace_id']} arm={r['arm']} dom={r['domain']} ch={r['channel']} wf={r['workflow']} "
              f"len={r['decode_len']} onset={r['evidence_onset']} base_first={r['first_alarm_base']} red_first={r['first_alarm_reduced']}")
        print(f"  text: {r['text_at_anchor'][:140]}")
        print(f"  base: {r['raw_score_base']}")
        print(f"  red : {r['raw_score_reduced']}")


if __name__ == "__main__":
    main()
