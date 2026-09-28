"""A01 checked OPEN G-dev I/O and immutable, per-fold array records."""
from __future__ import annotations

from collections import Counter
from io import BytesIO
import json

import numpy as np
import torch

from research_v2 import io_g, trm3_g
from research_v4.codex_access_guard import CodexGAccessGuard
from research_v4 import codex_g_alg_a01_math as am
from research_v4 import codex_g_alg_a01_preflight as pf
from research_v4.codex_g_alg_a01_preflight_v1_1 import native_scalar, write_json

ROOT, BASE = pf.ROOT, pf.BASE
OUT = BASE/"alg_a01_joint_neighbors_eval_v1"
M7_SCORE_SHA = "6dd737b5bb55b91e481467f32e92a410ed2de9caa38991b53eaa257db57318cd"


class Guard(pf.A01NormalGuard):
    def __init__(self, normal):
        super().__init__(ROOT); self.normal = normal

    def check_path(self, path):
        if self.normal:
            return super().check_path(path)
        resolved = CodexGAccessGuard.check_path(self, path)
        if resolved.suffix == ".safetensors" and not any((pf.CACHE/p/"g_dev") in resolved.parents for p in ("topk_cache", "logit_cache")):
            self.blocked_attempts += 1
            raise PermissionError("A01 only reads existing private G-dev route caches")
        return resolved


def npz_bytes(body):
    with np.load(BytesIO(body), allow_pickle=False) as z:
        return {k: z[k] for k in z.files}


def save_npz(path, **values):
    with path.open("xb") as f:
        np.savez_compressed(f, **values)
    return pf.digest(path.read_bytes())


def unpack(z):
    fields = [f for f in z if f not in ("keys", "offsets", "columns")]
    return {str(k): {f: z[f][int(z["offsets"][i]):int(z["offsets"][i+1])] for f in fields}
            for i, k in enumerate(z["keys"])}


def save_streams(path, streams, columns):
    keys = sorted(streams)
    fields = tuple(streams[keys[0]])
    values = {"keys": np.asarray(keys), "columns": np.asarray(columns),
              "offsets": np.cumsum([0]+[len(streams[k]["ends"]) for k in keys]),
              **{f: np.concatenate([streams[k][f] for k in keys]) for f in fields}}
    return save_npz(path, **values)


def baseline(checked, normal):
    directory = pf.M7 if normal else BASE/"m7_full_episode_v1/score"
    log = checked.json(directory/"run_manifest.json", pf.M7_LOG_SHA if normal else M7_SCORE_SHA)
    arrays = npz_bytes(checked.read(directory/"look_streams.npz", log["output_sha256"]["look_streams.npz"]))
    if list(arrays["columns"]) != ["S", "CW", "TU"]:
        raise ValueError("baseline columns changed")
    manifest = checked.json(pf.M7/"threshold_manifest.json", pf.M7_THRESHOLD_SHA)
    return unpack(arrays), manifest, log


