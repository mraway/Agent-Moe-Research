#!/usr/bin/env python3
"""Check the HARNESS v3.3 cells against the zoom-in round's post-hoc reconstruction.

``docs/research_v4/zoom_v32_improvement_space.md`` sections 2.1 / 2.3 produced its numbers
by re-scoring stored per-look conformal p (``zoom_v32_stat_lib.score_variant`` +
``zoom_v32_statistic.SConcentration``), never by running the harness.  This round makes the
same three changes REAL harness features, so the two must agree wherever the construction
is identical.  Where they cannot agree the reason is structural and is printed next to the
row:

* under ``H = inf`` the endpoint grid is longer, so the WINDOW REACHABILITY denominator the
  harness recomputes is 126, not the 125 the zoom inherited from the frozen ``H = 352``
  anchors of ``v3_2_a2_verify/stage2``.  The comparison below therefore also reports the
  hit count restricted to those frozen 125 keys, which is the like-for-like number;
* the zoom's alarm test is ``p <= alpha + 1e-12``, the harness's is ``p <= alpha``.  At
  ``alpha = 0.10`` with ``n_cal`` 104 / 95 / 94 no attainable p equals 0.10, so the two
  agree; the check is printed.

    PYTHONPATH=$PWD/src:$PWD/scripts python scripts/research_v4/v33_zoom_repro.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "artifacts" / "agent_v2" / "dataset_g" / "v3_3_dev"
FROZEN = ROOT / "artifacts" / "agent_v2" / "dataset_g" / "v3_2_a2_verify" / "stage2" / "result.json"

#: zoom_v32_improvement_space.md sections 2.1 / 2.3, transcribed verbatim
ZOOM = {
    ("S", "h352", 1): {
        "hit_16": 103, "hit_32": 106, "far_all": 0.09804, "far_filtered": 0.11945,
        "per_fold": [0.18947, 0.07447, 0.09615], "silent": 5,
        "worst_tertile": ("long", 0.15315), "clean_all": 0.10417,
        "scenario_far": 0.17857, "delta": 0.264,
    },
    ("S", "hinf", 1): {
        "hit_16": 110, "hit_32": 117, "far_all": 0.08088, "far_filtered": 0.09215,
        "per_fold": [0.1368, 0.0745, 0.0673], "silent": 3,
        "worst_tertile": ("long", 0.15315), "clean_all": 0.08333,
        "scenario_far": 0.14881, "delta": 0.272,
    },
    ("Z1", "h352", 1): {
        "hit_16": 103, "hit_32": 110, "far_all": 0.07353, "far_filtered": 0.08191,
        "per_fold": [0.0842, 0.0957, 0.0673], "silent": 6,
        "worst_tertile": ("medium", 0.10084), "clean_all": 0.08333,
        "scenario_far": 0.12500, "delta": 0.288,
    },
    ("Z1", "hinf", 1): {
        "hit_16": 113, "hit_32": 122, "far_all": 0.07598, "far_filtered": 0.08191,
        "per_fold": [0.0842, 0.0957, 0.0673], "silent": 5,
        "worst_tertile": ("medium", 0.10084), "clean_all": 0.08333,
        "scenario_far": 0.13095, "delta": 0.320,
    },
    ("S", "h352", 2): {
        "hit_16": 102, "hit_32": 106, "far_all": 0.06618, "far_filtered": 0.07850,
        "per_fold": [0.1158, 0.0426, 0.0769], "silent": 2,
        "worst_tertile": ("long", 0.10811), "clean_all": None,
        "scenario_far": None, "delta": 0.280,
    },
}

HEAD = {"S": "s", "Z1": "z1"}


def frozen_reachable_keys() -> set[str]:
    """The 125 keys the zoom's denominator is: reachable at +16 under the frozen H = 352."""

    payload = json.loads(FROZEN.read_text(encoding="utf-8"))
    rows = payload["cells"]["S"]["metrics"]["positives_anchored"]["per_episode"]
    return {k for k, row in rows.items() if row.get("reachable_plus_16")}


def close(a, b, tol=5e-5) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) <= tol


