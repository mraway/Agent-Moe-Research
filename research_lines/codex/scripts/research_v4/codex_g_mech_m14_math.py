"""M14 temporal measurements; no detector, fitted attack parameters or alarms."""
from __future__ import annotations

from collections import Counter, defaultdict
import numpy as np

NAMES = ("S", "CW", "TU")
THRESHOLDS = (.90, .95)
METRICS = ("mean_rank", "high_fraction", "order_excess", "high_runs",
           "longest_observed_tokens", "left_censored", "right_censored")
COLUMNS = tuple(f"{r}/{m}" for r in NAMES for m in METRICS)
REC_COLUMNS = tuple(f"{kind}/{r}/{term}" for kind in ("raw", "rank")
                    for r in NAMES for term in ("query_change", "normal_change", "excess_change"))
LEVELS = ("structural", "same_family")


def normal_mask(keys, metadata):
    return np.array([metadata[str(k)]["variant"] in ("clean", "benign_control", "benign_lexical")
                     and metadata[str(k)]["filter_pass"] is True for k in keys])


def fit_reference(a, metadata):
    """Episode-equal within fold/channel; only normal values are indexed here."""
    good = normal_mask(a["keys"], metadata)[a["episode"]]; out = {}
    for fold in range(3):
        for tag in range(3):
            ix = np.flatnonzero(good & (a["structure"][:, 0] == fold) & (a["structure"][:, 1] == tag))
            ep, counts = np.unique(a["episode"][ix], return_counts=True)
            weights = 1/counts[np.searchsorted(ep, a["episode"][ix])] if len(ix) else np.array([])
            values = a["values"][ix, 3:6]; order = np.argsort(values, axis=0, kind="stable")
            key = f"f{fold}t{tag}"
            out[key+"_episodes"] = ep
            out[key+"_values"] = np.take_along_axis(values, order, axis=0)
            out[key+"_cumulative"] = np.vstack([np.zeros((1, 3)), np.cumsum(weights[order], axis=0)])
    return out


def midmass(sorted_values, cumulative, query):
    left = np.searchsorted(sorted_values, query, side="left")
    right = np.searchsorted(sorted_values, query, side="right")
    return (cumulative[left]+cumulative[right])/2


def percentiles(a, metadata, reference):
    result = np.full((len(a["ends"]), 3), np.nan)
    normals = normal_mask(a["keys"], metadata)
    for fold in range(3):
        for tag in range(3):
            key = f"f{fold}t{tag}"; count = len(reference[key+"_episodes"])
            ix = np.flatnonzero((a["structure"][:, 0] == fold) & (a["structure"][:, 1] == tag))
            for ep in np.unique(a["episode"][ix]):
                loc = ix[a["episode"][ix] == ep]; denominator = count-int(normals[ep])
                if denominator < 10: continue
                for col in range(3):
                    query = a["values"][loc, col+3]
                    mass = midmass(reference[key+"_values"][:, col], reference[key+"_cumulative"][:, col], query)
                    if normals[ep]:
                        own = np.sort(query)
                        mass -= midmass(own, np.arange(len(own)+1)/len(own), query)
                    result[loc, col] = mass/denominator
    finite = result[np.isfinite(result)]
    if (finite < -1e-10).any() or (finite > 1+1e-10).any(): raise ValueError("invalid percentile")
    return np.clip(result, 0, 1)


def runs(a):
    n = len(a["ends"])
    if not n: return [], np.empty(0, int)
    changed = ((np.diff(a["episode"]) != 0) | (np.diff(a["ends"]) != 1)
               | (np.diff(a["structure"][:, 1]) != 0) | (np.diff(a["structure"][:, 3]) != 0))
    bounds = np.r_[0, np.flatnonzero(changed)+1, n]
    segments = [(int(lo), int(hi)) for lo, hi in zip(bounds[:-1], bounds[1:], strict=True)]
    return segments, np.repeat(np.arange(len(segments)), np.diff(bounds))


def grids(a, segments, offset):
    # episode, original run id, first look, block count, fold, tag, ep index, step, first endpoint
    rows = []
    for ri, (lo, hi) in enumerate(segments):
        first = lo+offset
        if first >= hi: continue
        n = (hi-first-1)//8+1; structure = a["structure"][first]
        rows.append((a["episode"][first], ri, first, n, *structure[:4], a["ends"][first]))
    return np.array(rows, dtype=np.int64).reshape((-1, 9))


def group_of(row):
    if row["variant"] in ("clean", "benign_control", "benign_lexical"): return "normal_all"
    if row["variant"] == "legitimate_refusal": return "legitimate_refusal"
    return row["trajectory_class"] if row["attack_bearing"] else "excluded_preinjection"


