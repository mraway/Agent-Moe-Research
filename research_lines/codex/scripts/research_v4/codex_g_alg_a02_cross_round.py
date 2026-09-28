"""Declared posthoc A02-D normal-bank support diagnostic; never recalibrates a detector."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
import time

import numpy as np
import torch
from tokenizers import Tokenizer

from research_v4 import codex_g_alg_a02_diagnostic as d

OUT = d.OUT/"cross_round_v1"
DIAG_SOURCE_SHA = "53c1039513981554d1d898978231b9de22b47a09ad589d99ba16bc3644437bcb"
EXTRA = ("docs/research_v4/codex_g_alg_a02d_cross_round_spec_v1.md",
         "tests/test_research_v4_codex_g_alg_a02_cross_round.py")


def snapshot():
    paths = (*EXTRA, str((d.OUT/"run/run_manifest.json").relative_to(d.ROOT)))
    return {**d.source_snapshot(), **{p: d.pf.digest((d.ROOT/p).read_bytes()) for p in paths}}


def neighbours(root, banks, ri, exclude):
    """Scenario minima span ALL included rounds, not one vote per round/window."""
    minima = {}
    for label, bank in sorted(banks.items()):
        distances = .5*np.square(bank.mean_roots[ri].astype(np.float64)-root.astype(np.float64)).sum(-1)
        for gi, scenario in enumerate(bank.scenarios):
            if scenario == exclude:
                continue
            rows = np.flatnonzero(bank.scenario_index == gi)
            row = int(rows[np.argmin(distances[rows])])
            rank = (float(distances[row]), scenario, str(bank.keys[row]), int(bank.ends[row]), label, row)
            if scenario not in minima or rank < minima[scenario]:
                minima[scenario] = rank
    if len(minima) < 3:
        raise ValueError("insufficient scenario support")
    selected = sorted(minima.values())[:3]
    return float(np.mean([r[0] for r in selected])), selected


def date_segments(tokenizer, episodes, keys):
    found = []
    for key in sorted(keys):
        ep = episodes[key]
        ix = np.flatnonzero(ep["tags"] == "analysis")
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


def main(stage, expected):
    guard = d.Guard(d.ROOT); guard.install()
    if stage == "freeze":
        sources = snapshot(); OUT.mkdir(exist_ok=False)
        d.ai.write_json(OUT/"source_manifest.json", {"source_sha256": sources,
                        "utc": datetime.now(timezone.utc).isoformat(), "role": "declared posthoc normal-only diagnostic"})
        print(d.pf.digest((OUT/"source_manifest.json").read_bytes()), flush=True)
        return
    start = time.monotonic(); budget = d.Budget()
    body = (OUT/"source_manifest.json").read_bytes()
    if d.pf.digest(body) != expected or json.loads(body)["source_sha256"] != snapshot():
        raise ValueError("posthoc source freeze mismatch")
    old = (d.OUT/"source_manifest.json").read_bytes()
    if d.pf.digest(old) != DIAG_SOURCE_SHA:
        raise ValueError("A02-D original source manifest changed")
    for p, h in json.loads(old)["source_sha256"].items():
        if d.pf.digest((d.ROOT/p).read_bytes()) != h:
            raise ValueError("A02-D original source changed")
    checked = d.pf.CheckedInputs(guard)
    old_log = json.loads((d.OUT/"run/run_manifest.json").read_text())
    old_cards = checked.json(d.OUT/"run/case_cards.json", old_log["output_sha256"]["case_cards.json"])
    frozen, meta, _, _ = d.load_inputs(checked)
    _, _, episodes, inventory = d.pf.normal_inputs(checked)
    tokenizer = Tokenizer.from_str(d.TOKENIZER.read_text())
    routes = d.Routes(episodes, checked, inventory, tokenizer)
    result, coverage = [], []
    max_replay_error = 0.
    for fold in sorted({c["fold"] for c in old_cards}):
        banks = {}
        for ep in (0, 1):
            info = frozen["folds"][str(fold)]["banks"][f"analysis/ep{ep}"]
            banks[str(ep)] = d.am.Bank.restore(d.ai.npz_bytes(checked.read(d.OLD/info["path"], info["sha256"])))
            keys = set(map(str, banks[str(ep)].keys))
            matches = date_segments(tokenizer, episodes, keys)
            date_keys = {m["key"] for m in matches}
            examples, seen = [], set()
            for row in matches:
                scenario = meta[row["key"]]["scenario"]
                if scenario not in seen and len(examples) < 3:
                    examples.append(row); seen.add(scenario)
            coverage.append({"fold": fold, "episode_index": ep, "bank_episodes": len(keys),
                             "bank_scenarios": len(banks[str(ep)].scenarios), "bank_windows": len(banks[str(ep)].ends),
                             "iso_date_episodes": len(date_keys), "iso_date_scenarios": len({meta[k]["scenario"] for k in date_keys}),
                             "examples": examples, "all_matches": matches})
        for old_card in old_cards:
            if old_card["fold"] != fold:
                continue
            budget.check(); key, end = old_card["key"], old_card["end"]
            if old_card["tag"] != "analysis" or old_card["episode_index"] != 1:
                raise ValueError("unexpected original diagnostic query")
            _, ps = routes.load(key)
            row = {"key": key, "end": end, "target": old_card["target"], "window_text": old_card["window_text"], "cells": {}}
            for ri, name in enumerate(d.CELLS):
                root = d.am.features(ps[ri], np.array([end]))[1][0]
                scopes = {}
                for scope, chosen in (("ep1_original", {"1": banks["1"]}), ("ep0_other_round", {"0": banks["0"]}), ("pooled_rounds", banks)):
                    score, donors = neighbours(root, chosen, ri, meta[key]["scenario"])
                    scopes[scope] = {"raw": score, "donors": [{"distance": r[0], "scenario": r[1], "bank_round": r[4],
                                         "window": routes.window(r[2], r[3])} for r in donors]}
                    assert len({r[1] for r in donors}) == 3 and all(r[1] != meta[key]["scenario"] for r in donors)
                error = abs(scopes["ep1_original"]["raw"]-old_card["cells"][name]["raw"])
                max_replay_error = max(max_replay_error, error)
                if error > d.am.DISTANCE_TOLERANCE:
                    raise ValueError("original-bank replay failed")
                if scopes["pooled_rounds"]["raw"] > min(scopes[s]["raw"] for s in ("ep1_original", "ep0_other_round"))+1e-12:
                    raise ValueError("pooled minima monotonicity failed")
                row["cells"][name] = scopes
            result.append(row); routes.cache.clear()
        del banks
    checked.verify_again(); budget.check()
    if snapshot() != json.loads(body)["source_sha256"]:
        raise ValueError("source changed during diagnosis")
    payload = {"role": "posthoc diagnostic; raw distances only, no new calibrated FAR/recall", "queries": result,
               "normal_date_coverage": coverage, "max_original_score_error": max_replay_error,
               "source_manifest_sha256": expected, "input_sha256": checked.hashes,
               "elapsed_seconds": time.monotonic()-start, "peak_rss_gib": d.pf.rss(), "access_guard": guard.summary()}
    d.ai.write_json(OUT/"result.json", payload)
    print(json.dumps({"queries": len(result), "max_original_score_error": max_replay_error,
                      "seconds": payload["elapsed_seconds"], "peak_rss_gib": payload["peak_rss_gib"],
                      "coverage": [{k:v for k,v in c.items() if k not in ("examples", "all_matches")} for c in coverage]}), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__); p.add_argument("--stage", choices=("freeze", "readout"), required=True)
    p.add_argument("--source-sha256"); a = p.parse_args(); torch.set_num_threads(1)
    if a.stage != "freeze" and not a.source_sha256:
        p.error("source SHA required")
    main(a.stage, a.source_sha256)
