"""M15: frozen token geometry on existing OPEN G-dev, no new model execution."""
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
from research_v4 import codex_g_mech_m14 as m14, codex_g_mech_m15_math as mm
from research_v4 import codex_g_mech_m9_math as rare_math
from research_v4.codex_access_guard import CodexGAccessGuard
from research_v4.codex_g_m1 import ROOT, BASE, LABELS, sha, write_json

m8 = m14.m8
OUT = BASE/"mechanism_m15_token_return_v1"
# The tokenizer revision is the same fixed snapshot used by M9.
TOKENIZER = ROOT/"artifacts/hf_cache/models--openai--gpt-oss-20b/snapshots/6cee5e81ee83917806bbde320786a8fb61efebee/tokenizer.json"
M9 = BASE/"mechanism_m9_window_decomposition_v1"
M9_LOG_SHA = "1cee7157cc3772bd706bdd0960e49e415d6ee1fb6f5e41cb3d1987cad68f410b"
M14_SHA = {"run_manifest.json": "e10845f70708a08e5afaed00a8ef76720705958fc6bcc57ab7b051dd39128439",
           "audit/checks.json": "41f72bc8fd05b90a92c16ccff69ebf1968c7f3ffac4b05f88fd9ddb777686233"}
SOURCES = tuple(sorted(set(m14.SOURCES) | {
    "docs/research_v4/codex_g_mech_m15_analysis_spec.md", "scripts/research_v4/codex_g_mech_m15.py",
    "scripts/research_v4/codex_g_mech_m15_math.py", "scripts/research_v4/codex_g_mech_m15_audit.py",
    "tests/test_research_v4_codex_g_mech_m15.py", "scripts/research_v4/codex_g_mech_m9_math.py",
    "scripts/research_v4/codex_g_mech_m11_math.py", "src/agent_v3/harmony.py"}))


class M15AccessGuard(CodexGAccessGuard):
    def check_path(self, path):
        p = super().check_path(path)
        allowed = (m8.m7.OUT, m8.OUT, m14.OUT, OUT, m8.m7.m6.M3/"topk_cache/g_dev", m8.m7.m6.M3/"logit_cache/g_dev")
        if ROOT/"artifacts" in p.parents and p not in (LABELS, TOKENIZER.resolve(), M9/"run_manifest.json") and not any(r == p or r in p.parents for r in allowed):
            self.blocked_attempts += 1
            raise PermissionError("M15 permits only frozen M7/M8/M14, hashed G-dev caches and fixed tokenizer JSON")
        return p
    def audit(self, event, args):
        super().audit(event, args)
        if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
            p = self.check_path(args[0]); flags = args[2] if len(args) > 2 and isinstance(args[2], int) else 0
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC) and ROOT/"artifacts" in p.parents and OUT not in p.parents:
                self.blocked_attempts += 1
                raise PermissionError("M15 cannot write earlier or other-line artifacts")


def source_freeze(expected):
    git = m8.m7.harness.git_output; head = git("rev-parse", "HEAD")
    if head != expected or git("diff", "HEAD", "--name-only"): raise ValueError("M15 requires exact clean tracked HEAD")
    if not set(SOURCES).issubset(set(git("ls-files").splitlines())): raise ValueError("commit plan, code, tests, audit first")
    return head, {p: sha((ROOT/p).read_bytes()) for p in SOURCES}


def prior_inputs():
    hashes = m14.prior_inputs()
    for name, digest in M14_SHA.items():
        p = m14.OUT/name
        if sha(p.read_bytes()) != digest: raise ValueError("M14 frozen log/audit changed")
        hashes[str(p.relative_to(ROOT))] = digest
    log = json.loads((m14.OUT/"run_manifest.json").read_text())
    assert log["status"] == "completed" and json.loads((m14.OUT/"audit/checks.json").read_text())["status"] == "PASS"
    for name, digest in log["output_sha256"].items():
        p = m14.OUT/name
        if sha(p.read_bytes()) != digest: raise ValueError("M14 output changed")
        hashes[str(p.relative_to(ROOT))] = digest
    p = M9/"run_manifest.json"; body = p.read_bytes()
    if sha(body) != M9_LOG_SHA: raise ValueError("M9 tokenizer provenance changed")
    hashes[str(p.relative_to(ROOT))] = M9_LOG_SHA
    tokenizer_body = TOKENIZER.read_bytes(); relative = str(TOKENIZER.relative_to(ROOT))
    if sha(tokenizer_body) != json.loads(body)["input_sha256"][relative]: raise ValueError("M9 tokenizer changed")
    hashes[relative] = sha(tokenizer_body)
    cache_hashes = json.loads((m8.m7.OUT/"score/run_manifest.json").read_text())["input_sha256"]
    return hashes, tokenizer_body, cache_hashes


