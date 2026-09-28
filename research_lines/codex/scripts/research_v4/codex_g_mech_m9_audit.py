"""Independent M9 cache-to-component replay. Does not call production math."""
from __future__ import annotations

from collections import Counter
import json
import time
import unicodedata

import numpy as np

from research_v4 import codex_g_mech_m9 as m9
from research_v4.codex_g_m1 import ROOT, sha, write_json
from research_v4.codex_g_m8_audit import close, manual_episode_means


def enumerate_experts(ids, logits, q, threshold):
    """Independently add every actually selected expert, vectorized over tokens."""
    layers, tokens, k = ids.shape
    result = np.zeros((tokens, layers, 2))
    for layer in range(layers):
        chosen_logits = np.array([logits[layer, np.arange(tokens), ids[layer, :, rank]] for rank in range(k)])
        lowest = chosen_logits.min(0)
        for rank in range(k):
            probabilities = q[layer, ids[layer, :, rank]]
            value = np.where(probabilities < threshold, -np.log(probabilities), 0.)
            result[:, layer, 0] += value
            result[:, layer, 1] += value*(1+chosen_logits[rank]-lowest)
    return result


def scalar_measures(terms):
    """Independent lag/layer accumulation, without production mean/band helpers."""
    values = np.zeros((len(terms), 20)); profiles = np.zeros((len(terms), 96))
    for stat in range(2):
        o = stat*10
        for layer in range(24):
            current = terms[:, 7, layer, stat]
            whole = sum((terms[:, lag, layer, stat]/8 for lag in range(8)), np.zeros(len(terms)))
            history = sum((terms[:, lag, layer, stat]/8 for lag in range(7)), np.zeros(len(terms)))
            values[:, o] += whole
            values[:, o+1] += current
            values[:, o+2] += current/8
            values[:, o+3] += history
            values[:, o+4+layer//8] += current
            values[:, o+7+layer//8] += whole
            profiles[:, stat*48+layer] = current
            profiles[:, stat*48+24+layer] = whole
    return values, profiles


def verify_summary(reading, qs, values, a, metadata):
    eps, means = manual_episode_means(qs, values, a)
    assert reading["episodes"] == len(eps) and reading["looks"] == len(qs)
    if not len(eps):
        assert reading["mean"] is None and not reading["episode_values"]
        return
    close(reading["mean"], means.mean(0)); close(reading["median"], np.median(means, axis=0))
    close(reading["positive_fraction"], (means > 0).mean(0))
    counts = Counter(a["episode"][qs].tolist())
    for row, ep, value in zip(reading["episode_values"], eps, means, strict=True):
        assert row["key"] == a["keys"][ep] and row["looks"] == counts[ep]
        close(row["value"], value)
    for name in ("family", "family_tier"):
        labels = [str(metadata[str(a["keys"][e])]["family"])+(
            "|"+str(metadata[str(a["keys"][e])]["tier"]) if name == "family_tier" else "") for e in eps]
        groups = sorted(set(labels)); sizes = np.array([labels.count(g) for g in groups])
        sums = np.array([sum((means[i] for i, label in enumerate(labels) if label == g), np.zeros(values.shape[1])) for g in groups])
        state = reading[name]
        assert state["cluster_count"] == len(groups) and state["cluster_sizes"] == dict(zip(groups, sizes.tolist(), strict=True))
        assert state["replicates"] == 2000 and state["seed"] == 802608
        draws = np.random.default_rng(802608).integers(len(groups), size=(2000, len(groups)))
        draws_mean = sums[draws].sum(1)/sizes[draws].sum(1)[:, None]
        close(state["mean_ci95"], np.quantile(draws_mean, [.025, .975], axis=0).T)


def run():
    started = time.monotonic(); guard = m9.M9AccessGuard(ROOT, "audit"); guard.install()
    out = m9.OUT/"audit"
    if out.exists(): raise ValueError("refusing to overwrite M9 audit")
    log_body = (m9.OUT/"run_manifest.json").read_bytes(); log = json.loads(log_body)
    assert log["status"] == "completed" and not log["access_guard"]["blocked_attempts"]
    assert m9.source_freeze(log["implementation_commit"])[1] == log["source_sha256"]
    m9.prior_inputs()
    for path, digest in log["input_sha256"].items(): assert sha((ROOT/path).read_bytes()) == digest
    for name, digest in log["output_sha256"].items(): assert sha((m9.OUT/name).read_bytes()) == digest
    a = m9.m8.m7.read_npz(m9.m8.OUT/"look_inventory.npz")
    g = m9.m8.m7.read_npz(m9.m8.OUT/"match_graph.npz")
    original = m9.m8.m7.read_npz(m9.m8.OUT/"residuals.npz")["effects"]
    w = m9.m8.m7.read_npz(m9.OUT/"window_terms.npz")
    delta = m9.m8.m7.read_npz(m9.OUT/"deltas.npz")["values"]
    metadata = json.loads((m9.m8.OUT/"episode_metadata.json").read_text())
    manifest = json.loads((m9.m8.m7.OUT/"calibrate/threshold_manifest.json").read_text())
    contexts = json.loads((m9.OUT/"causal_context_ids.json").read_text())
    expected_used = sorted(set(g["queries"].tolist()) | {int(d) for d in g["donors"].flatten() if d >= 0})
    np.testing.assert_array_equal(w["used"], expected_used)
    for ei, key in enumerate(a["keys"]):
        loc = np.flatnonzero(a["episode"][w["used"]] == ei)
        if not len(loc): continue
        row = metadata[str(key)]
        name = str(key).split("|", 1)[1].replace("#ep", "--ep")
        p = m9.m8.m7.m6.M3/"topk_cache/g_dev"/(name+".safetensors")
        lp = m9.m8.m7.m6.M3/"logit_cache/g_dev"/(name+".logits.safetensors")
        top = m9.m8.load_bytes(guard.check_path(p).read_bytes()); ids = top["top_k_ids"].numpy()
        tokens = top["token_ids"].numpy(); logits = m9.m8.load_bytes(guard.check_path(lp).read_bytes())["router_logits"].double().numpy()
        state = manifest["cells"]["S"]["folds"][str(row["fold"])]["statistics"]["S"]
        rebuilt = enumerate_experts(ids, logits, np.array(state["q"]), state["config"]["rare_threshold"])
        for position in loc:
            look = int(w["used"][position]); end = int(a["ends"][look])
            close(w["terms"][position], rebuilt[end-7:end+1])
            np.testing.assert_array_equal(tokens[end-7:end+1], a["tokens"][look])
            if str(look) in contexts:
                card = contexts[str(look)]; start = max([0]+[int(v) for v in np.cumsum(row["step_output_lengths"]) if v <= end])
                assert card["start"] == max(start, end-31)
                assert card["token_ids"] == tokens[card["start"]:end+1].tolist()
    features, layers = scalar_measures(w["terms"])
    close(w["features"], features); close(w["layers"], layers)
    close(features[:, [0, 10]], a["values"][w["used"], :2])
    lookup = {int(gid): i for i, gid in enumerate(w["used"])}
    matched = 0
    for pi in range(6):
        for vi in range(4):
            for qi, q in enumerate(g["queries"]):
                ds = [int(d) for d in g["donors"][pi, vi, qi] if d >= 0]
                if not ds:
                    assert np.isnan(delta[pi, vi, qi]).all(); continue
                expected = features[lookup[int(q)]]-sum((features[lookup[d]] for d in ds), np.zeros(20))/len(ds)
                close(delta[pi, vi, qi], expected)
                close(expected[[0, 10]], original[pi, vi, qi, :2])
                for offset in (0, 10):
                    close(expected[offset], expected[offset+2]+expected[offset+3])
                    close(expected[offset+1], sum(expected[offset+4:offset+7]))
                    close(expected[offset], sum(expected[offset+7:offset+10]))
                matched += 1
    result = json.loads((m9.OUT/"result.json").read_text())
    profiles = json.loads((m9.OUT/"layer_profiles.json").read_text())
    prior = json.loads((m9.m8.OUT/"result.json").read_text())
    summaries = 0
    for hi, phase in enumerate(("E_at", "X_pre", "X_at")):
        mask = g["phases"] == hi; qs = g["queries"][mask]
        for pi, pool in enumerate(g["pools"]):
            for vi, version in enumerate(g["versions"]):
                counts = (g["donors"][pi, vi, mask] >= 0).sum(1)
                cell = result["matrix"][phase][str(pool)][str(version)]
                assert cell["candidate_episodes"] == 126 and cell["eligible_looks"] == len(qs)
                for name, minimum in (("at_least_one", 1), ("at_least_three", 3)):
                    keep = counts >= minimum; r = cell["subsets"][name]
                    verify_summary(r, qs[keep], delta[pi, vi, mask][keep], a, metadata); summaries += 1
                    old = prior["matrix"][phase][str(pool)][str(version)][name]
                    assert (r["episodes"], r["looks"]) == (old["matched_episodes"], old["matched_looks"])
                    for stat, offset in (("S", 0), ("CW", 10)):
                        ratio = None if r["mean"] is None or abs(r["mean"][offset]) <= 1e-9 else r["mean"][offset+2]/r["mean"][offset]
                        close(r["signed_current_window_mean_ratio"][stat], ratio)
            if pi < 2:
                ds = g["donors"][pi, 1, mask]; keep = (ds >= 0).any(1)
                ld = np.array([layers[lookup[int(q)]]-np.mean([layers[lookup[int(d)]] for d in bank if d >= 0], axis=0) for q, bank in zip(qs[keep], ds[keep], strict=True)])
                verify_summary(profiles["profiles"][phase][str(pool)], qs[keep], ld, a, metadata); summaries += 1
    cards = json.loads((m9.OUT/"case_cards.json").read_text())
    tokenizer_body = guard.check_path(m9.TOKENIZER).read_bytes()
    tokenizer = m9.Tokenizer.from_str(tokenizer_body.decode())
    specials = sorted(int(v["id"]) for v in json.loads(tokenizer_body)["added_tokens"] if v.get("special"))
    assert specials == cards["special_ids"]
    expected_cards = [(str(a["keys"][a["episode"][q]]), int(a["ends"][q])) for qi, q in enumerate(g["queries"]) if g["phases"][qi] == 2 and (g["donors"][0, 1, qi] >= 0).any()]
    assert [(c["query"]["key"], c["query"]["end"]) for c in cards["cards"]] == sorted(expected_cards)
    assert len(cards["cards"]) == 45
    for card in cards["cards"]:
        qi = next(int(i) for i in np.flatnonzero((g["queries"] == card["query"]["row"]) & (g["phases"] == 2)))
        assert [d["row"] for d in card["donors"]] == g["donors"][0, 1, qi][g["donors"][0, 1, qi] >= 0].tolist()
        close(card["delta"], delta[0, 1, qi])
        for item in [card["query"]]+card["donors"]:
            row_id = item["row"]; piece = tokenizer.decode([item["token_id"]], skip_special_tokens=False)
            assert item["token_id"] == a["tokens"][row_id, -1]
            assert item["window_ids"] == a["tokens"][row_id].tolist()
            assert item["prefix_ids"] == contexts[str(row_id)]["token_ids"]
            assert item["prefix_start"] == contexts[str(row_id)]["start"]
            assert item["key"] == a["keys"][a["episode"][row_id]] and item["end"] == a["ends"][row_id]
            assert item["token_piece"] == piece
            assert item["window_text"] == tokenizer.decode(item["window_ids"], skip_special_tokens=False)
            assert item["prefix_text"] == tokenizer.decode(item["prefix_ids"], skip_special_tokens=False)
            char_types = [unicodedata.category(c)[0] for c in piece]
            stripped_types = [unicodedata.category(c)[0] for c in piece if not c.isspace()]
            cat = ("special" if item["token_id"] in specials else "whitespace" if piece and piece.isspace()
                   else "letter" if "L" in char_types else "number" if "N" in char_types
                   else "punctuation_symbol" if stripped_types and set(stripped_types) <= {"P", "S"} else "other")
            assert item["category"] == cat
            close(item["features"], features[lookup[row_id]])
    for cat, reading in cards["category_effects"].items():
        selected = [c for c in cards["cards"] if c["query"]["category"] == cat]
        qs = np.array([c["query"]["row"] for c in selected], dtype=np.int64)
        values = np.array([c["delta"] for c in selected]).reshape((-1, 20))
        verify_summary(reading, qs, values, a, metadata); summaries += 1
    for path, digest in log["input_sha256"].items(): assert sha((ROOT/path).read_bytes()) == digest
    out.mkdir()
    report = {"status": "PASS", "used_looks_rebuilt": len(w["used"]), "matched_decompositions_replayed": matched,
              "summaries_and_cluster_intervals_replayed": summaries, "case_cards": len(cards["cards"]),
              "all_layers_and_lags_rebuilt_from_actual_selected_experts": True,
              "M8_window_scores_and_matching_unchanged": True, "run_manifest_sha256": sha(log_body),
              "elapsed_seconds": time.monotonic()-started, "access_guard": guard.summary()}
    assert not report["access_guard"]["blocked_attempts"]
    write_json(out/"checks.json", report); print(json.dumps(report), flush=True)


if __name__ == "__main__": run()
