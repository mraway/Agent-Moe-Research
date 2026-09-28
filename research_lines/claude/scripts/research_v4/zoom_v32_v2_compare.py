"""V1 vs V2 (drop `commentary`) on the SAME positives: is A-V2's 103 the same 103?

EXPLORATORY, G-dev only.  Post hoc from the stored per-look p of two frozen-parameter
runs (the a2_verify-manifest V1 score run and the V2 single-stage ablation re-run with
`--outputs all`).  Each view keeps its OWN registered rule (its own E_view floor and its
own p at alpha = 0.10); only the hit SETS are compared.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict

import numpy as np

from zoom_v32_fusion import (  # type: ignore
    HORIZON,
    NORMAL_VARIANTS,
    Looks,
    alarms_for,
    far_block,
    load_meta,
    mcnemar_exact,
    single,
)

V1_RESULT = "artifacts/agent_v2/dataset_g/v3_2_zoom_fusion/v1_SPM_dump/result.json"
V2_RESULT = "artifacts/agent_v2/dataset_g/v3_2_zoom_fusion/v2_SP_dump/result.json"
V1_COMPACT = "artifacts/agent_v2/dataset_g/v3_2_zoom_fusion/compact_v1_SPM.npz"
V2_COMPACT = "artifacts/agent_v2/dataset_g/v3_2_zoom_fusion/compact_v2_SP.npz"
OUT = "artifacts/agent_v2/dataset_g/v3_2_zoom_fusion/v1_vs_v2.json"


def hits(looks: Looks, per_episode: dict, alpha: float = 0.10) -> tuple[set, set]:
    alarms = alarms_for(looks, single("S", alpha))
    reach, hit = set(), set()
    for key, block in per_episode.items():
        if not block["reachable_plus_16"]:
            continue
        reach.add(key)
        ends = alarms[key]
        if ends.size and int(block["e_view"]) <= int(ends[0]) <= int(block["x"]) + HORIZON:
            hit.add(key)
    return reach, hit


def main() -> int:
    v1 = json.load(open(V1_RESULT, encoding="utf-8"))
    v2 = json.load(open(V2_RESULT, encoding="utf-8"))
    pe1 = v1["cells"]["S"]["metrics"]["positives_anchored"]["per_episode"]
    pe2 = v2["cells"]["S"]["metrics"]["positives_anchored"]["per_episode"]
    r1, h1 = hits(Looks(V1_COMPACT), pe1)
    r2, h2 = hits(Looks(V2_COMPACT), pe2)
    paired = sorted(r1 & r2)

    def strata(keys) -> dict:
        out: dict[str, Counter] = defaultdict(Counter)
        for k in keys:
            b = pe1[k]
            out["family"][b["attack_family_id"] or "none"] += 1
            out["domain_group"][b["domain_group"]] += 1
            out["injection_channel"][b["injection_channel"]] += 1
            out["wording_tier"][b["wording_tier"]] += 1
        return {k: dict(v) for k, v in out.items()}

    only1 = sorted((h1 - h2) & set(paired))
    only2 = sorted((h2 - h1) & set(paired))
    payload = {
        "note": "EXPLORATORY, G-dev only",
        "reachable_v1": len(r1),
        "reachable_v2": len(r2),
        "paired": len(paired),
        "hits_v1": len(h1 & set(paired)),
        "hits_v2": len(h2 & set(paired)),
        "mcnemar": mcnemar_exact(
            [k in h1 for k in paired], [k in h2 for k in paired]
        ),
        "only_v1_keys": only1,
        "only_v2_keys": only2,
        "only_v1_strata": strata(only1),
        "only_v2_strata": strata(only2),
        "union_v1_or_v2": len((h1 | h2) & set(paired)),
        "intersection": len(h1 & h2 & set(paired)),
    }
    json.dump(payload, open(OUT, "w", encoding="utf-8"), indent=1, default=str)
    print(json.dumps({k: v for k, v in payload.items() if not k.endswith("_keys")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
