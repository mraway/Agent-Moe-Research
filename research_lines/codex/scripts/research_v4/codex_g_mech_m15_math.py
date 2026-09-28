"""M15 protocol-aware token contexts and full-routing return measurements."""
from __future__ import annotations

from itertools import combinations
import numpy as np
from research_v2 import io_g
from research_v4 import codex_g_mech_m11_math as geometry

REPS = geometry.REPS
DISTANCES = geometry.DISTANCES
TERMS = ("A", "N", "D")
STAGES = ("pre", "post", "change")
BANDS = ("all", "early", "middle", "late")
COLUMNS = tuple(f"{d}/{t}/{s}" for d in DISTANCES for t in TERMS for s in STAGES)
A_COLUMNS = tuple(f"{d}/A/{s}" for d in DISTANCES for s in STAGES)
ROLES = ("unparsed", "header", "body", "terminator")


class Vocabulary:
    def __init__(self, tokenizer): self.tokenizer = tokenizer
    def piece_id(self, piece):
        value = self.tokenizer.token_to_id(piece)
        if value is None: raise ValueError("missing Harmony token")
        return int(value)
    def convert_tokens_to_ids(self, piece): return self.piece_id(piece)
    def decode(self, ids, **kwargs): return self.tokenizer.decode(list(ids), skip_special_tokens=False)


def token_axis(tokens, step_lengths, vocabulary):
    if sum(step_lengths) != len(tokens): raise ValueError("step lengths do not cover generated tokens")
    spans = []; offset = 0
    for step, count in enumerate(step_lengths):
        spans.extend(io_g.spans_from_token_ids(tokens[offset:offset+count].tolist(), vocabulary, offset=offset, agent_step=step))
        offset += count
    tags = np.array(io_g.channel_tag_array(spans, len(tokens), scope="message"))
    roles = np.zeros(len(tokens), dtype=np.int8)
    roles[tags != "other"] = 1
    bodies = []
    for span in spans:
        start, stop = int(span["body_start"]), int(span["body_end"])
        roles[start:stop] = 2
        roles[stop:int(span["end"])+1] = 3
        if stop > start: bodies.append(dict(start=start, stop=stop, channel=span["channel"], step=int(span["agent_step"])))
    body_ids = np.full(len(tokens), -1, dtype=np.int64)
    for i, body in enumerate(bodies):
        sl = slice(body["start"], body["stop"])
        if (body_ids[sl] >= 0).any(): raise ValueError("overlapping message bodies")
        body_ids[sl] = i
    return dict(tokens=tokens, tags=tags, roles=roles, bodies=bodies, body_ids=body_ids,
                steps=np.repeat(np.arange(len(step_lengths)), step_lengths))


def body_context(axis, episode, anchor, label_end=None):
    """Never uses a protocol token as the pre-body baseline or mixes two bodies."""
    if anchor < 0 or anchor >= len(axis["tokens"]): return None, "anchor_outside_episode"
    bi = int(axis["body_ids"][anchor])
    if bi < 0: return None, "anchor_not_body"
    body = axis["bodies"][bi]; position = anchor-body["start"]
    if position:
        width = min(8, position); pre = list(range(anchor-width, anchor)); mode = "same_body"
        before = body
    else:
        if bi == 0: return None, "no_previous_body"
        before = axis["bodies"][bi-1]
        if before["stop"]-before["start"] < 8: return None, "previous_body_shorter_than_8"
        width = 8; pre = list(range(before["stop"]-8, before["stop"])); mode = "previous_message"
    stop = body["stop"] if label_end is None else min(body["stop"], int(label_end)+1)
    if stop <= anchor: return None, "empty_labelled_body"
    post = [t if t < stop else -1 for t in range(anchor, anchor+32)]
    assert (axis["roles"][pre] == 2).all()
    return dict(episode=int(episode), anchor=int(anchor), pre=pre, post=post, mode=mode, width=width,
                channel=body["channel"], step=body["step"], body_index=bi, body_offset=position,
                pre_channel=before["channel"], step_delta=body["step"]-before["step"],
                anchor_token=int(axis["tokens"][anchor])), "available"


def signature(actor, meta):
    return (meta["fold"], meta["episode_index"], actor["step"], actor["channel"], actor["mode"], actor["width"],
            actor["pre_channel"], actor["step_delta"], actor["body_offset"], actor["anchor_token"])


def normal_candidates(axes, metadata, keys, offsets):
    bank = {}
    for ep, key in enumerate(keys):
        meta = metadata[str(key)]
        if meta["variant"] not in ("clean", "benign_control", "benign_lexical") or meta["filter_pass"] is not True: continue
        for body in axes[ep]["bodies"]:
            for offset in sorted(offsets):
                anchor = body["start"]+offset
                if anchor >= body["stop"]: continue
                actor, _ = body_context(axes[ep], ep, anchor)
                if actor is not None: bank.setdefault(signature(actor, meta), []).append(actor)
    return bank


