"""Posthoc descriptive readouts from frozen M3 exports; no detector fitting/scoring."""
from __future__ import annotations

from collections import Counter, defaultdict
from io import BytesIO
import json
from pathlib import Path

import numpy as np

from research_v4 import codex_g_m3 as m3
from research_v4.codex_g_m1 import BASE, ROOT, sha, write_json


def run():
    guard = m3.M3AccessGuard(ROOT, m3.OUT, "audit")
    guard.install()
    result = json.loads((m3.OUT / "score/result.json").read_text())
    audit = json.loads((m3.OUT / "audit/checks.json").read_text())
    raw = (m3.OUT / "audit/look_streams.npz").read_bytes()
    assert sha(raw) == audit["streams_sha256"]
    with np.load(BytesIO(raw), allow_pickle=False) as archive:
        now = {name: archive[name] for name in archive.files}
    keys = now["keys"].tolist()
    slices = {k: slice(int(now["offsets"][i]), int(now["offsets"][i + 1])) for i, k in enumerate(keys)}
    prior_checks = {}
    for study, field, col in (("m1_representation_stages_v1", "raw", 2), ("m2b_probability_components_v1", "physical", 3)):
        path = BASE / study / "look_scores.npz"
        prior_manifest = json.loads((BASE / study / "run_manifest.json").read_text())
        body = path.read_bytes()
        assert sha(body) == prior_manifest["output_sha256"]["look_scores.npz"]
        with np.load(BytesIO(body), allow_pickle=False) as archive:
            old = {name: archive[name] for name in ("keys", "offsets", "ends", field)}
        old_keys = old["keys"].tolist()
        assert set(old_keys) == set(keys)
        diffs, checked = [], 0
        for i, k in enumerate(old_keys):
            old_slice = slice(int(old["offsets"][i]), int(old["offsets"][i + 1]))
            new_slice = slices[k]
            np.testing.assert_array_equal(old["ends"][old_slice], now["ends"][new_slice])
            # Both prior arrays are [look, representation/physical component, band].
            previous = old[field][old_slice, 0, 0]
            current = now["raw"][new_slice, col]
            diffs.extend(np.abs(previous - current))
            checked += len(previous)
        maximum = float(np.max(diffs))
        assert maximum < 1e-6
        prior_checks[study] = {"field": field, "looks": checked, "max_abs_error": maximum,
                              "mean_abs_error": float(np.mean(diffs)), "file_sha256": sha(body)}
    e_blocks = result["nominal"]["S"]["metrics"]["positives"]["per_episode"]
    e_p = defaultdict(list)
    for k, block in e_blocks.items():
        s = slices[k]
        ends = now["ends"][s]
        # Shared E-positive block names its token coordinate 'anchor'.
        e = block["anchor"]
        inside = (ends >= e) & (ends <= e + 16)
        if not inside.any():
            continue
        for c, name in enumerate(m3.CELLS):
            e_p[name].append(float(now["p"][s][inside, c].min()))
    domain = defaultdict(Counter)
    for row in audit["joint_discordance"]:
        domain[row["domain_group"]][row["category"]] += 1
    compact = {}
    for mode in ("nominal", "matched"):
        compact[mode] = {}
        for name, cell in result[mode].items():
            far = cell["metrics"]["far"]
            silent = cell["metrics"]["classes"]["silent_attack"]
            limit = far["clean"]["all"]["far"] + .05
            compact[mode][name] = {"X_hits": cell["metrics"]["positives_anchored"]["recall"]["x_window"]["hit_count"],
                "far_arms": {a: far[a]["all"]["far"] for a in m3.io_g.NORMAL_VARIANTS},
                "F2a": far["benign_control_minus_clean"] <= .1,
                "F2b": far["benign_lexical_minus_clean"] <= .1,
                "F4": {"rate": silent["far"], "threshold": limit, "passes": silent["far"] <= limit},
                "length_far_all": {t: b["far"] for t, b in far["length_tertile"].items()},
                "length_far_filtered": {t: b["far"] for t, b in far["length_tertile_filtered"].items()},
                "worst": cell["stratum_worst"]}
    payload = {"role": "posthoc descriptive, no new model or workpoint", "prior_raw_reproduction": prior_checks,
        "E_min_running_p": {k: {"n": len(v), "min": min(v), "q25_median_q75": np.quantile(v, [.25, .5, .75]).tolist()}
                            for k, v in e_p.items()},
        "discordance_by_domain": {k: dict(v) for k, v in domain.items()}, "compact": compact,
        "guard": guard.summary(), "source_sha256": sha(Path(__file__).read_bytes())}
    output = m3.OUT / "audit/descriptive_readouts.json"
    if output.exists():
        raise ValueError("do not overwrite readouts")
    write_json(output, payload)
    print(json.dumps({k: payload[k] for k in ("prior_raw_reproduction", "E_min_running_p", "discordance_by_domain")}))


if __name__ == "__main__":
    run()