def cache_path(key, *, logits=False):
    name = str(key).split("|", 1)[1].replace("#ep", "--ep")
    return m8.m7.m6.M3/("logit_cache/g_dev" if logits else "topk_cache/g_dev")/(name+(".logits.safetensors" if logits else ".safetensors"))


def checked_cache(key, expected, hashes, *, logits=False):
    p = cache_path(key, logits=logits); body = p.read_bytes(); rel = str(p.relative_to(ROOT))
    if sha(body) != expected[rel]: raise ValueError("M7 audited cache changed")
    hashes[rel] = sha(body); return m8.load_bytes(body)


def axes_and_audit(a, metadata, events, tokenizer_body, expected, hashes):
    tokenizer = Tokenizer.from_str(tokenizer_body.decode()); vocabulary = mm.Vocabulary(tokenizer)
    normal = m14.mm.normal_mask(a["keys"], metadata)
    selected = sorted(set(np.flatnonzero(normal).tolist()) | {e["episode"] for e in events})
    axes, ids = {}, {}; total_looks = 0
    for ep in selected:
        key = str(a["keys"][ep]); row = metadata[key]; data = checked_cache(key, expected, hashes)
        tokens = data["token_ids"].numpy(); ids[ep] = data["top_k_ids"].numpy()
        assert tokens.shape == (row["token_count"],) and ids[ep].shape == (24, len(tokens), 4)
        axis = mm.token_axis(tokens, row["step_output_lengths"], vocabulary); axes[ep] = axis
        ix = np.flatnonzero(a["episode"] == ep); ends = a["ends"][ix]
        np.testing.assert_array_equal(axis["tags"][ends], np.array(("analysis", "commentary", "final"))[a["structure"][ix, 1]])
        np.testing.assert_array_equal(axis["steps"][ends], a["structure"][ix, 3])
        np.testing.assert_array_equal(tokens[ends[:, None]+np.arange(-7, 1)], a["tokens"][ix]); total_looks += len(ix)
    audit = []
    for ev in events:
        axis = axes[ev["episode"]]; r = ev["start"]; actor, reason = mm.body_context(axis, ev["episode"], r, ev["end"])
        bi = int(axis["body_ids"][r]); before = r-axis["bodies"][bi]["start"] if bi >= 0 else None
        audit.append(dict(key=ev["key"], episode=ev["episode"], R=r, relation=ev["relation"], group=ev["group"], M14_pre=ev["pre"],
                          R_role=mm.ROLES[int(axis["roles"][r])], previous_role=mm.ROLES[int(axis["roles"][r-1])] if r else "outside_episode",
                          previous_piece=tokenizer.decode([int(axis["tokens"][r-1])], skip_special_tokens=False) if r else None,
                          same_body_tokens_before=before, body_context_reason=reason, mode=actor["mode"] if actor else None,
                          width=actor["width"] if actor else None, flow=f'{actor["pre_channel"]}->{actor["channel"]}' if actor else None))
    return axes, ids, dict(episodes_parsed=len(selected), M8_looks_replayed=total_looks, events=audit,
                          previous_role_counts=dict(Counter(r["previous_role"] for r in audit)),
                          boundary_previous_role_counts=dict(Counter(r["previous_role"] for r in audit if r["M14_pre"] == -1)),
                          boundary_context_reasons=dict(Counter(r["body_context_reason"] for r in audit if r["M14_pre"] == -1)))


def fixed_actor(ep, anchor, axis):
    # A deliberately retains M14's exact raw-token blocks, even if protocol tokens are present.
    if anchor < 8 or anchor+31 >= len(axis["tokens"]): raise ValueError("M14 fixed actor not fully observed")
    return dict(episode=int(ep), anchor=int(anchor), pre=list(range(anchor-8, anchor)), post=list(range(anchor, anchor+32)),
                mode="M14_fixed", width=8, channel=str(axis["tags"][anchor]), step=int(axis["steps"][anchor]))


