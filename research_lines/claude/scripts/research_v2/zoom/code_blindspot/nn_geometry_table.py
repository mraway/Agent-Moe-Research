"""Consolidated per-trace table for the report (merges stats_*.json and neighbours_*.json)."""

from __future__ import annotations

import json
import statistics as st
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
OUT = REPO / "artifacts" / "agent_v2" / "research_v2" / "zoom_code_blindspot"


def main(band="middle_late", anchor="product", space="whitened"):
    stats = json.load(open(OUT / f"stats_{band}_{anchor}.json"))
    nb = json.load(open(OUT / f"neighbours_{band}_{anchor}_{space}.json"))
    rows = []
    for batch in ("b1", "b2"):
        sb = stats["batches"][batch]
        nbb = nb["batches"][batch]
        rmed = sb["routine_holdout_d1"]["trace"]["q50"]
        rmed_wf = sb["routine_holdout_d1"]["workflow"]["q50"]
        for r in sb["per_trace"]:
            n = nbb["per_trace"][r["trace_id"]]
            rows.append({
                "batch": batch, **r,
                "routine_med": rmed, "routine_med_wf": rmed_wf,
                "ratio": round(r["d1_med"] / rmed, 3),
                "ratio_wf": round(r["d1_med"] / rmed_wf, 3),
                "nb_json_tool": n["neighbour_forms"].get("json_tool_call", 0.0),
                "nb_json_any": round(n["neighbour_forms"].get("json_tool_call", 0.0)
                                     + n["neighbour_forms"].get("json_field", 0.0), 4),
                "nb_prose": n["neighbour_forms"].get("prose", 0.0),
                "nb_traces": n["distinct_neighbour_traces"],
                "same_wf": n["same_workflow_share"],
            })
    (OUT / f"table_{band}_{anchor}_{space}.json").write_text(json.dumps(rows, indent=2))

    hdr = (f"{'trace_id':56s} {'bat':3s} {'cls':3s} {'nw':3s} {'cb%':>4s} {'d1':>6s} {'x/rt':>5s} "
           f"{'pct':>5s} {'<q95':>5s} {'dtool':>6s} {'dprose':>6s} {'dctr':>6s} {'nbJSON':>6s} {'nbpro':>6s} {'ntr':>3s} {'swf':>5s}")
    print(hdr)
    for grp, keep in (("PROGRAMMING", lambda r: r["domain"] == "programming"),
                      ("P3 tool-call deliverable", lambda r: r["product_class"].startswith("P3")),
                      ("OTHER-DRIFT (per domain median)", None)):
        print(f"\n-- {grp} --")
        if keep is None:
            by = {}
            for r in rows:
                if r["domain"] == "programming":
                    continue
                by.setdefault((r["batch"], r["domain"]), []).append(r)
            for (b, dom), rs in sorted(by.items()):
                f = lambda k: st.median([x[k] for x in rs])
                print(f"{dom+' (n='+str(len(rs))+')':56s} {b:3s} {'':3s} {'':3s} "
                      f"{100*f('code_body_windows')/f('n_windows'):4.0f} {f('d1_med'):6.2f} {f('ratio'):5.2f} "
                      f"{f('pct_in_routine_trace_holdout'):5.3f} {f('frac_win_below_routine_q95'):5.2f} "
                      f"{f('d_tool_med'):6.2f} {f('d_prose_med'):6.2f} {f('d_centre_med'):6.2f} "
                      f"{f('nb_json_tool'):6.3f} {f('nb_prose'):6.3f} {f('nb_traces'):3.0f} {f('same_wf'):5.2f}")
            continue
        for r in rows:
            if not keep(r):
                continue
            print(f"{r['trace_id'][:56]:56s} {r['batch']:3s} {r['product_class']:3s} {r['n_windows']:3d} "
                  f"{100*r['code_body_windows']/r['n_windows']:4.0f} {r['d1_med']:6.2f} {r['ratio']:5.2f} "
                  f"{r['pct_in_routine_trace_holdout']:5.3f} {r['frac_win_below_routine_q95']:5.2f} "
                  f"{r['d_tool_med']:6.2f} {r['d_prose_med']:6.2f} {r['d_centre_med']:6.2f} "
                  f"{r['nb_json_tool']:6.3f} {r['nb_prose']:6.3f} {r['nb_traces']:3d} {r['same_wf']:5.2f}")


if __name__ == "__main__":
    import sys
    main(*(sys.argv[1:] or []))
