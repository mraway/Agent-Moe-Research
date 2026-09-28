"""Independent M15 cache/vector, point-distance and matching arithmetic audit."""
from __future__ import annotations

from collections import Counter
from itertools import combinations
import json
import time
import numpy as np

from research_v4 import codex_g_mech_m15 as m
from research_v4 import codex_g_mech_m14_audit as old_audit
from research_v4.codex_g_m1 import ROOT, sha, write_json


def brute_context(axis, episode, anchor, label_end=None):
    current = next((i for i, b in enumerate(axis["bodies"]) if b["start"] <= anchor < b["stop"]), None)
    if current is None: return None
    body = axis["bodies"][current]; offset = anchor-body["start"]
    if offset:
        pre = list(range(max(body["start"], anchor-8), anchor)); mode = "same_body"; previous = body
    elif current and axis["bodies"][current-1]["stop"]-axis["bodies"][current-1]["start"] >= 8:
        previous = axis["bodies"][current-1]; pre = list(range(previous["stop"]-8, previous["stop"])); mode = "previous_message"
    else: return None
    stop = min(body["stop"], label_end+1) if label_end is not None else body["stop"]
    if stop <= anchor: return None
    return dict(episode=int(episode), anchor=int(anchor), pre=pre, post=[t if t < stop else -1 for t in range(anchor, anchor+32)],
                mode=mode, width=len(pre), channel=body["channel"], step=body["step"], body_index=current,
                body_offset=offset, pre_channel=previous["channel"], step_delta=body["step"]-previous["step"], anchor_token=int(axis["tokens"][anchor]))


def brute_choose(query, axes, metadata, keys):
    candidates = []; qmeta = metadata[str(keys[query["episode"]])]
    fields = ("mode", "width", "channel", "step", "pre_channel", "step_delta", "body_offset", "anchor_token")
    for ep, key in enumerate(keys):
        row = metadata[str(key)]
        if row["variant"] not in ("clean", "benign_control", "benign_lexical") or row["filter_pass"] is not True: continue
        if row["fold"] != qmeta["fold"] or row["episode_index"] != qmeta["episode_index"] or row["scenario"] == qmeta["scenario"]: continue
        for body in axes[ep]["bodies"]:
            anchor = body["start"]+query["body_offset"]
            if anchor >= body["stop"] or abs(anchor-query["anchor"]) > 64: continue
            actor = brute_context(axes[ep], ep, anchor)
            if actor is None or any(actor[f] != query[f] for f in fields): continue
            candidates.append((abs(anchor-query["anchor"]), abs(actor["pre"][0]-query["pre"][0]), str(key), actor["body_index"], actor))
    chosen, scenarios, episodes = [], set(), set()
    for *_, actor in sorted(candidates, key=lambda x: x[:4]):
        ep = actor["episode"]; scenario = metadata[str(keys[ep])]["scenario"]
        if ep in episodes or scenario in scenarios: continue
        chosen.append(actor); episodes.add(ep); scenarios.add(scenario)
        if len(chosen) == 3: break
    return chosen


def direct_distance(a, b, rare):
    # a,b: [T,3,L,E]. No call to M11's geometry implementation.
    values = []
    for rep in range(3):
        left, right = a[:, rep], b[:, rep]; tv_terms = .5*abs(left-right)
        middle = (left+right)/2; js = np.zeros(left.shape[:-1])
        for source in (left, right):
            selected = source > 0; terms = np.zeros_like(source)
            terms[selected] = source[selected]*np.log2(source[selected]/middle[selected])
            js += terms.sum(-1)/2
        values.extend([tv_terms.sum(-1), (tv_terms*rare).sum(-1), (tv_terms*(~rare)).sum(-1), js])
    return np.stack(values, axis=1)


def direct_frame(query, donors, rare):
    first = np.mean([direct_distance(query, d, rare) for d in donors], axis=0)
    second = (np.mean([direct_distance(donors[i], donors[j], rare) for i, j in combinations(range(len(donors)), 2)], axis=0)
              if len(donors) > 1 else np.full_like(first, np.nan))
    return np.stack([first, second, first-second], axis=-1)