def frozen_records(a, metadata, events, old_graph, axes):
    records = []
    for j, ev in enumerate(events):
        if ev["relation"] != "after_X": continue
        ds = old_graph["recovery_donors"][0, j]; ps = old_graph["recovery_posts"][0, j]
        chosen = ds[(ps >= 0).all(1) & (ds >= 0)]
        assert chosen.size and all(p >= 0 for p in ev["posts"])
        query = fixed_actor(ev["episode"], ev["start"], axes[ev["episode"]])
        donors = [fixed_actor(int(a["episode"][d]), int(a["ends"][d])+1, axes[int(a["episode"][d])]) for d in chosen]
        records.append(dict(cohort="A", event_index=j, key=ev["key"], episode=ev["episode"], relation=ev["relation"], group=ev["group"],
                            query=query, donors=donors, reason="M14_fixed", signature_candidates=len(donors)))
    assert len(records) == 10 and len({d["episode"] for r in records for d in r["donors"]}) == 22
    boundary = []
    for j, ev in enumerate(events):
        if ev["pre"] != -1: continue
        actor, reason = mm.body_context(axes[ev["episode"]], ev["episode"], ev["start"], ev["end"])
        boundary.append(dict(cohort="B", event_index=j, key=ev["key"], episode=ev["episode"], relation=ev["relation"], group=ev["group"],
                             query=actor, donors=[], reason=reason, signature_candidates=0))
    assert len(boundary) == 144
    offsets = {r["query"]["body_offset"] for r in boundary if r["query"] is not None}
    bank = mm.normal_candidates(axes, metadata, a["keys"], offsets)
    for record in boundary:
        query = record["query"]
        if query is None: continue
        candidates = bank.get(mm.signature(query, metadata[record["key"]]), [])
        record["signature_candidates"] = len(candidates)
        record["donors"] = mm.choose(query, candidates, metadata, a["keys"])
        record["reason"] = "matched" if record["donors"] else "no_donor_after_filters" if candidates else "no_matching_signature"
    return records+boundary


def build_vectors(a, metadata, records, points, axes, ids, expected, hashes):
    manifest = json.loads((m8.m7.OUT/"calibrate/threshold_manifest.json").read_text())
    rarity, rare = {}, {}
    for fold, block in manifest["cells"]["S"]["folds"].items():
        s = trm3_g.RareSurprisal().load_state(block["statistics"]["S"])
        assert s._layers == tuple(range(24)) and s.rare_threshold == .02
        rarity[int(fold)] = s.surprisal.numpy(); rare[int(fold)] = np.array(block["statistics"]["S"]["q"]) < .02
    vectors = np.empty((len(points), 3, 24, 32)); actual = np.empty((len(points), 24, 4), np.int64); raw = np.empty((len(points), 2))
    for ep in np.unique(points[:, 0]):
        loc = np.flatnonzero(points[:, 0] == ep); times = points[loc, 1]; key = str(a["keys"][ep])
        logits = checked_cache(key, expected, hashes, logits=True)["router_logits"].double().numpy()
        assert logits.shape == (24, metadata[key]["token_count"], 32)
        actual[loc] = ids[ep][:, times].transpose(1, 0, 2)
        vectors[loc] = mm.geometry.routing_vectors(actual[loc], logits[:, times].transpose(1, 0, 2))
        raw[loc] = rare_math.token_layer_terms(ids[ep][:, times], logits[:, times], rarity[metadata[key]["fold"]]).sum(1)
    np.testing.assert_allclose(vectors.sum(-1), 1, rtol=0, atol=1e-12)
    lookup = {tuple(point): i for i, point in enumerate(points)}; old = {(int(ep), int(t)): i for i, (ep, t) in enumerate(zip(a["episode"], a["ends"], strict=True))}
    errors = []; replays = 0
    for record in records:
        if record["cohort"] != "A": continue
        for actor in [record["query"]]+record["donors"]:
            blocks = [actor["pre"]]+[actor["post"][h*8:h*8+8] for h in range(4)]
            for block in blocks:
                new = raw[[lookup[(actor["episode"], t)] for t in block]].mean(0)
                expected_score = a["values"][old[(actor["episode"], block[-1])], :2]
                np.testing.assert_allclose(new, expected_score, rtol=0, atol=1e-9); errors.append(abs(new-expected_score)); replays += 1
    return vectors, actual, raw, rare, dict(M14_actor_blocks_replayed=replays, maximum_raw_S_CW_error=np.max(errors, axis=0).tolist())


