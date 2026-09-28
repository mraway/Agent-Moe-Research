"""T8: latency-floor table -- every variant ranked by the latency it buys and the FAR it costs."""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path("/home/wzh/Agent-Moe-Research/artifacts/agent_v2/research_v2/zoom/timing")
AUDIT = json.loads((OUT / "timing_audit.json").read_text())
SUP = json.loads((OUT / "supplement.json").read_text())
CASES = ("b1_to_b2", "b2_to_b1")
REF = {
    "CAND-A": {c: AUDIT["cases"][c]["CAND-A"]["width_sweep"]["w8|persist2"] for c in CASES},
    "CAND-B": {c: AUDIT["cases"][c]["CAND-B"]["width_sweep"]["w4|persist2"] for c in CASES},
}

rows = []
for r in SUP["matched"]:
    ref = REF[r["reference"]]
    if any(v is None for v in r["lat"]):
        continue
    excess = max(
        max(
            r["benign"][i] - ref[c]["far_benign"],
            r["resist"][i] - ref[c]["far_resist"],
            r["far"][i] - ref[c]["far_all"],
        )
        for i, c in enumerate(CASES)
    )
    gain = min(ref[c]["median_latency"] - r["lat"][i] for i, c in enumerate(CASES))
    r8gain = min(r["r8"][i] - ref[c]["r8"] for i, c in enumerate(CASES))
    rows.append((r["reference"], r["variant"], gain, excess, r8gain, r))

lines = ["## T8  Latency floor: what each variant buys and what it costs (both directions)\n"]
lines.append(
    "| reference | variant | min latency gain (tokens) | max excess FAR over reference | min R+8 change | lat b1->b2 / b2->b1 | R+8 b1->b2 / b2->b1 |"
)
lines.append("|---|---|---|---|---|---|---|")
for ref_name, variant, gain, excess, r8gain, r in sorted(rows, key=lambda t: (t[0], -t[2], t[3])):
    if gain < 0:
        continue
    lines.append(
        f"| {ref_name} | {variant.replace(chr(124), ' / ')} | {gain:+.1f} | {excess:+.3f} | {r8gain:+.3f} | "
        f"{r['lat'][0]:.1f} / {r['lat'][1]:.1f} | {r['r8'][0]:.3f} / {r['r8'][1]:.3f} |"
    )
text = "\n".join(lines)
(OUT / "tables.md").write_text((OUT / "tables.md").read_text() + "\n\n" + text)
print(text)
