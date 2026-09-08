"""Does the lens's geometric statistic predict the frozen CAND-A outcome?
Joins the FROZEN CAND-A alarms (read-only) with my recomputed per-trace geometry.
Diagnostic only: no new detector, no threshold, no configuration is selected."""
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"
FROZEN = REPO / "artifacts" / "agent_v2" / "research_v2" / "wgm" / "c2_g1_middle_late" / "result.json"
LABELS = REPO / "docs" / "research_v2" / "labels" / "product_onset_v1_adjudicated.jsonl"
CID = "mode=D|alpha=0.1|reading=persist2"


def main(anchor="product"):
    labels = {json.loads(l)["trace_id"]: json.loads(l) for l in LABELS.read_text().splitlines() if l.strip()}
    geo = json.load(open(OUT / f"refute_analyse_middle_late_{anchor}.json"))
    per = {}
    for b in ("b1", "b2"):
        for tid, v in geo[b]["traces"].items():
            per[tid] = dict(v, batch=b)
    frozen = json.load(open(FROZEN))
    rows = []
    for cr in frozen["case_runs"]:
        cand = [c for c in cr["candidates"] if c["candidate_id"].endswith(CID)][0]
        for tid, w, first, band_first, cnt in cand["trace_alarms"]:
            if tid not in per:
                continue
            lab = labels[tid]
            onset = int(lab[f"{anchor}_onset"])
            ev = int(lab["evidence_onset"])
            clean = (first is not None) and (first >= ev) and (first <= ev + 16)
            rows.append({"case": cr["case"], "trace": tid, "domain": per[tid]["domain"],
                         "pclass": per[tid]["pclass"], "first_alarm": first,
                         "evidence_onset": ev, "clean16": clean,
                         "d1": per[tid]["d1_med"], "g1": per[tid]["g1_med"],
                         "g1_pctW": per[tid]["g1_pct_window"], "d1_pctW": per[tid]["d1_pct_window"]})
    rows.sort(key=lambda r: (r["case"], r["g1"]))
    print(f"{'case':10s} {'trace':44s} {'dom':16s} {'g1':>8s} {'g1pctW':>7s} {'d1':>7s} {'first':>6s} {'ev':>4s} clean16")
    for r in rows:
        print(f"{r['case']:10s} {r['trace'][:44]:44s} {r['domain']:16s} {r['g1']:8.1f} {r['g1_pctW']:7.3f} {r['d1']:7.2f} "
              f"{str(r['first_alarm']):>6s} {r['evidence_onset']:4d} {'YES' if r['clean16'] else '-'}")
    (OUT / f"refute_detector_join_{anchor}.json").write_text(json.dumps(rows, indent=2))
    # rank-inversion count: detected traces whose g1 is below an undetected one
    for case in sorted({r["case"] for r in rows}):
        sub = [r for r in rows if r["case"] == case]
        det = [r for r in sub if r["clean16"]]
        und = [r for r in sub if not r["clean16"]]
        inv = sum(1 for d in det for u in und if d["g1"] < u["g1"])
        print(f"{case}: detected={len(det)} undetected={len(und)} g1-rank inversions (detected below undetected) = {inv}/{len(det)*max(1,len(und))}")
        if det:
            print(f"   min g1 among cleanly detected: {min(d['g1'] for d in det):.1f} ({min(det, key=lambda d: d['g1'])['trace']})")
            print(f"   max g1 among undetected:       {max(u['g1'] for u in und):.1f} ({max(und, key=lambda u: u['g1'])['trace']})")


if __name__ == "__main__":
    import sys
    main(*(sys.argv[1:] or []))