def choose(query, candidates, metadata, keys):
    row = metadata[str(keys[query["episode"]])]; ordered = []
    for candidate in candidates:
        ep = candidate["episode"]; other = metadata[str(keys[ep])]
        if other["scenario"] == row["scenario"]: continue
        delta = abs(query["anchor"]-candidate["anchor"])
        if delta > 64: continue
        ordered.append((delta, abs(query["pre"][0]-candidate["pre"][0]), str(keys[ep]), candidate["body_index"], candidate))
    chosen, seen_ep, seen_scenario = [], set(), set()
    for *_, candidate in sorted(ordered, key=lambda x: x[:4]):
        ep = candidate["episode"]; scenario = metadata[str(keys[ep])]["scenario"]
        if ep in seen_ep or scenario in seen_scenario: continue
        chosen.append(candidate); seen_ep.add(ep); seen_scenario.add(scenario)
        if len(chosen) == 3: break
    return chosen


def point_list(records):
    values = set()
    for record in records:
        actors = ([record["query"]] if record["query"] is not None else [])+record["donors"]
        for actor in actors:
            values.update((actor["episode"], t) for t in actor["pre"]+actor["post"] if t >= 0)
    return np.array(sorted(values), dtype=np.int64).reshape((-1, 2))


def per_point_geometry(query, donors, rare):
    """[T,3,L,E], [K,T,3,L,E] -> [T,12,L,3] (A,N,D)."""
    k, t = donors.shape[:2]
    if k < 1 or query.shape != donors.shape[1:]: raise ValueError("unaligned donor sequences")
    def distance(left, right):
        # Use the same distance definition as M11, without selecting any edges.
        count = len(left); v = np.concatenate([left, right])
        return geometry.pair_metrics(v, np.column_stack([np.arange(count), np.arange(count)+count]),
                                     np.broadcast_to(rare, (count, *rare.shape)))
    attack = np.stack([distance(query, donor) for donor in donors]).mean(0)
    normal = (np.stack([distance(donors[i], donors[j]) for i, j in combinations(range(k), 2)]).mean(0)
              if k >= 2 else np.full_like(attack, np.nan))
    return np.stack([attack, normal, attack-normal], axis=-1)


def measurements(records, points, vectors, raw, rare_by_fold, metadata, keys):
    lookup = {tuple(point): i for i, point in enumerate(points)}
    values = np.full((len(records), 2, 4, 12, 24, 3, 3), np.nan)
    rare_scores = np.full((len(records), 2, 4, 2, 3, 3), np.nan)
    # score/stat/query-or-normal-or-difference/pre-or-post-or-change
    profile = np.full((len(records), 40, 12, 24, 3), np.nan)
    chosen = np.zeros((len(records), 2, 4, 3), dtype=bool)
    def indices(actor, scope): return np.array([lookup[(actor["episode"], t)] for t in actor[scope] if t >= 0], int)
    for ri, record in enumerate(records):
        q = record["query"]
        if q is None or not record["donors"]: continue
        rare = rare_by_fold[metadata[str(keys[q["episode"]])]["fold"]]
        for mode in range(2):
            for h in range(4):
                start, stop = 8*h, 8*(h+1)
                if any(t < 0 for t in q["post"][start:stop]): continue
                if mode and any(t < 0 for t in q["post"]): continue
                good = [i for i, d in enumerate(record["donors"]) if all(t >= 0 for t in (d["post"] if mode else d["post"][start:stop]))]
                if not good: continue
                chosen[ri, mode, h, good] = True
                qpre = indices(q, "pre"); qpost = np.array([lookup[(q["episode"], t)] for t in q["post"][start:stop]])
                ds = [record["donors"][i] for i in good]
                dpre = np.array([indices(d, "pre") for d in ds])
                dpost = np.array([[lookup[(d["episode"], t)] for t in d["post"][start:stop]] for d in ds])
                pre = per_point_geometry(vectors[qpre], vectors[dpre], rare)
                post = per_point_geometry(vectors[qpost], vectors[dpost], rare)
                values[ri, mode, h] = np.stack([pre.mean(0), post.mean(0), post.mean(0)-pre.mean(0)], axis=-1)
                for si in range(2):
                    qp, qa = raw[qpre, si].mean(), raw[qpost, si].mean()
                    np_, na = raw[dpre, si].mean(), raw[dpost, si].mean()
                    rare_scores[ri, mode, h, si] = [[qp, qa, qa-qp], [np_, na, na-np_], [qp-np_, qa-na, (qa-qp)-(na-np_)]]
                if mode:
                    profile[ri, 8-len(pre):8] = pre
                    profile[ri, 8+start:8+stop] = post
    return dict(values=values, rare_scores=rare_scores, common_profile=profile, chosen_donors=chosen)


def band_values(values, band):
    indices = slice(None) if band == "all" else slice(8*(BANDS.index(band)-1), 8*BANDS.index(band))
    return values[..., indices, :, :].mean(axis=-3)