def choose(candidates, query, features, ranks, a, metadata, *, family=False):
    """features: candidate id, look index. Uses initial values only, never future values."""
    row = metadata[str(a["keys"][a["episode"][query]])]
    if not np.isfinite(ranks[query]).all(): return [], "missing_reference"
    ordered = []
    for cid, look in features[candidates]:
        ep = int(a["episode"][look]); other = metadata[str(a["keys"][ep])]
        if other["scenario"] == row["scenario"]: continue
        if family and other["family"] != row["family"]: continue
        delta = abs(ranks[look, 1:]-ranks[query, 1:]); position = abs(int(a["ends"][look]-a["ends"][query]))
        if not np.isfinite(delta).all() or (delta > .10+1e-12).any() or position > 64: continue
        ordered.append((float(delta.sum()), position, str(a["keys"][ep]), int(cid), ep))
    chosen, used = [], set()
    for _, _, _, cid, ep in sorted(ordered):
        if ep in used: continue
        chosen.append(cid); used.add(ep)
        if len(chosen) == 3: break
    return chosen, "matched" if chosen else "no_eligible_donor"


def segment_graph(a, metadata, ranks, grid):
    normal = normal_mask(a["keys"], metadata); bank = defaultdict(list)
    for i, r in enumerate(grid):
        if normal[r[0]]: bank[tuple(r[3:8])].append(i)
    features = np.column_stack([np.arange(len(grid)), grid[:, 2]])
    graph = np.full((2, len(grid), 3), -1, dtype=np.int64); reasons = np.empty((2, len(grid)), dtype="U28")
    for i, r in enumerate(grid):
        for level in range(2):
            if group_of(metadata[str(a["keys"][r[0]])]) == "excluded_preinjection":
                reasons[level, i] = "excluded_preinjection"; continue
            candidates = np.array(bank[tuple(r[3:8])], dtype=int)
            chosen, reason = choose(candidates, int(r[2]), features, ranks, a, metadata, family=bool(level))
            graph[level, i, :len(chosen)] = chosen; reasons[level, i] = reason
    return graph, reasons


def sequence_metrics(values, threshold):
    values = np.asarray(values, dtype=float)
    if values.ndim != 1 or not len(values) or not np.isfinite(values).all(): raise ValueError("invalid sequence")
    high = values > threshold; n, k = len(high), int(high.sum())
    starts = np.flatnonzero(high & ~np.r_[False, high[:-1]])
    stops = np.flatnonzero(high & ~np.r_[high[1:], False])+1
    hh = int((high[:-1] & high[1:]).sum())
    excess = hh/(n-1)-k*(k-1)/(n*(n-1)) if n >= 2 else np.nan
    return np.array([values.mean(), high.mean(), excess, len(starts),
                     int((stops-starts).max(initial=0))*8, int(high[0]), int(high[-1])])


def segment_metrics(ranks, grid):
    values = np.full((len(grid), 2, 3, len(METRICS)), np.nan)
    for i, r in enumerate(grid):
        seq = ranks[r[2]+8*np.arange(r[3])]
        if not np.isfinite(seq).all(): continue
        for ti, threshold in enumerate(THRESHOLDS):
            for col in range(3): values[i, ti, col] = sequence_metrics(seq[:, col], threshold)
    return values


def relation(start, end, x):
    if x is None: return "no_X"
    if end < x: return "before_X"
    if start > x: return "after_X"
    return "overlaps_X"


