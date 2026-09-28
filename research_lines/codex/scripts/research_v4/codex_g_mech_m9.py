"""Mechanism-only M9: replay the frozen M8 graph and decompose its raw scores."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
import resource
import time

import numpy as np
from tokenizers import Tokenizer

from research_v2 import trm3_g
from research_v4 import codex_g_m8 as m8
from research_v4 import codex_g_mech_m9_math as dm
from research_v4.codex_g_m1 import ROOT, BASE, sha, write_json

OUT = BASE / "mechanism_m9_window_decomposition_v1"
TOKENIZER = ROOT / "artifacts/hf_cache/models--openai--gpt-oss-20b/snapshots/6cee5e81ee83917806bbde320786a8fb61efebee/tokenizer.json"
M8_SHA = {"run_manifest.json": "467e23777b3fdf88f5c941d8f475c0c765f47e906ccc1ba9d2decb90c6d2d59a",
          "audit/checks.json": "62576c1199bae31ae865cf2ffe328970c00f7365caa9ff95fe8f0c2c873a37da"}
SOURCES = tuple(sorted(set(m8.SOURCES) | {
    "docs/research_v4/codex_g_mechanism_workstream.md", "docs/research_v4/codex_g_mech_m9_analysis_spec.md",
    "scripts/research_v4/codex_g_mech_m9.py", "scripts/research_v4/codex_g_mech_m9_math.py",
    "scripts/research_v4/codex_g_mech_m9_audit.py", "tests/test_research_v4_codex_g_mech_m9.py",
    "src/phase_a/generation.py", "src/agent_v3/episode.py", "src/routing/capture.py"}))


class M9AccessGuard(m8.m7.m6.M6AccessGuard):
    def check_path(self, path):
        p = super().check_path(path)
        roots = (m8.m7.OUT, m8.OUT, OUT, m8.m7.m6.M3/"topk_cache/g_dev", m8.m7.m6.M3/"logit_cache/g_dev")
        if ROOT/"artifacts" in p.parents and p != TOKENIZER.resolve() and not any(r == p or r in p.parents for r in roots):
            self.blocked_attempts += 1
            raise PermissionError("M9 permits only frozen M7/M8, private G-dev caches and one tokenizer JSON")
        return p

    def audit(self, event, args):
        super().audit(event, args)
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            p = self.check_path(args[0]); flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC) and ROOT/"artifacts" in p.parents and OUT not in p.parents:
                self.blocked_attempts += 1
                raise PermissionError("M9 cannot alter prior artifacts or tokenizer")


def source_freeze(expected):
    head = m8.m7.harness.git_output("rev-parse", "HEAD")
    if head != expected or m8.m7.harness.git_output("diff", "HEAD", "--name-only"):
        raise ValueError("M9 requires exact clean tracked source freeze before data access")
    if not set(SOURCES).issubset(set(m8.m7.harness.git_output("ls-files").splitlines())):
        raise ValueError("M9 sources, tests and audit must be committed")
    return head, {p: sha((ROOT/p).read_bytes()) for p in SOURCES}


def prior_inputs():
    logs, hashes = m8.prior_inputs()
    for name, digest in M8_SHA.items():
        p = m8.OUT/name
        if sha(p.read_bytes()) != digest: raise ValueError("M8 frozen log/audit changed")
        hashes[str(p.relative_to(ROOT))] = digest
    log = json.loads((m8.OUT/"run_manifest.json").read_text())
    assert log["status"] == "completed" and not log["access_guard"]["blocked_attempts"]
    assert json.loads((m8.OUT/"audit/checks.json").read_text())["status"] == "PASS"
    for name, digest in log["output_sha256"].items():
        p = m8.OUT/name
        if sha(p.read_bytes()) != digest: raise ValueError("M8 output changed")
        hashes[str(p.relative_to(ROOT))] = digest
    return logs, hashes


def cache_paths(key):
    name = str(key).split("|", 1)[1].replace("#ep", "--ep")
    return (m8.m7.m6.M3/"topk_cache/g_dev"/(name+".safetensors"),
            m8.m7.m6.M3/"logit_cache/g_dev"/(name+".logits.safetensors"))


def primary_rows(g):
    return np.flatnonzero((g["phases"] == 2) & (g["donors"][0, 1] >= 0).any(1))


def build_terms(guard, a, g, metadata, manifest, inventory):
    used = np.unique(np.r_[g["queries"], g["donors"][g["donors"] >= 0]])
    terms = np.empty((len(used), 8, 24, 2), dtype=np.float64)
    primary = primary_rows(g)
    ds = g["donors"][0, 1, primary]
    card_rows = set(np.r_[g["queries"][primary], ds[ds >= 0]].tolist())
    contexts, hashes = {}, {}
    states = {}
    for fold, block in manifest["cells"]["S"]["folds"].items():
        s = trm3_g.RareSurprisal().load_state(block["statistics"]["S"])
        cw = manifest["cells"]["CW"]["folds"][fold]["statistics"]["CW"]
        assert s._layers == tuple(range(24)) and cw["config"]["strength"] == 1.
        assert cw["config"]["recipe"] == "S_times_one_plus_actual_selected_logit_margin_v1"
        assert cw["layers"] == list(range(24)) and cw["config"]["window_width"] == 8
        assert cw["q"] == block["statistics"]["S"]["q"]
        assert cw["config"]["rare_threshold"] == s.rare_threshold
        states[fold] = s.surprisal.numpy()
    for ei, key in enumerate(a["keys"]):
        loc = np.flatnonzero((used >= a["offsets"][ei]) & (used < a["offsets"][ei+1]))
        if not len(loc): continue
        r = metadata[str(key)]; top_path, logit_path = cache_paths(key)
        contents = []
        for p in (top_path, logit_path):
            p = guard.check_path(p); body = p.read_bytes(); rel = str(p.relative_to(ROOT))
            if sha(body) != inventory[rel]: raise ValueError("M7-audited routing cache changed")
            hashes[rel] = sha(body); contents.append(m8.load_bytes(body))
        ids = contents[0]["top_k_ids"].numpy(); tokens = contents[0]["token_ids"].numpy()
        logits = contents[1]["router_logits"].double().numpy()
        assert ids.shape == (24, r["token_count"], 4) and logits.shape == (24, r["token_count"], 32)
        if int(a["structure"][used[loc[0]], 0]) != r["fold"]: raise ValueError("fold mismatch")
        values = dm.token_layer_terms(ids, logits, states[str(r["fold"])])
        ends = a["ends"][used[loc]]
        _, valid, windows = m8.mm.window_geometry(ends, tokens, r["step_output_lengths"])
        assert valid.all()
        np.testing.assert_array_equal(windows, a["tokens"][used[loc]])
        terms[loc] = values[ends[:, None]+np.arange(-7, 1)]
        for global_row in used[loc]:
            if int(global_row) in card_rows:
                lo, prefix = dm.causal_context(tokens, int(a["ends"][global_row]), r["step_output_lengths"])
                contexts[str(int(global_row))] = {"start": lo, "token_ids": prefix.tolist()}
    features, layers = dm.measures(terms)
    rebuilt = features[:, [dm.COLUMNS.index("S/window"), dm.COLUMNS.index("CW/window")]]
    np.testing.assert_allclose(rebuilt, a["values"][used, :2], rtol=0, atol=1e-9)
    return used, terms, features, layers, contexts, hashes, np.max(abs(rebuilt-a["values"][used, :2]), axis=0)


def summaries(a, g, features, layer_features, lookup, deltas, metadata, prior):
    f = m8.mm.MatchFeatures(**{k: a[k] for k in ("keys", "episode", "ends", "structure", "tokens", "tu", "valid")})
    result, profiles = {}, {}
    for hi, phase in enumerate(m8.mm.PHASES):
        mask = g["phases"] == hi; qs = g["queries"][mask]
        result[phase] = {}; profiles[phase] = {}
        for pi, pool in enumerate(m8.mm.POOLS):
            result[phase][pool] = {}
            for vi, version in enumerate(m8.mm.VERSIONS):
                counts = (g["donors"][pi, vi, mask] >= 0).sum(1)
                cell = {"candidate_episodes": 126, "eligible_looks": len(qs), "subsets": {}}
                for name, minimum in (("at_least_one", 1), ("at_least_three", 3)):
                    keep = counts >= minimum
                    reading = m8.mm.effect_summary(f, metadata, qs[keep], deltas[pi, vi, mask][keep])
                    assert (reading["episodes"], reading["looks"]) == tuple(prior["matrix"][phase][pool][version][name][k] for k in ("matched_episodes", "matched_looks"))
                    cell["subsets"][name] = {**reading, "signed_current_window_mean_ratio": dm.signed_shares(reading["mean"])}
                result[phase][pool][version] = cell
            if pi < 2:
                ld = dm.differences(layer_features, lookup, qs, g["donors"][pi, 1, mask])
                keep = (g["donors"][pi, 1, mask] >= 0).any(1)
                profiles[phase][pool] = m8.mm.effect_summary(f, metadata, qs[keep], ld[keep])
        print(json.dumps({"stage": "decomposition_summarized", "phase": phase}), flush=True)
    return result, profiles


def cards_and_categories(a, g, metadata, tokenizer_body, contexts, features, lookup, deltas):
    tokenizer = Tokenizer.from_str(tokenizer_body.decode())
    special_ids = {int(t["id"]) for t in json.loads(tokenizer_body)["added_tokens"] if t.get("special")}
    def describe(row):
        row = int(row); token = int(a["tokens"][row, -1]); piece = tokenizer.decode([token], skip_special_tokens=False)
        context = contexts[str(row)]
        return {"row": row, "key": str(a["keys"][a["episode"][row]]), "end": int(a["ends"][row]),
                "token_id": token, "token_piece": piece, "category": dm.category(piece, special=token in special_ids),
                "window_ids": a["tokens"][row].tolist(), "window_text": tokenizer.decode(a["tokens"][row].tolist(), skip_special_tokens=False),
                "prefix_start": context["start"], "prefix_ids": context["token_ids"],
                "prefix_text": tokenizer.decode(context["token_ids"], skip_special_tokens=False),
                "features": features[lookup[row]].tolist()}
    rows = primary_rows(g); cards = []
    for qi in rows:
        q = int(g["queries"][qi]); ds = g["donors"][0, 1, qi]
        cards.append({"query": describe(q), "donors": [describe(d) for d in ds if d >= 0],
                      "delta": deltas[0, 1, qi].tolist()})
    cards.sort(key=lambda x: (x["query"]["key"], x["query"]["end"]))
    f = m8.mm.MatchFeatures(**{k: a[k] for k in ("keys", "episode", "ends", "structure", "tokens", "tu", "valid")})
    categories = {}
    for cat in dm.CATEGORIES:
        items = [c for c in cards if c["query"]["category"] == cat]
        qs = np.array([c["query"]["row"] for c in items], dtype=np.int64)
        vs = np.array([c["delta"] for c in items]).reshape((-1, len(dm.COLUMNS)))
        categories[cat] = m8.mm.effect_summary(f, metadata, qs, vs)
    return {"scope": "all 45 X/normal_filtered/L1 query looks, lexicographic ordering; not outcome-selected examples",
            "cards": cards, "category_effects": categories,
            "token_look_counts": dict(Counter(str(c["query"]["token_id"]) for c in cards)),
            "special_ids": sorted(special_ids)}


def run(expected):
    start = time.monotonic(); guard = M9AccessGuard(ROOT, "analysis"); guard.install()
    head, sources = source_freeze(expected)
    if OUT.exists(): raise ValueError("M9 refuses to overwrite an existing run")
    logs, hashes = prior_inputs()
    a = m8.m7.read_npz(m8.OUT/"look_inventory.npz"); g = m8.m7.read_npz(m8.OUT/"match_graph.npz")
    metadata = json.loads((m8.OUT/"episode_metadata.json").read_text())
    prior = json.loads((m8.OUT/"result.json").read_text())
    old_deltas = m8.m7.read_npz(m8.OUT/"residuals.npz")["effects"]
    manifest = json.loads((m8.m7.OUT/"calibrate/threshold_manifest.json").read_text())
    used, terms, features, layers, contexts, cache_hashes, errors = build_terms(guard, a, g, metadata, manifest, logs["score"]["input_sha256"])
    tokenizer_body = guard.check_path(TOKENIZER).read_bytes(); hashes[str(TOKENIZER.relative_to(ROOT))] = sha(tokenizer_body)
    lookup = np.full(len(a["ends"]), -1, dtype=np.int64); lookup[used] = np.arange(len(used))
    deltas = np.stack([np.stack([dm.differences(features, lookup, g["queries"], d) for d in pool]) for pool in g["donors"]])
    np.testing.assert_allclose(deltas[..., [0, 10]], old_deltas[..., :2], rtol=0, atol=1e-9, equal_nan=True)
    OUT.mkdir(parents=True)
    log = {"status": "started", "implementation_commit": head, "source_sha256": sources,
           "input_sha256": {**hashes, **cache_hashes}, "started_utc": datetime.now(timezone.utc).isoformat(),
           "evidence_role": "G-dev development mechanism decomposition only; fixed M8 matching graph; no detector"}
    write_json(OUT/"run_manifest.json", log)
    np.savez_compressed(OUT/"window_terms.npz", used=used, terms=terms, features=features, layers=layers, columns=np.array(dm.COLUMNS), layer_columns=np.array(dm.LAYER_COLUMNS))
    np.savez_compressed(OUT/"deltas.npz", values=deltas)
    write_json(OUT/"causal_context_ids.json", contexts)
    matrix, profiles = summaries(a, g, features, layers, lookup, deltas, metadata, prior)
    write_json(OUT/"result.json", {"matrix": matrix, "columns": dm.COLUMNS, "used_looks": len(used),
                                  "raw_score_reconstruction_max_error": errors.tolist(), "M8_matched_graph_unchanged": True,
                                  "uncertainty": "fixed bank/graph, query-family bootstrap2000; no causal or confirmatory inference"})
    write_json(OUT/"layer_profiles.json", {"columns": dm.LAYER_COLUMNS, "profiles": profiles})
    write_json(OUT/"case_cards.json", cards_and_categories(a, g, metadata, tokenizer_body, contexts, features, lookup, deltas))
    for path, digest in log["input_sha256"].items():
        if sha((ROOT/path).read_bytes()) != digest: raise ValueError("input changed during M9")
    if source_freeze(expected) != (head, sources): raise ValueError("sources changed during M9")
    log.update(status="completed", finished_utc=datetime.now(timezone.utc).isoformat(), elapsed_seconds=time.monotonic()-start,
               peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2, access_guard=guard.summary(),
               output_sha256={p.name: sha(p.read_bytes()) for p in sorted(OUT.iterdir()) if p.is_file() and p.name != "run_manifest.json"})
    assert not log["access_guard"]["blocked_attempts"]
    write_json(OUT/"run_manifest.json", log)
    print(json.dumps({k: log[k] for k in ("status", "implementation_commit", "elapsed_seconds", "peak_rss_gib")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--freeze-commit", required=True)
    run(parser.parse_args().freeze_commit)
