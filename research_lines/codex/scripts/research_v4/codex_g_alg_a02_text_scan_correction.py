"""Replace only invalid A02-D date-coverage counts; preserve frozen raw distance outputs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
import time

import numpy as np
import torch
from tokenizers import Tokenizer

from research_v4 import codex_g_alg_a02_cross_round as c

d = c.d
OUT = d.OUT/"cross_round_text_correction_v1"
EXTRA = ("docs/research_v4/codex_g_alg_a02d_text_scan_correction_v1.md",
         "tests/test_research_v4_codex_g_alg_a02_text_scan_correction.py")


def date_segments(tokenizer, episodes, keys):
    found = []
    for key in sorted(keys):
        ep = episodes[key]
        ix = np.flatnonzero(np.asarray(ep["tags"]) == "analysis")
        if not len(ix):
            continue
        cut = np.flatnonzero((np.diff(ix) != 1) | (np.diff(ep["step_ids"][ix]) != 0))+1
        for block in np.split(ix, cut):
            text = tokenizer.decode(ep["token_ids"][block].tolist(), skip_special_tokens=False)
            matches = list(re.finditer(r"\b[0-9]{4}-[0-9]{2}-[0-9]{2}\b", text))
            if matches:
                found.append({"key": key, "segment_start": int(block[0]), "segment_end": int(block[-1]),
                              "snippets": [text[max(0, m.start()-40):m.end()+40] for m in matches]})
    return found


def snapshot():
    paths = (*EXTRA, str((c.OUT/"source_manifest.json").relative_to(d.ROOT)), str((c.OUT/"result.json").relative_to(d.ROOT)))
    return {**c.snapshot(), **{p: d.pf.digest((d.ROOT/p).read_bytes()) for p in paths}}


def unpack_window(window, weighted):
    ids = np.asarray(window["top_k_ids_t_l_k"])
    weights = np.asarray(window["selected_weights_t_l_k"]) if weighted else np.full(ids.shape, .25)
    if ids.shape != (8, 24, 4) or np.any(np.diff(np.sort(ids, axis=-1), axis=-1) == 0):
        raise ValueError("invalid saved actual selection")
    np.testing.assert_allclose(weights.sum(-1), 1, atol=1e-6, rtol=0)
    p = np.zeros((8, 24, 32), dtype=np.float64)
    np.put_along_axis(p, ids, weights, axis=-1)
    return p


def main(stage, expected):
    guard = d.Guard(d.ROOT); guard.install()
    if stage == "freeze":
        sources = snapshot(); OUT.mkdir(exist_ok=False)
        d.ai.write_json(OUT/"source_manifest.json", {"source_sha256": sources, "utc": datetime.now(timezone.utc).isoformat()})
        print(d.pf.digest((OUT/"source_manifest.json").read_bytes()), flush=True); return
    budget = d.Budget(); body = (OUT/"source_manifest.json").read_bytes()
    if d.pf.digest(body) != expected or snapshot() != json.loads(body)["source_sha256"]:
        raise ValueError("text correction source/input freeze mismatch")
    checked = d.pf.CheckedInputs(guard)
    frozen, meta, _, _ = d.load_inputs(checked)
    _, _, episodes, _ = d.pf.normal_inputs(checked)
    tokenizer = Tokenizer.from_str(d.TOKENIZER.read_text())
    raw = json.loads((c.OUT/"result.json").read_text())
    oldlog = json.loads((d.OUT/"run/run_manifest.json").read_text())
    queries = checked.json(d.OUT/"run/case_cards.json", oldlog["output_sha256"]["case_cards.json"])
    query_map = {(q["key"], q["end"]): q for q in queries}
    max_error, count = 0., 0
    for q in raw["queries"]:
        for name, scopes in q["cells"].items():
            qr = unpack_window(query_map[(q["key"], q["end"])], name == "W_mean")
            for scope in scopes.values():
                distances = []
                for donor in scope["donors"]:
                    w = donor["window"]; e = w["end"]
                    np.testing.assert_array_equal(w["window_ids"], episodes[w["key"]]["token_ids"][e-7:e+1])
                    dr = unpack_window(w, name == "W_mean")
                    distance = .5*np.square(np.sqrt(qr.mean(0))-np.sqrt(dr.mean(0))).sum()/24
                    max_error = max(max_error, abs(distance-donor["distance"]))
                    distances.append(distance); count += 1
                max_error = max(max_error, abs(np.mean(distances)-scope["raw"]))
    if max_error > 2e-6:
        raise ValueError("independent actual-weight distance replay failed")
    coverage = []
    for old in raw["normal_date_coverage"]:
        fold, ep = old["fold"], old["episode_index"]
        info = frozen["folds"][str(fold)]["banks"][f"analysis/ep{ep}"]
        keys = info["fit_keys"]
        matches = date_segments(tokenizer, episodes, keys)
        date_keys = {r["key"] for r in matches}; seen = set(); examples = []
        for row in matches:
            scenario = meta[row["key"]]["scenario"]
            if scenario not in seen and len(examples) < 3:
                examples.append(row); seen.add(scenario)
        coverage.append({**old, "iso_date_episodes": len(date_keys), "iso_date_scenarios": len({meta[k]["scenario"] for k in date_keys}),
                         "examples": examples, "all_matches": matches})
    checked.verify_again(); budget.check()
    if snapshot() != json.loads(body)["source_sha256"]:
        raise ValueError("correction source/input changed")
    result = {"supersedes": "cross_round_v1/result.json normal_date_coverage text counts/examples only",
              "normal_date_coverage": coverage, "all_raw_distances_unchanged": True,
              "independent_distance_count": count, "max_independent_distance_error": max_error,
              "source_manifest_sha256": expected, "input_sha256": checked.hashes,
              "original_result_sha256": d.pf.digest((c.OUT/"result.json").read_bytes()),
              "elapsed_seconds": time.monotonic()-budget.start, "peak_rss_gib": d.pf.rss(), "access_guard": guard.summary()}
    d.ai.write_json(OUT/"result.json", result)
    print(json.dumps({"coverage": [{k:v for k,v in row.items() if k not in ("examples", "all_matches")} for row in coverage],
                      "distance_checks": count, "max_error": max_error, "seconds": result["elapsed_seconds"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--stage", choices=("freeze", "readout"), required=True)
    parser.add_argument("--source-sha256"); a = parser.parse_args(); torch.set_num_threads(1)
    if a.stage != "freeze" and not a.source_sha256: parser.error("source SHA required")
    main(a.stage, a.source_sha256)
