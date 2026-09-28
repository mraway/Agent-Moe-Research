"""A03-D frozen-score timing and normal-tail diagnosis, never a detector fit."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import time
from unittest.mock import patch
import numpy as np
import torch
from tokenizers import Tokenizer
from research_v2 import trm3_g
from research_v4 import codex_g_alg_a01_preflight as pf, codex_g_alg_a01_io as ai
from research_v4 import codex_g_alg_a01_math as am, codex_g_alg_a01_eval as ae
from research_v4 import codex_g_alg_a02_diagnostic as d2
from research_v4 import codex_g_alg_a03_math as math, codex_g_alg_a03_eval as ev
from research_v4 import codex_g_alg_a03_run_v1_1 as old
from research_v4 import codex_g_m7_math as m7

ROOT, OLD = pf.ROOT, old.OUT
OUT = pf.BASE / "alg_a03d_timing_tail_v1"
SOURCE_SHA = "90f4cbc2da6697fb622db32f000a59aa6f05794e2417d8d62af575e99e7a7a3c"
THRESHOLD_SHA = "01f1fd1d56c3c13787621b67582dfffb27de2157c7d1a8129475d2648325b45c"
SCORE_SHA = "3e7b9c1943ebffb30602222907b0f089e78854c540247bd0e02590bd4ed77c45"
RESULT_SHA = "bb1d2eaa9a593e9190fdd036b837e673fb15c34a34d87ac34ccf4caa42de4c80"
AUDIT_SHA = "c22fcd6fd47ab926166eaa5e99c56f717aa8db3ef1faacd305b9077f134f02da"
M13 = pf.BASE / "mechanism_m13_injected_controls_v1/audit/checks.json"
M13_SHA = "63d1837139e462fb8e04d59faf41a5b3f7b71614f7a7b5f1677fb6bf50c849b6"
EXTRA = ("docs/research_v4/codex_g_alg_a03d_spec_v1.md",
         "scripts/research_v4/codex_g_alg_a03d_diagnostic.py",
         "tests/test_research_v4_codex_g_alg_a03d.py",
         "docs/research_v4/codex_g_mech_m13_report.md")
TIMING = ("S", "TU", "W_mean", "W_diag", "W_block", "W_full")
TAIL = ("W_diag", "W_block", "W_full")
DELTAS = (-64, -32, -16, -8, -1, 0, 8, 16)
PAIRS = (("W_full", "W_diag"), ("W_block", "W_diag"), ("W_full", "W_block"))


def parent_sources(checked):
    frozen = checked.json(OLD/"execution_source_manifest.json", SOURCE_SHA)
    for p, h in frozen["source_sha256"].items(): checked.read(ROOT/p, h)
    return frozen["source_sha256"]


def snapshot(checked):
    return {**parent_sources(checked), **pf.source_snapshot(),
            **{p: pf.digest((ROOT/p).read_bytes()) for p in EXTRA},
            str(d2.TOKENIZER.relative_to(ROOT)): pf.digest(d2.TOKENIZER.read_bytes())}


def freeze():
    guard = old.Guard(False); guard.install(); checked = pf.CheckedInputs(guard)
    sources = snapshot(checked); checked.read(M13, M13_SHA); checked.read(OLD/"audit/checks.json", AUDIT_SHA)
    OUT.mkdir(exist_ok=False)
    ai.write_json(OUT/"source_manifest.json", {"source_sha256": sources, "m13_audit_sha256": M13_SHA,
                  "parent_source_sha256": SOURCE_SHA, "created_utc": datetime.now(timezone.utc).isoformat(),
                  "role": "adaptive post-hoc G-dev diagnosis; fixed old scores, no detector selection",
                  "budget_seconds": 600, "cpu_threads": 1, "memory_gib": 2})
    print(pf.digest((OUT/"source_manifest.json").read_bytes()), flush=True)


def verify_source(sha, checked):
    frozen = checked.json(OUT/"source_manifest.json", sha)
    for p, h in frozen["source_sha256"].items(): checked.read(ROOT/p, h)
    if not set(pf.source_snapshot()).issubset(frozen["source_sha256"]): raise ValueError("unfrozen dependency")
    parent_sources(checked); checked.read(M13, M13_SHA); checked.read(OLD/"audit/checks.json", AUDIT_SHA)
    return frozen


def prohibit(*args, **kwargs): raise AssertionError("A03-D cannot fit or calibrate")


def early_hit(row, alarm, delta):
    return bool(alarm is not None and row["e"] is not None and row["e"] <= alarm <= row["x"]+delta)


def first_index(stream, col, alpha):
    ix = np.flatnonzero(stream["p"][:, col] <= alpha)
    return int(ix[0]) if len(ix) else None


def event(stream, col, alpha):
    i = first_index(stream, col, alpha)
    if i is None: return None
    peak = int(np.argmax(stream["z"][:i+1, col]))
    return {"index": i, "end": int(stream["ends"][i]), "tag": str(stream["tags"][i]),
            "ordinal": int(stream["ordinals"][i]), "raw": float(stream["raw"][i, col]),
            "z": float(stream["z"][i, col]), "p": float(stream["p"][i, col]),
            "peak_index": peak, "peak_end": int(stream["ends"][peak]),
            "peak_tag": str(stream["tags"][peak]), "peak_z": float(stream["z"][peak, col])}


def quantiles(values): return d2.quantiles(values)


def text_card(ep, end, tokenizer):
    tokens = ep["token_ids"]
    decode = lambda a: tokenizer.decode(a.tolist(), skip_special_tokens=False)
    return {"end": int(end), "tag": str(ep["tags"][end]), "step": int(ep["step_ids"][end]),
            "window_start": max(0, end-7), "window_ids": tokens[max(0, end-7):end+1].tolist(),
            "window_text": decode(tokens[max(0, end-7):end+1]),
            "prefix_start": max(0, end-71), "prefix_text": decode(tokens[max(0, end-71):max(0, end-7)]),
            "suffix_text_POSTHOC_ONLY": decode(tokens[end+1:end+33])}


def timing_analysis(meta, streams, episodes, tokenizer, alarms):
    positives = sorted(k for k, r in meta.items() if r["variant"] == "attack" and r["x"] is not None)
    if len(positives) != 126: raise ValueError("changed X denominator")
    hits, rows = {}, {}
    for name in TIMING:
        hits[name], rows[name] = {}, {}
        for d in DELTAS:
            h = {k: early_hit(meta[k], alarms[name][k], d) for k in positives}
            no_look = [k for k in positives if not np.any((streams[k]["ends"] >= meta[k]["e"]) &
                                                         (streams[k]["ends"] <= meta[k]["x"]+d))]
            hits[name][str(d)] = h
            rows[name][str(d)] = {"count": sum(h.values()), "n": len(h), "no_eligible_look": no_look}
    comparisons = {f"X{d:+}/{a}_minus_{b}": ae.paired(meta, hits[a][str(d)], hits[b][str(d)])
                   for d in (-16, -1) for a, b in (("W_full", "S"), ("W_full", "W_mean"), ("W_full", "W_diag"))}
    per_episode, cards, common = {}, {}, {}
    for k in positives:
        r, ep = meta[k], episodes[k]
        events = {n: event(streams[k], ev.CELLS.index(n), alarms["_alpha"][n]) for n in TIMING}
        per_episode[k] = {"e": r["e"], "x": r["x"], "e_tag": str(ep["tags"][r["e"]]),
                          "x_tag": str(ep["tags"][r["x"]]), "family": r["family"], "fold": r["fold"],
                          "events": events}
        if hits["W_full"]["-1"][k]:
            a = alarms["W_full"][k]
            cards[k] = {"e": r["e"], "x": r["x"], "alarm_minus_x": a-r["x"], "alarm_minus_e": a-r["e"],
                        "alarm": text_card(ep, a, tokenizer),
                        "e_context": text_card(ep, r["e"], tokenizer), "x_context": text_card(ep, r["x"], tokenizer)}
    for b in TIMING[:-1]:
        keys = [k for k in positives if hits["W_full"]["16"][k] and hits[b]["16"][k]]
        common[b] = {"keys": keys, "alarm_full_minus_baseline": quantiles([alarms["W_full"][k]-alarms[b][k] for k in keys])}
    return {"deadlines": rows, "comparisons_posthoc": comparisons, "common_hit_timing": common,
            "early_channels": dict(Counter(c["alarm"]["tag"] for c in cards.values())),
            "early_x_channels": dict(Counter(per_episode[k]["x_tag"] for k in cards)),
            "per_episode": per_episode}, cards


def signed_contributions(delta, model, rep=1):
    """Signed coordinate decomposition, not causal attribution or nonnegative weights."""
    d, w = len(delta), model.layer_width
    out = {"diag": delta**2*model.diag_whitener[rep]**2/d}
    block = np.empty(d)
    for layer, whiten in enumerate(model.block_whitener[rep]):
        lo = layer*w; v = delta[lo:lo+w]
        block[lo:lo+w] = v*((v@whiten)@whiten.T)/d
    out["block"] = block
    whiten = model.full_whitener[rep]
    out["full"] = delta*((delta@whiten)@whiten.T)/d
    return out


def normal_analysis(meta, streams, frozen, checked, budget):
    normals = sorted(k for k, r in meta.items() if r["variant"] in pf.NORMALS)
    grid = {}; tails = []; distributions = {}; replay_count = 0
    for name in TAIL:
        col = ev.CELLS.index(name); grid[name] = {}
        for alpha in (1/105, 1/96, 1/95):
            keys = [k for k in normals if first_index(streams[k], col, alpha) is not None]
            grid[name][str(alpha)] = {"all": len(keys), "filtered": sum(meta[k]["filter_pass"] is True for k in keys),
                                     "by_fold": {str(f): [k for k in keys if meta[k]["fold"] == f] for f in range(3)}}
        for k in normals:
            fold = str(meta[k]["fold"]); state = frozen["calibrations"][fold][name]
            params, running, maxima = d2.replay(state, streams[k], col, name)
            replay_count += len(running)
            alpha = 1/(len(maxima)+1); e = event(streams[k], col, alpha)
            if e:
                tails.append({"key": k, "name": name, "fold": int(fold), "filter_pass": meta[k]["filter_pass"],
                              "alpha": alpha, "event": e, "parameters": params[e["index"]],
                              "peak_parameters": params[e["peak_index"]], "cal_max_z": float(maxima[-1]),
                              "reference_count": len(maxima), "cal_exceed_count": int(np.count_nonzero(maxima >= e["peak_z"]))})
    for f in range(3):
        budget.check(); record = frozen["folds"][str(f)]
        data = ai.unpack(ai.npz_bytes(checked.read(OLD/f"calibrate/fold{f}/look_streams.npz", record["streams_sha256"])))
        roles = {"fit_resubstitution": record["fit_keys"], "cal_held_out": record["cal_keys"],
                 "eval_filtered": [k for k in normals if meta[k]["fold"] == f and meta[k]["filter_pass"] is True],
                 "eval_quality_failed": [k for k in normals if meta[k]["fold"] == f and meta[k]["filter_pass"] is not True]}
        distributions[str(f)] = {}
        for name in TAIL:
            col = math.CELLS.index(name); entry = {}
            for role, keys in roles.items():
                entry[role] = {}
                for tag in ("all", "analysis", "commentary", "final"):
                    maxima = {"raw": [], "z": []}
                    for k in keys:
                        s = data[k]; mask = np.ones(len(s["ends"]), bool) if tag == "all" else s["tags"] == tag
                        if not np.any(mask): continue
                        for field in maxima: maxima[field].append(float(s[field][mask, col].max()))
                    entry[role][tag] = {field: quantiles(v) for field, v in maxima.items()}
            cal = sorted(float(data[k]["z"][:, col].max()) for k in record["cal_keys"] if len(data[k]["ends"]))
            np.testing.assert_array_equal(cal, sorted(frozen["calibrations"][str(f)][name]["channels"][name]["path_maxima"]))
            distributions[str(f)][name] = entry
        del data
    return {"grid": grid, "floor_tail_events": tails, "role_distributions": distributions,
            "normal_z_p_replayed_looks": replay_count}


def select_queries(meta, streams, timing, normal, result, alarms):
    q = defaultdict(list); selected = set()
    for k, row in sorted(timing["per_episode"].items()):
        a = alarms["W_full"][k]
        if not early_hit(meta[k], a, -1): continue
        lead = row["x"]-a; group = (row["events"]["W_full"]["tag"], "1_7" if lead < 8 else "8_31" if lead < 32 else "32_plus")
        if group not in selected: q[k, a].append("early/"+"/".join(group)); selected.add(group)
    for k, r in sorted(meta.items()):
        if r["variant"] in pf.NORMALS and alarms["W_full"][k] is not None:
            q[k, alarms["W_full"][k]].append("normal/main_W_full")
    for r in normal["floor_tail_events"]:
        if r["name"] == "W_full": q[r["key"], r["event"]["end"]].append("normal/floor_W_full")
    contrasts = {}
    block = result["readings"]["matched_all"]["0.05"]
    for a, b in PAIRS:
        only_a = sorted(k for k, h in block[a]["hits"].items() if h and not block[b]["hits"][k])
        only_b = sorted(k for k, h in block[b]["hits"].items() if h and not block[a]["hits"][k])
        contrasts[f"{a}_minus_{b}"] = {"only_a": only_a, "only_b": only_b}
        for side, name, keys in (("a", a, only_a), ("b", b, only_b)):
            for k in keys[:2]: q[k, alarms[name][k]].append(f"contrast/{a}_minus_{b}/only_{side}")
    controls = []
    for (k, end), reasons in list(sorted(q.items())):
        if meta[k]["variant"] not in pf.NORMALS: continue
        selection = d2.select_controls(meta, streams, k, end, {meta[k]["scenario"]}, count=2)
        controls.append({"key": k, "end": end, **selection})
        for r in selection["controls"]: q[r["key"], r["end"]].append(f"normal_control_for/{k}/{end}")
    return [{"key": k, "end": e, "reasons": reasons} for (k, e), reasons in sorted(q.items())], controls, contrasts


def deep_cards(queries, meta, streams, episodes, inventory, checked, frozen, tokenizer, budget):
    routes = d2.Routes(episodes, checked, inventory, tokenizer); models = {}; cards = []; maxerr = 0.
    for query in queries:
        budget.check(); k, end = query["key"], query["end"]
        window = routes.window(k, end); ids, ps = routes.load(k)
        fold, tag = str(meta[k]["fold"]), window["tag"]; model_key = (fold, tag)
        if model_key not in models:
            info = frozen["folds"][fold]["models"][tag]
            models[model_key] = math.Model.restore(ai.npz_bytes(checked.read(OLD/info["path"], info["sha256"])))
        model = models[model_key]; roots = [am.features(p, np.asarray([end]))[1] for p in ps]
        raw = math.score(roots, model)[0]; stream = streams[k]; index = int(np.flatnonzero(stream["ends"] == end)[0])
        stored = stream["raw"][index, -6:]
        np.testing.assert_allclose(raw, stored, rtol=5e-6, atol=2e-6)
        maxerr = max(maxerr, float(np.abs(raw-stored).max()))
        delta = roots[1][0].astype(np.float64)-model.mu[1]; contribution = signed_contributions(delta, model)
        for name, values in contribution.items():
            np.testing.assert_allclose(values.sum(), raw[math.CELLS.index("W_"+name)], rtol=1e-10, atol=1e-10)
        cards.append({**query, "window": window, "text": text_card(episodes[k], end, tokenizer),
                      "metadata": {f: meta[k][f] for f in ("fold", "scenario", "episode_index", "variant", "filter_pass", "family", "e", "x")},
                      "raw_13": dict(zip(ev.CELLS, stream["raw"][index].tolist())),
                      "z_13": dict(zip(ev.CELLS, stream["z"][index].tolist())), "p_13": dict(zip(ev.CELLS, stream["p"][index].tolist())),
                      "reconstructed_raw_6": dict(zip(math.CELLS, raw.tolist())), "W_delta": delta.tolist(),
                      "W_signed_coordinate_contributions": {n: v.tolist() for n, v in contribution.items()},
                      "W_signed_layer_contributions": {n: v.reshape(24, 32).sum(1).tolist() for n, v in contribution.items()}})
        routes.cache.clear()
    return cards, maxerr


def load_parent(checked):
    frozen = checked.json(OLD/"calibrate/threshold_manifest.json", THRESHOLD_SHA)
    log = checked.json(OLD/"score/run_manifest.json", SCORE_SHA)
    if log["status"] != "completed": raise ValueError("parent incomplete")
    get = lambda n: checked.json(OLD/"score"/n, log["output_sha256"][n])
    meta, ledger = get("episode_metadata.json"), get("alarm_ledger.json")
    result = checked.json(OLD/"score/result.json", RESULT_SHA)
    data = ai.npz_bytes(checked.read(OLD/"score/look_streams.npz", log["output_sha256"]["look_streams.npz"]))
    if list(data["columns"]) != list(ev.CELLS): raise ValueError("changed column map")
    return frozen, meta, ai.unpack(data), ledger, result


def diagnose(checked, folder, budget, sources):
    frozen, meta, streams, ledger, result = load_parent(checked)
    source_meta, grid, episodes, inventory, _ = ai.score_inputs(checked)
    if source_meta != meta: raise ValueError("metadata drift")
    for k in meta:
        for f in ("ends", "tags", "ordinals"): np.testing.assert_array_equal(streams[k][f], grid[k][f])
    del grid
    tokenizer = Tokenizer.from_str(checked.read(d2.TOKENIZER, sources[str(d2.TOKENIZER.relative_to(ROOT))]).decode())
    alarms = dict(ledger["matched_all/0.05"])
    alarms["_alpha"] = {n: frozen["workpoints"]["matched_all"]["0.05"][n]["alpha"] for n in ev.CELLS}
    for n in ev.CELLS:
        actual = {k: m7.first(s["ends"], s["p"][:, ev.CELLS.index(n)], alarms["_alpha"][n]) for k, s in streams.items()}
        if actual != alarms[n]: raise ValueError("parent first-alarm replay failed")
        if m7.summarize(meta, actual)["hits"] != result["readings"]["matched_all"]["0.05"][n]["hits"]:
            raise ValueError("parent endpoint changed")
    timing, text = timing_analysis(meta, streams, episodes, tokenizer, alarms)
    print(json.dumps({"timing": timing["early_channels"], "early_cards": len(text), "rss_gib": pf.rss()}), flush=True)
    normal = normal_analysis(meta, streams, frozen, checked, budget)
    queries, controls, contrasts = select_queries(meta, streams, timing, normal, result, alarms)
    ai.write_json(folder/"selection.json", {"queries": queries, "controls": controls, "contrasts": contrasts})
    cards, rawerr = deep_cards(queries, meta, streams, episodes, inventory, checked, frozen, tokenizer, budget)
    ai.write_json(folder/"early_text_cards.json", text); ai.write_json(folder/"routing_cards.json", cards)
    output = {"timing": timing, "normal": normal, "contrasts": contrasts, "deep_card_count": len(cards),
              "raw_reconstruction_max_error": rawerr, "parent_13_first_alarms_and_hits_exact": True,
              "role": "adaptive fixed-detector post-hoc diagnosis; all comparisons secondary",
              "workpoints": frozen["workpoints"]["matched_all"]["0.05"]}
    ai.write_json(folder/"result.json", output)


def independent_audit(checked, folder, budget, runsha):
    log = checked.json(OUT/"run/run_manifest.json", runsha)
    if log["status"] != "completed": raise ValueError("diagnostic incomplete")
    for p, sha in log["output_sha256"].items(): checked.read(OUT/"run"/p, sha)
    get = lambda n: checked.json(OUT/"run"/n, log["output_sha256"][n])
    diagnostic, cards, selected = get("result.json"), get("routing_cards.json"), get("selection.json")
    frozen, meta, streams, ledger, _ = load_parent(checked)
    checks = 0
    for name in TIMING:
        for d in DELTAS:
            count, absent = 0, []
            for k, r in meta.items():
                if r["variant"] != "attack" or r["x"] is None: continue
                end = next((int(t) for t, p in zip(streams[k]["ends"], streams[k]["p"][:, ev.CELLS.index(name)])
                            if p <= frozen["workpoints"]["matched_all"]["0.05"][name]["alpha"]), None)
                count += int(end is not None and end >= r["e"] and end <= r["x"]+d)
                if not any(r["e"] <= t <= r["x"]+d for t in streams[k]["ends"]): absent.append(k)
            row = diagnostic["timing"]["deadlines"][name][str(d)]
            if count != row["count"] or sorted(absent) != sorted(row["no_eligible_look"]) or row["n"] != 126:
                raise ValueError("independent deadline check failed")
            checks += 1
    if [(c["key"], c["end"], c["reasons"]) for c in cards] != [(c["key"], c["end"], c["reasons"]) for c in selected["queries"]]:
        raise ValueError("cards differ from pre-route selection")
    solved, maxerr, sources = set(), 0., {}
    for card in cards:
        budget.check(); k, end = card["key"], card["end"]
        ids = np.asarray(card["window"]["top_k_ids_t_l_k"]); weights = np.asarray(card["window"]["selected_weights_t_l_k"])
        if ids.shape != (8, 24, 4) or weights.shape != ids.shape: raise ValueError("invalid route card shape")
        if any(len(set(x)) != 4 for x in ids.reshape(-1, 4)): raise ValueError("duplicate experts")
        np.testing.assert_allclose(weights.sum(-1), 1., rtol=0, atol=3e-7)
        for name, contrib in card["W_signed_coordinate_contributions"].items():
            np.testing.assert_allclose(np.asarray(contrib).reshape(24, 32).sum(1), card["W_signed_layer_contributions"][name], rtol=0, atol=1e-10)
            np.testing.assert_allclose(np.sum(contrib), card["raw_13"]["W_"+name], rtol=5e-6, atol=2e-6)
        fold, tag = str(meta[k]["fold"]), card["window"]["tag"]; group = (fold, tag)
        if group in solved: continue
        info = frozen["folds"][fold]["models"][tag]
        model = math.Model.restore(ai.npz_bytes(checked.read(OLD/info["path"], info["sha256"])))
        wp = np.zeros((8, 24, 32)); np.put_along_axis(wp, ids, weights, axis=2)
        delta = np.sqrt(wp.mean(0)/24).reshape(-1)-model.mu[1]
        np.testing.assert_allclose(delta, card["W_delta"], rtol=0, atol=3e-7)
        c = model.cov[1]; d = len(c); base = np.diag(c)+.01*np.trace(c)/d
        matrices = {"diag": np.diag(base), "full": .5*c+np.diag(base-.5*np.diag(c))}
        block = np.zeros_like(c)
        for lo in range(0, d, 32): block[lo:lo+32, lo:lo+32] = matrices["full"][lo:lo+32, lo:lo+32]
        matrices["block"] = block
        for name, matrix in matrices.items():
            independent = float(delta@np.linalg.solve(matrix, delta)/d)
            stored = card["raw_13"]["W_"+name]; maxerr = max(maxerr, abs(independent-stored))
            np.testing.assert_allclose(independent, stored, rtol=5e-6, atol=2e-6)
        solved.add(group)
    ai.write_json(folder/"checks.json", {"status": "PASS", "deadline_columns": checks, "cards_checked": len(cards),
                  "independent_linear_solve_groups": sorted(solved), "independent_score_max_error": maxerr,
                  "note": "independent formulas in same frozen diagnostic; not an external reviewer or fresh test set"})


def run(stage, sha, runsha=None):
    start = time.monotonic(); guard = old.Guard(False); guard.install(); checked = pf.CheckedInputs(guard)
    source = verify_source(sha, checked)
    previous = 0.
    if stage == "audit": previous = checked.json(OUT/"run/run_manifest.json", runsha)["elapsed_seconds"]
    budget = old.Budget(previous); folder = OUT/stage; folder.mkdir(exist_ok=False)
    status, error = "failed", None
    try:
        torch.set_num_threads(1); torch.set_num_interop_threads(1)
        with ExitStack() as scope:
            for obj, name in ((math, "fit"), (math, "moments"), (ae, "calibrate"), (trm3_g, "calibrate_g")):
                scope.enter_context(patch.object(obj, name, prohibit))
            if stage == "run": diagnose(checked, folder, budget, source["source_sha256"])
            else: independent_audit(checked, folder, budget, runsha)
        verify_source(sha, checked); checked.verify_again(); budget.check(); status = "completed"
    except Exception as exc:
        error = repr(exc); raise
    finally:
        manifest = {"status": status, "error": error, "source_manifest_sha256": sha,
                    "input_sha256": checked.hashes, "output_sha256": old.hashes(folder),
                    "elapsed_seconds": time.monotonic()-start, "previous_seconds": previous,
                    "peak_gib": pf.rss(), "access_guard": guard.summary()}
        ai.write_json(folder/"run_manifest.json", manifest)
        print(json.dumps({k: manifest[k] for k in ("status", "elapsed_seconds", "previous_seconds", "peak_gib", "error")}), flush=True)
        print(json.dumps({"run_manifest_sha256": pf.digest((folder/"run_manifest.json").read_bytes())}), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("stage", choices=("freeze", "run", "audit"))
    p.add_argument("--source-sha"); p.add_argument("--run-sha"); a = p.parse_args()
    if a.stage == "freeze": freeze()
    else:
        if not a.source_sha or (a.stage == "audit" and not a.run_sha): p.error("required frozen SHA missing")
        run(a.stage, a.source_sha, a.run_sha)