def main() -> int:
    frozen_keys = frozen_reachable_keys()
    print(f"frozen H=352 reachable-at-+16 denominator: {len(frozen_keys)}\n")
    verdicts: list[tuple[str, str, str]] = []
    for (statistic, horizon, debounce), want in ZOOM.items():
        name = f"stage2_{horizon}_nostrat_d{debounce}_{HEAD[statistic]}"
        path = RUNS / name / "result.json"
        if not path.exists():
            print(f"{statistic}·{horizon}·d{debounce}: MISSING ({name})")
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        cell = payload["cells"][statistic]
        metrics = cell["metrics"]
        pa = metrics["positives_anchored"]
        far = metrics["far"]
        per_episode = pa["per_episode"]
        got_16 = pa["recall"]["penalty_plus_16"]
        got_32 = pa["recall"]["penalty_plus_32"]
        restricted = sum(
            1
            for k in frozen_keys
            if k in per_episode and per_episode[k]["hit_plus_16"]
        )
        restricted_32 = sum(
            1
            for k in frozen_keys
            if k in per_episode and per_episode[k]["hit_plus_32"]
        )
        matched = ((payload.get("comparison_anchored") or {}).get("rows") or {}).get(
            "matched"
        ) or {}
        delta = (matched.get("bootstrap") or {}).get("point_estimate")
        silent = (metrics["classes"]["silent_attack"] or {}).get("alarm_count")
        folds = [
            cell["folds"][str(k)]["far"]["filtered"]["far"] for k in range(3)
        ]
        rows = [
            ("hit +16 (harness denominator)",
             f"{got_16['hit_count']}/{got_16['reachable_count']}",
             f"{want['hit_16']}/125",
             "EXACT" if (got_16["hit_count"] == want["hit_16"]
                         and got_16["reachable_count"] == 125) else "see note"),
            ("hit +16 (restricted to the frozen 125)",
             f"{restricted}/{len(frozen_keys)}", f"{want['hit_16']}/125",
             "EXACT" if restricted == want["hit_16"] else "DIFFERS"),
            ("hit +32 (restricted to the frozen 125)",
             f"{restricted_32}/{len(frozen_keys)}", f"{want['hit_32']}/125",
             "EXACT" if restricted_32 == want["hit_32"] else "DIFFERS"),
            ("far.all", f"{far['all']['far']:.5f}", f"{want['far_all']:.5f}",
             "EXACT" if close(far["all"]["far"], want["far_all"]) else "DIFFERS"),
            ("far.filtered", f"{far['filtered']['far']:.5f}",
             f"{want['far_filtered']:.5f}",
             "EXACT" if close(far["filtered"]["far"], want["far_filtered"]) else "DIFFERS"),
            ("per-fold far.filtered",
             "/".join(f"{v:.4f}" for v in folds),
             "/".join(f"{v:.4f}" for v in want["per_fold"]),
             "EXACT" if all(close(a, b, 1e-3) for a, b in zip(folds, want["per_fold"]))
             else "DIFFERS"),
            ("silent (40 denominator)",
             f"{silent}/40", f"{want['silent']}/40",
             "EXACT" if silent == want["silent"] else "DIFFERS"),
            ("worst length tertile",
             f"{far['worst_length_tertile'][0]} {far['worst_length_tertile'][1]:.5f}",
             f"{want['worst_tertile'][0]} {want['worst_tertile'][1]:.5f}",
             "EXACT" if (far["worst_length_tertile"][0] == want["worst_tertile"][0]
                         and close(far["worst_length_tertile"][1],
                                   want["worst_tertile"][1])) else "DIFFERS"),
            ("Delta vs P @+16 (matched measured FAR)",
             "None" if delta is None else f"{delta:.4f}",
             f"{want['delta']:.4f}",
             "EXACT" if close(delta, want["delta"], 1e-3) else "DIFFERS"),
        ]
        if want["clean_all"] is not None:
            got = far["clean"]["all"]["far"]
            rows.append(("far.clean.all", f"{got:.5f}", f"{want['clean_all']:.5f}",
                         "EXACT" if close(got, want["clean_all"]) else "DIFFERS"))
        if want["scenario_far"] is not None:
            got = far["all"]["matched_group_far"]
            rows.append(("scenario FAR", f"{got:.5f}", f"{want['scenario_far']:.5f}",
                         "EXACT" if close(got, want["scenario_far"]) else "DIFFERS"))
        print(f"=== {statistic} · H={horizon[1:]} · debounce {debounce}  ({name})")
        for label, got, want_v, verdict in rows:
            print(f"    {label:42} harness={got:22} zoom={want_v:18} {verdict}")
            verdicts.append((f"{statistic}|{horizon}|d{debounce}", label, verdict))
        print()
    differing = [v for v in verdicts if v[2] not in ("EXACT", "see note")]
    noted = [v for v in verdicts if v[2] == "see note"]
    print(f"{len(verdicts)} comparisons: {len(verdicts) - len(differing) - len(noted)} EXACT, "
          f"{len(noted)} structural note, {len(differing)} DIFFERING")
    for row in differing:
        print("   DIFFERS:", row)
    for row in noted:
        print("   NOTE   :", row)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