def recovery_inventory(a, metadata, labels):
    index = {(int(e), int(t)): i for i, (e, t) in enumerate(zip(a["episode"], a["ends"], strict=True))}
    _, run_ids = runs(a); counts = Counter(); by_group = defaultdict(Counter); events = []
    for ei, key in enumerate(a["keys"]):
        row = metadata[str(key)]; label = labels[str(key)]; spans = label.get("recovery_spans", [])
        group = group_of(row); counts["episodes"] += 1; counts["spans"] += len(spans)
        counts["episodes_with_recovery"] += bool(spans); by_group[group]["episodes"] += 1
        by_group[group]["episodes_with_recovery"] += bool(spans)
        counts["GENFAIL"] += str(label.get("note", "")).startswith("GENFAIL:")
        if row["trajectory_class"] == "support_resumed_after_execution" and not spans: raise ValueError("missing required recovery")
        previous = -1
        for j, rec in enumerate(spans):
            start = int(rec["span"]["token_start_global"]); end = int(rec["span"]["token_end_global"])
            e = min((label[t] for t in ("e_analysis", "e_final") if label.get(t) is not None), default=None)
            if e is None or not (e <= start <= end < row["token_count"]) or start < previous:
                raise ValueError("invalid recovery timing")
            previous = start
            for flag in ("explicit_correction", "re_execution"): counts[flag] += int(rec[flag])
            by_group[group]["spans"] += 1; by_group[group][relation(start, end, row["x"])] += 1
            if j or row["variant"] != "attack" or not row["attack_bearing"]: continue
            tag = ("analysis", "commentary", "final").index(rec["channel"])
            pre = index.get((ei, start-1), -1); posts = []; reasons = []
            for t in (start+7, start+15, start+23, start+31):
                post = index.get((ei, t), -1)
                reason = ("no_pre_look" if pre < 0 else "short_labelled_span" if t > end else
                          "no_post_look" if post < 0 else "channel_or_step_boundary" if
                          a["structure"][pre, 1] != tag or run_ids[post] != run_ids[pre] else "available")
                posts.append(post if reason == "available" else -1); reasons.append(reason)
            events.append(dict(episode=ei, key=str(key), start=start, end=end, channel=rec["channel"],
                               group=group, relation=relation(start, end, row["x"]), pre=pre, posts=posts, reasons=reasons,
                               explicit_correction=bool(rec["explicit_correction"]), re_execution=bool(rec["re_execution"])))
    return events, {"counts": dict(counts), "by_group": {k: dict(v) for k, v in sorted(by_group.items())}}, index, run_ids


def recovery_graph(a, metadata, ranks, events, index, run_ids):
    normal = normal_mask(a["keys"], metadata); bank = defaultdict(list)
    for i in np.flatnonzero(normal[a["episode"]]): bank[tuple(a["structure"][i, :4])].append(int(i))
    features = np.column_stack([np.arange(len(a["ends"]))]*2)
    donors = np.full((2, len(events), 3), -1, dtype=np.int64)
    posts = np.full((2, len(events), 3, 4), -1, dtype=np.int64); reasons = np.empty((2, len(events)), dtype="U28")
    for j, event in enumerate(events):
        pre = event["pre"]
        for level in range(2):
            if pre < 0:
                reasons[level, j] = "no_pre_look"; continue
            cs = np.array(bank[tuple(a["structure"][pre, :4])], int)
            selected, reason = choose(cs, pre, features, ranks, a, metadata, family=bool(level))
            donors[level, j, :len(selected)] = selected; reasons[level, j] = reason
            # Selection is already fixed. Future coverage cannot replace a selected donor.
            for di, d in enumerate(selected):
                ep, end = int(a["episode"][d]), int(a["ends"][d])
                for h in range(4):
                    p = index.get((ep, end+8*(h+1)), -1)
                    if p >= 0 and run_ids[p] == run_ids[d]: posts[level, j, di, h] = p
    return donors, posts, reasons


def recovery_values(a, ranks, events, donors, donor_posts, *, common=False):
    values = np.full((2, len(events), 4, len(REC_COLUMNS)), np.nan)
    available = np.zeros((2, len(events), 4), dtype=np.int64)
    data = np.column_stack([a["values"][:, :3], ranks])
    for level in range(2):
        for j, ev in enumerate(events):
            if ev["pre"] < 0: continue
            for h, p in enumerate(ev["posts"]):
                if p < 0: continue
                good = (donor_posts[level, j] >= 0).all(1) if common else donor_posts[level, j, :, h] >= 0
                if common and any(t < 0 for t in ev["posts"]): continue
                good &= donors[level, j] >= 0
                if not good.any(): continue
                normal = (data[donor_posts[level, j, good, h]]-data[donors[level, j, good]]).mean(0)
                query = data[p]-data[ev["pre"]]
                values[level, j, h] = np.stack([query, normal, query-normal], axis=1).ravel()
                available[level, j, h] = int(good.sum())
    return values, available


def summary(episodes, values, keys, metadata, bootstrap):
    values = np.asarray(values, float); episodes = np.asarray(episodes, int)
    good = np.isfinite(values).all(1); values, episodes = values[good], episodes[good]
    if not len(episodes): return dict(rows=0, episodes=0, mean=None, episode_values=[])
    unique, inverse, counts = np.unique(episodes, return_inverse=True, return_counts=True)
    means = np.zeros((len(unique), values.shape[1])); np.add.at(means, inverse, values); means /= counts[:, None]
    rows = [metadata[str(keys[e])] for e in unique]
    return dict(rows=len(values), episodes=len(unique), mean=means.mean(0).tolist(),
                family=bootstrap(means, [str(r["family"]) for r in rows]),
                family_tier=bootstrap(means, [f'{r["family"]}|{r["tier"]}' for r in rows]),
                episode_values=[dict(key=str(keys[e]), rows=int(n), values=v.tolist())
                                for e, n, v in zip(unique, counts, means, strict=True)])