def summaries(a, metadata, records, metrics):
    values, chosen = metrics["values"], metrics["chosen_donors"]
    ep = np.array([r["episode"] for r in records]); cohort = np.array([r["cohort"] for r in records]); result = {}
    def summarize(mask, v): return m14.summarize(a, metadata, ep[mask], v[mask])
    for name in ("A", "B"):
        scope = cohort == name; groups = {"all": scope}
        if name == "B":
            for field in ("relation", "group"):
                for value in sorted({r[field] for r in records if r["cohort"] == "B"}): groups[f"{field}/{value}"] = scope & np.array([r[field] == value for r in records])
            for field in ("mode", "channel"):
                for value in sorted({r["query"][field] for r in records if r["cohort"] == "B" and r["query"] is not None}):
                    groups[f"{field}/{value}"] = scope & np.array([r["query"] is not None and r["query"][field] == value for r in records])
            for field in ("injection_channel", "fold", "tier", "domain_group"):
                for value in sorted({metadata[r["key"]][field] for r in records if r["cohort"] == "B"}):
                    groups[f"{field}/{value}"] = scope & np.array([metadata[r["key"]][field] == value for r in records])
        cell = dict(candidate_episodes=int(scope.sum()), reasons=dict(Counter(records[i]["reason"] for i in np.flatnonzero(scope))), groups={})
        for group, mask in groups.items():
            item = {}
            for mi, mode in enumerate(("per_block", "common_32")):
                rows = {}
                for h in range(4):
                    bands = {}
                    for band in (mm.BANDS if group == "all" else ("all",)):
                        v = mm.band_values(values[:, mi, h], band)
                        bands[band] = {"A_any": summarize(mask, v[:, :, 0, :].reshape((len(records), 36))),
                                       "background_min2": summarize(mask, v.reshape((len(records), len(mm.COLUMNS))))}
                    bg = mask & (chosen[:, mi, h].sum(1) >= 2)
                    donors = [[r["donors"][d]["episode"] for d in np.flatnonzero(chosen[i, mi, h])] for i, r in enumerate(records) if bg[i]]
                    rows[str(8*(h+1))] = dict(bands=bands, donor_reuse_min2=m14.reuse(a, ep[bg], donors),
                        rare_scores=summarize(mask, metrics["rare_scores"][:, mi, h].reshape((len(records), 18))))
                item[mode] = rows
            cell["groups"][group] = item
        result[name] = cell
    return result


def run(expected):
    started = time.monotonic(); guard = M15AccessGuard(ROOT); guard.install(); head, sources = source_freeze(expected)
    if OUT.exists(): raise ValueError("refusing to overwrite M15 output")
    hashes, tokenizer_body, cache_hashes = prior_inputs(); a, metadata, _ = m14.read_inputs()
    events = json.loads((m14.OUT/"recovery_events.json").read_text()); old_graph = m8.m7.read_npz(m14.OUT/"temporal_graph.npz")
    axes, ids, protocol_audit = axes_and_audit(a, metadata, events, tokenizer_body, cache_hashes, hashes)
    records = frozen_records(a, metadata, events, old_graph, axes); points = mm.point_list(records)
    OUT.mkdir(parents=True); write_json(OUT/"records.json", records); write_json(OUT/"protocol_audit.json", protocol_audit)
    graph_sha = sha((OUT/"records.json").read_bytes())
    log = dict(status="token_graph_frozen_before_logit_measurement", implementation_commit=head, source_sha256=sources,
               input_sha256=hashes, graph_sha256=graph_sha, started_utc=datetime.now(timezone.utc).isoformat(),
               evidence_role="G-dev development token routing and protocol-aware return association; not causal, detector or confirmatory")
    write_json(OUT/"run_manifest.json", log)
    print(json.dumps({"stage": log["status"], "query_cohorts": dict(Counter(r["cohort"] for r in records)),
                      "boundary_reasons": dict(Counter(r["reason"] for r in records if r["cohort"] == "B")),
                      "protocol": protocol_audit["boundary_previous_role_counts"]}), flush=True)
    vectors, actual, raw, rare, replay = build_vectors(a, metadata, records, points, axes, ids, cache_hashes, hashes)
    metrics = mm.measurements(records, points, vectors, raw, rare, metadata, a["keys"])
    np.savez_compressed(OUT/"token_vectors.npz", points=points, vectors=vectors, actual_ids=actual, raw=raw, rare=np.stack([rare[i] for i in range(3)]))
    np.savez_compressed(OUT/"return_metrics.npz", **metrics)
    result = dict(cohorts=summaries(a, metadata, records, metrics), columns=mm.COLUMNS, A_columns=mm.A_COLUMNS,
                  representation="U actual uniform top4; W selected-logit softmax; P full32 softmax", protocol_audit=protocol_audit,
                  unique_points=len(points), route_episodes=len(np.unique(points[:, 0])), replay=replay,
                  uncertainty="2000 query-family/family-tier bootstrap conditional on fixed controls; shared-donor, reference and multiple-exploration uncertainty not included")
    write_json(OUT/"result.json", result)
    for p, digest in hashes.items():
        if sha((ROOT/p).read_bytes()) != digest: raise ValueError("M15 input changed")
    assert source_freeze(expected) == (head, sources) and sha((OUT/"records.json").read_bytes()) == graph_sha
    log.update(status="completed", elapsed_seconds=time.monotonic()-started, finished_utc=datetime.now(timezone.utc).isoformat(),
               peak_rss_gib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024**2, access_guard=guard.summary(),
               output_sha256={p.name: sha(p.read_bytes()) for p in sorted(OUT.iterdir()) if p.is_file() and p.name != "run_manifest.json"})
    assert not guard.blocked_attempts; write_json(OUT/"run_manifest.json", log)
    print(json.dumps({k: log[k] for k in ("status", "elapsed_seconds", "peak_rss_gib")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--freeze-commit", required=True)
    run(parser.parse_args().freeze_commit)