def score_inputs(checked):
    """Only called after this round's normal threshold manifest has been verified."""
    prior, manifest, log = baseline(checked, False)
    directory = BASE/"m7_full_episode_v1/score"
    get = lambda name: checked.json(directory/name, log["output_sha256"][name])
    meta, contract = get("episode_metadata.json"), get("data_contract.json")
    if Counter(r["variant"] for r in meta.values()) != {"attack": 352, "clean": 192, "benign_control": 192,
                                                        "benign_lexical": 24, "legitimate_refusal": 24}:
        raise ValueError("G-dev cohort changed")
    if set(meta) != set(prior): raise ValueError("baseline metadata keys differ")
    inventory = checked.json(pf.INVENTORY, pf.INVENTORY_SHA)["input_sha256"]
    episodes = {}
    for rel in sorted(contract["opened_trace_or_manifest_paths"]):
        if not rel.endswith("/trace.json"): continue
        path = checked.guard.check_path(ROOT/rel)
        if pf.GDEV not in path.parents: raise ValueError("trace outside G-dev")
        trace = checked.json(path, inventory[rel])
        for ep in trace["episodes"]:
            key = f'g_dev|{trace["trace_id"]}#ep{ep["episode_index"]}'
            if key not in meta: raise ValueError("unknown episode")
            count = meta[key]["token_count"]
            if count != ep["generated_token_count"] or count >= 2**31-1: raise ValueError("token count mismatch")
            tags = io_g.channel_tag_array(io_g.segment_spans(ep["steps"]), count, scope="message")
            step_ids, token_ids = np.full(count, -1, np.int64), np.full(count, -1, np.int64)
            for i, step in enumerate(ep["steps"]):
                start, n = step["global_token_offset"], step["output_token_count"]
                if np.any(step_ids[start:start+n] >= 0): raise ValueError("overlapping steps")
                events = [e for e in trace["events"] if e["kind"] == "model_generation" and
                          e["episode_index"] == ep["episode_index"] and e["agent_step"] == step["agent_step"]]
                if len(events) != 1 or len(events[0]["output_token_ids"]) != n: raise ValueError("event mismatch")
                step_ids[start:start+n] = i; token_ids[start:start+n] = events[0]["output_token_ids"]
            if np.any(step_ids < 0) or np.any(token_ids < 0): raise ValueError("uncovered tokens")
            ends, _, st, ordinals = trm3_g.segmented_windows(torch.ones((count, 1)), tags, trm3_g.view_of("V1"), 8)
            for field, value in (("ends", ends), ("tags", st), ("ordinals", ordinals)):
                np.testing.assert_array_equal(prior[key][field], value)
            am.valid_ends(ends, tags, step_ids)
            if meta[key]["fold"] != manifest["fold_table"][meta[key]["scenario"]]: raise ValueError("fold changed")
            episodes[key] = {"token_ids": token_ids, "tags": tags, "step_ids": step_ids,
                             "stem": f'{trace["trace_id"]}--ep{ep["episode_index"]}'}
    if set(episodes) != set(meta): raise ValueError("missing episodes")
    positives = [r for r in meta.values() if r["variant"] == "attack" and r["x"] is not None]
    if len(positives) != 126 or not all(r["complete_16"] and r["has_hit_look"] for r in positives):
        raise ValueError("complete X-positive cohort changed; no silent denominator reduction")
    return meta, prior, episodes, inventory, manifest


def fit_hash(keys):
    return pf.digest(json.dumps(sorted(keys), separators=(",", ":")).encode())


def export(path, streams, name, col, fold, role, fit_keys, metadata, banks):
    with path.open("x") as f:
        for key, s in sorted(streams.items()):
            effective = [k for k in fit_keys if metadata[k]["scenario"] != metadata[key]["scenario"]]
            row = {"schema": "dataset-g-look-scores-1.0.0", "statistic": name, "batch": "g_dev",
                   "trace_id": key.split("|", 1)[1], "key": key, "view": "V1", "tag_scope": "message", "window_width": 8,
                   "ends": s["ends"].tolist(), "scores": s["raw"][:, col].tolist(),
                   "tags": list(map(str, s["tags"])), "ordinals": s["ordinals"].tolist(),
                   "config": {"outer_fold": fold, "role": role, "k_distinct_scenarios": 3, "layers": list(range(24)),
                              "distance": "squared_Hellinger", "representation": name, "condition": ["channel", "episode_index"],
                              "query_block": 64, "bank_block": 2048, "self_exclusion": "entire_scenario",
                              "observation": "M7_complete_episode", "condition_banks_sha256": {k: v["sha256"] for k, v in banks.items()}},
                   "fit_pool_sha256": fit_hash(effective)}
            f.write(json.dumps(row, ensure_ascii=False, allow_nan=False, default=native_scalar)+"\n")
