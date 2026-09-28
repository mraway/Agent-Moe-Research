"""A02-D: frozen normal-only routing-tail diagnosis; no new detector or thresholds."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

import numpy as np
import torch
from safetensors.torch import load as load_tensors
from tokenizers import Tokenizer

from research_v4 import codex_g_alg_a01_math as am
from research_v4 import codex_g_alg_a01_preflight as pf
from research_v4 import codex_g_alg_a01_io as ai

ROOT = pf.ROOT
OUT = pf.BASE / "alg_a02d_normal_tail_v1"
OLD = ai.OUT
SOURCE_SHA = "1415877e8860f4fe807bce4d31ba47affcd92c0c8210cfa17076a41838a5de55"
THRESHOLD_SHA = "f521b01d33a315cde32f9446e378b8470f6bc240e6d4c7d59dc8f125300a5f7b"
TOKENIZER = ROOT / "artifacts/hf_cache/models--openai--gpt-oss-20b/snapshots/6cee5e81ee83917806bbde320786a8fb61efebee/tokenizer.json"
SPEC = "docs/research_v4/codex_g_alg_a02d_spec_v1.md"
TEST = "tests/test_research_v4_codex_g_alg_a02_diagnostic.py"
TARGETS = {
    "g_dev|g-dev-087--benign_control#ep1": [53],
    "g_dev|g-dev-087--clean#ep1": [54],
    "g_dev|g-dev-121--benign_control#ep1": [46, 47],
}
CELLS = ("U_mean", "W_mean")


class Guard(pf.A01NormalGuard):
    def check_path(self, path):
        p = super().check_path(path)
        if OLD/"score" in p.parents or OLD/"posthoc_risk_v1" in p.parents:
            self.blocked_attempts += 1
            raise PermissionError("A02-D refuses mixed-arm scored inputs")
        return p


def source_snapshot():
    return {**pf.source_snapshot(), **{p: pf.digest((ROOT/p).read_bytes()) for p in (SPEC, TEST)},
            str(TOKENIZER.relative_to(ROOT)): pf.digest(TOKENIZER.read_bytes())}


def freeze():
    guard = Guard(ROOT); guard.install()
    snapshot = source_snapshot()
    OUT.mkdir(exist_ok=False)
    ai.write_json(OUT/"source_manifest.json", {"source_sha256": snapshot,
                  "utc": datetime.now(timezone.utc).isoformat(),
                  "head_provenance": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  "role": "G-dev normal-only adaptive diagnosis, not a detector freeze",
                  "budget_seconds": 600, "memory_gib": 2, "cpu_threads": 1})
    print(pf.digest((OUT/"source_manifest.json").read_bytes()), flush=True)


def verify_source(expected):
    body = (OUT/"source_manifest.json").read_bytes()
    if pf.digest(body) != expected:
        raise ValueError("A02-D source manifest changed")
    if json.loads(body)["source_sha256"] != source_snapshot():
        raise ValueError("A02-D scoped dependency changed")
    old = (OLD/"source_manifest.json").read_bytes()
    if pf.digest(old) != SOURCE_SHA:
        raise ValueError("A01 source manifest changed")
    for p, h in json.loads(old)["source_sha256"].items():
        if pf.digest((ROOT/p).read_bytes()) != h:
            raise ValueError(f"A01 dependency changed: {p}")


class Budget:
    def __init__(self):
        self.start = time.monotonic()

    def check(self):
        if time.monotonic()-self.start > 600 or pf.rss() > 2:
            raise RuntimeError("A02-D 600s/2GiB resource gate")


def select_controls(meta, grids, target, end, excluded_scenarios, count=3):
    """Uses structural metadata and endpoints ONLY; never routes, scores or text."""
    target_grid, target_meta = grids[target], meta[target]
    j = int(np.flatnonzero(target_grid["ends"] == end)[0])
    tag, ordinal = str(target_grid["tags"][j]), int(target_grid["ordinals"][j])
    minima = {}
    for key, row in sorted(meta.items()):
        if (row["scenario"] in excluded_scenarios or row["filter_pass"] is not True or
            any(row[f] != target_meta[f] for f in ("fold", "episode_index", "variant"))):
            continue
        grid = grids[key]
        for i in np.flatnonzero(grid["tags"] == tag):
            rank = (abs(int(grid["ordinals"][i])-ordinal), abs(int(grid["ends"][i])-end),
                    key, int(grid["ends"][i]))
            if row["scenario"] not in minima or rank < minima[row["scenario"]]:
                minima[row["scenario"]] = rank
    chosen = sorted(minima.values())[:count]
    return {"available_scenarios": len(minima), "required": count,
            "status": "supported" if len(chosen) == count else "insufficient",
            "controls": [{"key": r[2], "end": r[3], "ordinal_gap": r[0], "end_gap": r[1]} for r in chosen]}


def selection(meta, grids):
    excluded = {meta[k]["scenario"] for k in TARGETS}
    return [{"key": k, "end": e, **select_controls(meta, grids, k, e, excluded)}
            for k in sorted(TARGETS) for e in TARGETS[k]]


def queries_from_selection(rows):
    result = {}
    for row in rows:
        target = (row["key"], row["end"])
        result.setdefault(target, {"target": True, "control_for": []})
        for c in row["controls"]:
            key = (c["key"], c["end"])
            result.setdefault(key, {"target": False, "control_for": []})["control_for"].append(list(target))
    return result


def quantiles(values):
    a = np.asarray(values, dtype=np.float64)
    if not np.isfinite(a).all():
        raise ValueError("nonfinite diagnostic values")
    return {"n": len(a), "quantiles": dict(zip(("min", "p50", "p90", "p99", "max"),
             map(float, np.quantile(a, (0, .5, .9, .99, 1))))) if len(a) else {}}


def bucket_parameters(state, tag, ordinal, stream_index):
    standard = state["standardiser"]
    pooled = tag not in standard["stats"]
    b = standard["pooled"] if pooled else standard["stats"][tag]
    if b is None:
        raise ValueError("unsupported standardisation")
    index = min((stream_index if pooled else ordinal)//b["bucket_size"], b["cap"])
    return {"pooled_fallback": pooled, "bucket": index, "cap": b["cap"],
            "mu": b["mu"][index], "sd": b["sd"][index],
            "trace_count": b["trace_counts"][index], "window_count": b["window_counts"][index],
            "reused": index in b["reused_buckets"]}


def replay(state, stream, ci, name):
    params = [bucket_parameters(state, str(t), int(o), i)
              for i, (t, o) in enumerate(zip(stream["tags"], stream["ordinals"]))]
    z = np.asarray([(v-p["mu"])/p["sd"] for v, p in zip(stream["raw"][:, ci], params)])
    np.testing.assert_allclose(z, stream["z"][:, ci], rtol=0, atol=1e-10)
    maxima = np.sort(state["channels"][name]["path_maxima"])
    running = np.maximum.accumulate(z)
    p = (1+len(maxima)-np.searchsorted(maxima, running, side="left"))/(1+len(maxima))
    np.testing.assert_array_equal(p, stream["p"][:, ci])
    return params, running, maxima


def direct_neighbours(root, bank, ri, scenario):
    """Independent float64 enumeration, not the A01 blocked dot-product kernel."""
    delta = bank.mean_roots[ri].astype(np.float64)-np.asarray(root, dtype=np.float64)
    distances = .5*np.square(delta).sum(-1)
    choices = []
    for gi, group in enumerate(bank.scenarios):
        if group == scenario:
            continue
        rows = np.flatnonzero(bank.scenario_index == gi)
        best = int(rows[np.argmin(distances[rows])])
        choices.append((float(distances[best]), group, best))
    if len(choices) < 3:
        raise ValueError("fewer than three independent scenarios")
    choices.sort()
    return float(np.mean([v[0] for v in choices[:3]])), [v[2] for v in choices[:3]], distances


def local_radius(bank, row, ri, fit_streams, fit_keys, metadata):
    key, end = str(bank.keys[row]), int(bank.ends[row])
    if key not in fit_keys:
        raise ValueError("local radius donor outside fitting set")
    s = fit_streams[key]
    i = int(np.flatnonzero(s["ends"] == end)[0])
    rows = s["donor_rows"][i, ri]
    scenario = metadata[key]["scenario"]
    groups = [bank.scenarios[bank.scenario_index[r]] for r in rows]
    if len(set(groups)) != 3 or scenario in groups:
        raise ValueError("radius includes own scenario or duplicate scenarios")
    return {"value": float(s["raw"][i, ri]), "key": key, "end": end,
            "excluded_scenario": scenario, "neighbour_scenarios": groups}


def distance_contributions(q, donor):
    """Both [8,24,32] actual route probabilities; layer-averaged mean geometry."""
    if q.shape != (8, 24, 32) or donor.shape != q.shape:
        raise ValueError("expected complete route windows")
    return .5*np.square(np.sqrt(q.astype(np.float64).mean(0))-
                         np.sqrt(donor.astype(np.float64).mean(0)))/24


class Routes:
    def __init__(self, episodes, checked, inventory, tokenizer):
        self.episodes, self.checked, self.inventory, self.tokenizer = episodes, checked, inventory, tokenizer
        self.cache = {}

    def load(self, key):
        if key not in self.cache:
            ep = self.episodes[key]
            p = pf.CACHE/"topk_cache/g_dev"/(ep["stem"]+".safetensors")
            top = load_tensors(self.checked.read(p, self.inventory[str(p.relative_to(ROOT))]))
            np.testing.assert_array_equal(top["token_ids"].numpy(), ep["token_ids"])
            p = pf.CACHE/"logit_cache/g_dev"/(ep["stem"]+".logits.safetensors")
            logits = load_tensors(self.checked.read(p, self.inventory[str(p.relative_to(ROOT))]))["router_logits"].float().numpy()
            ids = top["top_k_ids"].numpy()
            self.cache[key] = ids, am.representations(ids, logits)
        return self.cache[key]

    def window(self, key, end):
        ids, ps = self.load(key)
        ep = self.episodes[key]
        am.valid_ends(np.array([end]), ep["tags"], ep["step_ids"])
        selected = ids[:, end-7:end+1].transpose(1, 0, 2)
        weights = np.take_along_axis(ps[1][end-7:end+1], selected, axis=-1)
        tokens = ep["token_ids"][end-7:end+1].tolist()
        decode = lambda a: self.tokenizer.decode(a, skip_special_tokens=False)
        return {"key": key, "end": end, "window_ids": tokens, "window_text": decode(tokens),
                "pieces": [decode([t]) for t in tokens],
                "prefix_text": decode(ep["token_ids"][max(0, end-23):end-7].tolist()),
                "suffix_text_posthoc_only": decode(ep["token_ids"][end+1:end+9].tolist()),
                "top_k_ids_t_l_k": selected.tolist(), "selected_weights_t_l_k": weights.tolist(),
                "tag": str(ep["tags"][end]), "step": int(ep["step_ids"][end])}


def normal_summary(meta, combined, folds, frozen):
    alarms = {}
    for ci, name in enumerate(("S", "CW", "TU", *am.CELLS)):
        keys = [k for k, s in combined.items() if np.any(s["p"][:, ci] <= .01)]
        alarms[name] = {"all": len(keys), "filtered": sum(meta[k]["filter_pass"] is True for k in keys),
                        "keys": keys, "first_alarm_condition": dict(Counter(
                            f'{meta[k]["fold"]}/{meta[k]["episode_index"]}/'+str(combined[k]["tags"][np.flatnonzero(combined[k]["p"][:, ci] <= .01)[0]]) for k in keys))}
    for name in CELLS:
        found = {k for k in alarms[name]["keys"] if meta[k]["filter_pass"] is True}
        if found != set(TARGETS):
            raise ValueError("preselected filtered extreme cohort changed")
    for key, ends in TARGETS.items():
        actual = sorted({int(combined[key]["ends"][np.flatnonzero(combined[key]["p"][:, ci] <= .01)[0]])
                         for ci in (3, 4)})
        if actual != ends:
            raise ValueError("preselected first-alarm endpoints changed")
    rows = []
    for fold, streams in folds.items():
        record = frozen["folds"][str(fold)]
        roles = {"fit": record["fit_keys"], "cal": record["cal_keys"],
                 "eval": [k for k in meta if meta[k]["fold"] == fold]}
        for role, keys in roles.items():
            for tag in ("analysis", "commentary", "final"):
                for ep in (0, 1):
                    group = [k for k in keys if meta[k]["episode_index"] == ep and tag in streams[k]["tags"]]
                    if not group:
                        continue
                    for ri, name in enumerate(CELLS):
                        raw = np.concatenate([streams[k]["raw"][streams[k]["tags"] == tag, ri] for k in group])
                        z = np.concatenate([streams[k]["z"][streams[k]["tags"] == tag, ri] for k in group])
                        rows.append({"fold": fold, "role": role, "tag": tag, "episode_index": ep,
                                     "cell": name, "episodes": len(group), "scenarios": len({meta[k]["scenario"] for k in group}),
                                     "raw": quantiles(raw), "z": quantiles(z),
                                     "channel_episode_max_raw": quantiles([streams[k]["raw"][streams[k]["tags"] == tag, ri].max() for k in group]),
                                     "channel_episode_max_z": quantiles([streams[k]["z"][streams[k]["tags"] == tag, ri].max() for k in group])})
    return {"all_n": len(meta), "filtered_n": sum(r["filter_pass"] is True for r in meta.values()),
            "nominal_01_alarms": alarms, "role_condition_distributions": rows}


def load_inputs(checked):
    frozen = checked.json(OLD/"calibrate/threshold_manifest.json", THRESHOLD_SHA)
    outputs = frozen["normal_output_sha256"]
    get = lambda p: checked.read(OLD/"calibrate"/p, outputs[p])
    meta = json.loads(get("episode_metadata.json"))
    combined = ai.unpack(ai.npz_bytes(get("look_streams.npz")))
    folds = {f: ai.unpack(ai.npz_bytes(get(f"fold{f}/look_streams.npz"))) for f in range(3)}
    if len(meta) != 408 or any(r["variant"] not in pf.NORMALS for r in meta.values()):
        raise ValueError("normal-only cohort mismatch")
    return frozen, meta, combined, folds


def compute_cards(chosen, meta, folds, frozen, routes, checked, budget):
    cards, audit = [], {"max_query_score_error": 0., "max_radius_error": 0., "max_contribution_error": 0., "queries": 0}
    queries = queries_from_selection(chosen)
    conditions = sorted({(meta[k]["fold"], str(folds[meta[k]["fold"]][k]["tags"][np.flatnonzero(folds[meta[k]["fold"]][k]["ends"] == e)[0]]), meta[k]["episode_index"]) for k, e in queries})
    for fold, tag, ep in conditions:
        info = frozen["folds"][str(fold)]
        b = info["banks"][f"{tag}/ep{ep}"]
        bank = am.Bank.restore(ai.npz_bytes(checked.read(OLD/b["path"], b["sha256"])))
        for (key, end), role in sorted(queries.items()):
            if (meta[key]["fold"], meta[key]["episode_index"]) != (fold, ep):
                continue
            s = folds[fold][key]; i = int(np.flatnonzero(s["ends"] == end)[0])
            if str(s["tags"][i]) != tag:
                continue
            budget.check()
            if key in info["fit_keys"] or meta[key]["scenario"] in bank.scenarios:
                raise ValueError("query leaked into fitting bank")
            card = {**routes.window(key, end), **role, "fold": fold, "episode_index": ep,
                    "scenario": meta[key]["scenario"], "variant": meta[key]["variant"],
                    "ordinal": int(s["ordinals"][i]), "cells": {}}
            _, ps = routes.load(key)
            for ri, name in enumerate(CELLS):
                state = frozen["calibrations"][str(fold)][name]
                params, running, maxima = replay(state, s, ri, name)
                root = am.features(ps[ri], np.array([end]))[1][0]
                exact, direct_rows, distances = direct_neighbours(root, bank, ri, meta[key]["scenario"])
                err = abs(exact-s["raw"][i, ri]); audit["max_query_score_error"] = max(audit["max_query_score_error"], err)
                if err > am.DISTANCE_TOLERANCE:
                    raise ValueError("full-bank exact query audit failed")
                rows = s["donor_rows"][i, ri]
                groups = [bank.scenarios[bank.scenario_index[r]] for r in rows]
                if len(set(groups)) != 3 or meta[key]["scenario"] in groups:
                    raise ValueError("query donors not scenario isolated")
                donors, contributions = [], []
                for row, saved_d in zip(rows, s["donor_distances"][i, ri]):
                    dk, de = str(bank.keys[row]), int(bank.ends[row])
                    radius = local_radius(bank, row, ri, folds[fold], info["fit_keys"], meta)
                    dexa, _, _ = direct_neighbours(bank.mean_roots[ri][row], bank, ri, meta[dk]["scenario"])
                    radius_error = abs(dexa-radius["value"])
                    audit["max_radius_error"] = max(audit["max_radius_error"], radius_error)
                    _, dp = routes.load(dk)
                    terms = distance_contributions(ps[ri][end-7:end+1], dp[ri][de-7:de+1])
                    term_error = abs(float(terms.sum())-saved_d)
                    audit["max_contribution_error"] = max(audit["max_contribution_error"], term_error)
                    if max(radius_error, term_error, abs(distances[row]-saved_d)) > am.DISTANCE_TOLERANCE:
                        raise ValueError("actual route/radius distance audit failed")
                    donors.append({"bank_row": int(row), "distance": float(saved_d), "radius": radius,
                                   "window": routes.window(dk, de), "layer_h2": terms.sum(-1).tolist()})
                    contributions.append(terms)
                raw = float(s["raw"][i, ri]); scale = float(np.mean([d["radius"]["value"] for d in donors]))
                np.testing.assert_allclose(raw, np.mean([d["distance"] for d in donors]), rtol=0, atol=0)
                card["cells"][name] = {"raw": raw, "z": float(s["z"][i, ri]), "p": float(s["p"][i, ri]),
                    "running_max_z": float(running[i]), "reference_max_z": float(maxima.max()),
                    "reference_ge_running": int(np.sum(maxima >= running[i])), "n_reference": len(maxima),
                    "bucket": params[i], "bank_scenarios": len(bank.scenarios), "bank_windows": len(bank.ends),
                    "local_scale": scale, "raw_over_local_scale": raw/max(scale, 1e-6), "scale_floored": scale < 1e-6,
                    "donors": donors, "mean_coordinate_h2_l_e": np.mean(contributions, axis=0).tolist(),
                    "direct_nearest_rows": direct_rows, "direct_score": exact}
            cards.append(card); audit["queries"] += 1
            routes.cache.clear()
        del bank
    if len(cards) != len(queries):
        raise ValueError("missing selected cards")
    return cards, audit


def run(stage, expected):
    guard = Guard(ROOT); guard.install(); budget = Budget(); verify_source(expected)
    checked = pf.CheckedInputs(guard)
    frozen, meta, combined, folds = load_inputs(checked)
    selected = selection(meta, combined)
    if stage == "run":
        directory = OUT/"run"; directory.mkdir(exist_ok=False)
        ai.write_json(directory/"selection.json", selected)
    else:
        directory = OUT/"audit"; directory.mkdir(exist_ok=False)
        log = json.loads((OUT/"run/run_manifest.json").read_text())
        if log["status"] != "completed":
            raise ValueError("cannot audit incomplete run")
        for p, h in log["output_sha256"].items():
            checked.read(OUT/"run"/p, h)
        if selected != json.loads((OUT/"run/selection.json").read_text()):
            raise ValueError("structural selection replay failed")
    ai.write_json(directory/"started.json", {"source_manifest_sha256": expected, "stage": stage})
    try:
        summary = normal_summary(meta, combined, folds, frozen)
        old_meta, grid, episodes, inventory = pf.normal_inputs(checked)
        if old_meta != meta:
            raise ValueError("M7 normal metadata changed")
        for key in meta:
            for field in ("ends", "tags", "ordinals"):
                np.testing.assert_array_equal(grid[key][field], combined[key][field])
        body = TOKENIZER.read_bytes(); tokenizer = Tokenizer.from_str(body.decode())
        routes = Routes(episodes, checked, inventory, tokenizer)
        cards, audits = compute_cards(selected, meta, folds, frozen, routes, checked, budget)
        if stage == "run":
            ai.write_json(directory/"normal_summary.json", summary)
            ai.write_json(directory/"case_cards.json", cards)
            ai.write_json(directory/"numeric_checks.json", audits)
        else:
            for filename, value in (("normal_summary.json", summary), ("case_cards.json", cards), ("numeric_checks.json", audits)):
                if json.loads((OUT/"run"/filename).read_text()) != value:
                    raise ValueError(f"diagnostic replay changed: {filename}")
            ai.write_json(directory/"checks.json", {"status": "completed", **audits,
                          "structural_selection_replayed": True, "all_card_values_replayed": True,
                          "independent_numerics": "float64 full-bank enumeration vs frozen A01 float32 blocked scoring"})
        checked.verify_again(); verify_source(expected); budget.check()
        log = {"status": "completed", "source_manifest_sha256": expected, "threshold_manifest_sha256": THRESHOLD_SHA,
               "input_sha256": checked.hashes, "output_sha256": {p.name: pf.digest(p.read_bytes()) for p in sorted(directory.iterdir()) if p.is_file()},
               "elapsed_seconds": time.monotonic()-budget.start, "peak_rss_gib": pf.rss(), "access_guard": guard.summary()}
        ai.write_json(directory/"run_manifest.json", log)
        print(json.dumps({"stage": stage, "status": "completed", **audits,
                          "seconds": log["elapsed_seconds"], "peak_rss_gib": log["peak_rss_gib"]}), flush=True)
    except Exception as exc:
        ai.write_json(directory/"failure.json", {"type": type(exc).__name__, "message": str(exc),
                      "source_manifest_sha256": expected, "input_sha256": checked.hashes, "access_guard": guard.summary()})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("freeze", "run", "audit"), required=True)
    parser.add_argument("--source-sha256")
    args = parser.parse_args(); torch.set_num_threads(1)
    if args.stage == "freeze":
        freeze()
    elif not args.source_sha256:
        parser.error("source SHA required")
    else:
        run(args.stage, args.source_sha256)