def audit():
    start = time.monotonic(); guard = m.M15AccessGuard(ROOT); guard.install()
    log = json.loads((m.OUT/"run_manifest.json").read_text()); assert log["status"] == "completed"
    for path, digest in {**log["source_sha256"], **log["input_sha256"]}.items(): assert sha((ROOT/path).read_bytes()) == digest
    for name, digest in log["output_sha256"].items(): assert sha((m.OUT/name).read_bytes()) == digest
    hashes, tokenizer_body, expected_cache = m.prior_inputs(); a, metadata, _ = m.m14.read_inputs()
    events = json.loads((m.m14.OUT/"recovery_events.json").read_text()); records = json.loads((m.OUT/"records.json").read_text())
    axes, ids, protocol = m.axes_and_audit(a, metadata, events, tokenizer_body, expected_cache, hashes)
    assert protocol == json.loads((m.OUT/"protocol_audit.json").read_text())
    old_graph = m.m8.m7.read_npz(m.m14.OUT/"temporal_graph.npz")
    for record in records:
        ev = events[record["event_index"]]
        if record["cohort"] == "B":
            q = brute_context(axes[ev["episode"]], ev["episode"], ev["start"], ev["end"])
            assert q == record["query"]
            if q is not None: assert brute_choose(q, axes, metadata, a["keys"]) == record["donors"]
        else:
            assert ev["relation"] == "after_X" and record["query"]["pre"] == list(range(ev["start"]-8, ev["start"]))
            ds = old_graph["recovery_donors"][0, record["event_index"]]
            ps = old_graph["recovery_posts"][0, record["event_index"]]
            want = [(int(a["episode"][d]), int(a["ends"][d])+1) for d, p in zip(ds, ps, strict=True) if d >= 0 and (p >= 0).all()]
            assert [(d["episode"], d["anchor"]) for d in record["donors"]] == want
    read = m.m8.m7.read_npz; v = read(m.OUT/"token_vectors.npz"); metrics = read(m.OUT/"return_metrics.npz")
    vectors, points = v["vectors"], v["points"]
    np.testing.assert_array_equal(points, m.mm.point_list(records)); maximum_vector_error = 0.
    state = json.loads((m.m8.m7.OUT/"calibrate/threshold_manifest.json").read_text())
    for ep in np.unique(points[:, 0]):
        loc = np.flatnonzero(points[:, 0] == ep); times = points[loc, 1]; key = str(a["keys"][ep]); fold = metadata[key]["fold"]
        logits = m.checked_cache(key, expected_cache, hashes, logits=True)["router_logits"].double().numpy()[:, times].transpose(1, 0, 2)
        actual = ids[ep][:, times].transpose(1, 0, 2); np.testing.assert_array_equal(actual, v["actual_ids"][loc])
        full = np.exp(logits-logits.max(-1, keepdims=True)); full /= full.sum(-1, keepdims=True)
        selected = np.take_along_axis(logits, actual, axis=-1); weight = np.exp(selected-selected.max(-1, keepdims=True)); weight /= weight.sum(-1, keepdims=True)
        uniform, weighted = np.zeros_like(full), np.zeros_like(full)
        np.put_along_axis(uniform, actual, .25, axis=-1); np.put_along_axis(weighted, actual, weight, axis=-1)
        rebuilt = np.stack([uniform, weighted, full], axis=1)
        np.testing.assert_allclose(rebuilt, vectors[loc], rtol=0, atol=1e-14); maximum_vector_error = max(maximum_vector_error, float(abs(rebuilt-vectors[loc]).max()))
        q = np.array(state["cells"]["S"]["folds"][str(fold)]["statistics"]["S"]["q"])
        rarity = np.where(q < .02, -np.log(q), 0.)
        contributions = rarity[np.arange(24)[None, :, None], actual]
        raw = np.column_stack([contributions.sum((1, 2)), (contributions*(1+selected-selected.min(-1, keepdims=True))).sum((1, 2))])
        np.testing.assert_allclose(raw, v["raw"][loc], rtol=0, atol=1e-9)
        np.testing.assert_array_equal(v["rare"][fold], q < .02)
    lookup = {tuple(p): i for i, p in enumerate(points)}; frames = 0; maximum_metric_error = 0.
    for ri, record in enumerate(records):
        q = record["query"]
        if q is None: assert np.isnan(metrics["values"][ri]).all(); continue
        rare = v["rare"][metadata[record["key"]]["fold"]]
        for mode in range(2):
            for h in range(4):
                span = slice(8*h, 8*(h+1)); qgood = all(t >= 0 for t in q["post"][span]) and (not mode or all(t >= 0 for t in q["post"]))
                ds = [i for i, d in enumerate(record["donors"]) if qgood and all(t >= 0 for t in (d["post"] if mode else d["post"][span]))]
                assert np.flatnonzero(metrics["chosen_donors"][ri, mode, h]).tolist() == ds
                if not ds: assert np.isnan(metrics["values"][ri, mode, h]).all(); continue
                preq = [lookup[(q["episode"], t)] for t in q["pre"]]; postq = [lookup[(q["episode"], t)] for t in q["post"][span]]
                before = []; after = []; dpre = []; dpost = []
                for di in ds:
                    d = record["donors"][di]; p = [lookup[(d["episode"], t)] for t in d["pre"]]; s = [lookup[(d["episode"], t)] for t in d["post"][span]]
                    before.append(vectors[p]); after.append(vectors[s]); dpre.append(p); dpost.append(s)
                pre = direct_frame(vectors[preq], before, rare); post = direct_frame(vectors[postq], after, rare)
                rebuilt = np.stack([pre.mean(0), post.mean(0), post.mean(0)-pre.mean(0)], axis=-1)
                actual = metrics["values"][ri, mode, h]
                np.testing.assert_allclose(rebuilt, actual, rtol=0, atol=1e-12, equal_nan=True)
                err = abs(rebuilt-actual); maximum_metric_error = max(maximum_metric_error, float(err[np.isfinite(err)].max(initial=0.)))
                if mode:
                    np.testing.assert_allclose(metrics["common_profile"][ri, 8-len(pre):8], pre, rtol=0, atol=1e-12, equal_nan=True)
                    np.testing.assert_allclose(metrics["common_profile"][ri, 8+8*h:8+8*(h+1)], post, rtol=0, atol=1e-12, equal_nan=True)
                for rep in range(3): np.testing.assert_allclose(actual[4*rep], actual[4*rep+1]+actual[4*rep+2], rtol=0, atol=1e-12, equal_nan=True)
                np.testing.assert_allclose(actual[0], actual[3], rtol=0, atol=1e-12, equal_nan=True)
                for si in range(2):
                    qp, qa = v["raw"][preq, si].mean(), v["raw"][postq, si].mean()
                    np_, na = np.mean([v["raw"][p, si].mean() for p in dpre]), np.mean([v["raw"][p, si].mean() for p in dpost])
                    want = [[qp, qa, qa-qp], [np_, na, na-np_], [qp-np_, qa-na, (qa-qp)-(na-np_)]]
                    np.testing.assert_allclose(metrics["rare_scores"][ri, mode, h, si], want, rtol=0, atol=1e-9)
                frames += 1
    result = json.loads((m.OUT/"result.json").read_text()); checked = old_audit.check_summaries(result, metadata)
    # Check summary values against their exact per-query matrices, not just internal sums.
    for cohort, group in result["cohorts"].items():
        indices = {r["key"]: i for i, r in enumerate(records) if r["cohort"] == cohort}
        for cell in group["groups"].values():
            for mode_name, horizons in cell.items():
                mode = ("per_block", "common_32").index(mode_name)
                for horizon, cell2 in horizons.items():
                    h = int(horizon)//8-1
                    for band, summaries in cell2["bands"].items():
                        band_slice = slice(None) if band == "all" else slice(8*(("early", "middle", "late").index(band)), 8*(("early", "middle", "late").index(band)+1))
                        for name, summary in summaries.items():
                            for row in summary["episode_values"]:
                                measured = metrics["values"][indices[row["key"]], mode, h, :, band_slice].mean(1)
                                if name == "A_any": measured = measured[:, 0, :]
                                np.testing.assert_allclose(measured.ravel(), row["values"], rtol=0, atol=1e-12)
    checks = dict(status="PASS", points_rebuilt=len(points), route_episodes=len(np.unique(points[:, 0])),
                  independent_matching_records=len(records), independent_pre_post_frames=frames,
                  maximum_vector_error=maximum_vector_error, maximum_geometry_error=maximum_metric_error,
                  summary_arithmetic_and_cluster_CIs=checked, elapsed_seconds=time.monotonic()-start, access_guard=guard.summary())
    target = m.OUT/"audit"; target.mkdir(exist_ok=True)
    if (target/"checks.json").exists(): raise ValueError("refusing to overwrite M15 audit")
    write_json(target/"checks.json", checks); print(json.dumps(checks), flush=True)


if __name__ == "__main__": audit()
